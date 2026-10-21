# Manual testing

The automated editor tests run the plain Neovim configuration on Linux and macOS, and VS Code on Linux. This page lists what they cover, the editor and platform combinations to try by hand before a release, how to check each feature, and the results for each release.

## What the automated tests cover

Both editor tests use the same fixture, [`test/e2e/nvim/fixture`](../test/e2e/nvim/fixture/), copied into a temporary directory.

| Test | Command | Where it runs | What it checks |
| --- | --- | --- | --- |
| [Headless Neovim test](../editors/nvim/README.md#headless-smoke-test) | `make test-nvim`, also part of `make test` | CI jobs `test` (`ubuntu-24.04`, Neovim 0.11.6) and `test-macos` (`macos-15`, Apple Silicon under Rosetta 2, Homebrew Neovim), and both build jobs of the [release workflow](releasing.md#how-the-workflow-works) | See the [plain-configuration smoke-test coverage](../editors/nvim/README.md#headless-smoke-test). |
| [Extension Host test](../clients/vscode/README.md#extension-host-test) | `npm run test:e2e` in `clients/vscode`; not part of `make test` | CI job `vscode-extension-host` (`ubuntu-24.04`, VS Code 1.142.0), on the extension from source and on a VSIX it packages and installs. The release workflow runs it on the VSIX it attaches ([#117](https://github.com/SaltedTan/virgil-language-server/issues/117)). | See the [Extension Host checks](../clients/vscode/README.md#extension-host-test), including installed-VSIX mode. |
| [Lifecycle tests](../clients/vscode/README.md#development) | `npm test` in `clients/vscode` | CI job `vscode-client` (`ubuntu-24.04`) | Client startup, shutdown, restart, and recovery, the Virgil root settings, and the documents and files sent to the server, with a simulated extension host and language client |

The editor tests don't cover:

- VS Code on macOS, Remote-SSH, or Remote-WSL. Nothing runs inside WSL 2.
- The LazyVim configuration.
- Configuration changes other than an unsaved edit to the project file: changes on disk, a missing Virgil root, and changed settings. The lifecycle tests check the extension's side of a settings change only against a simulated extension host.
- Restarting the server from Neovim.
- What the editor shows. The tests read diagnostics, symbols, locations, hover text, signature help, and completion items through editor APIs, not from the Problems view, the outline, a hover popup, a signature popup, or a completion menu.

## Matrix

Try each setup with each feature: diagnostics, document symbols, definition, hover, signature help, references, workspace symbols, rename, completion, configuration changes, and restart.

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

Follow the [manual checklist](../clients/vscode/README.md#manual-checklist), which covers local Linux, Remote-SSH, Remote-WSL, and macOS. Its steps check the features as follows: diagnostics in steps 3, 4, 6, and 7; document symbols in step 5; definition, hover, signature help, references, and workspace symbols in step 7; rename in step 8; completion in step 9; configuration changes in steps 10 to 12; and restart in step 13. Steps 1, 2, and 14 to 17 check startup, startup errors, the message trace, and recovery.

### Neovim and LazyVim

Set `VIRGIL_LOC` to the checkout's `vendor/virgil`, change to `/tmp/virgil-check`, and start Neovim there. Neovim 0.11 doesn't show diagnostics as virtual text by default; `:lua vim.diagnostic.setloclist()` lists them.

1. **Diagnostics.** Open `bad.v3`. It has one error, "invalid start of expression", at line 3, column 27. Without saving, replace line 3 with `component C { def value = 1; }`; the error disappears. Undo the edit; it comes back. Open `words/Words.v3`. After a moment, it has one error, "expected int in var initialization, got Array<byte>", on line 7.
2. **Document symbols.** In `words/Words.v3`, `gO` (or `:lua vim.lsp.buf.document_symbol()`) lists the component `Words` and its method `join`.
3. **Definition.** On `StringBuilder` in line 7, `CTRL-]` (`gd` in LazyVim, or `:lua vim.lsp.buf.definition()`) opens Virgil's `lib/util/StringBuilder.v3` at `class StringBuilder`, on line 19.
4. **Hover.** On the same `StringBuilder`, `K` shows `class StringBuilder`.
5. **Signature help.** In `words/Words.v3`, put the cursor just after the `(` of `.puts(a)` in line 7 and, in Insert mode, press `CTRL-S` (Neovim 0.11's default mapping; in LazyVim, `CTRL-S` saves the file, so press `CTRL-K`), or run `:lua vim.lsp.buf.signature_help()` (`gK` in LazyVim) from Normal mode. A window shows `def puts(s: Array<byte>) -> this`, with `s: Array<byte>` highlighted. Just past the `)`, it shows nothing. Plain Neovim doesn't open the window as you type `(` or `,` without a plugin or an `LspAttach` autocommand that calls it; LazyVim's noice.nvim does ([Signature help](architecture.md#signature-help)).
6. **References.** Return to `words/Words.v3`, since the library file that step 3 opened belongs to another server without the fixture's project. On `StringBuilder` in line 7, run `:lua vim.lsp.buf.references()`. The list includes this use, the class's name in `lib/util/StringBuilder.v3`, and its uses in the other `lib/util` files that the fixture's project includes ([References](architecture.md#references)).
7. **Workspace symbols.** Back in `words/Words.v3`, run `:lua vim.lsp.buf.workspace_symbol("join")`. The list shows the method `join` in `words/Words.v3`, on line 6, although nothing uses it ([Workspace symbols](architecture.md#workspace-symbols)).
8. **Rename.** In `words/Words.v3`, with the cursor on `line` in line 7, run `:lua vim.lsp.buf.rename("text")`. Neovim shows the server's reason, "the project's analysis has errors; rename needs a program that verifies". Without saving, change line 7 to `var line = StringBuilder.new().puts(a).putc(' ').puts(b).toString();` and line 8 to `return line.length;`, and run it again: both uses become `text`. `u` makes them `line` again, and `u` once more brings back the type error ([Rename](architecture.md#rename)).
9. **Completion.** In `words/Words.v3`, press `o` on line 7 to open a line below it. In plain Neovim, press `CTRL-X CTRL-O`: after a moment, the menu lists the names in scope first, `line`, `a`, `b`, `this`, and `join`, then the library's types and the builtin types, and last the keywords that start a statement, from `break` to `while`. Press `CTRL-E` to close it, type `whi`, and press `CTRL-X CTRL-O` again: the menu lists only `while`, which `CTRL-Y` accepts. In LazyVim, the menu appears as you type `w` with no key, along with words from the buffer, and narrows as you type `whi`; `Enter` accepts `while`. The line reads `while`. Press `Esc` and `u`: the line is gone and the buffer is unmodified. Open the line again and complete `li` the same way: `line` comes first, as a variable with `var line: int`, and accepting it makes the line read `line`; `Esc` and `u` remove the line again. Open the line again, type `a.`, and complete the same way (LazyVim shows the menu as you type the `.`): it lists only `length`, a field with `def length: int`, since `a` is a string, and accepting it makes the line read `a.length`; `Esc` and `u` remove the line again. Inside the comment on line 3, `CTRL-X CTRL-O` (or typing a word or a `.` in LazyVim) offers nothing from the server ([Completion](architecture.md#completion), [Member completion](architecture.md#member-completion), [the Neovim configuration](../editors/nvim/README.md#completion)).
10. **Configuration changes.** Open `.virgil-lsp.json` and, without saving, add `"bogus": 1,` after the opening brace. An `UnknownField` error from `virgil-lsp` appears on that line. Undo the edit; the error disappears. Close the file, and add the same line on disk from a terminal. Whether the error appears at once depends on whether Neovim declares file watching, which `:lua =vim.lsp.protocol.make_client_capabilities().workspace.didChangeWatchedFiles` shows ([Changes on disk](../editors/nvim/README.md#changes-on-disk)). On macOS, Neovim 0.11 declares it, the server registers its watchers, and the error appears without further action. On Linux and in WSL 2, it doesn't, and the error appears when a `.v3` buffer is next opened, changed, or saved. Remove the line again. Then quit, start Neovim with `VIRGIL_LOC` unset, and open `.virgil-lsp.json`: Neovim shows the warning "Invalid project file …" (LazyVim doesn't, unless noice's LSP message handler is turned off; see [LazyVim](../editors/nvim/README.md#lazyvim)), and the file has a `MissingVirgilRoot` error on the `virgilDependencies` line. Quit again.
11. **Restart.** Start Neovim with `VIRGIL_LOC` set again, open `words/Words.v3`, and wait for its type error on line 7. In LazyVim, run `:LspRestart`. In plain Neovim, run `:lua vim.lsp.stop_client(vim.lsp.get_clients({ name = "virgil_lsp" }))`, then `:edit`. A new server attaches, and `words/Words.v3` gets its error again. If the old server exited with a nonzero status, Neovim warns "Client virgil_lsp quit with exit code …".

### Collecting logs

- **VS Code:** the "Virgil Language Server" output channel holds the extension's startup messages and the server's stderr. To add the LSP messages, set the channel's log level to Trace ([Settings](../clients/vscode/README.md#settings)).
- **Neovim:** the server's stderr goes to Neovim's LSP log, as `ERROR` lines marked `"stderr"`. `:LspLog` opens the log with nvim-lspconfig, which LazyVim includes; otherwise, `:lua =vim.lsp.log.get_filename()` prints its path. To add the LSP messages, run `:lua vim.lsp.log.set_level("debug")` before reproducing the problem.

Report a failed check with the [bug report form](https://github.com/SaltedTan/virgil-language-server/issues/new?template=bug_report.yml), after checking the [known limitations](known-limitations.md).

## Results

For each release, record every run with its date, the server's source revision, and the editor's version. Mark a feature *Pass*, or *Fail* with an issue link, only after checking it, and leave the rest *Not yet run*. A passing automated run counts for the features it checks.

### 0.1.0-alpha.1

The first six runs are at `8b2ea9f`, on `main` before hover ([#106](https://github.com/SaltedTan/virgil-language-server/issues/106)), references ([#123](https://github.com/SaltedTan/virgil-language-server/issues/123)), workspace symbols ([#122](https://github.com/SaltedTan/virgil-language-server/issues/122)), signature help ([#127](https://github.com/SaltedTan/virgil-language-server/issues/127)), and the release VSIX ([#117](https://github.com/SaltedTan/virgil-language-server/issues/117)) were merged, so they don't check those four features. The later runs are at `2be8831`, the release preparation commit (the pull request's CI checks out its merge with `main`, `f78d572`, which has the same tree), and the LazyVim run at the final branch's dry run: its pull request's CI, the branch's [dry run](releasing.md#dry-runs), and local runs with that dry run's Linux archive and VSIX. The matrix records what they check; the manual tests with the same archives and VSIX fill in the rest.

| Date | Commit | Where | Command | Editor | Result |
| --- | --- | --- | --- | --- | --- |
| 2026-10-17 | `8b2ea9f` | CI job `test`, `ubuntu-24.04`, [run 39027596224](https://github.com/SaltedTan/virgil-language-server/actions/runs/39027596224) | `make test` | Neovim 0.11.6 | Pass |
| 2026-10-17 | `8b2ea9f` | CI job `test-macos`, `macos-15`, same run | `make test` | Neovim 0.12.5 | Pass |
| 2026-10-17 | `8b2ea9f` | CI job `vscode-extension-host`, `ubuntu-24.04`, same run | `xvfb-run -a npm run test:e2e`, then on the packaged VSIX with `--vsix` | VS Code 1.142.0 | Pass |
| 2026-10-18 | `8b2ea9f` | Ubuntu 26.04.1, x86-64 | `REQUIRE_NVIM=1 make test-nvim` | Neovim 0.11.6 | Pass |
| 2026-10-18 | `8b2ea9f` | Ubuntu 26.04.1, x86-64, under Xvfb | `xvfb-run -a npm run test:e2e` | VS Code 1.142.0, extension from source | Pass |
| 2026-10-18 | `8b2ea9f` | Ubuntu 26.04.1, x86-64, under Xvfb | `npm run package`, then `xvfb-run -a npm run test:e2e -- --vsix virgil-lsp-vscode-0.1.0.vsix` | VS Code 1.142.0, extension 0.1.0 installed from the VSIX | Pass |
| 2026-10-20 | `f78d572`, the pull request's merge of `2be8831`, same tree | CI job `test`, `ubuntu-24.04`, [run 39407691447](https://github.com/SaltedTan/virgil-language-server/actions/runs/39407691447) | `make test` | Neovim 0.11.6 | Pass |
| 2026-10-20 | `f78d572`, the pull request's merge of `2be8831`, same tree | CI job `test-macos`, `macos-15`, same run | `make test` | Neovim 0.12.5 | Pass |
| 2026-10-20 | `f78d572`, the pull request's merge of `2be8831`, same tree | CI job `vscode-extension-host`, `ubuntu-24.04`, same run | `xvfb-run -a npm run test:e2e`, then on the packaged VSIX with `--vsix` | VS Code 1.142.0 | Pass |
| 2026-10-20 | `2be8831` | Release dry run, job "Package and test the VS Code extension", `ubuntu-24.04`, [run 39408586780](https://github.com/SaltedTan/virgil-language-server/actions/runs/39408586780) | The Extension Host test on the packaged VSIX, with the server from the run's Linux archive | VS Code 1.142.0, extension 0.1.0 installed from the VSIX | Pass |
| 2026-10-20 | `2be8831` | Ubuntu 26.04.1, x86-64, server from the dry run's Linux archive | `REQUIRE_NVIM=1 test/e2e/nvim/run.sh <archive>/virgil-lsp` | Neovim 0.11.6 | Pass |
| 2026-10-20 | `2be8831` | Ubuntu 26.04.1, x86-64, under Xvfb, server from the dry run's Linux archive | `xvfb-run -a npm run test:e2e -- --vsix virgil-lsp-vscode-0.1.0.vsix <archive>/virgil-lsp` | VS Code 1.142.0, extension 0.1.0 installed from the dry run's VSIX | Pass |
| 2026-10-20 | `5cd321e`, the release dry run's merge of `cbd871a`, same tree | Ubuntu 26.04.1, x86-64, in a detached tmux session, server from [dry run 39415504970](https://github.com/SaltedTan/virgil-language-server/actions/runs/39415504970)'s Linux archive | A scripted run of the procedure's Neovim steps under the [LazyVim starter](https://github.com/LazyVim/starter) at `803bc18` with `editors/nvim/lazyvim/virgil.lua`, through LazyVim's own client and keys (`gd`, `K`, `<C-k>`, `gK`, `:LspRestart`) | Neovim 0.11.6, LazyVim 16.0.1, nvim-lspconfig 2.12.0-29 | Pass |

WSL 2 is best effort, and none of its setups were tested for this alpha.

| Setup | Diagnostics | Document symbols | Definition | Hover | Configuration changes | Restart | References | Workspace symbols | Signature help |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| VS Code, Linux | Pass (automated) | Pass (automated) | Pass (automated) | Pass (automated) | Unsaved project-file edit only (automated) | Pass (automated) | Pass (automated) | Pass (automated) | Pass (automated) |
| VS Code, macOS | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| VS Code Remote-SSH | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| VS Code Remote-WSL | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) |
| Neovim, Linux | Pass (automated) | Pass (automated) | Pass (automated) | Pass (automated) | Unsaved project-file edit only (automated) | Not yet run | Pass (automated) | Pass (automated) | Pass (automated) |
| Neovim, macOS | Pass (automated) | Pass (automated) | Pass (automated) | Pass (automated) | Unsaved project-file edit only (automated) | Not yet run | Pass (automated) | Pass (automated) | Pass (automated) |
| Neovim, WSL 2 | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) |
| LazyVim, Linux | Pass | Pass | Pass | Pass | Pass; noice.nvim hides the missing-root warning, not its diagnostic | Pass | Pass | Pass | Pass |
| LazyVim, macOS | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| LazyVim, WSL 2 | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) |
