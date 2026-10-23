// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The Remote-SSH test's teardown, process groups, leak check, VS Code
// download, and bootstrap (e2e/remote-ssh/), mostly with a fake /proc, clock,
// keeper, and VS Code (see support/fakes.cjs). Staging checks run fixture
// executables; archive checks also run tar in a real process group.

const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');
const { test } = require('node:test');
const vm = require('node:vm');
const fakes = require('./support/fakes.cjs');

const { fakeClock, fakeGroup, fakeKeeper, fakeProc, fakeProcesses } = fakes;
const dir = path.join(__dirname, 'e2e/remote-ssh');

// Loads a module of the Remote-SSH test with some modules, process
// properties, and globals replaced.
const load = (file, ...rest) => fakes.load(path.join('remote-ssh', file), ...rest);

// localhost.cjs's teardown over fake processes (see fakeProc), each of which
// may say what happens when it gets a signal. Processes go on SIGKILL. With
// pidfd, signals go through the fake pidfd helper (see fakeProcesses).
function teardown(processes, started, { pidfd = false } = {}) {
  const signals = [];
  const deliver = (pid, name) => {
    const p = processes.get(pid);
    p?.on?.(name);
    if (name === 'SIGKILL') processes.delete(pid);
  };
  const kill = (pid, name) => {
    signals.push([pid, name]);
    deliver(pid, name);
  };
  const localhost = load('localhost.cjs', {
    'node:fs': fakeProc(processes),
    '../processes.cjs': fakeProcesses(processes, { kill, pidfd, signals, deliver }),
  }, { pid: 1, kill });
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

test('the teardown sends its signals through pidfds where they are available', async () => {
  const processes = new Map([[100, { start: '500' }], [200, { start: '600', exe: '/work/remote/virgil-lsp' }]]);
  processes.get(100).on = (name) => {
    if (name === 'SIGTERM') processes.delete(100);
  };
  const { result, signals } = await teardown(processes, [
    { name: 'Xvfb', child: child(100), identity: { pid: 100, start: '500' } },
  ], { pidfd: true });
  // The last SIGKILL finds that Xvfb has gone.
  assert.deepEqual(signals, [
    ['pidfd', 'SIGSTOP', [[200, '600']]], ['pidfd', 'SIGKILL', [[200, '600']]],
    ['pidfd', 'SIGTERM', [[100, '500']]], ['pidfd', 'SIGKILL', [[100, '500']]],
  ]);
  assert.equal(result.ok, true);
});

// run.cjs's runGroup, over fake processes (see fakeProc) and a fake clock and
// group (see fakeGroup in support/fakes.cjs), for a command whose group's
// keeper has the PID 50, and is in processes. With pidfd, signals to single
// processes go through the fake pidfd helper.
function runGroup(processes, timeout = 60000, { pidfd = false } = {}) {
  const { keeper, kill, deliver, signals } = fakeGroup(processes);
  const clock = fakeClock();
  const run = load('run.cjs', {
    '../processes.cjs': fakeProcesses(processes, { keeper, kill, deliver, pidfd, signals, globals: clock.globals }),
    '../run.cjs': require('./e2e/run.cjs'),
    './localhost.cjs': {},
  }, { pid: 1, kill }, clock.globals);
  const controller = new AbortController();
  let result;
  run.runGroup('command', [], { env: {}, timeout, signal: controller.signal }).then((value) => { result = value; });
  return {
    signals,
    keeper,
    interrupt: () => controller.abort(),
    command: (pid) => keeper.command(pid),
    exit: (code, signal, close = true) => {
      keeper.exit(code, signal);
      if (close) keeper.close();
    },
    close: () => keeper.close(),
    data: (name, text) => keeper.data(name, text),
    advance: clock.advance,
    // runGroup's result, once it has resolved.
    result: () => new Promise((resolve) => setImmediate(() => resolve(result))),
  };
}

// The keeper in a fake process table.
const KEEPER = { start: '90', group: 50 };

const ignoreTerm = (processes, pid) => Object.assign(processes.get(pid), { on: () => {} });

test('a command waits for both output pipes after exit and retains their trailing bytes', async () => {
  const processes = new Map([[50, KEEPER], [51, { start: '100', group: 50 }]]);
  const run = runGroup(processes);
  run.command(51);
  run.data('stdout', 'archive prefix');
  processes.delete(51);
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
  assert.equal(run.keeper.released, 'release\n');
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

test('a command runs under a keeper that leads its process group, with only its own standard streams', async () => {
  const { runGroup } = require(path.join(dir, 'run.cjs'));
  const script = 'echo "$$ $(ps -o pgid= -p $$) $PPID"; cat; ls /dev/fd/ >&2';
  const result = await runGroup('sh', ['-c', script], { input: 'input\n', timeout: 10000, signal: new AbortController().signal });
  assert.equal(result.code, 0);
  const [pid, group, parent, input] = result.stdout.toString().trim().split(/\s+/);
  assert.notEqual(group, pid);
  assert.equal(group, parent);
  assert.equal(input, 'input');
  // Sort numerically: ls's own directory descriptor, if any, can sort
  // lexically before 2 (for example, 142).
  const descriptors = result.stderr.trim().split(/\s+/).map(Number).sort((a, b) => a - b);
  assert.deepEqual(descriptors.slice(0, 3), [0, 1, 2]);
  assert.ok(descriptors.length <= 4, result.stderr);
});

test('a command\'s process group is stopped by its keeper, never by the runner with its number', async () => {
  // A command that ignores SIGTERM and exceeds its timeout; the group's
  // SIGKILL ends the keeper too, before it can say how the command ended.
  let processes = new Map([[50, KEEPER], [51, { start: '100', group: 50 }]]);
  let run = runGroup(processes, 1000);
  ignoreTerm(processes, 51);
  run.command(51);
  run.advance(1050);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM']]);
  run.advance(5100);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM'], ['keeper', 'SIGKILL']]);
  await run.result();
  run.close();
  run.advance(100);
  let result = await run.result();
  assert.equal(result.stopped, 'exceeded 1 seconds');
  assert.equal(result.signal, 'SIGKILL');

  // A command that exits, and leaves a process that ignores SIGTERM in its
  // group, which the runner saw there.
  processes = new Map([[50, KEEPER], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50 }]]);
  run = runGroup(processes);
  ignoreTerm(processes, 52);
  run.command(51);
  run.advance(50);
  processes.delete(51);
  run.exit(0, null);
  run.advance(10100);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM']]);
  run.advance(5100);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM'], ['keeper', 'SIGKILL']]);
  await run.result();
  run.advance(100);
  result = await run.result();
  assert.equal(result.groupLeft, true);
  assert.equal(result.code, 0);
});

test('a process that joins the group just before the command exits is waited for, and stopped with the group', async () => {
  // The command starts a process in its group and exits before the runner
  // looks again, so the runner has never seen it, and every process that it
  // has seen there is gone.
  for (const leaves of [true, false]) {
    const processes = new Map([[50, KEEPER], [51, { start: '100', group: 50 }]]);
    const run = runGroup(processes);
    run.command(51);
    run.advance(50);
    processes.set(52, { start: '120', group: 50 });
    processes.get(52).on = (name) => {
      if (name === 'SIGTERM') processes.delete(52);
    };
    processes.delete(51);
    run.exit(0, null);
    run.advance(5000);
    assert.equal(await run.result(), undefined);
    if (leaves) processes.delete(52);
    run.advance(5100);
    const result = await run.result();
    assert.equal(result.groupLeft, !leaves);
    assert.deepEqual(run.signals, leaves ? [] : [['keeper', 'SIGTERM']]);
    assert.equal(run.keeper.released, 'release\n');
  }
});

test('once the keeper has gone, the group is never signaled by its number, only the processes seen there', async () => {
  for (const pidfd of [false, true]) {
    // Something else kills the keeper. The command's own process then makes a
    // session of its own, the command exits, and an unrelated process is given
    // the group's number, and makes a group of it, with another process.
    const processes = new Map([[50, KEEPER], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50 }]]);
    const run = runGroup(processes, 60000, { pidfd });
    run.command(51);
    run.advance(50);
    processes.delete(50);
    run.keeper.reap('SIGKILL');
    run.advance(50);
    assert.equal(await run.result(), undefined);
    processes.set(52, { start: '110', group: 52 });
    processes.delete(51);
    processes.set(50, { start: '200', group: 50 });
    processes.set(53, { start: '210', group: 50 });
    run.interrupt();
    run.advance(5100);
    run.close();
    run.advance(100);
    const result = await run.result();
    const seen = [[51, '100'], [52, '110']];
    assert.deepEqual(run.signals, pidfd
      ? [['pidfd', 'SIGTERM', seen], ['pidfd', 'SIGKILL', seen]]
      : [[52, 'SIGTERM'], [52, 'SIGKILL']]);
    assert.equal(result.stopped, 'was interrupted');
    assert.match(result.error.message, /keeper exited with SIGKILL first/);
    assert.equal(processes.has(50) && processes.has(53), true);
  }
});

