#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Print the notes for a GitHub release: the CHANGELOG.md section, then the
# archives and the exact revisions recorded in their build manifests, and the
# VS Code extension with the version recorded in the VSIX itself. Every
# manifest in the asset directory must record the same version, tag, and
# revisions, and its archive must be present. The directory must hold exactly
# four VSIX files: universal, linux-x64, darwin-arm64, and darwin-x64,
# named after their common extension identity and version.
# GitHub doesn't resolve relative links in a release, so the CHANGELOG
# section's repository links, such as [Hover](docs/architecture.md#hover), are
# rewritten with the Downloads links' repository URL and ref: the manifests'
# tag when present, otherwise the source revision; a fragment alone links to
# CHANGELOG.md itself. The script doesn't parse Markdown: every "](" starts a
# link target, even in a code span or after a backslash escape, so that no
# inline link or image can be missed. A target with a URI scheme is kept as
# it is. A target outside the repository root, a root-relative or
# protocol-relative target, a target that the checkout lacks, and any other
# text after "](", such as a title, angle brackets, whitespace, or
# parentheses, fail. So do "]:", which every reference link definition holds,
# and any HTML tag with an attribute, whatever the attribute, since a URL
# reaches an HTML element only through one; that includes a tag in a code
# span, so write `a < b` rather than `a<b c`. See
# docs/releasing.md.
# Usage: release-notes.py <changelog-section> <asset-directory>
# <changelog-section> is a version, or "Unreleased" for a dry run.
import glob
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
PLATFORMS = {
    "x86-64-linux": "Linux x86-64; WSL 2 on a best-effort basis",
    "x86-64-darwin": "macOS: an x86-64 build, which Apple Silicon runs under Rosetta 2. "
    "It is not a native arm64 build.",
}
SHARED = ("version", "tag", "sourceRevision", "dirty", "virgilRevision")
# The fields that main() reads from each build manifest and the types that
# scripts/package-release.sh writes, which the release notes depend on: the
# version and target name the archive, dirty refuses a modified build, tag is
# null for a dry run, and the revisions are linked. Each string must also be
# non-empty and encode as UTF-8, or print() can't write it. workerHeap,
# compiler, and sha256 are in the manifest but the notes don't read them.
MANIFEST_FIELDS = (
    ("version", "a non-empty string"),
    ("tag", "a non-empty string or null"),
    ("target", "a non-empty string"),
    ("sourceRevision", "a non-empty string"),
    ("dirty", "a boolean"),
    ("virgilRevision", "a non-empty string"),
)
MANIFEST = "extension/package.json"
# What release_links() matches in the CHANGELOG section: an HTML tag with an
# attribute, which is a tag name as CommonMark defines it, then whitespace or
# "/", which the HTML tokenizer treats alike after a tag name, then any
# character but whitespace, "/", or ">". Where a name runs into any other
# character, such as the comma in Map<K, V>, the tokenizer takes that character
# into the name, which then names no element that GitHub keeps. Then every
# "](", with the target that follows it if that is a plain one, which has no
# spaces, parentheses, or angle brackets and ends at ")". Neither can backtrack
# far, so each pass is linear.
TAG = re.compile(r"<[A-Za-z][A-Za-z0-9-]*[\s/]+[^\s/>]")
LINK = re.compile(r"\]\((?:(?P<target>[^\s()<>]+)\))?")
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)
# What zipfile can raise for a damaged VSIX that is_zipfile accepts, as in
# scripts/check-vsix.py: a bad header, central directory, or CRC; an entry
# name that isn't the UTF-8 that its flags claim, or an offset too large to
# seek to (ValueError); corrupt or truncated compressed data or an I/O error;
# and a compression method, feature, or encryption that zipfile doesn't
# support (RuntimeError, including its subclass NotImplementedError). Catch
# them only around zipfile's own calls.
ZIP_ERRORS = (zipfile.BadZipFile, ValueError, zlib.error, LZMAError, EOFError, OSError,
              RuntimeError)


def fail(message):
    sys.exit("release-notes.py: error: " + message)


def parse_json(text, what):
    """Parses JSON, refusing the NaN, Infinity, and -Infinity constants that
    Python's json module accepts and JSON doesn't."""
    def constant(value):
        fail("%s is not valid JSON: it holds the non-JSON constant %s" % (what, value))
    try:
        return json.loads(text, parse_constant=constant)
    except ValueError as e:
        fail("%s is not valid JSON: %s" % (what, e))
    except RecursionError:
        fail("%s is nested too deeply to parse" % what)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, ValueError) as e:
        fail("cannot read %s: %s" % (path, e))
    return parse_json(text, path)


