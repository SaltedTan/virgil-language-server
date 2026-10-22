// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Extension Host checks, started by run.cjs. They mirror test/e2e/nvim/check.lua
// and act only through VS Code APIs and commands, as a user's edits would.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');

const EXTENSION_ID = 'virgil-language-server.virgil-lsp-vscode';
const CHANNEL_LOG = 'Virgil Language Server.log';
const project = process.env.VIRGIL_E2E_PROJECT;

async function waitFor(description, predicate, timeout = 10000) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await predicate();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${description}`);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

// The "Virgil Language Server" output channel, as written to its log file.
function channelLog() {
  const logs = process.env.VIRGIL_E2E_LOGS;
  if (!fs.existsSync(logs)) return '';
  const file = fs.readdirSync(logs, { recursive: true }).find((name) => path.basename(name) === CHANNEL_LOG);
  return file === undefined ? '' : fs.readFileSync(path.join(logs, file), 'utf8');
}

// Returns a function that returns what the channel has logged since this call.
function logFromHere() {
  const start = channelLog().length;
  return () => channelLog().slice(start);
}

function count(text, substring) {
  return text.split(substring).length - 1;
}

function setting(name) {
  return vscode.workspace.getConfiguration().get(name);
}

// Changes a user setting, as the Settings editor does.
function setSetting(name, value) {
  return vscode.workspace.getConfiguration().update(name, value, vscode.ConfigurationTarget.Global);
}

async function open(...segments) {
  const document = await vscode.workspace.openTextDocument(path.join(project, ...segments));
  return vscode.window.showTextDocument(document);
}

function diagnostics(editor) {
  return vscode.languages.getDiagnostics(editor.document.uri);
}

function waitForDiagnostics(description, editor, timeout) {
  return waitFor(description, () => {
    const list = diagnostics(editor);
    return list.length > 0 && list;
  }, timeout);
}

function code(diagnostic) {
  return typeof diagnostic.code === 'object' ? diagnostic.code.value : diagnostic.code;
}

function range(item) {
  const { start, end } = item.range;
  return [start.line, start.character, end.line, end.character];
}

// The target of each Location that a definition request at line:character returns.
async function definitions(editor, line, character) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeDefinitionProvider', editor.document.uri, new vscode.Position(line, character),
  );
  return (result ?? []).map((location) => [location.uri?.fsPath, ...range(location)]);
}

// The text of each hover at line:character, and the range of each that has one.
async function hovers(editor, line, character) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeHoverProvider', editor.document.uri, new vscode.Position(line, character),
  );
  return (result ?? []).map((hover) => [
    hover.contents.map((content) => (typeof content === 'string' ? content : content.value)),
    hover.range && range(hover),
  ]);
}

// The active signature and parameter of the signature help at line:character,
// and each signature's label and parameter labels.
async function signatureHelp(editor, line, character) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeSignatureHelpProvider', editor.document.uri, new vscode.Position(line, character),
  );
  return result && {
    activeSignature: result.activeSignature,
    activeParameter: result.activeParameter,
    signatures: result.signatures.map((s) => [s.label, s.parameters.map((p) => p.label)]),
  };
}

// The name, kind, file, and range of each symbol that a workspace symbol query returns.
async function workspaceSymbols(query) {
  const result = await vscode.commands.executeCommand('vscode.executeWorkspaceSymbolProvider', query);
  return (result ?? []).map((symbol) => [symbol.name, symbol.kind, symbol.location.uri.fsPath, ...range(symbol.location)]);
}

// The file, range, and text of each Location that a references request at
// line:character returns.
async function references(editor, line, character) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeReferenceProvider', editor.document.uri, new vscode.Position(line, character),
  );
  return (result ?? []).map((location) => {
    const { start, end } = location.range;
    const text = fs.readFileSync(location.uri.fsPath, 'utf8').split('\n')[start.line];
    return {
      file: location.uri.fsPath,
      range: range(location),
      text: start.line === end.line ? text.slice(start.character, end.character) : null,
    };
  });
}

async function documentSymbols(editor) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeDocumentSymbolProvider', editor.document.uri,
  );
  return result ?? [];
}

async function symbols(editor) {
  return (await documentSymbols(editor)).map((symbol) => ({ name: symbol.name, kind: symbol.kind }));
}

// The children of the top-level symbol {name}, with the range of each one's name.
async function children(editor, name) {
  const symbol = (await documentSymbols(editor)).find((s) => s.name === name);
  return (symbol?.children ?? []).map((child) => ({
    name: child.name, kind: child.kind, selection: range({ range: child.selectionRange }),
  }));
}

async function edit(editor, change) {
  assert.ok(await editor.edit(change), `editing ${editor.document.fileName} failed`);
  assert.ok(editor.document.isDirty);
}

// Undo acts on the active editor, as Ctrl+Z does.
async function undo(editor, text) {
  await vscode.window.showTextDocument(editor.document);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo', () => editor.document.getText() === text && !editor.document.isDirty);
}

// Whether words/Words.v3 has its single type error, which only an analysis
// with lib/util from the Virgil root reports.
function hasTypeError(words) {
  const list = diagnostics(words);
  return list.length === 1 && code(list[0]) === 'TypeError' && list[0].range.start.line === 6;
}

// The fatal errors in the server's stderr, as the output channel logs it: each
// is an exception line, such as "!NullCheckException", and the stack frames
// under it, innermost first, without their timestamps.
function fatalErrors(text) {
  const errors = [];
  for (const line of text.split('\n')) {
    const message = /^\S+ \S+ \[error\] (.*)$/.exec(line)?.[1];
    if (message === undefined) continue;
    if (message.startsWith('!')) errors.push(message);
    else if (/^\s+in /.test(message) && errors.length > 0) errors[errors.length - 1] += `\n\t${message.trim()}`;
  }
  return errors;
}

// Throws, with the server's stderr, if the output channel's log shows that a
// server exited with an error since log began. The message has each distinct
// fatal error in full, so that a different one after a crash loop shows.
function expectNoServerError(log) {
  const text = log();
  if (/Server process exited with (code|signal)/.test(text)) {
    const errors = new Set(fatalErrors(text));
    const exits = new Set(text.match(/Server process exited with (code|signal) \S+/g));
    throw new Error(`a server exited with an error:\n${[...errors, ...exits].join('\n')}`);
  }
}

// Runs a scenario that changes a setting or a file on disk. restore undoes
// what is still changed and waits until a server serves the project as before,
// so that a scenario that fails is reported and the next one still runs. If
// restoring fails, the suite stops.
async function scenario(failures, name, check, restore) {
  let failure;
  try {
    await check();
  } catch (error) {
    failure = error;
  }
  try {
    await restore();
  } catch (error) {
    if (failure !== undefined) console.error(`FAIL: VS Code ${name}: ${failure.stack}`);
    throw new Error(`restoring the state after "${name}" failed: ${error.message}`);
  }
  if (failure === undefined) {
    console.log(`PASS: VS Code ${name}`);
  } else {
    failures.push(name);
    console.error(`FAIL: VS Code ${name}: ${failure.stack}`);
  }
}

async function expectParserDiagnostic(editor) {
  const list = await waitForDiagnostics('the parser diagnostic in bad.v3', editor);
  assert.equal(list.length, 1, 'expected exactly one parser diagnostic');
  const [diagnostic] = list;
  assert.equal(code(diagnostic), 'ParseError');
  assert.equal(diagnostic.source, 'aeneas');
  assert.equal(diagnostic.message, 'invalid start of expression');
  assert.equal(diagnostic.severity, vscode.DiagnosticSeverity.Error);
  // Aeneas reports a point before the semicolon, which stays an empty range.
  assert.deepEqual(range(diagnostic), [2, 26, 2, 26]);
}

// Opening a .v3 file without a folder activates the extension through the
// language it contributes.
async function singleFile(extension) {
  assert.equal(extension.isActive, false, 'the extension was active before a Virgil document opened');
  const bad = await open('bad.v3');
  assert.equal(bad.document.languageId, 'virgil');
  await waitFor('activation by a .v3 document', () => extension.isActive);
  await expectParserDiagnostic(bad);
  const server = vscode.workspace.getConfiguration().get('virgil.server.path');
  // The channel writes its log file asynchronously.
  await waitFor('the server to start from virgil.server.path', () => channelLog().includes(`Starting ${server} --stdio`));
  console.log('PASS: VS Code activation on a .v3 file and parser diagnostics');
}

async function workspace(extension) {
  const failures = [];
  // The project file activates the extension before any document opens.
  await waitFor('activation by the workspace project file', () => extension.isActive);
  assert.deepEqual(vscode.workspace.textDocuments.filter((d) => d.languageId === 'virgil'), []);
  await waitFor('the server to start', () => channelLog().includes('Started virgil-lsp '));
  const root = vscode.workspace.getConfiguration().get('virgil.virgilRoot');
  assert.ok(channelLog().includes(`Virgil root: ${root} (from virgil.virgilRoot)`),
    'the Virgil root was not taken from virgil.virgilRoot');

  const bad = await open('bad.v3');
  const original = bad.document.getText();
  await expectParserDiagnostic(bad);
  assert.deepEqual(await symbols(bad), [], 'broken source should have no document symbols');

  // An unsaved edit must reach the server through the client's didChange,
  // clear the diagnostic, and update the outline. Undo brings both back.
  await edit(bad, (builder) => builder.replace(
    new vscode.Range(0, 0, bad.document.lineCount, 0), 'component C { def value = 1; }\ntype V { case A(x: int); }',
  ));
  await waitFor('cleared diagnostics after an unsaved edit', () => diagnostics(bad).length === 0);
  assert.deepEqual(await symbols(bad), [
    { name: 'C', kind: vscode.SymbolKind.Namespace },
    { name: 'V', kind: vscode.SymbolKind.Class },
  ]);
  // A variant's case is its child, and selects its own name.
  assert.deepEqual(await children(bad, 'V'), [{ name: 'A', kind: vscode.SymbolKind.EnumMember, selection: [1, 14, 1, 15] }]);
  await undo(bad, original);
  await expectParserDiagnostic(bad);

  // Only a program that resolves StringBuilder from lib/util, through the
  // project's virgilDependencies and virgil.virgilRoot, has this single error.
  const words = await open('words', 'Words.v3');
  const semantic = await waitForDiagnostics('semantic diagnostics for words/Words.v3', words, 40000);
  assert.deepEqual(semantic.map((d) => [code(d), d.source, d.message, d.range.start.line]), [
    ['TypeError', 'aeneas', 'expected int in var initialization, got Array<byte>', 6],
  ]);

  // Go to definition from that analysis: StringBuilder resolves to its class in
  // the closed lib/util file, which the server reads again to convert the range.
  const use = 6;
  const library = path.join(root, 'lib', 'util', 'StringBuilder.v3');
  const declaration = fs.readFileSync(library, 'utf8').split('\n').findIndex((line) => line.startsWith('class StringBuilder'));
  assert.ok(declaration >= 0, `no StringBuilder class in ${library}`);
  const column = words.document.lineAt(use).text.indexOf('StringBuilder');
  assert.deepEqual(await definitions(words, use, column), [
    [library, declaration, 6, declaration, 19],
  ]);
  // Hover over the same use shows the class's signature in a Virgil code block.
  assert.deepEqual(await hovers(words, use, column), [
    [['```virgil\nclass StringBuilder\n```'], [use, column, use, column + 'StringBuilder'.length]],
  ]);
  // Signature help just after the ( of .puts(a) on the same line shows the
  // method's label with its one parameter as label offsets, which the client
  // supports. The fixture's type error leaves the program parsed and analyzed.
  const puts = words.document.lineAt(use).text.indexOf('.puts(') + '.puts('.length;
  assert.deepEqual(await signatureHelp(words, use, puts), {
    activeSignature: 0, activeParameter: 0, signatures: [['def puts(s: Array<byte>) -> this', [[9, 23]]]],
  });
  // Workspace symbols come from the same analysis: join, which nothing uses,
  // is a method in the project's own words/Words.v3.
  assert.deepEqual((await workspaceSymbols('join')).slice(0, 1), [
    ['join', vscode.SymbolKind.Method, words.document.uri.fsPath, 5, 5, 5, 9],
  ]);

  // The references of the same class include the use, the class's name, and
  // uses in other closed lib/util files, which the server reads again; each
  // spells the name.
  const uses = await references(words, use, column);
  const libraryDirectory = path.join(root, 'lib', 'util') + path.sep;
  assert.deepEqual(uses.filter((u) => u.file === words.document.uri.fsPath).map((u) => u.range), [
    [use, column, use, column + 'StringBuilder'.length],
  ]);
  assert.ok(uses.some((u) => u.file === library && u.range[0] === declaration && u.range[1] === 6),
    'the references do not include the class name');
  assert.deepEqual(uses.filter((u) => u.text !== 'StringBuilder'
    || (u.file !== words.document.uri.fsPath && !u.file.startsWith(libraryDirectory))), []);
  assert.ok(new Set(uses.map((u) => u.file)).size > 2, 'no references in other library files');

  // While the type error stands, the server refuses to rename the local line,
  // with its reason, both when VS Code prepares the rename, as F2 does, and
  // when it asks for the edit. F2 shows the reason beside the cursor.
  const refusal = { message: "the project's analysis has errors; rename needs a program that verifies" };
  await assert.rejects(vscode.commands.executeCommand(
    'vscode.prepareRename', words.document.uri, new vscode.Position(use, 6),
  ), refusal);
  await assert.rejects(vscode.commands.executeCommand(
    'vscode.executeDocumentRenameProvider', words.document.uri, new vscode.Position(use, 6), 'text',
  ), refusal);

  // Rename needs a program that verifies, so an unsaved edit fixes the type
  // error first. The server's versioned edit renames both uses of the local
  // line, VS Code applies it, and one undo restores them; a second restores
  // the type error.
  const unedited = words.document.getText();
  await edit(words, (builder) => builder.replace(new vscode.Range(use, 0, use + 2, 0),
    "\t\tvar line = StringBuilder.new().puts(a).putc(' ').puts(b).toString();\n\t\treturn line.length;\n"));
  const fixed = words.document.getText();
  const renaming = await vscode.commands.executeCommand(
    'vscode.executeDocumentRenameProvider', words.document.uri, new vscode.Position(use, 6), 'text',
  );
  assert.deepEqual(renaming.entries().map(([uri, edits]) => [uri.fsPath, edits.map((e) => [...range(e), e.newText])]), [
    [words.document.uri.fsPath, [[use, 6, use, 10, 'text'], [use + 1, 9, use + 1, 13, 'text']]],
  ]);
  assert.ok(await vscode.workspace.applyEdit(renaming), 'the rename was not applied');
  assert.equal(words.document.lineAt(use).text, "\t\tvar text = StringBuilder.new().puts(a).putc(' ').puts(b).toString();");
  assert.equal(words.document.lineAt(use + 1).text, '\t\treturn text.length;');
  await vscode.window.showTextDocument(words.document);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo of the rename', () => words.document.getText() === fixed);
  await undo(words, unedited);

  // Completion takes its keywords from the unsaved text alone, which no
  // longer parses: "whi" starts a statement, so the keywords of a statement
  // are offered, and "while" replaces just the prefix. VS Code applies the
  // item, and one undo restores the line.
  await edit(words, (builder) => builder.insert(new vscode.Position(use + 1, 0), '\t\twhi\n'));
  const typed = words.document.getText();
  const completions = await vscode.commands.executeCommand(
    'vscode.executeCompletionItemProvider', words.document.uri, new vscode.Position(use + 1, 5),
  );
  const keyword = completions.items.find((i) => i.label === 'while');
  assert.ok(keyword, 'while is not offered');
  const keywords = completions.items.filter((i) => i.kind === vscode.CompletionItemKind.Keyword);
  assert.deepEqual(keywords.map((i) => i.label), [
    'break', 'continue', 'def', 'false', 'for', 'fun', 'if', 'match', 'null', 'return', 'true', 'var', 'while',
  ]);
  assert.equal(keyword.kind, vscode.CompletionItemKind.Keyword);
  const replaced = keyword.range instanceof vscode.Range ? keyword.range : keyword.range.replacing;
  assert.deepEqual(range({ range: replaced }), [use + 1, 2, use + 1, 5]);
  const insertText = typeof keyword.insertText === 'string' ? keyword.insertText : keyword.insertText.value;
  assert.equal(insertText, 'while');
  await edit(words, (builder) => builder.replace(replaced, insertText));
  assert.equal(words.document.lineAt(use + 1).text, '\t\twhile');
  await vscode.window.showTextDocument(words.document);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo of the completion', () => words.document.getText() === typed);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo of the prefix', () => words.document.getText() === unedited && !words.document.isDirty);

  // It also offers the declarations in scope, from an analysis of the
  // project with the unsaved text, repaired at the cursor: the local "line"
  // is nearest, so it is ranked first, then the parameters, "this", and the
  // component's method. Its detail is its signature. VS Code applies the
  // item, and one undo restores the line.
  await edit(words, (builder) => builder.insert(new vscode.Position(use + 1, 0), '\t\tli\n'));
  const prefixed = words.document.getText();
  const declared = await vscode.commands.executeCommand(
    'vscode.executeCompletionItemProvider', words.document.uri, new vscode.Position(use + 1, 4),
  );
  // The server ranks declarations by a four-digit sortText; VS Code gives the
  // keywords their labels.
  const ranked = declared.items.filter((i) => /^[0-9]{4}$/.test(i.sortText))
    .sort((x, y) => (x.sortText < y.sortText ? -1 : 1));
  assert.deepEqual(ranked.slice(0, 5).map((i) => i.label), ['line', 'a', 'b', 'this', 'join']);
  const local = ranked[0];
  assert.equal(local.kind, vscode.CompletionItemKind.Variable);
  assert.equal(local.detail, 'var line: int');
  assert.equal(local.sortText, '0000');
  const localRange = local.range instanceof vscode.Range ? local.range : local.range.replacing;
  assert.deepEqual(range({ range: localRange }), [use + 1, 2, use + 1, 4]);
  const localText = typeof local.insertText === 'string' ? local.insertText : local.insertText.value;
  assert.equal(localText, 'line');
  await edit(words, (builder) => builder.replace(localRange, localText));
  assert.equal(words.document.lineAt(use + 1).text, '\t\tline');
  await vscode.window.showTextDocument(words.document);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo of the declaration completion', () => words.document.getText() === prefixed);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo of the declaration prefix', () => words.document.getText() === unedited && !words.document.isDirty);

  // After ".", which triggers completion, the members of the receiver's type
  // alone, from an analysis of the project with the unsaved text, repaired
  // after the ".": the parameter "a" is a string, whose one member is its
  // length. VS Code applies the item, and one undo restores the line.
  await edit(words, (builder) => builder.insert(new vscode.Position(use + 1, 0), '\t\ta.\n'));
  const dotted = words.document.getText();
  const members = await vscode.commands.executeCommand(
    'vscode.executeCompletionItemProvider', words.document.uri, new vscode.Position(use + 1, 4), '.',
  );
  assert.deepEqual(members.items.map((i) => i.label), ['length']);
  const length = members.items[0];
  assert.equal(length.kind, vscode.CompletionItemKind.Field);
  assert.equal(length.detail, 'def length: int');
  assert.equal(length.sortText, '0000');
  const lengthRange = length.range instanceof vscode.Range ? length.range : length.range.replacing;
  assert.deepEqual(range({ range: lengthRange }), [use + 1, 4, use + 1, 4]);
  const lengthText = typeof length.insertText === 'string' ? length.insertText : length.insertText.value;
  assert.equal(lengthText, 'length');
  await edit(words, (builder) => builder.replace(lengthRange, lengthText));
  assert.equal(words.document.lineAt(use + 1).text, '\t\ta.length');
  await vscode.window.showTextDocument(words.document);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo of the member completion', () => words.document.getText() === dotted);
  await vscode.commands.executeCommand('undo');
  await waitFor('the undo of the receiver', () => words.document.getText() === unedited && !words.document.isDirty);

  // Signature help in a call still being typed, with no ")", comes from the
  // unsaved text repaired at the cursor: just after the "(" of .puts( the
  // method's one parameter is active, and after "1, " in Ints.max( from
  // lib/util the second is.
  const unclosed = '\t\tStringBuilder.new().puts(';
  await edit(words, (builder) => builder.insert(new vscode.Position(use + 1, 0), `${unclosed}\n`));
  assert.deepEqual(await signatureHelp(words, use + 1, unclosed.length), {
    activeSignature: 0, activeParameter: 0, signatures: [['def puts(s: Array<byte>) -> this', [[9, 23]]]],
  });
  await undo(words, unedited);
  const second = '\t\tvar n = Ints.max(1, ';
  await edit(words, (builder) => builder.insert(new vscode.Position(use + 1, 0), `${second}\n`));
  assert.deepEqual(await signatureHelp(words, use + 1, second.length), {
    activeSignature: 0, activeParameter: 1, signatures: [['def max(a: int, b: int) -> int', [[8, 14], [16, 22]]]],
  });
  await undo(words, unedited);

  // Inside the comment on line 3 the server offers nothing, either where
  // completion is invoked after the word StringBuilder or after a ".", which
  // triggers it. run.cjs turns off VS Code's word-based suggestions, so the
  // items are the server's alone.
  const comment = 2;
  const word = words.document.lineAt(comment).text.indexOf('StringBuilder') + 'StringBuilder'.length;
  const invoked = await vscode.commands.executeCommand(
    'vscode.executeCompletionItemProvider', words.document.uri, new vscode.Position(comment, word),
  );
  assert.deepEqual(invoked.items.map((i) => i.label), [], 'completion in a comment offered items');
  await edit(words, (builder) => builder.insert(new vscode.Position(comment, word), '.'));
  const commented = await vscode.commands.executeCommand(
    'vscode.executeCompletionItemProvider', words.document.uri, new vscode.Position(comment, word + 1), '.',
  );
  assert.deepEqual(commented.items.map((i) => i.label), [], 'completion after a "." in a comment offered items');
  await undo(words, unedited);

  await projectFileOnDisk(failures, bad, words);

  // Project file buffers go to the server too, so their configuration
  // diagnostics follow unsaved edits.
  const config = await open('.virgil-lsp.json');
  const configText = config.document.getText();
  assert.deepEqual(diagnostics(config), []);
  await edit(config, (builder) => builder.insert(new vscode.Position(1, 0), '  "bogus": 1,\n'));
  const unknown = await waitForDiagnostics('UnknownField after an unsaved project file edit', config);
  assert.deepEqual(unknown.map((d) => [code(d), d.source, d.range.start.line]), [['UnknownField', 'virgil-lsp', 1]]);
  await undo(config, configText);
  await waitFor('cleared configuration diagnostics after undoing the edit', () => diagnostics(config).length === 0);

  // The restart sends shutdown and exit, so the server exits with status 0
  // before its replacement starts and serves the open documents.
  assert.equal(count(channelLog(), 'Server process exited'), 0);
  await vscode.commands.executeCommand('virgil.server.restart');
  await waitFor('a clean server exit and a new server', () => {
    const log = channelLog();
    return count(log, 'Server process exited successfully') === 1 && count(log, 'Started virgil-lsp ') === 2;
  });
  assert.equal(count(channelLog(), 'Server process exited'), 1, 'the server did not exit cleanly');
  // The stopped client's diagnostics went with it, so the type error is the
  // replacement's, from its copy of words/Words.v3.
  await waitFor('the type error in words/Words.v3 from the new server', () => hasTypeError(words), 40000);
  assert.deepEqual(await symbols(words), [{ name: 'Words', kind: vscode.SymbolKind.Namespace }]);
  console.log('PASS: VS Code workspace activation, diagnostics publish/clear/undo, symbols, '
    + 'Virgil root, definition, hover, signature help, references, rename refusal, rename and undo, keyword, declaration, and member completion, '
    + 'signature help in unclosed calls, no completion in a comment, workspace symbols, project file edits, and restart with a clean server exit');

  // The scenarios that restart the server, or could disturb it, come last.
  await trace(failures, words);
  await noVirgilRoot(failures, words);
  await missingServer(failures, words);
  await missingWorker(failures, words);
  assert.deepEqual(failures, [], 'scenarios failed');
}

// With the project file closed and no document edited, a change on disk
// reaches the server only as a file event from the client's watchers. The
// scenario closes every editor, and reopens bad.v3 and words/Words.v3 in tabs
// of their own, as they were, so that the restart serves them as open editors.
async function projectFileOnDisk(failures, bad, words) {
  const file = path.join(project, '.virgil-lsp.json');
  const uri = vscode.Uri.file(file);
  const original = fs.readFileSync(file, 'utf8');
  const settled = () => vscode.languages.getDiagnostics(uri).length === 0 && hasTypeError(words);
  await scenario(failures, 'project file changed on disk while closed: UnknownField, cleared when restored', async () => {
    assert.ok(vscode.workspace.textDocuments.every((d) => d.uri.fsPath !== file), 'the project file is open');
    // The server also reads the project file again whenever it looks up a
    // document's project, as for the outline of a visible editor, and when an
    // analysis ends. With no editor visible, and the last edit's analysis
    // done, which a definition waits for, only a file event brings the change.
    await vscode.commands.executeCommand('workbench.action.closeAllEditors');
    const column = words.document.lineAt(6).text.indexOf('StringBuilder');
    assert.equal((await definitions(words, 6, column)).length, 1);
    await waitFor('the project in its usual state', settled, 40000);
    fs.writeFileSync(file, original.replace('{\n', '{\n  "bogus": 1,\n'));
    const unknown = await waitFor('UnknownField after a change on disk', () => {
      const list = vscode.languages.getDiagnostics(uri);
      return list.length > 0 && list;
    });
    assert.deepEqual(unknown.map((d) => [code(d), d.source, d.range.start.line]), [['UnknownField', 'virgil-lsp', 1]]);
    fs.writeFileSync(file, original);
    await waitFor('cleared diagnostics and the type error in words/Words.v3 again', settled, 40000);
  }, async () => {
    if (fs.readFileSync(file, 'utf8') !== original) fs.writeFileSync(file, original);
    await waitFor('the project in its usual state', settled, 40000);
    await vscode.window.showTextDocument(bad.document, { preview: false });
    await vscode.window.showTextDocument(words.document, { preview: false });
  });
}

// At the output channel's Trace log level, the channel records the LSP
// messages. The test sets the level as the Output view's log level menu does,
// on the channel that VS Code names after the extension and its log file. The
// menu's commands end in the level's number.
async function trace(failures, words) {
  const unedited = words.document.getText();
  const level = async (value) => {
    await vscode.commands.executeCommand('workbench.output.action.switchBetweenOutputs', `${EXTENSION_ID}.${CHANNEL_LOG}`);
    await vscode.commands.executeCommand(`workbench.action.output.activeOutputLogLevel.${value}`);
  };
  await scenario(failures, 'message trace at the Trace log level, with textDocument/didChange', async () => {
    const log = logFromHere();
    await level(vscode.LogLevel.Trace);
    // The client traces the $/setTrace notification that starts the trace.
    await waitFor('the trace to start', () => log().includes("Sending notification '$/setTrace'"));
    // The project file's editor is the active one.
    const editor = await vscode.window.showTextDocument(words.document);
    await edit(editor, (builder) => builder.insert(new vscode.Position(7, 0), '\n'));
    await waitFor('textDocument/didChange in the trace', () => log().includes("Sending notification 'textDocument/didChange'"));
  }, async () => {
    await level(vscode.LogLevel.Info);
    if (words.document.getText() !== unedited) await undo(words, unedited);
    await waitFor('the type error in words/Words.v3', () => hasTypeError(words), 40000);
  });
}

// With neither virgil.virgilRoot nor VIRGIL_LOC, which run.cjs removes, the
// server restarts without a Virgil root, and the project's virgilDependencies
// can't be resolved. The output channel and the diagnostic are checked by
// separate scenarios, each from its own restart.
async function noVirgilRoot(failures, words) {
  const root = setting('virgil.virgilRoot');
  const server = setting('virgil.server.path');
  const config = vscode.Uri.file(path.join(project, '.virgil-lsp.json'));
  // Errors logged meanwhile can come from a client that the restart replaces,
  // as after a failed check, so only the new server's start counts.
  const restore = async () => {
    if (setting('virgil.virgilRoot') !== root) {
      const log = logFromHere();
      await setSetting('virgil.virgilRoot', root);
      await waitFor('a restart with the Virgil root', () => {
        const text = log();
        return text.includes(`Virgil root: ${root} (from virgil.virgilRoot)`) && text.includes('Started virgil-lsp ');
      }, 20000);
    }
    await waitFor('the type error in words/Words.v3', () => hasTypeError(words), 40000);
    assert.deepEqual(vscode.languages.getDiagnostics(config), [], 'MissingVirgilRoot did not clear');
  };
  await scenario(failures, 'no Virgil root: a restart that says so in the output channel', async () => {
    assert.equal(process.env.VIRGIL_LOC, undefined, 'VIRGIL_LOC is set in the extension host');
    const log = logFromHere();
    await setSetting('virgil.virgilRoot', '');
    await waitFor('a restart that says there is no Virgil root', () => {
      const text = log();
      return text.includes('virgil.virgilRoot changed; restarting the server')
        && text.includes('No Virgil root: neither virgil.virgilRoot nor VIRGIL_LOC is set')
        && text.includes(`Starting ${server} --stdio`);
    });
  }, restore);
  await scenario(failures, 'no Virgil root: a running server, and MissingVirgilRoot on .virgil-lsp.json, cleared when restored', async () => {
    const log = logFromHere();
    await setSetting('virgil.virgilRoot', '');
    const missing = await waitFor('MissingVirgilRoot on .virgil-lsp.json', () => {
      expectNoServerError(log);
      const list = vscode.languages.getDiagnostics(config);
      return list.length > 0 && list;
    }, 40000);
    assert.deepEqual(missing.map((d) => [code(d), d.source, d.range.start.line]), [['MissingVirgilRoot', 'virgil-lsp', 6]]);
    // The server initialized, reporting its version (not the extension's
    // "(no version reported)" fallback), and still answers.
    await waitFor('the server to start and report its version', () => {
      expectNoServerError(log);
      return /Started virgil-lsp \d+\.\d+\.\d+\S*$/m.test(log());
    });
    assert.deepEqual(await symbols(words), [{ name: 'Words', kind: vscode.SymbolKind.Namespace }]);
    expectNoServerError(log);
  }, restore);
}

// Points virgil.server.path back at server, if it no longer is, and waits for
// the server to start from it and publish words/Words.v3's diagnostics again.
async function restoreServer(server, words) {
  if (setting('virgil.server.path') !== server) {
    const log = logFromHere();
    await setSetting('virgil.server.path', server);
    await waitFor('the server to start again', () => {
      expectNoServerError(log);
      const text = log();
      return text.includes(`Starting ${server} --stdio`) && text.includes('Started virgil-lsp ');
    }, 20000);
  }
  await waitFor('the type error in words/Words.v3', () => hasTypeError(words), 40000);
}

// An absolute virgil.server.path that doesn't exist stops the server, and no
// client runs. The error notification beside the log message is visual.
async function missingServer(failures, words) {
  const server = setting('virgil.server.path');
  const missing = path.join(path.dirname(project), 'missing', 'virgil-lsp');
  await scenario(failures, 'nonexistent virgil.server.path: the output channel, no client, and recovery', async () => {
    const log = logFromHere();
    await setSetting('virgil.server.path', missing);
    await waitFor('the missing server in the output channel', () => log().includes(
      `The server did not start: ${missing} (from virgil.server.path) is missing or not an executable file.`,
    ));
    // Nothing started, and the stopped client's diagnostics and features
    // went with it.
    assert.ok(!log().includes('Starting '), 'a server started');
    assert.deepEqual(diagnostics(words), []);
    assert.deepEqual(await symbols(words), []);
  }, () => restoreServer(server, words));
}

// A virgil.server.path without virgil-lsp-worker beside it: a copy of the
// server in a directory of its own, so that the real worker is never moved.
async function missingWorker(failures, words) {
  const server = setting('virgil.server.path');
  const directory = fs.mkdtempSync(path.join(path.dirname(project), 'no-worker-'));
  const copy = path.join(directory, 'virgil-lsp');
  await scenario(failures, 'virgil.server.path without virgil-lsp-worker: the output channel and recovery', async () => {
    fs.copyFileSync(server, copy);
    fs.chmodSync(copy, 0o755);
    const log = logFromHere();
    await setSetting('virgil.server.path', copy);
    await waitFor('the missing worker in the output channel', () => log().includes(
      `The server did not start: ${path.join(directory, 'virgil-lsp-worker')} is missing or not executable.`,
    ));
    assert.ok(!log().includes('Starting '), 'a server started');
  }, async () => {
    try {
      await restoreServer(server, words);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

async function run() {
  const extension = vscode.extensions.getExtension(EXTENSION_ID);
  assert.ok(extension, `${EXTENSION_ID} is not loaded`);
  // run.cjs sets this when it installed the extension from a VSIX.
  const installed = process.env.VIRGIL_E2E_EXTENSIONS_DIR;
  if (installed !== undefined) {
    assert.equal(path.dirname(extension.extensionPath), installed,
      `${EXTENSION_ID} was loaded from ${extension.extensionPath}, not from the installed VSIX`);
  }
  if (vscode.workspace.workspaceFolders === undefined) await singleFile(extension);
  else await workspace(extension);
}

module.exports = { run };
