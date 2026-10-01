-- Minimal Neovim (0.11+) configuration for the Virgil language server.
-- Usage: nvim --clean -u editors/nvim/virgil_lsp.lua file.v3

vim.filetype.add({ extension = { v3 = "virgil" } })

vim.lsp.config("virgil_lsp", {
  cmd = { "virgil-lsp", "--stdio" },
  filetypes = { "virgil" },
  root_markers = { ".virgil-lsp.json", "DEPS", ".git" },
})

vim.lsp.enable("virgil_lsp")
