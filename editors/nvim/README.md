# Neovim / LazyVim

> **Development setup.** See the [project status](../../README.md) for current server features. The plain Neovim configuration is smoke-tested headlessly in CI. The LazyVim configuration isn't tested in CI; a scripted run on Linux passed for 0.1.0-alpha.1 ([results](../../docs/manual-testing.md#results)).

Requires Neovim 0.11 or later. No editor plugin is needed: Neovim's built-in client talks to `virgil-lsp`.

## LazyVim

Copy [`lazyvim/virgil.lua`](lazyvim/virgil.lua) to `~/.config/nvim/lua/plugins/virgil.lua`, and make sure `virgil-lsp` is on your `PATH`, or edit `cmd` to give an absolute path. Set `VIRGIL_LOC` as described under [The Virgil root](#the-virgil-root).

LazyVim's keys reach the server: `gd`, `K`, `gr`, and, for signature help, `gK` in Normal mode or `<C-k>` in Insert mode; noice.nvim also opens signature help as you type `(` or `,`. In LazyVim, `<C-s>` saves the file instead of showing signature help.

LazyVim's noice.nvim drops `window/showMessage` notifications from every language server ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)), so warnings such as "Invalid project file …" don't appear, although the project file's diagnostics do. To see them, turn off noice's LSP message handler in a plugin spec, so that Neovim's own handler shows them:

```lua
{ "folke/noice.nvim", opts = { lsp = { message = { enabled = false } } } },
```

## Plain Neovim

Add the contents of [`virgil_lsp.lua`](virgil_lsp.lua) to your `init.lua`. To reproduce a problem without your own configuration:

```sh
nvim --clean -u editors/nvim/virgil_lsp.lua path/to/file.v3
```

## The Virgil root

Project files name Virgil's own libraries relative to a Virgil checkout or installation, as in `"virgilDependencies": ["lib/util/*.v3"]` ([Configuration](../../docs/configuration.md#the-virgil-root)). Both configurations send its location as `init_options = { virgilRoot = vim.env.VIRGIL_LOC }`, so set `VIRGIL_LOC` before starting Neovim to the absolute physical path of the directory that holds `lib/`:

```sh
export VIRGIL_LOC=$HOME/virgil
```

Or replace `vim.env.VIRGIL_LOC` with the path. Without a root, a project file with `virgilDependencies` gets a `MissingVirgilRoot` diagnostic, and its documents stay in single-file mode. `:lua =vim.lsp.get_clients({ name = "virgil_lsp" })[1].config.init_options` shows what was sent. The option is read when the server starts: after changing it, restart Neovim, or run `:LspRestart` with nvim-lspconfig.

## Project files

Both configurations also attach the server to `.virgil-lsp.json` buffers, so configuration diagnostics follow unsaved edits. That is why `filetypes` includes `json`: the `root_dir` function skips every other JSON buffer, so the server never sees other JSON files.

## Changes on disk

The server notices changes made outside the editor, such as a checkout, only from `workspace/didChangeWatchedFiles` file events ([changes on disk](../../docs/configuration.md#changes-on-disk)). It asks for them when the client declares `workspace.didChangeWatchedFiles.dynamicRegistration`. Neovim 0.11.6 on Linux declares it false (`:lua =vim.lsp.protocol.make_client_capabilities().workspace.didChangeWatchedFiles`), so with these configurations the server refreshes projects when they are used instead: a project file fixed on disk is noticed when a `.v3` buffer is next opened, changed, or saved, and a source created or deleted outside Neovim when a project file changes. Restart the server (`:LspRestart` with nvim-lspconfig) after a checkout that adds or removes files.

`vim.lsp.buf.add_workspace_folder()` and `vim.lsp.buf.remove_workspace_folder()` send `workspace/didChangeWorkspaceFolders`, which the server handles.

## Rename

`vim.lsp.buf.rename()` (`grn`) asks the server first whether the name under the cursor can be renamed, and then for the edit, which it applies to the open buffers and the closed files it names ([Rename](../../docs/architecture.md#rename)). The server sends versioned `documentChanges`, and refuses to rename for a client that does not declare them. Neovim 0.11 applies them but does not declare `workspace.workspaceEdit.documentChanges`, so both configurations add it to `capabilities`; `:lua =vim.lsp.get_clients({ name = "virgil_lsp" })[1].capabilities.workspace.workspaceEdit` shows it. Neovim checks the version of only the first document of an edit. A refused rename shows the server's reason, such as "rename needs a program that verifies" while the project has errors.

## Headless smoke test

From the repository root, run `make test-nvim` (also included in `make test`).
The test uses the plain configuration above with the built server on `PATH` and
`--headless --clean -n` (no swap files), with `VIRGIL_LOC` set to the pinned
checkout in `vendor/virgil`. It checks client attachment, parser diagnostics
published and cleared after an unsaved edit, document symbols, including a
variant case nested in its variant, semantic diagnostics for the fixture's
project, which uses `lib/util` from the Virgil root, a definition in that
closed library file, a hover over the same use, signature help in a call on the
same line, its references in that file and other closed library files, a rename
of a local through `vim.lsp.buf.rename()` once an unsaved edit fixes the type
error, and its undo, a workspace symbol that nothing uses, configuration
diagnostics after an unsaved edit to the project file, that other JSON buffers
stay detached, and a graceful server shutdown with exit status 0. The fixture
is copied into a temporary directory; editor state is kept there, and the
directory is removed afterward. The VS Code
[Extension Host test](../../clients/vscode/README.md#extension-host-test) uses
the same fixture, so a change to it affects both editors' checks.

The test skips with a message if Neovim is missing or older than 0.11. Set
`REQUIRE_NVIM=1` to fail instead of skipping, as both CI jobs do. See the
[tested editor versions](../../docs/compatibility.md#editors).

## Troubleshooting

- `:checkhealth vim.lsp` shows whether the client attached.
- `:LspInfo` (with nvim-lspconfig) or `:lua =vim.lsp.get_clients()` lists running clients.
- `:LspLog` (with nvim-lspconfig) opens the client log; without it, `:lua =vim.lsp.log.get_filename()` prints the log's path. Server logs are written to stderr and appear there, as lines marked `"stderr"`.
