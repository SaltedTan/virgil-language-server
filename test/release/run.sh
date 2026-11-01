#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for the scripts that the release workflow runs: scripts/check-release.sh
# on every tag, scripts/check-vsix.py on the packaged VS Code extension, and
# scripts/release-notes.py on the release assets. The check-release.sh cases
# write VERSION and CHANGELOG.md to a temporary repository root; the others
# construct VSIX files, build manifests, and archives in a temporary directory.
# Usage: test/release/run.sh [--archive <archive>]
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
    # The Python scripts report every error themselves.
    if grep -q '^Traceback' "$TMP/err"; then
        ok=0; echo "  stderr: a Python traceback"
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

# vsix <path> <package.json|@<file>|-> [<file>...]: writes a zip file like a
# VSIX, with <package.json>, or the bytes of <file> for @<file>, as
# extension/package.json, or none for "-", and an empty extension/<file> for
# each file, or an entry named exactly <entry> for each raw:<entry>, such as a
# directory entry ending in "/", or an entry named <entry> that holds <text>
# for each text:<entry>=<text>. Its entries are stored, not compressed. A
# name may repeat, without zipfile's warning.
vsix() {
    python3 -W "ignore:Duplicate name" - "$@" <<'PY'
import sys, zipfile
path, package, files = sys.argv[1], sys.argv[2], sys.argv[3:]
with zipfile.ZipFile(path, "w") as z:
    z.writestr("extension.vsixmanifest", '<PackageManifest><Metadata><Identity/><Properties><Property Id="Microsoft.VisualStudio.Code.PreRelease" Value="true"/></Properties></Metadata></PackageManifest>')
    if package.startswith("@"):
        with open(package[1:], "rb") as f:
            z.writestr("extension/package.json", f.read())
    elif package != "-":
        z.writestr("extension/package.json", package)
    for f in files:
        if f.startswith("text:"):
            entry, _, text = f[5:].partition("=")
            z.writestr(entry, text)
        else:
            z.writestr(f[4:] if f.startswith("raw:") else "extension/" + f, "")
PY
}

# damage <vsix> <entry> data|name|nul|zip64: damages a VSIX that is_zipfile
# still accepts. data inverts the first byte of the entry's data, so that the
# entry fails its CRC check; name marks the entry's name in the central
# directory as UTF-8 and makes its first byte one that UTF-8 can't start with;
# nul replaces the first "_" in the entry's name with a NUL, in both of its
# headers; and zip64 moves the entry's offset in the central directory into a
# ZIP64 extra field, as 2**64 - 1.
damage() {
    python3 - "$@" <<'PY'
import struct, sys, zipfile
path, entry, how = sys.argv[1:]
with zipfile.ZipFile(path) as z:
    offset = z.getinfo(entry).header_offset
with open(path, "rb") as f:
    data = bytearray(f.read())
# The central directory follows every entry's data, so the name's last
# occurrence is in the entry's central directory header, 46 bytes after the
# header starts.
name = data.rindex(entry.encode())
header = name - 46
if how == "data":
    name_length, extra_length = struct.unpack_from("<HH", data, offset + 26)
    data[offset + 30 + name_length + extra_length] ^= 0xff
elif how == "name":
    flags, = struct.unpack_from("<H", data, header + 8)
    struct.pack_into("<H", data, header + 8, flags | 0x800)
    data[name] = 0xff
elif how == "nul":
    data = data.replace(entry.encode(), entry.replace("_", "\0", 1).encode())
elif how == "zip64":
    name_length, extra_length = struct.unpack_from("<HH", data, header + 28)
    struct.pack_into("<H", data, header + 30, extra_length + 12)
    struct.pack_into("<I", data, header + 42, 0xffffffff)
    data[name + name_length:name + name_length] = struct.pack("<HHQ", 1, 8, 2**64 - 1)
    # The end of central directory record holds the central directory's size.
    end = data.rindex(b"PK\x05\x06")
    size, = struct.unpack_from("<I", data, end + 12)
    struct.pack_into("<I", data, end + 12, size + 12)
with open(path, "wb") as f:
    f.write(data)
PY
}
# A JSON array nested more deeply than Python's json module can parse, and a
# package.json that isn't UTF-8.
python3 -c 'print("[" * 1000000)' > "$TMP/deep.json"
printf '{"name": "\377"}' > "$TMP/not-utf-8.json"

# An extension directory with a package.json, a lockfile, and clients/vscode's
# .vscodeignore, and the files of a good VSIX packaged from it. Its version
# differs from clients/vscode's. The lockfile's optional package for macOS is
# absent from the VSIX, as on Linux.
EXT=$TMP/extension
mkdir "$EXT"
cp "$ROOT/clients/vscode/.vscodeignore" "$EXT/.vscodeignore"
package='{"name": "virgil-lsp-vscode", "publisher": "virgil-language-server", "version": "7.8.9",
  "preview": true, "main": "./out/extension.js", "dependencies": {"vscode-languageclient": "1.0.0"}}'
echo "$package" > "$EXT/package.json"
cat > "$EXT/package-lock.json" <<'JSON'
{"lockfileVersion": 3, "packages": {
  "": {},
  "node_modules/vscode-languageclient": {},
  "node_modules/vscode-jsonrpc": {},
  "node_modules/minimatch": {},
  "node_modules/minimatch/node_modules/brace-expansion": {},
  "node_modules/fsevents": {"optional": true, "os": ["darwin"]},
  "node_modules/@vscode/vsce": {"dev": true},
  "node_modules/@vscode/vsce-sign": {"dev": true, "optional": true}
}}
JSON
# Without the transitive dependency brace-expansion.
direct=(LICENSE.txt out/extension.js syntaxes/virgil.tmLanguage.json
    node_modules/vscode-languageclient/package.json node_modules/vscode-languageclient/lib/main.js
    node_modules/vscode-jsonrpc/package.json node_modules/minimatch/package.json)
files=("${direct[@]}" node_modules/minimatch/node_modules/brace-expansion/package.json
    node_modules/minimatch/node_modules/brace-expansion/index.js)
VSIX=$TMP/virgil-lsp-vscode-7.8.9.vsix

