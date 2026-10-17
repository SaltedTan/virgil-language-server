#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Smoke-check a VSIX from `npm run package` in clients/vscode before the release
# workflow tests and attaches it. The VSIX must be a zip file named
# <name>-<version>.vsix, and its extension/package.json must have the name,
# publisher, and version of the extension directory's package.json. It must
# hold the extension's main file and its runtime dependencies, and nothing that
# .vscodeignore leaves out (sources, tests, source maps, type declarations, the
# test's VS Code download) or that the lockfile marks as a development
# dependency, such as @vscode/vsce-sign. See docs/releasing.md.
# Usage: check-vsix.py <vsix> [<extension-directory>]
# The extension directory defaults to clients/vscode.
import json
import os
import posixpath
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDENTITY = ("name", "publisher", "version")
# Top-level paths in the extension directory that .vscodeignore leaves out.
EXCLUDED = ("src/", "test/", ".vscode/", ".vscode-test/", "tsconfig.json")
EXCLUDED_SUFFIXES = (".map", ".d.ts")
# The node_modules directory of an installed package, nested or not.
PACKAGE = re.compile(r"^(?:node_modules/(?:@[^/]+/)?[^/]+/)+")


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: check-vsix.py <vsix> [<extension-directory>]")
    vsix = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) == 3 else os.path.join(ROOT, "clients", "vscode")
    with open(os.path.join(source, "package.json"), encoding="utf-8") as f:
        expected = json.load(f)
    with open(os.path.join(source, "package-lock.json"), encoding="utf-8") as f:
        lock = json.load(f)["packages"]

    name = os.path.basename(vsix)
    if not zipfile.is_zipfile(vsix):
        sys.exit("check-vsix.py: error: %s is not a zip file" % vsix)
    with zipfile.ZipFile(vsix) as z:
        entries = z.namelist()
        try:
            manifest = json.loads(z.read("extension/package.json").decode("utf-8"))
        except KeyError:
            sys.exit("check-vsix.py: error: %s has no extension/package.json" % name)
        except ValueError as e:
            sys.exit("check-vsix.py: error: %s: extension/package.json is not valid JSON: %s" % (name, e))
    if not isinstance(manifest, dict):
        sys.exit("check-vsix.py: error: %s: extension/package.json is not a JSON object" % name)

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
    for dep in sorted(expected.get("dependencies", {})):
        if "node_modules/%s/package.json" % dep not in files:
            problems.append("the runtime dependency %s is missing" % dep)
    dev = set()
    for f in files:
        package = PACKAGE.match(f)
        if f.endswith(EXCLUDED_SUFFIXES) or (package is None and f.startswith(EXCLUDED)):
            problems.append("it holds the development file %s" % f)
        if package is not None:
            # A package that the lockfile doesn't list isn't a runtime one either.
            key = package.group(0).rstrip("/")
            if lock.get(key, {"dev": True}).get("dev", False):
                dev.add(key)
    problems += ["it holds %s, which isn't a runtime dependency in package-lock.json" % d
                 for d in sorted(dev)]

    if problems:
        for p in problems:
            print("check-vsix.py: error: %s: %s" % (name, p), file=sys.stderr)
        sys.exit(1)
    print("%s: %s.%s %s, %d files" % (name, expected["publisher"], expected["name"],
                                      expected["version"], len(entries)))


if __name__ == "__main__":
    main()
