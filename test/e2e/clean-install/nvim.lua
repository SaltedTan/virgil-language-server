-- Copyright 2026 The Virgil Language Server Authors.
-- SPDX-License-Identifier: Apache-2.0
-- Observe the README-installed configuration; never configure a client here.
local function check()
  local buf = vim.api.nvim_get_current_buf()
  assert(vim.wait(60000, function()
    for _, d in ipairs(vim.diagnostic.get(buf)) do
      if d.severity == vim.diagnostic.severity.ERROR and d.message == "expected int in var initialization, got Array<byte>" then return true end
    end
  end, 100), "no string-to-int diagnostic; got " .. vim.inspect(vim.diagnostic.get(buf)))
  local client = assert(vim.lsp.get_clients({ bufnr = buf, name = "virgil_lsp" })[1], "no README-configured client")
  local root = vim.env.HOME .. "/.local/opt/" .. vim.fn.fnamemodify(vim.fn.resolve(vim.fn.exepath("virgil-lsp")), ":h:t") .. "/virgil"
  assert(client.config.init_options.virgilRoot == vim.env.VIRGIL_LOC, "unexpected client root")
  local line = vim.api.nvim_buf_get_lines(buf, 2, 3, false)[1]
  local params = { textDocument = { uri = vim.uri_from_bufnr(buf) }, position = { line = 2, character = assert(line:find("StringBuilder", 1, true)) - 1 } }
  local definition = assert(client:request_sync("textDocument/definition", params, 30000, buf))
  assert(not definition.err, vim.inspect(definition.err))
  local location = assert(definition.result, "definition missing")
  assert(vim.uri_to_fname(location.uri) == root .. "/lib/util/StringBuilder.v3", vim.inspect(location))
  local lines = vim.fn.readfile(root .. "/lib/util/StringBuilder.v3")
  assert(lines[location.range.start.line + 1]:match("class StringBuilder"), "definition is not the class declaration")
  local hover = assert(client:request_sync("textDocument/hover", params, 30000, buf))
  assert(not hover.err and vim.inspect(hover.result):match("class StringBuilder"), vim.inspect(hover))
  local mode = vim.env.VIRGIL_LOC and "explicit" or "bundled"
  vim.fn.writefile({ vim.json.encode({ checks = { "diagnostic", "definition", "hover" },
    root = vim.env.VIRGIL_LOC or vim.NIL, definition = vim.uri_to_fname(location.uri) }) },
    vim.env.HOME .. "/" .. vim.env.CLEAN_EDITOR .. "-" .. mode .. ".json")
  print("PASS diagnostic, library definition, hover; root=" .. (vim.env.VIRGIL_LOC or "bundled"))
  client:stop()
  assert(vim.wait(10000, function() return client:is_stopped() end), "server did not stop")
end
vim.schedule(function()
  local ok, err = xpcall(check, debug.traceback)
  if not ok then vim.api.nvim_err_writeln(err); vim.cmd("cquit 1") else vim.cmd("qa!") end
end)