# check_vsix <name> <expected-exit> <stderr-pattern> <package.json|-> [<extra-file>...]
# Checks a VSIX of the good files and the extra ones against $EXT.
check_vsix() {
    local name=$1 code=$2 err_pat=$3 package=$4
    shift 4
    vsix "$VSIX" "$package" "${files[@]}" "$@"
    expect_run "check-vsix.py: $name" "$code" - "$err_pat" "$CHECK_VSIX" "$VSIX" "$EXT"
}
# excluded <name> <file> <pattern>: a VSIX with the file fails, citing the
# ignore pattern that excludes it.
excluded() {
    check_vsix "$1" 1 "it holds $(ere "$2"), which the ignore pattern '$(ere "$3")' excludes$" "$package" "$2"
}
# ere <text>: escapes text for grep -E.
ere() {
    printf '%s' "$1" | sed 's#[][\.*^$+?(){}|]#\\&#g'
}

vsix "$VSIX" "$package" "${files[@]}"
expect_run "check-vsix.py: matching VSIX, without an optional package" 0 \
    '^virgil-lsp-vscode-7\.8\.9\.vsix: virgil-language-server\.virgil-lsp-vscode 7\.8\.9, 11 files$' "" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
check_vsix "an optional package" 0 "" "$package" node_modules/fsevents/package.json
check_vsix "another version" 1 "version is '7\.8\.10', but .*/package\.json has '7\.8\.9'" \
    "${package/7.8.9/7.8.10}"
check_vsix "another name" 1 "name is 'virgil-lsp', but" "${package/virgil-lsp-vscode/virgil-lsp}"
check_vsix "another publisher" 1 "publisher is 'someone', but" "${package/virgil-language-server/someone}"
check_vsix "no extension/package.json" 1 "has no extension/package\.json" -
check_vsix "malformed extension/package.json" 1 "extension/package\.json is not valid JSON" '{"name":'
check_vsix "a non-JSON constant in extension/package.json" 1 \
    "extension/package\.json is not valid JSON: it holds the non-JSON constant NaN$" \
    "${package/true/NaN}"
check_vsix "JSON array as extension/package.json" 1 "extension/package\.json is not a JSON object" '[]'
check_vsix "extension/package.json that isn't UTF-8" 1 "extension/package\.json is not UTF-8: " "@$TMP/not-utf-8.json"
check_vsix "deeply nested extension/package.json" 1 "extension/package\.json is nested too deeply to parse$" \
    "@$TMP/deep.json"
excluded "sources" src/extension.ts 'src/**'
excluded "tests" test/lifecycle.cjs 'test/**'
excluded "the test's VS Code download" .vscode-test/code '**/.vscode-test/**'
excluded "TypeScript configuration" tsconfig.json tsconfig.json
check_vsix "a file that an ignore pattern's name only begins" 0 "" "$package" tsconfig.json.backup
excluded "source map" out/extension.js.map '**/*.map'
excluded "source map at the top, where ** matches no directories" extension.js.map '**/*.map'
excluded "source map deep in a nested package, where ** matches several directories" \
    node_modules/minimatch/node_modules/brace-expansion/dist/esm/index.js.map '**/*.map'
excluded "type declarations in a dependency" node_modules/vscode-jsonrpc/lib/main.d.ts '**/*.d.ts'
excluded "ESLint configuration in a dependency" node_modules/vscode-languageclient/eslint.config.js \
    'node_modules/**/eslint.config.js'
excluded "TypeScript build information in a dependency" node_modules/vscode-jsonrpc/tsconfig.tsbuildinfo \
    'node_modules/**/tsconfig.tsbuildinfo'
excluded "a file that vsce leaves out by default" package-lock.json package-lock.json
check_vsix "a dependency's own test directory" 0 "" "$package" node_modules/vscode-jsonrpc/test/x.js
check_vsix "a development dependency" 1 \
    "holds node_modules/@vscode/vsce-sign, which package-lock\.json doesn't list as a production package$" \
    "$package" node_modules/@vscode/vsce-sign/package.json
check_vsix "a package that the lockfile doesn't list" 1 "holds node_modules/left-pad, which package-lock" \
    "$package" node_modules/left-pad/index.js
check_vsix "an unlisted package nested under a dependency's directory" 1 \
    "holds node_modules/vscode-languageclient/lib/node_modules/left-pad, which package-lock" \
    "$package" node_modules/vscode-languageclient/lib/node_modules/left-pad/package.json
check_vsix "an unlisted package outside node_modules at the top" 1 "holds lib/node_modules/left-pad, which" \
    "$package" lib/node_modules/left-pad/package.json
check_vsix "a file in node_modules outside a package" 1 "holds node_modules/\.package-lock\.json, which isn't in a package" \
    "$package" node_modules/.package-lock.json
check_vsix "a regular file named node_modules" 0 "" "$package" node_modules/vscode-languageclient/lib/node_modules
check_vsix "directory entries" 0 "" "$package" raw:extension/ raw:extension/node_modules/ raw:extension/out/
check_vsix "the files that vsce writes outside extension/" 0 "" "$package" "raw:[Content_Types].xml"
check_vsix "an entry with a . segment" 1 "the entry 'extension/\./src/secret\.ts' isn't a canonical relative path$" \
    "$package" raw:extension/./src/secret.ts
check_vsix "an entry with a .. segment" 1 "the entry 'extension/out/\.\./src/secret\.ts' isn't a canonical" \
    "$package" raw:extension/out/../src/secret.ts
check_vsix "an entry with an empty segment" 1 "the entry 'extension//src/secret\.ts' isn't a canonical" \
    "$package" raw:extension//src/secret.ts
check_vsix "an entry with a backslash" 1 "the entry 'extension/src.*secret\.ts' isn't a canonical" \
    "$package" 'raw:extension/src\secret.ts'
check_vsix "an entry with an LF" 1 "the entry 'extension/out/a\\\\nb\.js' isn't a canonical relative path$" \
    "$package" $'out/a\nb.js'
# U+2028, which vsce's file walk skips like LF.
check_vsix "an entry with a line separator" 1 \
    "the entry 'extension/out/a\\\\u2028b\.js' isn't a canonical relative path$" "$package" $'out/a\xe2\x80\xa8b.js'