def json_type(value):
    """Names the JSON type of a decoded value, with the article that a message
    reads with. bool is a subclass of int, so check it before int."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return "a " + type(value).__name__


def string_fault(value, want):
    """What is wrong with a value that main() reads as a string, as the text
    that follows the field's name, or None if it is usable: it isn't a string,
    it is empty, or it holds a lone surrogate, as a JSON escape such as
    \\ud800 decodes to, which print() can't encode as UTF-8."""
    if not isinstance(value, str):
        return "is %s, not %s" % (json_type(value), want)
    if not value:
        return "is an empty string, not %s" % want
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "is not valid Unicode"
    return None


def validate_manifest(path, manifest):
    """Checks that a build manifest is an object with the fields that main()
    reads, each of the type that scripts/package-release.sh writes, and that
    each string is non-empty and encodes as UTF-8, then reports every problem
    with it at once."""
    if not isinstance(manifest, dict):
        fail("%s is not a JSON object" % path)
    problems = []
    missing = [key for key, _ in MANIFEST_FIELDS if key not in manifest]
    if missing:
        problems.append("lacks required fields: " + ", ".join(missing))
    for key, want in MANIFEST_FIELDS:
        if key not in manifest:
            continue
        value = manifest[key]
        if key == "dirty":
            ok = isinstance(value, bool)
            why = "is %s, not %s" % (json_type(value), want)
        elif key == "tag" and value is None:
            ok = True
        else:
            why = string_fault(value, want)
            ok = why is None
        if not ok:
            problems.append("%s %s" % (key, why))
    if problems:
        fail("%s: %s" % (path, "; ".join(problems)))


def changelog_section(heading):
    prefix = "## [%s]" % heading
    lines = None
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        for line in f:
            if lines is None:
                if line.startswith(prefix):
                    lines = []
            elif line.startswith("## "):
                break
            else:
                lines.append(line.rstrip("\n"))
    if lines is None:
        fail("CHANGELOG.md has no '%s' section" % prefix)
    return "\n".join(lines).strip()


def release_link(target, url, ref):
    """Rewrites the target of a CHANGELOG.md link to the file at ref, keeping
    any fragment, unless it has a URI scheme."""
    if SCHEME.match(target):
        return target
    if target.startswith("/"):
        fail("CHANGELOG.md links to %s, which is not relative to the repository root"
             % target)
    path, sep, fragment = target.partition("#")
    path = posixpath.normpath(path) if path else "CHANGELOG.md"
    if path == ".." or path.startswith("../"):
        fail("CHANGELOG.md links to %s, outside the repository" % target)
    if not os.path.exists(os.path.join(ROOT, path)):
        fail("CHANGELOG.md links to %s, which does not exist" % target)
    return "%s/blob/%s/%s%s%s" % (url, ref, path, sep, fragment)


def release_links(section, url, ref):
    """Rewrites the repository links in a CHANGELOG.md section, which are
    relative to the repository root, as links to the files at ref."""
    at = section.find("]:")
    if at >= 0:
        line = section[section.rfind("\n", 0, at) + 1:].partition("\n")[0]
        fail("CHANGELOG.md: cannot rewrite %r: the release notes don't support reference "
             "link definitions" % line)
    tag = TAG.search(section)
    if tag:
        start = section.rfind("\n", 0, tag.start()) + 1
        end = section.find("\n", tag.end())
        fail("CHANGELOG.md: cannot rewrite %r: the release notes don't support HTML tags with "
             "attributes" % section[start:None if end < 0 else end])

    def rewrite(match):
        if match["target"] is None:
            text = section[match.start():].partition("\n")[0]
            if ")" in text:
                text = text[:text.index(")") + 1]
            fail("CHANGELOG.md: cannot rewrite the link at %r: the release notes treat every "
                 "\"](\" as a link and rewrite only targets without spaces, parentheses, or "
                 "angle brackets" % text)
        return "](%s)" % release_link(match["target"], url, ref)
    return LINK.sub(rewrite, section)


def extensions(assets):
    vsixes = sorted(glob.glob(os.path.join(assets, "*.vsix")))
    if len(vsixes) != 4:
        fail("expected four VS Code extensions (*.vsix) in %s, found %d" % (assets, len(vsixes)))
    found = [extension(path) for path in vsixes]
    first = found[0][1]
    expected = {"%s-%s%s.vsix" % (first["name"], first["version"], suffix)
                for suffix in ("", "-linux-x64", "-darwin-arm64", "-darwin-x64")}
    if {name for name, _ in found} != expected:
        fail("VSIX files do not cover universal, linux-x64, darwin-arm64, and darwin-x64")
    if any(any(ext[k] != first[k] for k in ("name", "publisher", "version")) for _, ext in found):
        fail("VSIX identities disagree")
    return ", ".join("`%s`" % name for name, _ in found), first


