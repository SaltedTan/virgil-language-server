#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Smoke-check a VSIX from `npm run package` in clients/vscode before the release
# workflow tests and attaches it. The VSIX must be a zip file named
# <name>-<version>.vsix whose entries are canonical relative paths in
# extension/, apart from the two files that vsce writes beside it, and its
# extension/package.json must have the name, publisher, and version of the
# extension directory's package.json. It must hold the extension's main file
# and, for each production package in package-lock.json (a "packages" key
# that isn't marked "dev") that npm always installs, that package's
# package.json. It must hold no file that
# .vscodeignore or vsce's defaults exclude, matched as vsce matches them
# against each file's name before vsce renames it, and no package whose path,
# from its innermost node_modules directory up, the lockfile doesn't list as a
# production package, such as @vscode/vsce-sign, a development dependency. See
# docs/releasing.md.
# Usage: check-vsix.py <vsix> [<extension-directory>]
# The extension directory defaults to clients/vscode.
import functools
import json
import os
import posixpath
import re
import sys
import zipfile
import zlib

try:
    from lzma import LZMAError
except ImportError:  # zipfile then refuses LZMA entries with a RuntimeError.
    LZMAError = RuntimeError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDENTITY = ("name", "publisher", "version")
# These mirror vsce 4.0.0's defaults (defaultIgnore and notIgnored in
# node_modules/@vscode/vsce/out/package.js), which it combines with the
# patterns in .vscodeignore. Keep them in step when vsce is updated.
VSCE_DEFAULT_IGNORE = (
    ".vscodeignore", "package-lock.json", "npm-debug.log", "yarn.lock", "yarn-error.log",
    "npm-shrinkwrap.json", ".editorconfig", ".npmrc", ".yarnrc", ".gitattributes", "*.todo",
    "tslint.yaml", ".eslintrc*", ".babelrc*", ".prettierrc*", ".cz-config.js", ".commitlintrc*",
    "webpack.config.js", "ISSUE_TEMPLATE.md", "CONTRIBUTING.md", "PULL_REQUEST_TEMPLATE.md",
    "CODE_OF_CONDUCT.md", ".github", ".travis.yml", "appveyor.yml", "**/.git", "**/.git/**",
    "**/*.vsix", "**/.DS_Store", "**/*.vsixmanifest", "**/.vscode-test/**",
    "**/.vscode-test-web/**",
)
VSCE_NOT_IGNORED = ("!package.json", "!README.md")
# vsce 4.0.0 renames these top-level files in the VSIX (ReadmeProcessor,
# ChangelogProcessor, and LicenseProcessor in the same file), with the options
# that `npm run package` uses: a README.md or CHANGELOG.md in any case becomes
# readme.md or changelog.md, and a license file without an extension gains
# ".txt". Exclusions apply to the source names. Keep them in step too.
VSCE_RENAMES = (
    ("readme.md", re.compile(r"readme\.md", re.I)),
    ("changelog.md", re.compile(r"changelog\.md", re.I)),
)
VSCE_LICENSE = re.compile(r"licen[cs]e", re.I)
# The files that vsce writes at the top of the VSIX, outside extension/.
VSIX_ROOT_FILES = ("[Content_Types].xml", "extension.vsixmanifest")
MANIFEST = "extension/package.json"
# Pattern syntax that minimatch supports and this script doesn't, because
# .vscodeignore doesn't need it, in group 1: an unescaped bracket or brace,
# which starts a character class or brace expansion; an extglob such as
# "+(a|b)"; and a backslash at the end of a segment or before another
# backslash or a "|". minimatch's brace expansion, which runs on a pattern
# with braces even if they are escaped, turns an escaped backslash into one
# that escapes the next character, and a VSIX entry can't hold a backslash
# anyway. minimatch 10.2.6 copies an escaped "|" unescaped into the regular
# expression for a segment with "*" or "?", where it separates alternatives.
# The first alternative consumes every other escape, so that the escaped
# character, such as the "[" in "\[draft\]", is a literal rather than syntax.
PATTERN_SYNTAX = re.compile(r"\\[^/\\|]|([][{}]|[?*+@!]\(|\\)")
# A segment such as "*.js" or "??.md", which minimatch 10.2.6 matches with a
# shortcut (starDotExtRE and qmarksRE in its index.js) that compares the end
# of a name with the rest of the segment as written, so that an escape there
# would only match a backslash. This script refuses an escape in such a
# segment. Check these quirks again when vsce's minimatch is updated.
SHORTCUT_SEGMENT = re.compile(r"\*+[^+@!?*[(]*|\?+[^+@!?*[(]*")
# A character outside the Basic Multilingual Plane, which JavaScript strings,
# and so minimatch's "?" and "*", count as two UTF-16 code units.
NON_BMP = re.compile("[\U00010000-\U0010ffff]")
# What zipfile can raise for a damaged VSIX that is_zipfile accepts: a bad
# header, central directory, or CRC (BadZipFile); an entry name that isn't the
# UTF-8 that its flags claim (UnicodeDecodeError, a ValueError) or an offset
# too large to seek to, such as a ZIP64 offset of 2**64 - 1 (ValueError);
# corrupt or truncated compressed data (zlib.error, LZMAError, EOFError, and
# OSError, which bzip2 raises and which also covers I/O errors); and a
# compression method, feature, or encryption that zipfile doesn't support
# (RuntimeError, including its subclass NotImplementedError). Catch them only
# around zipfile's own calls.
ZIP_ERRORS = (zipfile.BadZipFile, ValueError, zlib.error, LZMAError, EOFError, OSError,
              RuntimeError)


