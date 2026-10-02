# Neovim / LazyVim

> **Not usable yet.** `virgil-lsp --stdio` handles the LSP lifecycle (`initialize`, `shutdown`, `exit`) and versioned full-text document synchronization, but not diagnostics or other language features yet. Useful language features arrive later in milestone M2. These files describe the intended setup and will be tested by headless Neovim end-to-end tests.

Requires Neovim 0.11 or later. No editor plugin is needed: Neovim's built-in client talks to `virgil-lsp`.

## LazyVim

Copy [`lazyvim/virgil.lua`](lazyvim/virgil.lua) to `~/.config/nvim/lua/plugins/virgil.lua`, and make sure `virgil-lsp` is on your `PATH`, or edit `cmd` to give an absolute path.

## Plain Neovim

Add the contents of [`virgil_lsp.lua`](virgil_lsp.lua) to your `init.lua`. To reproduce a problem without your own configuration:

```sh
nvim --clean -u editors/nvim/virgil_lsp.lua path/to/file.v3
```

## Troubleshooting

- `:checkhealth vim.lsp` shows whether the client attached.
- `:LspInfo` (with nvim-lspconfig) or `:lua =vim.lsp.get_clients()` lists running clients.
- `:LspLog` opens the client log. Server logs are written to stderr and appear there.
