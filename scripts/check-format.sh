#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

build=$1
shift

file_list=$(mktemp "$build/check-format-files.XXXXXX")
trap 'rm -f -- "$file_list"' EXIT

git ls-files -z -- "*.v3" "$@" | while IFS= read -r -d '' path; do
    printf './%s\0' "$path"
done > "$file_list"
xargs -0 "$build/virgil-lsp" format --check < "$file_list"
