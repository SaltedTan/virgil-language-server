// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Extension Host checks, started by run.cjs. They mirror test/e2e/nvim/check.lua
// and act only through VS Code APIs and commands, as a user's edits would.

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');
const { fromShutdown } = require('./shutdown.cjs');

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

// Go to Type Definition resolves a call's result, independently of its
// method definition, and returns no location for built-in value types.
async function typeDefinitions(editor, line, character) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeTypeDefinitionProvider', editor.document.uri, new vscode.Position(line, character),
  );
  return (result ?? []).map((location) => [location.uri?.fsPath, ...range(location)]);
}

// The target of each Location that an implementation request at line:character returns.
async function implementations(editor, line, character) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeImplementationProvider', editor.document.uri, new vscode.Position(line, character),
  );
  return (result ?? []).map((location) => [location.uri?.fsPath, ...range(location)]);
}

// Each type or call hierarchy item's name, kind, detail, file, range, and name
// range.
function hierarchyItems(items) {
  return (items ?? []).map((item) => [
    item.name, item.kind, item.detail, item.uri.fsPath, range(item),
    range({ range: item.selectionRange }),
  ]);
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

// Exact ranges and highlight kinds at a document position.
async function documentHighlights(editor, line, character) {
  const result = await vscode.commands.executeCommand(
    'vscode.executeDocumentHighlights', editor.document.uri, new vscode.Position(line, character),
  );
  return (result ?? []).map((highlight) => [...range(highlight), highlight.kind]);
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
function selectedServer() {
  return setting('virgil.server.path') || path.join(
    vscode.extensions.getExtension('virgil-language-server.virgil-lsp-vscode').extensionPath, 'server', 'virgil-lsp');
}

async function checkBundledSelection(extension) {
  if (process.env.VIRGIL_E2E_BUNDLED !== '1') return;
  assert.ok(fs.existsSync(path.join(extension.extensionPath, 'server-target.json')), 'installed VSIX has no bundle');
  assert.equal(setting('virgil.server.path'), '', 'bundled startup must have no server override');
  assert.equal(setting('virgil.virgilRoot'), '', 'bundled startup must have no library override');
  for (const entry of (process.env.PATH || '').split(path.delimiter)) {
    assert.ok(!fs.existsSync(path.join(entry, 'virgil-lsp')), 'external server on PATH: ' + entry);
  }
  // VS Code flushes the output channel asynchronously, after diagnostics may arrive.
  await waitFor('bundled server selection in the output channel', () => channelLog().includes('(bundled for '));
}

async function singleFile(extension) {
  assert.equal(extension.isActive, false, 'the extension was active before a Virgil document opened');
  const bad = await open('bad.v3');
  assert.equal(bad.document.languageId, 'virgil');
  await waitFor('activation by a .v3 document', () => extension.isActive);
  await expectParserDiagnostic(bad);
  const server = selectedServer();
  await checkBundledSelection(extension);
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
  await checkBundledSelection(extension);
  const configuredRoot = setting('virgil.virgilRoot');
  const root = configuredRoot || path.join(path.dirname(selectedServer()), 'virgil');
  if (configuredRoot) assert.ok(channelLog().includes(`Virgil root: ${root} (from virgil.virgilRoot)`),
    'the Virgil root was not taken from virgil.virgilRoot');

  const folding = await open('folding.v3');
  const folds = await vscode.commands.executeCommand('vscode.executeFoldingRangeProvider', folding.document.uri);
  assert.deepEqual(folds.map((r) => [r.start, r.end, r.kind]), [
    [0, 1, vscode.FoldingRangeKind.Comment], [3, 14, undefined], [4, 9, undefined],
    [5, 6, undefined], [7, 8, undefined], [11, 12, vscode.FoldingRangeKind.Comment], [13, 14, vscode.FoldingRangeKind.Comment],
  ]);
  console.log('PASS: vscode.folding-ranges.response');
  const selections = await vscode.commands.executeCommand('vscode.executeSelectionRangeProvider',
    folding.document.uri, [new vscode.Position(6, 8)]);
  assert.equal(selections.length, 1);
  const chain = [];
  for (let r = selections[0]; r; r = r.parent) {
    chain.push([r.range.start.line, r.range.start.character, r.range.end.line, r.range.end.character]);
  }
  // VS Code adds its own word and whole-line levels to the server chain.
  assert.deepEqual(chain, [
    [6, 6, 6, 12], [6, 6, 6, 13], [6, 0, 6, 13],
    [5, 14, 7, 5], [5, 4, 7, 12], [5, 4, 9, 5], [5, 0, 9, 5],
    [4, 12, 10, 3], [4, 2, 10, 3], [4, 2, 14, 11], [4, 0, 14, 11],
    [3, 16, 15, 1], [3, 0, 15, 1], [0, 0, 16, 0],
  ], JSON.stringify(chain));
  console.log('PASS: vscode.selection-ranges.response');

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

  // vscode.diagnostics.semicolon-fix: use the real provider and editor edit.
  const semicolon = await open('semicolon.v3');
  const [semicolonDiagnostic] = await waitForDiagnostics('missing semicolon', semicolon);
  assert.equal(semicolonDiagnostic.message, '";" expected');
  const fixes = await vscode.commands.executeCommand('vscode.executeCodeActionProvider',
    semicolon.document.uri, semicolonDiagnostic.range, vscode.CodeActionKind.QuickFix.value);
  assert.equal(fixes.length, 1);
  const [fix] = fixes;
  assert.equal(fix.title, 'Insert missing ";"');
  assert.equal(fix.kind.value, 'quickfix');
  assert.equal(fix.isPreferred, true);
  // executeCodeActionProvider omits diagnostics when it marshals the action.
  // The stdio and Neovim tests check the attached diagnostic verbatim.
  assert.equal(fix.command, undefined);
  assert.deepEqual(fix.edit.entries().map(([uri, edits]) => [uri.toString(), edits.map((e) => ({
    range: [e.range.start.line, e.range.start.character, e.range.end.line, e.range.end.character], text: e.newText,
  }))]), [[semicolon.document.uri.toString(), [{ range: [2, 10, 2, 10], text: ';' }]]]);
  assert.equal(await vscode.workspace.applyEdit(fix.edit), true);
  await waitFor('cleared semicolon diagnostic', () => diagnostics(semicolon).length === 0);
  assert.equal(semicolon.document.lineAt(2).text, '\t\treturn 1; // insert before this comment');
  console.log('PASS: vscode.diagnostics.semicolon-fix');

  // Only a program that resolves StringBuilder from lib/util, through the
  // project's virgilDependencies and virgil.virgilRoot, has this single error.
  let words = await open('words', 'Words.v3');
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
  const typeCall = words.document.lineAt(use).text.indexOf('.puts(') + 1;
  assert.deepEqual(await typeDefinitions(words, use, typeCall), [
    [library, declaration, 6, declaration, 19],
  ]);
  assert.deepEqual(await typeDefinitions(words, use, words.document.lineAt(use).text.indexOf('line:')), []);
  await vscode.window.showTextDocument(words.document, { preview: false });
  const inlay = await open('words', 'Inlay.v3');
  const hints = await vscode.commands.executeCommand('vscode.executeInlayHintProvider',
    inlay.document.uri, new vscode.Range(0, 0, inlay.document.lineCount, 0));
  assert.deepEqual(hints.map((h) => [h.position.line, h.position.character, h.kind,
    typeof h.label === 'string' ? h.label : h.label.map((p) => p.value).join('')]), [
    [4, 33, vscode.InlayHintKind.Type, ': int'],
    [4, 41, vscode.InlayHintKind.Parameter, 'count:'],
  ]);
  words = await vscode.window.showTextDocument(words.document);
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

  // vscode.document-highlight.response: only this document's StringBuilder use.
  assert.deepEqual(await documentHighlights(words, use, column), [
    [use, column, use, column + 'StringBuilder'.length, vscode.DocumentHighlightKind.Read],
  ]);
  const highlightsDocument = await vscode.workspace.openTextDocument(
    path.join(path.dirname(words.document.uri.fsPath), 'Highlights.v3'),
  );
  assert.deepEqual(await documentHighlights({ document: highlightsDocument }, 5, 2), [
    [4, 6, 4, 11, vscode.DocumentHighlightKind.Text],
    [5, 2, 5, 7, vscode.DocumentHighlightKind.Write],
    [5, 10, 5, 15, vscode.DocumentHighlightKind.Read],
    [6, 2, 6, 7, vscode.DocumentHighlightKind.Write],
    [7, 9, 7, 14, vscode.DocumentHighlightKind.Read],
  ]);
  console.log('PASS: vscode.document-highlight.response');

  // vscode.implementation.response: Shape's subclasses, directly and
  // transitively, and the methods that override its area, by offset; the
  // unextended Circle has none.
  const shapes = path.join(path.dirname(words.document.uri.fsPath), 'Shapes.v3');
  const shapesDocument = await vscode.workspace.openTextDocument(shapes);
  assert.deepEqual(await implementations({ document: shapesDocument }, 4, 6), [
    [shapes, 7, 6, 7, 13], [shapes, 10, 6, 10, 12], [shapes, 11, 6, 11, 12],
  ]);
  assert.deepEqual(await implementations({ document: shapesDocument }, 5, 5), [
    [shapes, 8, 5, 8, 9], [shapes, 12, 5, 12, 9],
  ]);
  assert.deepEqual(await implementations({ document: shapesDocument }, 11, 6), []);
  console.log('PASS: vscode.implementation.response');

  // vscode.type-hierarchy.response: Polygon's item, as Show Type Hierarchy
  // prepares it, and one level up and down from it, through the items the
  // server gave; Shape's subtypes are Polygon and Circle.
  const [polygon, ...others] = await vscode.commands.executeCommand(
    'vscode.prepareTypeHierarchy', shapesDocument.uri, new vscode.Position(7, 6),
  );
  assert.deepEqual(others, []);
  const polygonItem = ['Polygon', vscode.SymbolKind.Class, 'class Polygon extends Shape', shapes, [7, 0, 9, 1], [7, 6, 7, 13]];
  assert.deepEqual(hierarchyItems([polygon]), [polygonItem]);
  const supertypes = await vscode.commands.executeCommand('vscode.provideSupertypes', polygon);
  assert.deepEqual(hierarchyItems(supertypes), [
    ['Shape', vscode.SymbolKind.Class, 'class Shape', shapes, [4, 0, 6, 1], [4, 6, 4, 11]],
  ]);
  assert.deepEqual(hierarchyItems(await vscode.commands.executeCommand('vscode.provideSubtypes', polygon)), [
    ['Square', vscode.SymbolKind.Class, 'class Square extends Polygon', shapes, [10, 0, 10, 32], [10, 6, 10, 12]],
  ]);
  assert.deepEqual(hierarchyItems(await vscode.commands.executeCommand('vscode.provideSubtypes', supertypes[0])), [
    polygonItem,
    ['Circle', vscode.SymbolKind.Class, 'class Circle extends Shape', shapes, [11, 0, 13, 1], [11, 6, 11, 12]],
  ]);
  console.log('PASS: vscode.type-hierarchy.response');

  // vscode.call-hierarchy.response: square's item, as Show Call Hierarchy
  // prepares it from a call, its two callers, one of which calls it twice,
  // and the calls out of that caller, through the items the server gave.
  const calls = path.join(path.dirname(words.document.uri.fsPath), 'Calls.v3');
  const callsDocument = await vscode.workspace.openTextDocument(calls);
  const [square, ...more] = await vscode.commands.executeCommand(
    'vscode.prepareCallHierarchy', callsDocument.uri, new vscode.Position(7, 35),
  );
  assert.deepEqual(more, []);
  const squareItem = ['square', vscode.SymbolKind.Method, 'def square(x: int) -> int', calls, [5, 1, 5, 44], [5, 5, 5, 11]];
  assert.deepEqual(hierarchyItems([square]), [squareItem]);
  const incoming = await vscode.commands.executeCommand('vscode.provideIncomingCalls', square);
  const ranges = (call) => call.fromRanges.map((r) => [r.start.line, r.start.character, r.end.line, r.end.character]);
  assert.deepEqual(incoming.map((call) => [...hierarchyItems([call.from]), ranges(call)]), [
    [['sumSquares', vscode.SymbolKind.Method, 'def sumSquares(a: int, b: int) -> int', calls, [6, 1, 6, 72], [6, 5, 6, 15]],
      [[6, 48, 6, 54], [6, 60, 6, 66]]],
    [['cube', vscode.SymbolKind.Method, 'def cube(x: int) -> int', calls, [7, 1, 7, 50], [7, 5, 7, 9]], [[7, 34, 7, 40]]],
  ]);
  const outgoing = await vscode.commands.executeCommand('vscode.provideOutgoingCalls', incoming[0].from);
  assert.deepEqual(outgoing.map((call) => [...hierarchyItems([call.to]), ranges(call)]), [
    [squareItem, [[6, 48, 6, 54], [6, 60, 6, 66]]],
  ]);
  assert.deepEqual(await vscode.commands.executeCommand('vscode.provideOutgoingCalls', square), []);
  console.log('PASS: vscode.call-hierarchy.response');

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
    + 'Virgil root, definition, type definition, inlay hints, hover, signature help, references, rename refusal, rename and undo, keyword, declaration, and member completion, '
    + 'signature help in unclosed calls, no completion in a comment, workspace symbols, project file edits, and restart with a clean server exit');

  // The scenarios that restart the server, or could disturb it, come last.
  await trace(failures, words);
  await noVirgilRoot(failures, words);
  await missingServer(failures, words);
  await missingWorker(failures, words);
  await actionDuringRecovery(failures, words, 'restart');
  await actionDuringRecovery(failures, words, 'configuration');
  await writeAfterServerDied(failures, bad, words);
  await lostFraming(failures, bad, words);
  await lostFraming(failures, bad, words, true);
  await crashLimit(failures, bad, words);
  await pendingEditRestart(failures, bad, words);
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
async function level(value) {
  await vscode.commands.executeCommand('workbench.output.action.switchBetweenOutputs', `${EXTENSION_ID}.${CHANNEL_LOG}`);
  await vscode.commands.executeCommand(`workbench.action.output.activeOutputLogLevel.${value}`);
}

async function trace(failures, words) {
  const unedited = words.document.getText();
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
  const originalRoot = setting('virgil.virgilRoot');
  const root = originalRoot || path.join(path.dirname(selectedServer()), 'virgil');
  if (!originalRoot) await setSetting('virgil.virgilRoot', root);
  const server = selectedServer();
  const config = vscode.Uri.file(path.join(project, '.virgil-lsp.json'));
  // Root errors need an intentionally incomplete install now that archives
  // carry a default library. Copy the executables; a symlink resolves back
  // to the original install and would still discover its bundle.
  const directory = fs.mkdtempSync(path.join(path.dirname(project), 'no-bundle-'));
  const copy = path.join(directory, 'virgil-lsp');
  for (const name of ['virgil-lsp', 'virgil-lsp-worker']) {
    fs.copyFileSync(path.join(path.dirname(fs.realpathSync(server)), name), path.join(directory, name));
    fs.chmodSync(path.join(directory, name), 0o755);
  }
  await restoreServer(copy, words);
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
        && text.includes(`Starting ${copy} --stdio`);
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
  await restoreServer(server, words);
  if (!originalRoot) {
    const log = logFromHere();
    await setSetting('virgil.virgilRoot', originalRoot);
    await waitFor('the bundled root restart to finish', () =>
      log().includes('No Virgil root:') && log().includes('Started virgil-lsp '));
    await waitFor('the restored bundled library diagnostics', () => hasTypeError(words), 40000);
  }
  fs.rmSync(directory, { recursive: true, force: true });
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
  const server = selectedServer();
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
  const server = selectedServer();
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

// Checklist step 17 makes a killed server's automatic recovery race with a
// restart, a configuration change, or closing the window. The recovery start
// must be under way when the action comes, so the scenarios start the server
// from a script, written by recoveryServer, that holds a start, before it
// runs the server, for as long as a file "hold" in its directory exists:
// the client has then spawned the recovery's server and sent initialize,
// which the held server doesn't read yet. A held start runs the server with
// its input copied to input-PID, PID being the script's, and records its exit
// status. The server exits with status 0 only after shutdown and then exit.

function shellWord(word) {
  return `'${word.replaceAll("'", "'\\''")}'`;
}

// Writes the script, virgil-lsp in directory, which runs server. Each start
// appends its PID to "starts". A held start writes its PID to "held", and
// when it goes on, "PID removed" to "released" if the hold file is gone, or
// "PID terminating" if the hold file names a log that says the extension host
// is terminating; after 20 seconds, it goes on anyway. A held start, and any
// start while a file "record" is in directory, runs the server with its input
// copied to input-PID, and appends "PID status" to "exits" when the server
// exits. A start that finds a file "linger" renames it to "lingering", runs
// the server as its child, which alone reads the client's input, writes the
// child's PID to "child", and appends "PID status" to "exits" when it exits;
// then, keeping the client's output open, the script runs on until
// "lingering" is gone, or for 20 seconds, writes "PID removed" or
// "PID timeout" to "released", and exits with status 0. A start that finds a
// file "stray" renames it to "straying", runs the server as its child in the
// same way, writes the child's PID to "child", and waits while the child
// runs and "straying" is there; once a file "inject" is there, it writes a
// line to its output, which the client reads after the server's, renames
// "inject" to "injected", and waits for the child, then appends "PID status"
// to "exits" and exits with that status. Any other start runs the server in
// its place, with the script's PID. The client looks for the worker beside
// the script, so a link to the real one is there.
function recoveryServer(directory, server) {
  const script = path.join(directory, 'virgil-lsp');
  fs.writeFileSync(script, `#!/bin/sh
state=${shellWord(directory)}
echo $$ >> "$state/starts"
if [ -e "$state/hold" ]; then
  echo $$ > "$state/held"
  log=$(cat "$state/hold")
  reason=timeout
  waited=0
  while [ $waited -lt 400 ]; do
    if [ ! -e "$state/hold" ]; then reason=removed; break; fi
    if [ -n "$log" ] && grep -q 'Extension host terminating' "$log" 2>/dev/null; then reason=terminating; break; fi
    sleep 0.05
    waited=$((waited + 1))
  done
  echo "$$ $reason" >> "$state/released"
elif [ -e "$state/linger" ]; then
  mv "$state/linger" "$state/lingering"
  exec 3<&0 0</dev/null
  ${shellWord(server)} "$@" <&3 3<&- &
  child=$!
  exec 3<&-
  echo $child > "$state/child"
  wait $child
  echo "$$ $?" >> "$state/exits"
  reason=timeout
  waited=0
  while [ $waited -lt 400 ]; do
    if [ ! -e "$state/lingering" ]; then reason=removed; break; fi
    sleep 0.05
    waited=$((waited + 1))
  done
  echo "$$ $reason" >> "$state/released"
  exit 0
elif [ -e "$state/stray" ]; then
  mv "$state/stray" "$state/straying"
  exec 3<&0 0</dev/null
  ${shellWord(server)} "$@" <&3 3<&- &
  child=$!
  exec 3<&-
  echo $child > "$state/child"
  while kill -0 $child 2>/dev/null && [ -e "$state/straying" ] && [ ! -e "$state/inject" ]; do
    sleep 0.05
  done
  if [ -e "$state/inject" ]; then
    echo 'virgil-lsp wrapper: starting the server'
    mv "$state/inject" "$state/injected"
  fi
  wait $child
  status=$?
  echo "$$ $status" >> "$state/exits"
  exit $status
elif [ ! -e "$state/record" ]; then
  exec ${shellWord(server)} "$@"
fi
tee "$state/input-$$" | ${shellWord(server)} "$@"
status=$?
echo "$$ $status" >> "$state/exits"
exit $status
`, { mode: 0o755 });
  fs.symlinkSync(path.join(path.dirname(fs.realpathSync(server)), 'virgil-lsp-worker'), path.join(directory, 'virgil-lsp-worker'));
  return script;
}

// The lines of the file name in directory that the script writes.
function recorded(directory, name) {
  const file = path.join(directory, name);
  return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter((line) => line !== '') : [];
}

// The requests and notifications that the script's start with PID pid has
// copied from its input so far, as messages returns them.
function serverInput(directory, pid) {
  const file = path.join(directory, `input-${pid}`);
  return fs.existsSync(file) ? messages(fs.readFileSync(file)) : [];
}

// The requests and notifications in a server's input, as the script copied it,
// in order, without the responses to the server's requests.
function messages(input) {
  const found = [];
  let rest = input;
  for (;;) {
    const end = rest.indexOf('\r\n\r\n');
    const length = Number(/Content-Length: *(\d+)/i.exec(rest.subarray(0, Math.max(end, 0)).toString())?.[1]);
    if (end < 0 || !(rest.length >= end + 4 + length)) return found;
    const message = JSON.parse(rest.subarray(end + 4, end + 4 + length).toString());
    if (message.method !== undefined) found.push(message);
    rest = rest.subarray(end + 4 + length);
  }
}

// Whether a list of requests and notifications has a didOpen for uri before
// the last exit.
function openedBeforeExit(received, uri) {
  const exit = received.findLastIndex((message) => message.method === 'exit');
  return received.some((message, index) => index < exit
    && message.method === 'textDocument/didOpen' && message.params?.textDocument?.uri === uri);
}

// Throws unless the held start with PID held received initialize first, then
// initialized, so the recovery had finished, and the didOpen for the open
// bad.v3, then shutdown and exit last, and then exited with status 0. The
// didOpen may come on either side of shutdown, because a slow write can follow
// it, but it must reach the server before exit. Cancellations may also follow
// shutdown (see shutdown.cjs).
function expectStoppedAfterRecovery(directory, held) {
  const input = fs.readFileSync(path.join(directory, `input-${held}`));
  const received = messages(input);
  const methods = received.map((message) => message.method);
  assert.equal(methods[0], 'initialize', `the recovered server's input: ${methods}`);
  assert.ok(methods.indexOf('initialized') > 0, `the recovered server got no initialized: ${methods}`);
  assert.deepEqual(fromShutdown(methods), ['shutdown', 'exit'],
    `the recovered server did not get shutdown and then exit last: ${methods}`);
  const document = vscode.Uri.file(path.join(project, 'bad.v3')).toString();
  assert.ok(openedBeforeExit(received, document),
    `the recovered server got no textDocument/didOpen for the open bad.v3 before exit: ${methods}`);
  assert.deepEqual(recorded(directory, 'exits'), [`${held} 0`], 'the recovered server did not exit with status 0');
}

// The processes in this extension host's process group, which the servers
// that the client starts join, with each one's parent and its arguments,
// which start with the path of the executable that the client or the server
// started. As run.cjs does, by ps, which macOS has too.
function groupProcesses() {
  const ps = (args) => {
    const result = spawnSync('ps', args, { encoding: 'utf8' });
    if (result.status !== 0) throw new Error(`ps failed: ${result.error?.message ?? result.stderr}`);
    return result.stdout;
  };
  const group = Number(ps(['-o', 'pgid=', '-p', String(process.pid)]));
  return ps(['-A', '-o', 'pid=', '-o', 'ppid=', '-o', 'pgid=', '-o', 'args=']).split('\n').flatMap((line) => {
    const [, pid, ppid, pgid, args] = /^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/.exec(line) ?? [];
    return Number(pgid) === group ? [{ pid: Number(pid), ppid: Number(ppid), args }] : [];
  });
}

function runs(entry, executable) {
  return entry.args === executable || entry.args.startsWith(`${executable} `);
}

// Kills the server with PID pid, after checking that it is the one that
// parent, by default the client, started: parent's child, running server.
function killServer(pid, server, parent = process.pid) {
  const target = groupProcesses().find((p) => p.pid === pid);
  assert.ok(target !== undefined && target.ppid === parent && runs(target, server),
    `pid ${pid} is not a virgil-lsp that ${parent === process.pid ? 'this extension host' : `pid ${parent}`} started: ${JSON.stringify(target)}`);
  process.kill(pid, 'SIGKILL');
}

// Waits until this extension host runs exactly one server, which isn't one
// of retired, the worker only as that server's child, and nothing from
// directory, and returns that server's PID.
async function expectOneServer(server, directory, retired) {
  const worker = path.join(path.dirname(fs.realpathSync(server)), 'virgil-lsp-worker');
  let processes;
  const one = () => {
    processes = groupProcesses();
    const servers = processes.filter((p) => runs(p, server));
    return servers.length === 1 && !retired.includes(servers[0].pid)
      && processes.every((p) => (!runs(p, worker) || p.ppid === servers[0].pid) && !p.args.includes(directory))
      && servers[0].pid;
  };
  try {
    return await waitFor('one virgil-lsp left', one, 5000);
  } catch (error) {
    const listed = processes.filter((p) => runs(p, server) || runs(p, worker) || p.args.includes(directory));
    throw new Error(`${error.message}; running: ${JSON.stringify(listed)}`);
  }
}

// Points virgil.server.path at a recoveryServer script in directory, waits
// for the server to start from it, and returns the script and the server's
// PID.
async function startRecoveryServer(server, directory) {
  const script = recoveryServer(directory, server);
  const log = logFromHere();
  await setSetting('virgil.server.path', script);
  await waitFor('the server to start from the script', () => {
    expectNoServerError(log);
    const text = log();
    return text.includes(`Starting ${script} --stdio`) && text.includes('Started virgil-lsp ');
  }, 20000);
  const [pid] = recorded(directory, 'starts').map(Number);
  return { script, pid };
}

// Kills the server with PID pid, and waits until the client's recovery
// start is held. Returns the held start's PID and the log since the kill.
async function killAndHold(directory, pid, server, release = '') {
  fs.writeFileSync(path.join(directory, 'hold'), release);
  const log = logFromHere();
  killServer(pid, server);
  const held = await waitFor('the recovery to start the server again', () => {
    const [start] = recorded(directory, 'held');
    return log().includes('Connection to server got closed. Server will restart.') && start !== undefined && Number(start);
  });
  return { held, log };
}

// Throws unless each of lines is in text, in order.
function expectInOrder(text, lines) {
  let at = 0;
  for (const [index, line] of lines.entries()) {
    const found = text.indexOf(line, at);
    assert.ok(found >= 0, `"${line}" is not ${index === 0 ? 'in the log' : `after "${lines[index - 1]}"`}:\n${text}`);
    at = found + line.length;
  }
}

// Kills the server, and while the recovery start is held, runs the
// Restart Language Server command or changes virgil.server.path back from the
// script to the server. In the message trace, the action waits for the
// recovery's initialize and initialized; then the client sends the recovered
// server shutdown and exit, and only then starts one replacement. The client
// counts a server as stopped once it has sent exit, so the recovered server's
// exit can be logged after the replacement's start.
async function actionDuringRecovery(failures, words, action) {
  const server = selectedServer();
  const directory = fs.mkdtempSync(path.join(path.dirname(project), 'recovery-'));
  const name = action === 'restart'
    ? 'server killed, and Virgil: Restart Language Server during the recovery'
    : 'server killed, and virgil.server.path changed during the recovery';
  await scenario(failures, `${name}: the recovery, then shutdown and exit, then one replacement`, async () => {
    // The trace starts before the server that is killed, so that no
    // $/setTrace is on its way to it then.
    const traced = logFromHere();
    await level(vscode.LogLevel.Trace);
    await waitFor('the trace to start', () => traced().includes("Sending notification '$/setTrace'"));
    const { script, pid } = await startRecoveryServer(server, directory);
    const { held, log } = await killAndHold(directory, pid, server);
    let done;
    if (action === 'restart') {
      // VS Code runs a command of an extension in the same extension host
      // before executeCommand returns, so the restart is queued now.
      done = vscode.commands.executeCommand('virgil.server.restart');
    } else {
      done = setSetting('virgil.server.path', server);
      await waitFor('the configuration change', () => log().includes('virgil.server.path changed; restarting the server'));
    }
    // Nothing has stopped the recovering client or started another.
    assert.ok(!/Sending request 'shutdown|Starting /.test(log()), `the action did not wait for the recovery:\n${log()}`);
    const released = logFromHere();
    fs.rmSync(path.join(directory, 'hold'));
    await done;
    const replacement = action === 'restart' ? script : server;
    await waitFor('the replacement to start, and the recovered server to exit', () => {
      const text = log();
      return text.includes(`Starting ${replacement} --stdio`) && text.includes('Started virgil-lsp ')
        && recorded(directory, 'exits').length > 0;
    }, 20000);
    expectStoppedAfterRecovery(directory, held);
    assert.deepEqual(recorded(directory, 'released'), [`${held} removed`]);
    const text = log();
    expectInOrder(text, [
      "Received response 'initialize",
      "Sending notification 'initialized'",
      "Sending request 'shutdown",
      "Sending notification 'exit'",
      `Starting ${replacement} --stdio`,
      "Sending request 'initialize",
      'Started virgil-lsp ',
    ]);
    assert.deepEqual([count(text, "Sending request 'shutdown"), count(text, '[info] Starting '), count(text, 'Started virgil-lsp ')], [1, 1, 1], text);
    // The killed server's exit and the recovered server's are the only ones.
    assert.deepEqual(text.match(/Server process exited .*/g), ['Server process exited with signal SIGKILL.', 'Server process exited successfully']);
    const remaining = await expectOneServer(server, directory, [pid, held]);
    if (action === 'restart') assert.equal(remaining, Number(recorded(directory, 'starts')[2]));
    // Once the recovery went on, no request or notification failed, which the
    // client would also show as an error notification, by the time the
    // replacement has opened the documents and analyzed words/Words.v3. The
    // kill itself can fail a message on its way to the killed server.
    await waitFor('the type error in words/Words.v3 from the replacement', () => hasTypeError(words), 40000);
    assert.deepEqual(released().match(/ \[error\] (Sending|Request) .* failed.*/g) ?? [], [], 'the client reported failures');
  }, async () => {
    try {
      fs.rmSync(path.join(directory, 'hold'), { force: true });
      await level(vscode.LogLevel.Info);
      await restoreServer(server, words);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

// A server that dies while the client writes to it fails the write with
// EPIPE, which vscode-jsonrpc reports without a count, and the client can see
// the failed write before the connection's close. vscode-languageclient's
// default error handler then stopped the client, which never restarted the
// server (#216). The server starts from a recoveryServer script that lingers:
// the server, its child, alone reads the client's input, and the script keeps
// the client's output open after the server has died. Once the test has
// killed the server, the connection stays open until the client writes, and
// that write fails every time. The client must restart the server exactly
// once, from the same script, which copies the restarted server's input.
async function writeAfterServerDied(failures, bad, words) {
  const server = selectedServer();
  const directory = fs.mkdtempSync(path.join(path.dirname(project), 'write-error-'));
  const documents = [bad.document, words.document];
  await scenario(failures, 'server killed, then a write to it fails with EPIPE: one restart, which gets the open documents', async () => {
    fs.writeFileSync(path.join(directory, 'record'), '');
    fs.writeFileSync(path.join(directory, 'linger'), '');
    const { pid } = await startRecoveryServer(server, directory);
    await expectParserDiagnostic(bad);
    const [child] = recorded(directory, 'child').map(Number);
    const log = logFromHere();
    killServer(child, server, pid);
    await waitFor('the killed server to exit', () => recorded(directory, 'exits').length > 0);
    assert.deepEqual(recorded(directory, 'exits'), [`${pid} 137`]);
    // Nothing has closed the connection, so the client writes the request.
    await vscode.commands.executeCommand('vscode.executeHoverProvider', bad.document.uri, new vscode.Position(0, 0))
      .then(() => {}, () => {});
    const replacement = await waitFor('the client to restart the server', () => {
      const starts = recorded(directory, 'starts');
      return log().includes('Connection to server got closed. Server will restart.') && starts.length > 1 && Number(starts[1]);
    }).catch((error) => { throw new Error(`${error.message}; the log since the kill:\n${log()}`); });
    // The restarted server gets initialize, then initialized, and each open
    // document in a didOpen, with the editor's text and version.
    await waitFor('the restarted server to get the open documents', () => {
      const received = serverInput(directory, replacement);
      return received[0]?.method === 'initialize' && received.some((m) => m.method === 'initialized')
        && documents.every((d) => received.some((m) => m.method === 'textDocument/didOpen' && m.params.textDocument.uri === d.uri.toString()));
    });
    for (const document of documents) {
      const opened = serverInput(directory, replacement)
        .find((m) => m.method === 'textDocument/didOpen' && m.params.textDocument.uri === document.uri.toString());
      assert.deepEqual([opened.params.textDocument.text, opened.params.textDocument.version], [document.getText(), document.version]);
    }
    await expectParserDiagnostic(bad);
    await waitFor('the type error in words/Words.v3 from the restarted server', () => hasTypeError(words), 40000);
    // The write failed, and the client neither stopped nor restarted twice.
    const text = log();
    assert.ok(text.includes('connection to server is erroring.\nwrite EPIPE'), `no write failed with EPIPE:\n${text}`);
    assert.deepEqual([count(text, 'Server will restart.'), count(text, 'Shutting down server.'), count(text, 'will not be restarted')], [1, 0, 0], text);
    assert.equal(recorded(directory, 'starts').length, 2, 'the client started more than one replacement');
    // Released, the lingering script exits, and the replacement's server is
    // the only one left, with no worker but its own.
    fs.rmSync(path.join(directory, 'lingering'));
    await waitFor('the lingering script to exit', () => recorded(directory, 'released').length > 0
      && !groupProcesses().some((p) => p.pid === pid));
    assert.deepEqual(recorded(directory, 'released'), [`${pid} removed`]);
    const worker = path.join(path.dirname(fs.realpathSync(server)), 'virgil-lsp-worker');
    const processes = groupProcesses();
    const servers = processes.filter((p) => runs(p, server));
    assert.deepEqual(servers.map((p) => p.ppid), [replacement], `running: ${JSON.stringify(servers)}`);
    assert.ok(processes.every((p) => !runs(p, worker) || p.ppid === servers[0].pid), `a stray worker: ${JSON.stringify(processes)}`);
  }, async () => {
    try {
      fs.rmSync(path.join(directory, 'linger'), { force: true });
      fs.rmSync(path.join(directory, 'lingering'), { force: true });
      await restoreServer(server, words);
      await expectParserDiagnostic(bad);
      // Each start records its exit once its server has exited, and the
      // lingering one releases once its file is gone.
      const lingered = fs.existsSync(path.join(directory, 'child')) ? 1 : 0;
      await waitFor('the servers started from the script to exit',
        () => recorded(directory, 'exits').length === recorded(directory, 'starts').length
          && recorded(directory, 'released').length === lingered);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

// Output on the server's stdout that isn't a message, such as a line that a
// wrapper on virgil.server.path writes, leaves the connection open, but the
// client can no longer tell where a message starts: every later one fails to
// read, so no response arrives. vscode-languageclient's default error handler
// reports the error, with an error notification, and stops the client, and
// the extension leaves the error to it, rather than keep a client that can't
// hear its server. The server's answer to shutdown can't be read either, so
// the stop times out, and 2 seconds later the library kills the processes
// that it started. The server starts as the child of a recoveryServer script,
// which writes one line to the client when the test asks.
async function lostFraming(failures, bad, words, pendingRequest = false) {
  const server = selectedServer();
  const directory = fs.mkdtempSync(path.join(path.dirname(project), 'framing-'));
  const remaining = () => {
    const [child] = recorded(directory, 'child').map(Number);
    return groupProcesses().filter((p) => p.args.includes(directory) || p.pid === child || p.ppid === child);
  };
  const name = pendingRequest ? 'vscode.recovery.lost-framing-request'
    : 'a line on the server\'s output that is not a message: the client reports it and stops';
  await scenario(failures, name, async () => {
    fs.writeFileSync(path.join(directory, 'stray'), '');
    const { pid } = await startRecoveryServer(server, directory);
    await expectParserDiagnostic(bad);
    await waitFor('the type error in words/Words.v3', () => hasTypeError(words), 40000);
    const traced = logFromHere();
    await level(vscode.LogLevel.Trace);
    await waitFor('the trace to start', () => traced().includes("Sending notification '$/setTrace'"));
    const log = logFromHere();
    // Keep the request pending until the bad line: otherwise a background
    // response can trigger the read error and unregister the provider before
    // the command runs, or the request can finish before the line is written.
    // Resume the server only after the script writes the line, so that its
    // response completes the malformed header and triggers the read error.
    const child = Number(recorded(directory, 'child')[0]);
    process.kill(child, 'SIGSTOP');
    let began;
    let settled;
    try {
      // Rename propagates the provider's error; hover returns an empty list.
      const request = pendingRequest
        ? vscode.commands.executeCommand('vscode.executeDocumentRenameProvider', bad.document.uri, new vscode.Position(0, 0), 'Renamed')
        : vscode.commands.executeCommand('vscode.executeHoverProvider', bad.document.uri, new vscode.Position(0, 0));
      request.then(
        (value) => { settled = { value, ms: performance.now() - began }; },
        (error) => { settled = { error, ms: performance.now() - began }; },
      );
      // VS Code prepares a rename first. With the server paused, that
      // request must fail before the command could send textDocument/rename.
      const method = pendingRequest ? 'textDocument/prepareRename' : 'textDocument/hover';
      await waitFor('the request to be sent before the bad line', () => log().includes(`Sending request '${method}`));
      assert.equal(settled, undefined, 'the request settled before the bad line');
      began = performance.now();
      fs.writeFileSync(path.join(directory, 'inject'), '');
      await waitFor('the script to write the line', () => fs.existsSync(path.join(directory, 'injected')));
    } finally {
      process.kill(child, 'SIGCONT');
    }
    if (pendingRequest) {
      // Rejection is local, with no response or shutdown to wait for. Allow
      // one second for Extension Host scheduling and command IPC, still well
      // below the library's two-second shutdown timeout.
      await waitFor('the rename that hit lost framing to settle with an error within 1000 ms', () => settled, 1000);
      assert.ok(settled.ms < 1000, JSON.stringify(settled));
      assert.match(String(settled.error), /The language server connection failed: Header must provide a Content-Length property/);
      assert.ok(!log().includes('Stopping server timed out'), log());
      console.log(`Lost-framing rename rejected after ${Math.round(settled.ms)} ms`);
    }
    await waitFor('the client to report the error and stop', () => log().includes('Shutting down server.'))
      .catch((error) => { throw new Error(`${error.message}; the log since the line:\n${log()}`); });
    expectInOrder(log(), ['[error] Client Virgil Language Server: connection to server is erroring.', 'Shutting down server.']);
    // The stopping client's diagnostics and features went with it.
    assert.deepEqual([diagnostics(bad), diagnostics(words)], [[], []]);
    assert.deepEqual(await symbols(bad), []);
    // The library kills the script, and the server and its worker with it.
    const killed = /Server process exited with (signal SIGKILL|code 137)\./;
    await waitFor('the library to kill the script and the server', () => killed.test(log()) && remaining().length === 0)
      .catch((error) => { throw new Error(`${error.message}; running: ${JSON.stringify(remaining())}; the log since the line:\n${log()}`); });
    const text = log();
    expectInOrder(text, ['Stopping server timed out', 'Server process exited with ']);
    assert.deepEqual([count(text, 'Server process exited with '), count(text, 'Server will restart.'), count(text, 'will not be restarted')], [1, 0, 0], text);
    assert.deepEqual(recorded(directory, 'starts'), [String(pid)], 'the client started another server');
  }, async () => {
    try {
      await level(vscode.LogLevel.Info);
      fs.rmSync(path.join(directory, 'straying'), { force: true });
      // A client that still runs the script, as one that let the error pass
      // would, can't stop it either: the library kills it 2 seconds after the
      // stop times out, so the restore waits for that before it checks the
      // restarted server.
      if (setting('virgil.server.path') !== server) {
        const log = logFromHere();
        await setSetting('virgil.server.path', server);
        await waitFor('the server to start again', () => {
          const text = log();
          return text.includes(`Starting ${server} --stdio`) && text.includes('Started virgil-lsp ');
        }, 20000);
      }
      await waitFor('the script and its server to exit', () => remaining().length === 0, 15000);
      await restoreServer(server, words);
      await expectParserDiagnostic(bad);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

// vscode-languageclient restarts a crashed server up to 4 times in 3 minutes,
// and stops the client at the fifth crash, with its message; the extension
// keeps that policy. The test kills the server 5 times in a row, each time
// once the server that the client started has published bad.v3's diagnostic.
async function crashLimit(failures, bad, words) {
  const server = selectedServer();
  const directory = fs.mkdtempSync(path.join(path.dirname(project), 'crashes-'));
  const limit = 'The Virgil Language Server server crashed 5 times in the last 3 minutes. '
    + 'The server will not be restarted. See the output for more information.';
  await scenario(failures, 'server killed 5 times in a row: 4 restarts, then the crash limit and no client', async () => {
    let { pid } = await startRecoveryServer(server, directory);
    const log = logFromHere();
    for (let crash = 1; crash <= 4; crash++) {
      await expectParserDiagnostic(bad);
      killServer(pid, server);
      pid = await waitFor(`restart ${crash}`, () => {
        const starts = recorded(directory, 'starts');
        return count(log(), 'Server will restart.') === crash && starts.length === crash + 1 && Number(starts[crash]);
      });
    }
    await expectParserDiagnostic(bad);
    killServer(pid, server);
    await waitFor('the crash limit, and the five exits', () => log().includes(limit)
      && count(log(), 'Server process exited with signal SIGKILL.') === 5);
    const text = log();
    assert.deepEqual([count(text, 'Server will restart.'), count(text, limit)], [4, 1], text);
    assert.equal(recorded(directory, 'starts').length, 5);
    // The stopped client's diagnostics went with it, and nothing is left
    // running.
    assert.deepEqual(diagnostics(bad), []);
    const worker = path.join(path.dirname(fs.realpathSync(server)), 'virgil-lsp-worker');
    await waitFor('no virgil-lsp or virgil-lsp-worker', () => !groupProcesses().some((p) => runs(p, server) || runs(p, worker)));
    assert.equal(recorded(directory, 'starts').length, 5, 'a server started after the crash limit');
  }, async () => {
    try {
      await restoreServer(server, words);
      await expectParserDiagnostic(bad);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

// The client sends a full-text change 250 ms after an edit, or before its next
// request or notification, so a restart at once after an unsaved edit stops the
// client with the edit still pending, and the client never sends it. The new
// server must get the edit's text from its didOpen, which the client sends for
// each open document as it starts. The server starts from a recoveryServer
// script that records each start's input, which tells what reached each
// server: if the old server got the edit, the restart didn't find it pending,
// so the scenario fails rather than pass without testing that.
//
// vscode-languageclient, 11.0.0 included, can then log "Delivering pending
// changes failed" and "TypeError: this.task is not a function" in the output
// channel, about half a second after the edit, often after the scenario has
// ended: the stopped client's timer for the change outlives it, and its retry
// fails (https://github.com/microsoft/vscode-languageserver-node/issues/1916).
// Any restart with a change pending can log it, as other scenarios' restarts
// and this one's restore sometimes do. It loses nothing, depends on timing,
// and goes away with a fixed release, so the scenario neither requires nor
// forbids it, and no check of this suite counts it as a failure.
async function pendingEditRestart(failures, bad, words) {
  const server = selectedServer();
  const breadcrumbs = setting('breadcrumbs.enabled');
  const directory = fs.mkdtempSync(path.join(path.dirname(project), 'pending-'));
  const { document } = bad;
  const uri = document.uri.toString();
  const saved = fs.readFileSync(document.uri.fsPath, 'utf8');
  const active = vscode.window.activeTextEditor?.document;
  await scenario(failures, 'Virgil: Restart Language Server with an unsaved edit still pending: '
    + 'the new server gets its exact text, then a later edit', async () => {
    // Breadcrumbs request bad.v3's document symbols when the new client's
    // provider registers. In vscode-languageclient 10.1.2, the didOpen of a
    // visible document such as bad.v3 first sends the hidden documents'
    // pending didOpens, one by one, and a request for it doesn't wait for a
    // didOpen already on its way, so the request can reach the new server
    // first. The server answers it with no symbols, and VS Code keeps that
    // answer for the document's version, which the check of the new server's
    // symbols below would read. The scenario checks the pending edit, so it
    // turns breadcrumbs off.
    await setSetting('breadcrumbs.enabled', false);
    assert.ok(document.getText() === saved && !document.isDirty, 'bad.v3 is not as saved');
    const editor = await vscode.window.showTextDocument(document, { preview: false });
    fs.writeFileSync(path.join(directory, 'record'), '');
    const { pid } = await startRecoveryServer(server, directory);
    await expectParserDiagnostic(editor);
    await waitFor('the type error in words/Words.v3', () => hasTypeError(words), 40000);
    // Nothing may send a request or a notification between the edit and the
    // restart. VS Code runs the command in this extension host before
    // executeCommand returns, and the queued restart starts to stop the old
    // client at once.
    await edit(editor, (builder) => builder.replace(
      new vscode.Range(0, 0, document.lineCount, 0), 'component RestartLatest { def value = 1; }\n',
    ));
    const [text, version] = [document.getText(), document.version];
    await vscode.commands.executeCommand('virgil.server.restart');
    const starts = recorded(directory, 'starts').map(Number);
    assert.equal(starts.length, 2, `expected the old server's start and its replacement's, not ${starts.join(', ')}`);
    // The script records an exit once the server and the copy of its input are
    // done. The server exits with status 0 only after shutdown and then exit.
    await waitFor('the old server to exit', () => recorded(directory, 'exits').length > 0);
    assert.deepEqual(recorded(directory, 'exits'), [`${pid} 0`], 'the old server did not exit with status 0');
    const old = serverInput(directory, pid);
    const opened = old.find((m) => m.method === 'textDocument/didOpen' && m.params.textDocument.uri === uri);
    assert.equal(opened?.params.textDocument.text, saved, 'the old server did not get the saved bad.v3 in a didOpen');
    const sent = old.filter((m) => m.method === 'textDocument/didChange' && m.params.textDocument.uri === uri
      && m.params.contentChanges.some((change) => change.text.includes('RestartLatest')));
    assert.equal(sent.length, 0, 'the old server got the edit before the restart, in a didChange of version '
      + `${sent.map((m) => m.params.textDocument.version)}: something sent it first, so the restart didn't find it pending`);
    // The new server's didOpen has the buffer's text and version, which its
    // document symbols come from: the saved text has none.
    const reopened = await waitFor('the new server to get bad.v3 in a didOpen', () => serverInput(directory, starts[1])
      .find((m) => m.method === 'textDocument/didOpen' && m.params.textDocument.uri === uri));
    assert.deepEqual([reopened.params.textDocument.text, reopened.params.textDocument.version], [text, version]);
    assert.deepEqual(await symbols(editor), [{ name: 'RestartLatest', kind: vscode.SymbolKind.Namespace }]);
    // A later edit reaches the new server as usual.
    await edit(editor, (builder) => builder.replace(
      new vscode.Range(0, 0, document.lineCount, 0), 'component RestartLater { def value = 2; }\n',
    ));
    const [later, next] = [document.getText(), document.version];
    const change = await waitFor('the later edit to reach the new server', () => serverInput(directory, starts[1])
      .find((m) => m.method === 'textDocument/didChange' && m.params.textDocument.uri === uri
        && m.params.textDocument.version === next));
    assert.deepEqual(change.params.contentChanges, [{ text: later }]);
    assert.deepEqual(await symbols(editor), [{ name: 'RestartLater', kind: vscode.SymbolKind.Namespace }]);
  }, async () => {
    try {
      await setSetting('breadcrumbs.enabled', breadcrumbs);
      if (document.isDirty) {
        await vscode.window.showTextDocument(document, { preview: false });
        await vscode.commands.executeCommand('workbench.action.files.revert');
        await waitFor('bad.v3 as saved again', () => document.getText() === saved && !document.isDirty);
      }
      await restoreServer(server, words);
      await expectParserDiagnostic(bad);
      if (active !== undefined) await vscode.window.showTextDocument(active, { preview: false });
      // Each start records its exit in directory once its server has exited.
      await waitFor('the servers started from the script to exit',
        () => recorded(directory, 'exits').length === recorded(directory, 'starts').length);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

// The log of this extension host, whose termination releases the held
// start in closeDuringRecovery: the exthost.log that names this process.
function extensionHostLog() {
  const logs = process.env.VIRGIL_E2E_LOGS;
  const started = `Extension host with pid ${process.pid} started`;
  const found = fs.readdirSync(logs, { recursive: true })
    .filter((file) => path.basename(file) === 'exthost.log')
    .map((file) => path.join(logs, file))
    .filter((file) => fs.readFileSync(file, 'utf8').includes(started));
  assert.equal(found.length, 1, `expected one extension host log in ${logs} that says "${started}"`);
  return found[0];
}

// The launch that closes the window during the recovery, which run.cjs
// starts with VIRGIL_E2E_RECOVERY set to a new directory for the script.
// The suite kills the server and ends while the recovery start is held,
// which closes the window. The extension host logs that it is terminating
// and then deactivates the extension, before it can read the held server's
// answer to initialize: so the start goes on only when the extension's stop
// is queued. run.cjs checks the result once the window has closed.
async function closeDuringRecovery(extension, directory) {
  await waitFor('activation by the workspace project file', () => extension.isActive);
  await waitFor('the server to start', () => channelLog().includes('Started virgil-lsp '));
  const server = selectedServer();
  const { pid } = await startRecoveryServer(server, directory);
  await expectParserDiagnostic(await open('bad.v3'));
  await killAndHold(directory, pid, server, extensionHostLog());
  console.log('PASS: VS Code server killed; closing the window during the recovery');
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
  const recovery = process.env.VIRGIL_E2E_RECOVERY;
  if (recovery !== undefined) await closeDuringRecovery(extension, recovery);
  else if (vscode.workspace.workspaceFolders === undefined) await singleFile(extension);
  else await workspace(extension);
}

module.exports = { run };
