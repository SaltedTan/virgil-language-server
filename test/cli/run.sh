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
    finish "$name" $ok
}

# expect_stdio <name> <expected-exit> <expected-stdout-file> <stderr-pattern|-> -- <args...>
# Runs "--stdio <args...>" with the caller's standard input. Standard output
# must equal the file byte for byte, so it can hold nothing but framed messages.
expect_stdio() {
    local name=$1 code=$2 want=$3 err_pat=$4
    shift 5
    "$EXE" --stdio "$@" > "$TMP/out" 2> "$TMP/err"
    local got=$?
    local ok=1
    [ "$got" -eq "$code" ] || { ok=0; echo "  exit: expected $code, got $got"; }
    cmp -s "$want" "$TMP/out" || { ok=0; echo "  stdout: differs from"; sed 's/^/  expected| /' "$want"; }
    check_stream stderr "$TMP/err" "$err_pat" || ok=0
    finish "$name" $ok
}

# finish <name> <ok>: counts a result, and prints the output of a failure.
finish() {
    local name=$1 ok=$2
    if [ "$ok" -eq 1 ]; then
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

# frame <payload>: prints the payload with a Content-Length header, in bytes.
frame() {
    printf 'Content-Length: %d\r\n\r\n%s' $(( $(printf %s "$1" | wc -c) )) "$1"
}
request() {  # request <id> <method> [params]
    local params=${3:-'{}'}
    printf '{"jsonrpc":"2.0","id":%s,"method":"%s","params":%s}' "$1" "$2" "$params"
}
not_found() {  # not_found <id> <method>
    printf '{"jsonrpc":"2.0","id":%s,"error":{"code":-32601,"message":"method not found: %s"}}' "$1" "$2"
}
E=$'\xc3\xa9'  # U+00E9, two bytes in UTF-8
INVALID='{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"a message must be a JSON object"}}'

# Requests, a notification, a non-object payload, and non-ASCII text. With no
# handlers registered yet, every request gets MethodNotFound.
{
    frame "$(request 1 initialize)"
    frame '{"jsonrpc":"2.0","method":"initialized","params":{}}'
    frame '[]'
    frame "$(request "\"$E\"" "h${E}llo")"
} > "$TMP/in"
{
    frame "$(not_found 1 initialize)"
    frame "$INVALID"
    frame "$(not_found "\"$E\"" "h${E}llo")"
} > "$TMP/want"
expect_stdio "stdio writes only framed replies" 0 "$TMP/want" "" -- < "$TMP/in"

# The same input, split so that one message arrives in two reads.
split_input() {
    head -c 30 "$TMP/in"
    sleep 0.2
    tail -c +31 "$TMP/in"
}
expect_stdio "stdio reads a message split across reads" 0 "$TMP/want" "" -- < <(split_input)

: > "$TMP/empty"
expect_stdio "stdio exits cleanly at the end of input" 0 "$TMP/empty" "" -- < "$TMP/empty"

# The server has sent no requests, so a response from the client matches none.
# It is not answered, and the reason goes to stderr.
frame '{"jsonrpc":"2.0","id":1,"result":null}' > "$TMP/in"
expect_stdio "stdio logs a response that matches no request" 0 "$TMP/empty" \
    'warning: ignored a response: no outstanding request has id 1' -- < "$TMP/in"

{ frame "$(request 1 big '["0123456789012345678901234567890123456789"]')"; frame "$(request 2 small)"; } > "$TMP/in"
frame "$(not_found 2 small)" > "$TMP/want"
expect_stdio "stdio skips a message over the limit" 0 "$TMP/want" \
    'warning: skipped a message: payload of [0-9]+ bytes is longer than the limit of 64 bytes' -- \
    --max-message-bytes=64 < "$TMP/in"

# Five million zeros: under the default 16 MiB message limit, but far more
# values than the heap can hold once parsed. The message is answered with a
# ParseError, and the next request is still read.
{
    printf '{"jsonrpc":"2.0","id":1,"method":"x","params":['
    yes '0,' | head -n 4999999 | tr -d '\n'
    printf '0]}'
} > "$TMP/dense"
{
    printf 'Content-Length: %d\r\n\r\n' $(( $(wc -c < "$TMP/dense") ))
    cat "$TMP/dense"
    frame "$(request 2 small)"
} > "$TMP/in"
{
    frame '{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"invalid JSON at byte 0: more than 500000 values"}}'
    frame "$(not_found 2 small)"
} > "$TMP/want"
expect_stdio "stdio rejects a message with too many values and keeps going" 0 "$TMP/want" "" -- < "$TMP/in"

{ frame "$(request 1 a)"; printf 'Content-Length: x\r\n\r\n{}'; frame "$(request 2 b)"; } > "$TMP/in"
frame "$(not_found 1 a)" > "$TMP/want"
expect_stdio "stdio stops at a malformed header" 1 "$TMP/want" \
    'error: malformed message header: Content-Length is invalid' -- < "$TMP/in"

# A bare LF proves the header is malformed, so the server must stop while the
# client still holds the pipe open. If it waited for more input instead, it
# would report the input ending inside a message once the pipe closed.
held_open() {
    frame "$(request 1 a)"
    printf 'Content-Length: 2\n\n{}'
    sleep 3
}
frame "$(not_found 1 a)" > "$TMP/want"
expect_stdio "stdio stops at a bare LF without waiting for more input" 1 "$TMP/want" \
    'error: malformed message header: header has an LF without a CR before it' -- < <(held_open)

printf 'Content-Length: 10\r\n\r\n{' > "$TMP/in"
expect_stdio "stdio fails when the input ends inside a message" 1 "$TMP/empty" \
    'error: input ended in the middle of a message' -- < "$TMP/in"

# A header alone, declaring the longest payload the limit allows. The payload
# is buffered as it arrives, so the input ending is reported, not a heap trap.
printf 'Content-Length: 16777216\r\n\r\n' > "$TMP/in"
expect_stdio "stdio fails when the input ends after a header at the maximum limit" 1 "$TMP/empty" \
    'error: input ended in the middle of a message' -- --max-message-bytes=16777216 < "$TMP/in"

expect "stdio rejects a bad message limit" 2 "" "invalid option" -- --stdio --max-message-bytes=0
expect "stdio rejects a message limit over nine digits" 2 "" "invalid option" -- --stdio --max-message-bytes=1234567890
expect "stdio rejects a message limit over the supported maximum" 2 "" \
    "invalid option '--max-message-bytes=16777217': the supported maximum is 16777216 bytes" -- \
    --stdio --max-message-bytes=16777217 < /dev/null
# The reproduction from #26: this limit used to be accepted, and the header
# alone then ran out of heap.
printf 'Content-Length: 999999999\r\n\r\n' > "$TMP/in"
expect_stdio "stdio rejects a limit too large for the heap" 2 "$TMP/empty" \
    "invalid option '--max-message-bytes=999999999': the supported maximum is 16777216 bytes" -- \
    --max-message-bytes=999999999 < "$TMP/in"
expect "stdio rejects unknown options" 2 "" "unknown option" -- --stdio --bogus

echo "cli: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
