-- LazyVim plugin spec for the Virgil language server.
-- Copy to ~/.config/nvim/lua/plugins/virgil.lua
vim.filetype.add({ extension = { v3 = "virgil" } })

return {
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        virgil_lsp = {
          -- virgil-lsp is not in mason; install it yourself and put it on PATH.
          mason = false,
          cmd = { "virgil-lsp", "--stdio" },
          filetypes = { "virgil" },
          root_markers = { ".virgil-lsp.json", "DEPS", ".git" },
        },
      },
    },
  },
}
