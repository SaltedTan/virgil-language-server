// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The Remote-SSH test's teardown, process groups, leak check, and bootstrap
// (e2e/remote-ssh/), with a fake /proc, clock, and VS Code: nothing is started
// or signaled.

const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const dir = path.join(__dirname, 'e2e/remote-ssh');

// Loads a module of the Remote-SSH test with some modules, process
// properties, and globals replaced.
function load(file, modules, overrides, globals = {}) {
  const module = { exports: {} };
  const localRequire = createRequire(path.join(dir, file));
  vm.runInNewContext(fs.readFileSync(path.join(dir, file), 'utf8'), {
    module, exports: module.exports, __dirname: dir, Buffer, AbortController, setTimeout, clearTimeout,
    process: Object.create(process, Object.getOwnPropertyDescriptors(overrides)),
    require: (name) => modules[name] ?? localRequire(name),
    ...globals,
  });
  return module.exports;
}

// node:fs with a fake /proc of processes, by PID: each has a start time, and
// may have a state, a parent, a process group, an executable, and an
// environment.
function fakeProc(processes) {
  const missing = (file) => Object.assign(new Error(`ENOENT: ${file}`), { code: 'ENOENT' });
  const entry = (file) => {
    const [, pid, name] = /^\/proc\/(\d+)\/(\w+)$/.exec(file) ?? [];
    const p = processes.get(Number(pid));
    if (p === undefined) throw missing(file);
    return [Number(pid), p, name];
  };
  return {
    ...fs,
    readdirSync: (name) => (name === '/proc' ? [...processes.keys()].map(String) : fs.readdirSync(name)),
    readFileSync: (file) => {
      const [pid, p, name] = entry(file);
      if (name === 'stat') return `${pid} (x) ${p.state ?? 'S'} ${p.ppid ?? 1} ${p.group ?? 0} ${Array(16).fill(0).join(' ')} ${p.start} 0 0`;
      if (name === 'environ') return (p.environ ?? []).join('\0');
      return '';
    },
    readlinkSync: (file) => entry(file)[1].exe ?? '/usr/bin/other',
  };
}

// localhost.cjs's teardown over fake processes (see fakeProc), each of which
// may say what happens when it gets a signal. Processes go on SIGKILL.
function teardown(processes, started) {
  const signals = [];
  const kill = (pid, name) => {
    signals.push([pid, name]);
    const p = processes.get(pid);
    p?.on?.(name);
    if (name === 'SIGKILL') processes.delete(pid);
  };
  const localhost = load('localhost.cjs', { 'node:fs': fakeProc(processes) }, { pid: 1, kill });
  const result = localhost.teardown({ work: '/work', sockets: '/sockets', marker: 'RUN=1', started, port: undefined });
  return result.then((value) => ({ result: value, signals }));
}

const child = (pid, exitCode = null) => ({ pid, exitCode, signalCode: null });

test('the teardown never signals a PID that a process of the run had and another process now has', async () => {
  const processes = new Map([
    // Xvfb has exited, and an unrelated process now has its PID.
    [100, { start: '900' }],
    // A process of the run, whose PID an unrelated process gets once it is
    // frozen and before it is killed.
    [200, { start: '600', environ: ['RUN=1'] }],
    // The SSH server, and a session that wrote its title over its environment.
    [300, { start: '700' }],
    [301, { start: '710', ppid: 300 }],
    [400, { start: '800' }],
  ]);
  processes.get(200).on = (name) => {
    if (name === 'SIGSTOP') processes.set(200, { start: '950' });
  };
  const { result, signals } = await teardown(processes, [
    { name: 'Xvfb', child: child(100, 1), identity: { pid: 100, start: '500' } },
    { name: 'sshd', child: child(300), identity: { pid: 300, start: '700' } },
  ]);
  assert.deepEqual(signals.filter(([pid]) => pid === 100 || pid === 400), []);
  assert.deepEqual(signals.filter(([pid]) => pid === 200), [[200, 'SIGSTOP']]);
  assert.deepEqual(signals.filter(([pid]) => pid >= 300).sort(),
    [[300, 'SIGKILL'], [300, 'SIGSTOP'], [301, 'SIGKILL'], [301, 'SIGSTOP']]);
  assert.equal(result.ok, true);
  assert.deepEqual([...result.stopped.map((p) => p.pid)].sort(), [200, 300, 301]);
});

