// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const { PassThrough, Writable } = require('node:stream');
const { test } = require('node:test');
const vm = require('node:vm');

// Run the installed language client's hover provider, request error handling,
// and notification code. Only the editor API and client startup are replaced.
const notifications = [];
const vscode = {
  version: '1.91.0',
  LogLevel: { Info: 3 },
  CodeActionKind: { Empty: {} },
  window: {
    showErrorMessage: (message) => { notifications.push(message); return Promise.resolve(); },
  },
  languages: { registerHoverProvider: () => ({ dispose() {} }) },
};
for (const name of ['CancellationError', 'CompletionItem', 'CodeLens', 'DocumentLink', 'CodeAction',
  'Diagnostic', 'CallHierarchyItem', 'TypeHierarchyItem', 'SymbolInformation', 'InlayHint']) {
  vscode[name] = class {};
}
const load = Module._load;
let library;
let HoverFeature;
try {
  Module._load = function (name, ...args) {
    return name === 'vscode' ? vscode : load.call(this, name, ...args);
  };
  library = require('vscode-languageclient/node');
  ({ HoverFeature } = require('vscode-languageclient/$test/common/hover'));
} finally {
  Module._load = load;
}

async function clientForTest(t) {
  notifications.length = 0;
  const logs = [];
  const channel = { info() {}, error: (text) => logs.push(text), show() {} };
  let client;
  let stops = 0;
  class LanguageClient extends library.LanguageClient {
    constructor(...args) { super(...args); client = this; }
    registerBuiltinFeatures() {}
    start() { return Promise.resolve(); }
    stop() { stops++; return Promise.resolve(); }
    isRunning() { return false; }
    get state() { return library.State.Running; }
    get visibleDocuments() { return { dispose() {} }; }
  }
  const editor = {
    ...vscode,
    window: { ...vscode.window, createOutputChannel: () => channel },
    commands: { registerCommand: () => ({ dispose() {} }) },
    workspace: {
      getConfiguration: () => ({ get: (key, fallback) => key === 'server.path' ? '/server/virgil-lsp' : fallback }),
      onDidChangeConfiguration: () => ({ dispose() {} }),
      createFileSystemWatcher: () => ({ dispose() {} }),
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../out/extension.js'), 'utf8'), {
    module, exports: module.exports, process,
    require(name) {
      if (name === 'vscode') return editor;
      if (name === 'vscode-languageclient/node') return { ...library, LanguageClient };
      if (name === 'node:fs/promises') return {
        realpath: async (file) => file,
        stat: async () => ({ isFile: () => true }),
        access: async () => {},
      };
      return require(name);
    },
  });
  await module.exports.activate({ subscriptions: [] });
  assert.ok(client, 'activation created a client');
  t.after(() => module.exports.deactivate());
  return { client, logs, stops: () => stops };
}

for (const code of ['EPIPE', 'ECONNRESET', 'ERR_STREAM_DESTROYED', 'ERR_STREAM_WRITE_AFTER_END', 'serialization']) {
  test(`hover after a ${code} write failure leaves notifications to the connection handler`, async (t) => {
    const { client, logs, stops } = await clientForTest(t);
    const input = new PassThrough();
    const output = new Writable({
      write(chunk, encoding, done) { done(Object.assign(new Error(`write ${code}`), { code })); },
    });
    const connection = library.createMessageConnection(input, output, { error() {}, warn() {}, info() {}, log() {} });
    t.after(() => { connection.dispose(); input.destroy(); output.destroy(); });
    const handled = [];
    connection.onError(([error, message, count]) => {
      handled.push(client.handleConnectionError(error, message, count));
    });
    connection.listen();
    client.sendRequest = (type, params, token) => connection.sendRequest(type,
      code === 'serialization' ? { ...params, invalid: 1n } : params, token);
    const [, hover] = new HoverFeature(client).registerLanguageProvider({ documentSelector: ['virgil'] });
    await assert.rejects(hover.provideHover(
      { uri: { toString: () => 'file:///bad.v3' } }, { line: 0, character: 0 }, library.CancellationToken.None,
    ), (error) => error.code === library.ErrorCodes.MessageWriteError);
    await Promise.all(handled);
    assert.ok(logs.some((text) => text.includes('Request textDocument/hover failed.')), 'the request failure is logged');
    if (['EPIPE', 'ECONNRESET', 'ERR_STREAM_DESTROYED'].includes(code)) {
      assert.ok(logs.some((text) => text.includes(`write ${code}`)), 'the pipe error is logged');
      assert.deepEqual([...notifications], [], 'the failed hover must not raise a notification');
      assert.equal(stops(), 0, 'the error leaves recovery to the close handler');
    } else {
      assert.ok(notifications.some((text) => text.includes('Shutting down server.')), 'the default handler reports the fault');
      assert.equal(stops(), 1, 'the default handler stops the client');
    }
  });
}