check_vsix "an absolute entry" 1 "the entry '/extension/src/secret\.ts' isn't a canonical" \
    "$package" raw:/extension/src/secret.ts
check_vsix "an entry outside extension/" 1 "the entry 'src/secret\.ts' is outside extension/$" \
    "$package" raw:src/secret.ts
check_vsix "a directory entry outside extension/" 1 "the entry 'extension\.vsixmanifest/' is outside extension/$" \
    "$package" raw:extension.vsixmanifest/
vsix "$VSIX" "$package" "${direct[@]}"
expect_run "check-vsix.py: no transitive dependency" 1 - \
    "the production package node_modules/minimatch/node_modules/brace-expansion is missing$" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$VSIX" "$package" LICENSE.txt out/extension.js
expect_run "check-vsix.py: no direct dependency" 1 - \
    "the production package node_modules/vscode-languageclient is missing$" "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$VSIX" "$package" LICENSE.txt node_modules/vscode-languageclient/package.json
expect_run "check-vsix.py: no main file" 1 - "the main file out/extension\.js is missing" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$TMP/virgil-lsp-vscode.vsix" "$package" "${files[@]}"
expect_run "check-vsix.py: named without its version" 1 - \
    "the file is virgil-lsp-vscode\.vsix, not virgil-lsp-vscode-7\.8\.9\.vsix" \
    "$CHECK_VSIX" "$TMP/virgil-lsp-vscode.vsix" "$EXT"
echo "not a zip file" > "$VSIX"
expect_run "check-vsix.py: not a zip file" 1 - "virgil-lsp-vscode-7\.8\.9\.vsix is not a zip file" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$VSIX" "$package" "${files[@]}"
damage "$VSIX" extension/package.json data
expect_run "check-vsix.py: corrupt extension/package.json data" 1 - \
    "virgil-lsp-vscode-7\.8\.9\.vsix: cannot read extension/package\.json: Bad CRC-32" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
# A payload member other than the manifest: flipping a byte in its data fails
# its CRC check, even though the central directory stays valid.
vsix "$VSIX" "$package" "${files[@]}" 'text:extension/out/bundle.js=console.log(1);'
damage "$VSIX" extension/out/bundle.js data
expect_run "check-vsix.py: corrupt data in a member other than the manifest" 1 - \
    "virgil-lsp-vscode-7\.8\.9\.vsix: cannot read extension/out/bundle\.js: Bad CRC-32" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$VSIX" "$package" "${files[@]}"
damage "$VSIX" extension/out/extension.js name
expect_run "check-vsix.py: a central directory name that isn't UTF-8" 1 - \
    "cannot read .*/virgil-lsp-vscode-7\.8\.9\.vsix: 'utf-8' codec can't decode" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
vsix "$VSIX" "$package" "${files[@]}"
damage "$VSIX" extension/package.json zip64
expect_run "check-vsix.py: a ZIP64 offset of 2**64 - 1" 1 - \
    "virgil-lsp-vscode-7\.8\.9\.vsix: cannot read extension/package\.json: ." \
    "$CHECK_VSIX" "$VSIX" "$EXT"
# zipfile names this entry extension/good.txt, without the NUL and the rest.
vsix "$VSIX" "$package" "${files[@]}" good.txt_/bad
damage "$VSIX" extension/good.txt_/bad nul
expect_run "check-vsix.py: an entry with a NUL" 1 - \
    "the entry 'extension/good\.txt\\\\x00/bad' isn't a canonical relative path$" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
# zipfile would read this empty entry as extension/package.json.
vsix "$VSIX" "$package" "${files[@]}" package.json_x
damage "$VSIX" extension/package.json_x nul
expect_run "check-vsix.py: a second extension/package.json, after a NUL" 1 - \
    "the entry 'extension/package\.json\\\\x00x' isn't a canonical relative path$" \
    "$CHECK_VSIX" "$VSIX" "$EXT"
check_vsix "two extension/package.json entries" 1 \
    "holds more than one entry named 'extension/package\.json'$" "$package" raw:extension/package.json
check_vsix "two entries with the same name" 1 \
    "holds more than one entry named 'extension/out/extension\.js'$" "$package" out/extension.js
# The extension directory's own package.json is read with the same parser, so
# an Infinity there is reported too, rather than accepted.
rm -rf "$TMP/non-json"
cp -R "$EXT" "$TMP/non-json"
printf '%s\n' "${package/true/Infinity}" > "$TMP/non-json/package.json"
vsix "$VSIX" "$package" "${files[@]}"
expect_run "check-vsix.py: a non-JSON constant in the extension directory's package.json" 1 - \
    "package\.json is not valid JSON: it holds the non-JSON constant Infinity$" \
    "$CHECK_VSIX" "$VSIX" "$TMP/non-json"

# Rules that clients/vscode's .vscodeignore doesn't use: a negation, which vsce
# applies after every ignore pattern, and a directory named without "/**".
cp -R "$EXT" "$TMP/rules"
printf '!src/keep.txt\n# a comment\n\ndocs\n' >> "$TMP/rules/.vscodeignore"
for f in src/keep.txt src/extension.ts docs/notes.md; do
    vsix "$VSIX" "$package" "${files[@]}" "$f"
    "$CHECK_VSIX" "$VSIX" "$TMP/rules" > "$TMP/out" 2> "$TMP/err"
    echo "$f $?"
done > "$TMP/rules.out"
printf 'src/keep.txt 0\nsrc/extension.ts 1\ndocs/notes.md 1\n' > "$TMP/rules.want"
expect_run "check-vsix.py: a negation keeps a file, and a directory pattern excludes what it holds" 0 - "" \
    diff "$TMP/rules.want" "$TMP/rules.out"

# An escaped character is a literal: an escaped bracket doesn't start a class,
# an escaped brace, parenthesis, "*", or "?" matches only itself, and an
# escaped leading "!" isn't a negation.
cp -R "$EXT" "$TMP/escaped"
escaped=('assets/\[draft\].txt' 'notes/\{\*\?\@\(x\)\}.md' '\!keep.txt')
literal=('assets/[draft].txt' 'notes/{*?@(x)}.md' '!keep.txt')
printf '%s\n' "${escaped[@]}" >> "$TMP/escaped/.vscodeignore"
for i in "${!escaped[@]}"; do
    vsix "$VSIX" "$package" "${files[@]}" "${literal[i]}"
    expect_run "check-vsix.py: the escaped pattern ${escaped[i]} excludes ${literal[i]}" 1 - \
        "it holds $(ere "${literal[i]}"), which the ignore pattern '$(ere "${escaped[i]}")' excludes$" \
        "$CHECK_VSIX" "$VSIX" "$TMP/escaped"