test('the teardown stops a running Xvfb last, with SIGTERM', async () => {
  const processes = new Map([[100, { start: '500' }], [200, { start: '600', exe: '/work/remote/virgil-lsp' }]]);
  processes.get(100).on = (name) => {
    if (name === 'SIGTERM') processes.delete(100);
  };
  const { result, signals } = await teardown(processes, [
    { name: 'Xvfb', child: child(100), identity: { pid: 100, start: '500' } },
  ]);
  assert.deepEqual(signals, [[200, 'SIGSTOP'], [200, 'SIGKILL'], [100, 'SIGTERM']]);
  assert.equal(result.ok, true);
});

// run.cjs's runGroup, over fake processes (see fakeProc) and a fake clock, for
// a command whose first process has the PID 50, which exits when the test calls
// exit(). Processes go on SIGKILL, and may say what happens on other signals.
function runGroup(processes, timeout = 60000) {
  const signals = [];
  const kill = (target, name) => {
    signals.push([target, name]);
    for (const [pid, p] of [...processes]) {
      if (pid !== target && p.group !== -target) continue;
      p.on?.(name);
      if (name === 'SIGKILL') processes.delete(pid);
    }
  };
  let now = 0;
  let next = 1;
  const timers = new Map();
  const add = (fn, ms, every) => {
    timers.set(next, { at: now + ms, fn, every });
    return next++;
  };
  const clear = (id) => timers.delete(id);
  const child = Object.assign(new EventEmitter(), { pid: 50, stdout: new EventEmitter(), stderr: new EventEmitter() });
  const run = load('run.cjs', {
    'node:fs': fakeProc(processes),
    'node:child_process': { spawn: () => child },
    '../run.cjs': require('./e2e/run.cjs'),
    './localhost.cjs': {},
  }, { pid: 1, kill }, {
    Date: { now: () => now },
    setTimeout: (fn, ms) => add(fn, ms), setInterval: (fn, ms) => add(fn, ms, ms), clearTimeout: clear, clearInterval: clear,
  });
  const controller = new AbortController();
  let result;
  run.runGroup('command', [], { env: {}, timeout, signal: controller.signal }).then((value) => { result = value; });
  return {
    signals,
    interrupt: () => controller.abort(),
    exit: (code, signal, close = true) => {
      child.emit('exit', code, signal);
      if (close) child.emit('close', code, signal);
    },
    close: () => child.emit('close'),
    data: (name, text) => child[name].emit('data', Buffer.from(text)),
    // Runs the timers that are due in the next ms milliseconds.
    advance: (ms) => {
      const end = now + ms;
      for (;;) {
        const [id, timer] = [...timers].filter(([, t]) => t.at <= end).sort(([, a], [, b]) => a.at - b.at)[0] ?? [];
        if (timer === undefined) break;
        now = timer.at;
        if (timer.every === undefined) timers.delete(id);
        else timer.at += timer.every;
        timer.fn();
      }
      now = end;
    },
    // runGroup's result, once it has resolved.
    result: () => new Promise((resolve) => setImmediate(() => resolve(result))),
  };
}

const ignoreTerm = (processes, pid) => Object.assign(processes.get(pid), { on: () => {} });

test('a command waits for both output pipes after exit and retains their trailing bytes', async () => {
  const processes = new Map([[50, { start: '100', group: 50 }]]);
  const run = runGroup(processes);
  run.data('stdout', 'archive prefix');
  processes.delete(50);
  run.exit(0, null, false);
  run.advance(100);
  assert.equal(await run.result(), undefined);
  run.data('stdout', ' and tail');
  run.data('stderr', 'final diagnostic');
  run.close();
  const result = await run.result();
  assert.equal(result.stdout.toString(), 'archive prefix and tail');
  assert.equal(result.stderr, 'final diagnostic');
  assert.equal(result.at, 0);
});

test('a command that cannot spawn still completes after its pipes close', async () => {
  const { runGroup } = require(path.join(dir, 'run.cjs'));
  const result = await runGroup(path.join(dir, 'nonexistent-command'), [], {
    timeout: 1000, signal: new AbortController().signal,
  });
  assert.equal(result.error.code, 'ENOENT');
  assert.equal(result.stdout.length, 0);
  assert.equal(result.stderr, '');
});

