-- LazyVim plugin spec for the Virgil language server.
-- Copy to ~/.config/nvim/lua/plugins/virgil.lua
vim.filetype.add({ extension = { v3 = "virgil" } })

-- Virgil sources indent with tabs. LazyVim turns on expandtab, which would
-- indent new lines with spaces; the VS Code extension sets
-- editor.insertSpaces to false for the same reason.
vim.api.nvim_create_autocmd("FileType", {
  pattern = "virgil",
  group = vim.api.nvim_create_augroup("virgil_lsp_indent", { clear = true }),
  callback = function(args) vim.bo[args.buf].expandtab = false end,
})

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
          -- Rename replies with versioned documentChanges, which Neovim
          -- applies but does not declare; the server refuses to rename for a
          -- client without them.
          capabilities = { workspace = { workspaceEdit = { documentChanges = true } } },
        },
      },
    },
  },
}