done
for f in assets/d.txt assets/draft.txt 'notes/{ab@(x)}.md' keep.txt; do
    vsix "$VSIX" "$package" "${files[@]}" "$f"
    expect_run "check-vsix.py: the escaped patterns keep $f" 0 - "" "$CHECK_VSIX" "$VSIX" "$TMP/escaped"
done

# rules_first <name> <expected-exit> <stderr-pattern> <rules> <file>: checks a
# VSIX of the good files and <file> against $EXT with <rules>, written with
# printf %b, before the lines of its .vscodeignore.
rules_first() {
    rm -rf "$TMP/rules-first"
    cp -R "$EXT" "$TMP/rules-first"
    { printf '%b' "$4"; cat "$EXT/.vscodeignore"; } > "$TMP/rules-first/.vscodeignore"
    vsix "$VSIX" "$package" "${files[@]}" "$5"
    expect_run "check-vsix.py: $1" "$2" - "$3" "$CHECK_VSIX" "$VSIX" "$TMP/rules-first"
}
# vsce splits .vscodeignore at each CR or LF and trims each line with
# JavaScript's trim(), which removes a byte order mark and a no-break space
# but not U+0085 or U+001F.
rules_first "a byte order mark before the first rule" 1 \
    "it holds private/x\.ts, which the ignore pattern 'private/\*\*' excludes$" \
    '\xef\xbb\xbfprivate/**\n' private/x.ts
rules_first "a byte order mark before a comment" 0 "" '\xef\xbb\xbf# see [notes]\n' notes.txt
rules_first "a rule between no-break spaces" 1 \
    "it holds secret\.txt, which the ignore pattern 'secret\.txt' excludes$" \
    '\xc2\xa0secret.txt\xc2\xa0\n' secret.txt
rules_first "a rule after U+0085, which trim() keeps" 0 "" '\xc2\x85secret.txt\n' secret.txt
rules_first "a rule after U+001F, which trim() keeps" 0 "" '\x1fsecret.txt\n' secret.txt
for f in one/x two/x three/x; do
    rules_first "rules that end in CR, CR LF, and LF exclude $f" 1 \
        "it holds $f, which the ignore pattern '${f%/x}/\*\*' excludes$" 'one/**\rtwo/**\r\nthree/**\n' "$f"
done

# As in minimatch, "?" matches one UTF-16 code unit, and an emoji is two.
for pattern in 'assets/?.txt' 'assets/??.txt'; do
    rm -rf "$TMP/utf-16"
    cp -R "$EXT" "$TMP/utf-16"
    printf '%s\n' "$pattern" >> "$TMP/utf-16/.vscodeignore"
    for f in assets/😀.txt assets/é.txt; do
        vsix "$VSIX" "$package" "${files[@]}" "$f"
        "$CHECK_VSIX" "$VSIX" "$TMP/utf-16" > "$TMP/out" 2> "$TMP/err"
        echo "$pattern $f $? $(grep -Ec "it holds $(ere "$f"), which the ignore pattern '$(ere "$pattern")' excludes$" "$TMP/err")"
    done
done > "$TMP/utf-16.out"
printf '%s\n' 'assets/?.txt assets/😀.txt 0 0' 'assets/?.txt assets/é.txt 1 1' \
    'assets/??.txt assets/😀.txt 1 1' 'assets/??.txt assets/é.txt 0 0' > "$TMP/utf-16.want"
expect_run "check-vsix.py: ? matches a UTF-16 code unit, half of an emoji" 0 - "" \
    diff "$TMP/utf-16.want" "$TMP/utf-16.out"

# Pattern syntax that .vscodeignore doesn't need is refused, not emulated: an
# unescaped bracket, even beside escaped ones or after an escaped backslash;
# an escaped backslash or "|"; an escape in a segment such as "*.js", which
# minimatch matches without removing escapes; and a ".", "..", or empty
# segment, which minimatch partly resolves before it matches.
vsix "$VSIX" "$package" "${files[@]}"
# shellcheck disable=SC1003 # 'tmp\' is a pattern that ends in a backslash
for pattern in '*.{js,ts}' 'tmp/[a\-c].js' '[z-a]' 'src/+(a|b).ts' 'tmp\' 'tmp\/x' \
        'assets/[draft].txt' 'assets/\[draft].txt' 'assets/\\[draft\].txt' 'a\\b' 'a\|b*' \
        'out/*\-old.js' '?\-' '!tmp/../assets/keep.txt' './src/**' 'src/./x.ts' 'src//x.ts'; do
    rm -rf "$TMP/unsupported"
    cp -R "$EXT" "$TMP/unsupported"
    printf '%s\n' "$pattern" >> "$TMP/unsupported/.vscodeignore"
    expect_run "check-vsix.py: the unsupported pattern $pattern" 1 - \
        "doesn't support the pattern '$(ere "$pattern")'$" "$CHECK_VSIX" "$VSIX" "$TMP/unsupported"
done

# vsce renames a top-level README.md, CHANGELOG.md, and extensionless license
# file in the VSIX; the ignore patterns apply to the names in the directory.
# vsce keeps README.md whatever the patterns, but not CHANGELOG.md.
cp -R "$EXT" "$TMP/renames"
: > "$TMP/renames/README.md"
: > "$TMP/renames/CHANGELOG.md"
: > "$TMP/renames/LICENSE"
printf '*.md\nLICENSE.txt\n' >> "$TMP/renames/.vscodeignore"
vsix "$VSIX" "$package" "${files[@]}" readme.md
expect_run "check-vsix.py: a renamed README and license, which the patterns don't exclude" 0 - "" \
    "$CHECK_VSIX" "$VSIX" "$TMP/renames"
vsix "$VSIX" "$package" "${files[@]}" readme.md changelog.md
expect_run "check-vsix.py: a renamed changelog, which a pattern excludes" 1 - \
    "it holds changelog\.md \(CHANGELOG\.md in .*/renames\), which the ignore pattern '\*\.md' excludes$" \
    "$CHECK_VSIX" "$VSIX" "$TMP/renames"