def fail(message):
    sys.exit("check-vsix.py: error: " + message)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, ValueError) as e:
        fail("cannot read %s: %s" % (path, e))


def parse_json(text, what):
    try:
        return json.loads(text)
    except ValueError as e:
        fail("%s is not valid JSON: %s" % (what, e))
    except RecursionError:
        fail("%s is nested too deeply to parse" % what)


def load_json(path):
    return parse_json(read(path), path)


def unsupported(rule):
    """Whether a pattern uses syntax that this script doesn't implement. That
    includes an empty segment between two others, which minimatch removes by
    coalescing slashes, and a "." or ".." segment, which minimatch partly
    resolves before matching, such as "tmp/../assets/keep.txt"."""
    if any(m.group(1) for m in PATTERN_SYNTAX.finditer(rule)):
        return True
    segments = rule.lstrip("!").split("/")
    return ("" in segments[1:-1] or "." in segments or ".." in segments
            or any("\\" in s and SHORTCUT_SEGMENT.fullmatch(s) for s in segments))


def ignore_rules(path):
    """Returns the ignore and negated patterns that vsce builds from .vscodeignore."""
    lines = [line.strip() for line in re.split(r"[\n\r]", read(path))]
    lines = [line for line in lines if line and not line.startswith("#")]
    # vsce also matches everything under a pattern whose last segment has no
    # "*", in case it names a directory.
    lines += [line + "**" if line.endswith("/") else line + "/**"
              for line in lines if not re.search(r"(^|/)[^/]*\*[^/]*$", line)]
    rules = list(VSCE_DEFAULT_IGNORE) + lines + list(VSCE_NOT_IGNORED)
    for rule in rules:
        if unsupported(rule):
            fail("%s: check-vsix.py doesn't support the pattern '%s'" % (path, rule))
    ignore = [r for r in rules if not r.startswith("!")]
    negated = [r[1:] for r in rules if r.startswith("!")]
    return ignore, negated


@functools.lru_cache(maxsize=None)
def segment_regex(segment):
    """Translates one path segment of a minimatch pattern into a regular expression."""
    out = []
    i = 0
    while i < len(segment):
        c = segment[i]
        if c == "\\":
            # The escaped character is a literal. ignore_rules refuses a
            # backslash at the end of a segment.
            out.append(re.escape(segment[i + 1]))
            i += 1
        elif c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out), re.DOTALL)


