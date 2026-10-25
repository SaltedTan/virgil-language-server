# Compatibility

## Virgil revision

| Server version | Pinned Virgil revision | Notes |
| --- | --- | --- |
| `main` (unreleased) | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Required CI |
| `0.1.0-alpha.3` | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Third prerelease |
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

The [repository setup script](../scripts/github-setup.sh) configures both pinned-Virgil CI jobs and the `vscode-client` job as required checks on `main`. It does not include `vscode-extension-host`, `vscode-extension-host-macos`, or `lazyvim-linux`, the [LazyVim test](../test/e2e/lazyvim/README.md#ci) on Linux, which runs on every pull request but stays advisory until it has passed 20 runs in a row; its failures show in the pull request without blocking the merge. The count restarts after merging the harness fixes for [weekly run 39885998015](https://github.com/SaltedTan/virgil-language-server/actions/runs/39885998015). The weekly [editor tests workflow](../.github/workflows/editor-tests.yml) runs the LazyVim test on Linux with Neovim 0.11 and 0.12 and on macOS, the Extension Host test on macOS on an installed VSIX, and the [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test) on Linux, and none of its jobs are required. The [release workflow](releasing.md#how-the-workflow-works) runs the same jobs on each release's own archives and VSIX, and publishes only if they pass, the Remote-SSH test included ([manual testing](manual-testing.md#vs-code)); that is release policy, not a required check. Making any of these jobs required is a maintainer decision. Merging a change to the script does not apply it; a maintainer re-runs the script to update branch protection.

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
| LazyVim | 0.11 (`vim.lsp.config`) | Configuration provided. The [LazyVim test](../test/e2e/lazyvim/README.md#ci) runs on Linux with Neovim 0.11.6 on every pull request ([advisory CI policy](#host-platforms)), and weekly and on each release's archives, on Linux with Neovim 0.11.6 and 0.12.6 and on macOS. A scripted run on Linux with LazyVim 16.0.1 passed for 0.1.0-alpha.1, and for 0.1.0-alpha.2 with rename, completion through blink.cmp 1.10.2, and signature help for a call still being typed, also on macOS with Neovim 0.12.4, restarting with `:lsp restart`. For 0.1.0-alpha.3, the LazyVim test passed on Linux with Neovim 0.11.6 and 0.12.4, and on macOS with Neovim 0.12.4, with its one expected failure: noice.nvim drops the missing-root warning ([results](manual-testing.md#results)) |
| VS Code, including Remote-SSH and Remote-WSL | 1.91 (required by `vscode-languageclient` 10) | [Prerelease extension](../clients/vscode/README.md), [installed from a VSIX](../clients/vscode/README.md#installing-from-a-vsix), type-checked and lifecycle-tested in Linux CI, which also runs the [Extension Host test](../clients/vscode/README.md#extension-host-test) with VS Code 1.142.0, both on the extension loaded from source and on the packaged VSIX installed with `--install-extension`. macOS CI (Apple Silicon, under Rosetta 2) runs it on the extension loaded from source, and a weekly workflow on the packaged VSIX. The release workflow runs the same test on the VSIX that each prerelease attaches, using each platform VSIX's bundled server, and keeps a universal-VSIX check with an external server. The weekly workflow, on a packaged VSIX, and the release workflow, on the release's Linux platform VSIX, also run it through Remote-SSH, from a Linux client to the same machine ([Remote-SSH test](../clients/vscode/README.md#remote-ssh-test)). A [manual checklist](../clients/vscode/README.md#manual-checklist) covers Linux, Remote-SSH, Remote-WSL, and macOS. It was last run on Linux with VS Code 1.140 ([PR #75](https://github.com/SaltedTan/virgil-language-server/pull/75)), before the Virgil-root and project-file steps were added. For 0.1.0-alpha.2 and 0.1.0-alpha.3, the Extension Host test also passed on macOS (Apple Silicon) with VS Code 1.142.0, on each release's VSIX ([results](manual-testing.md#results)). For 0.1.0-alpha.3, it also passed through Remote-SSH, from a Linux client to a Linux host, with the release's VSIX installed on the remote ([results](manual-testing.md#010-alpha3)) |

Server and VS Code extension versions are independent, and the extension's version doesn't follow the server's ([versions](../clients/vscode/README.md#versions)). Each server prerelease also attaches the VSIX of the extension's version at the tagged commit, tested against that release's Linux server, and its release notes give both versions ([Releases](releasing.md#vs-code-extension)). Starting with extension `0.1.3`, platform VSIX files bundle the tested server, worker, and pinned library; the universal VSIX needs an external server. An explicit `virgil.server.path` takes precedence over a bundle. This table records which versions work together.

The extension can be installed alongside `titzer.virgil`, the Virgil author's extension. [With the Virgil extension installed](../clients/vscode/README.md#with-the-virgil-extension-installed) describes what VS Code 1.142.0 did in a test with both installed, and what that test didn't cover.

| VS Code extension | Server versions |
| --- | --- |
| `0.1.3` (development prerelease VSIX) | Its bundled development server, or an external `0.1.0-alpha.3` or `main` server |
| `0.1.2` (prerelease VSIX, attached to server `0.1.0-alpha.3`) | `0.1.0-alpha.3` and `main`; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root (below) |
| `0.1.1` (prerelease VSIX, attached to server `0.1.0-alpha.2`) | `0.1.0-alpha.3`; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root |
| `0.1.0` (prerelease VSIX, attached to server `0.1.0-alpha.1`) | `0.1.0-alpha.3`; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root |

The three released extension versions (`0.1.0`–`0.1.2`) have the same code, and differ only in their version numbers and READMEs. The extension relies only on standard LSP: full-text document synchronization, `publishDiagnostics`, `textDocument/documentSymbol`, `textDocument/definition`, `textDocument/hover`, `textDocument/references`, `textDocument/signatureHelp`, `workspace/symbol`, file events (`workspace/didChangeWatchedFiles`), and workspace-folder changes. With `0.1.0-alpha.2` and later, it also uses `textDocument/prepareRename` and `textDocument/rename` with versioned `documentChanges`, and `textDocument/completion`, whose `completionProvider` declares the trigger character `.` for [member completion](architecture.md#member-completion), none of which `0.1.0-alpha.1` answers. With a server from `main`, it also uses `textDocument/documentHighlight` ([document highlights](architecture.md#document-highlights)), and `textDocument/typeDefinition` ([resolved type navigation](architecture.md#type-definition)), which no released server answers yet. It needs `virgil-lsp-worker` beside `virgil-lsp`, which its startup checks verify.

With a server before `0.1.0-alpha.3`, every extension version needs a Virgil root. When neither `virgil.virgilRoot` nor `VIRGIL_LOC` gives an absolute path, `0.1.0-alpha.1` and `0.1.0-alpha.2` exit as they start, so VS Code has no language server, not even for single files ([#175](https://github.com/SaltedTan/virgil-language-server/issues/175)). Set `virgil.virgilRoot` to a Virgil checkout ([The Virgil root](releasing.md#the-virgil-root)), or install `0.1.0-alpha.3`, which starts without a root.

Each release workflow tests the VSIX it attaches with its own server. The Extension Host test also passed with `0.1.0` and `0.1.0-alpha.2`'s server, and with `0.1.1` and `0.1.0-alpha.1`'s ([results](manual-testing.md#010-alpha2)). For `0.1.0-alpha.3`, it passed with `0.1.0` and with `0.1.1` on that release's server, with that release's test, no-root checks included. It also passed with `0.1.2` on the `0.1.0-alpha.2` and `0.1.0-alpha.1` servers, each time with the test of the server's own release, which always sets a Virgil root. `0.1.0-alpha.3`'s test, which also starts the server without a root, failed only there with `0.1.1` on the `0.1.0-alpha.2` server ([results](manual-testing.md#010-alpha3)).