vsix "$VSIX" "$package" "${files[@]}" readme.md notes.md
expect_run "check-vsix.py: a file that vsce doesn't rename" 1 - \
    "it holds notes\.md, which the ignore pattern '\*\.md' excludes$" "$CHECK_VSIX" "$VSIX" "$TMP/renames"

# A lockfile that isn't an object with a "packages" object of package objects.
for lock in '{"packages": {"": {}, "node_modules/a": null}}' '{"packages":' '{}' '{"packages": []}' '[]'; do
    rm -rf "$TMP/lock"
    cp -R "$EXT" "$TMP/lock"
    printf '%s\n' "$lock" > "$TMP/lock/package-lock.json"
    expect_run "check-vsix.py: the lockfile $lock" 1 - \
        'package-lock\.json (is not valid JSON|has no "packages" object of package objects)' \
        "$CHECK_VSIX" "$VSIX" "$TMP/lock"
done
rm "$TMP/lock/package-lock.json"
expect_run "check-vsix.py: no lockfile" 1 - "cannot read .*/package-lock\.json" "$CHECK_VSIX" "$VSIX" "$TMP/lock"

# assets [<tag as JSON>]: release assets as the build and vsix jobs produce
# them: a manifest and an empty archive for each target, and the VSIX. The
# manifests' tag is null, as for a dry run, unless <tag> is given.
ASSETS=$TMP/assets
revision=0123456789abcdef0123456789abcdef01234567
virgil=fedcba9876543210fedcba9876543210fedcba98
assets() {
    local target tag=${1:-null}
    rm -rf "$ASSETS"
    mkdir "$ASSETS"
    for target in x86-64-linux x86-64-darwin; do
        printf '{"version": "1.2.3-alpha.4", "tag": %s, "target": "%s", "sourceRevision": "%s",
            "dirty": false, "virgilRevision": "%s"}\n' "$tag" "$target" "$revision" "$virgil" \
            > "$ASSETS/virgil-lsp-1.2.3-alpha.4-$target.manifest.json"
        : > "$ASSETS/virgil-lsp-1.2.3-alpha.4-$target.tar.gz"
    done
    for target in "" -linux-x64 -darwin-arm64 -darwin-x64; do
        vsix "$ASSETS/virgil-lsp-vscode-7.8.9$target.vsix" "$package" "${files[@]}"
    done
}
# notes <name> <expected-exit> <stdout-pattern|-> <stderr-pattern|-|"">
notes() {
    expect_run "release-notes.py: $1" "$2" "$3" "$4" "$NOTES" Unreleased "$ASSETS"
}

assets
notes "lists the archives" 0 '^[|] `virgil-lsp-1\.2\.3-alpha\.4-x86-64-darwin\.tar\.gz` [|] macOS' ""
notes "says that WSL 2 is best effort" 0 \
    '^[|] `virgil-lsp-1\.2\.3-alpha\.4-x86-64-linux\.tar\.gz` [|] Linux x86-64; WSL 2 on a best-effort basis [|]$' ""
notes "lists the VSIX with the version that it records" 0 \
    '.*`virgil-lsp-vscode-7\.8\.9\.vsix` are version 7\.8\.9 of the VS Code extension \(`virgil-language-server\.virgil-lsp-vscode`\), a preview\. Its versions' ""
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" \
    '{"name": "virgil-lsp-vscode", "publisher": "virgil-language-server", "version": "7.8.9"}'
for target in -linux-x64 -darwin-arm64 -darwin-x64; do
    vsix "$ASSETS/virgil-lsp-vscode-7.8.9$target.vsix" \
        '{"name": "virgil-lsp-vscode", "publisher": "virgil-language-server", "version": "7.8.9"}'
done
notes "lists a VSIX that isn't a preview without saying it is" 0 \
    '\(`virgil-language-server\.virgil-lsp-vscode`\)\. Its versions' ""
assets
notes "links the revisions" 0 "^- Server: \[\`$revision\`\]" ""
rm "$ASSETS/"*.vsix
notes "no VSIX" 1 - "expected four VS Code extensions \(\*\.vsix\) in .*, found 0$"
assets
vsix "$ASSETS/virgil-lsp-vscode-7.8.10.vsix" "${package/7.8.9/7.8.10}" "${files[@]}"
notes "an extra VSIX file" 1 - "expected four VS Code extensions .*, found 5$"
assets
echo "not a zip file" > "$ASSETS/virgil-lsp-vscode-7.8.9.vsix"
notes "VSIX that isn't a zip file" 1 - "virgil-lsp-vscode-7\.8\.9\.vsix is not a zip file"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" -
notes "VSIX without extension/package.json" 1 - "has no extension/package\.json"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" '{"name": "virgil-lsp-vscode", "version":'
notes "VSIX with malformed extension/package.json" 1 - "extension/package\.json is not valid JSON"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "${package/true/NaN}"
notes "VSIX with a non-JSON constant in extension/package.json" 1 - \
    "extension/package\.json is not valid JSON: it holds the non-JSON constant NaN$"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "@$TMP/not-utf-8.json"
notes "VSIX with extension/package.json that isn't UTF-8" 1 - "extension/package\.json is not UTF-8: "
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "@$TMP/deep.json"
notes "VSIX with deeply nested extension/package.json" 1 - \
    "extension/package\.json is nested too deeply to parse$"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "$package" "${files[@]}"
damage "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" extension/package.json data
notes "VSIX with corrupt extension/package.json data" 1 - \
    "virgil-lsp-vscode-7\.8\.9\.vsix: cannot read extension/package\.json: Bad CRC-32"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "$package" "${files[@]}"
damage "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" extension/out/extension.js name
notes "VSIX with a central directory name that isn't UTF-8" 1 - \
    "cannot read .*/virgil-lsp-vscode-7\.8\.9\.vsix: 'utf-8' codec can't decode"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "$package" "${files[@]}"
