-- Copyright 2026 The Virgil Language Server Authors.
-- SPDX-License-Identifier: Apache-2.0
-- Loaded with --cmd, before LazyVim's init.lua. Instrumentation only: it
-- records what the client LazyVim starts does, and changes none of its
-- behavior except the LSP log level.
vim.lsp.log.set_level("debug")

local O = {
  exits = {}, -- by client id: { code, signal }
  published = vim.defaulttable(function() return {} end), -- by URI, in arrival order
  requests = {}, -- every textDocument/signatureHelp request, with its caller
  completions = {}, -- every textDocument/completion request, with its caller and reply
  ui_enter = false, -- whether UIEnter fired, which LazyVim's VeryLazy waits for
}
_G.VirgilObs = O

vim.api.nvim_create_autocmd("UIEnter", {
  once = true,
  callback = function() O.ui_enter = true end,
})

-- Merged into the configuration that the plugin spec gives LazyVim, as
-- check.lua merges it into the plain configuration.
vim.lsp.config("virgil_lsp", {
  on_exit = function(code, signal, client_id)
    O.exits[client_id] = { code = code, signal = signal }
  end,
})

local publish = vim.lsp.handlers["textDocument/publishDiagnostics"]
vim.lsp.handlers["textDocument/publishDiagnostics"] = function(err, result, ctx, config)
  if result then
    table.insert(O.published[result.uri], vim.tbl_extend("force", result, { client_id = ctx.client_id }))
  end
  return publish(err, result, ctx, config)
end

-- Which plugin asks for signature help: the first stack frame outside
-- Neovim's own LSP runtime.
local Client = require("vim.lsp.client")
local request = Client.request
function Client:request(method, params, handler, bufnr)
  if method == "textDocument/completion" then
    local caller = "?"
    for level = 2, 12 do
      local info = debug.getinfo(level, "S")
      if not info then break end
      if not info.source:find("runtime/lua/vim/lsp", 1, true) then
        caller = info.short_src
        break
      end
    end
    local record = {
      time = vim.uv.hrtime(), caller = caller, client_id = self.id,
      position = params and params.position, context = params and params.context,
    }
    table.insert(O.completions, record)
    local inner = handler
    handler = function(err, result, ctx, config)
      record.err, record.result, record.answered = err, result, vim.uv.hrtime()
      if inner then return inner(err, result, ctx, config) end
      local default = vim.lsp.handlers[method]
      if default then return default(err, result, ctx, config) end
    end
  end
  if method == "textDocument/signatureHelp" then
    local caller = "?"
    for level = 2, 12 do
      local info = debug.getinfo(level, "S")
      if not info then break end
      if not info.source:find("runtime/lua/vim/lsp", 1, true) then
        caller = info.short_src
        break
      end
    end
    local record = {
      time = vim.uv.hrtime(), caller = caller, client_id = self.id,
      position = params and params.position, context = params and params.context,
    }
    table.insert(O.requests, record)
    local inner = handler
    handler = function(err, result, ctx, config)
      record.err, record.result = err, result
      record.label = vim.tbl_get(result or {}, "signatures", 1, "label")
      if inner then return inner(err, result, ctx, config) end
      local default = vim.lsp.handlers[method]
      if default then return default(err, result, ctx, config) end
    end
  end
  return request(self, method, params, handler, bufnr)
end

-- Every server and worker process that Neovim starts, appended to
-- $VIRGIL_DRIVER_PIDS as "PID NAME", so that run.sh can check that none
-- outlives the run wherever the server is installed. Neovim starts the server
-- in its own process group, so it would otherwise escape a killed session.
local pids_file = vim.env.VIRGIL_DRIVER_PIDS
if pids_file then
  local seen = {}
  local function scan(pid)
    for _, child in ipairs(vim.api.nvim_get_proc_children(pid)) do
      local proc = vim.api.nvim_get_proc(child)
      if not seen[child] and proc and proc.name and proc.name:find("virgil-lsp", 1, true) then
        seen[child] = true
        local f = io.open(pids_file, "a")
        if f then
          f:write(child, " ", proc.name, "\n")
          f:close()
        end
      end
      scan(child)
    end
  end
  local timer = assert(vim.uv.new_timer())
  timer:start(0, 500, vim.schedule_wrap(function() pcall(scan, vim.fn.getpid()) end))
  vim.api.nvim_create_autocmd("LspAttach", { callback = function() pcall(scan, vim.fn.getpid()) end })
  vim.api.nvim_create_autocmd("VimLeavePre", { callback = function() timer:stop() end })
end
