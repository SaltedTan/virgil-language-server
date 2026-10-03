// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function extensionHost() {
  const clients = [];
  const commands = new Map();
  const events = [];
  let configurationChanged;
  let nextStart;
  class LanguageClient {
    constructor() {
      this.id = clients.length;
      this.state = 'initial';
      clients.push(this);
    }
    start() {
      if (this.starting) return this.starting;
      this.state = 'starting';
      events.push(`start:${this.id}`);
      const gate = nextStart;
      nextStart = undefined;
      this.starting = (gate?.promise ?? Promise.resolve()).then(() => {
        this.state = 'running';
      });
      return this.starting;
    }
    isRunning() { return this.state === 'running'; }
    async stop() {
      assert.equal(this.state, 'running');
      events.push(`shutdown:${this.id}`, `exit:${this.id}`);
      this.state = 'stopped';
      this.starting = undefined;
    }
    connectionClosed() {
      this.state = 'initial';
      this.starting = undefined;
      return this.start();
    }
  }
  const channel = { info() {}, error() {}, show() {} };
  const vscode = {
    window: {
      createOutputChannel: () => channel,
      showErrorMessage: () => Promise.resolve(undefined),
    },
    commands: {
      registerCommand(name, action) { commands.set(name, action); return {}; },
    },
    workspace: {
      getConfiguration: () => ({ get: () => '/server/virgil-lsp' }),
      onDidChangeConfiguration(callback) { configurationChanged = callback; return {}; },
    },
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../out/extension.js'), 'utf8'), {
    module, exports: module.exports, process,
    require(name) {
      if (name === 'vscode') return vscode;
      if (name === 'vscode-languageclient/node') return { LanguageClient, TransportKind: { stdio: 0 } };
      if (name === 'node:fs/promises') return {
        realpath: async (file) => file,
        stat: async () => ({ isFile: () => true }),
        access: async () => {},
      };
      return require(name);
    },
  });
  return {
    extension: module.exports, clients, events,
    activate: () => module.exports.activate({ subscriptions: [] }),
    restart: () => commands.get('virgil.server.restart')(),
    configure: () => configurationChanged({ affectsConfiguration: () => true }),
    pauseStart() { nextStart = deferred(); return nextStart; },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

for (const action of ['restart', 'configure', 'deactivate']) {
  test(`${action} waits for automatic recovery and shuts down the tracked client`, async () => {
    const host = extensionHost();
    await host.activate();
    const gate = host.pauseStart();
    const recovery = host.clients[0].connectionClosed();
    await settle();
    assert.equal(host.clients[0].state, 'starting');
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
  assert.equal(host.clients[0].state, 'initial');
  assert.equal(host.clients[1].state, 'running');
  await host.extension.deactivate();
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
