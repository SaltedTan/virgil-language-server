// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

// The language client's public states. Its private ones (initial, starting,
// startFailed, running, stopping, stopped) map to these.
const State = { Stopped: 1, Running: 2, Starting: 3, StartFailed: 4 };

// `documents` are open in the editor, so each client opens them as it starts.
function extensionHost({ settings = {}, env = {}, documents = [] } = {}) {
  const clients = [];
  const commands = new Map();
  const events = [];
  // Failures the language client reports, each with an error notification.
  const errors = [];
  const tabListeners = new Set();
  const editorListeners = new Set();
  const subscribe = (listeners) => (callback) => {
    listeners.add(callback);
    return { dispose: () => listeners.delete(callback) };
  };
  let configurationChanged;
  let nextStart;
  let nextStop;
  const values = { 'virgil.server.path': '/server/virgil-lsp', ...settings };
  const warnings = [];
  const watchers = [];
  class LanguageClient {
    constructor(id, name, serverOptions, clientOptions) {
      this.options = clientOptions;
      this.id = clients.length;
      this.$state = 'initial';
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
        // Document sync opens each open document before the start resolves.
        for (const document of documents) {
          this.sendNotification(`didOpen ${document}`).catch((error) => errors.push(error.message));
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
      await this.$start();
      events.push(`${method}:${this.id}`);
    }
    async $start() {
      if (this.$state === 'startFailed') throw new Error('Previous start failed');
      await this.start();
      if (this.$state !== 'running') throw new Error('Starting server failed');
    }
    isRunning() { return this.$state === 'running'; }
    get visibleDocuments() {
      if (!this.visibility) {
        const scan = () => events.push(`scan:${this.id}`);
        const subscriptions = [
          vscode.window.tabGroups.onDidChangeTabs(scan),
          vscode.window.onDidChangeVisibleTextEditors(scan),
        ];
        this.visibility = { dispose: () => subscriptions.forEach((item) => item.dispose()) };
      }
      return this.visibility;
    }
    async stop() {
      assert.equal(this.$state, 'running');
      events.push(`shutdown:${this.id}`);
      this.$state = 'stopping';
      const gate = nextStop;
      nextStop = undefined;
      if (gate) await gate.promise;
      events.push(`exit:${this.id}`);
      this.$state = 'stopped';
      this.starting = undefined;
      if (this.failStop) throw new Error('shutdown failed');
    }
    connectionClosed() {
      this.$state = 'initial';
      this.starting = undefined;
      return this.start();
    }
  }
  const channel = { info() {}, warn() {}, error() {}, show() {} };
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
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../out/extension.js'), 'utf8'), {
    module, exports: module.exports,
    process: Object.assign(Object.create(process), { env }),
    require(name) {
      if (name === 'vscode') return vscode;
      if (name === 'vscode-languageclient/node') return { LanguageClient, State, TransportKind: { stdio: 0 } };
      if (name === 'node:fs/promises') return {
        realpath: async (file) => file,
        stat: async () => ({ isFile: () => true }),
        access: async () => {},
      };
      return require(name);
    },
  });
  return {
    extension: module.exports, clients, events, errors, values, warnings, watchers,
    activate: () => module.exports.activate({ subscriptions: [] }),
    restart: () => commands.get('virgil.server.restart')(),
    // Changes the given setting, or every setting.
    configure: (setting) => configurationChanged({
      affectsConfiguration: (section) => setting === undefined || section === setting,
    }),
    pauseStart() { nextStart = deferred(); return nextStart; },
    pauseStop() { nextStop = deferred(); return nextStop; },
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
  ['no virgilRoot is sent for a relative VIRGIL_LOC', {}, { VIRGIL_LOC: 'vendor/virgil' }, {}],
]) {
  test(title, async () => {
    const host = extensionHost({ settings, env });
    await host.activate();
    assert.deepEqual(plain(host.clients[0].options.initializationOptions), expected);
    assert.deepEqual(host.warnings, []);
    await host.extension.deactivate();
  });
}

test('a relative virgil.virgilRoot is reported and not sent, even with VIRGIL_LOC', async () => {
  const host = extensionHost({ settings: { 'virgil.virgilRoot': 'vendor/virgil' }, env: { VIRGIL_LOC: VIRGIL_ROOT } });
  await host.activate();
  assert.deepEqual(plain(host.clients[0].options.initializationOptions), {});
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
  assert.deepEqual(plain(host.clients[1].options.initializationOptions), { virgilRoot: VIRGIL_ROOT });
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