test('a group whose keeper Node.js has reaped, but not yet reported, is signaled only through the processes seen there', async () => {
  // Something else kills the keeper, and Node.js reaps it, but hasn't run its
  // exit listeners yet, as when it reaps several children at once. The
  // command's own process has made a session of its own, and an unrelated
  // process has been given the group's number, and makes a group of it with
  // another process.
  const processes = new Map([[50, KEEPER], [51, { start: '100', group: 50 }]]);
  const run = runGroup(processes);
  run.command(51);
  run.advance(50);
  processes.delete(50);
  processes.set(51, { start: '100', group: 51 });
  processes.set(50, { start: '200', group: 50 });
  processes.set(53, { start: '210', group: 50 });
  // The runner asks the keeper, which isn't there to answer.
  run.interrupt();
  assert.deepEqual(run.signals, []);
  // Its exit listeners run, and its reports end without saying that it sent
  // the signal: the runner sends it to the process that it saw instead.
  run.keeper.reap('SIGKILL');
  assert.deepEqual(run.signals, [[51, 'SIGTERM']]);
  run.advance(5100);
  run.close();
  run.advance(100);
  const result = await run.result();
  assert.match(result.error.message, /keeper exited with SIGKILL first/);
  assert.deepEqual(run.signals.filter(([target]) => target < 0 || target === 53), []);
  assert.equal(processes.has(50) && processes.has(53), true);
});

