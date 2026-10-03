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
# Executable discovery resolves symlinks (e.g. /var to /private/var on macOS).
TMP=$(cd "$TMP" && pwd -P)
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
expect "analyze bindings reports oversized enum set without trapping" 1 'enum-set-too-large/main\.v3:3:29: EnumSetTooLarge' "" -- \
    analyze --bindings "$A/enum-set-too-large/main.v3"
check_stream stdout "$TMP/out" 'enum-set-too-large/main\.v3:3:33: UnresolvedMember' && \
    check_stream stdout "$TMP/out" 'main\.v3:3:27-3:28 -> ENUM E @ .*main\.v3:1:6-1:7'
finish "analyze bindings retains diagnostics and resolved enum use" $(( $? == 0 ))
expect "analyze repeats with identical results" 0 '^ok: 2 files' "" -- \
    analyze --repeat=3 --bindings "$A/two-file/shapes.v3" "$A/two-file/main.v3"
expect "analyze stats go to stderr" 0 '^ok: 2 files' 'analysis 1: parse [0-9]+ us, verify [0-9]+ us' -- \
    analyze --stats "$A/two-file/shapes.v3" "$A/two-file/main.v3"
expect "analyze missing file" 1 "" "cannot read file" -- analyze "$TMP/missing.v3"
expect "analyze rejects a bad repeat count" 2 "" "invalid option" -- analyze --repeat=0 "$A/two-file/main.v3"
expect "analyze rejects unknown options" 2 "" "unknown option" -- analyze --bogus "$A/two-file/main.v3"
expect "analyze rejects a bad worker timeout" 2 "" "invalid option" -- analyze --worker-timeout-ms=0 "$A/two-file/main.v3"
expect "analyze rejects a bad worker analysis limit" 2 "" "invalid option" -- \
    analyze --worker-max-analyses=x "$A/two-file/main.v3"

# Project files (docs/configuration.md). Glob expansion is not implemented yet,
# so the fixture programs are analyzed from the file lists their project files
# describe.
P=$FIXTURES/projects
TWO=$P/two-programs
expect "the server program verifies alone" 0 '^ok: 2 files parsed and verified$' "" -- \
    analyze "$TWO/server/Program.v3" "$TWO/shared/Greeting.v3"
expect "the client program verifies alone" 0 '^ok: 2 files parsed and verified$' "" -- \
    analyze "$TWO/client/Program.v3" "$TWO/shared/Greeting.v3"
expect "the two programs are not one program" 1 'client/Program\.v3:3:11: TypeRedefined: type "Program" redefined' "" -- \
    analyze "$TWO/server/Program.v3" "$TWO/client/Program.v3" "$TWO/shared/Greeting.v3"
