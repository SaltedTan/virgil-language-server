-- LazyVim plugin spec for the Virgil language server.
-- Copy to ~/.config/nvim/lua/plugins/virgil.lua
vim.filetype.add({ extension = { v3 = "virgil" } })

local root_markers = { ".virgil-lsp.json", "DEPS", ".git" }

return {
  {
    "neovim/nvim-lspconfig",
    opts = {
      servers = {
        virgil_lsp = {
          -- virgil-lsp is not in mason; install it yourself and put it on PATH.
          mason = false,
          cmd = { "virgil-lsp", "--stdio" },
          filetypes = { "virgil", "json" },
          -- Of the JSON files, only project files go to the server, so that it
          -- sees their unsaved edits.
          root_dir = function(bufnr, on_dir)
            if vim.bo[bufnr].filetype == "json"
              and vim.fs.basename(vim.api.nvim_buf_get_name(bufnr)) ~= ".virgil-lsp.json" then
              return
            end
            on_dir(vim.fs.root(bufnr, root_markers))
          end,
          -- The Virgil checkout that project files' virgilDependencies are
          -- relative to.
          init_options = { virgilRoot = vim.env.VIRGIL_LOC },
        },
      },
    },
  },
}
