#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Check that a commit can be released under a tag, and print its version. The
# tag must be "v" followed by VERSION, VERSION must be a semantic version, and
# CHANGELOG.md must have a "## [<version>]" section. See docs/releasing.md.
# Usage: check-release.sh <tag> [<repository-root>]
set -euo pipefail

TAG=${1:?usage: check-release.sh <tag> [<repository-root>]}
ROOT=${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}

fail() {
    echo "error: $1" >&2
    exit 1
}

[ -f "$ROOT/VERSION" ] || fail "$ROOT/VERSION not found"
version=$(tr -d '[:space:]' < "$ROOT/VERSION")

# Semantic Versioning 2.0.0, without its rule against leading zeros in numeric
# prerelease identifiers.
id='[0-9A-Za-z-]+'
semver="^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-$id(\.$id)*)?(\+$id(\.$id)*)?$"
[[ $version =~ $semver ]] || fail "VERSION '$version' is not a semantic version"
[ "$TAG" = "v$version" ] || fail "tag '$TAG' does not match VERSION '$version'; expected 'v$version'"

heading="## [$version]"
awk -v h="$heading" 'index($0, h) == 1 { found = 1 } END { exit !found }' "$ROOT/CHANGELOG.md" ||
    fail "CHANGELOG.md has no '$heading' section"

echo "$version"
