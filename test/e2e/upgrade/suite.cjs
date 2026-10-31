// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Extension Host observer for one upgrade rehearsal phase (see vscode.cjs).
// The installed VSIX supplies all client configuration; this registers no
// server and changes no setting. It checks only what every rehearsed release
// supports: the active extension's version, its bundled server's, a
// diagnostic, a definition into the bundled lib/util, and hover.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vscode = require('vscode');

const EXTENSION = 'virgil-language-server.virgil-lsp-vscode';
const CHANNEL_LOG = 'Virgil Language Server.log';
const DIAGNOSTIC = 'expected int in var initialization, got Array<byte>';
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function channelLog() {
  const logs = process.env.UPGRADE_LOGS;
  if (!fs.existsSync(logs)) return '';
  return fs.readdirSync(logs, { recursive: true }).filter((name) => path.basename(name) === CHANNEL_LOG)
    .map((name) => fs.readFileSync(path.join(logs, name), 'utf8')).join('');
}

async function waitFor(what, predicate, ms = 60000) {
  const until = Date.now() + ms;
  while (!predicate()) {
    assert.ok(Date.now() < until, `no ${what} within ${ms / 1000} seconds`);
    await delay(100);
  }
}

exports.run = async function () {
  const expected = JSON.parse(process.env.UPGRADE_EXPECTED);
  const installed = vscode.extensions.all.filter((e) => e.id.toLowerCase() === EXTENSION);
  assert.equal(installed.length, 1, `expected one ${EXTENSION}, found ${installed.map((e) => e.extensionPath)}`);
  const extension = installed[0];
  // The version that VS Code loaded, not just the one last installed.
  assert.equal(extension.packageJSON.version, expected.extensionVersion, 'the active extension version');
  assert.equal(extension.extensionPath,
    path.join(process.env.UPGRADE_EXTENSIONS_DIR, `${EXTENSION}-${expected.extensionVersion}`));
  const config = vscode.workspace.getConfiguration('virgil');
  for (const name of ['server.path', 'virgilRoot']) {
    const inspected = config.inspect(name);
    assert.deepEqual([inspected.globalValue, inspected.workspaceValue, inspected.workspaceFolderValue],
      [undefined, undefined, undefined], `virgil.${name} is set`);
  }
  assert.equal(process.env.VIRGIL_LOC, undefined);
  for (const entry of (process.env.PATH || '').split(path.delimiter)) {
    assert.ok(!fs.existsSync(path.join(entry, 'virgil-lsp')), `external server on PATH: ${entry}`);
  }
  const server = path.join(extension.extensionPath, 'server', 'virgil-lsp');
  const bundledVersion = execFileSync(server, ['--version'], { encoding: 'utf8', env: { PATH: '/usr/bin:/bin' } });
  assert.equal(bundledVersion, expected.versionOutput, 'the bundled server version');

  const file = vscode.Uri.file(path.join(process.env.UPGRADE_PROJECT, 'Install.v3'));
  const document = await vscode.workspace.openTextDocument(file);
  await vscode.window.showTextDocument(document);
  await waitFor(`diagnostic ${JSON.stringify(DIAGNOSTIC)}`, () => vscode.languages.getDiagnostics(file)
    .some((d) => d.severity === vscode.DiagnosticSeverity.Error && d.message === DIAGNOSTIC));
  assert.equal(extension.isActive, true);
  const position = new vscode.Position(2, document.lineAt(2).text.indexOf('StringBuilder'));
  const definitions = await vscode.commands.executeCommand('vscode.executeDefinitionProvider', file, position);
  assert.equal(definitions.length, 1);
  const uri = definitions[0].uri || definitions[0].targetUri;
  const range = definitions[0].range || definitions[0].targetSelectionRange;
  const root = path.join(extension.extensionPath, 'server', 'virgil');
  const library = path.join(root, 'lib/util/StringBuilder.v3');
  assert.equal(uri.fsPath, library);
  assert.match(fs.readFileSync(library, 'utf8').split('\n')[range.start.line], /class StringBuilder/);
  const hovers = await vscode.commands.executeCommand('vscode.executeHoverProvider', file, position);
  assert.ok(hovers.some((h) => h.contents.some((c) => (c.value ?? String(c)).includes('class StringBuilder'))),
    'hover on StringBuilder');

  // VS Code writes the output channel's log asynchronously.
  const lines = [
    `Server selection: ${server} (bundled for linux-x64;`,
    `Starting ${server} --stdio`,
    'Started virgil-lsp ',
    `Virgil root: ${root} (bundle, revision ${expected.virgilRevision})`,
  ];
  await waitFor(`output channel lines ${JSON.stringify(lines)}`, () => lines.every((line) => channelLog().includes(line)));
  assert.equal(channelLog().split('\n').filter((line) => line.includes(' Starting ')).length, 1, 'one server start');
  // The version in the running server's initialize reply.
  const serverStarted = /Started virgil-lsp (\S+)/.exec(channelLog())[1];
  assert.equal(serverStarted, expected.serverVersion, 'the running server version');
  fs.writeFileSync(process.env.UPGRADE_EVIDENCE, JSON.stringify({
    extensionVersion: extension.packageJSON.version,
    extensionPath: extension.extensionPath,
    bundledVersion,
    serverStarted,
    vscodeVersion: vscode.version,
    checks: ['active extension version', 'bundled server --version', 'no server or root override',
      'diagnostic', 'library definition', 'hover', 'bundled server selection and root'],
    definition: library,
  }, null, 2) + '\n');
  console.log(`PASS extension ${extension.packageJSON.version}: bundled server ${expected.serverVersion}, `
    + 'diagnostic, library definition, hover');
};
