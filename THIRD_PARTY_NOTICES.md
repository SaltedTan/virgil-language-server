# Third-party notices

This file lists third-party material that is used by, linked into, or adapted in this repository. Every entry records its license and exact revision. Update it in the same pull request that adds, upgrades, or removes the dependency.

## Virgil (Aeneas compiler and libraries)

| Field | Value |
| --- | --- |
| Upstream | https://github.com/titzer/virgil |
| Location | `vendor/virgil` (git submodule) |
| Pinned revision | `b86f43f102406e94dc2ac4a85909c38ef414e214` (2026-11-03) |
| License | Apache License 2.0 (the root README names `vendor/virgil/rt/LICENSE`; compiler headers also refer to `aeneas/LICENSE`) |
| Copyright | Ben L. Titzer, Google Inc., and the Virgil authors, as stated in individual source headers |
| How it is used | Compiled into the `virgil-lsp` executable, or for the JVM build into `virgil-lsp.jar` and `virgil-lsp-worker.jar`: `aeneas/src`, plus the libraries listed in `aeneas/DEPS` (`lib/util`, `lib/asm/*`, `lib/file/elf`) and `lib/file/json/JsonParser.v3`. `lib/test` is used only by the unit-test executable. The prebuilt `bin/stable` compiler is used as a build tool. Release archives and platform VSIX payloads also redistribute the entire unchanged `lib/` as `virgil/lib/`, with `virgil/LICENSE` copied from `rt/LICENSE` and `virgil/REVISION` recording this pin. |
| Modifications | The submodule is unmodified. The single-file parser driver is adapted as recorded below. |

The bundled sources retain all upstream headers and attribution. The bundle contains no compiler executable, `rt/`, or `aeneas/` sources.

