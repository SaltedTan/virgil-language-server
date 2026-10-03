# Neovim / LazyVim

> **Development setup.** See the [project status](../../README.md) for current server features. The plain Neovim configuration is smoke-tested headlessly in CI; the LazyVim configuration is **untested**.

Requires Neovim 0.11 or later. No editor plugin is needed: Neovim's built-in client talks to `virgil-lsp`.

## LazyVim

Copy [`lazyvim/virgil.lua`](lazyvim/virgil.lua) to `~/.config/nvim/lua/plugins/virgil.lua`, and make sure `virgil-lsp` is on your `PATH`, or edit `cmd` to give an absolute path.

## Plain Neovim

Add the contents of [`virgil_lsp.lua`](virgil_lsp.lua) to your `init.lua`. To reproduce a problem without your own configuration:

```sh
nvim --clean -u editors/nvim/virgil_lsp.lua path/to/file.v3
```

## Headless smoke test

From the repository root, run `make test-nvim` (also included in `make test`).
The test uses the plain configuration above with the built server on `PATH`
and `--headless --clean -n` (no swap files). It checks client attachment, parser
diagnostics published and cleared after an unsaved edit, document symbols, and
a graceful server shutdown with exit status 0. Fixtures and editor state are
copied into a temporary directory and removed afterward.

The test skips with a message if Neovim is missing or older than 0.11. Set
`REQUIRE_NVIM=1` to fail instead of skipping, as both CI jobs do. See the
[tested editor versions](../../docs/compatibility.md#editors).

## Troubleshooting

- `:checkhealth vim.lsp` shows whether the client attached.
- `:LspInfo` (with nvim-lspconfig) or `:lua =vim.lsp.get_clients()` lists running clients.
- `:LspLog` opens the client log. Server logs are written to stderr and appear there.
