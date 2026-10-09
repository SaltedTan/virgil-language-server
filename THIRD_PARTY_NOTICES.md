# Third-party notices

This file lists third-party material that is used by, linked into, or adapted in this repository. Every entry records its license and exact revision. Update it in the same pull request that adds, upgrades, or removes the dependency.

## Virgil (Aeneas compiler and libraries)

| Field | Value |
| --- | --- |
| Upstream | https://github.com/titzer/virgil |
| Location | `vendor/virgil` (git submodule) |
| Pinned revision | `7cf038fc13a5474836bec8b14c7a3e796fd07fa5` (2026-10-08) |
| License | Apache License 2.0 (`vendor/virgil/aeneas/LICENSE`; source headers refer to it) |
| Copyright | Ben L. Titzer, Google Inc., and the Virgil authors, as stated in individual source headers |
| How it is used | Compiled into the `virgil-lsp` executable: `aeneas/src`, plus the libraries listed in `aeneas/DEPS` (`lib/util`, `lib/asm/*`, `lib/file/elf`) and `lib/file/json/JsonParser.v3`. `lib/test` is used only by the unit-test executable. The prebuilt `bin/stable` compiler is used as a build tool. |
| Modifications | The submodule is unmodified. The single-file parser driver is adapted as recorded below. |

Release archives that contain a `virgil-lsp` binary must include a copy of the Apache License 2.0 and this notice file.

## Adapted material

`src/analysis/AnalysisSingleFileParser.v3` adapts the setup and top-level loop of [`Parser.parseFile`](https://github.com/titzer/virgil/blob/7cf038fc13a5474836bec8b14c7a3e796fd07fa5/aeneas/src/vst/Parser.v3#L102-L123), and from `ParserState` its [current and retrospective token extraction](https://github.com/titzer/virgil/blob/7cf038fc13a5474836bec8b14c7a3e796fd07fa5/aeneas/src/vst/ParserState.v3#L40-L55), [`advance`](https://github.com/titzer/virgil/blob/7cf038fc13a5474836bec8b14c7a3e796fd07fa5/aeneas/src/vst/ParserState.v3#L56-L61), [`error`](https://github.com/titzer/virgil/blob/7cf038fc13a5474836bec8b14c7a3e796fd07fa5/aeneas/src/vst/ParserState.v3#L159-L169), and [`errorAtOffset`](https://github.com/titzer/virgil/blob/7cf038fc13a5474836bec8b14c7a3e796fd07fa5/aeneas/src/vst/ParserState.v3#L178-L184), from Virgil revision `7cf038fc13a5474836bec8b14c7a3e796fd07fa5`, under Apache License 2.0. It also mirrors the whitespace and comment skipping of [`Parser.skipToNextToken`](https://github.com/titzer/virgil/blob/7cf038fc13a5474836bec8b14c7a3e796fd07fa5/aeneas/src/vst/Parser.v3#L1445-L1512). Its original notice, **Copyright 2011 Google Inc. All rights reserved.**, is preserved in the adapted file. See [Compiler adapter](docs/architecture.md#compiler-adapter) for implementation details.

## VS Code development client (npm)

`clients/vscode/` installs these packages from the npm registry with `npm ci`. Its `package-lock.json` is the record of exact versions: it pins the version and integrity hash of every package, including transitive ones, so dependency updates change it rather than this file. Nothing from these packages is committed or adapted, and no extension package (VSIX) is published yet. A VSIX would bundle the runtime packages, so their license texts must ship with it.

| Package | License | Use |
| --- | --- | --- |
| [`vscode-languageclient`](https://github.com/microsoft/vscode-languageserver-node) | MIT | Runtime: the LSP client |
| `vscode-languageserver-protocol`, `vscode-languageserver-types`, `vscode-languageserver-textdocument`, `vscode-jsonrpc` | MIT | Runtime, through `vscode-languageclient` |
| `minimatch` | BlueOak-1.0.0 | Runtime, through `vscode-languageclient` |
| `brace-expansion`, `balanced-match` | MIT | Runtime, through `minimatch` |
| `semver` | ISC | Runtime, through `vscode-languageclient` |
| [`typescript`](https://github.com/microsoft/TypeScript), with its platform package `@typescript/typescript-<platform>` | Apache-2.0 | Build tool |
| `@types/vscode`, `@types/node`, `undici-types` | MIT | Type declarations for the build |
