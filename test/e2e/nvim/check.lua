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
-- Publications in arrival order, by document URI.
local published = vim.defaulttable(function() return {} end)
local publish = vim.lsp.handlers["textDocument/publishDiagnostics"]
vim.lsp.handlers["textDocument/publishDiagnostics"] = function(err, result, ctx, config)
  if result then
    table.insert(published[result.uri], result)
  end
  return publish(err, result, ctx, config)
end

local client
local function wait_for(description, predicate, timeout)
  assert(vim.wait(timeout or 10000, predicate, 10), "timed out waiting for " .. description)
end

local function latest(target)
  local list = published[target]
  return list[#list]
end

local function codes(publication)
  local result = {}
  for _, d in ipairs(publication.diagnostics) do
    result[#result + 1] = d.code
  end
  return table.concat(result, ",")
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
  local publications = published[uri]
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
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, { "component C { def value = 1; }", "type V { case A(x: int); }" })
  wait_for("published empty diagnostics after the edit", function()
    local last = publications[#publications]
    return #publications > before_edit and #last.diagnostics == 0
      and last.version > initial.version and #vim.diagnostic.get(bufnr) == 0
  end)
  local outline = symbols()
  assert(#outline == 2 and outline[1].name == "C"
    and outline[1].kind == vim.lsp.protocol.SymbolKind.Namespace
    and outline[2].name == "V" and outline[2].kind == vim.lsp.protocol.SymbolKind.Class,
    "expected component C (kind 3) and variant V (kind 5): " .. vim.inspect(outline))
  -- A variant's case is its child, and selects its own name.
  local case = outline[2].children and outline[2].children[1]
  assert(#outline[2].children == 1 and case.name == "A"
    and case.kind == vim.lsp.protocol.SymbolKind.EnumMember
    and case.selectionRange.start.line == 1 and case.selectionRange.start.character == 14
    and case.selectionRange["end"].line == 1 and case.selectionRange["end"].character == 15,
    "expected case A (kind 22) of V: " .. vim.inspect(outline[2]))

  -- The fixture's project uses lib/util through virgilDependencies. run.sh sets
  -- VIRGIL_LOC to the pinned checkout, which the configuration passes on as
  -- initializationOptions.virgilRoot, so the project is valid and analyzed.
  local root = client.config.root_dir
  local config_uri = vim.uri_from_fname(vim.fs.joinpath(root, ".virgil-lsp.json"))
  assert(client.config.init_options.virgilRoot == vim.env.VIRGIL_LOC,
    "virgilRoot was not taken from VIRGIL_LOC: " .. vim.inspect(client.config.init_options))
  wait_for("published configuration diagnostics", function() return latest(config_uri) ~= nil end)
  assert(#latest(config_uri).diagnostics == 0,
    "unexpected configuration diagnostics: " .. codes(latest(config_uri)))
  vim.cmd.edit(vim.fs.joinpath(root, "words", "Words.v3"))
  local words_uri = vim.uri_from_bufnr(0)
  -- Only a program that resolves StringBuilder from lib/util has this single error.
  wait_for("semantic diagnostics for words/Words.v3", function()
    local last = latest(words_uri)
    return last ~= nil and #last.diagnostics > 0
  end, 40000)
  local semantic = latest(words_uri).diagnostics
  assert(#semantic == 1 and semantic[1].code == "TypeError"
    and semantic[1].message == "expected int in var initialization, got Array<byte>",
    "unexpected semantic diagnostics: " .. vim.inspect(semantic))

  -- Go to definition from that analysis: StringBuilder resolves to its class in
  -- the closed lib/util file, which the server reads again to convert the range.
  local words_buf = vim.api.nvim_get_current_buf()
  local use_line = 6
  -- The line is ASCII, so a byte index is a UTF-16 character offset.
  local start = vim.api.nvim_buf_get_lines(words_buf, use_line, use_line + 1, true)[1]:find("StringBuilder", 1, true)
  local response, err = client:request_sync("textDocument/definition", {
    textDocument = { uri = words_uri }, position = { line = use_line, character = start - 1 },
  }, 10000, words_buf)
  assert(response and not response.err, "definition failed: " .. vim.inspect(err or response.err))
  local location = response.result
  local library = vim.fs.joinpath(vim.env.VIRGIL_LOC, "lib", "util", "StringBuilder.v3")
  local class_line
  for i, text in ipairs(vim.fn.readfile(library)) do
    if not class_line and text:find("^class StringBuilder") then class_line = i - 1 end
  end
  assert(location and location.uri and vim.uri_to_fname(location.uri) == library
    and location.range.start.line == class_line and location.range.start.character == 6
    and location.range["end"].line == class_line and location.range["end"].character == 19,
    "unexpected definition: " .. vim.inspect(location))

  -- Hover over the same use shows the class's signature in a Virgil code block.
  response, err = client:request_sync("textDocument/hover", {
    textDocument = { uri = words_uri }, position = { line = use_line, character = start - 1 },
  }, 10000, words_buf)
  assert(response and not response.err, "hover failed: " .. vim.inspect(err or response.err))
  local hover = response.result
  assert(hover and hover.contents and hover.contents.kind == "markdown"
    and hover.contents.value == "```virgil\nclass StringBuilder\n```"
    and hover.range.start.line == use_line and hover.range.start.character == start - 1
    and hover.range["end"].line == use_line and hover.range["end"].character == start - 1 + #"StringBuilder",
    "unexpected hover: " .. vim.inspect(hover))

  -- Signature help just after the ( of .puts(a) on the same line shows the
  -- method's label with its one parameter as label offsets, which Neovim
  -- supports. The fixture's type error leaves the program parsed and analyzed.
  local puts = vim.api.nvim_buf_get_lines(words_buf, use_line, use_line + 1, true)[1]:find(".puts(", 1, true)
  response, err = client:request_sync("textDocument/signatureHelp", {
    textDocument = { uri = words_uri }, position = { line = use_line, character = puts - 1 + #".puts(" },
  }, 10000, words_buf)
  assert(response and not response.err, "signature help failed: " .. vim.inspect(err or response.err))
  local help = response.result
  local signature = help and help.signatures and help.signatures[1]
  assert(signature and #help.signatures == 1 and help.activeSignature == 0 and help.activeParameter == 0
    and signature.label == "def puts(s: Array<byte>) -> this"
    and #signature.parameters == 1 and vim.deep_equal(signature.parameters[1].label, { 9, 23 }),
    "unexpected signature help: " .. vim.inspect(help))

  -- Workspace symbols come from the same analysis: join, which nothing uses,
  -- is a method in the project's own words/Words.v3.
  response, err = client:request_sync("workspace/symbol", { query = "join" }, 10000, words_buf)
  assert(response and not response.err, "workspace/symbol failed: " .. vim.inspect(err or response.err))
  local symbol = response.result and response.result[1]
  assert(symbol and symbol.name == "join" and symbol.kind == vim.lsp.protocol.SymbolKind.Method
    and vim.uri_to_fname(symbol.location.uri) == vim.api.nvim_buf_get_name(words_buf)
    and symbol.location.range.start.line == 5 and symbol.location.range.start.character == 5
    and symbol.location.range["end"].line == 5 and symbol.location.range["end"].character == 9,
    "unexpected workspace symbol: " .. vim.inspect(symbol))

  -- The references of the same class, with its declaration: the use, the
  -- class's name, and uses in other closed lib/util files, which the server
  -- reads again. Each spells the name, in order of path and then position.
  response, err = client:request_sync("textDocument/references", {
    textDocument = { uri = words_uri }, position = { line = use_line, character = start - 1 },
    context = { includeDeclaration = true },
  }, 10000, words_buf)
  assert(response and not response.err, "references failed: " .. vim.inspect(err or response.err))
  local references = response.result or {}
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

  -- nvim.document-highlight.response: only this document's StringBuilder use.
  response, err = client:request_sync("textDocument/documentHighlight", {
    textDocument = { uri = words_uri }, position = { line = use_line, character = start - 1 },
  }, 10000, words_buf)
  assert(response and not response.err, "document highlights failed: " .. vim.inspect(err or response.err))
  assert(vim.deep_equal(response.result, {
    { kind = 2, range = {
      start = { line = use_line, character = start - 1 },
      ["end"] = { line = use_line, character = start - 1 + #"StringBuilder" },
    } },
  }), "unexpected document highlights: " .. vim.inspect(response.result))
  local highlight_uri = vim.uri_from_fname(vim.fs.joinpath(root, "words", "Highlights.v3"))
  response, err = client:request_sync("textDocument/documentHighlight", {
    textDocument = { uri = highlight_uri }, position = { line = 5, character = 2 },
  }, 10000, words_buf)
  assert(response and not response.err, "highlight kinds failed: " .. vim.inspect(err or response.err))
  local want = {}
  for _, spot in ipairs({ { 4, 6, 1 }, { 5, 2, 3 }, { 5, 10, 2 }, { 6, 2, 3 }, { 7, 9, 2 } }) do
    want[#want + 1] = { kind = spot[3], range = {
      start = { line = spot[1], character = spot[2] },
      ["end"] = { line = spot[1], character = spot[2] + #"count" },
    } }
  end
  assert(vim.deep_equal(response.result, want), "unexpected highlight kinds: " .. vim.inspect(response.result))
  print("PASS: nvim.document-highlight.response")

  -- Rename needs a program that verifies, so an unsaved edit fixes the type
  -- error first. The client's own rename, with its prepareRename and the
  -- server's versioned edit, renames both uses of the local line, and one
  -- undo restores them; a second restores the type error.
  local original = vim.api.nvim_buf_get_lines(words_buf, 0, -1, true)
  vim.api.nvim_buf_set_lines(words_buf, 6, 8, true, {
    "\t\tvar line = StringBuilder.new().puts(a).putc(' ').puts(b).toString();",
    "\t\treturn line.length;",
  })
  local fixed = vim.api.nvim_buf_get_lines(words_buf, 0, -1, true)
  -- Edits from one script share an undo block unless it is closed, as a
  -- user's separate commands are.
  vim.o.undolevels = vim.o.undolevels
  local notified, notify = {}, vim.notify
  vim.notify = function(message, ...)
    notified[#notified + 1] = message
    return notify(message, ...)
  end
  vim.api.nvim_win_set_cursor(0, { use_line + 1, 6 })
  vim.lsp.buf.rename("text", { bufnr = words_buf })
  assert(vim.wait(40000, function()
    local lines = vim.api.nvim_buf_get_lines(words_buf, 6, 8, true)
    return lines[1] == "\t\tvar text = StringBuilder.new().puts(a).putc(' ').puts(b).toString();"
      and lines[2] == "\t\treturn text.length;"
  end, 10), "the rename was not applied: " .. vim.inspect(notified))
  vim.notify = notify
  vim.o.undolevels = vim.o.undolevels
  vim.cmd("silent undo")
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), fixed),
    "undo did not restore the renamed local")
  vim.cmd("silent undo")
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original),
    "undo did not restore the type error")

  -- Completion takes its keywords from the unsaved text alone, which no
  -- longer parses: "whi" starts a statement, so the keywords of a statement
  -- are offered, and "while" replaces just the prefix. Neovim applies the
  -- item's edit, and one undo restores the line.
  vim.api.nvim_buf_set_lines(words_buf, 7, 7, true, { "\t\twhi" })
  local typed = vim.api.nvim_buf_get_lines(words_buf, 0, -1, true)
  vim.o.undolevels = vim.o.undolevels
  response, err = client:request_sync("textDocument/completion", {
    textDocument = { uri = words_uri }, position = { line = 7, character = 5 },
  }, 10000, words_buf)
  assert(response and not response.err, "completion failed: " .. vim.inspect(err or response.err))
  local list = response.result
  local item
  for _, i in ipairs(list and list.items or {}) do
    if i.label == "while" then item = i end
  end
  assert(list and list.isIncomplete == false and item and item.kind == vim.lsp.protocol.CompletionItemKind.Keyword
    and vim.deep_equal(item.textEdit, {
      newText = "while", range = { start = { line = 7, character = 2 }, ["end"] = { line = 7, character = 5 } },
    }), "unexpected completion: " .. vim.inspect(list))
  vim.lsp.util.apply_text_edits({ item.textEdit }, words_buf, client.offset_encoding)
  assert(vim.api.nvim_buf_get_lines(words_buf, 7, 8, true)[1] == "\t\twhile",
    "the completion was not applied: " .. vim.inspect(vim.api.nvim_buf_get_lines(words_buf, 7, 8, true)))
  vim.o.undolevels = vim.o.undolevels
  vim.cmd("silent undo")
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), typed),
    "undo did not restore the typed prefix")
  vim.api.nvim_buf_set_lines(words_buf, 7, 8, true, {})
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original),
    "the buffer was not restored")

  -- It also offers the declarations in scope, from an analysis of the
  -- project with the unsaved text, repaired at the cursor: the local "line"
  -- is nearest, so it is ranked first, then the parameters, "this", and the
  -- component's method. Its detail is its signature. Neovim applies the
  -- item's edit, and one undo restores the line.
  vim.api.nvim_buf_set_lines(words_buf, 7, 7, true, { "\t\tli" })
  typed = vim.api.nvim_buf_get_lines(words_buf, 0, -1, true)
  vim.o.undolevels = vim.o.undolevels
  response, err = client:request_sync("textDocument/completion", {
    textDocument = { uri = words_uri }, position = { line = 7, character = 4 },
  }, 20000, words_buf)
  assert(response and not response.err, "completion failed: " .. vim.inspect(err or response.err))
  list = response.result
  local labels = {}
  for k = 1, 5 do labels[k] = list and list.items[k] and list.items[k].label end
  item = list and list.items[1]
  assert(vim.deep_equal(labels, { "line", "a", "b", "this", "join" }) and list.isIncomplete == false
    and item.kind == vim.lsp.protocol.CompletionItemKind.Variable and item.detail == "var line: int"
    and item.sortText == "0000" and vim.deep_equal(item.textEdit, {
      newText = "line", range = { start = { line = 7, character = 2 }, ["end"] = { line = 7, character = 4 } },
    }), "unexpected declaration completion: " .. vim.inspect(list and { labels, item }))
  vim.lsp.util.apply_text_edits({ item.textEdit }, words_buf, client.offset_encoding)
  assert(vim.api.nvim_buf_get_lines(words_buf, 7, 8, true)[1] == "\t\tline",
    "the declaration completion was not applied: " .. vim.inspect(vim.api.nvim_buf_get_lines(words_buf, 7, 8, true)))
  vim.o.undolevels = vim.o.undolevels
  vim.cmd("silent undo")
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), typed),
    "undo did not restore the typed prefix")
  vim.api.nvim_buf_set_lines(words_buf, 7, 8, true, {})
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original),
    "the buffer was not restored")

  -- After ".", the members of the receiver's type alone, from an analysis of
  -- the project with the unsaved text, repaired after the ".": the parameter
  -- "a" is a string, whose one member is its length. Neovim applies the
  -- item's edit, which inserts after the ".", and one undo restores the line.
  vim.api.nvim_buf_set_lines(words_buf, 7, 7, true, { "\t\ta." })
  typed = vim.api.nvim_buf_get_lines(words_buf, 0, -1, true)
  vim.o.undolevels = vim.o.undolevels
  response, err = client:request_sync("textDocument/completion", {
    textDocument = { uri = words_uri }, position = { line = 7, character = 4 },
    context = { triggerKind = 2, triggerCharacter = "." },
  }, 20000, words_buf)
  assert(response and not response.err, "completion failed: " .. vim.inspect(err or response.err))
  list = response.result
  item = list and list.items[1]
  assert(list and #list.items == 1 and list.isIncomplete == false and item.label == "length"
    and item.kind == vim.lsp.protocol.CompletionItemKind.Field and item.detail == "def length: int"
    and item.sortText == "0000" and vim.deep_equal(item.textEdit, {
      newText = "length", range = { start = { line = 7, character = 4 }, ["end"] = { line = 7, character = 4 } },
    }), "unexpected member completion: " .. vim.inspect(list))
  vim.lsp.util.apply_text_edits({ item.textEdit }, words_buf, client.offset_encoding)
  assert(vim.api.nvim_buf_get_lines(words_buf, 7, 8, true)[1] == "\t\ta.length",
    "the member completion was not applied: " .. vim.inspect(vim.api.nvim_buf_get_lines(words_buf, 7, 8, true)))
  vim.o.undolevels = vim.o.undolevels
  vim.cmd("silent undo")
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), typed),
    "undo did not restore the typed receiver")
  vim.api.nvim_buf_set_lines(words_buf, 7, 8, true, {})
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(words_buf, 0, -1, true), original),
    "the buffer was not restored")

  -- Other JSON files stay with the editor. Project file buffers go to the same
  -- client, so its configuration diagnostics follow unsaved edits. The client
  -- attaches to the project file after the other JSON buffer has been handled.
  vim.cmd.edit(vim.fs.joinpath(root, "package.json"))
  local other_json = vim.api.nvim_get_current_buf()
  vim.cmd.edit(vim.fs.joinpath(root, ".virgil-lsp.json"))
  local config_buf = vim.api.nvim_get_current_buf()
  wait_for("client attachment to the project file", function()
    return vim.lsp.buf_is_attached(config_buf, client.id)
  end)
  assert(#vim.lsp.get_clients({ bufnr = other_json }) == 0, "client attached to package.json")
  vim.api.nvim_buf_set_lines(config_buf, 1, 1, false, { '  "bogus": 1,' })
  wait_for("UnknownField after an unsaved project file edit", function()
    return codes(latest(config_uri)) == "UnknownField"
  end)
  vim.api.nvim_buf_set_lines(config_buf, 1, 2, false, {})
  wait_for("cleared configuration diagnostics after undoing the edit", function()
    return #latest(config_uri).diagnostics == 0
  end)

  -- Graceful stop sends shutdown and exit; do not force-kill the success path.
  client:stop()
  wait_for("server exit", function() return exited ~= nil end)
  assert(exited.code == 0 and exited.signal == 0,
    "server did not exit cleanly: " .. vim.inspect(exited))
  wait_for("client removal", function() return vim.lsp.get_client_by_id(client.id) == nil end)
  print("PASS: Neovim attach, diagnostics publish/clear, symbols, Virgil root, definition, hover, signature help, references, document highlights, rename and undo, keyword, declaration, and member completion, workspace symbols, project file edits, and clean server exit")
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