test('a command\'s process group is stopped by its number while it is the run\'s', async () => {
  // A command that ignores SIGTERM and exceeds its timeout.
  let processes = new Map([[50, { start: '100', group: 50 }]]);
  let run = runGroup(processes, 1000);
  ignoreTerm(processes, 50);
  run.advance(1050);
  assert.deepEqual(run.signals, [[-50, 'SIGTERM']]);
  run.advance(5100);
  assert.deepEqual(run.signals, [[-50, 'SIGTERM'], [-50, 'SIGKILL']]);
  run.exit(null, 'SIGKILL');
  assert.equal((await run.result()).stopped, 'exceeded 1 seconds');

  // A command that exits, and leaves a process that ignores SIGTERM in its
  // group, which the runner saw there.
  processes = new Map([[50, { start: '100', group: 50 }], [51, { start: '110', group: 50 }]]);
  run = runGroup(processes);
  ignoreTerm(processes, 51);
  run.advance(50);
  processes.delete(50);
  run.exit(0, null);
  run.advance(10100);
  assert.deepEqual(run.signals, [[-50, 'SIGTERM']]);
  run.advance(5100);
  assert.deepEqual(run.signals, [[-50, 'SIGTERM'], [-50, 'SIGKILL']]);
  run.advance(100);
  const result = await run.result();
  assert.equal(result.groupLeft, true);
  assert.equal(result.code, 0);
});

test('a process group whose number has passed to another group is never signaled', async () => {
  // The command and a process that it started exit at once, and an unrelated
  // process is given the command's PID, and makes a group of it, before the
  // runner looks again.
  let processes = new Map([[50, { start: '100', group: 50 }], [51, { start: '110', group: 50 }]]);
  let run = runGroup(processes);
  run.advance(50);
  processes.clear();
  processes.set(50, { start: '200', group: 50 });
  processes.set(52, { start: '210', group: 50 });
  run.exit(0, null);
  run.advance(20000);
  assert.deepEqual(run.signals, []);
  assert.equal((await run.result()).groupLeft, false);

  // The command exits and leaves a process in its group, which SIGTERM stops;
  // then an unrelated process is given the number before SIGKILL would follow.
  processes = new Map([[50, { start: '100', group: 50 }], [51, { start: '110', group: 50 }]]);
  run = runGroup(processes);
  processes.get(51).on = (name) => {
    if (name !== 'SIGTERM') return;
    processes.delete(51);
    processes.set(50, { start: '200', group: 50 });
  };
  run.advance(50);
  processes.delete(50);
  run.exit(0, null);
  run.advance(10100);
  assert.deepEqual(run.signals, [[-50, 'SIGTERM']]);
  run.advance(20000);
  assert.deepEqual(run.signals, [[-50, 'SIGTERM']]);
  assert.equal((await run.result()).groupLeft, true);
});

test('once a process group is no longer the run\'s, its processes are stopped one by one', async () => {
  // The command exits and leaves a process in its group, which then makes a
  // session of its own; an unrelated process is given the number, and the
  // run is interrupted before the runner looks again.
  const processes = new Map([[50, { start: '100', group: 50 }], [51, { start: '110', group: 50 }]]);
  const run = runGroup(processes);
  run.advance(50);
  processes.delete(50);
  run.exit(0, null);
  processes.set(51, { start: '110', group: 51 });
  processes.set(50, { start: '200', group: 50 });
  run.interrupt();
  assert.deepEqual(run.signals, [[51, 'SIGTERM']]);
  run.advance(20000);
  assert.deepEqual(run.signals, [[51, 'SIGTERM']]);
  assert.equal((await run.result()).stopped, 'was interrupted');
});

