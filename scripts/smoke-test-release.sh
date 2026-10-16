#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Smoke-test a release archive from scripts/package-release.sh. The archive is
# extracted in a new temporary directory, outside the checkout, and run from
# there with an empty environment: its files and their checksums must match
# the manifest, --version must report the manifest's version and Virgil
# revision, and the analyze command, which needs the worker, must verify one
# program and report the errors in another, both directly and through a
# symlink as docs/releasing.md installs it.
# Usage: smoke-test-release.sh <archive>
set -uo pipefail

ARCHIVE=${1:?usage: smoke-test-release.sh <archive>}
ARCHIVE=$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")
name=$(basename "$ARCHIVE" .tar.gz)
TMP=$(mktemp -d)
# Executable discovery resolves symlinks (e.g. /var to /private/var on macOS).
TMP=$(cd "$TMP" && pwd -P)
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

check() {
    local what=$1
    shift
    if "$@" > "$TMP/out" 2>&1; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        echo "FAIL: $what"
        sed 's/^/  | /' "$TMP/out"
    fi
}

sha256() {
    if command -v sha256sum > /dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d ' ' -f 1
}

# manifest <key> [<subkey>]: prints a manifest value, with null as "null".
manifest() {
    python3 -c 'import json, sys
v = json.load(open(sys.argv[1]))
for k in sys.argv[2:]: v = v[k]
print("null" if v is None else json.dumps(v) if isinstance(v, bool) else v)' "$DIR/manifest.json" "$@"
}

# Runs the extracted server in the work directory with an empty environment.
# PATH holds only the symlinked bin directory and the system's.
run() {
    (cd "$TMP/work" && env -i PATH="$TMP/bin:/usr/bin:/bin" "$@")
}

expected_members() {
    for f in LICENSE README.md THIRD_PARTY_NOTICES.md manifest.json virgil-lsp virgil-lsp-worker; do
        echo "$name/$f"
    done
}
members() {
    local listed
    listed=$(tar -tzf "$ARCHIVE") || return 1
    diff <(expected_members) <(echo "$listed" | sed 's:/$::' | grep -vxF "$name" | LC_ALL=C sort)
}
check "archive holds exactly the expected files under $name/" members

mkdir "$TMP/extract" "$TMP/work" "$TMP/bin"
if ! tar -xzf "$ARCHIVE" -C "$TMP/extract"; then
    echo "FAIL: cannot extract $ARCHIVE"
    exit 1
fi
DIR=$TMP/extract/$name
ln -s "$DIR/virgil-lsp" "$TMP/bin/virgil-lsp"

executables() {
    [ -f "$DIR/virgil-lsp" ] && [ -x "$DIR/virgil-lsp" ] &&
        [ -f "$DIR/virgil-lsp-worker" ] && [ -x "$DIR/virgil-lsp-worker" ]
}
check "virgil-lsp and virgil-lsp-worker are executable" executables

version=$(manifest version)
target=$(manifest target)
virgil_revision=$(manifest virgilRevision)
dirty=$(manifest dirty)

named() {
    [ "$name" = "virgil-lsp-$version-$target" ] ||
        { echo "archive $name, manifest version $version and target $target"; return 1; }
}
check "archive name matches the manifest" named

checksums() {
    local f want got
    for f in virgil-lsp virgil-lsp-worker; do
        want=$(manifest sha256 "$f")
        got=$(sha256 "$DIR/$f")
        [ "$want" = "$got" ] || { echo "$f: manifest $want, file $got"; return 1; }
    done
}
check "executables match the manifest's checksums" checksums

version_output() {
    local out
    out=$(run "$DIR/virgil-lsp" --version) || { echo "$out"; return 1; }
    echo "$out"
    [ "$(echo "$out" | sed -n 1p)" = "virgil-lsp $version" ] &&
        echo "$out" | grep -qx "  virgil revision: $virgil_revision" &&
        { [ "$dirty" = true ] || ! echo "$out" | grep -q '^  source revision: .*-dirty$'; }
}
check "--version reports the manifest's version and Virgil revision" version_output

cat > "$TMP/work/main.v3" <<'V3'
component Main {
	def main() -> int {
		return Shapes.square(3).area();
	}
}
V3
cat > "$TMP/work/shapes.v3" <<'V3'
class Rect(width: int, height: int) {
	def area() -> int { return width * height; }
}
component Shapes {
	def square(side: int) -> Rect { return Rect.new(side, side); }
}
V3
cat > "$TMP/work/error.v3" <<'V3'
component Main {
	def main() -> int { return Missing.value; }
}
V3

# analyze <server> <expected-exit> <stdout-pattern> <files...>
analyze() {
    local exe=$1 code=$2 pattern=$3 got
    shift 3
    run "$exe" analyze "$@" > "$TMP/analyze" 2>&1
    got=$?
    cat "$TMP/analyze"
    [ "$got" -eq "$code" ] || { echo "exit: expected $code, got $got"; return 1; }
    grep -Eq -- "$pattern" "$TMP/analyze" || { echo "no match for /$pattern/"; return 1; }
}
check "analyze verifies a two-file program" \
    analyze "$DIR/virgil-lsp" 0 '^ok: 2 files parsed and verified$' main.v3 shapes.v3
check "analyze reports a semantic error" \
    analyze "$DIR/virgil-lsp" 1 '^error\.v3:2:[0-9]+: UnresolvedIdentifier' error.v3
check "analyze works through a symlink on PATH" \
    analyze virgil-lsp 0 '^ok: 2 files parsed and verified$' main.v3 shapes.v3

echo "$name: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