expect "the Virgil library fixture verifies with lib/util" 0 '^ok: [0-9]+ files parsed and verified$' "" -- \
    analyze "$P/virgil-lib/src/Words.v3" "$ROOT"/vendor/virgil/lib/util/*.v3
expect "the Virgil library fixture needs lib/util" 1 'Words\.v3:5:29: UnresolvedIdentifier: identifier "Vector" cannot be found' "" -- \
    analyze "$P/virgil-lib/src/Words.v3"

# Whole-program analysis runs in virgil-lsp-worker, next to the executable
# (docs/decisions/0004-analysis-worker-process.md). A verifier trap or hang
# ends only the worker; the command reports it and stdout stays clean.
WORKER=$(dirname "$EXE")/virgil-lsp-worker
expect "analyze contains a verifier crash" 1 "" \
    'analysis worker [0-9]+ crashed during an analysis; it ended with exit status 255' -- \
    analyze "$A/worker-crash/main.v3"
check_stream stderr "$TMP/err" '!NullCheckException' && \
    check_stream stderr "$TMP/err" 'error: analysis run 1 failed: the analysis worker crashed \(exit status 255\)$'
finish "analyze crash keeps the worker's trace and reports the failure" $(( $? == 0 ))
SECONDS=0
expect "analyze contains a verifier hang" 1 "" \
    'analysis worker [0-9]+ exceeded the analysis time limit of 1000 ms and was killed; it ended with signal 9' -- \
    analyze --worker-timeout-ms=1000 "$A/worker-hang/main.v3"
[ "$SECONDS" -lt 10 ] || { fail=$((fail + 1)); echo "FAIL: the hanging analysis was not stopped at its time limit"; }
check_stream stderr "$TMP/err" 'error: analysis run 1 failed: the analysis worker timed out \(signal 9\)$'
finish "analyze hang reports the failure" $(( $? == 0 ))
# A worker whose supervisor dies during a hanging analysis must still end: it
# arms a timer for twice the time limit plus a second (4 s here).
"$EXE" analyze --worker-timeout-ms=1500 "$A/worker-hang/main.v3" > /dev/null 2>&1 &
supervisor=$!
worker=
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    worker=$(pgrep -P "$supervisor")
    [ -z "$worker" ] || break
    sleep 0.1
done
sleep 0.7
kill -9 "$supervisor"
wait "$supervisor" 2>/dev/null
SECONDS=0
while [ -n "$worker" ] && kill -0 "$worker" 2>/dev/null && [ "$SECONDS" -lt 15 ]; do sleep 0.2; done
if [ -n "$worker" ] && ! kill -0 "$worker" 2>/dev/null; then
    pass=$((pass + 1))
else
    fail=$((fail + 1))
    echo "FAIL: an orphaned analysis worker kept running (process ${worker:-not found})"
    [ -z "$worker" ] || kill -9 "$worker" 2>/dev/null
fi

mkdir "$TMP/alone"
cp "$EXE" "$TMP/alone/virgil-lsp"
EXE_SAVED=$EXE
EXE=$TMP/alone/virgil-lsp
expect "analyze reports a missing worker" 1 "" \
    "cannot start the analysis worker: cannot execute $TMP/alone/virgil-lsp-worker" -- \
    analyze "$A/two-file/shapes.v3" "$A/two-file/main.v3"
EXE=$EXE_SAVED
EXE_SAVED=$EXE
EXE=$WORKER
expect "worker refuses to run without a server" 2 "" 'virgil-lsp-worker: error: virgil-lsp-worker is started by virgil-lsp' --
EXE=$EXE_SAVED

# Many analyses of the Aeneas sources in one command, across replaced workers:
# every run must report the same diagnostics and all 162,929 bindings.
VIRGIL=$ROOT/vendor/virgil
aeneas=("$VIRGIL"/aeneas/src/*/*.v3)
for dep in $(grep -v '^lib/test/' "$VIRGIL/aeneas/DEPS"); do
    aeneas+=("$VIRGIL"/$dep)
done
expect "analyze repeats the Aeneas sources across replaced workers" 0 \
    "^ok: ${#aeneas[@]} files parsed and verified$" \
    'replacing analysis worker [0-9]+ after 8 analyses \(limit 8\)' -- \
    analyze --bindings --stats --repeat=20 --worker-max-analyses=8 "${aeneas[@]}"
[ "$(grep -c 'replacing analysis worker' "$TMP/err")" -eq 2 ] && \
    [ "$(grep -Ec '^virgil-lsp: analysis [0-9]+: parse [0-9]+ us, verify [0-9]+ us, 162929 bindings' "$TMP/err")" -eq 20 ] && \
    [ "$(grep -c ' -> ' "$TMP/out")" -eq 162929 ]
finish "analyze repeats report every binding from each worker" $(( $? == 0 ))

# A complete report spans the header and both files. Pin its bytes so buffer
# reuse cannot leak capacity bytes, duplicate a prior chunk, or miss a chunk.
# The golden uses repository-relative fixture paths; CLI output uses absolute
# paths here, so add ROOT to every path in the expected report.
awk -v root="$ROOT/" '{ gsub(/test\/fixtures\//, root "test/fixtures/"); print }' \
    "$ROOT/test/cli/analysis-bindings.txt" > "$TMP/analysis-want"
for options in "--bindings" "--bindings --repeat=3" "--bindings --stats --repeat=3"; do
    # Word splitting is intentional: options is a fixed list, not user input.
    "$EXE" analyze $options "$A/two-file/shapes.v3" "$A/two-file/main.v3" > "$TMP/out" 2> "$TMP/err"
    got=$?
    ok=1
    [ "$got" -eq 0 ] || ok=0
    cmp -s "$TMP/analysis-want" "$TMP/out" || ok=0
    if [[ "$options" == *--stats* ]]; then
        [ "$(wc -l < "$TMP/err")" -eq 3 ] || ok=0
        for run in 1 2 3; do
            grep -Eq "^virgil-lsp: analysis $run: parse [0-9]+ us, verify [0-9]+ us, 13 bindings in [0-9]+ us, uids \\+[0-9]+ \\(next [0-9]+\\), global types \\+[0-9]+ \\(total [0-9]+\\)$" "$TMP/err" || ok=0
        done
    else
        [ ! -s "$TMP/err" ] || ok=0
    fi
    finish "analyze complete report $options" "$ok"