test('a process that joins the group just before the command exits is waited for (real processes)', async () => {
  const { runGroup } = require(path.join(dir, 'run.cjs'));
  const started = Date.now();
  // The sleep doesn't hold the output pipes, which runGroup waits for anyway.
  const command = 'sleep 0.5 </dev/null >/dev/null 2>&1 & echo $!; exit 0';
  const result = await runGroup('sh', ['-c', command], { timeout: 10000, signal: new AbortController().signal });
  assert.equal(result.code, 0);
  assert.equal(result.groupLeft, false);
  assert.ok(Date.now() - started >= 450, `resolved after ${Date.now() - started} ms`);
  const sleep = Number(result.stdout.toString().trim());
  assert.throws(() => process.kill(sleep, 0), (error) => error.code === 'ESRCH' || error.code === 'EPERM');
});

// run.cjs's downloadVSCode over fake processes and a fake clock and keeper,
// as runGroup above, where the keeper runs download.cjs, PID 51, and its tar,
// PID 52, which ignores SIGTERM.
function download() {
  const processes = new Map([[50, KEEPER], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50 }]]);
  processes.get(51).on = (name) => {
    if (name === 'SIGTERM') processes.delete(51);
  };
  ignoreTerm(processes, 52);
  const { keeper, kill, signals } = fakeGroup(processes);
  const clock = fakeClock();
  const runner = load('run.cjs', {
    '../processes.cjs': fakeProcesses(processes, { keeper, kill, signals, globals: clock.globals }),
    '../run.cjs': require('./e2e/run.cjs'),
    './localhost.cjs': {},
  }, { pid: 1, kill, stderr: { write() {} } }, clock.globals);
  const controller = new AbortController();
  const run = {
    env: {}, signal: controller.signal,
    checkpoint() {
      if (controller.signal.aborted) throw controller.signal.reason;
    },
  };
  let outcome;
  runner.downloadVSCode(run).then((value) => { outcome = { value }; }, (error) => { outcome = { error }; });
  return { processes, signals, keeper, controller, advance: clock.advance, outcome: () => new Promise((resolve) => setImmediate(() => resolve(outcome))) };
}