def match_parts(pattern, parts):
    if not pattern:
        return not parts
    if pattern[0] == "**":
        # A trailing "**" matches at least one segment: "src/**" doesn't match "src".
        first = 1 if len(pattern) == 1 else 0
        return any(match_parts(pattern[1:], parts[i:]) for i in range(first, len(parts) + 1))
    return (bool(parts) and segment_regex(pattern[0]).fullmatch(parts[0]) is not None
            and match_parts(pattern[1:], parts[1:]))


def utf16(text):
    """Replaces each character outside the Basic Multilingual Plane with its
    UTF-16 surrogate pair, so that text has JavaScript's length."""
    def pair(m):
        c = ord(m.group()) - 0x10000
        return chr(0xD800 + (c >> 10)) + chr(0xDC00 + (c & 0x3FF))
    return NON_BMP.sub(pair, text)


def minimatch(path, pattern):
    """minimatch(path, pattern, {dot: true}) for the syntax that ignore_rules accepts:
    "*", "?", and "\\" escapes within a segment, "**" as a whole segment for any
    number of segments (at least one at the end), dot files matched like any
    other, a leading "!" for negation, and a leading "#" for a comment. Like
    minimatch, "?" matches one UTF-16 code unit, so it matches only half of a
    character outside the Basic Multilingual Plane, such as an emoji."""
    if pattern.startswith("#"):
        return False
    negate = False
    while pattern.startswith("!"):
        negate = not negate
        pattern = pattern[1:]
    return match_parts(tuple(utf16(pattern).split("/")), tuple(utf16(path).split("/"))) != negate


def package_of(path):
    """Returns the path of the innermost package that holds a file, such as
    node_modules/a/lib/node_modules/@b/c, or None outside node_modules, or
    "" for a file in node_modules that isn't in a package. Only directories
    count: a file named node_modules is an ordinary file."""
    parts = path.split("/")
    directories = parts[:-1]
    if "node_modules" not in directories:
        return None
    i = len(directories) - 1 - directories[::-1].index("node_modules")
    end = i + (3 if parts[i + 1].startswith("@") else 2)
    # The package's name must be followed by a file in it.
    if end >= len(parts):
        return ""
    return "/".join(parts[:end])


def entry_problem(entry):
    """Why a ZIP entry isn't one that vsce writes, or None."""
    path = entry[:-1] if entry.endswith("/") else entry
    if path.startswith("/") or "\\" in path or any(s in ("", ".", "..") for s in path.split("/")):
        return "the entry %r isn't a canonical relative path" % entry
    if path != "extension" and not path.startswith("extension/") and (
            entry.endswith("/") or path not in VSIX_ROOT_FILES):
        return "the entry %r is outside extension/" % entry
    return None


def source_name(path, top):
    """The name in the extension directory of a file at path in extension/,
    undoing vsce's renames. top lists the extension directory's own entries."""
    candidates = None
    for renamed, original in VSCE_RENAMES:
        if path == renamed:
            candidates = [n for n in top if original.fullmatch(n)]
    if "/" not in path and path.endswith(".txt") and VSCE_LICENSE.fullmatch(path[:-4]):
        candidates = [n for n in top if n in (path, path[:-4])]
    if not candidates:
        return path
    if len(candidates) > 1:
        fail("vsce packages each of %s as %s" % (", ".join(sorted(candidates)), path))
    return candidates[0]


def read_vsix(vsix, name):
    """Returns the entry names of a VSIX, as the archive records them, and its
    parsed extension/package.json."""
    if not zipfile.is_zipfile(vsix):
        fail("%s is not a zip file" % vsix)
    try:
        z = zipfile.ZipFile(vsix)
    except ZIP_ERRORS as e:
        fail("cannot read %s: %s" % (vsix, e))
    with z:
        # zipfile finds an entry by its name, which it ends before any NUL, and
        # reads the last entry of each name, so first refuse a name that
        # zipfile changes or that the archive records more than once.
        entries = [info.orig_filename for info in z.infolist()]
        for info in z.infolist():
            if info.orig_filename != info.filename:
                fail("%s: the entry %r isn't a canonical relative path" % (name, info.orig_filename))
        seen = set()
        for entry in entries:
            if entry in seen:
                fail("%s holds more than one entry named %r" % (name, entry))
            seen.add(entry)
        try:
            data = z.read(MANIFEST)
        except KeyError:
            fail("%s has no %s" % (name, MANIFEST))
        except ZIP_ERRORS as e:
            fail("%s: cannot read %s: %s" % (name, MANIFEST, e))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        fail("%s: %s is not UTF-8: %s" % (name, MANIFEST, e))
    return entries, parse_json(text, "%s: %s" % (name, MANIFEST))