`lib/util/FpConv.v3` states that it is adapted from [rsc/fpfmt](https://github.com/rsc/fpfmt), Copyright 2025 The Go Authors, under a BSD license. The compiler's number parser reads float literals through it, so it is compiled into the `virgil-lsp` and `virgil-lsp-worker` executables as well as bundled. That repository's `LICENSE`, BSD-3-Clause at revision `ec108cbb39e2cae8e1961d79ba34bd9cc8249f80`, reads:

```text
Copyright (c) 2009 The Go Authors. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

   * Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following disclaimer
in the documentation and/or other materials provided with the
distribution.
   * Neither the name of Google Inc. nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

Before Virgil `b86f43f1`, `lib/util/Floats.v3` carried Ulf Adams's copyright and Apache-2.0 notice; the rewritten file no longer carries it.

Release archives and platform VSIX payloads that contain a `virgil-lsp` binary must include a copy of the Apache License 2.0 and this notice file. [`scripts/package-release.sh`](scripts/package-release.sh) adds both to the archive; [`scripts/package-vsix.py`](scripts/package-vsix.py) preserves them in the VSIX payload.

## Adapted material

`src/analysis/AnalysisSingleFileParser.v3` adapts the setup and top-level loop of [`Parser.parseFile`](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/aeneas/src/vst/Parser.v3#L102-L123), and from `ParserState` its [current and retrospective token extraction](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/aeneas/src/vst/ParserState.v3#L40-L55), [`advance`](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/aeneas/src/vst/ParserState.v3#L56-L61), [`optN` matching and the `column` tab rule](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/aeneas/src/vst/ParserState.v3), [`error`](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/aeneas/src/vst/ParserState.v3#L159-L169), and [`errorAtOffset`](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/aeneas/src/vst/ParserState.v3#L178-L184), from Virgil revision `b86f43f102406e94dc2ac4a85909c38ef414e214`, under Apache License 2.0. It also mirrors the whitespace and comment skipping of [`Parser.skipToNextToken`](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/aeneas/src/vst/Parser.v3#L1445-L1512). Its original notice, **Copyright 2011 Google Inc. All rights reserved.**, is preserved in the adapted file. See [Compiler adapter](docs/architecture.md#compiler-adapter) for implementation details.

`src/os/jvm/V3S_System.java`, the runtime class for Virgil's JVM target, is adapted from [`rt/jvm/src/V3S_System.java`](https://github.com/titzer/virgil/blob/b86f43f102406e94dc2ac4a85909c38ef414e214/rt/jvm/src/V3S_System.java) at Virgil revision `b86f43f102406e94dc2ac4a85909c38ef414e214`, under Apache License 2.0. Its arithmetic, conversion, byte-access, time, `chmod`, and `error` methods are copied unchanged, and `test/jvm/V3SSystemTest.java` checks them against the pinned copy. Its file and output methods are rewritten around a descriptor table, and the host operations after them are this repository's. Its original notice, **Copyright 2008 Google Inc. All rights reserved.**, is preserved in the adapted file. Its compiled classes are in both JVM jars, which the JVM release archive and VSIX redistribute with the Apache License 2.0 and this notice file.

## Eclipse Temurin Java runtime (JVM assets only)

| Field | Value |
| --- | --- |
| Upstream | [Eclipse Temurin](https://adoptium.net/temurin/) by Eclipse Adoptium, binaries from https://github.com/adoptium/temurin21-binaries |
| Pinned release | Temurin `21.0.13+9` (`JAVA_RUNTIME_VERSION` `21.0.13+9-LTS`), in [`scripts/jvm-runtime.json`](scripts/jvm-runtime.json) with each JDK archive's SHA-256: `OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.13_9.tar.gz` (`bf036b35b071bfb2f54f2c0af8ee8d88afa95ee6eb3dce02c0f94cfc2524d04a`), whose modules are linked, and `OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz` (`d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c`), a build tool |
| Corresponding source | Attached to the same GitHub release as the JVM archive and VSIX: `OpenJDK21U-jdk-sources_21.0.13_9.tar.gz` (115,231,532 bytes, SHA-256 `b5232c9980d8a0f0459676c084c41c6e2c771ed9e785890b03c6a438406ad1ca`), Eclipse Adoptium's own source archive for this build, unchanged and under its upstream name. Upstream it is an asset of Adoptium's release [`jdk-21.0.13+9`](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.13%2B9), built from tag `jdk-21.0.13+9_adopt` of https://github.com/adoptium/jdk21u (commit `2ab10ca20c19`, which the macOS JDK's `release` file records as `SOURCE`), with Adoptium's build scripts at commit `1cf43d7a3e77886bfea7d550a902e151a87d724d` of https://github.com/adoptium/temurin-build (`BUILD_SOURCE`). This project's `jlink` options are in [`scripts/jvm-runtime.py`](scripts/jvm-runtime.py) (`JLINK_OPTIONS`) and in each JVM archive's `manifest.json` (`runtime.jlink.options`); [`scripts/jvm-runtime.json`](scripts/jvm-runtime.json) pins the source archive with the JDKs |
| License | GNU General Public License, version 2, with the Classpath Exception (`legal/java.base/LICENSE` and `legal/java.base/ASSEMBLY_EXCEPTION`), and the licenses of the third-party code in `java.base`, which `legal/java.base/ADDITIONAL_LICENSE_INFO` and the `legal/java.base/*.md` files give |
| Copyright | Oracle and/or its affiliates, and the other holders that the `legal/java.base/` files name |
| How it is used | The JVM release archive (`virgil-lsp-<version>-jvm-arm64-darwin.tar.gz`) and VSIX (`virgil-lsp-vscode-<extension version>-darwin-arm64-jvm.vsix`) redistribute it in `runtime/`, with its `legal/java.base/` notices and `release` file, to run the two jars. [`scripts/jvm-runtime.py`](scripts/jvm-runtime.py) links it with `jlink` from the pinned macOS JDK's `java.base` module. The build host's pinned JDK runs `jlink`, and `javac` for the runtime classes, as build tools; on Linux that is the Linux JDK, no part of which is redistributed. No other release asset contains any part of either JDK. |
| Modifications | jlink keeps only the `java.base` module, strips Java debug attributes from its classes, compresses its module image, and leaves out header files and man pages. It copies the native files (`bin/`, `lib/*.dylib`, `lib/server/`, `lib/jspawnhelper`) unchanged, with Temurin's own code signatures, and packaging checks that they match the JDK's. Packaging adds the JDK's `IMPLEMENTOR`, `IMPLEMENTOR_VERSION`, `JAVA_RUNTIME_VERSION`, `OS_NAME`, and `OS_ARCH` to the runtime's `release` file. |

## VS Code client (npm)

`clients/vscode/` installs these packages from the npm registry with `npm ci`. Its `package-lock.json` is the record of exact versions: it pins the version and integrity hash of every package, including transitive ones, so dependency updates change it rather than this file. Nothing from these packages is committed or adapted. The Extension Host test also downloads a VS Code build from Microsoft into `clients/vscode/.vscode-test/` when it runs; it isn't committed or redistributed.

Of the npm packages below, the extension package (VSIX) that [`npm run package`](clients/vscode/README.md#packaging) builds includes only the runtime packages, unmodified, each with its own license file: `License.txt` in the `vscode-*` packages, which also carry `thirdpartynotices.txt`, `LICENSE.md` in `minimatch` and `balanced-match`, and `LICENSE` in `brace-expansion` and `semver`. The VSIX also carries the repository's Apache License 2.0. Build tools, type declarations, and test tools aren't bundled. List a VSIX's files with `unzip -l` to check them.

| Package | License | Use |
| --- | --- | --- |
| [`vscode-languageclient`](https://github.com/microsoft/vscode-languageserver-node) | MIT | Runtime: the LSP client |
| `vscode-languageserver-protocol`, `vscode-languageserver-types`, `vscode-languageserver-textdocument`, `vscode-jsonrpc` | MIT | Runtime, through `vscode-languageclient` |
| `minimatch` | BlueOak-1.0.0 | Runtime, through `vscode-languageclient` |
| `brace-expansion`, `balanced-match` | MIT | Runtime, through `minimatch` |
| `semver` | ISC | Runtime, through `vscode-languageclient`; also used by `@vscode/test-electron` |
| [`typescript`](https://github.com/microsoft/TypeScript), with its platform package `@typescript/typescript-<platform>` | Apache-2.0 | Build tool |
| `@types/vscode`, `@types/node`, `undici-types` | MIT | Type declarations for the build |
| [`@vscode/test-electron`](https://github.com/microsoft/vscode-test) | MIT | Test tool: downloads VS Code for the [Extension Host test](clients/vscode/README.md#extension-host-test) |
| `http-proxy-agent`, `https-proxy-agent`, `agent-base`, `debug`, `ms` | MIT | Test tool, through `@vscode/test-electron` |
| `jszip` | MIT OR GPL-3.0-or-later, used under MIT | Test tool, through `@vscode/test-electron` |
| `lie`, `immediate`, `setimmediate`, `readable-stream`, `core-util-is`, `isarray`, `process-nextick-args`, `safe-buffer`, `string_decoder`, `util-deprecate` | MIT | Test tool, through `jszip` |
| `inherits` | ISC | Test tool, through `readable-stream` |
| `pako` | MIT AND Zlib | Test tool, through `jszip` |
| `ora`, `chalk`, `cli-cursor`, `restore-cursor`, `onetime`, `mimic-function`, `cli-spinners`, `is-interactive`, `is-unicode-supported`, `log-symbols`, `stdin-discarder`, `string-width`, `emoji-regex`, `get-east-asian-width`, `strip-ansi`, `ansi-regex` | MIT | Test tool, through `@vscode/test-electron` (progress output) |
| `signal-exit` | ISC | Test tool, through `restore-cursor` |
| [`@vscode/vsce`](https://github.com/microsoft/vscode-vsce) | MIT | Build tool: packages the VSIX |
| `@vscode/vsce-sign`, with its platform package `@vscode/vsce-sign-<platform>` | Microsoft Software License Terms (proprietary; `node_modules/@vscode/vsce-sign/LICENSE.txt`), which permit use with Visual Studio Code to develop and test applications | Build tool, through `@vscode/vsce`, which loads it for package signing and signature verification. `npm run package` doesn't sign the VSIX, and the package isn't redistributed. |
| `@azure/abort-controller`, `@azure/core-auth`, `@azure/core-client`, `@azure/core-process`, `@azure/core-rest-pipeline`, `@azure/core-tracing`, `@azure/core-util`, `@azure/identity`, `@azure/logger`, `@azure/msal-browser`, `@azure/msal-common`, `@azure/msal-node`, `@napi-rs/keyring`, with its platform package `@napi-rs/keyring-<platform>`, `@secretlint/core`, `@secretlint/profiler`, `@secretlint/secretlint-rule-no-dotenv`, `@secretlint/secretlint-rule-preset-recommend`, `@secretlint/source-creator`, `@secretlint/types`, `@typespec/ts-http-runtime`, `azure-devops-node-api`, `buffer-crc32`, `bundle-name`, `call-bind-apply-helpers`, `call-bound`, `cockatiel`, `commander`, `default-browser`, `default-browser-id`, `define-lazy-prop`, `dunder-proto`, `es-define-property`, `es-errors`, `es-object-atoms`, `fdir`, `function-bind`, `get-intrinsic`, `get-proto`, `gopd`, `has-symbols`, `hasown`, `is-docker`, `is-in-ssh`, `is-inside-container`, `is-wsl`, `jsonc-parser`, `jsonwebtoken`, `jwa`, `jws`, `lodash.includes`, `lodash.isboolean`, `lodash.isinteger`, `lodash.isnumber`, `lodash.isplainobject`, `lodash.isstring`, `lodash.once`, `marked`, `math-intrinsics`, `mime`, `object-inspect`, `open`, `parse5`, `pend`, `picomatch`, `powershell-utils`, `proper-lockfile`, `retry`, `run-applescript`, `side-channel`, `side-channel-list`, `side-channel-map`, `side-channel-weakmap`, `tinyglobby`, `tunnel`, `typed-rest-client`, `underscore`, `url-join`, `wsl-utils`, `xml2js`, `xmlbuilder`, `yauzl`, `yazl` | MIT | Build tool, through `@vscode/vsce` |
| `graceful-fs`, `hosted-git-info`, `lru-cache`, `mute-stream`, `read`, `yallist` | ISC | Build tool, through `@vscode/vsce` |
| `boundary`, `entities`, `structured-source` | BSD-2-Clause | Build tool, through `@vscode/vsce` |
| `buffer-equal-constant-time`, `qs` | BSD-3-Clause | Build tool, through `@vscode/vsce` |
| `ecdsa-sig-formatter` | Apache-2.0 | Build tool, through `@vscode/vsce` |
| `binaryextensions`, `editions`, `istextorbinary`, `textextensions`, `version-range` | Artistic-2.0 | Build tool, through `@vscode/vsce` |
| `sax` | BlueOak-1.0.0 | Build tool, through `@vscode/vsce` |
| `tslib` | 0BSD | Build tool, through `@vscode/vsce` |

`@vscode/vsce` also uses some of the packages listed above for the runtime and test tools, such as `semver`, `minimatch`, `debug`, and the proxy agents, and a second copy of `signal-exit` (ISC).

## Language Server Protocol 3.17 metaModel

| Field | Value |
| --- | --- |
| Upstream | https://github.com/microsoft/language-server-protocol |
| Location | `test/protocol/schema/3.17/metaModel.json` |
| Pinned revision | `baf2db31b5fb314a8f358f9a839d3c66e9d98734` (`_specifications/lsp/3.17/metaModel/metaModel.json`) |
| SHA-256 | `c207890d9e0f54d8f9e462a924670f563423ec9af90a0a369645ff55384c8d6d` |
| License | Creative Commons Attribution 4.0 International (`test/protocol/schema/3.17/License.txt`, copied unchanged from upstream) |
| Attribution | Microsoft, Language Server Protocol contributors |
| How it is used | Offline input for the bounded protocol shape tests. It is not compiled into the server or shipped in release archives. |
| Modifications | None. The model retains its CC BY 4.0 license separately from the repository's Apache-2.0 code. See the [model README](test/protocol/schema/3.17/README.md) for source and update instructions. |
