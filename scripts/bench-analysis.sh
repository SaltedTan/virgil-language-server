#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Measure whole-program parse and verify time with `virgil-lsp analyze --stats`
# for a small fixture and for the Aeneas sources the server is built from.
# Each run is a fresh process: repeated large analyses in one process currently
# exhaust the heap because Aeneas keeps every analyzed program reachable.
#
# Usage: scripts/bench-analysis.sh [runs]   (default 10; run `make` first)
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VIRGIL=${VIRGIL:-$ROOT/vendor/virgil}
EXE=${EXE:-$ROOT/build/virgil-lsp}
RUNS=${1:-10}
if [[ ! $RUNS =~ ^[1-9][0-9]*$ ]]; then
    echo "error: runs must be a positive integer" >&2
    exit 1
fi

# Install cleanup before allocating anything, including on interrupted runs.
TMP=
trap 'if [[ -n $TMP ]]; then rm -rf "$TMP"; fi' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
TMP=$(mktemp -d)

small=("$ROOT"/test/fixtures/analysis/two-file/*.v3)
# Same file set as AENEAS_SRC in the Makefile.
aeneas=("$VIRGIL"/aeneas/src/*/*.v3)
for dep in $(grep -v '^lib/test/' "$VIRGIL/aeneas/DEPS"); do
    aeneas+=("$VIRGIL"/$dep)
done

# Prints min, median, and max of the numbers on stdin.
summarize() {
    sort -n | awk '{ v[NR] = $1 } END { printf "min %d, median %d, max %d", v[1], v[int((NR + 1) / 2)], v[NR] }'
}

bench() {
    local label=$1; shift
    local lines parse=$TMP/parse verify=$TMP/verify stderr=$TMP/stderr
    local run status samples p v
    lines=$(cat "$@" | wc -l | tr -d ' ')
    : > "$parse"; : > "$verify"
    for run in $(seq "$RUNS"); do
        if "$EXE" analyze --stats "$@" > /dev/null 2> "$stderr"; then
            :
        else
            status=$?
            echo "error: $label run $run failed (exit status $status)" >&2
            cat "$stderr" >&2
            exit "$status"
        fi
        samples=$(sed -n 's/.*parse \([0-9][0-9]*\) us, verify \([0-9][0-9]*\) us.*/\1 \2/p' "$stderr")
        if [[ -z $samples ]]; then
            echo "error: $label run $run produced no timing sample (expected --stats parse/verify timings)" >&2
            cat "$stderr" >&2
            exit 1
        fi
        while read -r p v; do
            echo "$p" >> "$parse"; echo "$v" >> "$verify"
        done <<< "$samples"
    done
    echo "$label: $# files, $lines lines, $RUNS runs"
    echo "  parse  (us): $(summarize < "$parse")"
    echo "  verify (us): $(summarize < "$verify")"
}

bench "small fixture" "${small[@]}"
bench "Aeneas sources" "${aeneas[@]}"
