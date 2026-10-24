// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The real launch and retry lifecycle, over fake client outcomes and SSH
// responses. No VS Code, SSH server, or other process is started.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { fakeClock, load } = require('./support/fakes.cjs');

async function launches(t, scenario) {
  const root = fs.mkdtempSync(path.join(__dirname, '.remote-retry-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const messages = [];
  t.mock.method(console, 'error', (message) => messages.push(message));
  const clock = fakeClock();
  const events = [];
  const notes = [];
  let tries = 0;
  let current;
  let servers = [];
  let lateReport;
  const run = {
    work: path.join(root, 'work'), evidence: path.join(root, 'evidence'), values: { out: path.join(root, 'out') },
    target: { root: '/remote', alias: 'host' }, settings: {}, errors: [], tokens: new Set(),
    manifest: { launches: [] }, controller: new AbortController(), failures: [],
    checkpoint() { if (this.signal.aborted) throw Object.assign(new Error('interrupted'), { kind: 'interrupted' }); },
    fail(message) { this.failures.push(message); }, note(message) { notes.push(message); }, record(error) { this.errors.push(error); },
    async remote(command) {
      if (command.startsWith("'/remote/agent/node' -e ") || command.startsWith(`'${process.execPath}' -e `)) {
        const options = JSON.parse(/'([^']*)'$/.exec(command)[1]);
        if (options.signal) {
          events.push(`stop:${options.host.pid}:${options.signal}`);
          lateReport = scenario.lateReport;
          if (!scenario.survivor) servers = servers.filter((p) => p.pid !== options.host.pid);
        }
        events.push(`processes:${servers.length}`);
        return { stdout: JSON.stringify({ found: servers, unidentified: scenario.unidentified ? [99] : [],
          host: servers.some((p) => p.pid === options.host?.pid), sampledAt: clock.globals.Date.now() }) };
      }
      if (command.includes('else exit 44')) {
        assert.ok(command.includes(`'/results/${current}.json'`));
        events.push(`report:${current}`);
        if (lateReport !== undefined) return lateReport;
        if (scenario.reportError) return { code: 1, stdout: '' };
        if ((tries === 1 || scenario.secondFailure) && !scenario.started) return { code: 44, stdout: '' };
        return { code: 0, stdout: scenario.unreadable ? '{' : JSON.stringify({ outcome: 'pass', folders: scenario.name?.endsWith('workspace') ? ['file:///project'] : null, seconds: 1 }) };
      }
      return { code: 0, stdout: '', data: Buffer.alloc(0) };
    },
  };
  run.signal = run.controller.signal;
  const runner = load('remote-ssh/run.cjs', {
    'node:fs': { ...fs, cpSync: (...args) => {
      if (scenario.logFailure) throw new Error('ENOSPC');
      return fs.cpSync(...args);
    } },
    '../processes.cjs': {
      startGroup(command, args, { onExit }) {
        tries++;
        if (tries > 1) assert.deepEqual(servers, [], 'the retry must start after all remote processes exit');
        const userData = args.find((arg) => arg.startsWith('--user-data-dir=')).slice('--user-data-dir='.length);
        current = path.basename(path.dirname(userData));
        events.push(`launch:${current}`);
        const logs = path.join(userData, 'logs', 'time');
        fs.mkdirSync(logs, { recursive: true });
        fs.writeFileSync(path.join(logs, 'main.log'), scenario.marker ?? 'CodeWindow: detected unresponsive');
        const stdout = new EventEmitter();
        const stderr = new EventEmitter();
        let ended = false;
        let groupRuns = scenario.groupLeft;
        const end = () => {
          if (ended) return;
          ended = true;
          const code = tries === 1 || scenario.secondFailure ? (scenario.code ?? 1) : 0;
          if (scenario.servers && tries === 1) servers = [
            { pid: 10, start: '100', exe: '/remote/agent/node' },
            { pid: 11, start: '101', exe: '/remote/agent/node' },
          ];
          if (scenario.languageLeft) servers.push({ pid: 20, start: '102', exe: '/remote/virgil-lsp' });
          stdout.emit('data', Buffer.from('PASS: suite\n'));
          onExit({ code, error: scenario.spawnError ? new Error('ENOENT') : undefined });
          stdout.emit('close'); stderr.emit('close');
        };
        queueMicrotask(() => {
          if (scenario.timeout) clock.advance(300001);
          else if (scenario.interrupt) run.controller.abort();
          else end();
        });
        return {
          pid: 50, stdout, stderr, look: () => ({ empty: !groupRuns }),
          signal() { groupRuns = false; queueMicrotask(end); },
          async release() { events.push(`released:${current}`); return !scenario.releaseFailed; },
        };
      },
    },
  }, {}, clock.globals);
  const name = scenario.name ?? 'probe';
  const mode = name === 'probe' ? 'probe' : name.endsWith('workspace') ? 'workspace' : 'single-file';
  const layout = { results: '/results', data: '/data', project: '/project', fixture: '/fixture', tests: '/tests',
    machineSettings: '/settings/settings.json', server: '/remote', agent: '/remote/agent', serverNode: '/remote/agent/node' };
  let done = false;
  let error;
  const pending = runner.launch(run, name, mode, { executable: '/code', layout, vsix: { version: '1' }, repetition: scenario.repetition })
    .catch((e) => { error = e; }).finally(() => { done = true; });
  for (let ticks = 0; !done && ticks < 2000; ticks++) {
    await new Promise(setImmediate);
    clock.advance(100);
  }
  assert.equal(done, true, 'the fake launch must finish within its bounds');
  await pending;
  assert.equal(clock.timers.size, 0);
  return { run, root, messages, events, notes, error };
}

for (const name of ['probe', '1-single-file', '2-single-file', '2-workspace']) {
  for (const marker of ['CodeWindow: detected unresponsive', 'CodeWindow: renderer process gone (reason: crashed)']) {
    test(`${name} retries once for ${marker}, retaining both records and logs`, async (t) => {
      const { run, root, messages, events, error } = await launches(t, { name, marker, servers: true, repetition: name.startsWith('2-') ? 2 : undefined });
      assert.equal(error, undefined);
      assert.deepEqual(run.manifest.launches.map((l) => [l.name, l.result]), [[name, 'INFRA'], [`${name}-retry`, 'PASS']]);
      assert.equal(run.manifest.launches[0].retriedAs, `${name}-retry`);
      if (name.startsWith('2-')) assert.ok(run.manifest.launches.every((l) => l.repetition === 2));
      assert.deepEqual(run.failures, []);
      for (const event of [`released:${name}`, 'stop:10:SIGTERM', 'stop:11:SIGTERM']) {
        assert.ok(events.includes(event), event);
        assert.ok(events.indexOf(event) < events.indexOf(`launch:${name}-retry`));
      }
      assert.equal(fs.readFileSync(path.join(root, 'evidence', 'client', name, 'time', 'main.log'), 'utf8'), marker);
      for (const attempt of [name, `${name}-retry`]) {
        assert.equal(fs.existsSync(path.join(root, 'evidence', 'launches', `${attempt}.log`)), true);
      }
      assert.equal(messages.filter((m) => m.startsWith('RETRY:')).length, 1);
      assert.ok(messages.some((m) => m.includes('before the tests started in the remote extension host')));
    });
  }
}

for (const name of ['probe', '1-single-file', '2-single-file', '2-workspace']) {
  for (const [label, lateReport] of [
    ['complete', { code: 0, stdout: JSON.stringify({ outcome: 'pass' }) }],
    ['incomplete', { code: 0, stdout: '{' }],
    ['unreadable', { code: 1, stdout: '' }],
  ]) {
    test(`${name} does not retry when a ${label} report appears during teardown`, async (t) => {
      const { run, messages, events, notes, error } = await launches(t, { name, servers: true, lateReport });
      assert.equal(error.kind, 'infra');
      assert.match(error.message, new RegExp(`^${name}: VS Code exited with 1 before the tests started`));
      assert.equal(error.retryOutcome.activated, false);
      assert.equal(run.manifest.launches.length, 1);
      assert.equal(run.manifest.launches[0].result, 'INFRA');
      assert.equal(Object.hasOwn(run.manifest.launches[0], 'retriedAs'), false);
      assert.equal(events.filter((event) => event.startsWith('launch:')).length, 1);
      assert.equal(events.filter((event) => event === `report:${name}`).length, 2);
      assert.deepEqual(events.slice(-2), ['processes:0', `report:${name}`]);
      for (const pid of [10, 11]) assert.ok(events.includes(`stop:${pid}:SIGTERM`));
      assert.equal(messages.some((message) => message.startsWith('RETRY:')), false);
      assert.ok(notes.some((note) => note.includes('bootstrap started after the window exited')));
    });
  }
}

for (const [label, scenario] of [
  ['a timeout', { timeout: true }], ['an interrupt', { interrupt: true }], ['a spawn failure', { spawnError: true }],
  ['started bootstrap', { started: true }], ['unreadable bootstrap report', { started: true, unreadable: true }],
  ['no window error', { marker: 'some other failure' }], ['a recovered window', { marker: 'CodeWindow: recovered from unresponsive' }],
  ['a successful exit without bootstrap', { code: 0 }], ['an unreadable result file', { reportError: true }],
  ['client group leftovers', { groupLeft: true }], ['a failed keeper release', { releaseFailed: true }],
  ['failure to keep the logs', { logFailure: true }],
  ['language server leftovers', { name: '2-single-file', languageLeft: true }], ['unidentified processes', { unidentified: true }],
  ['remote processes surviving cleanup', { servers: true, survivor: true }],
]) {
  test(`${label} fails without retrying`, async (t) => {
    const { run, messages, events, error } = await launches(t, scenario);
    assert.equal(run.manifest.launches.length, 1);
    assert.equal(messages.some((m) => m.startsWith('RETRY:')), false);
    assert.equal(events.filter((e) => e.startsWith('launch:')).length, 1);
    assert.ok(error !== undefined || run.failures.length > 0);
  });
}

test('a second pre-bootstrap window failure fails and retains both attempts', async (t) => {
  const { run, root, messages, error } = await launches(t, { secondFailure: true });
  assert.equal(error.kind, 'infra');
  assert.equal(run.manifest.launches.length, 2);
  assert.equal(run.manifest.launches[1].result, 'INFRA');
  for (const name of ['probe', 'probe-retry']) {
    assert.ok(fs.existsSync(path.join(root, 'work', 'client', name, 'user-data', 'logs', 'time', 'main.log')));
    assert.ok(fs.existsSync(path.join(root, 'evidence', 'launches', `${name}.log`)));
  }
  assert.equal(messages.filter((m) => m.startsWith('RETRY:')).length, 1);
});