damage "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" extension/package.json zip64
notes "VSIX with a ZIP64 offset of 2**64 - 1" 1 - \
    "virgil-lsp-vscode-7\.8\.9\.vsix: cannot read extension/package\.json: ."
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "$package" "${files[@]}" raw:extension/package.json
notes "VSIX with two extension/package.json entries" 1 - \
    "holds more than one entry named 'extension/package\.json'$"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "$package" "${files[@]}" package.json_x
damage "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" extension/package.json_x nul
notes "VSIX with a second extension/package.json, after a NUL" 1 - \
    "holds more than one entry named 'extension/package\.json'$"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" '{"name": "virgil-lsp-vscode", "publisher": "virgil-language-server"}'
notes "VSIX without a version" 1 - "lacks a name, publisher, or version"
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" "${package/7.8.9/7.9.0}"
notes "VSIX named for another version" 1 - \
    "virgil-lsp-vscode-7\.8\.9\.vsix holds version 7\.9\.0 of virgil-lsp-vscode"
# A lone surrogate decodes to a string that print() can't encode as UTF-8, so
# the extension's fields are refused as if one were missing.
vsix "$ASSETS/virgil-lsp-vscode-7.8.9.vsix" \
    '{"name": "virgil-lsp-vscode", "publisher": "\ud800", "version": "7.8.9"}'
notes "VSIX with a lone surrogate in publisher" 1 - \
    "extension/package\.json lacks a name, publisher, or version$"
assets
# A build manifest is read with the same parser, so an Infinity or NaN there
# is reported rather than accepted.
printf '%s\n' '{"version": NaN, "tag": null, "target": "x86-64-linux",
    "sourceRevision": "x", "dirty": false, "virgilRevision": "y"}' \
    > "$ASSETS/virgil-lsp-1.2.3-alpha.4-x86-64-linux.manifest.json"
notes "a build manifest with a non-JSON constant" 1 - \
    "manifest\.json is not valid JSON: it holds the non-JSON constant NaN$"
# The fields that main() reads are checked together, so an incomplete or
# badly typed manifest is reported rather than raising a KeyError or TypeError.
manifest=$ASSETS/virgil-lsp-1.2.3-alpha.4-x86-64-linux.manifest.json
printf '%s\n' '{}' > "$manifest"
notes "a build manifest with no fields" 1 - \
    "manifest\.json: lacks required fields: version, tag, target, sourceRevision, dirty, virgilRevision$"
printf '%s\n' '[]' > "$manifest"
notes "a build manifest that is a JSON array" 1 - "manifest\.json is not a JSON object$"
printf '%s\n' '{"version": "1.2.3-alpha.4", "tag": null, "target": "x86-64-linux",
    "sourceRevision": "x", "dirty": false}' > "$manifest"
notes "a build manifest without virgilRevision" 1 - \
    "manifest\.json: lacks required fields: virgilRevision$"
printf '%s\n' '{"version": "1.2.3-alpha.4", "tag": null}' > "$manifest"
notes "a build manifest with several missing fields" 1 - \
    "manifest\.json: lacks required fields: target, sourceRevision, dirty, virgilRevision$"
printf '%s\n' '{"version": "1.2.3-alpha.4", "tag": null, "target": "x86-64-linux",
    "sourceRevision": "x", "dirty": "false", "virgilRevision": "y"}' > "$manifest"
notes "a build manifest with dirty as a string" 1 - \
    "manifest\.json: dirty is a string, not a boolean$"
printf '%s\n' '{"version": "1.2.3-alpha.4", "tag": null, "target": "x86-64-linux",
    "sourceRevision": "x", "dirty": 0, "virgilRevision": "y"}' > "$manifest"
notes "a build manifest with dirty as a number" 1 - \
    "manifest\.json: dirty is a number, not a boolean$"
printf '%s\n' '{"version": "1.2.3-alpha.4", "tag": 123, "target": "x86-64-linux",
    "sourceRevision": "x", "dirty": false, "virgilRevision": "y"}' > "$manifest"
notes "a build manifest with a tag that isn't a string" 1 - \
    "manifest\.json: tag is a number, not a non-empty string or null$"
# manifest_field <field> <JSON value>: writes a good build manifest to
# $manifest with <field> holding the JSON text <value>.
manifest_field() {
    local version='"1.2.3-alpha.4"' tag=null target='"x86-64-linux"'
    local sourceRevision='"x"' dirty=false virgilRevision='"y"'
    case $1 in
        version) version=$2 ;;
        tag) tag=$2 ;;
        target) target=$2 ;;
        sourceRevision) sourceRevision=$2 ;;
        dirty) dirty=$2 ;;
        virgilRevision) virgilRevision=$2 ;;
    esac
    printf '{"version": %s, "tag": %s, "target": %s, "sourceRevision": %s, "dirty": %s, "virgilRevision": %s}\n' \
        "$version" "$tag" "$target" "$sourceRevision" "$dirty" "$virgilRevision" > "$manifest"
}
# A string field must be a non-empty string that encodes as UTF-8, so that the
# notes can print it: a number, null, and an empty string are each refused with
# the JSON type rather than the value.
for field in version target sourceRevision virgilRevision; do
    for bad in 'a number:1' 'null:null' 'an empty string:""'; do
        want=${bad%%:*}
        manifest_field "$field" "${bad#*:}"
        notes "a build manifest with $field as $want" 1 - \
            "manifest\.json: $field is $want, not a non-empty string$"
    done
done
manifest_field tag '""'
notes "a build manifest with an empty tag" 1 - \
    "manifest\.json: tag is an empty string, not a non-empty string or null$"
# A lone surrogate decodes to a string that print() can't encode as UTF-8.
manifest_field sourceRevision '"\ud800"'
notes "a build manifest with a lone surrogate" 1 - \
    "manifest\.json: sourceRevision is not valid Unicode$"
# A missing field and a wrongly typed field are reported in one message.
printf '%s\n' '{"version": "1.2.3-alpha.4", "tag": null, "sourceRevision": "x",
    "dirty": "false", "virgilRevision": "y"}' > "$manifest"
notes "a build manifest with a missing and a wrongly typed field" 1 - \
    '^release-notes\.py: error: .*manifest\.json: lacks required fields: target; dirty is a string, not a boolean$'
