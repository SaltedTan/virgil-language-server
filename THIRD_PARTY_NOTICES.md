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
| How it is used | Compiled into the `virgil-lsp` executable: `aeneas/src`, plus the libraries listed in `aeneas/DEPS` (`lib/util`, `lib/asm/*`, `lib/file/elf`). `lib/test` is used only by the unit-test executable. The prebuilt `bin/stable` compiler is used as a build tool. |
| Modifications | None. This repository calls Virgil APIs and does not copy or modify Virgil source. |

Release archives that contain a `virgil-lsp` binary must include a copy of the Apache License 2.0 and this notice file.

## Adapted material

None. This project is an independent implementation. If you intentionally adapt licensed code, for example from the MIT-licensed [`linxuanm/virgil-lsp`](https://github.com/linxuanm/virgil-lsp), add an entry here. Include the source URL, revision, license, the files affected, and the preserved copyright notice. Do not copy material from repositories that have no explicit license.
