#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Smoke-check a VSIX from `npm run package` in clients/vscode before the release
# workflow tests and attaches it. The VSIX must be a zip file named
# <name>-<version>.vsix, and its extension/package.json must have the name,
# publisher, and version of the extension directory's package.json. It must
# hold the extension's main file and, for each production package in
# package-lock.json (a "packages" key that isn't marked "dev") that npm always
# installs, that package's package.json. It must hold no file that
# .vscodeignore or vsce's defaults exclude, matched as vsce matches them, and
# no package whose path, from its innermost node_modules directory up, the
# lockfile doesn't list as a production package, such as @vscode/vsce-sign, a
# development dependency. See docs/releasing.md.
# Usage: check-vsix.py <vsix> [<extension-directory>]
# The extension directory defaults to clients/vscode.
import functools
import json
import os
import posixpath
import re
import sys
import zipfile

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
# Pattern syntax that minimatch supports and this script doesn't: braces,
# extglobs, and POSIX character classes.
UNSUPPORTED = re.compile(r"[{}]|[?*+@!]\(|\[:")


def fail(message):
    sys.exit("check-vsix.py: error: " + message)


def ignore_rules(path):
    """Returns the ignore and negated patterns that vsce builds from .vscodeignore."""
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in re.split(r"[\n\r]", f.read())]
    lines = [line for line in lines if line and not line.startswith("#")]
    # vsce also matches everything under a pattern whose last segment has no
    # "*", in case it names a directory.
    lines += [line + "**" if line.endswith("/") else line + "/**"
              for line in lines if not re.search(r"(^|/)[^/]*\*[^/]*$", line)]
    rules = list(VSCE_DEFAULT_IGNORE) + lines + list(VSCE_NOT_IGNORED)
    for rule in rules:
        if UNSUPPORTED.search(rule):
            fail("%s: check-vsix.py doesn't support the pattern %r" % (path, rule))
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
        if c == "\\" and i + 1 < len(segment):
            out.append(re.escape(segment[i + 1]))
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            start = i + 1
            negate = start < len(segment) and segment[start] in "!^"
            if negate:
                start += 1
            # A "]" right after the opening bracket is part of the class.
            end = segment.find("]", start + 1)
            if end < 0:
                out.append(re.escape(c))
            else:
                body = segment[start:end].replace("\\", "\\\\").replace("[", "\\[")
                out.append("[" + ("^" if negate else "") + body.replace("^", "\\^") + "]")
                i = end
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


def minimatch(path, pattern):
    """minimatch(path, pattern, {dot: true}) for the syntax that ignore_rules accepts:
    "*", "?", and classes within a segment, "**" as a whole segment for any
    number of segments (at least one at the end), dot files matched like any
    other, a leading "!" for negation, and a leading "#" for a comment."""
    if pattern.startswith("#"):
        return False
    negate = False
    while pattern.startswith("!"):
        negate = not negate
        pattern = pattern[1:]
    return match_parts(tuple(pattern.split("/")), tuple(path.split("/"))) != negate


def package_of(path):
    """Returns the path of the innermost package that holds a file, such as
    node_modules/a/lib/node_modules/@b/c, or None outside node_modules, or
    "" for a file in node_modules that isn't in a package."""
    parts = path.split("/")
    if "node_modules" not in parts:
        return None
    i = len(parts) - 1 - parts[::-1].index("node_modules")
    end = i + (3 if i + 1 < len(parts) and parts[i + 1].startswith("@") else 2)
    # The package's name must be followed by a file in it.
    if end >= len(parts):
        return ""
    return "/".join(parts[:end])


def required(entry):
    """Whether npm always installs a production package from package-lock.json."""
    return not any(entry.get(k) for k in ("optional", "devOptional", "os", "cpu", "link"))


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: check-vsix.py <vsix> [<extension-directory>]")
    vsix = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) == 3 else os.path.join(ROOT, "clients", "vscode")
    with open(os.path.join(source, "package.json"), encoding="utf-8") as f:
        expected = json.load(f)
    with open(os.path.join(source, "package-lock.json"), encoding="utf-8") as f:
        lock = json.load(f)["packages"]
    ignore, negated = ignore_rules(os.path.join(source, ".vscodeignore"))
    production = {k: v for k, v in lock.items() if k.startswith("node_modules/") and not v.get("dev")}

    name = os.path.basename(vsix)
    if not zipfile.is_zipfile(vsix):
        fail("%s is not a zip file" % vsix)
    with zipfile.ZipFile(vsix) as z:
        entries = z.namelist()
        try:
            manifest = json.loads(z.read("extension/package.json").decode("utf-8"))
        except KeyError:
            fail("%s has no extension/package.json" % name)
        except ValueError as e:
            fail("%s: extension/package.json is not valid JSON: %s" % (name, e))
    if not isinstance(manifest, dict):
        fail("%s: extension/package.json is not a JSON object" % name)

    problems = []
    for key in IDENTITY:
        if manifest.get(key) != expected[key]:
            problems.append("%s is %r, but %s/package.json has %r"
                            % (key, manifest.get(key), source, expected[key]))
    want = "%s-%s.vsix" % (expected["name"], expected["version"])
    if name != want:
        problems.append("the file is %s, not %s" % (name, want))

    files = [e[len("extension/"):] for e in entries if e.startswith("extension/")]
    main_file = posixpath.normpath(expected["main"])
    if main_file not in files:
        problems.append("the main file %s is missing" % main_file)
    for key in sorted(production):
        if required(production[key]) and key + "/package.json" not in files:
            problems.append("the production package %s is missing" % key)
    unlisted = set()
    for f in files:
        rule = next((r for r in ignore if minimatch(f, r)), None)
        if rule is not None and not any(minimatch(f, r) for r in negated):
            problems.append("it holds %s, which the ignore pattern %r excludes" % (f, rule))
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
