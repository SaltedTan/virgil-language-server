# Manual testing

The automated editor tests run the plain Neovim configuration on Linux and macOS, and VS Code on Linux. This page lists what they cover, the editor and platform combinations to try by hand before a release, how to check each feature, and the results for each release.

## What the automated tests cover

Both editor tests use the same fixture, [`test/e2e/nvim/fixture`](../test/e2e/nvim/fixture/), copied into a temporary directory.

| Test | Command | Where it runs | What it checks |
| --- | --- | --- | --- |
| [Headless Neovim test](../editors/nvim/README.md#headless-smoke-test) | `make test-nvim`, also part of `make test` | CI jobs `test` (`ubuntu-24.04`, Neovim 0.11.6) and `test-macos` (`macos-15`, Apple Silicon under Rosetta 2, Homebrew Neovim), and both build jobs of the [release workflow](releasing.md#how-the-workflow-works) | The plain configuration: attachment, parser diagnostics published and cleared after an unsaved edit, document symbols, semantic diagnostics that need `lib/util` from the Virgil root, a definition in that closed library file, hover over the same use, configuration diagnostics after an unsaved edit to the project file, other JSON buffers left detached, and a clean server exit |
| [Extension Host test](../clients/vscode/README.md#extension-host-test) | `npm run test:e2e` in `clients/vscode`; not part of `make test` | CI job `vscode-extension-host` (`ubuntu-24.04`, VS Code 1.142.0), on the extension from source and on a VSIX it packages and installs. The release workflow runs it on the VSIX it attaches ([#117](https://github.com/SaltedTan/virgil-language-server/issues/117)). | Activation on a `.v3` file and on a project file, the Virgil root named in the output channel, parser diagnostics after an unsaved edit and its undo, document symbols, a semantic diagnostic, a definition and hover in the closed library file, configuration diagnostics after an unsaved edit to the project file, and **Virgil: Restart Language Server** with a clean server exit |
| [Lifecycle tests](../clients/vscode/README.md#development) | `npm test` in `clients/vscode` | CI job `vscode-client` (`ubuntu-24.04`) | Client startup, shutdown, restart, and recovery, the Virgil root settings, and the documents and files sent to the server, with a simulated extension host and language client |

The editor tests don't cover:

- VS Code on macOS, Remote-SSH, or Remote-WSL. Nothing runs inside WSL 2.
- The LazyVim configuration.
- Configuration changes other than an unsaved edit to the project file: changes on disk, a missing Virgil root, and changed settings. The lifecycle tests check the extension's side of a settings change only against a simulated extension host.
- Restarting the server from Neovim.
- What the editor shows. The tests read diagnostics, symbols, locations, and hover text through editor APIs, not from the Problems view, the outline, or a hover popup.

## Matrix

Try each setup with each feature: diagnostics, document symbols, definition, hover, configuration changes, and restart.

| Setup | The server runs on | Archive | Automated coverage |
| --- | --- | --- | --- |
| VS Code | Linux x86-64 | `x86-64-linux` | Extension Host test |
| VS Code | macOS on Apple Silicon, under Rosetta 2 | `x86-64-darwin` | None |
| VS Code Remote-SSH, from any desktop | Linux x86-64 | `x86-64-linux` | None |
| VS Code Remote-WSL, from Windows | WSL 2 | `x86-64-linux` | None |
| Neovim, plain configuration | Linux x86-64, locally or over SSH | `x86-64-linux` | Headless Neovim test |
| Neovim, plain configuration | macOS on Apple Silicon, under Rosetta 2 | `x86-64-darwin` | Headless Neovim test |
| Neovim, plain configuration | WSL 2 | `x86-64-linux` | None |
| LazyVim | Linux x86-64, macOS, or WSL 2 | As for Neovim | None |

[Where the server runs](compatibility.md#where-the-server-runs) explains the remote setups.

## Procedure

### Before you start

1. Use the release candidate: the archives and VSIX from a [dry run](releasing.md#dry-runs) of the release preparation branch, or from the published prerelease. Install the server on the machine that holds the files ([Installing](releasing.md#installing)), then the extension or the Neovim configuration.
2. On the same machine, clone the repository at the same commit, with submodules; for WSL 2, clone inside WSL, so that files keep LF line endings. Its `vendor/virgil` is a Virgil checkout at the server's Virgil revision: use it as the Virgil root.
3. Copy the fixture, so that edits don't touch the checkout: `cp -R test/e2e/nvim/fixture /tmp/virgil-check`. Its `bad.v3` has a syntax error on line 3. Its project file defines a project `words`, which uses Virgil's `lib/util`.
4. Record `virgil-lsp --version`, the editor's version, the extension's version, and the platform.

### VS Code

Follow the [manual checklist](../clients/vscode/README.md#manual-checklist), which covers local Linux, Remote-SSH, Remote-WSL, and macOS. Its steps check the features as follows: diagnostics in steps 3, 4, 6, and 7; document symbols in step 5; definition and hover in step 7; configuration changes in steps 8 to 10; and restart in step 11. Steps 1, 2, and 12 to 15 check startup, startup errors, the message trace, and recovery.

### Neovim and LazyVim

Set `VIRGIL_LOC` to the checkout's `vendor/virgil`, change to `/tmp/virgil-check`, and start Neovim there. Neovim 0.11 doesn't show diagnostics as virtual text by default; `:lua vim.diagnostic.setloclist()` lists them.

1. **Diagnostics.** Open `bad.v3`. It has one error, "invalid start of expression", at line 3, column 27. Without saving, replace line 3 with `component C { def value = 1; }`; the error disappears. Undo the edit; it comes back. Open `words/Words.v3`. After a moment, it has one error, "expected int in var initialization, got Array<byte>", on line 7.
2. **Document symbols.** In `words/Words.v3`, `gO` (or `:lua vim.lsp.buf.document_symbol()`) lists the component `Words` and its method `join`.
3. **Definition.** On `StringBuilder` in line 7, `CTRL-]` (`gd` in LazyVim, or `:lua vim.lsp.buf.definition()`) opens Virgil's `lib/util/StringBuilder.v3` at `class StringBuilder`, on line 19.
4. **Hover.** On the same `StringBuilder`, `K` shows `class StringBuilder`.
5. **Configuration changes.** Open `.virgil-lsp.json` and, without saving, add `"bogus": 1,` after the opening brace. An `UnknownField` error from `virgil-lsp` appears on that line. Undo the edit; the error disappears. Close the file, and add the same line on disk from a terminal. Neovim sends no file events, so the error appears when a `.v3` buffer is next opened, changed, or saved ([Changes on disk](../editors/nvim/README.md#changes-on-disk)). Remove the line again. Then quit, start Neovim with `VIRGIL_LOC` unset, and open `.virgil-lsp.json`: Neovim shows the warning "Invalid project file …", and the file has a `MissingVirgilRoot` error on the `virgilDependencies` line.
6. **Restart.** In LazyVim, run `:LspRestart`. In plain Neovim, run `:lua vim.lsp.stop_client(vim.lsp.get_clients({ name = "virgil_lsp" }))`, then `:edit`. A new server attaches, and `words/Words.v3` gets its error again. If the old server exited with a nonzero status, Neovim warns "Client virgil_lsp quit with exit code …".

### Collecting logs

- **VS Code:** the "Virgil Language Server" output channel holds the extension's startup messages and the server's stderr. To add the LSP messages, set the channel's log level to Trace ([Settings](../clients/vscode/README.md#settings)).
- **Neovim:** the server's stderr goes to Neovim's LSP log, as `ERROR` lines marked `"stderr"`. `:LspLog` opens the log with nvim-lspconfig, which LazyVim includes; otherwise, `:lua =vim.lsp.log.get_filename()` prints its path. To add the LSP messages, run `:lua vim.lsp.log.set_level("debug")` before reproducing the problem.

Report a failed check with the [bug report form](https://github.com/SaltedTan/virgil-language-server/issues/new?template=bug_report.yml), after checking the [known limitations](known-limitations.md).

## Results

For each release, record every run with its date, the server's source revision, and the editor's version. Mark a feature *Pass*, or *Fail* with an issue link, only after checking it, and leave the rest *Not yet run*. A passing automated run counts for the features it checks.

### 0.1.0-alpha.1

These runs are at `8b2ea9f`, on `main` before hover ([#106](https://github.com/SaltedTan/virgil-language-server/issues/106)) and the release VSIX ([#117](https://github.com/SaltedTan/virgil-language-server/issues/117)) were merged, so they don't check hover. Run them again at the release commit.

| Date | Commit | Where | Command | Editor | Result |
| --- | --- | --- | --- | --- | --- |
| 2026-10-17 | `8b2ea9f` | CI job `test`, `ubuntu-24.04`, [run 39027596224](https://github.com/SaltedTan/virgil-language-server/actions/runs/39027596224) | `make test` | Neovim 0.11.6 | Pass |
| 2026-10-17 | `8b2ea9f` | CI job `test-macos`, `macos-15`, same run | `make test` | Neovim 0.12.5 | Pass |
| 2026-10-17 | `8b2ea9f` | CI job `vscode-extension-host`, `ubuntu-24.04`, same run | `xvfb-run -a npm run test:e2e`, then on the packaged VSIX with `--vsix` | VS Code 1.142.0 | Pass |
| 2026-10-18 | `8b2ea9f` | Ubuntu 26.04.1, x86-64 | `REQUIRE_NVIM=1 make test-nvim` | Neovim 0.11.6 | Pass |
| 2026-10-18 | `8b2ea9f` | Ubuntu 26.04.1, x86-64, under Xvfb | `xvfb-run -a npm run test:e2e` | VS Code 1.142.0, extension from source | Pass |
| 2026-10-18 | `8b2ea9f` | Ubuntu 26.04.1, x86-64, under Xvfb | `npm run package`, then `xvfb-run -a npm run test:e2e -- --vsix virgil-lsp-vscode-0.1.0.vsix` | VS Code 1.142.0, extension 0.1.0 installed from the VSIX | Pass |

| Setup | Diagnostics | Document symbols | Definition | Hover | Configuration changes | Restart |
| --- | --- | --- | --- | --- | --- | --- |
| VS Code, Linux | Pass (automated) | Pass (automated) | Pass (automated) | Not yet run | Unsaved project-file edit only (automated) | Pass (automated) |
| VS Code, macOS | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| VS Code Remote-SSH | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| VS Code Remote-WSL | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| Neovim, Linux | Pass (automated) | Pass (automated) | Pass (automated) | Not yet run | Unsaved project-file edit only (automated) | Not yet run |
| Neovim, macOS | Pass (automated) | Pass (automated) | Pass (automated) | Not yet run | Unsaved project-file edit only (automated) | Not yet run |
| Neovim, WSL 2 | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| LazyVim, Linux | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| LazyVim, macOS | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| LazyVim, WSL 2 | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