def required(entry):
    """Whether npm always installs a production package from package-lock.json."""
    return not any(entry.get(k) for k in ("optional", "devOptional", "os", "cpu", "link"))


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: check-vsix.py <vsix> [<extension-directory>]")
    vsix = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) == 3 else os.path.join(ROOT, "clients", "vscode")
    package_json = os.path.join(source, "package.json")
    expected = load_json(package_json)
    if not isinstance(expected, dict) or not all(
            isinstance(expected.get(k), str) for k in IDENTITY + ("main",)):
        fail("%s lacks a name, publisher, version, or main" % package_json)
    if str(expected.get("license", "")).startswith("SEE LICENSE IN"):
        fail("check-vsix.py doesn't support a license given as 'SEE LICENSE IN <file>'")
    lockfile = os.path.join(source, "package-lock.json")
    lock = load_json(lockfile)
    if not isinstance(lock, dict) or not isinstance(lock.get("packages"), dict) or not all(
            isinstance(v, dict) for v in lock["packages"].values()):
        fail("%s has no \"packages\" object of package objects" % lockfile)
    ignore, negated = ignore_rules(os.path.join(source, ".vscodeignore"))
    production = {k: v for k, v in lock["packages"].items()
                  if k.startswith("node_modules/") and not v.get("dev")}
    top = os.listdir(source)

    name = os.path.basename(vsix)
    entries, manifest = read_vsix(vsix, name)
    if not isinstance(manifest, dict):
        fail("%s: %s is not a JSON object" % (name, MANIFEST))

    problems = []
    for key in IDENTITY:
        if manifest.get(key) != expected[key]:
            problems.append("%s is %r, but %s/package.json has %r"
                            % (key, manifest.get(key), source, expected[key]))
    want = "%s-%s.vsix" % (expected["name"], expected["version"])
    if name != want:
        problems.append("the file is %s, not %s" % (name, want))

    files = []
    for entry in entries:
        problem = entry_problem(entry)
        if problem is not None:
            problems.append(problem)
        elif entry.startswith("extension/") and not entry.endswith("/"):
            files.append(entry[len("extension/"):])
    main_file = posixpath.normpath(expected["main"])
    if main_file not in files:
        problems.append("the main file %s is missing" % main_file)
    for key in sorted(production):
        if required(production[key]) and key + "/package.json" not in files:
            problems.append("the production package %s is missing" % key)
    unlisted = set()
    for f in files:
        original = source_name(f, top)
        rule = next((r for r in ignore if minimatch(original, r)), None)
        if rule is not None and not any(minimatch(original, r) for r in negated):
            shown = f if original == f else "%s (%s in %s)" % (f, original, source)
            problems.append("it holds %s, which the ignore pattern '%s' excludes" % (shown, rule))
        package = package_of(f)
        if package == "":
            problems.append("it holds %s, which isn't in a package" % f)
        elif package is not None and package not in production:
            unlisted.add(package)
    problems += ["it holds %s, which package-lock.json doesn't list as a production package" % p
                 for p in sorted(unlisted)]

    if problems:
        for p in problems:
            print("check-vsix.py: error: %s: %s" % (name, p), file=sys.stderr)
        sys.exit(1)
    print("%s: %s.%s %s, %d files" % (name, expected["publisher"], expected["name"],
                                      expected["version"], len(entries)))


if __name__ == "__main__":
    main()