done

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
VERSION=$("$EXE" --version | head -n 1)
VERSION=${VERSION#* }
INITIALIZE=$(request 1 initialize '{"processId":null,"rootUri":null,"capabilities":{}}')
INIT_RESULT=$(printf '{"jsonrpc":"2.0","id":1,"result":{"capabilities":{"documentSymbolProvider":true,"textDocumentSync":{"change":1,"openClose":true,"save":{"includeText":false}}},"serverInfo":{"name":"virgil-lsp","version":"%s"}}}' "$VERSION")
EXIT='{"jsonrpc":"2.0","method":"exit"}'

# Golden transcripts in test/protocol/ cover the protocol over --stdio. The
# cases here need input generated at run time or held open.

# A million zeros: under the default 4 MiB message limit, but far more
# values than the heap can hold once parsed. The message is answered with a
# ParseError, and the next request is still read.
{
    printf '{"jsonrpc":"2.0","id":2,"method":"x","params":['
    yes '0,' | head -n 999999 | tr -d '\n'
    printf '0]}'
} > "$TMP/dense"
{
    frame "$INITIALIZE"
    printf 'Content-Length: %d\r\n\r\n' $(( $(wc -c < "$TMP/dense") ))
    cat "$TMP/dense"
    frame "$(request 3 small)"
    frame "$(request 4 shutdown)"
    frame "$EXIT"
} > "$TMP/in"
{
    frame "$INIT_RESULT"
    frame '{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"invalid JSON at byte 0: more than 50000 values"}}'
    frame "$(not_found 3 small)"
    frame '{"jsonrpc":"2.0","id":4,"result":null}'
} > "$TMP/want"
expect_stdio "stdio rejects a message with too many values and keeps going" 0 "$TMP/want" "" -- < "$TMP/in"

# A bare LF proves the header is malformed, so the server must stop while the
# client still holds the pipe open. If it waited for more input instead, it
# would report the input ending inside a message once the pipe closed.
held_open() {
    frame "$INITIALIZE"
    printf 'Content-Length: 2\n\n{}'
    sleep 3
}
frame "$INIT_RESULT" > "$TMP/want"
expect_stdio "stdio stops at a bare LF without waiting for more input" 1 "$TMP/want" \
    'error: malformed message header: header has an LF without a CR before it' -- < <(held_open)

# exit ends the process even while the client holds the pipe open.
exit_held_open() {
    frame "$INITIALIZE"
    frame "$(request 2 shutdown)"
    frame "$EXIT"
    sleep 3
}
{ frame "$INIT_RESULT"; frame '{"jsonrpc":"2.0","id":2,"result":null}'; } > "$TMP/want"
SECONDS=0
expect_stdio "stdio exits at exit without waiting for more input" 0 "$TMP/want" "" -- < <(exit_held_open)
[ "$SECONDS" -lt 3 ] || { fail=$((fail + 1)); echo "FAIL: stdio waited for the end of input after exit"; }

: > "$TMP/empty"
expect "stdio rejects a bad message limit" 2 "" "invalid option" -- --stdio --max-message-bytes=0
expect "stdio rejects a message limit over nine digits" 2 "" "invalid option" -- --stdio --max-message-bytes=1234567890
expect "stdio rejects a message limit over the supported maximum" 2 "" \
    "invalid option '--max-message-bytes=4194305': the supported maximum is 4194304 bytes" -- \
    --stdio --max-message-bytes=4194305 < /dev/null
# The reproduction from #26: this limit used to be accepted, and the header
# alone then ran out of heap.
printf 'Content-Length: 999999999\r\n\r\n' > "$TMP/in"
expect_stdio "stdio rejects a limit too large for the heap" 2 "$TMP/empty" \
    "invalid option '--max-message-bytes=999999999': the supported maximum is 4194304 bytes" -- \
    --max-message-bytes=999999999 < "$TMP/in"
expect "stdio rejects unknown options" 2 "" "unknown option" -- --stdio --bogus

bash "$ROOT/test/cli/bench-analysis.sh" || { fail=$((fail + 1)); echo "FAIL: benchmark driver regression tests"; }

echo "cli: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
