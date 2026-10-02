# Third-party notices

This file lists third-party material that is used by, linked into, or adapted in this repository. Every entry records its license and exact revision. Update it in the same pull request that adds, upgrades, or removes the dependency.

## Virgil (Aeneas compiler and libraries)

| Field | Value |
| --- | --- |
| Upstream | https://github.com/titzer/virgil |
| Location | `vendor/virgil` (git submodule) |
| Pinned revision | `9945e4300bba2cd6d872c1a6f4ecfaa600f4be48` (2026-09-30) |
| License | Apache License 2.0 (`vendor/virgil/aeneas/LICENSE`; source headers refer to it) |
| Copyright | Ben L. Titzer, Google Inc., and the Virgil authors, as stated in individual source headers |
| How it is used | Compiled into the `virgil-lsp` executable: `aeneas/src`, plus the libraries listed in `aeneas/DEPS` (`lib/util`, `lib/asm/*`, `lib/file/elf`) and `lib/file/json/JsonParser.v3`. `lib/test` is used only by the unit-test executable. The prebuilt `bin/stable` compiler is used as a build tool. |
| Modifications | The submodule is unmodified. The single-file parser driver is adapted as recorded below. |

Release archives that contain a `virgil-lsp` binary must include a copy of the Apache License 2.0 and this notice file.

## Adapted material

`src/analysis/AnalysisSingleFileParser.v3` adapts the setup and top-level loop of [`Parser.parseFile`](https://github.com/titzer/virgil/blob/9945e4300bba2cd6d872c1a6f4ecfaa600f4be48/aeneas/src/vst/Parser.v3#L102-L123) and current-token extraction from [`ParserState`](https://github.com/titzer/virgil/blob/9945e4300bba2cd6d872c1a6f4ecfaa600f4be48/aeneas/src/vst/ParserState.v3#L40-L55) from Virgil revision `9945e4300bba2cd6d872c1a6f4ecfaa600f4be48`, under Apache License 2.0. Its original notice, **Copyright 2011 Google Inc. All rights reserved.**, is preserved in the adapted file. See [Compiler adapter](docs/architecture.md#compiler-adapter) for implementation details.
