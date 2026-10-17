#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for the scripts that the release workflow runs: scripts/check-release.sh
# on every tag, scripts/check-vsix.py on the packaged VS Code extension, and
# scripts/release-notes.py on the release assets. The check-release.sh cases
# write VERSION and CHANGELOG.md to a temporary repository root; the others
# construct VSIX files, build manifests, and archives in a temporary directory.
# Usage: test/release/run.sh
# Backticks in single-quoted patterns are Markdown, not command substitutions.
# shellcheck disable=SC2016
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CHECK=$ROOT/scripts/check-release.sh
CHECK_VSIX=$ROOT/scripts/check-vsix.py
NOTES=$ROOT/scripts/release-notes.py
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
    report "$name" $? "$code" "$want_out" - "$err_pat"
}

# expect_run <name> <expected-exit> <stdout-pattern|-> <stderr-pattern|-> <command...>
# Like expect, but runs the command, and standard output must match
# <stdout-pattern> rather than equal a string.
expect_run() {
    local name=$1 code=$2 out_pat=$3 err_pat=$4
    shift 4
    "$@" > "$TMP/out" 2> "$TMP/err"
    report "$name" $? "$code" - "$out_pat" "$err_pat"
}

# report <name> <exit> <expected-exit> <stdout|-> <stdout-pattern|-> <stderr-pattern|-|"">
report() {
    local name=$1 got=$2 code=$3 want_out=$4 out_pat=$5 err_pat=$6 ok=1
    [ "$got" -eq "$code" ] || { ok=0; echo "  exit: expected $code, got $got"; }
    if [ "$want_out" != "-" ] && [ "$(cat "$TMP/out")" != "$want_out" ]; then
        ok=0; echo "  stdout: expected '$want_out'"
    fi
    if [ "$out_pat" != "-" ] && ! grep -Eq -- "$out_pat" "$TMP/out"; then
        ok=0; echo "  stdout: no match for /$out_pat/"
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

# vsix <path> <package.json|-> [<file>...]: writes a zip file like a VSIX, with
# <package.json> as extension/package.json, or none for "-", and an empty
# extension/<file> for each file.
vsix() {
    python3 - "$@" <<'PY'
import sys, zipfile
path, package, files = sys.argv[1], sys.argv[2], sys.argv[3:]
with zipfile.ZipFile(path, "w") as z:
    z.writestr("extension.vsixmanifest", "<PackageManifest/>")
    if package != "-":
        z.writestr("extension/package.json", package)
    for f in files:
        z.writestr("extension/" + f, "")
PY
}

# An extension directory's package.json and lockfile, and the files of a good
# VSIX packaged from it. Its version differs from clients/vscode's.
EXT=$TMP/extension
mkdir "$EXT"
package='{"name": "virgil-lsp-vscode", "publisher": "virgil-language-server", "version": "7.8.9",
  "preview": true, "main": "./out/extension.js", "dependencies": {"vscode-languageclient": "1.0.0"}}'
echo "$package" > "$EXT/package.json"
cat > "$EXT/package-lock.json" <<'JSON'
{"packages": {
  "": {},
  "node_modules/vscode-languageclient": {},
  "node_modules/vscode-jsonrpc": {},
  "node_modules/minimatch": {},
  "node_modules/minimatch/node_modules/brace-expansion": {},
  "node_modules/@vscode/vsce": {"dev": true},
  "node_modules/@vscode/vsce-sign": {"dev": true, "optional": true}
}}
JSON
files=(LICENSE.txt out/extension.js syntaxes/virgil.tmLanguage.json
    node_modules/vscode-languageclient/package.json node_modules/vscode-languageclient/lib/main.js
    node_modules/vscode-jsonrpc/package.json node_modules/minimatch/package.json
    node_modules/minimatch/node_modules/brace-expansion/index.js)
VSIX=$TMP/virgil-lsp-vscode-7.8.9.vsix

# check_vsix <name> <expected-exit> <stderr-pattern> <package.json|-> [<extra-file>...]
check_vsix() {
    local name=$1 code=$2 err_pat=$3 package=$4
    shift 4
    vsix "$VSIX" "$package" "${files[@]}" "$@"
    expect_run "check-vsix.py: $name" "$code" - "$err_pat" "$CHECK_VSIX" "$VSIX" "$EXT"
}

vsix "$VSIX" "$package" "${files[@]}"
expect_run "check-vsix.py: matching VSIX" 0 \
    '^virgil-lsp-vscode-7\.8\.9\.vsix: virgil-language-server\.virgil-lsp-vscode 7\.8\.9, 10 files$' "" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
check_vsix "another version" 1 "version is '7\.8\.10', but .*/package\.json has '7\.8\.9'" \
    "${package/7.8.9/7.8.10}"
check_vsix "another name" 1 "name is 'virgil-lsp', but" "${package/virgil-lsp-vscode/virgil-lsp}"
check_vsix "another publisher" 1 "publisher is 'someone', but" "${package/virgil-language-server/someone}"
check_vsix "no extension/package.json" 1 "has no extension/package\.json" -
check_vsix "malformed extension/package.json" 1 "extension/package\.json is not valid JSON" '{"name":'
check_vsix "JSON array as extension/package.json" 1 "extension/package\.json is not a JSON object" '[]'
check_vsix "sources" 1 "development file src/extension\.ts" "$package" src/extension.ts
check_vsix "tests" 1 "development file test/lifecycle\.cjs" "$package" test/lifecycle.cjs
check_vsix "the test's VS Code download" 1 "development file \.vscode-test/code" "$package" .vscode-test/code
check_vsix "TypeScript configuration" 1 "development file tsconfig\.json" "$package" tsconfig.json
check_vsix "source map" 1 "development file out/extension\.js\.map" "$package" out/extension.js.map
check_vsix "type declarations in a dependency" 1 \
    "development file node_modules/vscode-jsonrpc/lib/main\.d\.ts" "$package" node_modules/vscode-jsonrpc/lib/main.d.ts
