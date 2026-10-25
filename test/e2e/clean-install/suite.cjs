// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0
// Extension Host observer. The installed VSIX supplies all client configuration.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
exports.run = async function () {
  const extension = vscode.extensions.getExtension('virgil-language-server.virgil-lsp-vscode');
  assert.ok(extension, 'README did not install the VSIX');
  assert.ok(extension.extensionPath.startsWith(path.join(process.env.HOME, '.vscode', 'extensions') + path.sep), extension.extensionPath);
  const config = vscode.workspace.getConfiguration('virgil');
  assert.equal(config.get('server.path'), '');
  assert.equal(config.inspect('server.path').globalValue, undefined);
  assert.equal(config.get('virgilRoot'), '');
  const file = vscode.Uri.file(path.join(process.env.CLEAN_PROJECT, 'Install.v3'));
  const document = await vscode.workspace.openTextDocument(file);
  await vscode.window.showTextDocument(document);
  const until = Date.now() + 60000;
  while (!vscode.languages.getDiagnostics(file).some((d) => d.severity === vscode.DiagnosticSeverity.Error && d.message === 'expected int in var initialization, got Array<byte>')) {
    assert.ok(Date.now() < until, 'no string-to-int diagnostic; check README installation/configuration');
    await delay(100);
  }
  const position = new vscode.Position(2, document.lineAt(2).text.indexOf('StringBuilder'));
  const definitions = await vscode.commands.executeCommand('vscode.executeDefinitionProvider', file, position);
  assert.equal(definitions.length, 1);
  const definition = definitions[0];
  const uri = definition.uri || definition.targetUri;
  const range = definition.range || definition.targetSelectionRange;
  if (!process.env.VIRGIL_LOC) {
    for (const entry of process.env.PATH.split(path.delimiter)) {
      assert.ok(!fs.existsSync(path.join(entry, 'virgil-lsp')), 'external server on PATH');
    }
  }
  const library = path.join(process.env.VIRGIL_LOC || path.join(extension.extensionPath, 'server/virgil'), 'lib/util/StringBuilder.v3');
  assert.equal(uri.fsPath, library);
  assert.match(fs.readFileSync(library, 'utf8').split('\n')[range.start.line], /class StringBuilder/);
  const hovers = await vscode.commands.executeCommand('vscode.executeHoverProvider', file, position);
  assert.ok(hovers.some((h) => h.contents.some((c) => c.value.includes('class StringBuilder'))));
  const mode = process.env.VIRGIL_LOC ? 'explicit' : 'bundled';
  fs.writeFileSync(path.join(process.env.HOME, `vscode-${mode}.json`), JSON.stringify({
    checks: ['diagnostic', 'definition', 'hover'], root: process.env.VIRGIL_LOC || null,
    extension: extension.extensionPath, definition: library,
  }) + '\n');
  console.log(`PASS diagnostic, library definition, hover; root=${process.env.VIRGIL_LOC || 'bundled'}`);
};
