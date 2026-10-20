#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Build the server for one target and package it as a release archive. See
# docs/releasing.md.
# Usage: package-release.sh [--tag <tag>] [--allow-dirty] <output-directory>
#
# Writes virgil-lsp-<version>-<target>.tar.gz and a copy of its build manifest,
# virgil-lsp-<version>-<target>.manifest.json, to the output directory, and
# prints their paths. The archive holds one directory of the same name with
# virgil-lsp, virgil-lsp-worker, LICENSE, THIRD_PARTY_NOTICES.md, README.md,
# and manifest.json.
#
# --tag checks the tag with scripts/check-release.sh and records it in the
# manifest; without it, the manifest's tag is null. The checkout must be clean
# and Virgil must be at the pinned revision. See docs/releasing.md for the
# --allow-dirty option for local builds; releases never use it.
#
# VIRGIL, VIRGIL_V3C, V3C_TARGET, and WORKER_HEAP select the build as they do
# for make (docs/development.md). The build runs in a new temporary directory,
# so the manifest describes exactly what is packaged.
set -euo pipefail

usage="usage: package-release.sh [--tag <tag>] [--allow-dirty] <output-directory>"
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

tag=""
allow_dirty=0
while [ "$#" -gt 0 ]; do
    case $1 in
        --tag) tag=${2:?$usage}; shift 2 ;;
        --allow-dirty) allow_dirty=1; shift ;;
        -*) echo "$usage" >&2; exit 2 ;;
        *) break ;;
    esac
done
[ "$#" -eq 1 ] || { echo "$usage" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd)

# make runs in the checkout, so relative VIRGIL and VIRGIL_V3C paths are
# relative to it. VIRGIL keeps the Makefile's relative default: the executables
# record their source paths, so an absolute one would make them differ from
# the ones that make test builds.
cd "$ROOT"
virgil=${VIRGIL:-vendor/virgil}

if [ -n "$tag" ]; then
    version=$(scripts/check-release.sh "$tag")
else
    version=$(tr -d '[:space:]' < VERSION)
fi
case $version in
    "" | *[!0-9A-Za-z.+-]*) echo "error: VERSION '$version' is not a version" >&2; exit 1 ;;
esac

target=$(scripts/v3c.sh -print-target)
compiler=$(scripts/v3c.sh -print-compiler)
worker_heap=${WORKER_HEAP:-$(make -s --no-print-directory print-worker-heap)}

# Virgil's bin/v3c launcher replaces itself with a symlink to a host compiler
# the first time it runs. The build doesn't use it (scripts/v3c.sh passes the
# compiler explicitly), so restore it rather than build a "-dirty" revision.
if [ "$(git -C "$virgil" status --porcelain -- bin/v3c)" = " T bin/v3c" ]; then
    echo "note: restoring $virgil/bin/v3c, which Virgil replaced with a symlink" >&2
    git -C "$virgil" checkout -- bin/v3c
fi

revision=$(git rev-parse HEAD)
virgil_revision=$(git -C "$virgil" rev-parse HEAD)
pinned=$(git rev-parse HEAD:vendor/virgil)
problems=""
# Untracked files matter only under src/, which the Makefile compiles whole.
if [ -n "$(git status --porcelain --untracked-files=no --ignore-submodules=all)" ] ||
   [ -n "$(git status --porcelain --untracked-files=all -- src)" ]; then
    problems="$problems  the checkout has uncommitted changes (git status)\n"
fi
if [ "$virgil_revision" != "$pinned" ]; then
    problems="$problems  Virgil is at $virgil_revision, not the pinned $pinned\n"
fi
if [ -n "$(git -C "$virgil" status --porcelain)" ]; then
    problems="$problems  the Virgil checkout at $virgil has changes (git -C $virgil status)\n"
fi
dirty=false
if [ -n "$problems" ]; then
    if [ "$allow_dirty" -eq 0 ]; then
        printf "error: refusing to package:\n%b" "$problems" >&2
        echo "       use --allow-dirty to package anyway; releases need a clean checkout" >&2
        exit 1
    fi
    printf "warning: packaging anyway (--allow-dirty):\n%b" "$problems" >&2
    dirty=true
fi

