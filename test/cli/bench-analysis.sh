#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
# Regression tests for the benchmark driver; no compiler build required.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir "$TMP/scratch"

cat > "$TMP/analyzer" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case $MODE in
    fail)
        echo 'HeapOverflow: test stderr' >&2
        exit 7
        ;;
    fail-aeneas)
        if [[ $3 != */two-file/* ]]; then
            echo 'Aeneas failure: test stderr' >&2
            exit 9
        fi
        ;;
    no-sample)
        echo 'changed stats format' >&2
        exit 0
        ;;
    missing-second)
        count=0
        [[ ! -f $COUNTER ]] || read -r count < "$COUNTER"
        echo "$((count + 1))" > "$COUNTER"
        if [[ $count == 1 ]]; then
            echo 'second run has no stats' >&2
            exit 0
        fi
        ;;
esac
echo 'stdout is not a timing sample'
echo 'analysis 1: parse 12 us, verify 34 us' >&2
SH
chmod +x "$TMP/analyzer"

# Each invocation must leave its dedicated TMPDIR empty, even on failure.
check() {
    local name=$1 exe=$2 mode=$3 runs=$4 want=$5 pattern=$6
    local got=0
    TMPDIR="$TMP/scratch" EXE="$exe" MODE="$mode" COUNTER="$TMP/count" \
        "$ROOT/scripts/bench-analysis.sh" "$runs" > "$TMP/out" 2> "$TMP/err" || got=$?
    if [[ $got != "$want" ]] || { [[ $pattern != - ]] && ! grep -Eq "$pattern" "$TMP/err"; } ||
        [[ -n $(find "$TMP/scratch" -mindepth 1 -print) ]]; then
        echo "FAIL: benchmark $name (exit $got, expected $want)"
        cat "$TMP/out" "$TMP/err"
        exit 1
    fi
}

check 'EXE=false reproduction' false fail 1 1 'small fixture run 1 failed \(exit status 1\)'
[[ ! -s $TMP/out ]]
check 'stderr and exit status' "$TMP/analyzer" fail 1 7 'small fixture run 1 failed \(exit status 7\)'
grep -q 'HeapOverflow: test stderr' "$TMP/err"
check 'failure after earlier results' "$TMP/analyzer" fail-aeneas 1 9 'Aeneas sources run 1 failed \(exit status 9\)'
grep -q '^small fixture:' "$TMP/out"
grep -q 'Aeneas failure: test stderr' "$TMP/err"
check 'no sample' "$TMP/analyzer" no-sample 1 1 'small fixture run 1 produced no timing sample'
grep -q 'changed stats format' "$TMP/err"
[[ ! -s $TMP/out ]]
check 'each run needs a sample' "$TMP/analyzer" missing-second 2 1 'small fixture run 2 produced no timing sample'
[[ ! -s $TMP/out ]]
check 'normal run' "$TMP/analyzer" normal 2 0 -
[[ ! -s $TMP/err ]]
grep -q '^small fixture: .*2 runs$' "$TMP/out"
grep -q '^Aeneas sources: .*2 runs$' "$TMP/out"
[[ $(grep -c 'parse  (us): min 12, median 12, max 12' "$TMP/out") == 2 ]]
[[ $(grep -c 'verify (us): min 34, median 34, max 34' "$TMP/out") == 2 ]]
check 'zero runs rejected' "$TMP/analyzer" normal 0 1 'runs must be a positive integer'
echo 'benchmark: 7 passed, 0 failed'
