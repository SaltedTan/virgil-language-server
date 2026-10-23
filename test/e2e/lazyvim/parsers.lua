-- Copyright 2026 The Virgil Language Server Authors.
-- SPDX-License-Identifier: Apache-2.0
-- Installs nvim-treesitter's parsers before the runs, as LazyVim's
-- nvim-treesitter spec does at a first start: the tree-sitter CLI with
-- mason.nvim, if it isn't on PATH, then the parsers in the spec's
-- ensure_installed, at the revisions that the locked nvim-treesitter pins.
-- run.sh runs it with -c in a headless Neovim under the starter's
-- configuration, with a plugin spec that leaves LazyVim's configuration of
-- nvim-treesitter only its setup, and under its deadline for installing the
-- plugins, which bounds the waits here. It also waits for what mason.nvim
-- installs meanwhile for LazyVim, such as stylua, so that quitting cuts off
-- no install. It writes "tree-sitter CLI ..." and "parser LANGUAGE REVISION"
-- lines to $VIRGIL_PARSERS_OUT, and quits with status 1 if a parser is missing.
local forever = 2 ^ 31 - 1

local function main()
  local cli
  LazyVim.treesitter.ensure_treesitter_cli(function(ok, err) cli = { ok = ok, err = err } end)
  vim.wait(forever, function() return cli ~= nil end, 100)
  assert(cli.ok, "the tree-sitter CLI was not installed: " .. tostring(cli.err))

  require("lazy").load({ plugins = { "nvim-treesitter" } })
  local TS = require("nvim-treesitter")
  local languages = LazyVim.opts("nvim-treesitter").ensure_installed
  TS.install(languages, { summary = true }):wait(forever)
  local installed = {}
  for _, lang in ipairs(TS.get_installed("parsers")) do installed[lang] = true end
  local missing = vim.tbl_filter(function(lang) return not installed[lang] end, languages)
  assert(#missing == 0, "parsers not installed: " .. table.concat(missing, ", "))

  local registry = require("mason-registry")
  vim.wait(forever, function()
    for _, package in ipairs(registry.get_all_packages()) do
      if package:is_installing() then return false end
    end
    return true
  end, 200)

  local version = vim.system({ "tree-sitter", "--version" }, { text = true }):wait().stdout or "?"
  local lines = { string.format("tree-sitter CLI: %s (%s)", vim.trim(version), vim.fn.exepath("tree-sitter")) }
  local info = require("nvim-treesitter.config").get_install_dir("parser-info")
  for _, lang in ipairs(languages) do
    local read, revision = pcall(vim.fn.readfile, vim.fs.joinpath(info, lang .. ".revision"))
    lines[#lines + 1] = string.format("parser %s %s", lang, read and revision[1] or "?")
  end
  vim.fn.writefile(lines, assert(vim.env.VIRGIL_PARSERS_OUT))
end

local ok, err = xpcall(main, debug.traceback)
if not ok then
  io.stderr:write("parsers.lua: ", tostring(err), "\n")
  vim.cmd("cquit 1")
end
vim.cmd("qa!")