test('a server request error still raises its notification', async (t) => {
  const { client } = await clientForTest(t);
  const error = new library.ResponseError(library.ErrorCodes.InvalidParams, 'Invalid hover position');
  client.sendRequest = async () => { throw error; };
  const [, hover] = new HoverFeature(client).registerLanguageProvider({ documentSelector: ['virgil'] });
  await assert.rejects(hover.provideHover(
    { uri: { toString: () => 'file:///bad.v3' } }, { line: 0, character: 0 }, library.CancellationToken.None,
  ), (caught) => caught === error);
  assert.equal(notifications.length, 1);
  assert.match(notifications[0], /Request textDocument\/hover failed\.[\s\S]*Invalid hover position/);
});

test('lost framing rejects every pending request, including one waiting for document sync', { timeout: 2000 }, async (t) => {
  const { client, stops } = await clientForTest(t);
  const input = new PassThrough();
  const output = new PassThrough();
  let sent = '';
  const written = new Promise((resolve) => output.on('data', (chunk) => {
    sent += chunk.toString();
    if (sent.includes('textDocument/documentSymbol')) resolve();
  }));
  const connection = library.createMessageConnection(input, output, { error() {}, warn() {}, info() {}, log() {} });
  t.after(() => { connection.dispose(); input.destroy(); output.destroy(); });
  connection.onError(([error, message, count]) => {
    void client.handleConnectionError(error, message, count);
  });
  connection.listen();
  // Keep the installed library's sendRequest and JSON-RPC connection; replace
  // startup and document synchronization, which need a real editor.
  client.$start = async () => connection;
  let release;
  const sync = new Promise((resolve) => { release = resolve; });
  let opens = 0;
  client._didOpenTextDocumentFeature = {
    sendPendingOpenNotifications: () => ++opens === 4 ? sync : Promise.resolve(),
  };
  client._didChangeTextDocumentFeature = { syncKind: 0 };
  const params = { textDocument: { uri: 'file:///bad.v3' } };
  const methods = ['textDocument/hover', 'textDocument/rename', 'textDocument/documentSymbol', 'textDocument/hover'];
  const requests = methods.map((method) => client.sendRequest(method, params, library.CancellationToken.None));
  const results = Promise.allSettled(requests);
  await written;
  assert.equal((sent.match(/Content-Length:/g) ?? []).length, 3, sent);
  input.write('wrapper banner\nContent-Length: 2\r\n\r\n{}');
  for (const result of await results) {
    assert.equal(result.status, 'rejected');
    assert.equal(result.reason.code, library.ErrorCodes.MessageReadError);
    assert.match(result.reason.message, /Header must provide a Content-Length property/);
    assert.throws(() => client.handleFailedRequest({ method: 'textDocument/hover' }, undefined, result.reason, undefined),
      (error) => error === result.reason);
  }
  assert.equal(stops(), 1);
  assert.equal(notifications.length, 1, 'only the connection error raises a notification');
  await assert.rejects(client.sendRequest('textDocument/hover', params),
    (error) => error.code === library.ErrorCodes.MessageReadError);
  // A late completion or rejection of an underlying request remains handled.
  release();
});
