-- Copyright 2026 The Virgil Language Server Authors.
-- SPDX-License-Identifier: Apache-2.0
-- Load the documented configuration verbatim, adding only a lifecycle observer
-- before Neovim opens the fixture and starts its client.
dofile(vim.env.VIRGIL_NVIM_CONFIG)
local exited
vim.lsp.config("virgil_lsp", {
  on_exit = function(code, signal)
    exited = { code = code, signal = signal }
  end,
})
local bufnr
local uri
local publications = {}
local publish = vim.lsp.handlers["textDocument/publishDiagnostics"]
vim.lsp.handlers["textDocument/publishDiagnostics"] = function(err, result, ctx, config)
  if result and result.uri == uri then
    publications[#publications + 1] = result
  end
  return publish(err, result, ctx, config)
end

local client
local function wait_for(description, predicate)
  assert(vim.wait(10000, predicate, 10), "timed out waiting for " .. description)
end

local function symbols()
  local response, err = client:request_sync(
    "textDocument/documentSymbol", { textDocument = { uri = uri } }, 10000, bufnr
  )
  assert(response, "documentSymbol failed: " .. vim.inspect(err))
  assert(not response.err, "documentSymbol error: " .. vim.inspect(response.err))
  assert(type(response.result) == "table", "documentSymbol did not return an array")
  return response.result
end

local function check()
  bufnr = vim.api.nvim_get_current_buf()
  uri = vim.uri_from_bufnr(bufnr)
  wait_for("client attachment", function()
    client = vim.lsp.get_clients({ bufnr = bufnr, name = "virgil_lsp" })[1]
    return client and client.initialized
  end)
  assert(client.config.root_dir == vim.fs.dirname(vim.api.nvim_buf_get_name(bufnr)),
    "root_dir was not discovered from the fixture's .virgil-lsp.json")
  assert(client.server_info and client.server_info.name == "virgil-lsp", "unexpected serverInfo")

  wait_for("published parser diagnostic", function()
    return #publications > 0 and #vim.diagnostic.get(bufnr) > 0
  end)
  local initial = publications[#publications]
  assert(#initial.diagnostics == 1, "expected exactly one parser diagnostic")
  local diagnostic = initial.diagnostics[1]
  assert(diagnostic.source == "aeneas" and diagnostic.message == "invalid start of expression",
    "unexpected diagnostic: " .. vim.inspect(diagnostic))
  assert(diagnostic.severity == vim.lsp.protocol.DiagnosticSeverity.Error, "expected error severity")
  assert(diagnostic.range.start.line == 2 and diagnostic.range.start.character == 26,
    "unexpected parser diagnostic position: " .. vim.inspect(diagnostic.range))
  assert(#symbols() == 0, "broken source should have no document symbols")

  -- An unsaved buffer edit must drive didChange, clear diagnostics, and update
  -- the outline. No direct RPC didOpen/didChange calls bypass the editor client.
  local before_edit = #publications
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, { "component C { def value = 1; }" })
  wait_for("published empty diagnostics after the edit", function()
    local latest = publications[#publications]
    return #publications > before_edit and #latest.diagnostics == 0
      and latest.version > initial.version and #vim.diagnostic.get(bufnr) == 0
  end)
  local outline = symbols()
  assert(#outline == 1 and outline[1].name == "C"
    and outline[1].kind == vim.lsp.protocol.SymbolKind.Namespace,
    "expected component C (kind 3): " .. vim.inspect(outline))

  -- Graceful stop sends shutdown and exit; do not force-kill the success path.
  client:stop()
  wait_for("server exit", function() return exited ~= nil end)
  assert(exited.code == 0 and exited.signal == 0,
    "server did not exit cleanly: " .. vim.inspect(exited))
  wait_for("client removal", function() return vim.lsp.get_client_by_id(client.id) == nil end)
  print("PASS: Neovim attach, diagnostics publish/clear, symbols, and clean server exit")
end

vim.schedule(function()
  local ok, err = xpcall(check, debug.traceback)
  if not ok then
    io.stderr:write("FAIL: Neovim smoke test: " .. err .. "\n")
    if client then
      client:stop(true)
    end
    vim.cmd("cquit 1")
  else
    vim.cmd("qa!")
  end
end)