check_vsix "a dependency's own test directory" 0 "" "$package" node_modules/vscode-jsonrpc/test/x.js
check_vsix "a development dependency" 1 \
    "holds node_modules/@vscode/vsce-sign, which isn't a runtime dependency in package-lock\.json" \
    "$package" node_modules/@vscode/vsce-sign/package.json
check_vsix "a package that the lockfile doesn't list" 1 "holds node_modules/left-pad, which isn't" \
    "$package" node_modules/left-pad/index.js
vsix "$VSIX" "$package" LICENSE.txt node_modules/vscode-languageclient/package.json
expect_run "check-vsix.py: no main file" 1 - "the main file out/extension\.js is missing" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$VSIX" "$package" out/extension.js
expect_run "check-vsix.py: no runtime dependency" 1 - \
    "the runtime dependency vscode-languageclient is missing" "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$TMP/virgil-lsp-vscode.vsix" "$package" "${files[@]}"
expect_run "check-vsix.py: named without its version" 1 - \
    "the file is virgil-lsp-vscode\.vsix, not virgil-lsp-vscode-7\.8\.9\.vsix" \
    "$CHECK_VSIX" "$TMP/virgil-lsp-vscode.vsix" "$EXT"
echo "not a zip file" > "$VSIX"
expect_run "check-vsix.py: not a zip file" 1 - "virgil-lsp-vscode-7\.8\.9\.vsix is not a zip file" \
    "$CHECK_VSIX" "$VSIX" "$EXT"

# Release assets as the build and vsix jobs produce them: a manifest and an
# empty archive for each target, and the VSIX.
ASSETS=$TMP/assets
revision=0123456789abcdef0123456789abcdef01234567
virgil=fedcba9876543210fedcba9876543210fedcba98
assets() {
    local target
    rm -rf "$ASSETS"
    mkdir "$ASSETS"
    for target in x86-64-linux x86-64-darwin; do
        printf '{"version": "1.2.3-alpha.4", "tag": null, "target": "%s", "sourceRevision": "%s",
            "dirty": false, "virgilRevision": "%s"}\n' "$target" "$revision" "$virgil" \
            > "$ASSETS/virgil-lsp-1.2.3-alpha.4-$target.manifest.json"
        : > "$ASSETS/virgil-lsp-1.2.3-alpha.4-$target.tar.gz"
    done
    vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "$package" "${files[@]}"
}
# notes <name> <expected-exit> <stdout-pattern|-> <stderr-pattern|-|"">
notes() {
    expect_run "release-notes.py: $1" "$2" "$3" "$4" "$NOTES" Unreleased "$ASSETS"
}

assets
notes "lists the archives" 0 '^[|] `virgil-lsp-1\.2\.3-alpha\.4-x86-64-darwin\.tar\.gz` [|] macOS' ""
notes "lists the VSIX with the version that it records" 0 \
    '^`virgil-lsp-vscode-7\.8\.9\.vsix` is version 7\.8\.9 of the VS Code extension \(`virgil-language-server\.virgil-lsp-vscode`\), a preview\. Its versions' ""
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" \
    '{"name": "virgil-lsp-vscode", "publisher": "virgil-language-server", "version": "7.8.9"}'
notes "lists a VSIX that isn't a preview without saying it is" 0 \
    '\(`virgil-language-server\.virgil-lsp-vscode`\)\. Its versions' ""
assets
notes "links the revisions" 0 "^- Server: \[\`$revision\`\]" ""
rm "$ASSETS/virgil-lsp-vscode-7.8.9.vsix"
notes "no VSIX" 1 - "expected one VS Code extension \(\*\.vsix\) in .*, found 0$"
assets
vsix "$ASSETS/virgil-lsp-vscode-7.8.10.vsix" "${package/7.8.9/7.8.10}" "${files[@]}"
notes "two VSIX files" 1 - "expected one VS Code extension .*, found 2$"
assets
echo "not a zip file" > "$ASSETS/virgil-lsp-vscode-7.8.9.vsix"
notes "VSIX that isn't a zip file" 1 - "virgil-lsp-vscode-7\.8\.9\.vsix is not a zip file"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" -
notes "VSIX without extension/package.json" 1 - "has no extension/package\.json"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" '{"name": "virgil-lsp-vscode", "version":'
notes "VSIX with malformed extension/package.json" 1 - "extension/package\.json is not valid JSON"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" '{"name": "virgil-lsp-vscode", "publisher": "virgil-language-server"}'
notes "VSIX without a version" 1 - "lacks a name, publisher, or version"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "${package/7.8.9/7.9.0}"
notes "VSIX named for another version" 1 - \
    "virgil-lsp-vscode-7\.8\.9\.vsix holds version 7\.9\.0 of virgil-lsp-vscode"
assets
rm "$ASSETS/virgil-lsp-1.2.3-alpha.4-x86-64-linux.tar.gz"
notes "missing archive" 1 - "missing archive virgil-lsp-1\.2\.3-alpha\.4-x86-64-linux\.tar\.gz"

echo "release scripts: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
