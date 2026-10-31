#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Checks that a release assets directory holds exactly the files a tag release
# attaches, and lists them: the two server archives and their build manifests,
# the four VSIX files, and SHA256SUMS. The VSIX files carry the extension's
# own version, which the server's VERSION does not, so they are matched by
# name shape and checked for one shared extension version and one file per
# target, rather than derived from VERSION.
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
for target in x86-64-linux x86-64-darwin; do
  expected+=("virgil-lsp-$version-$target.tar.gz")
  expected+=("virgil-lsp-$version-$target.manifest.json")
done

# The four VSIX files: one per platform target and one universal, all
# carrying the extension's version.
shopt -s nullglob
vsix=("$dir"/virgil-lsp-vscode-*.vsix)
shopt -u nullglob
if [ "${#vsix[@]}" -ne 4 ]; then
  echo "error: expected 4 VSIX files, found ${#vsix[@]}" >&2
  exit 1
fi
ext_version=
for f in "${vsix[@]}"; do
  name=${f##*/}
  v=${name#virgil-lsp-vscode-}
  v=${v%.vsix}
  case $v in
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
for target in linux-x64 darwin-arm64 darwin-x64; do
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

# The directory must hold exactly those files, no more and no fewer.
actual=()
shopt -s nullglob
for f in "$dir"/*; do
  [ -f "$f" ] && actual+=("${f##*/}")
done
shopt -u nullglob

want=$(printf '%s\n' "${expected[@]}" | sort)
have=$(printf '%s\n' "${actual[@]}" | sort)
if [ "$want" != "$have" ]; then
  echo "error: the assets directory holds the wrong files:" >&2
  diff <(printf '%s\n' "$have") <(printf '%s\n' "$want") >&2 || true
  exit 1
fi

printf '%s\n' "${expected[@]}" | sort
