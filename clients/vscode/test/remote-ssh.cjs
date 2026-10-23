// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The Remote-SSH test's teardown, leak check, and bootstrap (e2e/remote-ssh/),
// with a fake /proc and a fake VS Code: nothing is started or signaled.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const dir = path.join(__dirname, 'e2e/remote-ssh');

// Loads a module of the Remote-SSH test with some modules and process
// properties replaced.
function load(file, modules, overrides) {
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(dir, file), 'utf8'), {
    module, exports: module.exports, __dirname: dir, Buffer, setTimeout, clearTimeout,
    process: Object.create(process, Object.getOwnPropertyDescriptors(overrides)),
    require: (name) => modules[name] ?? require(name),
  });
  return module.exports;
}

// node:fs with a fake /proc of processes, by PID: each has a start time, and
// may have a parent, an executable, and an environment.
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
      if (name === 'stat') return `${pid} (x) S ${p.ppid ?? 1} ${Array(17).fill(0).join(' ')} ${p.start} 0 0`;
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
