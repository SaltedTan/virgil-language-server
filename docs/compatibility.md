# Compatibility

## Virgil revision

| Server version | Pinned Virgil revision | Notes |
| --- | --- | --- |
| `main` (unreleased) | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Required CI |
| `0.1.0-alpha.2` | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Second prerelease |
| `0.1.0-alpha.1` | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | First prerelease |

The scheduled `virgil-master` workflow builds and tests against current upstream `master` as an advisory signal. A failure there does not block merges.

## LSP baseline

The server targets a conservative subset of [LSP 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/). See [Lifecycle](architecture.md#lifecycle) for initialization behavior and [Document store](architecture.md#document-store) for synchronization capabilities.

## Host platforms

The reasons for this list are in [ADR-0003](decisions/0003-supported-platforms.md).

| Platform | Status | Build | CI |
| --- | --- | --- | --- |
| Linux x86-64 | **Supported** | `x86-64-linux` | Required |
| Windows x86-64 with WSL 2 | Best effort: the Linux build, run inside WSL 2, is untested | `x86-64-linux` | Linux job only; nothing runs inside WSL |
| macOS on Apple Silicon | **Supported** (interim), using the Intel build under Rosetta 2 | `x86-64-darwin` | Required, on `macos-15` (Apple Silicon) |
| macOS on Intel | Best effort: same build, untested | `x86-64-darwin` | None |
| Native Windows | Not supported. WSL 2 runs the Linux build on a best-effort basis. | — | — |
| Linux arm64 | Not supported yet | — | — |
| WSL 1 | Not supported | — | — |

The [repository setup script](../scripts/github-setup.sh) configures both pinned-Virgil CI jobs and the `vscode-client` job as required checks on `main`. It does not include `vscode-extension-host`; making that job required is a maintainer decision. Merging a change to the script does not apply it; a maintainer re-runs the script to update branch protection.

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

The [manual test matrix](manual-testing.md) records which of these setups were tried with each editor for each release. [Known limitations](known-limitations.md#platforms-and-editors) lists what the platforms and editors don't support.

## Editors

| Editor | Minimum version | Status |
| --- | --- | --- |
| Neovim (plain config) | 0.11 (`vim.lsp.config`) | Headless smoke test: 0.11.6 locally and pinned in Linux CI; macOS CI uses Homebrew Neovim (0.11+) and logs its version |
| LazyVim | 0.11 (`vim.lsp.config`) | Configuration provided. Not tested in CI; a scripted run on Linux with LazyVim 16.0.1 passed for 0.1.0-alpha.1, and for 0.1.0-alpha.2 with rename, completion through blink.cmp 1.10.2, and signature help for a call still being typed ([results](manual-testing.md#results)) |
| VS Code, including Remote-SSH and Remote-WSL | 1.91 (required by `vscode-languageclient` 10) | [Prerelease extension](../clients/vscode/README.md), [installed from a VSIX](../clients/vscode/README.md#installing-from-a-vsix), type-checked and lifecycle-tested in Linux CI, which also runs the [Extension Host test](../clients/vscode/README.md#extension-host-test) with VS Code 1.142.0, both on the extension loaded from source and on the packaged VSIX installed with `--install-extension`. The release workflow runs the same test on the VSIX that each prerelease attaches, with the server from that release's Linux archive. A [manual checklist](../clients/vscode/README.md#manual-checklist) covers Linux, Remote-SSH, Remote-WSL, and macOS. It was last run on Linux with VS Code 1.140 ([PR #75](https://github.com/SaltedTan/virgil-language-server/pull/75)), before the Virgil-root and project-file steps were added |

Server and VS Code extension versions are independent, and the extension's version doesn't follow the server's ([versions](../clients/vscode/README.md#versions)). Each server prerelease also attaches the VSIX of the extension's version at the tagged commit, tested against that release's Linux server, and its release notes give both versions ([Releases](releasing.md#vs-code-extension)). The extension bundles no server. This table records which versions work together.

The extension can be installed alongside `titzer.virgil`, the Virgil author's extension, which has its own grammar and debugger but no language server. In a test with VS Code 1.142.0, in either install order, `.v3` files used this extension's grammar and language configuration, the other extension's semantic highlighting and debugger also worked, and this extension's features were unaffected ([with the Virgil extension installed](../clients/vscode/README.md#with-the-virgil-extension-installed)).

| VS Code extension | Server versions |
| --- | --- |
| `0.1.1` (prerelease VSIX, attached to server `0.1.0-alpha.2`; `clients/vscode` on `main`) | `0.1.0-alpha.2`, `0.1.0-alpha.1`, and `main` |
| `0.1.0` (prerelease VSIX, attached to server `0.1.0-alpha.1`) | `0.1.0-alpha.1` and `0.1.0-alpha.2` |

The two extension versions have the same code: `0.1.1` differs from `0.1.0` only in its README. The extension relies only on standard LSP: full-text document synchronization, `publishDiagnostics`, `textDocument/documentSymbol`, `textDocument/definition`, `textDocument/hover`, `textDocument/references`, `textDocument/signatureHelp`, `workspace/symbol`, file events (`workspace/didChangeWatchedFiles`), and workspace-folder changes. With `0.1.0-alpha.2`, it also uses `textDocument/prepareRename` and `textDocument/rename` with versioned `documentChanges`, and `textDocument/completion`, whose `completionProvider` declares the trigger character `.` for [member completion](architecture.md#member-completion), none of which `0.1.0-alpha.1` answers. It needs `virgil-lsp-worker` beside `virgil-lsp`, which its startup checks verify. Each release workflow tests the VSIX it attaches with its own server; the Extension Host test also passed with `0.1.0` and `0.1.0-alpha.2`'s server, and with `0.1.1` and `0.1.0-alpha.1`'s ([results](manual-testing.md#010-alpha2)).
