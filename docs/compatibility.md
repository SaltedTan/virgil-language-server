# Compatibility

## Virgil revision

| Server version | Pinned Virgil revision | Notes |
| --- | --- | --- |
| `main` (unreleased) | [`9945e430`](https://github.com/titzer/virgil/commit/9945e4300bba2cd6d872c1a6f4ecfaa600f4be48) (2026-09-30) | Required CI |

The scheduled `virgil-master` workflow builds and tests against current upstream `master` as an advisory signal. A failure there does not block merges.

## LSP baseline

The server targets a conservative subset of [LSP 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/) and negotiates capabilities instead of assuming a particular editor.

## Host platforms

The reasons for this list are in [ADR-0003](decisions/0003-supported-platforms.md).

| Platform | Status | Build | CI |
| --- | --- | --- | --- |
| Linux x86-64 | **Supported** | `x86-64-linux` | Required |
| Windows x86-64 with WSL 2 | **Supported**, using the Linux build inside WSL 2 | `x86-64-linux` | Covered by the Linux job |
| macOS on Apple Silicon | **Supported** (interim), using the Intel build under Rosetta 2 | `x86-64-darwin` | `macos-15` (Apple Silicon), not yet required |
| macOS on Intel | Best effort: same build, untested | `x86-64-darwin` | None |
| Native Windows | Not supported. Use WSL 2. | — | — |
| Linux arm64 | Not supported yet | — | — |
| WSL 1 | Not supported | — | — |

Apple Silicon support will move to a native `arm64-darwin` build once Virgil supports that target. Rosetta 2 is needed until then. Install it with:

```sh
softwareupdate --install-rosetta --agree-to-license
```

## Where the server runs

The language server runs on the machine that holds the files, which isn't always the machine showing the editor.

| Setup | `virgil-lsp` runs on | Install it on |
| --- | --- | --- |
| Editor on a Linux machine | That Linux machine | Linux |
| VS Code Remote-SSH, or Neovim over SSH | The remote Linux machine | The remote machine |
| VS Code Remote-WSL, or Neovim inside WSL | Inside WSL 2 | The WSL distribution |
| Editor on a Mac, local files | The Mac | macOS |

## Editors

| Editor | Minimum version | Status |
| --- | --- | --- |
| Neovim / LazyVim | 0.11 (`vim.lsp.config`) | Planned (M2) |
| VS Code, including Remote-SSH and Remote-WSL | to be decided | Planned (M2) |

Server and VS Code extension versions are released independently. This table will record which ranges work together.
