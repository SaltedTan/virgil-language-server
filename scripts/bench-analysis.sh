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
    local lines parse verify
    lines=$(cat "$@" | wc -l | tr -d ' ')
    parse=$(mktemp); verify=$(mktemp)
    for _ in $(seq "$RUNS"); do
        "$EXE" analyze --stats "$@" 2>&1 >/dev/null |
            sed -n 's/.*parse \([0-9]*\) us, verify \([0-9]*\) us.*/\1 \2/p' |
            while read -r p v; do echo "$p" >> "$parse"; echo "$v" >> "$verify"; done
    done
    echo "$label: $# files, $lines lines, $RUNS runs"
    echo "  parse  (us): $(summarize < "$parse")"
    echo "  verify (us): $(summarize < "$verify")"
    rm -f "$parse" "$verify"
}

bench "small fixture" "${small[@]}"
bench "Aeneas sources" "${aeneas[@]}"
