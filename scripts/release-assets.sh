#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Checks that a release assets directory holds exactly the files a tag release
# attaches, and lists them: the three server archives (x86-64-linux,
# x86-64-darwin, and the JVM build for Apple Silicon, jvm-arm64-darwin) and
# their build manifests, the five VSIX files, the JVM runtime's source archive
# under the name that scripts/jvm-runtime.json pins, and SHA256SUMS. The VSIX
# files carry the extension's own version, which the server's VERSION does
# not, so they are matched by name shape and checked for one shared extension
# version and one file per target, rather than derived from VERSION. See
# docs/releasing.md.
# Usage: scripts/release-assets.sh <version> <assets-dir>
# Prints the file names, one per line, on success.
set -uo pipefail

if [ $# -ne 2 ]; then
    echo "Usage: $(basename "$0") <version> <assets-dir>" >&2
    exit 2
fi
version=$1
dir=$2

if [ -z "$version" ] || [ ! -d "$dir" ]; then
    echo "error: need a non-empty version and an assets directory, got: $version $dir" >&2
    exit 2
fi

expected=()
for target in x86-64-linux x86-64-darwin jvm-arm64-darwin; do
    expected+=("virgil-lsp-$version-$target.tar.gz")
    expected+=("virgil-lsp-$version-$target.manifest.json")
done

# The JVM archive's runtime source, which every release with a JVM asset
# attaches; scripts/check-release.sh --runtime-source checks its contents.
pin=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/jvm-runtime.json
if ! source_name=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["source"]["name"])' "$pin") ||
   [ -z "$source_name" ]; then
    echo "error: cannot read the runtime source archive's name from $pin" >&2
    exit 2
fi
expected+=("$source_name")

# The five VSIX files: one per platform target, the JVM build's darwin-arm64
# one, and one universal, all carrying the extension's version.
shopt -s nullglob
vsix=("$dir"/virgil-lsp-vscode-*.vsix)
shopt -u nullglob
if [ "${#vsix[@]}" -ne 5 ]; then
    echo "error: expected 5 VSIX files, found ${#vsix[@]}" >&2
    exit 1
fi
ext_version=
for f in "${vsix[@]}"; do
    name=${f##*/}
    v=${name#virgil-lsp-vscode-}
    v=${v%.vsix}
    case $v in
        *-darwin-arm64-jvm) v=${v%-darwin-arm64-jvm} ;;
        *-linux-x64) v=${v%-linux-x64} ;;
        *-darwin-arm64) v=${v%-darwin-arm64} ;;
        *-darwin-x64) v=${v%-darwin-x64} ;;
    esac
    if [ -z "$v" ]; then
        echo "error: no extension version in VSIX name: $name" >&2
        exit 1
    fi
    if [ -z "$ext_version" ]; then
        ext_version=$v
    elif [ "$v" != "$ext_version" ]; then
        echo "error: VSIX files disagree on the extension version: $ext_version and $v" >&2
        exit 1
    fi
done
for target in linux-x64 darwin-arm64 darwin-x64 darwin-arm64-jvm; do
    found=
    for f in "${vsix[@]}"; do
        [ "${f##*/}" = "virgil-lsp-vscode-$ext_version-$target.vsix" ] && found=1
    done
    if [ -z "$found" ]; then
        echo "error: missing VSIX: virgil-lsp-vscode-$ext_version-$target.vsix" >&2
        exit 1
    fi
done
found=
for f in "${vsix[@]}"; do
    [ "${f##*/}" = "virgil-lsp-vscode-$ext_version.vsix" ] && found=1
done
if [ -z "$found" ]; then
    echo "error: missing universal VSIX: virgil-lsp-vscode-$ext_version.vsix" >&2
    exit 1
fi
for f in "${vsix[@]}"; do
    expected+=("${f##*/}")
done
expected+=("SHA256SUMS")

# The directory must hold exactly those files, no more and no fewer. Every
# entry counts, hidden ones included, and each must be a regular file with a
# plain name, so the newline-separated comparison below is exact.
actual=()
shopt -s nullglob dotglob
for f in "$dir"/*; do
    name=${f##*/}
    case $name in
        *[!A-Za-z0-9._+-]*)
            echo "error: unexpected name in the assets directory: $(printf '%q' "$name")" >&2
            exit 1 ;;
    esac
    if [ -L "$f" ] || [ ! -f "$f" ]; then
        echo "error: not a regular file in the assets directory: $name" >&2
        exit 1
    fi
    actual+=("$name")
done
shopt -u nullglob dotglob

want=$(printf '%s\n' "${expected[@]}" | sort)
have=$(printf '%s\n' "${actual[@]}" | sort)
if [ "$want" != "$have" ]; then
    echo "error: the assets directory holds the wrong files:" >&2
    diff <(printf '%s\n' "$have") <(printf '%s\n' "$want") >&2 || true
    exit 1
fi

printf '%s\n' "${expected[@]}" | sort
