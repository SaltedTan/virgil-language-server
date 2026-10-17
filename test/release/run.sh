#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for scripts/check-release.sh, which the release workflow runs on every
# tag. Each case writes VERSION and CHANGELOG.md to a temporary repository root.
# Usage: test/release/run.sh
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CHECK=$ROOT/scripts/check-release.sh
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

# expect <name> <expected-exit> <stdout|-> <stderr-pattern|-> <version> <changelog-headings> <tag>
# Standard output must equal <stdout> exactly, and standard error must be empty
# when <stderr-pattern> is ""; "-" skips a check.
expect() {
    local name=$1 code=$2 want_out=$3 err_pat=$4 version=$5 headings=$6 tag=$7
    printf '%b' "$version" > "$TMP/VERSION"
    printf '# Changelog\n\n%b\n' "$headings" > "$TMP/CHANGELOG.md"
    "$CHECK" "$tag" "$TMP" > "$TMP/out" 2> "$TMP/err"
    local got=$? ok=1
    [ "$got" -eq "$code" ] || { ok=0; echo "  exit: expected $code, got $got"; }
    if [ "$want_out" != "-" ] && [ "$(cat "$TMP/out")" != "$want_out" ]; then
        ok=0; echo "  stdout: expected '$want_out'"
    fi
    if [ -z "$err_pat" ]; then
        [ ! -s "$TMP/err" ] || { ok=0; echo "  stderr: expected empty"; }
    elif [ "$err_pat" != "-" ] && ! grep -Eq -- "$err_pat" "$TMP/err"; then
        ok=0; echo "  stderr: no match for /$err_pat/"
    fi
    if [ "$ok" -eq 1 ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL: $name"
        sed 's/^/  stdout| /' "$TMP/out"
        sed 's/^/  stderr| /' "$TMP/err"
    fi
}

released='## [Unreleased]\n\n## [0.1.0-alpha.1] - 2026-11-02\n\n### Added\n'

expect "matching tag prints the version" 0 "0.1.0-alpha.1" "" \
    "0.1.0-alpha.1\n" "$released" v0.1.0-alpha.1
expect "VERSION may end without a newline" 0 "0.1.0-alpha.1" "" \
    "0.1.0-alpha.1" "$released" v0.1.0-alpha.1
expect "build metadata is part of the version" 0 "1.2.3-rc.1+linux.5" "" \
    "1.2.3-rc.1+linux.5" "## [1.2.3-rc.1+linux.5]" v1.2.3-rc.1+linux.5
for version in 0.1.0 0.0.0-dev 0.1.0-0.10.0 0.1.0-01a.01-.a01 0.1.0+01.002; do
    expect "valid semantic version $version" 0 "$version" "" \
        "$version" "## [$version]" "v$version"
done
expect "tag without v" 1 "" "expected 'v0.1.0-alpha.1'" \
    "0.1.0-alpha.1" "$released" 0.1.0-alpha.1
expect "tag for another version" 1 "" "tag 'v0.1.0-alpha.2' does not match VERSION '0.1.0-alpha.1'" \
    "0.1.0-alpha.1" "$released" v0.1.0-alpha.2
expect "tag for a prefix of the version" 1 "" "does not match" \
    "0.1.0-alpha.10" "## [0.1.0-alpha.10]" v0.1.0-alpha.1
expect "version that isn't semantic" 1 "" "VERSION '0.1' is not a semantic version" \
    "0.1" "## [0.1]" v0.1
expect "leading zero in the version" 1 "" "not a semantic version" \
    "0.01.0" "## [0.01.0]" v0.01.0
for version in 0.1.0-01 0.1.0-alpha.01.beta 0.1.0-alpha.01 0.1.0-alpha.00; do
    expect "leading zero in numeric prerelease $version" 1 "" "not a semantic version" \
        "$version" "## [$version]" "v$version"
done
expect "empty prerelease identifier" 1 "" "not a semantic version" \
    "0.1.0-alpha..1" "## [0.1.0-alpha..1]" v0.1.0-alpha..1
expect "changelog without the version's section" 1 "" "CHANGELOG.md has no '## \[0.1.0-alpha.1\]' section" \
    "0.1.0-alpha.1" '## [Unreleased]\n\n### Added\n' v0.1.0-alpha.1
expect "changelog section for a longer version" 1 "" "no '## \[0.1.0-alpha.1\]' section" \
    "0.1.0-alpha.1" '## [0.1.0-alpha.10] - 2026-11-02' v0.1.0-alpha.1
expect "changelog subsection heading" 1 "" "no '## \[0.1.0-alpha.1\]' section" \
    "0.1.0-alpha.1" '### [0.1.0-alpha.1]' v0.1.0-alpha.1
expect "changelog mention outside a heading" 1 "" "no '## \[0.1.0-alpha.1\]' section" \
    "0.1.0-alpha.1" 'See ## [0.1.0-alpha.1] below.' v0.1.0-alpha.1

echo "check-release.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
