#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Check that a commit can be released under a tag, and print its version. The
# tag must be "v" followed by VERSION, VERSION must be a semantic version, and
# CHANGELOG.md must have a "## [<version>]" section. See docs/releasing.md.
# Usage: check-release.sh <tag> [<repository-root>]
#        check-release.sh --is-prerelease <version>
#        check-release.sh --archive <archive>
#        check-release.sh --runtime-source <file>
# --is-prerelease validates a semantic version and prints "true" if it carries
# a SemVer prerelease identifier or "false" otherwise.
# --runtime-source checks that a file is the pinned source archive of the JVM
# archive's Java runtime (scripts/jvm-runtime.json), by name and SHA-256.
set -euo pipefail

# Archive verification is also used before extraction by the smoke test.
if [ "${1:-}" = --archive ]; then
    ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    exec python3 "$ROOT/scripts/release-library.py" check "${2:?usage: check-release.sh --archive <archive>}"
fi
if [ "${1:-}" = --runtime-source ]; then
    ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    exec python3 "$ROOT/scripts/jvm-runtime.py" check-source "${2:?usage: check-release.sh --runtime-source <file>}"
fi

id='[0-9A-Za-z-]+'
prerelease_id='(0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)'
semver="^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-$prerelease_id(\.$prerelease_id)*)?(\+$id(\.$id)*)?$"

fail() {
    echo "error: $1" >&2
    exit 1
}

if [ "${1:-}" = --is-prerelease ]; then
    version=${2:?usage: check-release.sh --is-prerelease <version>}
    [[ $version =~ $semver ]] || fail "VERSION '$version' is not a semantic version"
    if [ -n "${BASH_REMATCH[4]}" ]; then
        echo true
    else
        echo false
    fi
    exit 0
fi

TAG=${1:?usage: check-release.sh <tag> [<repository-root>]}
ROOT=${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}

[ -f "$ROOT/VERSION" ] || fail "$ROOT/VERSION not found"
version=$(tr -d '[:space:]' < "$ROOT/VERSION")

[[ $version =~ $semver ]] || fail "VERSION '$version' is not a semantic version"
[ "$TAG" = "v$version" ] || fail "tag '$TAG' does not match VERSION '$version'; expected 'v$version'"

heading="## [$version]"
awk -v h="$heading" 'index($0, h) == 1 { found = 1 } END { exit !found }' "$ROOT/CHANGELOG.md" ||
    fail "CHANGELOG.md has no '$heading' section"

echo "$version"
