-- Copyright 2026 The Virgil Language Server Authors.
-- SPDX-License-Identifier: Apache-2.0
-- Drives the virgil_lsp client that LazyVim starts from the shipped plugin
-- spec, with the assertions of test/e2e/nvim/check.lua and a few LazyVim
-- specific ones. run.sh starts it in a tmux session; see README.md. Loaded
-- with -c after LazyVim's init.lua; observer.lua must have been loaded with
-- --cmd. Results go to $VIRGIL_DRIVER_OUT, one line per check: PASS, FAIL,
-- XFAIL (a known plugin limitation, as expected), or XPASS (that limitation
-- is gone, which fails the run), then a DONE line with the counts.
-- $VIRGIL_DRIVER_MODE is "main" (VIRGIL_LOC set) or "noroot";
-- $VIRGIL_DRIVER_NOICE_WORKAROUND=1 says that the configuration turns off
-- noice's LSP message handler. On a failure, the tmux pane is appended to
-- $VIRGIL_DRIVER_SCREEN.
local O = assert(_G.VirgilObs, "observer.lua was not loaded with --cmd")
local mode = vim.env.VIRGIL_DRIVER_MODE or "main"
local noice_workaround = vim.env.VIRGIL_DRIVER_NOICE_WORKAROUND == "1"
local out = assert(io.open(assert(vim.env.VIRGIL_DRIVER_OUT), "w"))
local passes, failures, expected_failures = 0, 0, 0

local function emit(line)
  out:write(line, "\n")
  out:flush()
end

local function info(fmt, ...)
  emit("INFO " .. string.format(fmt, ...))
end

-- The screen as the user would see it, from tmux, after a redraw.
local function capture_screen(label)
  local file, pane = vim.env.VIRGIL_DRIVER_SCREEN, vim.env.TMUX_PANE
  if not (file and pane and vim.env.TMUX) then return end
  pcall(vim.cmd.redraw)
  vim.wait(200)
  local ok, result = pcall(function()
    return vim.system({ "tmux", "capture-pane", "-p", "-t", pane }, { text = true }):wait(5000)
  end)
  local f = io.open(file, "a")
  if not f then return end
  f:write("== screen after ", label, "\n")
  f:write(ok and result.code == 0 and result.stdout or ("(capture failed: " .. vim.inspect(result) .. ")\n"))
  f:close()
end

local co
local function sleep(ms)
  local self = coroutine.running()
  vim.defer_fn(function()
    local ok, err = coroutine.resume(self)
    if not ok then
      emit("FAIL driver: " .. tostring(err))
      vim.cmd("cquit 1")
    end
  end, ms)
  coroutine.yield()
end

-- Polls between main-loop turns, so typed input and plugin timers still run.
local function wait_for(description, predicate, timeout)
  local deadline = vim.uv.now() + (timeout or 10000)
  while true do
    local ok, value = pcall(predicate)
    if ok and value then return value end
    if vim.uv.now() > deadline then
      error("timed out waiting for " .. description .. (ok and "" or (": " .. tostring(value))), 0)
    end
    sleep(20)
  end
end

local function step(name, fn)
  local ok, err = xpcall(fn, debug.traceback)
  if ok then
    passes = passes + 1
    emit("PASS " .. name .. (type(err) == "string" and (": " .. err) or ""))
  else
    failures = failures + 1
    emit("FAIL " .. name .. ": " .. tostring(err):gsub("\n", "\n    "))
    capture_screen("FAIL " .. name)
  end
  return ok
end

-- A step that a known limitation of a pinned plugin makes fail. Its failure
-- is reported as XFAIL with the reason; if it passes, the limitation is gone,
-- and XPASS fails the run so that the lock update gets reviewed.
local function expected_failure(name, reason, fn)
  local ok, err = xpcall(fn, debug.traceback)
  if ok then
    failures = failures + 1
    emit("XPASS " .. name .. (type(err) == "string" and (": " .. err) or "")
      .. "; expected to fail: " .. reason)
  else
    expected_failures = expected_failures + 1
    emit("XFAIL " .. name .. ": " .. reason .. "; " .. (tostring(err):match("^[^\n]*")))
  end
end