test('an interrupt during the VS Code download stops it and its tar before the runner goes on', async () => {
  const run = download();
  const [, [keeperFile, options]] = run.keeper.spawned;
  assert.equal(keeperFile, path.join(__dirname, 'e2e/keeper.cjs'));
  const { command, args } = JSON.parse(options);
  assert.equal(command, process.execPath);
  assert.deepEqual(args.slice(0, 2), [path.join(__dirname, 'e2e/download.cjs'), require('./e2e/run.cjs').VSCODE_VERSION]);
  run.keeper.command(51);
  run.advance(50);
  const interrupted = Object.assign(new Error('interrupted by SIGTERM'), { kind: 'interrupted' });
  run.controller.abort(interrupted);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM']]);
  run.advance(4000);
  assert.equal(await run.outcome(), undefined);
  assert.equal(run.processes.has(52), true);
  run.advance(1100);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM'], ['keeper', 'SIGKILL']]);
  await run.outcome();
  run.keeper.close();
  run.advance(100);
  assert.equal((await run.outcome()).error, interrupted);
  assert.deepEqual([...run.processes.keys()], []);
});

test('a failed VS Code download is an infrastructure failure, with download.cjs\'s message', async () => {
  const run = download();
  run.keeper.command(51);
  run.keeper.data('stderr', 'Failed to download and unzip VS Code 1.142.0\n');
  run.processes.delete(51);
  run.processes.delete(52);
  run.keeper.exit(1, null);
  run.keeper.close();
  run.advance(100);
  const { error } = await run.outcome();
  assert.equal(error.kind, 'infra');
  assert.match(error.message, /download\.cjs exited with 1: Failed to download and unzip VS Code 1\.142\.0$/);
});

// The pinned @vscode/test-electron, with its HTTP requests answered with a
// .tar.gz of archive's files, under the top-level directory that VS Code's
// archives have; returns what it requested.
function testElectron(archive) {
  const requests = [];
  const real = require('@vscode/test-electron/out/request');
  const request = {
    ...real,
    getStream: async (url) => {
      requests.push(url);
      if (requests.length === 1) {
        return Object.assign(Readable.from([]), { statusCode: 302, headers: { location: 'https://example.invalid/code.tar.gz' } });
      }
      return Object.assign(Readable.from([archive]), { statusCode: 200, headers: { 'content-length': String(archive.length) } });
    },
  };
  const file = require.resolve('@vscode/test-electron/out/download.js');
  const localRequire = require('node:module').createRequire(file);
  const module = { exports: {} };
  vm.runInThisContext(`(function (exports, require, module, __filename, __dirname) {${fs.readFileSync(file, 'utf8')}\n})`)(
    module.exports, (name) => (name === './request' ? request : localRequire(name)), module, file, path.dirname(file));
  return { download: module.exports.download, requests };
}

