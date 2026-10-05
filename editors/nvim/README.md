# Neovim / LazyVim

> **Development setup.** See the [project status](../../README.md) for current server features. The plain Neovim configuration is smoke-tested headlessly in CI; the LazyVim configuration is **untested**.

Requires Neovim 0.11 or later. No editor plugin is needed: Neovim's built-in client talks to `virgil-lsp`.

## LazyVim

Copy [`lazyvim/virgil.lua`](lazyvim/virgil.lua) to `~/.config/nvim/lua/plugins/virgil.lua`, and make sure `virgil-lsp` is on your `PATH`, or edit `cmd` to give an absolute path. Set `VIRGIL_LOC` as described under [The Virgil root](#the-virgil-root).

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

## Headless smoke test

From the repository root, run `make test-nvim` (also included in `make test`).
The test uses the plain configuration above with the built server on `PATH`
and `--headless --clean -n` (no swap files), with `VIRGIL_LOC` set to the pinned
checkout in `vendor/virgil`. It checks client attachment, parser diagnostics
published and cleared after an unsaved edit, document symbols, semantic
diagnostics for the fixture's project, which uses `lib/util` from the Virgil
root, configuration diagnostics after an unsaved edit to the project file, that
other JSON buffers stay detached, and a graceful server shutdown with exit
status 0. The fixture is copied into a temporary directory; editor state is kept
there, and the directory is removed afterward.

The test skips with a message if Neovim is missing or older than 0.11. Set
`REQUIRE_NVIM=1` to fail instead of skipping, as both CI jobs do. See the
[tested editor versions](../../docs/compatibility.md#editors).

## Troubleshooting

- `:checkhealth vim.lsp` shows whether the client attached.
- `:LspInfo` (with nvim-lspconfig) or `:lua =vim.lsp.get_clients()` lists running clients.
- `:LspLog` opens the client log. Server logs are written to stderr and appear there.
