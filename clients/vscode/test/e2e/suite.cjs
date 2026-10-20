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

function count(text, substring) {
  return text.split(substring).length - 1;
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
  assert.deepEqual(await symbols(words), [{ name: 'Words', kind: vscode.SymbolKind.Namespace }]);
  console.log('PASS: VS Code workspace activation, diagnostics publish/clear/undo, symbols, '
    + 'Virgil root, definition, hover, signature help, references, workspace symbols, project file edits, and restart with a clean server exit');
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