test('a VS Code that an interrupt left half-extracted in the cache is replaced, not used', async (t) => {
  const root = fs.mkdtempSync(path.join(__dirname, '.remote-ssh-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'VSCode-linux-x64'));
  fs.writeFileSync(path.join(root, 'VSCode-linux-x64', 'code'), 'new');
  const archive = execFileSync('tar', ['-czf', '-', '-C', root, 'VSCode-linux-x64']);
  const cache = path.join(root, 'cache');
  const version = require('./e2e/run.cjs').VSCODE_VERSION;
  const installed = path.join(cache, `vscode-linux-x64-${version}`);
  fs.mkdirSync(installed, { recursive: true });
  fs.writeFileSync(path.join(installed, 'code'), 'half');
  const { SilentReporter } = require('@vscode/test-electron');
  const options = { version, cachePath: cache, platform: 'linux-x64', reporter: new SilentReporter() };
  let electron = testElectron(archive);
  assert.equal(await electron.download(options), path.join(installed, 'code'));
  assert.equal(electron.requests.length, 2);
  assert.equal(fs.readFileSync(path.join(installed, 'code'), 'utf8'), 'new');
  assert.ok(fs.existsSync(path.join(installed, 'is-complete')));
  // A finished one is used without a request.
  electron = testElectron(archive);
  assert.equal(await electron.download(options), path.join(installed, 'code'));
  assert.equal(electron.requests.length, 0);
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
  await t.test('archive input preserves packaged executables', async () => {
    const archive = path.join(root, 'build.tar.gz');
    execFileSync('tar', ['-czf', archive, '-C', root, 'build']);
    const work = fs.mkdtempSync(path.join(root, 'work-'));
    const staged = await serverFiles({ values: { archive }, work, env: process.env, signal: new AbortController().signal, checkpoint() {}, manifest: {} });
    assert.equal(execFileSync(path.join(staged.dir, 'virgil-lsp'), { encoding: 'utf8' }), 'wrong');
    assert.equal(execFileSync(path.join(staged.dir, 'virgil-lsp-worker'), { encoding: 'utf8' }), 'worker');
  });
});

test('missing product executables fail before an upload, for direct and archive inputs', async (t) => {
  const root = fs.mkdtempSync(path.join(__dirname, '.remote-ssh-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const build = path.join(root, 'build');
  fs.mkdirSync(build);
  const { serverFiles } = require(path.join(dir, 'run.cjs'));
  for (const present of [[], ['virgil-lsp'], ['virgil-lsp-worker']]) {
    for (const name of present) fs.writeFileSync(path.join(build, name), name);
    for (const kind of ['server', 'archive']) {
      await t.test(`${kind} input with ${present.join(', ') || 'no executables'}`, async () => {
        const values = kind === 'server' ? { server: path.join(build, 'virgil-lsp') } : { archive: path.join(root, 'build.tar.gz') };
        if (kind === 'archive') execFileSync('tar', ['-czf', values.archive, '-C', root, 'build']);
        const work = fs.mkdtempSync(path.join(root, 'work-'));
        const run = { values, work, env: process.env, signal: new AbortController().signal, checkpoint() {}, manifest: {} };
        await assert.rejects(serverFiles(run), (error) => error.kind === 'fail' && /missing server executable/.test(error.message));
        for (const name of ['virgil-lsp', 'virgil-lsp-worker']) {
          assert.equal(run.manifest.server.sha256[name] === null, !present.includes(name));
        }
      });
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
      remote: async () => ({ stdout: JSON.stringify({ found: now < at + 6000 ? [server] : [], host: false, sampledAt: now }) }),
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
        const stdout = JSON.stringify({ found: [], host, signaledAt: signaled ? now : undefined, sampledAt: now });
        now += 1000;
        return { stdout };
      },
    };
    const result = await run.stopHost(remote, { agent: '/remote/server', serverNode: '/node' }, { pid: 10, start: '500', exe: '/node' });
    assert.equal(result.signal, stubborn ? 'SIGKILL' : 'SIGTERM');
    assert.equal(result.at, stoppedAt);
    assert.ok(result.at < now - 1000);
    assert.ok(result.processes.sampledAt > result.at);
  }
});

test('a late first sample or a late empty sample cannot verify the leak deadline', async () => {
  for (const at of [0, 10000]) {
    for (const samples of [[{ offset: 7000, alive: false }], [{ offset: 7000, alive: true }],
      [{ offset: 3000, alive: true }, { offset: 7000, alive: false }]]) {
      let now = at;
      const runner = load('run.cjs', {}, {}, {
        Date: { now: () => now },
        setTimeout: (fn, ms) => { now += ms; queueMicrotask(fn); },
      });
      const pending = [...samples];
      const remote = {
        target: { root: '/remote' },
        remote: async () => {
          const sample = pending.shift();
          now = at + sample.offset;
          return { stdout: JSON.stringify({
            found: sample.alive ? [{ pid: 20, start: '600', exe: '/remote/virgil-lsp' }] : [],
            host: false, sampledAt: now,
          }) };
        },
      };
      await assert.rejects(runner.settle(remote, { agent: '/remote/server', serverNode: '/node' }, at),
        (error) => error.kind === 'infra' && error.message === 'leak check could not be verified in time');
    }
  }
});

test('host containment cannot pass with only a late shutdown-confirmation sample', async () => {
  let now = 0;
  const runner = load('run.cjs', {}, {}, {
    Date: { now: () => now },
    setTimeout: (fn, ms) => { now += ms; queueMicrotask(fn); },
  });
  const remote = {
    target: { root: '/remote' },
    remote: async (command) => {
      const options = JSON.parse(/'([^']*)'$/.exec(command)[1]);
      if (options.signal) return { stdout: JSON.stringify({ found: [], host: true, signaledAt: 0, sampledAt: 0 }) };
      now = 7000;
      return { stdout: JSON.stringify({ found: [], host: false, sampledAt: now }) };
    },
  };
  const layout = { agent: '/remote/server', serverNode: '/node' };
  const stopped = await runner.stopHost(remote, layout, { pid: 10, start: '500', exe: '/node' });
  await assert.rejects(runner.settle(remote, layout, stopped.at, undefined, stopped.processes),
    (error) => error.kind === 'infra' && error.message === 'leak check could not be verified in time');
});

test('launch samples at client exit and preserves deadlines across reports and host containment', async () => {
  for (const scenario of [
    { serverExit: 0, reportAt: 7000, result: 'PASS' },
    { serverExit: 6000, reportAt: 7000, result: 'INFRA' },
    { linger: true, reportAt: 1500, result: 'PASS' },
    { linger: true, ignoresHost: true, reportAt: 1500, result: 'FAIL' },
  ]) {
    let now = 0;
    let next = 1;
    const timers = new Map();
    const add = (fn, ms, every) => {
      timers.set(next, { at: now + ms, fn, every });
      return next++;
    };
    const advance = async (ms) => {
      const end = now + ms;
      for (;;) {
        const [id, timer] = [...timers].filter(([, value]) => value.at <= end).sort(([, a], [, b]) => a.at - b.at)[0] ?? [];
        if (timer === undefined) break;
        now = timer.at;
        if (timer.every === undefined) timers.delete(id);
        else timer.at += timer.every;
        timer.fn();
        await new Promise(setImmediate);
      }
      now = end;
      await new Promise(setImmediate);
    };
    const keeper = fakeKeeper(50);
    const spawn = keeper.spawn;
    keeper.spawn = (...args) => {
      queueMicrotask(() => {
        keeper.exit(0, null);
        keeper.data('stdout', 'PASS: suite\n');
        add(() => keeper.close(), 1500);
      });
      return spawn(...args);
    };
    const sampleTimes = [];
    const notes = [];
    const failures = [];
    let stoppedAt;
    let reportStarted;
    const runner = load('run.cjs', {
      'node:fs': { ...fs, mkdirSync() {}, writeFileSync() {}, openSync: () => 42, writeSync() {}, closeSync() {} },
      '../processes.cjs': fakeProcesses(new Map([[50, KEEPER]]), { keeper, kill() {} }),
    }, {}, {
      Date: { now: () => now },
      setTimeout: (fn, ms) => add(fn, ms), setInterval: (fn, ms) => add(fn, ms, ms),
      clearTimeout: (id) => timers.delete(id), clearInterval: (id) => timers.delete(id),
    });
    const run = {
      target: { alias: 'host', root: '/remote' }, work: '/work', evidence: '/evidence', settings: {},
      manifest: { launches: [] }, signal: new AbortController().signal,
      checkpoint() {}, fail: (message) => failures.push(message), note: (message) => notes.push(message),
      remote: async (command) => {
        if (command.startsWith("'/remote/server/node' -e ")) {
          sampleTimes.push(now);
          const options = JSON.parse(/'([^']*)'$/.exec(command)[1]);
          const hostRuns = scenario.linger && stoppedAt === undefined;
          const serverRuns = scenario.linger ? scenario.ignoresHost || stoppedAt === undefined : now < scenario.serverExit;
          if (options.signal) stoppedAt = now;
          return { stdout: JSON.stringify({
            found: [
              ...(hostRuns ? [{ pid: 10, start: '500', exe: '/remote/server/node' }] : []),
              ...(serverRuns ? [{ pid: 20, ppid: 10, start: '600', exe: '/remote/virgil-lsp' }] : []),
            ],
            host: hostRuns, sampledAt: now, signaledAt: options.signal ? now : undefined,
          }) };
        }
        if (command === "cat '/results/late.json'") {
          reportStarted = now;
          await new Promise((resolve) => add(resolve, scenario.reportAt - now));
          return { code: 0, stdout: JSON.stringify({ outcome: 'pass', pid: 10, start: '500', folders: null, seconds: 1 }) };
        }
        return { code: 0, stdout: '' };
      },
    };
    const layout = {
      results: '/results', data: '/data', project: '/project', tests: '/tests', fixture: '/fixture',
      machineSettings: '/settings/machine.json', server: '/remote', agent: '/remote/server', serverNode: '/remote/server/node',
    };
    const result = runner.launch(run, 'late', 'single-file', { executable: '/code', layout, vsix: { version: '1' } })
      .then((record) => ({ record }), (error) => ({ error }));
    await new Promise(setImmediate);
    assert.deepEqual(sampleTimes, [0]);
    assert.equal(reportStarted, undefined);
    await advance(11000);
    assert.equal(reportStarted, 1500);
    const completed = await result;
    if (scenario.result === 'INFRA') {
      assert.equal(completed.error.kind, 'infra');
      assert.equal(completed.error.message, 'leak check could not be verified in time');
      assert.equal(run.manifest.launches[0].result, 'INFRA');
    } else {
      assert.equal(completed.record.result, scenario.result);
      assert.equal(failures.length, scenario.result === 'FAIL' ? 1 : 0);
    }
    assert.equal(notes.length, scenario.linger ? 1 : 0);
    if (scenario.linger) {
      assert.equal(stoppedAt, 5000);
      assert.equal(completed.record.remoteExtensionHost.stoppedWith, 'SIGTERM');
    }
    assert.equal(timers.size, 0);
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
    }, { platform: 'linux' });
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
    const keeper = fakeKeeper(50);
    const spawn = keeper.spawn;
    keeper.spawn = (...args) => {
      queueMicrotask(() => {
        keeper.exit(0, null);
        keeper.data('stdout', 'trailing output');
        keeper.close();
      });
      return spawn(...args);
    };
    const runner = load('run.cjs', {
      'node:fs': files,
      '../run.cjs': require('./e2e/run.cjs'),
      '../processes.cjs': fakeProcesses(new Map([[50, KEEPER]]), { keeper, kill() {} }),
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
      platform: 'linux',
      on() {}, stdout: new EventEmitter(), stderr: new EventEmitter(),
      exit: (value) => { status = value; throw exited; },
    });
    await assert.rejects(runner.main(), (error) => error === exited);
    assert.equal(status, 3);
    assert.equal(JSON.parse(written.get('manifest.json')).result, 'INFRA');
  }
});

