# Compatibility

## Virgil revision

| Server version | Pinned Virgil revision | Notes |
| --- | --- | --- |
| `main` (unreleased) | [`9945e430`](https://github.com/titzer/virgil/commit/9945e4300bba2cd6d872c1a6f4ecfaa600f4be48) (2026-09-30) | Required CI |

The scheduled `virgil-master` workflow builds and tests against current upstream `master` as an advisory signal. A failure there does not block merges.

## LSP baseline

The server targets a conservative subset of [LSP 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/) and negotiates capabilities instead of assuming a particular editor.

## Host platforms

| Platform | Status |
| --- | --- |
| Linux x86-64 | Supported. Built and tested in CI. |
| macOS x86-64 | Expected to work through Virgil's `x86-64-darwin` target. Not yet tested in CI. |
| macOS arm64 | Untested. May work under Rosetta 2 using the x86-64 build. |
| Windows | Not supported natively. Use VS Code Remote–WSL with the Linux build. |

## Editors

| Editor | Minimum version | Status |
| --- | --- | --- |
| Neovim / LazyVim | 0.11 (`vim.lsp.config`) | Planned (M2) |
| VS Code | to be decided | Planned (M2) |

Server and VS Code extension versions are released independently. This table will record which ranges work together.
