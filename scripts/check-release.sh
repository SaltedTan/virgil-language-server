#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Check that a commit can be released under a tag, and print its version. The
# tag must be "v" followed by VERSION, VERSION must be a semantic version, and
# CHANGELOG.md must have a "## [<version>]" section. See docs/releasing.md.
# Usage: check-release.sh <tag> [<repository-root>]
#        check-release.sh --archive <archive>
set -euo pipefail

# Archive verification is also used before extraction by the smoke test.
if [ "${1:-}" = --archive ]; then
    ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    pinned=$(git -C "$ROOT" rev-parse HEAD:vendor/virgil)
    exec python3 "$ROOT/scripts/release-library.py" check "${2:?usage: check-release.sh --archive <archive>}" "$pinned"
fi

TAG=${1:?usage: check-release.sh <tag> [<repository-root>]}
ROOT=${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}

fail() {
    echo "error: $1" >&2
    exit 1
}

[ -f "$ROOT/VERSION" ] || fail "$ROOT/VERSION not found"
version=$(tr -d '[:space:]' < "$ROOT/VERSION")

id='[0-9A-Za-z-]+'
prerelease_id='(0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
semver="^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-$prerelease_id(\.$prerelease_id)*)?(\+$id(\.$id)*)?$"
[[ $version =~ $semver ]] || fail "VERSION '$version' is not a semantic version"
[ "$TAG" = "v$version" ] || fail "tag '$TAG' does not match VERSION '$version'; expected 'v$version'"

heading="## [$version]"
awk -v h="$heading" 'index($0, h) == 1 { found = 1 } END { exit !found }' "$ROOT/CHANGELOG.md" ||
    fail "CHANGELOG.md has no '$heading' section"

echo "$version"
