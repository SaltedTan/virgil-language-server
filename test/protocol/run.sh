#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Golden transcript tests: runs "virgil-lsp --stdio" on recorded input bytes
# and compares standard output byte for byte. Each directory next to this
# script is one case:
#
#   input        The bytes written to standard input.
#   input.<n>    Instead of input: parts written one after another, numbered
#                from 1 (at most 9), with a pause between them, so that the
#                server reads them separately.
#   output       The expected standard output. @VERSION@ stands for the
#                server version, and the Content-Length of a message that
#                contains it is checked as written, then adjusted.
#   status       The expected exit status.
#   args         Optional. Arguments added after --stdio, on one line.
#   stderr       Optional. Extended regular expressions, one per line of
#                standard error: line n must match expression n, and the
#                line counts must be equal. Without this file, standard error
#                must be empty.
#
# Usage: test/protocol/run.sh <path-to-virgil-lsp> [case...]
set -uo pipefail
# Byte semantics for string lengths and offsets.
export LC_ALL=C

EXE=${1:?usage: run.sh <path-to-virgil-lsp> [case...]}
shift
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

VERSION=$("$EXE" --version | head -n 1)
VERSION=${VERSION#* }
HEADER_RE=$'^Content-Length: ([0-9]+)\r\n\r\n'

# expand <file>: prints the expected output in <file> with @VERSION@
# substituted. Fails if the file isn't a sequence of correctly framed messages.
expand() {
    local rest='' len payload
    IFS= read -r -d '' rest < "$1"
    while [ -n "$rest" ]; do
        [[ $rest =~ $HEADER_RE ]] || { echo "$1: expected a Content-Length header" >&2; return 1; }
        len=${BASH_REMATCH[1]}
        rest=${rest:${#BASH_REMATCH[0]}}
        payload=${rest:0:$len}
        [ "${#payload}" -eq "$len" ] || { echo "$1: payload shorter than Content-Length $len" >&2; return 1; }
        rest=${rest:$len}
        payload=${payload//@VERSION@/$VERSION}
        printf 'Content-Length: %d\r\n\r\n%s' "${#payload}" "$payload"
    done
}

# feed <case-dir>: writes the input of the case.
feed() {
    if [ -f "$1/input" ]; then
        cat "$1/input"
        return
    fi
    local part first=1
    for part in "$1"/input.[1-9]; do
        [ "$first" -eq 1 ] || sleep 0.2
        first=0
        cat "$part"
    done
}

# run_case <case-dir>: returns 0 if the case passes.
run_case() {
    local c=$1 ok=1 want got pat n
    local -a args=()
    if [ -f "$c/args" ]; then read -r -a args < "$c/args"; fi
    "$EXE" --stdio ${args[@]+"${args[@]}"} < <(feed "$c") > "$TMP/out" 2> "$TMP/err"
    got=$?
    read -r want < "$c/status"
    [ "$got" -eq "$want" ] || { ok=0; echo "  exit: expected $want, got $got"; }
    if ! expand "$c/output" > "$TMP/want"; then
        ok=0
    elif ! cmp -s "$TMP/want" "$TMP/out"; then
        ok=0
        echo "  stdout: differs from"
        cat -v "$TMP/want" | sed 's/^/  expected| /'
        echo
    fi
    if [ -f "$c/stderr" ]; then
        if [ "$(wc -l < "$c/stderr")" -ne "$(wc -l < "$TMP/err")" ]; then
            ok=0
            echo "  stderr: expected $(wc -l < "$c/stderr" | tr -d ' ') lines"
        fi
        n=0
        while IFS= read -r pat; do
            n=$((n + 1))
            sed -n "${n}p" "$TMP/err" | grep -Eq -- "$pat" || { ok=0; echo "  stderr: line $n does not match /$pat/"; }
        done < "$c/stderr"
    elif [ -s "$TMP/err" ]; then
        ok=0
        echo "  stderr: expected empty"
    fi
    if [ "$ok" -eq 0 ]; then
        cat -v "$TMP/out" | sed 's/^/  stdout| /'
        echo
        sed 's/^/  stderr| /' "$TMP/err"
    fi
    [ "$ok" -eq 1 ]
}

pass=0
fail=0
if [ $# -gt 0 ]; then
    cases=("$@")
else
    cases=()
    for c in "$DIR"/*/; do cases+=("$(basename "$c")"); done
fi
for name in "${cases[@]}"; do
    if run_case "$DIR/$name" > "$TMP/report"; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL: $name"
        cat "$TMP/report"
    fi
done
echo "protocol: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && [ "$pass" -gt 0 ]
