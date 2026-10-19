#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Print the notes for a GitHub release: the CHANGELOG.md section, then the
# archives and the exact revisions recorded in their build manifests, and the
# VS Code extension with the version recorded in the VSIX itself. Every
# manifest in the asset directory must record the same version, tag, and
# revisions, and its archive must be present. The directory must hold exactly
# one VSIX, named <name>-<version>.vsix after its extension/package.json. See
# docs/releasing.md.
# Usage: release-notes.py <changelog-section> <asset-directory>
# <changelog-section> is a version, or "Unreleased" for a dry run.
import glob
import json
import os
import sys
import zipfile
import zlib

try:
    from lzma import LZMAError
except ImportError:  # zipfile then refuses LZMA entries with a RuntimeError.
    LZMAError = RuntimeError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORMS = {
    "x86-64-linux": "Linux x86-64, including WSL 2",
    "x86-64-darwin": "macOS: an x86-64 build, which Apple Silicon runs under Rosetta 2. "
    "It is not a native arm64 build.",
}
SHARED = ("version", "tag", "sourceRevision", "dirty", "virgilRevision")
MANIFEST = "extension/package.json"
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


def extension(assets):
    vsixes = sorted(glob.glob(os.path.join(assets, "*.vsix")))
    if len(vsixes) != 1:
        fail("expected one VS Code extension (*.vsix) in %s, found %d" % (assets, len(vsixes)))
    path = vsixes[0]
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
            isinstance(manifest.get(k), str) and manifest[k] for k in ("name", "publisher", "version")):
        fail(name + ": extension/package.json lacks a name, publisher, or version")
    if name != "%s-%s.vsix" % (manifest["name"], manifest["version"]):
        fail("%s holds version %s of %s" % (name, manifest["version"], manifest["name"]))
    return name, manifest


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: release-notes.py <changelog-section> <asset-directory>")
    heading, assets = sys.argv[1:]
    manifests = []
    for path in sorted(glob.glob(os.path.join(assets, "*.manifest.json"))):
        manifests.append(load_json(path))
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
    vsix, ext = extension(assets)

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "SaltedTan/virgil-language-server")
    url = "%s/%s" % (server, repository)
    revision, virgil = first["sourceRevision"], first["virgilRevision"]
    ref = first["tag"] or revision

    out = [changelog_section(heading), "", "## Downloads", ""]
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
        "`%s` is version %s of the VS Code extension (`%s.%s`)%s. Its versions are "
        "independent of the server's, and it doesn't include a server: install a server archive "
        "on the machine that holds your files, then [install the extension](%s/blob/%s/docs/releasing.md#vs-code-extension) "
        "there from the VSIX."
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