printf '\377\n' > "$manifest"
notes "a build manifest that isn't UTF-8" 1 - \
    "cannot read .*manifest\.json: 'utf-8' codec can't decode byte 0xff"
printf '%s\n' '{"version": "1.2.3-alpha.4",' > "$manifest"
notes "a build manifest with truncated JSON" 1 - "manifest\.json is not valid JSON: "
rm "$manifest"
mkdir "$manifest"
notes "an unreadable build manifest" 1 - "cannot read .*manifest\.json: "
assets
rm "$ASSETS/virgil-lsp-1.2.3-alpha.4-x86-64-linux.tar.gz"
notes "missing archive" 1 - "missing archive virgil-lsp-1\.2\.3-alpha\.4-x86-64-linux\.tar\.gz"

# CHANGELOG.md's links are relative to the repository root, which
# release-notes.py finds from its own path, so these cases run a copy of it
# from a temporary root with a fixture CHANGELOG.md and the files it links.
REPO=$TMP/repo
mkdir -p "$REPO/scripts" "$REPO/docs/sub"
cp "$NOTES" "$REPO/scripts/"
: > "$REPO/docs/a.md"
: > "$REPO/docs/sub/b.png"
base="https://example\.test/o/r/blob/$revision"
# links <name> <expected-exit> <stdout-pattern|-> <stderr-pattern|-|""> <section>
# Prints the notes for an Unreleased section holding <section>, with the
# repository URL set as the release workflow sets it.
links() {
    printf '# Changelog\n\n## [Unreleased]\n\n%s\n\n## [0.1.0] - 2026-01-01\n\n- Old.\n' "$5" \
        > "$REPO/CHANGELOG.md"
    expect_run "release-notes.py: $1" "$2" "$3" "$4" \
        env GITHUB_SERVER_URL=https://example.test GITHUB_REPOSITORY=o/r \
        "$REPO/scripts/release-notes.py" Unreleased "$ASSETS"
}
assets
notes "today's CHANGELOG.md Unreleased section" 0 '^## Downloads$' ""
links "a repository link at the revision of a dry run, with its fragment" 0 \
    "^- See \[A\]\($base/docs/a\.md#x\)\.$" "" '- See [A](docs/a.md#x).'
links "a fragment alone links to CHANGELOG.md" 0 \
    "^- \[Top\]\($base/CHANGELOG\.md#unreleased\)$" "" '- [Top](#unreleased)'
links "an image" 0 "^- !\[B\]\($base/docs/sub/b\.png\)$" "" '- ![B](docs/sub/b.png)'
links "./ and ../ are normalized" 0 \
    "^- \[A\]\($base/docs/a\.md\) \[A\]\($base/docs/a\.md\)$" "" \
    '- [A](./docs/a.md) [A](docs/sub/../a.md)'
links "https and mailto links are kept" 0 \
    '^- \[K\]\(https://example\.com/a\.md\) \[M\]\(mailto:a@example\.com\)$' "" \
    '- [K](https://example.com/a.md) [M](mailto:a@example.com)'
# The script doesn't parse Markdown: every "](" is a link target, even in a
# code span or after a backslash escape, so no backtick or escape can hide a
# link, and every "]:" is refused, so no definition can make a reference link.
links "a link in a code span is rewritten too" 0 \
    "^- \`\[a\]\($base/docs/a\.md\)\` and \`\`x \`!\[b\]\($base/docs/sub/b\.png\)\` y\`\`$" "" \
    '- `[a](docs/a.md)` and ``x `![b](docs/sub/b.png)` y``'
links "a link in a code span is checked too" 1 - "links to docs/missing\.md, which does not exist$" \
    '- `[a](docs/missing.md)`'
links "a link after a backslash escape is rewritten too" 0 \
    "^- [\]\[A\]\($base/docs/a\.md\) and \[B[\]\]\($base/docs/a\.md\)$" "" \
    '- \[A](docs/a.md) and [B\](docs/a.md)'
links "escaped backticks don't hide a link" 0 \
    "^- Escape [\]\` before \[A\]\($base/docs/a\.md\) and [\]\` after\.$" "" \
    '- Escape \` before [A](docs/a.md) and \` after.'
links "a stray backtick doesn't hide a later entry's link" 0 "^- \[A\]\($base/docs/a\.md\)$" "" \
    '- A ` stray backtick.
- [A](docs/a.md)
- `code`'
links "a stray backtick doesn't hide a heading's link" 0 "^### \[A\]\($base/docs/a\.md\)$" "" \
    'A ` stray backtick.