sha256() {
    if command -v sha256sum > /dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d ' ' -f 1
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# Standard output is left for the paths of the files written.
make --no-print-directory BUILD="$WORK/build" WORKER_HEAP="$worker_heap" all >&2

name=virgil-lsp-$version-$target
stage=$WORK/stage/$name
mkdir -p "$stage"
cp "$WORK/build/virgil-lsp" "$WORK/build/virgil-lsp-worker" "$stage/"
cp LICENSE THIRD_PARTY_NOTICES.md "$stage/"

case $target in
    x86-64-linux)
        platform="Linux x86-64. WSL 2 on x86-64 Windows is expected to run it, on a best-effort
basis: it isn't tested there."
        ;;
    x86-64-darwin)
        platform="macOS. This is an Intel (x86-64) build, not a native arm64 one: Apple Silicon
Macs run it under Rosetta 2, which you can install with
\`softwareupdate --install-rosetta --agree-to-license\`."
        ;;
    *)
        platform="The \`$target\` target."
        ;;
esac
cat > "$stage/README.md" <<README
# virgil-lsp $version ($target)

The [Virgil Language Server](https://github.com/SaltedTan/virgil-language-server),
an unofficial language server for the Virgil programming language, for
$platform

- \`virgil-lsp\` is the server. Editors start it as \`virgil-lsp --stdio\`.
- \`virgil-lsp-worker\` is its analysis worker. The server starts the worker from
  the directory that holds the server's real path, so keep the two together. A
  symlink to \`virgil-lsp\` from a directory on your \`PATH\` works.
- \`manifest.json\` records the version, the exact server and Virgil revisions,
  and how the executables were built.
- \`LICENSE\` and \`THIRD_PARTY_NOTICES.md\` cover the server and the Virgil
  compiler and libraries compiled into it.

To install:

    mkdir -p ~/.local/opt ~/.local/bin
    tar -xzf $name.tar.gz -C ~/.local/opt
    ln -sf ~/.local/opt/$name/virgil-lsp ~/.local/bin/virgil-lsp
    virgil-lsp --version

The archive doesn't include Virgil's library sources. Projects that use
\`virgilDependencies\` need the editor to send a Virgil root. The release
documentation covers checksums, editors, remote machines, and the Virgil root:
https://github.com/SaltedTan/virgil-language-server/blob/${tag:-$revision}/docs/releasing.md#installing
README

case $compiler in /*) ;; *) compiler=$ROOT/$compiler ;; esac
virgil_root=$(cd "$virgil" && pwd)
case $compiler in
    "$virgil_root"/*) compiler_path=${compiler#"$virgil_root"/} ;;
    *) compiler_path=$compiler ;;
esac
python3 - "$stage/manifest.json" "$version" "$tag" "$target" "$revision" "$dirty" \
    "$virgil_revision" "$worker_heap" "$compiler_path" "$(sha256 "$compiler")" \
    "$(sha256 "$stage/virgil-lsp")" "$(sha256 "$stage/virgil-lsp-worker")" <<'PY'
import json, sys
(out, version, tag, target, revision, dirty, virgil_revision, worker_heap,
 compiler_path, compiler_sha256, server_sha256, worker_sha256) = sys.argv[1:]
manifest = {
    "manifestVersion": 1,
    "name": "virgil-lsp",
    "version": version,
    "tag": tag or None,
    "target": target,
    "sourceRevision": revision,
    "dirty": dirty == "true",
    "virgilRevision": virgil_revision,
    "workerHeap": worker_heap,
    "compiler": {"path": compiler_path, "sha256": compiler_sha256},
    "sha256": {"virgil-lsp": server_sha256, "virgil-lsp-worker": worker_sha256},
}
with open(out, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY

chmod 755 "$stage" "$stage/virgil-lsp" "$stage/virgil-lsp-worker"
chmod 644 "$stage/LICENSE" "$stage/THIRD_PARTY_NOTICES.md" "$stage/README.md" "$stage/manifest.json"
# COPYFILE_DISABLE keeps macOS tar from adding AppleDouble (._*) files.
(cd "$WORK/stage" && COPYFILE_DISABLE=1 tar -czf "$OUT/$name.tar.gz" "$name")
cp "$stage/manifest.json" "$OUT/$name.manifest.json"
echo "$OUT/$name.tar.gz"
echo "$OUT/$name.manifest.json"