test('server staging preserves a selected alternate executable and its resolved worker', async (t) => {
  const root = fs.mkdtempSync(path.join(__dirname, '.remote-ssh-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const build = path.join(root, 'build');
  fs.mkdirSync(build);
  for (const [name, value] of [['virgil-lsp', 'wrong'], ['candidate', 'selected'], ['virgil-lsp-worker', 'worker']]) {
    fs.writeFileSync(path.join(build, name), `#!/bin/sh\nprintf '${value}'\n`, { mode: 0o755 });
  }
  const link = path.join(root, 'server-link');
  fs.symlinkSync(path.join(build, 'candidate'), link);
  fs.writeFileSync(path.join(root, 'virgil-lsp-worker'), 'wrong worker');
  const { serverFiles } = require(path.join(dir, 'run.cjs'));
  for (const server of [path.join(build, 'candidate'), link]) {
    const work = fs.mkdtempSync(path.join(root, 'work-'));
    const run = { values: { server }, work, manifest: {} };
    const staged = await serverFiles(run);
    assert.equal(execFileSync(path.join(staged.dir, 'virgil-lsp'), { encoding: 'utf8' }), 'selected');
    assert.equal(execFileSync(path.join(staged.dir, 'virgil-lsp-worker'), { encoding: 'utf8' }), 'worker');
    for (const name of staged.names) {
      assert.equal(staged.details.sha256[name], crypto.createHash('sha256').update(fs.readFileSync(path.join(staged.dir, name))).digest('hex'));
    }
    assert.equal(staged.details.server, server);
  }
  const archive = path.join(root, 'build.tar.gz');
  execFileSync('tar', ['-czf', archive, '-C', root, 'build']);
  const work = fs.mkdtempSync(path.join(root, 'work-'));
  const staged = await serverFiles({ values: { archive }, work, env: process.env, signal: new AbortController().signal, checkpoint() {}, manifest: {} });
  assert.equal(execFileSync(path.join(staged.dir, 'virgil-lsp'), { encoding: 'utf8' }), 'wrong');
  assert.equal(execFileSync(path.join(staged.dir, 'virgil-lsp-worker'), { encoding: 'utf8' }), 'worker');
});

test('missing product executables fail before an upload, for direct and archive inputs', async (t) => {
  const root = fs.mkdtempSync(path.join(__dirname, '.remote-ssh-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const build = path.join(root, 'build');
  fs.mkdirSync(build);
  const { serverFiles } = require(path.join(dir, 'run.cjs'));
  for (const present of [[], ['virgil-lsp'], ['virgil-lsp-worker']]) {
    for (const name of present) fs.writeFileSync(path.join(build, name), name);
    const archive = path.join(root, 'build.tar.gz');
    execFileSync('tar', ['-czf', archive, '-C', root, 'build']);
    for (const values of [{ server: path.join(build, 'virgil-lsp') }, { archive }]) {
      const work = fs.mkdtempSync(path.join(root, 'work-'));
      const run = { values, work, env: process.env, signal: new AbortController().signal, checkpoint() {}, manifest: {} };
      await assert.rejects(serverFiles(run), (error) => error.kind === 'fail' && /missing server executable/.test(error.message));
      for (const name of ['virgil-lsp', 'virgil-lsp-worker']) {
        assert.equal(run.manifest.server.sha256[name] === null, !present.includes(name));
      }
    }
    for (const name of present) fs.unlinkSync(path.join(build, name));
  }
});

test('leak checks use the client-exit or host-stop time rather than the retrieval time', async () => {
  for (const at of [0, 10000]) {
    let now = at + 3000;
    const run = load('run.cjs', { '../run.cjs': require('./e2e/run.cjs') }, {}, {
      Date: { now: () => now },
      setTimeout: (fn, ms) => { now += ms; queueMicrotask(fn); },
    });
    const server = { pid: 20, start: '600', exe: '/remote/virgil-lsp/virgil-lsp' };
    const remote = {
      target: { root: '/remote' },
      remote: async () => ({ stdout: JSON.stringify({ found: now < at + 6000 ? [server] : [], host: false }) }),
    };
    const result = await run.settle(remote, { agent: '/remote/server', serverNode: '/node' }, at);
    assert.deepEqual(JSON.parse(JSON.stringify(result.left)), [server]);
    assert.ok(now <= at + 5200);
  }
});

test('host containment retains the signal timestamp across slow SSH responses', async () => {
  for (const stubborn of [false, true]) {
    let now = 1000;
    let signals = 0;
    let stoppedAt;
    const run = load('run.cjs', { '../run.cjs': require('./e2e/run.cjs') }, {}, {
      Date: { now: () => now },
      setTimeout: (fn, ms) => { now += ms; queueMicrotask(fn); },
    });
    const remote = {
      target: { root: '/remote' },
      remote: async (command) => {
        const options = JSON.parse(/'([^']*)'$/.exec(command)[1]);
        const signaled = options.signal !== undefined;
        if (signaled) {
          signals++;
          stoppedAt = now;
        }
        const host = signaled || (stubborn && signals < 2);
        const stdout = JSON.stringify({ found: [], host, signaledAt: signaled ? now : undefined });
        now += 1000;
        return { stdout };
      },
    };
    const result = await run.stopHost(remote, { agent: '/remote/server', serverNode: '/node' }, { pid: 10, start: '500', exe: '/node' });
    assert.equal(result.signal, stubborn ? 'SIGKILL' : 'SIGTERM');
    assert.equal(result.at, stoppedAt);
    assert.ok(result.at < now - 1000);
  }
});

test('output-directory filesystem failures are infrastructure errors', () => {
  for (const operation of ['mkdirSync', 'readdirSync', 'realpathSync']) {
    const files = {
      ...fs,
      existsSync: () => true,
      realpathSync: (file) => file,
      mkdirSync() {},
      readdirSync: () => [],
      [operation]: (file) => {
        if (operation === 'realpathSync' && file !== '/out') return file;
        throw new Error('ENOSPC');
      },
    };
    const run = load('run.cjs', {
      'node:fs': files,
      'node:util': { parseArgs: () => ({ values: { vsix: '/test.vsix', 'virgil-root': '/virgil', 'work-dir': '/work', out: '/out', repeat: '1', sshd: '/sshd' } }) },
      '../run.cjs': require('./e2e/run.cjs'),
    }, {});
    assert.throws(() => run.options(), (error) => error.kind === 'infra' && error.message.includes('ENOSPC'));
  }
});

test('launch settings and every launch-log operation classify filesystem failures as infrastructure', async () => {
  for (const operation of ['settings', 'mkdir', 'open', 'header', 'data', 'close']) {
    let writes = 0;
    let closes = 0;
    const fail = () => { throw new Error('ENOSPC'); };
    const files = {
      ...fs,
      mkdirSync: (file) => { if (operation === 'mkdir' && file.includes('launches')) fail(); },
      writeFileSync: () => { if (operation === 'settings') fail(); },
      openSync: () => (operation === 'open' ? fail() : 42),
      writeSync: () => {
        writes++;
        if ((operation === 'header' && writes === 1) || (operation === 'data' && writes > 1)) fail();
      },
      closeSync: () => {
        closes++;
        if (operation === 'close') fail();
      },
    };
    const child = Object.assign(new EventEmitter(), { stdout: new EventEmitter(), stderr: new EventEmitter() });
    const runner = load('run.cjs', {
      'node:fs': files,
      '../run.cjs': require('./e2e/run.cjs'),
      'node:child_process': { spawn: () => {
        queueMicrotask(() => {
          child.emit('exit', 0, null);
          child.stdout.emit('data', Buffer.from('trailing output'));
          child.emit('close', 0, null);
        });
        return child;
      } },
    }, {}, { setInterval, clearInterval });
    const run = {
      target: { alias: 'host' }, work: '/work', evidence: '/evidence', settings: {},
      manifest: { launches: [] }, signal: new AbortController().signal,
      remote: async () => ({}), checkpoint() {},
    };
    const layout = { results: '/results', data: '/data', project: '/project', tests: '/tests' };
    await assert.rejects(runner.launch(run, 'probe', 'probe', { executable: '/code', layout, vsix: { version: '1' } }),
      (error) => error.kind === 'infra' && error.message.includes('ENOSPC'));
    assert.equal(run.manifest.launches[0].result, 'INFRA');
    assert.equal(closes, ['header', 'data', 'close'].includes(operation) ? 1 : 0);
  }
});

test('scratch-directory failures produce an INFRA manifest and exit status', async () => {
  for (const operation of ['mkdtempSync', 'mkdirSync']) {
    const written = new Map();
    let status;
    const exited = new Error('exit');
    const runner = load('run.cjs', {
      'node:fs': {
        ...fs,
        existsSync: () => true,
        accessSync() {}, realpathSync: (file) => file,
        readdirSync: () => [], mkdirSync() {},
        mkdtempSync: (prefix) => `${prefix}test`, rmSync() {},
        writeFileSync: (file, data) => written.set(path.basename(file), data),
        [operation]: (file) => {
          if (operation === 'mkdirSync' && file === '/out') return;
          throw new Error('ENOSPC');
        },
      },
      'node:util': { parseArgs: () => ({ values: { vsix: '/test.vsix', 'virgil-root': '/virgil', 'work-dir': '/work', out: '/out', repeat: '1', sshd: '/sshd' } }) },
      'node:child_process': { execFileSync: () => 'commit' },
      '../run.cjs': require('./e2e/run.cjs'),
    }, {
      on() {}, stdout: new EventEmitter(), stderr: new EventEmitter(),
      exit: (value) => { status = value; throw exited; },
    });
    await assert.rejects(runner.main(), (error) => error === exited);
    assert.equal(status, 3);
    assert.equal(JSON.parse(written.get('manifest.json')).result, 'INFRA');
  }
});

// run.cjs's program that lists and signals processes on the host, over fake
// processes (see fakeProc), with options as run.cjs gives it them.
function remoteProcesses(processes, options) {
  const signals = [];
  const output = [];
  const { REMOTE_PROCESSES } = require(path.join(dir, 'run.cjs'));
  vm.runInNewContext(REMOTE_PROCESSES, {
    require: (name) => (name === 'node:fs' ? fakeProc(processes) : require(name)),
    process: { pid: 1, argv: [process.execPath, JSON.stringify({ kill: [], ...options })], kill: (pid, name) => signals.push([pid, name]) },
    console: { log: (line) => output.push(JSON.parse(line)) },
  });
  return { listing: output[0], signals };
}

test('the leak check signals a remote extension host only by its PID, start time, and executable', () => {
  const agent = '/remote/server/.vscode-server';
  const host = { pid: 10, start: '500', exe: `${agent}/bin/c/node` };
  const processes = new Map([
    [10, { start: '500', exe: host.exe }],
    [20, { start: '600', ppid: 10, exe: '/remote/virgil-lsp/virgil-lsp' }],
    [21, { start: '610', ppid: 20, exe: '/remote/virgil-lsp/virgil-lsp-worker' }],
    [30, { start: '700', exe: '/usr/bin/other' }],
  ]);
  const options = { root: '/remote', agent };
  const { listing, signals } = remoteProcesses(processes, { ...options, host, signal: 'SIGTERM' });
  assert.deepEqual(signals, [[10, 'SIGTERM']]);
  assert.equal(listing.host, true);
  assert.deepEqual(listing.found.map((p) => [p.pid, p.ppid]), [[10, 1], [20, 10], [21, 20]]);
  // Another process that has since been given the host's PID, and a process
  // with its PID and start time but another executable.
  for (const other of [{ ...host, start: '499' }, { ...host, exe: `${agent}/bin/c/other` }]) {
    const result = remoteProcesses(processes, { ...options, host: other, signal: 'SIGKILL' });
    assert.deepEqual(result.signals, []);
    assert.equal(result.listing.host, false);
  }
  // Leftovers are killed by PID and start time, and never the VS Code Server's.
  assert.deepEqual(remoteProcesses(processes, { ...options, kill: ['20@600', '21@611', '10@500'] }).signals, [[20, 'SIGKILL']]);
});

// bootstrap.cjs for a launch in mode, in a window with the workspace folders
// given by URI, with a fake VS Code that hasn't loaded the extension.
async function bootstrap(mode, folders) {
  const config = {
    launch: `1-${mode}`, mode, result: '/results/1.json', serverNode: '/server/node', project: '/remote/project',
    folders: mode === 'workspace' ? ['file:///remote/project'] : null,
  };
  const written = [];
  let suite = false;
  const files = {
    ...fs,
    readFileSync: (file, encoding) => (file === path.join(dir, 'run-config.json') ? JSON.stringify(config) : fs.readFileSync(file, encoding)),
    writeFileSync: (file, data) => written.push(JSON.parse(data)),
  };
  const vscode = {
    env: { remoteName: 'ssh-remote' },
    workspace: { workspaceFolders: folders?.map((uri) => ({ uri: { toString: () => uri } })) },
    extensions: { getExtension: () => undefined },
  };
  const { run } = load('bootstrap.cjs', {
    'node:fs': files, vscode, './suite.cjs': { run: async () => { suite = true; } },
  }, { execPath: '/server/node', platform: 'linux', env: {} });
  const error = await run().then(() => undefined, (caught) => caught);
  return { error, result: written.at(-1), suite };
}

test('bootstrap.cjs refuses a launch whose workspace folders are not its mode\'s', async () => {
  for (const [mode, folders] of [['workspace', undefined], ['workspace', ['file:///elsewhere']], ['single-file', ['file:///remote/project']]]) {
    const { error, result, suite } = await bootstrap(mode, folders);
    assert.match(error.message, /^scenario mismatch: /, `${mode} with ${folders}`);
    assert.equal(result.outcome, 'fail');
    assert.equal(suite, false);
  }
  for (const [mode, folders] of [['workspace', ['file:///remote/project']], ['single-file', undefined]]) {
    const { error, result } = await bootstrap(mode, folders);
    assert.match(error.message, /is not loaded/, `${mode} with ${folders}`);
    // Its identity, which run.cjs needs to stop it: the PID and start time of
    // this process, which it runs in here.
    assert.equal(result.pid, process.pid);
    if (process.platform === 'linux') {
      const stat = fs.readFileSync('/proc/self/stat', 'utf8');
      assert.equal(result.start, stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19]);
    }
  }
});