### [A](docs/a.md)
`code`'
links "a link in a code span across list-item lines is rewritten" 0 \
    "^  2\. \[a\]\($base/docs/a\.md\)$" "" \
    '- `example
  2. [a](docs/a.md)
  continuation`'
links "brackets in prose and src= or href= outside a tag are kept" 0 \
    '^- Index xs\[i\]\[j\], `xs\[i\]\[j\]`, \[A\]\[\], and \[a\]\[b\]; the src= directory and href=x\.$' "" \
    '- Index xs[i][j], `xs[i][j]`, [A][], and [a][b]; the src= directory and href=x.'
links "a link outside the repository" 1 - "links to \.\./a\.md, outside the repository$" \
    '- [A](../a.md)'
links "a link that leaves the repository after descending" 1 - \
    "links to docs/\.\./\.\./a\.md, outside the repository$" '- [A](docs/../../a.md)'
links "a root-relative link" 1 - \
    "links to /docs/a\.md, which is not relative to the repository root$" '- [A](/docs/a.md)'
links "a protocol-relative link" 1 - \
    "links to //example\.com/a, which is not relative to the repository root$" \
    '- [A](//example.com/a)'
links "a link to a missing file" 1 - "links to docs/missing\.md#x, which does not exist$" \
    '- [A](docs/missing.md#x)'
# Each form is <section>|<pattern for the text that the error names>.
for form in '[A](docs/a.md "Title")|\]\(docs/a\.md "Title"\)' \
    '[A](<a.md>)|\]\(<a\.md>\)' \
    '[A]( docs/a.md)|\]\( docs/a\.md\)' \
    '[A](https://example.com/a_(b))|\]\(https://example\.com/a_\(b\)' \
    '`[a](b c)`|\]\(b c\)' \
    '[A](docs/a.md and more|\]\(docs/a\.md and more' \
    '[A](|\]\('; do
    links "a \"](\" that isn't a plain target: ${form%%|*}" 1 - \
        "cannot rewrite the link at '${form#*|}': the release notes treat every \"\]\(\" as a link" \
        "- Entry.

${form%%|*}"
done
for form in '[a]: docs/a.md|\[a\]: docs/a\.md' \
    '[a]: https://example.com|\[a\]: https://example\.com' \
    '`[x]: y`|`\[x\]: y`' \
    '[^1]: A footnote.|\[\^1\]: A footnote\.' \
    '> [guide]: docs/a.md
>
> [guide]|> \[guide\]: docs/a\.md' \
    '- Entry.

    [guide]: docs/a.md

[guide]|    \[guide\]: docs/a\.md' \
    '[a
b]: docs/a.md|b\]: docs/a\.md'; do
    links "a reference definition: ${form%%|*}" 1 - \
        "cannot rewrite '${form#*|}': the release notes don't support reference link definitions$" \
        "- Entry.

${form%%|*}"
done
# Any HTML tag with an attribute is refused, whatever the attribute, since a
# URL reaches an HTML element only through one; the error names the line. A
# tag in a code span is refused too. Each form is <section>|<pattern for the
# line that the error names>.
for form in '<a href="docs/a.md">A</a>|<a href="docs/a\.md">A</a>' \
    '<a href="https://example.com">K</a>|<a href="https://example\.com">K</a>' \
    "<A HREF='docs/a.md'>A</A>|<A HREF='docs/a\.md'>A</A>" \
    '<img src=docs/sub/b.png>|<img src=docs/sub/b\.png>' \
    '<IMG SRC="https://example.com/b.png">|<IMG SRC="https://example\.com/b\.png">' \
    '<img alt=">" src="docs/sub/b.png">|<img alt=">" src="docs/sub/b\.png">' \
    '<source srcset="docs/sub/b.png">|<source srcset="docs/sub/b\.png">' \
    '<div>
<a title=""href="docs/a.md">A</a>
<img alt=""src="docs/sub/b.png">
</div>|<a title=""href="docs/a\.md">A</a>' \
    '<div>
<img alt=""src="docs/sub/b.png">
</div>|<img alt=""src="docs/sub/b\.png">' \
    '<q cite="docs/a.md">A</q>|<q cite="docs/a\.md">A</q>' \
    '<blockquote cite="docs/a.md">A</blockquote>|<blockquote cite="docs/a\.md">A</blockquote>' \
    '<ins cite="docs/a.md">A</ins>|<ins cite="docs/a\.md">A</ins>' \
    '<del cite="docs/a.md">A</del>|<del cite="docs/a\.md">A</del>' \
    '<img longdesc="docs/a.md" usemap="docs/a.md#m">|<img longdesc="docs/a\.md" usemap="docs/a\.md#m">' \
    '<img/src="docs/sub/b.png">|<img/src="docs/sub/b\.png">' \
    '<h1 id="x">A</h1>|<h1 id="x">A</h1>' \
    '<a
href="docs/a.md">A</a>|<a\\nhref="docs/a\.md">A</a>' \
    '<span title="The src= directory">A</span>|<span title="The src= directory">A</span>' \
    '[A](<docs/a.md>)|\[A\]\(<docs/a\.md>\)' \
    '- Compare `a<b c`.|- Compare `a<b c`\.'; do
    links "an HTML tag with an attribute: ${form%%|*}" 1 - \
        "cannot rewrite [\"']${form#*|}[\"']: the release notes don't support HTML tags with attributes$" \
        "- Entry.

${form%%|*}"
done
links "angle brackets that aren't a tag with an attribute are kept" 0 \
    '^- Breaks <br>, <br/>, and <br />, a closing </a>, Ref<L>, Map<K, V>, and a < b\.$' "" \
    '- Breaks <br>, <br/>, and <br />, a closing </a>, Ref<L>, Map<K, V>, and a < b.'
links "an unclosed angle bracket doesn't take later prose for a tag" 0 \
    '^- Documented `List<T`\.$' "" \
    '- Documented `List<T`.
- The src= directory is now supported.'
# Every accepted form at once, after which no "](" in the notes may have a
# target without a URI scheme.
links "every accepted form" 0 '^## Downloads$' "" \
    '- See [A](docs/a.md#x), ![B](./docs/sub/b.png), [Top](#unreleased), and [K](https://example.com).
- `[a](docs/sub/../a.md)` and \[A](docs/a.md), escaped \` [A](docs/a.md) \`.
- `example
  2. [a](docs/a.md)
  continuation` xs[i][j], the src= directory, Map<K, V>, <br>, and a < b.'
cp "$TMP/out" "$TMP/notes.md"
expect_run "release-notes.py: no relative link target remains" 1 - "" \
    bash -c "grep -oE '\]\([^)]*' \"\$1\" | grep -vE '^\]\([A-Za-z][A-Za-z0-9+.-]*:'" - \
    "$TMP/notes.md"
assets '"v1.2.3-alpha.4"'
links "a repository link at the release's tag" 0 \
    '^- \[A\]\(https://example\.test/o/r/blob/v1\.2\.3-alpha\.4/docs/a\.md\)$' "" '- [A](docs/a.md)'

expect_run "VSIX payload integrity and provenance" 0 - - python3 "$ROOT/test/release/test_vsix_payload.py"
expect_run "release asset selection" 0 - - python3 "$ROOT/test/release/test_release_assets.py"
expect_run "bundled library integrity and reproducibility" 0 - - python3 "$ROOT/test/release/test_library.py"
expect_run "pin agreement check: unit tests" 0 - - python3 "$ROOT/test/release/test_pin.py"
expect_run "pin agreement check: the pin agrees in this tree" 0 \
    '^(Virgil pin agrees everywhere: [0-9a-f]{40}|skipped: no Git metadata for .*)$' - python3 "$ROOT/test/release/check_pin.py"
if [ "${1:-}" = --archive ]; then
    expect_run "archive installation (moved, spaces, symlink, read-only library)" 0 \
        '0 failed$' - "$ROOT/scripts/smoke-test-release.sh" "${2:?missing archive}"
fi

echo "release scripts: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
