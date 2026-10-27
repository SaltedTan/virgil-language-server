// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const { PassThrough, Writable } = require('node:stream');
const { test } = require('node:test');
const vm = require('node:vm');
const { StreamMessageReader, StreamMessageWriter } = require('vscode-jsonrpc/node');

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

// The editor's output channel, which collects the client's error logs.
function outputChannel() {
  const logs = [];
  return { logs, channel: { info() {}, warn() {}, error: (text) => logs.push(text), show() {} } };
}

// The installed library's client without its built-in features, whose start
// does nothing, since starting needs a real editor.
class UnstartedClient extends library.LanguageClient {
  registerBuiltinFeatures() {}
  start() { return Promise.resolve(); }
  get visibleDocuments() { return { dispose() {} }; }
}

// Activates the extension with the installed library's client. Unless
// stopping is true, the client's stop only counts its calls, and the client
// reports itself running but never stops a server.
async function clientForTest(t, { stopping = false } = {}) {
  notifications.length = 0;
  const { logs, channel } = outputChannel();
  let client;
  let stops = 0;
  class Stoppable extends UnstartedClient {
    constructor(...args) { super(...args); client = this; }
  }
  class LanguageClient extends Stoppable {
    stop() { stops++; return Promise.resolve(); }
    isRunning() { return false; }
    get state() { return library.State.Running; }
  }
  const editor = {
    ...vscode,
    window: { ...vscode.window, createOutputChannel: () => channel },
    commands: { registerCommand: () => ({ dispose() {} }) },
    workspace: {
      getConfiguration: () => ({ get: (key, fallback) => key === 'virgil.server.path' ? '/server/virgil-lsp' : fallback }),
      onDidChangeConfiguration: () => ({ dispose() {} }),
      createFileSystemWatcher: () => ({ dispose() {} }),
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../out/extension.js'), 'utf8'), {
    module, exports: module.exports, process,
    require(name) {
      if (name === 'vscode') return editor;
      if (name === 'vscode-languageclient/node') return { ...library, LanguageClient: stopping ? Stoppable : LanguageClient };
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

// A scripted server on in-memory streams. It records the method of each
// message it reads; received(method) waits for the first message with that
// method, and reply answers a request.
function scriptedServer(t) {
  const toServer = new PassThrough();
  const toClient = new PassThrough();
  const reader = new StreamMessageReader(toServer);
  const writer = new StreamMessageWriter(toClient);
  const messages = [];
  const waiting = [];
  reader.listen((message) => {
    messages.push(message);
    for (const wait of waiting.filter(({ method }) => method === message.method)) {
      waiting.splice(waiting.indexOf(wait), 1);
      wait.resolve(message);
    }
  });
  t.after(() => { reader.dispose(); writer.dispose(); toServer.destroy(); toClient.destroy(); });
  return {
    transports: () => ({ reader: new StreamMessageReader(toClient), writer: new StreamMessageWriter(toServer) }),
    methods: () => messages.map((message) => message.method),
    received: (method) => new Promise((resolve) => {
      const message = messages.find((candidate) => candidate.method === method);
      if (message !== undefined) resolve(message); else waiting.push({ method, resolve });
    }),
    reply: (id, response) => writer.write({ jsonrpc: '2.0', id, ...response }),
  };
}

// Connects a client to a scripted server as if it had started, with a
// message trace. A request's wait for the pending didOpen notifications
// resolves held, and lasts until release is called.
async function connectHeld(client, server) {
  client.createMessageTransports = async () => server.transports();
  const connection = await client.createConnection();
  connection.listen();
  const trace = [];
  await connection.trace(library.Trace.Messages, { log: (message) => trace.push(message) });
  client.$state = 'running';
  let release;
  let isHeld;
  const opened = new Promise((resolve) => { release = resolve; });
  const held = new Promise((resolve) => { isHeld = resolve; });
  client._didOpenTextDocumentFeature = { sendPendingOpenNotifications: () => { isHeld(); return opened; } };
  client._didChangeTextDocumentFeature = { syncKind: library.TextDocumentSyncKind.Incremental };
  return { trace, held, release };
}

// A hover request that waits behind a pending didOpen while stop() sends
// shutdown (#212): the stop begins once the request waits, and once shutdown
// has reached the server, the didOpen goes through. Once the request has reached the server or its caller has its
// result, VS Code cancels it, and the server answers the request, if it got
// it, as ours does after shutdown, then answers shutdown.
async function hoverDuringStop(t, client, stop) {
  const server = scriptedServer(t);
  const { trace, held, release } = await connectHeld(client, server);
  const [, hover] = new HoverFeature(client).registerLanguageProvider({ documentSelector: ['virgil'] });
  const source = new library.CancellationTokenSource();
  const hovering = hover.provideHover(
    { uri: { toString: () => 'file:///race.v3' } }, { line: 0, character: 0 }, source.token,
  ).then((value) => ({ value }), (error) => ({ error }));
  await held;
  const stopping = stop();
  const shutdown = await server.received('shutdown');
  release();
  const late = await Promise.race([hovering.then(() => undefined), server.received('textDocument/hover')]);
  source.cancel();
  if (late !== undefined) {
    await server.received('$/cancelRequest');
    server.reply(late.id, { error: { code: library.ErrorCodes.InvalidRequest, message: 'the server is shutting down' } });
  }
  server.reply(shutdown.id, { result: null });
  await server.received('exit');
  await stopping;
  return { methods: server.methods(), late, hover: await hovering, trace };
}

test('the library writes a request that waited for document sync after a stop\'s shutdown', { timeout: 2000 }, async (t) => {
  notifications.length = 0;
  const { channel } = outputChannel();
  const client = new UnstartedClient('virgil', 'Virgil', { command: 'virgil-lsp' }, { outputChannel: channel });
  const { methods, late, trace, hover } = await hoverDuringStop(t, client, () => client.stop());
  // Without the extension's check, the race of #212: if this fails because
  // the request no longer follows shutdown, the library has fixed it.
  assert.ok(late, 'the hover request reached the server');
  assert.deepEqual(methods, ['shutdown', 'textDocument/hover', '$/cancelRequest', 'exit']);
  assert.ok(trace.some((line) => line.startsWith('Sending request \'textDocument/hover')), trace.join('\n'));
});

test('a request that waited for document sync while the client stopped is dropped before its write', { timeout: 2000 }, async (t) => {
  const { client, logs } = await clientForTest(t, { stopping: true });
  const { methods, late, hover, trace } = await hoverDuringStop(t, client, () => client.stopInQueue());
  assert.equal(late, undefined, 'the hover request never reached the server');
  assert.deepEqual(methods, ['shutdown', 'exit'], 'only exit follows shutdown, with no $/cancelRequest');
  // The provider's caller gets no hover, as for a request on a stopped
  // client, with nothing in the trace or the log, and no notification.
  assert.deepEqual(hover, { value: null });
  assert.deepEqual(trace.filter((line) => line.includes('textDocument/hover')), []);
  assert.deepEqual(logs, []);
  assert.deepEqual([...notifications], []);
  // The client is stopped; a new request gets the library's own answer.
  await assert.rejects(client.sendRequest('textDocument/hover', {}),
    (error) => error.code === library.ErrorCodes.ConnectionInactive && error.message === 'Client is not running');
});
