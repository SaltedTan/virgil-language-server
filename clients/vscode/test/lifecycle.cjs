// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const { ErrorCodes, ResponseError } = require('vscode-jsonrpc');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

// The language client's public states. Its private ones (initial, starting,
// startFailed, running, stopping, stopped) map to these.
const State = { Stopped: 1, Running: 2, Starting: 3, StartFailed: 4 };
// What an error handler returns, as the language client numbers them.
const ErrorAction = { Continue: 1, Shutdown: 2 };
const CloseAction = { DoNotRestart: 1, Restart: 2 };

// `documents` are open in the editor, so each client opens them as it starts.
function extensionHost({ settings = {}, env = {}, documents = [], diffEditors = [], extensions = [], target, missing, platform = process.platform, arch = process.arch } = {}) {
  const openDocuments = [...new Set([...documents, ...diffEditors.flatMap(({ original, modified }) => [original, modified])])];
  const clients = [];
  const commands = new Map();
  const events = [];
  // Failures the language client reports, each with an error notification.
  const errors = [];
  const outputWarnings = [];
  const messages = [];
  const pendingTimers = new Set();
  const tabListeners = new Set();
  const editorListeners = new Set();
  const subscribe = (listeners) => (callback) => {
    listeners.add(callback);
    return { dispose: () => listeners.delete(callback) };
  };
  let configurationChanged;
  let nextStart;
  let nextStop;
  let nextNotification;
  const values = { 'virgil.server.path': '/server/virgil-lsp', ...settings };
  const warnings = [];
  const watchers = [];
  class LanguageClient {
    constructor(id, name, serverOptions, clientOptions) {
      this.options = clientOptions;
      this.serverOptions = serverOptions;
      this.id = clients.length;
      this.$state = 'initial';
      this.pendingOpenNotifications = new Map();
      clients.push(this);
    }
    get state() {
      const states = { starting: State.Starting, startFailed: State.StartFailed, running: State.Running };
      return states[this.$state] ?? State.Stopped;
    }
    start() {
      if (this.$state === 'stopping') return Promise.reject(new Error('Client is currently stopping'));
      if (this.starting) return this.starting;
      this.visibleDocuments;
      this.$state = 'starting';
      events.push(`start:${this.id}`);
      const gate = nextStart;
      nextStart = undefined;
      this.starting = (gate?.promise ?? Promise.resolve()).then(() => {
        this.$state = 'running';
        this.connection = { closed: false };
        // Document sync opens each open document before the start resolves.
        for (const document of openDocuments) {
          if (this.visibleDocuments.isVisible(document)) {
            this.sendNotification(`didOpen ${document}`).catch((error) => errors.push(error.message));
          } else {
            this.pendingOpenNotifications.set(document, document);
          }
        }
      }, (error) => {
        this.$state = 'startFailed';
        throw error;
      });
      return this.starting;
    }
    // Like each request and notification, this calls start first.
    async sendNotification(method) {
      if (['startFailed', 'stopping', 'stopped'].includes(this.$state)) throw new Error('Client is not running');
      const gate = nextNotification;
      nextNotification = undefined;
      const connection = await this.$start();
      await this.sendPendingOpenNotifications();
      if (gate) await gate.promise;
      if (connection.closed) throw new Error(`Sending ${method} failed`);
      events.push(`${method}:${this.id}`);
    }
    async sendPendingOpenNotifications() {
      const pending = [...this.pendingOpenNotifications.values()];
      this.pendingOpenNotifications.clear();
      for (const document of pending) await this.sendNotification(`didOpen ${document}`);
    }
    async $start() {
      if (this.$state === 'startFailed') throw new Error('Previous start failed');
      await this.start();
      if (this.$state !== 'running') throw new Error('Starting server failed');
      return this.connection;
    }
    isRunning() { return this.$state === 'running'; }
    get visibleDocuments() {
      if (!this.visibility) {
        const scan = () => events.push(`scan:${this.id}`);
        const subscriptions = [
          vscode.window.tabGroups.onDidChangeTabs(scan),
          vscode.window.onDidChangeVisibleTextEditors(scan),
        ];
        this.visibility = {
          isVisible: (document) => documents.includes(document) || diffEditors.some(({ modified }) => modified === document),
          dispose: () => subscriptions.forEach((item) => item.dispose()),
        };
      }
      return this.visibility;
    }
    async stop() {
      assert.equal(this.$state, 'running');
      events.push(`shutdown:${this.id}`);
      this.$state = 'stopping';
      this.pendingOpenNotifications.clear();
      const gate = nextStop;
      nextStop = undefined;
      if (gate) await gate.promise;
      events.push(`exit:${this.id}`);
      this.connection.closed = true;
      this.$state = 'stopped';
      this.starting = undefined;
      if (this.failStop) throw new Error('shutdown failed');
    }
    // Each call starts a count of crashes; the fifth close stops the client.
    // As the library's does, error() continues for a count from 1 to 3; it
    // records each call and its result in `errors`.
    createDefaultErrorHandler() {
      let crashes = 0;
      const errors = [];
      this.defaultErrorHandler = {
        errors,
        error(error, message, count) {
          const result = { action: count >= 1 && count <= 3 ? ErrorAction.Continue : ErrorAction.Shutdown };
          errors.push({ args: [error, message, count], result });
          return result;
        },
        closed: () => ({ action: ++crashes <= 4 ? CloseAction.Restart : CloseAction.DoNotRestart }),
      };
      return this.defaultErrorHandler;
    }
    connectionClosed() {
      this.$state = 'initial';
      this.connection.closed = true;
      this.pendingOpenNotifications.clear();
      this.starting = undefined;
      return this.start();
    }
  }
  const channel = { info(message) { messages.push(message); }, warn(message) { outputWarnings.push(message); }, error(message) { messages.push(message); }, show() {} };
  const vscode = {
    window: {
      createOutputChannel: () => channel,
      showErrorMessage: () => Promise.resolve(undefined),
      showWarningMessage(message) { warnings.push(message); return Promise.resolve(undefined); },
      tabGroups: { onDidChangeTabs: subscribe(tabListeners) },
      onDidChangeVisibleTextEditors: subscribe(editorListeners),
    },
    commands: {
      registerCommand(name, action) { commands.set(name, action); return {}; },
    },
    workspace: {
      getConfiguration: () => ({ get: (key, fallback) => values[key] ?? fallback }),
      onDidChangeConfiguration(callback) { configurationChanged = callback; return {}; },
      createFileSystemWatcher(pattern) {
        const watcher = { pattern, disposed: false, dispose() { this.disposed = true; } };
        watchers.push(watcher);
        return watcher;
      },
    },
    // The other installed extensions, by ID.
    extensions: { getExtension: (id) => (extensions.includes(id) ? { id } : undefined) },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../out/extension.js'), 'utf8'), {
    module, exports: module.exports,
    process: Object.create(process, Object.getOwnPropertyDescriptors({ env, platform, arch })),
    require(name) {
      if (name === 'node:timers') return {
        setTimeout(callback, delay) {
          const timer = setTimeout(() => {
            pendingTimers.delete(timer);
            callback();
          }, delay);
          pendingTimers.add(timer);
          return timer;
        },
        clearTimeout(timer) {
          pendingTimers.delete(timer);
          clearTimeout(timer);
        },
      };
      if (name === 'vscode') return vscode;
      if (name === 'vscode-languageclient/node') return { ErrorAction, ErrorCodes, ResponseError, LanguageClient, State, TransportKind: { stdio: 0 } };
      if (name === 'node:fs/promises') return {
        readFile: async () => {
          if (target === undefined) throw Object.assign(new Error('missing'), { code: 'ENOENT' });
          return JSON.stringify({ target });
        },
        realpath: async (file) => file,
        stat: async (file) => ({ isFile: () => file !== missing }),
        access: async () => {},
      };
      return require(name);
    },
  });
  return {
    extension: module.exports, clients, events, errors, values, warnings, watchers, outputWarnings, pendingTimers, messages,
    activate: () => module.exports.activate({ subscriptions: [], extensionPath: '/extension' }),
    restart: () => commands.get('virgil.server.restart')(),
    // Changes the given setting, or every setting.
    configure: (setting) => configurationChanged({
      affectsConfiguration: (section) => setting === undefined || section === setting,
    }),
    pauseStart() { nextStart = deferred(); return nextStart; },
    pauseStop() { nextStop = deferred(); return nextStop; },
    pauseNotification() { nextNotification = deferred(); return nextNotification; },
    changeVisibility() {
      for (const callback of tabListeners) callback();
      for (const callback of editorListeners) callback();
    },
    subscriptionCounts: () => [tabListeners.size, editorListeners.size],
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
// Copies a value from the extension's context into this one for deepEqual.
const plain = (value) => JSON.parse(JSON.stringify(value));

for (const action of ['restart', 'configure', 'deactivate']) {
  test(`${action} waits for automatic recovery and shuts down the tracked client`, async () => {
    const host = extensionHost();
    await host.activate();
    const gate = host.pauseStart();
    const recovery = host.clients[0].connectionClosed();
    await settle();
    assert.equal(host.clients[0].$state, 'starting');
    assert.deepEqual(host.subscriptionCounts(), [1, 1]);
    const requested = action === 'deactivate' ? host.extension.deactivate() : host[action]();
    await settle();
    assert.equal(host.clients.length, 1);
    assert.deepEqual(host.events, ['start:0', 'start:0']);
    gate.resolve();
    await recovery;
    await requested;
    await settle();
    assert.deepEqual(host.events.slice(2, 4), ['shutdown:0', 'exit:0']);
    assert.equal(host.clients.filter((client) => client.isRunning()).length, action === 'deactivate' ? 0 : 1);
    await host.extension.deactivate();
    assert.equal(host.clients.filter((client) => client.isRunning()).length, 0);
    assert.deepEqual(host.subscriptionCounts(), [0, 0]);
  });
}

test('a replaced client cannot start from a queued automatic recovery', async () => {
  const host = extensionHost();
  await host.activate();
  const restart = host.restart();
  const recovery = host.clients[0].connectionClosed();
  await restart;
  await recovery;
  assert.deepEqual(host.events, ['start:0', 'start:1']);
  assert.equal(host.clients[0].$state, 'initial');
  assert.equal(host.clients[1].$state, 'running');
  await host.extension.deactivate();
});

for (const action of ['restart', 'configure', 'deactivate']) {
  test(`the recovering client's messages go out before ${action} stops it, without errors`, async () => {
    const host = extensionHost({ documents: ['a.v3', 'b.v3'] });
    await host.activate();
    const gate = host.pauseStart();
    const recovery = host.clients[0].connectionClosed();
    await settle();
    const requested = action === 'deactivate' ? host.extension.deactivate() : host[action]();
    await settle();
    // Sent while the recovery starts, it waits for that start only.
    const message = host.clients[0].sendNotification('didChange');
    gate.resolve();
    await recovery;
    await message;
    await requested;
    await settle();
    const recovered = host.events.slice(host.events.lastIndexOf('start:0') + 1, host.events.indexOf('shutdown:0'));
    assert.deepEqual(recovered, ['didChange:0', 'didOpen a.v3:0', 'didOpen b.v3:0']);
    assert.deepEqual(host.errors, []);
    assert.equal(host.clients.filter((client) => client.isRunning()).length, action === 'deactivate' ? 0 : 1);
    await host.extension.deactivate();
  });
}

for (const action of ['restart', 'virgil.server.path', 'virgil.virgilRoot', 'virgil.inlayHints.types', 'virgil.inlayHints.parameterNames', 'virgil.semanticTokens', 'deactivate']) {
  test(`diff-editor pending opens survive recovery followed by ${action}`, async () => {
    const host = extensionHost({
      diffEditors: [
        { original: 'original.v3', modified: 'modified.v3' },
        { original: 'other-original.v3', modified: 'other-modified.v3' },
      ],
    });
    await host.activate();
    await settle();
    assert.deepEqual(host.errors, []);
    const gate = host.pauseStart();
    const recovery = host.clients[0].connectionClosed();
    await settle();
    const requested = action === 'deactivate' ? host.extension.deactivate()
      : action === 'restart' ? host.restart() : host.configure(action);
    await settle();
    assert.deepEqual(host.events.filter((event) => event === 'shutdown:0'), []);
    gate.resolve();
    await recovery;
    await requested;
    await settle();
    assert.deepEqual(host.errors, []);
    const recovered = host.events.slice(host.events.lastIndexOf('start:0') + 1, host.events.indexOf('shutdown:0'));
    assert.deepEqual(recovered.sort(), [
      'didOpen modified.v3:0', 'didOpen original.v3:0',
      'didOpen other-modified.v3:0', 'didOpen other-original.v3:0',
    ].sort());
    assert.equal(host.clients.filter((client) => client.isRunning()).length, action === 'deactivate' ? 0 : 1);
    await host.extension.deactivate();
  });
}

test('restart drains a failed notification without hiding its failure or blocking shutdown', async () => {
  const host = extensionHost();
  await host.activate();
  const gate = host.pauseNotification();
  const sent = host.clients[0].sendNotification('didChange');
  const rejected = assert.rejects(sent, /write failed/);
  await settle();
  const restart = host.restart();
  await settle();
  assert.deepEqual(host.events, ['start:0']);
  gate.reject(new Error('write failed'));
  await rejected;
  await restart;
  assert.deepEqual(host.events, ['start:0', 'shutdown:0', 'exit:0', 'start:1']);
  await host.extension.deactivate();
});

for (const action of ['restart', 'virgil.server.path', 'virgil.virgilRoot', 'virgil.inlayHints.types', 'virgil.inlayHints.parameterNames', 'virgil.semanticTokens', 'deactivate']) {
  test(`${action} proceeds after a recovery notification write never settles`, { timeout: 1000 }, async () => {
    const host = extensionHost({ documents: ['a.v3'] });
    await host.activate();
    await settle();
    const recovering = host.clients[0];
    recovering.notificationDrainTimeout = 1;
    const startup = host.pauseStart();
    const recovery = recovering.connectionClosed();
    await settle();
    host.pauseNotification();
    const requested = action === 'deactivate' ? host.extension.deactivate()
      : action === 'restart' ? host.restart() : host.configure(action);
    const following = action === 'deactivate' ? requested : host.restart();
    startup.resolve();
    await recovery;
    await following;
    await settle();
    assert.equal(recovering.$state, 'stopped');
    assert.deepEqual(host.events.slice(host.events.lastIndexOf('start:0') + 1), action === 'deactivate'
      ? ['shutdown:0', 'exit:0']
      : ['shutdown:0', 'exit:0', 'start:1', 'didOpen a.v3:1', 'shutdown:1', 'exit:1', 'start:2', 'didOpen a.v3:2']);
    assert.deepEqual(host.outputWarnings, ['Notification drain timed out (1 still pending); stopping the server']);
    assert.deepEqual(host.errors, []);
    assert.equal(host.pendingTimers.size, 0);
    await host.extension.deactivate();
  });
}

test('a notification settling before the drain deadline is sent before stop and clears the timer', async () => {
  const host = extensionHost();
  await host.activate();
  assert.equal(host.clients[0].notificationDrainTimeout, 2000);
  const write = host.pauseNotification();
  const sent = host.clients[0].sendNotification('didChange');
  await settle();
  const restart = host.restart();
  await settle();
  assert.deepEqual(host.events, ['start:0']);
  assert.equal(host.pendingTimers.size, 1);
  write.resolve();
  await sent;
  await restart;
  assert.deepEqual(host.events, ['start:0', 'didChange:0', 'shutdown:0', 'exit:0', 'start:1']);
  assert.deepEqual(host.outputWarnings, []);
  assert.equal(host.pendingTimers.size, 0);
  await host.extension.deactivate();
});

test('a message while a restart stops the client is refused at once, and nothing starts it again', async () => {
  const host = extensionHost();
  await host.activate();
  const gate = host.pauseStop();
  const restart = host.restart();
  await settle();
  assert.equal(host.clients[0].$state, 'stopping');
  await assert.rejects(host.clients[0].sendNotification('didChange'), /Client is not running/);
  const late = host.clients[0].start();
  await settle();
  assert.deepEqual(host.events, ['start:0', 'shutdown:0']);
  gate.resolve();
  await restart;
  await late;
  assert.deepEqual(host.events, ['start:0', 'shutdown:0', 'exit:0', 'start:1']);
  assert.equal(host.clients[0].$state, 'stopped');
  await host.extension.deactivate();
});

test('a restart queued behind a failed recovery starts one replacement', async () => {
  const host = extensionHost();
  await host.activate();
  const gate = host.pauseStart();
  const recovery = host.clients[0].connectionClosed();
  await settle();
  const restart = host.restart();
  gate.reject(new Error('initialization failed'));
  await assert.rejects(recovery, /initialization failed/);
  await restart;
  assert.equal(host.clients[0].$state, 'startFailed');
  await assert.rejects(host.clients[0].sendNotification('didChange'), /Client is not running/);
  assert.deepEqual(host.events, ['start:0', 'start:0', 'start:1']);
  assert.equal(host.clients[1].$state, 'running');
  await host.extension.deactivate();
});

// Node's errors for a pipe that failed, with their codes.
function pipeError(code, text) {
  return Object.assign(new Error(text), { code });
}

test('errors that close the connection let the close decide, and the default handler decides others', async () => {
  const host = extensionHost();
  await host.activate();
  const [client] = host.clients;
  const { errorHandler } = client.options;
  // The stream's own error for a failed write has no message or count
  // (#216), as a read error has; the failed write reports one with both, and
  // so does each write after it.
  for (const [error, message, count] of [
    [pipeError('EPIPE', 'write EPIPE'), undefined, undefined],
    [pipeError('EPIPE', 'write EPIPE'), { method: 'textDocument/didOpen' }, 1],
    [pipeError('ECONNRESET', 'write ECONNRESET'), { method: 'textDocument/didOpen' }, 1],
    [pipeError('ECONNRESET', 'read ECONNRESET'), undefined, undefined],
    [pipeError('ERR_STREAM_DESTROYED', 'Cannot call write after a stream was destroyed'), { method: 'exit' }, 4],
  ]) {
    assert.deepEqual(plain(await errorHandler.error(error, message, count)),
      { action: ErrorAction.Continue, handled: true }, error.message);
  }
  assert.deepEqual(client.defaultErrorHandler.errors, []);
  // Output that isn't a message, a message that isn't JSON or can't be
  // written, and a write after the client ended the server's input don't
  // close the connection at once: the default handler gets each error as it
  // came, and its result is the client's.
  const others = [
    [new Error('Header must provide a Content-Length property.\n{"banner\\ncontent-length":"38"}'), undefined, undefined],
    [new SyntaxError('Unexpected token \'b\', "banner" is not valid JSON'), undefined, undefined],
    [new TypeError('Do not know how to serialize a BigInt'), undefined, undefined],
    [pipeError('ERR_STREAM_WRITE_AFTER_END', 'write after end'), { method: 'exit' }, 1],
  ];
  for (const [error, message, count] of others) {
    const result = await errorHandler.error(error, message, count);
    const { args, result: decided } = client.defaultErrorHandler.errors.at(-1);
    assert.deepEqual(args, [error, message, count]);
    assert.equal(result, decided, error.message);
  }
  assert.deepEqual(client.defaultErrorHandler.errors.map(({ result }) => result.action),
    [ErrorAction.Shutdown, ErrorAction.Shutdown, ErrorAction.Shutdown, ErrorAction.Continue]);
  assert.deepEqual(host.events, ['start:0']);
  await host.extension.deactivate();
});

test("a client's closes follow the library's restart policy, counted for each client", async () => {
  const host = extensionHost();
  await host.activate();
  const { errorHandler } = host.clients[0].options;
  // One default handler counts every close of the client.
  const closes = [];
  for (let crash = 0; crash < 5; crash++) closes.push((await errorHandler.closed()).action);
  assert.deepEqual(closes, [...Array(4).fill(CloseAction.Restart), CloseAction.DoNotRestart]);
  // A restart's new client counts from zero.
  await host.restart();
  assert.equal((await host.clients[1].options.errorHandler.closed()).action, CloseAction.Restart);
  await host.extension.deactivate();
});

for (const action of ['restart', 'configure']) {
  test(`${action} releases retired visibility subscriptions`, async () => {
    const host = extensionHost();
    await host.activate();
    for (let count = 0; count < 3; count++) {
      await host[action]();
      await settle();
      assert.deepEqual(host.subscriptionCounts(), [1, 1]);
      const before = host.events.length;
      host.changeVisibility();
      assert.deepEqual(host.events.slice(before), [`scan:${count + 1}`, `scan:${count + 1}`]);
    }
    await host.extension.deactivate();
    assert.deepEqual(host.subscriptionCounts(), [0, 0]);
    const before = host.events.length;
    host.changeVisibility();
    assert.equal(host.events.length, before);
  });
}

test('retiring a stopped client releases visibility subscriptions', async () => {
  const host = extensionHost();
  await host.activate();
  await host.clients[0].stop();
  assert.deepEqual(host.subscriptionCounts(), [1, 1]);
  await host.restart();
  assert.deepEqual(host.subscriptionCounts(), [1, 1]);
  await host.extension.deactivate();
  assert.deepEqual(host.subscriptionCounts(), [0, 0]);
});

test('retiring a client after failed startup releases visibility subscriptions', async () => {
  const host = extensionHost();
  const gate = host.pauseStart();
  const activation = host.activate();
  await settle();
  gate.reject(new Error('initialization failed'));
  await activation;
  assert.deepEqual(host.subscriptionCounts(), [1, 1]);
  await host.extension.deactivate();
  assert.deepEqual(host.subscriptionCounts(), [0, 0]);
});

test('retirement releases visibility subscriptions when shutdown fails', async () => {
  const host = extensionHost();
  await host.activate();
  host.clients[0].failStop = true;
  await host.restart();
  assert.deepEqual(host.subscriptionCounts(), [1, 1]);
  await host.extension.deactivate();
  assert.deepEqual(host.subscriptionCounts(), [0, 0]);
});

test('deactivation waits for initial startup and sends shutdown then exit', async () => {
  const host = extensionHost();
  const gate = host.pauseStart();
  const activation = host.activate();
  await settle();
  const deactivation = host.extension.deactivate();
  await settle();
  assert.deepEqual(host.events, ['start:0']);
  gate.resolve();
  await activation;
  await deactivation;
  assert.deepEqual(host.events, ['start:0', 'shutdown:0', 'exit:0']);
});

const VIRGIL_ROOT = path.resolve(__dirname, '../../../vendor/virgil');

for (const [title, settings, env, expected] of [
  ['virgil.virgilRoot is sent as initializationOptions.virgilRoot, ahead of VIRGIL_LOC',
    { 'virgil.virgilRoot': ` ${VIRGIL_ROOT} ` }, { VIRGIL_LOC: '/opt/other' }, { virgilRoot: VIRGIL_ROOT }],
  ['VIRGIL_LOC is sent when virgil.virgilRoot is empty', {}, { VIRGIL_LOC: VIRGIL_ROOT }, { virgilRoot: VIRGIL_ROOT }],
  ['no virgilRoot is sent without a root', {}, {}, {}],
  ['a relative VIRGIL_LOC blocks server bundle fallback', {}, { VIRGIL_LOC: 'vendor/virgil' }, { virgilRoot: 'vendor/virgil' }],
]) {
  test(title, async () => {
    const host = extensionHost({ settings, env });
    await host.activate();
    assert.deepEqual(plain(host.clients[0].options.initializationOptions), { ...expected, inlayHints: { types: true, parameterNames: true }, semanticTokens: true });
    assert.deepEqual(host.warnings, []);
    await host.extension.deactivate();
  });
}

test('a relative virgil.virgilRoot is reported and blocks fallback, even with VIRGIL_LOC', async () => {
  const host = extensionHost({ settings: { 'virgil.virgilRoot': 'vendor/virgil' }, env: { VIRGIL_LOC: VIRGIL_ROOT } });
  await host.activate();
  assert.deepEqual(plain(host.clients[0].options.initializationOptions), { virgilRoot: 'vendor/virgil', inlayHints: { types: true, parameterNames: true }, semanticTokens: true });
  assert.equal(host.warnings.length, 1);
  assert.match(host.warnings[0], /virgil\.virgilRoot must be an absolute path, not vendor\/virgil/);
  await host.extension.deactivate();
});

test('changing virgil.virgilRoot restarts the server with the new root', async () => {
  const host = extensionHost();
  await host.activate();
  host.values['virgil.virgilRoot'] = VIRGIL_ROOT;
  await host.configure('virgil.trace.server');
  assert.equal(host.clients.length, 1);
  await host.configure('virgil.virgilRoot');
  await settle();
  assert.deepEqual(host.events, ['start:0', 'shutdown:0', 'exit:0', 'start:1']);
  assert.deepEqual(plain(host.clients[1].options.initializationOptions), { virgilRoot: VIRGIL_ROOT, inlayHints: { types: true, parameterNames: true }, semanticTokens: true });
  await host.extension.deactivate();
});

test('project files are sent to the server with Virgil documents', async () => {
  const host = extensionHost();
  await host.activate();
  assert.deepEqual(plain(host.clients[0].options.documentSelector), [
    { scheme: 'file', language: 'virgil' },
    { scheme: 'untitled', language: 'virgil' },
    { scheme: 'file', pattern: '**/.virgil-lsp.json' },
  ]);
  await host.extension.deactivate();
});

test('project files and sources are watched for each client, and released with it', async () => {
  const host = extensionHost();
  await host.activate();
  const patterns = ['**/.virgil-lsp.json', '**/*.v3'];
  assert.deepEqual(host.watchers.map((w) => w.pattern), patterns);
  assert.equal(host.clients[0].options.synchronize.fileEvents.length, 2);
  host.clients[0].options.synchronize.fileEvents.forEach((w, i) => assert.equal(w, host.watchers[i]));
  await host.restart();
  assert.deepEqual(host.watchers.map((w) => [w.pattern, w.disposed]),
    [[patterns[0], true], [patterns[1], true], [patterns[0], false], [patterns[1], false]]);
  await host.extension.deactivate();
  assert.ok(host.watchers.every((w) => w.disposed));
});

for (const [name, settings, target, command, reason] of [
  ['explicit override', {}, 'darwin-arm64', '/server/virgil-lsp', 'explicit virgil.server.path'],
  ['bundle before PATH', { 'virgil.server.path': '' }, 'linux-x64', '/extension/server/virgil-lsp', 'bundled for linux-x64'],
  ['universal fallback', { 'virgil.server.path': '' }, undefined, '/bin/virgil-lsp', 'no bundle and no explicit'],
  ['explicit PATH command', { 'virgil.server.path': 'virgil-lsp' }, 'linux-x64', '/bin/virgil-lsp', 'explicit virgil.server.path'],
]) {
  test(`server selection: ${name}`, async () => {
    const host = extensionHost({ settings, target, platform: 'linux', arch: 'x64', env: { PATH: '/bin' } });
    await host.activate();
    assert.equal(host.clients[0].serverOptions.command, command);
    assert.ok(host.messages.some((m) => m.includes(reason)), host.messages.join('\n'));
    await host.extension.deactivate();
  });
}

test('a mismatched workspace host refuses the bundle instead of falling back to PATH', async () => {
  const host = extensionHost({ settings: { 'virgil.server.path': '' }, target: 'darwin-arm64',
    platform: 'linux', arch: 'x64', env: { PATH: '/bin' } });
  await host.activate();
  assert.equal(host.clients.length, 0);
  assert.ok(host.messages.some((m) => m.includes('workspace extension host is linux-x64')));
});

test('Apple Silicon bundle explains its Rosetta requirement', async () => {
  const host = extensionHost({ settings: { 'virgil.server.path': '' }, target: 'darwin-arm64',
    platform: 'darwin', arch: 'arm64' });
  await host.activate();
  assert.equal(host.clients[0].serverOptions.command, '/extension/server/virgil-lsp');
  assert.ok(host.messages.some((m) => m.includes('requires Rosetta 2')));
  await host.extension.deactivate();
});

for (const name of ['virgil-lsp', 'virgil-lsp-worker']) {
  test(`a broken bundle (${name}) does not fall back to PATH`, async () => {
    const host = extensionHost({ settings: { 'virgil.server.path': '' }, target: 'linux-x64',
      platform: 'linux', arch: 'x64', env: { PATH: '/bin' }, missing: `/extension/server/${name}` });
    await host.activate();
    assert.equal(host.clients.length, 0);
    assert.ok(host.messages.some((m) => m.includes('The server did not start:') && m.includes(name)));
  });
}
for (const key of ['types', 'parameterNames']) {
  test(`changing virgil.inlayHints.${key} restarts with that hint kind disabled`, async () => {
    const host = extensionHost();
    await host.activate();
    host.values[`virgil.inlayHints.${key}`] = false;
    await host.configure(`virgil.inlayHints.${key}`);
    await settle();
    assert.equal(host.clients.length, 2);
    assert.deepEqual(plain(host.clients[1].options.initializationOptions.inlayHints), {
      types: key !== 'types', parameterNames: key !== 'parameterNames',
    });
    await host.extension.deactivate();
  });
}

for (const [setting, extensions, expected] of [
  ['auto', [], true],
  ['auto', ['titzer.virgil'], false],
  ['on', ['titzer.virgil'], true],
  ['off', [], false],
]) {
  test(`virgil.semanticTokens ${setting}${extensions.length ? ' beside titzer.virgil' : ''} sends semanticTokens: ${expected}`, async () => {
    const settings = setting === 'auto' ? {} : { 'virgil.semanticTokens': setting };
    const host = extensionHost({ settings, extensions });
    await host.activate();
    assert.equal(host.clients[0].options.initializationOptions.semanticTokens, expected);
    assert.equal(host.messages.includes('Semantic tokens are off: titzer.virgil is enabled, and virgil.semanticTokens is auto'),
      setting === 'auto' && !expected);
    await host.extension.deactivate();
  });
}

test('changing virgil.semanticTokens restarts with semantic tokens off', async () => {
  const host = extensionHost();
  await host.activate();
  host.values['virgil.semanticTokens'] = 'off';
  await host.configure('virgil.semanticTokens');
  await settle();
  assert.deepEqual(host.events, ['start:0', 'shutdown:0', 'exit:0', 'start:1']);
  assert.equal(host.clients[1].options.initializationOptions.semanticTokens, false);
  await host.extension.deactivate();
});