local function latest(target)
  local list = O.published[target]
  return list[#list]
end

local function codes(publication)
  local result = {}
  for _, d in ipairs(publication.diagnostics) do
    result[#result + 1] = d.code
  end
  return table.concat(result, ",")
end

-- Waits for the reply between main-loop turns, as wait_for does. request_sync
-- waits in a nested event loop instead, which can't return before the loops
-- nested in it do: plugins nest their own, such as lazy.nvim's check for
-- updates, and observer.lua's process scans once held one past its timeout
-- after the reply had come.
local function request(client, method, params, bufnr)
  local response
  local sent, id = client:request(method, params, function(err, result)
    response = { err = err, result = result }
  end, bufnr)
  assert(sent, method .. " was not sent")
  local ok, err = pcall(wait_for, "the " .. method .. " reply", function() return response end)
  if not ok then
    client:cancel_request(id)
    error(err, 0)
  end
  assert(not response.err, method .. " error: " .. vim.inspect(response.err))
  return response.result
end

-- Text of every visible floating window, such as noice's hover and
-- signature popups.
local function floats()
  local texts = {}
  for _, win in ipairs(vim.api.nvim_list_wins()) do
    local ok, config = pcall(vim.api.nvim_win_get_config, win)
    if ok and config.relative ~= "" then
      local buf = vim.api.nvim_win_get_buf(win)
      texts[#texts + 1] = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
    end
  end
  return texts
end

local function float_with(text)
  for _, t in ipairs(floats()) do
    if t:find(text, 1, true) then return t end
  end
end

-- blink.cmp's completion list and menu, as the user sees them.
local function blink_items()
  local ok_cmp, cmp = pcall(require, "blink.cmp")
  local ok_list, list = pcall(require, "blink.cmp.completion.list")
  local visible = ok_cmp and cmp.is_menu_visible and cmp.is_menu_visible() or false
  return visible, (visible and ok_list and list.items) or {}
end

local function item_text(item)
  local kind = item.kind and (vim.lsp.protocol.CompletionItemKind[item.kind] or tostring(item.kind)) or "?"
  return string.format("%s (%s %s%s)", item.label, tostring(item.source_id), kind,
    item.detail and (", " .. item.detail) or "")
end

local function items_text(items, limit)
  local texts = {}
  for i = 1, math.min(#items, limit or 12) do texts[i] = item_text(items[i]) end
  if #items > (limit or 12) then texts[#texts + 1] = string.format("... %d in all", #items) end
  return table.concat(texts, "; ")
end

local function lsp_items(items)
  return vim.tbl_filter(function(item) return item.source_id == "lsp" end, items)
end

-- Types one key at a time, as a user does, so that blink.cmp and noice see
-- each character.
local function type_keys(keys)
  for _, key in ipairs(keys) do
    vim.api.nvim_input(key)
    sleep(150)
  end
end

local function virgil_clients(filter)
  return vim.lsp.get_clients(vim.tbl_extend("force", { name = "virgil_lsp" }, filter or {}))
end

-- A plugin's commit in the lock that the run restored.
local function locked_commit(name)
  local lock = vim.fn.stdpath("config") .. "/lazy-lock.json"
  local ok, decoded = pcall(vim.json.decode, table.concat(vim.fn.readfile(lock), "\n"))
  return ok and decoded[name] and decoded[name].commit or "?"
end

local function lazy_info()
  local version = (package.loaded["lazyvim.config"] or {}).version or "?"
  info("Neovim %s; LazyVim %s (%s); nvim-lspconfig %s; blink.cmp %s; noice.nvim %s",
    tostring(vim.version()), version, locked_commit("LazyVim"), locked_commit("nvim-lspconfig"),
    locked_commit("blink.cmp"), locked_commit("noice.nvim"))
end

-- LazyVim loads noice, mini.pairs, and its keymaps on VeryLazy, which
-- lazy.nvim fires only after UIEnter, so the session must have a UI, as a
-- user's does. The driver waits for it and never fires VeryLazy itself.
local function startup_checks()
  step("startup: a UI attached and LazyVim's VeryLazy fired", function()
    wait_for("VeryLazy and noice", function()
      return vim.g.did_very_lazy and package.loaded["noice"] ~= nil
    end, 15000)
    local uis = #vim.api.nvim_list_uis()
    assert(uis >= 1, "no UI attached")
    assert(O.ui_enter, "UIEnter did not fire")
    return string.format("UIs attached: %d; UIEnter fired; VeryLazy fired; noice loaded", uis)
  end)
end

-- Messages that noice shows: LSP messages, notifications, and Neovim's own.
local function noice_messages()
  local ok, manager = pcall(require, "noice.message.manager")
  if not ok then return {} end
  local result = {}
  for _, m in ipairs(manager.get({}, { history = true, sort = true })) do
    result[#result + 1] = { event = m.event, kind = m.kind, level = m.level, text = m:content() }
  end
  return result
end

local function errors_seen()
  local messages = vim.api.nvim_exec2("messages", { output = true }).output
  info("v:errmsg: %q", vim.v.errmsg)
  for line in messages:gmatch("[^\n]+") do
    if line:find("[Ee]rror") or line:find("E%d+:") or line:find("Traceback") then
      emit("WARN :messages: " .. line)
    end
  end
  for _, m in ipairs(noice_messages()) do
    local text = m.text:gsub("\n", " | ")
    local line = string.format("noice %s/%s [%s]: %s", tostring(m.event), tostring(m.kind), tostring(m.level), text)
    if m.level == "error" or text:find("[Ee]rror") then emit("WARN " .. line) else info("%s", line) end
  end
end

local function main_checks()
  local bufnr = vim.api.nvim_get_current_buf()
  local uri = vim.uri_from_bufnr(bufnr)
  local publications = O.published[uri]
  local client

  if not step("attach", function()
    client = wait_for("client attachment", function()
      local c = virgil_clients({ bufnr = bufnr })[1]
      return c and c.initialized and c
    end, 20000)
    assert(client.config.root_dir == vim.fs.dirname(vim.api.nvim_buf_get_name(bufnr)),
      "root_dir was not discovered from the fixture's .virgil-lsp.json: " .. tostring(client.config.root_dir))
    assert(client.server_info and client.server_info.name == "virgil-lsp", "unexpected serverInfo")
    assert(vim.deep_equal(client.config.cmd, { "virgil-lsp", "--stdio" }), "unexpected cmd: " .. vim.inspect(client.config.cmd))
    assert(client.config.init_options.virgilRoot == vim.env.VIRGIL_LOC,
      "virgilRoot was not taken from VIRGIL_LOC: " .. vim.inspect(client.config.init_options))
    info("server_info: %s; cmd resolves to %s", vim.inspect(client.server_info), vim.fn.exepath("virgil-lsp"))
    info("client id %d, root_dir %s, init_options %s", client.id, client.config.root_dir, vim.inspect(client.config.init_options))
    info("server capabilities: %s", vim.inspect(client.server_capabilities, { newline = " ", indent = "" }))
    info("client capabilities from LazyVim: workspace.fileOperations=%s textDocument.completion? %s",
      vim.inspect(client.config.capabilities and client.config.capabilities.workspace
        and client.config.capabilities.workspace.fileOperations),
      tostring(client.config.capabilities and client.config.capabilities.textDocument
        and client.config.capabilities.textDocument.completion ~= nil))
    return string.format("client %d (%s %s), root %s", client.id, client.server_info.name,
      tostring(client.server_info.version), client.config.root_dir)
  end) then
    return
  end

  local initial
  step("diagnostics: parser error published", function()
    wait_for("published parser diagnostic", function()
      return #publications > 0 and #vim.diagnostic.get(bufnr) > 0
    end)
    initial = publications[#publications]
    assert(#initial.diagnostics == 1, "expected exactly one parser diagnostic: " .. vim.inspect(initial))
    local d = initial.diagnostics[1]
    assert(d.source == "aeneas" and d.message == "invalid start of expression",
      "unexpected diagnostic: " .. vim.inspect(d))
    assert(d.severity == vim.lsp.protocol.DiagnosticSeverity.Error, "expected error severity")
    assert(d.range.start.line == 2 and d.range.start.character == 26,
      "unexpected parser diagnostic position: " .. vim.inspect(d.range))
    assert(#request(client, "textDocument/documentSymbol", { textDocument = { uri = uri } }, bufnr) == 0,
      "broken source should have no document symbols")
    return "line 3, col 27: " .. d.message
  end)

  local original_bad = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  step("diagnostics: cleared after an unsaved edit, back after undo", function()
    local before_edit = #publications
    vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, { "component C { def value = 1; }", "type V { case A(x: int); }" })
    wait_for("published empty diagnostics after the edit", function()
      local last = publications[#publications]
      return #publications > before_edit and #last.diagnostics == 0
        and last.version > initial.version and #vim.diagnostic.get(bufnr) == 0
    end)
    local before_undo = #publications
    vim.cmd("silent undo")
    assert(vim.deep_equal(vim.api.nvim_buf_get_lines(bufnr, 0, -1, false), original_bad), "undo did not restore bad.v3")
    wait_for("parser diagnostic after undo", function()
      local last = publications[#publications]
      return #publications > before_undo and #last.diagnostics == 1
        and last.diagnostics[1].message == "invalid start of expression"
    end)
    vim.cmd("silent redo")
  end)

  step("document symbols: bad.v3 after the edit", function()
    local outline = wait_for("outline of the edited text", function()
      local o = request(client, "textDocument/documentSymbol", { textDocument = { uri = uri } }, bufnr)
      return #o == 2 and o
    end)
    assert(outline[1].name == "C" and outline[1].kind == vim.lsp.protocol.SymbolKind.Namespace
      and outline[2].name == "V" and outline[2].kind == vim.lsp.protocol.SymbolKind.Class,
      "expected component C (kind 3) and variant V (kind 5): " .. vim.inspect(outline))
    local case = outline[2].children and outline[2].children[1]
    assert(#outline[2].children == 1 and case.name == "A"
      and case.kind == vim.lsp.protocol.SymbolKind.EnumMember
      and case.selectionRange.start.line == 1 and case.selectionRange.start.character == 14
      and case.selectionRange["end"].line == 1 and case.selectionRange["end"].character == 15,
      "expected case A (kind 22) of V: " .. vim.inspect(outline[2]))
    return "C (Namespace), V (Class) > A (EnumMember)"
  end)

  local root = client.config.root_dir
  local config_uri = vim.uri_from_fname(vim.fs.joinpath(root, ".virgil-lsp.json"))
  step("Virgil root: project file valid with lib/util from VIRGIL_LOC", function()
    wait_for("published configuration diagnostics", function() return latest(config_uri) ~= nil end)
    assert(#latest(config_uri).diagnostics == 0,
      "unexpected configuration diagnostics: " .. codes(latest(config_uri)))
  end)

  vim.cmd.edit(vim.fs.joinpath(root, "words", "Words.v3"))
  local words_buf = vim.api.nvim_get_current_buf()
  local words_uri = vim.uri_from_bufnr(words_buf)
  local original_words = vim.api.nvim_buf_get_lines(words_buf, 0, -1, false)
  pcall(wait_for, "Words.v3 attachment", function() return vim.lsp.buf_is_attached(words_buf, client.id) end)
  local function type_error_published(after)
    return function()
      local list = O.published[words_uri]
      local last = list[#list]
      return #list > (after or 0) and #last.diagnostics == 1 and last.diagnostics[1].code == "TypeError"
        and last.diagnostics[1].message == "expected int in var initialization, got Array<byte>" and last
    end
  end
  step("diagnostics: semantic TypeError in words/Words.v3", function()
    assert(vim.lsp.buf_is_attached(words_buf, client.id), "Words.v3 not attached to the project's client")
    local last = wait_for("semantic diagnostics for words/Words.v3", type_error_published(), 40000)
    return "line " .. (last.diagnostics[1].range.start.line + 1) .. ": " .. last.diagnostics[1].message
  end)

  step("document symbols: words/Words.v3", function()
    local outline = request(client, "textDocument/documentSymbol", { textDocument = { uri = words_uri } }, words_buf)
    assert(#outline == 1 and outline[1].name == "Words" and outline[1].children
      and #outline[1].children == 1 and outline[1].children[1].name == "join",
      "expected Words > join: " .. vim.inspect(outline))
    return string.format("Words (kind %d) > join (kind %d)", outline[1].kind, outline[1].children[1].kind)
  end)

  local use_line = 6
  local line_text = vim.api.nvim_buf_get_lines(words_buf, use_line, use_line + 1, true)[1]
  local start = line_text:find("StringBuilder", 1, true)
  local position = { line = use_line, character = start - 1 }
  local library = vim.fs.joinpath(vim.env.VIRGIL_LOC, "lib", "util", "StringBuilder.v3")
  local class_line
  for i, text in ipairs(vim.fn.readfile(library)) do
    if not class_line and text:find("^class StringBuilder") then class_line = i - 1 end
  end

  step("definition (request)", function()
    local location = request(client, "textDocument/definition", { textDocument = { uri = words_uri }, position = position }, words_buf)
    assert(location and location.uri and vim.uri_to_fname(location.uri) == library
      and location.range.start.line == class_line and location.range.start.character == 6
      and location.range["end"].line == class_line and location.range["end"].character == 19,
      "unexpected definition: " .. vim.inspect(location))
    return string.format("lib/util/StringBuilder.v3:%d:7", class_line + 1)
  end)

  step("definition (LazyVim gd)", function()
    -- LazyVim maps gd for the buffer on LspAttach; a user can't press it sooner.
    wait_for("LazyVim's gd mapping", function()
      local map = vim.fn.maparg("gd", "n", false, true)
      return map.buffer == 1 and map.desc == "Goto Definition"
    end)
    vim.api.nvim_win_set_cursor(0, { use_line + 1, start - 1 })
    vim.api.nvim_input("gd")
    wait_for("gd to open StringBuilder.v3", function()
      return vim.api.nvim_buf_get_name(0) == library and vim.api.nvim_win_get_cursor(0)[1] == class_line + 1
    end)
    local cursor = vim.api.nvim_win_get_cursor(0)
    -- The library file is in the Virgil checkout, so it gets its own client.
    local library_buf = vim.api.nvim_get_current_buf()
    local other = wait_for("a client for the library file", function()
      return virgil_clients({ bufnr = library_buf })[1]
    end)
    info("library file client %d, root %s", other.id, tostring(other.config.root_dir))
    vim.api.nvim_set_current_buf(words_buf)
    return string.format("jumped to %d:%d", cursor[1], cursor[2] + 1)
  end)

  step("inlay hints (LazyVim API)", function()
    vim.cmd.edit(vim.fn.fnameescape(vim.fs.joinpath(root, "words", "Inlay.v3")))
    local buf = vim.api.nvim_get_current_buf()
    wait_for("LazyVim inlay hints", function()
      return vim.lsp.inlay_hint.is_enabled({ bufnr = buf }) and #vim.lsp.inlay_hint.get({ bufnr = buf }) == 2
    end)
    local hints = vim.lsp.inlay_hint.get({ bufnr = buf })
    table.sort(hints, function(a, b) return a.inlay_hint.position.character < b.inlay_hint.position.character end)
    -- Neovim stores received UTF-16 positions as buffer byte columns.
    local function position(hint)
      local p = hint.inlay_hint.position
      local text = vim.api.nvim_buf_get_lines(0, p.line, p.line + 1, true)[1]
      return { line = p.line, character = vim.str_utfindex(text, "utf-16", p.character) }
    end
    assert(vim.deep_equal(position(hints[1]), { line = 4, character = 33 })
      and hints[1].inlay_hint.label[1].value == ": int" and hints[1].inlay_hint.kind == 1, vim.inspect(hints))
    assert(vim.deep_equal(position(hints[2]), { line = 4, character = 41 })
      and hints[2].inlay_hint.label == "count:" and hints[2].inlay_hint.kind == 2, vim.inspect(hints))
    vim.api.nvim_set_current_buf(words_buf)
    return ": int at 5:34; count: at 5:42; hints enabled by LazyVim"
  end)

  local type_column = line_text:find(".puts(", 1, true)
  step("type definition (request)", function()
    local location = request(client, "textDocument/typeDefinition", {
      textDocument = { uri = words_uri }, position = { line = use_line, character = type_column },
    }, words_buf)
    assert(location and location.uri and vim.uri_to_fname(location.uri) == library
      and location.range.start.line == class_line and location.range.start.character == 6
      and location.range["end"].line == class_line and location.range["end"].character == 19,
      "unexpected type definition: " .. vim.inspect(location))
    local builtin = request(client, "textDocument/typeDefinition", {
      textDocument = { uri = words_uri }, position = { line = use_line, character = 6 },
    }, words_buf)
    assert(builtin == nil or builtin == vim.NIL, "built-in int must have no type location: " .. vim.inspect(builtin))
    return string.format("puts result -> lib/util/StringBuilder.v3:%d:7; int -> null", class_line + 1)
  end)

  step("type definition (LazyVim gy)", function()
    wait_for("LazyVim's gy mapping", function()
      local map = vim.fn.maparg("gy", "n", false, true)
      return map.buffer == 1 and map.desc and map.desc:find("Definition", 1, true)
    end)
    vim.api.nvim_win_set_cursor(0, { use_line + 1, type_column })
    vim.api.nvim_input("gy")
    wait_for("gy to open StringBuilder.v3", function()
      local cursor = vim.api.nvim_win_get_cursor(0)
      return vim.api.nvim_buf_get_name(0) == library and cursor[1] == class_line + 1 and cursor[2] == 6
    end)
    vim.api.nvim_set_current_buf(words_buf)
    return string.format("jumped to %d:7", class_line + 1)
  end)

  step("hover (request)", function()
    local hover = request(client, "textDocument/hover", { textDocument = { uri = words_uri }, position = position }, words_buf)
    assert(hover and hover.contents and hover.contents.kind == "markdown"
      and hover.contents.value == "```virgil\nclass StringBuilder\n```"
      and hover.range.start.line == use_line and hover.range.start.character == start - 1
      and hover.range["end"].line == use_line and hover.range["end"].character == start - 1 + #"StringBuilder",
      "unexpected hover: " .. vim.inspect(hover))
  end)

  step("hover (LazyVim K popup)", function()
    vim.api.nvim_win_set_cursor(0, { use_line + 1, start - 1 })
    vim.api.nvim_input("K")
    local text = wait_for("a hover window with class StringBuilder", function() return float_with("class StringBuilder") end)
    -- noice hides it when the cursor moves.
    vim.api.nvim_input("0")
    wait_for("the hover window to close", function() return not float_with("class StringBuilder") end, 5000)
    return "window shows " .. vim.inspect(text) .. ", closed on cursor move"
  end)

  local puts = line_text:find(".puts(", 1, true)
  step("signature help (request)", function()
    local help = request(client, "textDocument/signatureHelp", {
      textDocument = { uri = words_uri }, position = { line = use_line, character = puts - 1 + #".puts(" },
    }, words_buf)
    local signature = help and help.signatures and help.signatures[1]
    assert(signature and #help.signatures == 1 and help.activeSignature == 0 and help.activeParameter == 0
      and signature.label == "def puts(s: Array<byte>) -> this"
      and #signature.parameters == 1 and vim.deep_equal(signature.parameters[1].label, { 9, 23 }),
      "unexpected signature help: " .. vim.inspect(help))
    local after = request(client, "textDocument/signatureHelp", {
      textDocument = { uri = words_uri }, position = { line = use_line, character = puts - 1 + #".puts(a)" },
    }, words_buf)
    assert(after == nil or after == vim.NIL, "expected nothing just past the ): " .. vim.inspect(after))
  end)

  step("workspace symbols", function()
    local symbol = (request(client, "workspace/symbol", { query = "join" }, words_buf) or {})[1]
    assert(symbol and symbol.name == "join" and symbol.kind == vim.lsp.protocol.SymbolKind.Method
      and vim.uri_to_fname(symbol.location.uri) == vim.api.nvim_buf_get_name(words_buf)
      and symbol.location.range.start.line == 5 and symbol.location.range.start.character == 5
      and symbol.location.range["end"].line == 5 and symbol.location.range["end"].character == 9,
      "unexpected workspace symbol: " .. vim.inspect(symbol))
  end)

  step("references", function()
    local references = request(client, "textDocument/references", {
      textDocument = { uri = words_uri }, position = position, context = { includeDeclaration = true },
    }, words_buf) or {}
    local found_use, found_class, previous = false, false, nil
    local library_files = {}
    for _, location in ipairs(references) do
      local file, range = vim.uri_to_fname(location.uri), location.range
      local text = file == vim.api.nvim_buf_get_name(words_buf)
        and vim.api.nvim_buf_get_lines(words_buf, range.start.line, range.start.line + 1, true)[1]
        or vim.fn.readfile(file)[range.start.line + 1]
      local first = vim.str_byteindex(text, "utf-16", range.start.character, false)
      local key = { file, range.start.line, range.start.character }
      assert(range["end"].line == range.start.line
        and text:sub(first + 1, first + #"StringBuilder") == "StringBuilder"
        and vim.str_utfindex(text, "utf-16", first + #"StringBuilder", false) == range["end"].character,
        "a reference does not spell StringBuilder: " .. vim.inspect(location))
      assert(previous == nil or previous[1] < key[1] or (previous[1] == key[1]
        and (previous[2] < key[2] or (previous[2] == key[2] and previous[3] < key[3]))),
        "references out of order or repeated: " .. vim.inspect(references))
      previous = key
      if file == vim.api.nvim_buf_get_name(words_buf) then
        assert(not found_use and range.start.line == use_line and range.start.character == start - 1,
          "unexpected reference in words/Words.v3: " .. vim.inspect(location))
        found_use = true
      else
        assert(vim.startswith(file, vim.fs.joinpath(vim.env.VIRGIL_LOC, "lib", "util") .. "/"),
          "a reference outside the project: " .. vim.inspect(location))
        library_files[file] = true
        found_class = found_class or (file == library and range.start.line == class_line and range.start.character == 6)
      end
    end
    assert(found_use and found_class and vim.tbl_count(library_files) > 1,
      "unexpected references: " .. vim.inspect(references))
    return string.format("%d locations in %d library files and Words.v3", #references, vim.tbl_count(library_files))
  end)

  -- lazyvim.document-highlight.navigation: the plugin's own extmarks and
  -- mapped keys, after natural CursorMoved handling and its debounce timer.
  step("document highlights: next and previous use (LazyVim ]] and [[)", function()
    vim.cmd.edit(vim.fs.joinpath(root, "words", "Highlights.v3"))
    local buf = vim.api.nvim_get_current_buf()
    wait_for("Highlights.v3 attachment", function() return vim.lsp.buf_is_attached(buf, client.id) end)
    assert(Snacks.words.is_enabled(), "snacks words is not enabled")
    for _, mapping in ipairs({ { "]]", "Next Reference" }, { "[[", "Prev Reference" } }) do
      wait_for("LazyVim's " .. mapping[1] .. " mapping", function()
        local map = vim.fn.maparg(mapping[1], "n", false, true)
        return map.buffer == 1 and map.desc == mapping[2]
      end)
    end
    local positions = { { 5, 6 }, { 6, 2 }, { 6, 10 }, { 7, 2 }, { 8, 9 } }
    -- Move onto the first write, between the declaration and the RHS read.
    vim.api.nvim_win_set_cursor(0, positions[2])
    wait_for("snacks words' exact count highlights", function()
      local words, current = Snacks.words.get()
      local starts = vim.tbl_map(function(word) return word.from end, words)
      return current == 2 and vim.deep_equal(starts, positions)
    end, 15000)
    vim.api.nvim_input("]]")
    wait_for("]] to the next use", function()
      return vim.deep_equal(vim.api.nvim_win_get_cursor(0), positions[3])
    end)
    vim.api.nvim_input("[[")
    wait_for("[[ to the previous use", function()
      return vim.deep_equal(vim.api.nvim_win_get_cursor(0), positions[2])
    end)
    return "five snacks word highlights; ]] moved 6:3 to 6:11, [[ returned to 6:3"
  end)
  vim.api.nvim_set_current_buf(words_buf)

  -- Shape's subclasses, directly and transitively, and the methods that
  -- override its area, by offset; the unextended Circle has none.
  local shapes = vim.fs.joinpath(root, "words", "Shapes.v3")
  local shapes_uri = vim.uri_from_fname(shapes)
  step("implementation (request)", function()
    local function names(spots)
      local result = {}
      for _, spot in ipairs(spots) do
        result[#result + 1] = { uri = shapes_uri, range = {
          start = { line = spot[1], character = spot[2] }, ["end"] = { line = spot[1], character = spot[3] },
        } }
      end
      return result
    end
    for _, case in ipairs({
      { 4, 6, names({ { 7, 6, 13 }, { 10, 6, 12 }, { 11, 6, 12 } }) },
      { 5, 5, names({ { 8, 5, 9 }, { 12, 5, 9 } }) },
      { 11, 6, {} },
    }) do
      local locations = request(client, "textDocument/implementation", {
        textDocument = { uri = shapes_uri }, position = { line = case[1], character = case[2] },
      }, words_buf)
      assert(vim.deep_equal(locations, case[3]),
        string.format("unexpected implementations at %d:%d: %s", case[1] + 1, case[2] + 1, vim.inspect(locations)))
    end
    return "Shape -> Polygon, Square, Circle; Shape.area -> 2 overrides; Circle -> none"
  end)

  -- Polygon's one subclass is Square, so gI jumps to it in the same file.
  step("implementation (LazyVim gI)", function()
    vim.cmd.edit(shapes)
    local buf = vim.api.nvim_get_current_buf()
    wait_for("Shapes.v3 attachment", function() return vim.lsp.buf_is_attached(buf, client.id) end)
    wait_for("LazyVim's gI mapping", function()
      local map = vim.fn.maparg("gI", "n", false, true)
      return map.buffer == 1 and map.desc == "Goto Implementation"
    end)
    vim.api.nvim_win_set_cursor(0, { 8, 6 })
    vim.api.nvim_input("gI")
    wait_for("gI to jump to Square", function()
      local cursor = vim.api.nvim_win_get_cursor(0)
      return vim.api.nvim_get_current_buf() == buf and cursor[1] == 11 and cursor[2] == 6
    end)
    return "jumped to 11:7"
  end)
  vim.api.nvim_set_current_buf(words_buf)

  -- Rename through LazyVim's <leader>cr, which it maps for a server with
  -- rename. Neovim asks prepareRename first; while the project has a type
  -- error the server refuses, and Neovim shows its reason with vim.notify,
  -- which noice keeps. The procedure's unsaved fix then lets it rename the
  -- local line through the prompt, and undo restores both edits in turn.
  local leader = vim.g.mapleader or "\\"
  local name_column = line_text:find("line", 1, true) - 1
  step("rename refused while the project has a type error (LazyVim <leader>cr)", function()
    local map = wait_for("LazyVim's <leader>cr mapping", function()
      local m = vim.fn.maparg(leader .. "cr", "n", false, true)
      return m.buffer == 1 and m.desc == "Rename" and m
    end)
    info("renameProvider: %s; <leader>cr: desc %q, rhs %s", vim.inspect(client.server_capabilities.renameProvider),
      map.desc, tostring(map.rhs or (map.callback and "<Lua callback>")))
    vim.api.nvim_set_current_buf(words_buf)
    vim.api.nvim_win_set_cursor(0, { use_line + 1, name_column })
    vim.api.nvim_input(leader .. "cr")
    local shown = wait_for("the server's reason in a notification", function()
      for _, m in ipairs(noice_messages()) do
        if m.text:find("rename needs a program that verifies", 1, true) then return m end
      end
    end, 15000)
    sleep(300)
    assert(vim.api.nvim_get_mode().mode == "n", "a rename prompt opened: mode " .. vim.api.nvim_get_mode().mode)
    assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original_words), "the buffer changed")
    return string.format("noice %s/%s [%s] %q", tostring(shown.event), tostring(shown.kind), tostring(shown.level), shown.text)
  end)

  step("rename through LazyVim <leader>cr after the unsaved fix, then undo", function()
    local fixed_use = (original_words[use_line + 1]:gsub("var line: int =", "var line ="))
    local fixed_return = (original_words[use_line + 2]:gsub("return line;", "return line.length;"))
    local before = #O.published[words_uri]
    vim.api.nvim_buf_set_lines(words_buf, use_line, use_line + 2, true, { fixed_use, fixed_return })
    vim.o.undolevels = vim.o.undolevels
    local fixed = vim.api.nvim_buf_get_lines(words_buf, 0, -1, true)
    wait_for("no diagnostics after the fix", function()
      local list = O.published[words_uri]
      return #list > before and #list[#list].diagnostics == 0
    end, 20000)
    vim.api.nvim_win_set_cursor(0, { use_line + 1, name_column })
    vim.api.nvim_input(leader .. "cr")
    local prompt = wait_for("the rename prompt", function()
      local mode = vim.api.nvim_get_mode().mode
      if mode == "i" or mode == "c" then
        return { mode = mode, filetype = vim.bo.filetype,
          text = mode == "c" and vim.fn.getcmdline() or vim.api.nvim_get_current_line() }
      end
    end, 15000)
    info("rename prompt: mode %s, filetype %q, text %q", prompt.mode, prompt.filetype, prompt.text)
    assert(prompt.text == "line", "the prompt does not offer the name line: " .. vim.inspect(prompt))
    local function prompt_state()
      return string.format("mode %s, filetype %q, line %q, pumvisible %d, blink menu %s",
        vim.api.nvim_get_mode().mode, vim.bo.filetype, vim.api.nvim_get_current_line(), vim.fn.pumvisible(),
        tostring((blink_items())))
    end
    sleep(500)
    type_keys({ "<C-w>" })
    info("prompt after <C-w>: %s", prompt_state())
    type_keys({ "t", "e", "x", "t" })
    sleep(300)
    info("prompt after typing text: %s", prompt_state())
    vim.api.nvim_input("<CR>")
    local renamed_use = (fixed_use:gsub("var line", "var text"))
    local renamed_return = (fixed_return:gsub("return line", "return text"))
    local function renamed()
      local lines = vim.api.nvim_buf_get_lines(words_buf, use_line, use_line + 2, true)
      return lines[1] == renamed_use and lines[2] == renamed_return
    end
    local ok = pcall(wait_for, "both uses renamed", renamed, 10000)
    if not ok then
      local state = prompt_state()
      vim.api.nvim_input("<Esc><Esc>")
      sleep(300)
      vim.api.nvim_set_current_buf(words_buf)
      vim.api.nvim_buf_set_lines(words_buf, 0, -1, true, original_words)
      error("the rename was not applied after <CR>; then: " .. state, 0)
    end
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    assert(vim.api.nvim_get_current_buf() == words_buf, "the rename left words/Words.v3")
    vim.o.undolevels = vim.o.undolevels
    vim.api.nvim_input("u")
    wait_for("one undo to restore line", function()
      return vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), fixed)
    end, 5000)
    before = #O.published[words_uri]
    vim.api.nvim_input("u")
    wait_for("a second undo to restore the type error", function()
      return vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original_words)
    end, 5000)
    wait_for("the TypeError again", type_error_published(before), 20000)
    return string.format("prompt (%s, %s) offered %q; both uses became text; two undos restored line, then the TypeError",
      prompt.mode, prompt.filetype, prompt.text)
  end)

  -- Type "(" as a user does, with LazyVim's plugins loaded: mini.pairs closes
  -- it, and noice asks for signature help on the server's trigger character.
  step("signature help after typing ( (noice auto-open)", function()
    local triggers = (client.server_capabilities.signatureHelpProvider or {}).triggerCharacters
    info("signatureHelpProvider: %s", vim.inspect(client.server_capabilities.signatureHelpProvider))
    info("blink.cmp signature.enabled = %s; noice lsp.signature.enabled = %s",
      tostring(vim.tbl_get(package.loaded["blink.cmp.config"] or {}, "signature", "enabled")),
      tostring(vim.tbl_get(package.loaded["noice.config"] or {}, "options", "lsp", "signature", "enabled")))
    vim.api.nvim_win_set_cursor(0, { use_line + 2, 0 })
    local before_type = #O.published[words_uri]
    vim.api.nvim_input("O")
    wait_for("Insert mode", function() return vim.api.nvim_get_mode().mode == "i" end)
    vim.api.nvim_input("var t = StringBuilder.new().puts;<Left>")
    wait_for("the typed line", function() return vim.api.nvim_get_current_line():find("puts;$") end)
    sleep(300)
    local mark = #O.requests
    vim.api.nvim_input("(")
    local typed = wait_for("the typed (", function()
      local l = vim.api.nvim_get_current_line()
      return l:find("puts%(") and l
    end)
    info("line after typing (: %q, cursor %s, mode %s", typed, vim.inspect(vim.api.nvim_win_get_cursor(0)), vim.api.nvim_get_mode().mode)
    info("triggerCharacters: %s", vim.inspect(triggers))
    local window = wait_for("a signature window after typing (", function()
      local answered = false
      for i = mark + 1, #O.requests do
        answered = answered or O.requests[i].label == "def puts(s: Array<byte>) -> this"
      end
      return answered and float_with("def puts(s: Array<byte>) -> this")
    end, 15000)
    local callers = {}
    for i = mark + 1, #O.requests do
      local r = O.requests[i]
      callers[#callers + 1] = string.format("%s at %d:%d -> %s", r.caller, r.position.line, r.position.character,
        tostring(r.label))
    end
    info("signatureHelp requests after (: %s", table.concat(callers, "; "))
    info("signature window: %q", window)
    -- The same position through the client, for an exact reply.
    local cursor = vim.api.nvim_win_get_cursor(0)
    local help = request(client, "textDocument/signatureHelp", {
      textDocument = { uri = words_uri }, position = { line = cursor[1] - 1, character = cursor[2] },
    }, words_buf)
    local signature = help and help.signatures and help.signatures[1]
    assert(signature and signature.label == "def puts(s: Array<byte>) -> this" and help.activeParameter == 0,
      "unexpected signature help at the cursor: " .. vim.inspect(help))
    return string.format("%d request(s) from %s; window %q", #O.requests - mark, callers[1] or "?", window)
  end)

  vim.api.nvim_input("<Esc>")
  wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
  step("diagnostics: Words.v3 restored", function()
    local before = #O.published[words_uri]
    vim.api.nvim_buf_set_lines(words_buf, 0, -1, false, original_words)
    wait_for("the TypeError alone again", type_error_published(before), 20000)
  end)

  -- Each key from a closed popup: pressed while noice shows the signature, it
  -- focuses the window instead, as Neovim's own handler does. The cursor goes
  -- inside .puts(a), after the a, where entering Insert mode triggers nothing.
  local function signature_key(key)
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode and no signature window", function()
      return vim.api.nvim_get_mode().mode == "n" and not float_with("def puts(")
    end, 5000)
    vim.api.nvim_set_current_buf(words_buf)
    vim.api.nvim_win_set_cursor(0, { use_line + 1, puts - 1 + #".puts(a" })
    vim.api.nvim_input("i")
    wait_for("Insert mode", function() return vim.api.nvim_get_mode().mode == "i" end)
    sleep(300)
    local map = vim.fn.maparg(key, "i", false, true)
    local mark = #O.requests
    vim.api.nvim_input(key)
    local answered = wait_for("an answered signatureHelp request after " .. key, function()
      for i = mark + 1, #O.requests do
        if O.requests[i].result ~= nil or O.requests[i].err then return O.requests[i] end
      end
    end, 10000)
    assert(answered.label == "def puts(s: Array<byte>) -> this",
      "unexpected reply: " .. vim.inspect({ answered.err, answered.result }))
    wait_for("a signature window after " .. key, function() return float_with("def puts(s: Array<byte>) -> this") end, 5000)
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    return string.format("mapping %q (buffer=%s); request from %s; window shows the label",
      tostring(map.desc or map.rhs or "default"), tostring(map.buffer), answered.caller)
  end
  step("signature help with <C-k> in Insert mode (LazyVim/blink mapping)", function() return signature_key("<C-k>") end)
  -- Neovim 0.11 maps <C-s> in Insert mode to signature help, but LazyVim
  -- maps it to :w (lazyvim/config/keymaps.lua), so it isn't pressed here.
  step("<C-s> in Insert mode is LazyVim's Save File, not signature help", function()
    local map = vim.fn.maparg("<C-s>", "i", false, true)
    assert(map.desc == "Save File", "unexpected Insert <C-s> mapping: " .. vim.inspect(map))
    return "desc " .. vim.inspect(map.desc) .. ", rhs " .. vim.inspect(map.rhs)
  end)
  step("signature help with gK in Normal mode (LazyVim mapping)", function()
    wait_for("no signature window", function() return not float_with("def puts(") end, 5000)
    vim.api.nvim_win_set_cursor(0, { use_line + 1, puts - 1 + #".puts(" })
    local mark = #O.requests
    vim.api.nvim_input("gK")
    local answered = wait_for("an answered request after gK", function()
      for i = mark + 1, #O.requests do
        if O.requests[i].label then return O.requests[i] end
      end
    end, 10000)
    wait_for("a signature window after gK", function() return float_with("def puts(s: Array<byte>) -> this") end, 5000)
    vim.api.nvim_input("<Esc>")
    return "request from " .. answered.caller
  end)

  -- Completion as blink.cmp shows it while typing, as in step 9 of the
  -- procedure: a new line below line 7, a keyword, a name, and a member,
  -- each accepted with Enter (LazyVim's blink.cmp preset) and undone with
  -- u after Esc. The buffer is saved first, so "unmodified" is checkable.
  vim.api.nvim_set_current_buf(words_buf)
  vim.cmd("silent write")
  local function open_line()
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    if vim.fn.bufwinid(words_buf) ~= -1 then
      vim.api.nvim_set_current_win(vim.fn.bufwinid(words_buf))
    else
      vim.api.nvim_set_current_buf(words_buf)
    end
    if not vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original_words) then
      emit("WARN words/Words.v3 was left changed by an earlier step; restored before this one")
      vim.api.nvim_buf_set_lines(words_buf, 0, -1, true, original_words)
      vim.api.nvim_buf_call(words_buf, function() vim.cmd("silent write") end)
    end
    vim.api.nvim_win_set_cursor(0, { use_line + 1, 0 })
    vim.api.nvim_input("o")
    wait_for("Insert mode on a new line", function()
      return vim.api.nvim_get_mode().mode == "i" and vim.api.nvim_win_get_cursor(0)[1] == use_line + 2
    end)
  end
  -- Presses u until the buffer is the saved original again, at most four
  -- times, and returns the number of presses and the lines after each. If
  -- that fails, restores the buffer, so that later steps start from it.
  local function undo_to_original()
    local seen = {}
    local win = vim.fn.bufwinid(words_buf)
    if vim.api.nvim_get_current_win() ~= win then
      emit("WARN focus was in another window before undo; returned to words/Words.v3")
      vim.api.nvim_input("<Esc>")
      sleep(200)
      vim.api.nvim_set_current_win(win)
    end
    for presses = 1, 4 do
      vim.api.nvim_input("u")
      local restored = pcall(wait_for, "an undo", function()
        return vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original_words)
      end, 1500)
      local changed = vim.api.nvim_buf_get_lines(words_buf, use_line + 1, use_line + 2, true)[1]
      seen[#seen + 1] = string.format("%q", changed)
      if restored then return presses, table.concat(seen, ", ") end
    end
    vim.api.nvim_buf_set_lines(words_buf, 0, -1, true, original_words)
    vim.api.nvim_buf_call(words_buf, function() vim.cmd("silent write") end)
    error("four undos did not restore the buffer; line 8 after each: " .. table.concat(seen, ", "), 0)
  end
  local function accept_and_undo(expected)
    vim.api.nvim_input("<CR>")
    local accepted = wait_for("the accepted item on the line", function()
      local l = vim.api.nvim_buf_get_lines(words_buf, use_line + 1, use_line + 2, true)[1]
      return l:match("^%s*" .. vim.pesc(expected) .. "$") and l
    end, 5000)
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    local presses, seen = undo_to_original()
    local modified = vim.bo[words_buf].modified
    -- After blink.cmp accepts an item, the first u brings back the typed
    -- prefix and a second removes the line (docs/manual-testing.md, step 9).
    if presses > 2 or modified then
      emit(string.format("WARN undo after accepting %q: %d press(es) of u (line 8 after each: %s); modified %s",
        expected, presses, seen, tostring(modified)))
    else
      info("undo after accepting %q: %d press(es) of u (line 8 after each: %s)", expected, presses, seen)
    end
    return accepted, presses, modified
  end
  local function asked(mark)
    local texts = {}
    for i = mark + 1, #O.completions do
      local r = O.completions[i]
      local result = r.result
      local items = result and (result.items or result) or {}
      texts[#texts + 1] = string.format("%d:%d%s -> %s", r.position.line, r.position.character,
        r.context and r.context.triggerCharacter and (" after " .. r.context.triggerCharacter) or "",
        r.err and ("error " .. vim.inspect(r.err)) or r.answered and (#items .. " item(s)") or "no reply yet")
    end
    return #O.completions - mark, table.concat(texts, "; ")
  end

  step("completion: a keyword as you type w, then whi (blink.cmp)", function()
    open_line()
    local mark = #O.completions
    type_keys({ "w" })
    local items = wait_for("blink.cmp's menu with the keyword while from the server", function()
      local visible, list = blink_items()
      for _, item in ipairs(list) do
        if item.source_id == "lsp" and item.label == "while"
          and item.kind == vim.lsp.protocol.CompletionItemKind.Keyword then return list end
      end
    end, 15000)
    info("menu after w: %s", items_text(items))
    type_keys({ "h", "i" })
    items = wait_for("the menu narrowed to while", function()
      local visible, list = blink_items()
      return visible and #list > 0 and list[1].label == "while" and list[1].source_id == "lsp"
        and not vim.tbl_contains(vim.tbl_map(function(i) return i.label end, list), "return") and list
    end, 10000)
    info("menu after whi: %s", items_text(items))
    local count, requests = asked(mark)
    info("completion requests: %d (%s)", count, requests)
    local line, presses, modified = accept_and_undo("while")
    return string.format("menu after whi starts with %s; Enter gave %q; Esc and %d u restored the file (modified %s)",
      item_text(items[1]), line, presses, tostring(modified))
  end)

  step("completion: the local line first as you type li (blink.cmp)", function()
    open_line()
    local mark = #O.completions
    type_keys({ "l", "i" })
    local items = wait_for("blink.cmp's menu with the local line from the server", function()
      local visible, list = blink_items()
      for _, item in ipairs(list) do
        if item.source_id == "lsp" and item.label == "line" then return list end
      end
    end, 15000)
    info("menu after li: %s", items_text(items))
    local first = items[1]
    assert(first.label == "line" and first.source_id == "lsp"
      and first.kind == vim.lsp.protocol.CompletionItemKind.Variable and first.detail == "var line: int",
      "the menu does not start with the server's line, a variable with var line: int: " .. items_text(items))
    local count, requests = asked(mark)
    info("completion requests: %d (%s)", count, requests)
    local line, presses, modified = accept_and_undo("line")
    return string.format("menu starts with %s; Enter gave %q; Esc and %d u restored the file (modified %s)",
      item_text(first), line, presses, tostring(modified))
  end)

  step("completion: members as you type a. (blink.cmp)", function()
    open_line()
    type_keys({ "a" })
    local mark = #O.completions
    type_keys({ "." })
    local items = wait_for("blink.cmp's menu with the member length from the server", function()
      local visible, list = blink_items()
      for _, item in ipairs(list) do
        if item.source_id == "lsp" and item.label == "length" then return list end
      end
    end, 15000)
    info("menu after a.: %s", items_text(items))
    local from_server = lsp_items(items)
    assert(#from_server == 1 and from_server[1].kind == vim.lsp.protocol.CompletionItemKind.Field
      and from_server[1].detail == "def length: int",
      "the server's items after a. are not just the field length: " .. items_text(from_server))
    local count, requests = asked(mark)
    info("completion requests after the .: %d (%s)", count, requests)
    local triggered = false
    for i = mark + 1, #O.completions do
      local c = O.completions[i].context
      triggered = triggered or (c and c.triggerKind == 2 and c.triggerCharacter == ".")
    end
    assert(triggered, "blink.cmp did not ask with the trigger character .")
    local line, presses, modified = accept_and_undo("a.length")
    return string.format("server items: %s (menu: %d items); Enter gave %q; Esc and %d u restored the file (modified %s)",
      items_text(from_server), #items, line, presses, tostring(modified))
  end)

  step("completion: nothing from the server in a comment", function()
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    vim.api.nvim_win_set_cursor(0, { 3, 0 })
    vim.api.nvim_input("A")
    wait_for("Insert mode", function() return vim.api.nvim_get_mode().mode == "i" end)
    local mark = #O.completions
    type_keys({ " ", "S", "t", "r" })
    sleep(1500)
    local _, after_word = blink_items()
    type_keys({ "." })
    sleep(1500)
    local _, after_dot = blink_items()
    local count, requests = asked(mark)
    info("completion requests in the comment: %d (%s)", count, requests)
    info("menu after Str: %s; after .: %s", items_text(after_word), items_text(after_dot))
    assert(#lsp_items(after_word) == 0 and #lsp_items(after_dot) == 0, "the server offered items in a comment")
    for i = mark + 1, #O.completions do
      local r = O.completions[i].result
      assert(r ~= nil, "a completion request in the comment had no reply")
      assert(#(r.items or r) == 0, "the server replied with items in a comment: " .. vim.inspect(r))
    end
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    undo_to_original()
    return string.format("%d request(s), all answered with no items; the menu showed only other sources", count)
  end)

  -- Signature help while a call is still being typed. mini.pairs closes the
  -- ( as it is typed; <Del> removes that ), so the call has no ) at all, as
  -- in Util.add(1, | . noice asks on the trigger characters ( and ,.
  local function signature_window(label, mark)
    local answered = wait_for("an answered signatureHelp request with " .. label, function()
      for i = mark + 1, #O.requests do
        local r = O.requests[i]
        if r.label and r.label:find(label, 1, true) then return r end
      end
    end, 15000)
    local window = wait_for("a signature window with " .. label, function() return float_with(label) end, 5000)
    return answered, window
  end
  local function active_parameter_text()
    for _, win in ipairs(vim.api.nvim_list_wins()) do
      local ok, config = pcall(vim.api.nvim_win_get_config, win)
      if ok and config.relative ~= "" then
        local buf = vim.api.nvim_win_get_buf(win)
        for _, mark in ipairs(vim.api.nvim_buf_get_extmarks(buf, -1, 0, -1, { details = true })) do
          local d = mark[4]
          if d.hl_group and tostring(d.hl_group):find("ActiveParameter") and d.end_col then
            local row = vim.api.nvim_buf_get_lines(buf, mark[2], mark[2] + 1, false)[1] or ""
            return row:sub(mark[3] + 1, d.end_col)
          end
        end
      end
    end
  end
  local function help_at_cursor()
    local cursor = vim.api.nvim_win_get_cursor(0)
    return request(client, "textDocument/signatureHelp", {
      textDocument = { uri = words_uri }, position = { line = cursor[1] - 1, character = cursor[2] },
    }, words_buf)
  end

  step("signature help while typing a call with no ) (noice on , and <C-k>)", function()
    open_line()
    type_keys(vim.split("var n = Ints.max(", ""))
    local closed = wait_for("mini.pairs's )", function()
      local l = vim.api.nvim_get_current_line()
      return l:find("Ints%.max%(%)$") and l
    end, 5000)
    info("line after typing (: %q", closed)
    type_keys({ "<Del>" })
    wait_for("the call with no )", function() return vim.api.nvim_get_current_line():find("Ints%.max%($") end, 5000)
    type_keys({ "1" })
    local mark = #O.requests
    type_keys({ "," })
    local answered, window = signature_window("max(a: int, b: int)", mark)
    local active = active_parameter_text()
    info("after ,: request from %s at %d:%d; window %q; highlighted %q", answered.caller,
      answered.position.line, answered.position.character, window, tostring(active))
    type_keys({ " " })
    local typed = vim.api.nvim_get_current_line()
    assert(typed:find("Ints%.max%(1, $"), "unexpected line: " .. typed)
    local help = help_at_cursor()
    local signature = help and help.signatures and help.signatures[1]
    assert(signature and signature.label:find("max(a: int, b: int)", 1, true) and help.activeParameter == 1
      and #signature.parameters == 2, "unexpected signature help at the cursor: " .. vim.inspect(help))
    -- <C-k> from a closed window, as a user would press it. noice keeps the
    -- window after <Esc>, and hides it when the cursor moves, as for hover.
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    vim.api.nvim_input("0")
    wait_for("no signature window after moving the cursor", function()
      return not float_with("max(a: int, b: int)")
    end, 5000)
    vim.api.nvim_input("A")
    wait_for("Insert mode at the end of the line", function() return vim.api.nvim_get_mode().mode == "i" end)
    sleep(300)
    mark = #O.requests
    vim.api.nvim_input("<C-k>")
    local by_key, key_window = signature_window("max(a: int, b: int)", mark)
    info("after <C-k>: request from %s; window %q; highlighted %q; focus in the signature window: %s", by_key.caller,
      key_window, tostring(active_parameter_text()), tostring(vim.api.nvim_get_current_win() ~= vim.fn.bufwinid(words_buf)))
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    undo_to_original()
    assert(active == "b: int", "the window after , does not highlight b: int: " .. tostring(active))
    return string.format("line %q: %q, active parameter %d (highlighted %q); <C-k> showed it again",
      typed, signature.label, help.activeParameter, tostring(active))
  end)

  step("signature help in a call that mini.pairs closed (noice on ,)", function()
    open_line()
    type_keys(vim.split("var n = Ints.max(1", ""))
    local mark = #O.requests
    type_keys({ "," })
    local answered, window = signature_window("max(a: int, b: int)", mark)
    type_keys({ " " })
    local typed = vim.api.nvim_get_current_line()
    assert(typed:find("Ints%.max%(1, %)$"), "unexpected line: " .. typed)
    local help = help_at_cursor()
    assert(help and help.signatures and help.signatures[1].label:find("max(a: int, b: int)", 1, true)
      and help.activeParameter == 1, "unexpected signature help at the cursor: " .. vim.inspect(help))
    local active = active_parameter_text()
    vim.api.nvim_input("<Esc>")
    wait_for("Normal mode", function() return vim.api.nvim_get_mode().mode == "n" end)
    undo_to_original()
    assert(active == "b: int", "the window does not highlight b: int: " .. tostring(active))
    return string.format("line %q; request from %s; window %q, highlighted %q; active parameter %d",
      typed, answered.caller, window, tostring(active), help.activeParameter)
  end)

  -- A client that declares dynamic registration of file watching gets the
  -- server's watchers, and the server notices the project file on disk by
  -- itself. Neovim declares it on macOS, not on Linux; without it, the server
  -- notices when a .v3 buffer next changes (editors/nvim/README.md, "Changes
  -- on disk").
  local watched = vim.tbl_get(client.capabilities or {}, "workspace", "didChangeWatchedFiles",
    "dynamicRegistration") == true
  local function unknown_field()
    return latest(config_uri) and codes(latest(config_uri)) == "UnknownField"
  end
  -- Adds "bogus" to the project file on disk, calls check, and restores the
  -- file, and the .v3 line that check may have added; then waits for the
  -- diagnostics to clear and for the TypeError again.
  local function edit_on_disk(check)
    local path = vim.fs.joinpath(root, ".virgil-lsp.json")
    local saved = vim.fn.readfile(path)
    local edited = vim.list_extend({ saved[1], '  "bogus": 1,' }, vim.list_slice(saved, 2))
    local touched = false
    vim.api.nvim_set_current_buf(words_buf)
    -- An analysis still running for an earlier edit reads the project file
    -- again, so the edit waits for the server to publish for the buffer's
    -- current text. The project file has never been open in a buffer: closing
    -- one also makes the server read it again.
    wait_for("the TypeError for the current text of words/Words.v3", function()
      local last = type_error_published()()
      return last and last.version == vim.lsp.util.buf_versions[words_buf]
    end, 20000)
    -- Let the preceding cursor move finish snacks.nvim words and hint refresh
    -- requests. Semantic requests also reread configuration on use, independently
    -- of file watching, so they must finish before this negative watcher check.
    sleep(1000)
    request(client, "workspace/symbol", { query = "" }, words_buf)
    assert(#latest(config_uri).diagnostics == 0,
      "configuration diagnostics before the on-disk edit: " .. codes(latest(config_uri)))
    vim.fn.writefile(edited, path)
    local ok, err = pcall(check, function()
      vim.api.nvim_buf_set_lines(words_buf, -1, -1, false, { "// touched" })
      touched = true
    end)
    vim.fn.writefile(saved, path)
    if touched then
      vim.api.nvim_buf_set_lines(words_buf, -2, -1, false, {})
    end
    assert(ok, err)
    wait_for("cleared configuration diagnostics after the on-disk undo", function()
      return #latest(config_uri).diagnostics == 0
    end, 20000)
    wait_for("the TypeError again", type_error_published(), 20000)
  end
  if watched then
    step("project file edits (on disk, noticed through the client's file watching)", function()
      edit_on_disk(function()
        wait_for("UnknownField after the on-disk edit, with no .v3 change", unknown_field, 20000)
      end)
      return "the client declares workspace.didChangeWatchedFiles.dynamicRegistration; "
        .. "UnknownField appeared and cleared with no .v3 change"
    end)
  else
    step("project file edits (on disk, noticed on the next .v3 change)", function()
      edit_on_disk(function(touch)
        sleep(3000)
        assert(not unknown_field(), "UnknownField appeared before a .v3 change, although the client "
          .. "doesn't declare file watching")
        touch()
        wait_for("UnknownField after the on-disk edit and a .v3 change", unknown_field, 20000)
      end)
      return "the client doesn't declare workspace.didChangeWatchedFiles.dynamicRegistration; "
        .. "UnknownField appeared only after a .v3 change"
    end)
  end

  step("project file edits (unsaved)", function()
    vim.cmd.edit(vim.fs.joinpath(root, "package.json"))
    local other_json = vim.api.nvim_get_current_buf()
    vim.cmd.edit(vim.fs.joinpath(root, ".virgil-lsp.json"))
    local config_buf = vim.api.nvim_get_current_buf()
    wait_for("client attachment to the project file", function()
      return vim.lsp.buf_is_attached(config_buf, client.id)
    end)
    assert(#vim.lsp.get_clients({ bufnr = other_json }) == 0,
      "a client attached to package.json: " .. vim.inspect(vim.tbl_map(function(c) return c.name end, vim.lsp.get_clients({ bufnr = other_json }))))
    vim.api.nvim_buf_set_lines(config_buf, 1, 1, false, { '  "bogus": 1,' })
    wait_for("UnknownField after an unsaved project file edit", function()
      return codes(latest(config_uri)) == "UnknownField"
    end)
    local d = latest(config_uri).diagnostics[1]
    assert(d.range.start.line == 1, "UnknownField not on the added line: " .. vim.inspect(d.range))
    vim.cmd("silent undo")
    wait_for("cleared configuration diagnostics after undoing the edit", function()
      return #latest(config_uri).diagnostics == 0
    end)
    vim.cmd("bwipeout! " .. other_json)
    vim.cmd("bwipeout! " .. config_buf)
    return "UnknownField on line 2, cleared on undo; package.json stays detached"
  end)

  -- Neovim 0.12 has a built-in :lsp, and nvim-lspconfig then defines no :Lsp*
  -- commands. A bare :lsp restart restarts only the clients attached to the
  -- current buffer, here not the library file's own client; a bare
  -- :LspRestart restarts every client, and on Neovim 0.11 stops each of two
  -- clients twice, so that Neovim ends the server with signal 15.
  local builtin_lsp = vim.fn.exists(":lsp") == 2
  local restart_command = builtin_lsp and "lsp restart" or "LspRestart"

  -- Returns the new client on words/Words.v3. Neovim itself only warns when
  -- code ~= 0 or signal is neither 0 nor 15 (vim/lsp.lua), so strict asks
  -- for code 0 and signal 0. A bare :lsp restart must leave the other
  -- clients running.
  local function restart(command, strict)
    assert(builtin_lsp or vim.fn.exists(":LspRestart") == 2,
      "neither Neovim's :lsp nor nvim-lspconfig's :LspRestart exists")
    vim.api.nvim_set_current_buf(words_buf)
    local old, others = {}, {}
    local current_only = command == "lsp restart"
    local current = {}
    for _, c in ipairs(virgil_clients({ bufnr = vim.api.nvim_get_current_buf() })) do current[c.id] = true end
    for _, c in ipairs(virgil_clients()) do
      if current_only and not current[c.id] then others[c.id] = true else old[c.id] = true end
    end
    info("%s with clients %s%s", command, table.concat(vim.tbl_keys(old), ","),
      next(others) and (", leaving " .. table.concat(vim.tbl_keys(others), ",")) or "")
    local before = #O.published[words_uri]
    vim.cmd(command)
    wait_for("old clients to exit", function()
      for id in pairs(old) do
        if not O.exits[id] then return false end
      end
      return true
    end, 15000)
    local statuses = {}
    for id in pairs(old) do
      local e = O.exits[id]
      statuses[#statuses + 1] = string.format("%d: code %d signal %d", id, e.code, e.signal)
      assert(e.code == 0 and (e.signal == 0 or (not strict and e.signal == 15)),
        "old client " .. id .. " exited uncleanly: " .. vim.inspect(e))
    end
    local new = wait_for("a new client on words/Words.v3", function()
      local c = virgil_clients({ bufnr = words_buf })[1]
      return c and not old[c.id] and c.initialized and c
    end, 20000)
    assert(vim.lsp.buf_is_attached(bufnr, new.id), "bad.v3 not re-attached")
    wait_for("the TypeError from the new client", function()
      local list = O.published[words_uri]
      return type_error_published(before)() and list[#list].client_id == new.id
    end, 40000)
    for id in pairs(others) do
      local c = vim.lsp.get_client_by_id(id)
      assert(not O.exits[id] and c and not c:is_stopped(), "client " .. id .. " of another buffer was stopped")
    end
    client = new
    return string.format("old clients exited (%s); new client %d re-attached, TypeError republished%s",
      table.concat(statuses, "; "), new.id,
      next(others) and ("; clients of other buffers kept running (" .. table.concat(vim.tbl_keys(others), ",") .. ")") or "")
  end
  if builtin_lsp then
    step("restart (:lsp restart, Neovim's built-in command, current buffer's clients)", function()
      return restart("lsp restart", false)
    end)
  else
    step("restart (:LspRestart, nvim-lspconfig's command, as documented)", function()
      return restart("LspRestart", false)
    end)
  end
  step(string.format("restart (:%s virgil_lsp, clean exit required)", restart_command), function()
    return restart(restart_command .. " virgil_lsp", true)
  end)

  step("clean server exit", function()
    local ids = {}
    for _, c in ipairs(virgil_clients()) do
      ids[#ids + 1] = c.id
      c:stop()
    end
    wait_for("server exit", function()
      for _, id in ipairs(ids) do
        if not O.exits[id] then return false end
      end
      return true
    end)
    for _, id in ipairs(ids) do
      assert(O.exits[id].code == 0 and O.exits[id].signal == 0,
        "client " .. id .. " did not exit cleanly: " .. vim.inspect(O.exits[id]))
    end
    wait_for("client removal", function() return #virgil_clients() == 0 end)
    return "clients " .. table.concat(ids, ",") .. " exited with code 0, signal 0"
  end)
end

local function noroot_checks()
  local bufnr = vim.api.nvim_get_current_buf()
  local uri = vim.uri_from_bufnr(bufnr)
  local client
  step("no root: attach to .virgil-lsp.json", function()
    assert(vim.env.VIRGIL_LOC == nil, "VIRGIL_LOC is set")
    client = wait_for("client attachment", function()
      local c = virgil_clients({ bufnr = bufnr })[1]
      return c and c.initialized and c
    end, 20000)
    info("init_options: %s", vim.inspect(client.config.init_options))
  end)
  step("no root: MissingVirgilRoot diagnostic", function()
    local last = wait_for("MissingVirgilRoot", function()
      local l = latest(uri)
      return l and codes(l):find("MissingVirgilRoot") and l
    end, 20000)
    local d = last.diagnostics[1]
    local line = vim.api.nvim_buf_get_lines(bufnr, d.range.start.line, d.range.start.line + 1, true)[1]
    assert(line:find("virgilDependencies", 1, true), "MissingVirgilRoot not on the virgilDependencies line: " .. line)
    assert(#vim.diagnostic.get(bufnr) > 0, "not in vim.diagnostic")
    return string.format("%s on line %d: %s", d.code, d.range.start.line + 1, d.message)
  end)
  local function warning_shown()
    local handler = vim.lsp.handlers["window/showMessage"]
    info("window/showMessage handler: %s", handler and debug.getinfo(handler, "S").short_src or "nil")
    local shown = wait_for("the Invalid project file warning in noice's history", function()
      for _, m in ipairs(noice_messages()) do
        if m.text:find("Invalid project file", 1, true) then return m end
      end
    end, 5000)
    return string.format("noice %s/%s [%s] %q", tostring(shown.event), tostring(shown.kind),
      tostring(shown.level), shown.text)
  end
  if noice_workaround then
    step("no root: Invalid project file warning shown", warning_shown)
  else
    -- noice sets the handler to its _on_message, which is nil, so it drops
    -- every server's window/showMessage. The noroot-noice-workaround run
    -- checks the warning with noice's LSP message handler turned off.
    expected_failure("no root: Invalid project file warning shown",
      string.format("noice.nvim %s drops window/showMessage (folke/noice.nvim#1074)",
        locked_commit("noice.nvim")),
      warning_shown)
  end
  step("no root: clean server exit", function()
    client:stop()
    wait_for("server exit", function() return O.exits[client.id] end)
    assert(O.exits[client.id].code == 0 and O.exits[client.id].signal == 0, vim.inspect(O.exits[client.id]))
  end)
end

co = coroutine.create(function()
  local ok, err = xpcall(function()
    lazy_info()
    startup_checks()
    sleep(500)
    if mode == "noroot" then noroot_checks() else main_checks() end
    errors_seen()
  end, debug.traceback)
  if not ok then
    failures = failures + 1
    emit("FAIL driver: " .. tostring(err))
    capture_screen("FAIL driver")
  end
  emit(string.format("DONE %d passed, %d failed, %d expected failure(s)", passes, failures, expected_failures))
  out:close()
  vim.cmd(failures == 0 and "qa!" or "cquit 1")
end)

vim.schedule(function()
  local ok, err = coroutine.resume(co)
  if not ok then
    emit("FAIL driver: " .. tostring(err))
    capture_screen("FAIL driver")
    vim.cmd("cquit 1")
  end
end)