// run.cjs's program that lists and signals processes on the host, over fake
// processes (see fakeProc), with options as run.cjs gives it them. With
// pidfd, the host's pidfd helper "runs", and gives result for every target;
// without it, python3 isn't found there.
function remoteProcesses(processes, options, { pidfd = false, result = 'sent' } = {}) {
  const signals = [];
  const output = [];
  const { REMOTE_PROCESSES } = require(path.join(dir, 'run.cjs'));
  const spawnSync = (command, args) => {
    if (!pidfd) return { error: Object.assign(new Error(`spawnSync ${command} ENOENT`), { code: 'ENOENT' }) };
    const [name, targets] = JSON.parse(args.at(-1));
    signals.push(['pidfd', name, targets]);
    return { status: 0, stdout: JSON.stringify(targets.map(() => result)) };
  };
  const modules = { 'node:fs': fakeProc(processes), 'node:child_process': { spawnSync } };
  vm.runInNewContext(REMOTE_PROCESSES, {
    require: (name) => modules[name] ?? require(name),
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
  // Where the host has pidfds, the same signals go through them.
  assert.deepEqual(remoteProcesses(processes, { ...options, host, signal: 'SIGTERM', kill: ['20@600', '21@611'] }, { pidfd: true }).signals,
    [['pidfd', 'SIGTERM', [[10, '500']]], ['pidfd', 'SIGKILL', [[20, '600']]]]);
  // Where the helper finds a process gone or another's, nothing else signals
  // it; where it couldn't use a pidfd for it, the process gets the signal
  // after a check.
  for (const [result, fallback] of [['gone', []], ['other', []], ['error EPERM', [[10, 'SIGTERM'], [20, 'SIGKILL']]], ['error ENOSYS', [[10, 'SIGTERM'], [20, 'SIGKILL']]]]) {
    const { signals: sent } = remoteProcesses(processes, { ...options, host, signal: 'SIGTERM', kill: ['20@600'] }, { pidfd: true, result });
    assert.deepEqual(sent.filter(([pid]) => pid !== 'pidfd'), fallback, result);
  }
  // Without pidfds, a process that has since exited, and whose PID another
  // process has been given, is not signaled.
  const reused = new Map(processes);
  const sent = [];
  const { REMOTE_PROCESSES } = require(path.join(dir, 'run.cjs'));
  vm.runInNewContext(REMOTE_PROCESSES, {
    require: (name) => ({
      'node:fs': fakeProc(reused),
      'node:child_process': { spawnSync: () => {
        reused.set(20, { start: '900', exe: '/remote/virgil-lsp/virgil-lsp' });
        return { status: 0, stdout: 'null\n' };
      } },
    })[name],
    process: { pid: 1, argv: [process.execPath, JSON.stringify({ ...options, kill: ['20@600'] })], kill: (pid, name) => sent.push([pid, name]) },
    console: { log() {} },
  });
  assert.deepEqual(sent, []);
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
