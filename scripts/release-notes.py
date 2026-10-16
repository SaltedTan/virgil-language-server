#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Print the notes for a GitHub release: the CHANGELOG.md section, then the
# archives and the exact revisions recorded in their build manifests. Every
# manifest in the asset directory must record the same version, tag, and
# revisions, and its archive must be present. See docs/releasing.md.
# Usage: release-notes.py <changelog-section> <asset-directory>
# <changelog-section> is a version, or "Unreleased" for a dry run.
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORMS = {
    "x86-64-linux": "Linux x86-64, including WSL 2",
    "x86-64-darwin": "macOS: an x86-64 build, which Apple Silicon runs under Rosetta 2. "
    "It is not a native arm64 build.",
}
SHARED = ("version", "tag", "sourceRevision", "dirty", "virgilRevision")


def fail(message):
    sys.exit("release-notes.py: error: " + message)


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


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: release-notes.py <changelog-section> <asset-directory>")
    heading, assets = sys.argv[1:]
    manifests = []
    for path in sorted(glob.glob(os.path.join(assets, "*.manifest.json"))):
        with open(path, encoding="utf-8") as f:
            manifests.append(json.load(f))
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
