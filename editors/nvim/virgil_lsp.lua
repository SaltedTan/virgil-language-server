-- Minimal Neovim (0.11+) configuration for the Virgil language server.
-- Usage: nvim --clean -u editors/nvim/virgil_lsp.lua file.v3

vim.filetype.add({ extension = { v3 = "virgil" } })

local root_markers = { ".virgil-lsp.json", "DEPS", ".git" }

vim.lsp.config("virgil_lsp", {
  cmd = { "virgil-lsp", "--stdio" },
  filetypes = { "virgil", "json" },
  -- Of the JSON files, only project files go to the server, so that it sees
  -- their unsaved edits.
  root_dir = function(bufnr, on_dir)
    if vim.bo[bufnr].filetype == "json"
      and vim.fs.basename(vim.api.nvim_buf_get_name(bufnr)) ~= ".virgil-lsp.json" then
      return
    end
    on_dir(vim.fs.root(bufnr, root_markers))
  end,
  -- The Virgil checkout that project files' virgilDependencies are relative to.
  init_options = { virgilRoot = vim.env.VIRGIL_LOC },
})

vim.lsp.enable("virgil_lsp")