def extension(path):
    name = os.path.basename(path)
    if not zipfile.is_zipfile(path):
        fail(name + " is not a zip file")
    try:
        z = zipfile.ZipFile(path)
    except ZIP_ERRORS as e:
        fail("cannot read %s: %s" % (path, e))
    with z:
        # zipfile reads the last entry with the name, which it ends before any
        # NUL, so refuse to choose between copies.
        if sum(info.filename == MANIFEST for info in z.infolist()) > 1:
            fail("%s holds more than one entry named %r" % (name, MANIFEST))
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
    manifest = parse_json(text, "%s: %s" % (name, MANIFEST))
    if not isinstance(manifest, dict) or not all(
            string_fault(manifest.get(k), "a non-empty string") is None
            for k in ("name", "publisher", "version")):
        fail(name + ": extension/package.json lacks a name, publisher, or version")
    if name not in {"%s-%s%s.vsix" % (manifest["name"], manifest["version"], suffix)
                    for suffix in ("", "-linux-x64", "-darwin-arm64", "-darwin-x64")}:
        fail("%s holds version %s of %s" % (name, manifest["version"], manifest["name"]))
    return name, manifest


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: release-notes.py <changelog-section> <asset-directory>")
    heading, assets = sys.argv[1:]
    manifests = []
    for path in sorted(glob.glob(os.path.join(assets, "*.manifest.json"))):
        manifest = load_json(path)
        validate_manifest(path, manifest)
        manifests.append(manifest)
    if not manifests:
        fail("no build manifests in " + assets)
    first = manifests[0]
    for m in manifests:
        for key in SHARED:
            if m[key] != first[key]:
                fail("manifests disagree on %s: %r and %r" % (key, first[key], m[key]))
        archive = "virgil-lsp-%s-%s.tar.gz" % (m["version"], m["target"])
        if not os.path.isfile(os.path.join(assets, archive)):
            fail("missing archive " + archive)
    if first["dirty"]:
        fail("the archives were built from a modified checkout")
    vsix, ext = extensions(assets)

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "SaltedTan/virgil-language-server")
    url = "%s/%s" % (server, repository)
    revision, virgil = first["sourceRevision"], first["virgilRevision"]
    ref = first["tag"] or revision

    out = [release_links(changelog_section(heading), url, ref), "", "## Downloads", ""]
    out.append("| Archive | Platform |")
    out.append("| --- | --- |")
    for m in manifests:
        archive = "virgil-lsp-%s-%s.tar.gz" % (m["version"], m["target"])
        out.append("| `%s` | %s |" % (archive, PLATFORMS.get(m["target"], "`%s`" % m["target"])))
    out += [
        "",
        "Each archive holds `virgil-lsp` and `virgil-lsp-worker`, which must stay in the same "
        "directory, and a `manifest.json`, which is also attached on its own as "
        "`virgil-lsp-<version>-<target>.manifest.json`. "
        "The executables are unsigned and not notarized. Check the downloads against "
        "`SHA256SUMS`, then follow the [installation instructions](%s/blob/%s/docs/releasing.md#installing)."
        % (url, ref),
        "",
        "### VS Code extension",
        "",
        "%s are version %s of the VS Code extension (`%s.%s`)%s. Its versions are "
        "independent of the server's. Install the VSIX for your workspace host: linux-x64, "
        "darwin-arm64 (x86-64 server under Rosetta 2), or darwin-x64. These bundle the tested "
        "server, worker, and pinned library. The universal VSIX needs an external server. "
        "See [installation instructions](%s/blob/%s/docs/releasing.md#vs-code-extension)."
        % (vsix, ext["version"], ext["publisher"], ext["name"],
           ", a preview" if ext.get("preview") is True else "", url, ref),
        "",
        "## Revisions",
        "",
        "- Server: [`%s`](%s/commit/%s)" % (revision, url, revision),
        "- Virgil: [`%s`](https://github.com/titzer/virgil/commit/%s)" % (virgil, virgil),
        "",
        "The manifests also record each build's target, worker heap, and the SHA-256 of the "
        "prebuilt Virgil compiler that built it.",
    ]
    print("\n".join(out))


if __name__ == "__main__":
    main()
