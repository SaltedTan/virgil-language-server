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

// Run the installed language client's hover provider, document
// synchronization, request error handling, and notification code. Only the
// editor API and client startup are replaced.
const notifications = [];
const vscode = {
  version: '1.91.0',
  LogLevel: { Info: 3 },
  CodeActionKind: { Empty: {} },
  EventEmitter: class { event = () => ({ dispose() {} }); fire() {} dispose() {} },
  window: {
    showErrorMessage: (message) => { notifications.push(message); return Promise.resolve(); },
  },
  // The documents open in the editor; every one is a Virgil document.
  workspace: {
    textDocuments: [], onDidOpenTextDocument: () => ({ dispose() {} }), onDidChangeTextDocument: () => ({ dispose() {} }),
  },
  languages: { registerHoverProvider: () => ({ dispose() {} }), match: () => 10 },
};
for (const name of ['CancellationError', 'CompletionItem', 'CodeLens', 'DocumentLink', 'CodeAction',
  'Diagnostic', 'CallHierarchyItem', 'TypeHierarchyItem', 'SymbolInformation', 'InlayHint']) {
  vscode[name] = class {};
}
const load = Module._load;
let library;
let HoverFeature;
let DidOpenTextDocumentFeature;
let DidChangeTextDocumentFeature;
try {
  Module._load = function (name, ...args) {
    return name === 'vscode' ? vscode : load.call(this, name, ...args);
  };
  library = require('vscode-languageclient/node');
  ({ HoverFeature } = require('vscode-languageclient/$test/common/hover'));
  ({ DidOpenTextDocumentFeature, DidChangeTextDocumentFeature } = require('vscode-languageclient/$test/common/textSynchronization'));
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
    extensions: { getExtension: () => undefined },
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
// message it reads; received(method, matches) waits for the first message with
// that method that matches, and reply answers a request. The first write from
// the client whose text hold matches is held: it neither reaches the server nor
// completes until release is called, and held resolves once it is held.
function scriptedServer(t, { hold = () => false } = {}) {
  const toServer = new PassThrough();
  const toClient = new PassThrough();
  const reader = new StreamMessageReader(toServer);
  const writer = new StreamMessageWriter(toClient);
  const messages = [];
  const waiting = [];
  reader.listen((message) => {
    messages.push(message);
    for (const wait of waiting.filter(({ method, matches }) => method === message.method && matches(message))) {
      waiting.splice(waiting.indexOf(wait), 1);
      wait.resolve(message);
    }
  });
  let release;
  let isHeld;
  const held = new Promise((resolve) => { isHeld = resolve; });
  const fromClient = new Writable({
    write(chunk, encoding, done) {
      const pass = () => { toServer.write(chunk); done(); };
      if (release === undefined && hold(chunk.toString())) {
        release = pass;
        isHeld();
      } else {
        pass();
      }
    },
  });
  t.after(() => { reader.dispose(); writer.dispose(); fromClient.destroy(); toServer.destroy(); toClient.destroy(); });
  return {
    transports: () => ({ reader: new StreamMessageReader(toClient), writer: new StreamMessageWriter(fromClient) }),
    messages: () => messages,
    methods: () => messages.map((message) => message.method),
    received: (method, matches = () => true) => new Promise((resolve) => {
      const message = messages.find((candidate) => candidate.method === method && matches(candidate));
      if (message !== undefined) resolve(message); else waiting.push({ method, matches, resolve });
    }),
    reply: (id, response) => writer.write({ jsonrpc: '2.0', id, ...response }),
    held,
    release: () => release(),
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

// A Virgil document open in the editor.
function textDocument(uri, text) {
  return { uri: { toString: () => uri }, languageId: 'virgil', version: 1, getText: () => text };
}

const HIDDEN = 'file:///hidden.v3';
const SECOND = 'file:///second.v3';
const VISIBLE = 'file:///bad.v3';

// Requests sent while the didOpen of a hidden document is held (#318), as when
// a restarted client registers its providers and VS Code asks for the active
// editor's breadcrumbs. The client opens the documents as it does when it
// starts, with the server's text synchronization: the transport holds the
// first hidden document's didOpen, edit then runs, and the transport lets the
// didOpen through once every request has passed the library's own waits for
// document synchronization and reached the middleware, right before its
// write, or once a request's flush of pending full-text changes is about to
// write one, since that write would wait for the held one. The server answers each request with its method, and the client then
// stops. Returns the messages the server got, in order, each with its
// document's URI, the didChange messages, and the requests' results.
async function requestsBehindHiddenOpen(t, client, stop, { documents, syncKind, edit = async () => {}, requests }) {
  const server = scriptedServer(t, { hold: (text) => text.includes('"textDocument/didOpen"') && text.includes(HIDDEN) });
  client.createMessageTransports = async () => server.transports();
  const connection = await client.createConnection();
  connection.listen();
  client.$state = 'running';
  Object.defineProperty(client, 'visibleDocuments', {
    value: { isVisible: (document) => document.uri.toString() === VISIBLE, dispose() {} },
  });
  const registration = { documentSelector: [{ language: 'virgil' }], syncKind };
  client._didOpenTextDocumentFeature = new DidOpenTextDocumentFeature(client, client._syncedDocuments);
  client._didChangeTextDocumentFeature = new DidChangeTextDocumentFeature(client, new Map());
  client._didChangeTextDocumentFeature.register({ id: 'virgil', registerOptions: registration });
  const { middleware } = client.clientOptions;
  const sendRequest = middleware.sendRequest ?? ((type, param, token, next) => next(type, param, token));
  let count = 0;
  let allReached;
  const reached = new Promise((resolve) => { allReached = resolve; });
  middleware.sendRequest = (...args) => {
    if (++count === requests.length) allReached();
    return sendRequest(...args);
  };
  // A request's flush of pending full-text changes waits for their write.
  const feature = client._didChangeTextDocumentFeature;
  const aboutToSend = feature.aboutToSendNotification;
  const changing = new Promise((resolve) => {
    feature.aboutToSendNotification = (...args) => { resolve(); return aboutToSend.apply(feature, args); };
  });
  vscode.workspace.textDocuments = documents;
  t.after(() => { vscode.workspace.textDocuments = []; });
  client._didOpenTextDocumentFeature.register({ id: 'virgil', registerOptions: registration });
  await server.held;
  await edit(client._didChangeTextDocumentFeature);
  const results = requests.map((uri) => client.sendRequest('textDocument/documentSymbol', { textDocument: { uri } },
    library.CancellationToken.None));
  await Promise.race([reached, changing]);
  server.release();
  for (const uri of requests) {
    const request = await server.received('textDocument/documentSymbol', (message) => message.params.textDocument.uri === uri);
    server.reply(request.id, { result: request.method });
  }
  const symbols = await Promise.all(results);
  const stopping = stop();
  server.reply((await server.received('shutdown')).id, { result: null });
  await server.received('exit');
  await stopping;
  const order = server.messages().map(({ method, params }) => [method, params?.textDocument?.uri].filter(Boolean).join(' '));
  const changes = server.messages().filter(({ method }) => method === 'textDocument/didChange').map(({ params }) => params);
  return { order, changes, symbols };
}

// The library's client and the extension's.
const clients = [
  ['the library', async () => {
    const { channel } = outputChannel();
    const client = new UnstartedClient('virgil', 'Virgil', { command: 'virgil-lsp' }, { outputChannel: channel });
    return { client, stop: () => client.stop() };
  }],
  ['the extension', async (t) => {
    const { client } = await clientForTest(t, { stopping: true });
    return { client, stop: () => client.stopInQueue() };
  }],
];

for (const [name, create] of clients) {
  test(`${name} writes a request for a document after that document's didOpen while another didOpen is held`, { timeout: 2000 }, async (t) => {
    const { client, stop } = await create(t);
    const { order, symbols } = await requestsBehindHiddenOpen(t, client, stop, {
      documents: [textDocument(HIDDEN, 'component Hidden {}\n'), textDocument(SECOND, 'component Second {}\n'),
        textDocument(VISIBLE, 'component Bad {}\n')],
      syncKind: library.TextDocumentSyncKind.Full,
      // The visible document's, and the second hidden document's, whose
      // didOpen also comes after the held one.
      requests: [VISIBLE, SECOND],
    });
    // vscode-languageclient 10.1.2 sent a hidden document's didOpen only once
    // it was visible or a request or another document's didOpen sent the
    // pending ones, one by one, so a request could overtake its document's
    // didOpen. 11.0.0 sends every document's didOpen as it registers
    // (microsoft/vscode-languageserver-node#1868).
    assert.deepEqual(order, [
      `textDocument/didOpen ${HIDDEN}`, `textDocument/didOpen ${SECOND}`, `textDocument/didOpen ${VISIBLE}`,
      `textDocument/documentSymbol ${VISIBLE}`, `textDocument/documentSymbol ${SECOND}`, 'shutdown', 'exit',
    ]);
    assert.deepEqual(symbols, ['textDocument/documentSymbol', 'textDocument/documentSymbol']);
  });

  test(`${name} writes a full-text change made while a didOpen is held before a later request`, { timeout: 2000 }, async (t) => {
    const { client, stop } = await create(t);
    const visible = textDocument(VISIBLE, 'component Bad {}\n');
    const { order, changes } = await requestsBehindHiddenOpen(t, client, stop, {
      documents: [textDocument(HIDDEN, 'component Hidden {}\n'), visible],
      syncKind: library.TextDocumentSyncKind.Full,
      // The edit makes the visible document's version 2. The client sends a
      // full-text change 250 ms after an edit, or before its next request or
      // notification; the editor stub never fires the 250 ms delay.
      edit: (feature) => {
        visible.version = 2;
        visible.getText = () => 'component Latest {}\n';
        return feature.callback({ document: visible, contentChanges: [{ text: visible.getText() }] });
      },
      requests: [VISIBLE],
    });
    // The server must have version 2 when it answers, or the editor would keep
    // an answer for version 1 as version 2's.
    assert.deepEqual(order, [
      `textDocument/didOpen ${HIDDEN}`, `textDocument/didOpen ${VISIBLE}`, `textDocument/didChange ${VISIBLE}`,
      `textDocument/documentSymbol ${VISIBLE}`, 'shutdown', 'exit',
    ]);
    assert.deepEqual(changes, [{ textDocument: { uri: VISIBLE, version: 2 }, contentChanges: [{ text: 'component Latest {}\n' }] }]);
  });
}
