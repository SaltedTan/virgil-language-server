#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Command-line smoke tests for the virgil-lsp executable.
# Usage: test/cli/run.sh <path-to-virgil-lsp>
set -uo pipefail

EXE=${1:?usage: run.sh <path-to-virgil-lsp>}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
FIXTURES=$ROOT/test/fixtures
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

# expect <name> <expected-exit> <stdout-pattern|-> <stderr-pattern|-> -- <args...>
# A pattern of "" requires the stream to be empty; "-" skips the check.
expect() {
    local name=$1 code=$2 out_pat=$3 err_pat=$4
    shift 5
    "$EXE" "$@" > "$TMP/out" 2> "$TMP/err"
    local got=$?
    local ok=1
    [ "$got" -eq "$code" ] || { ok=0; echo "  exit: expected $code, got $got"; }
    check_stream stdout "$TMP/out" "$out_pat" || ok=0
    check_stream stderr "$TMP/err" "$err_pat" || ok=0
    if [ $ok -eq 1 ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL: $name"
        sed 's/^/  stdout| /' "$TMP/out"
        sed 's/^/  stderr| /' "$TMP/err"
    fi
}

check_stream() {
    local label=$1 file=$2 pat=$3
    if [ "$pat" = "-" ]; then return 0; fi
    if [ -z "$pat" ]; then
        [ ! -s "$file" ] || { echo "  $label: expected empty"; return 1; }
    else
        grep -Eq -- "$pat" "$file" || { echo "  $label: no match for /$pat/"; return 1; }
    fi
}

expect "version" 0 '^virgil-lsp [0-9]+\.[0-9]+\.[0-9]+' "" -- --version
expect "version reports virgil revision" 0 'virgil revision: [0-9a-f]{40}' "" -- --version
expect "help" 0 'Usage: virgil-lsp' "" -- --help
expect "no arguments keeps stdout clean" 2 "" 'Usage: virgil-lsp' --
expect "unknown argument keeps stdout clean" 2 "" "unknown argument" -- --bogus
expect "parse clean file" 0 'ok, 2 top-level declarations' "" -- parse "$FIXTURES/syntax/clean.v3"
expect "parse reports tab-expanded column" 1 'tab-error\.v3:3:17: ParseError' "" -- parse "$FIXTURES/syntax/tab-error.v3"
expect "parse missing file" 1 "" "cannot read file" -- parse "$TMP/missing.v3"

A=$FIXTURES/analysis
expect "analyze clean two-file program" 0 '^ok: 2 files parsed and verified$' "" -- \
    analyze "$A/two-file/shapes.v3" "$A/two-file/main.v3"
expect "analyze reports unresolved name" 1 'unresolved/main\.v3:6:35: UnresolvedIdentifier' "" -- \
    analyze "$A/two-file/shapes.v3" "$A/unresolved/main.v3"
expect "analyze reports type error" 1 'type-error/main\.v3:4:30: TypeError: expected int in var initialization, got Rect' "" -- \
    analyze "$A/two-file/shapes.v3" "$A/type-error/main.v3"
expect "analyze reports syntax error" 1 'syntax-error/main\.v3:5:25: ParseError' "" -- \
    analyze "$A/two-file/shapes.v3" "$A/syntax-error/main.v3"
expect "analyze follows a binding across files" 0 'main\.v3:4:25-4:31 -> COMPONENT Shapes @ .*two-file/shapes\.v3:8:11-8:17' "" -- \
    analyze --bindings "$A/two-file/shapes.v3" "$A/two-file/main.v3"
expect "analyze repeats with identical results" 0 '^ok: 2 files' "" -- \
    analyze --repeat=3 --bindings "$A/two-file/shapes.v3" "$A/two-file/main.v3"
expect "analyze stats go to stderr" 0 '^ok: 2 files' 'analysis 1: parse [0-9]+ us, verify [0-9]+ us' -- \
    analyze --stats "$A/two-file/shapes.v3" "$A/two-file/main.v3"
expect "analyze missing file" 1 "" "cannot read file" -- analyze "$TMP/missing.v3"
expect "analyze rejects a bad repeat count" 2 "" "invalid option" -- analyze --repeat=0 "$A/two-file/main.v3"
expect "analyze rejects unknown options" 2 "" "unknown option" -- analyze --bogus "$A/two-file/main.v3"

echo "cli: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
