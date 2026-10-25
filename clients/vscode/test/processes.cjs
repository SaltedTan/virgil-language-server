// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The test runners' process groups and signals (e2e/processes.cjs and
// e2e/keeper.cjs), and the Extension Host test's runGroup (e2e/run.cjs), and
// with a stopped keeper the Remote-SSH test's too, mostly with a fake /proc
// or ps, clock, and keeper (see support/fakes.cjs); some checks run real
// processes.

const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { fakeClock, fakeGroup, fakeProc, fakeProcesses, fakeThreadedProc, load } = require('./support/fakes.cjs');

const processes = require('./e2e/processes.cjs');

const pidfdSkip = process.platform !== 'linux' ? 'pidfds are a Linux feature'
  : processes.pidfdSupport() === true ? false : `no pidfds here: ${processes.pidfdSupport()}`;
const pythonSkip = spawnSync('python3', ['-I', '-c', '']).status === 0 ? false : 'python3 is not on PATH';
const macSkip = process.platform !== 'darwin' ? 'macOS libproc regression' : pythonSkip;

// Reproduce the result of ps's two-phase walk while real processes fork and
// are reaped: omit the surviving child from exactly the first listing after
// its parent is reaped. Subsequent scans and the native group listing are real.
for (const file of ['run.cjs', 'remote-ssh/run.cjs']) {
  test(`${file} keeps a forking macOS group's child alive until stopped (real processes)`, { skip: macSkip }, async () => {
    const directory = fs.mkdtempSync(path.join(__dirname, '.processes-'));
    const ready = path.join(directory, 'ready');
    let child;
    let omitted = false;
    let observed;
    let native = false;
    const real = require('node:child_process');
    const owned = load('processes.cjs', {
      'node:child_process': { ...real, spawnSync: (command, args, options) => {
        const result = real.spawnSync(command, args, options);
        if (command === 'python3' && args[2] === processes.DARWIN_GROUP_PIDS) {
          assert.ok(Array.isArray(JSON.parse(result.stdout)), result.stderr);
          native = true;
        }
        if (command === 'ps' && args[0] === '-A' && !omitted && fs.existsSync(ready)) {
          const [parent, pid] = JSON.parse(fs.readFileSync(ready, 'utf8'));
          const lines = result.stdout.split('\n');
          if (!lines.some((line) => Number(line.trim().split(/\s+/)[0]) === parent)) {
            child = processes.processInfo(pid);
            assert.ok(child && child.state !== 'Z');
            omitted = true;
            result.stdout = lines.filter((line) => Number(line.trim().split(/\s+/)[0]) !== pid).join('\n');
          }
        }
        return result;
      } },
    });
    const wrapped = { ...owned, startGroup: (...args) => {
      const group = owned.startGroup(...args);
      const look = group.look;
      group.look = () => {
        const was = omitted;
        const result = look();
        if (!was && omitted) observed = result;
        return result;
      };
      return group;
    } };
    const runner = load(file, { [file === 'run.cjs' ? './processes.cjs' : '../processes.cjs']: wrapped }, {}, { setInterval, clearInterval });
    const controller = new AbortController();
    let result;
    const running = runner.runGroup('python3', ['-I', '-c', `
import json, os, sys, time
directory = sys.argv[1]
helper = os.fork()
if helper == 0:
    child = os.fork()
    if child == 0:
        time.sleep(30)
        os._exit(0)
    with open(directory + '/child', 'w') as file:
        json.dump(child, file)
    os._exit(0)
os.waitpid(helper, 0)
with open(directory + '/child') as file:
    child = json.load(file)
with open(directory + '/ready.tmp', 'w') as file:
    json.dump([os.getpid(), child], file)
os.rename(directory + '/ready.tmp', directory + '/ready')
`, directory], { env: process.env, stdin: 'ignore', timeout: 60000, signal: controller.signal }).then((value) => { result = value; });
    try {
      assert.ok(await until(() => observed, 10000), file);
      assert.equal(native, true, 'CI must exercise libproc, not silently fall back');
      assert.equal(observed.empty, false, 'the listing that missed the child cannot release the keeper');
      await new Promise((resolve) => setTimeout(resolve, 150));
      assert.equal(result, undefined, file);
      assert.equal(processes.processInfo(child.pid)?.start, child.start);
      controller.abort();
      assert.ok(await until(() => result, 10000), file);
      assert.equal(result.stopped, 'was interrupted');
      const after = processes.processInfo(child.pid);
      assert.ok(after === undefined || after.start !== child.start || after.state === 'Z');
    } finally {
      controller.abort();
      if (child) processes.signalProcesses([child], 'SIGKILL');
      await running;
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

// ps's output for a fake process table on a system without /proc: each
// process has a start time, as ps prints it, and may have a state, a parent,
// and a group. As on macOS, ps -A has the kernel list the PIDs, then look up
// each process, leaving out one that has gone by then; looking(pid), if
// given, runs just before each lookup.
function fakePs(table, looking = () => {}) {
  return (args) => {
    const all = args[0] !== '-p';
    const lines = (all ? [...table.keys()] : [Number(args[1])]).flatMap((pid) => {
      if (all) looking(pid);
      const p = table.get(pid);
      return p === undefined ? [] : [[pid, p.ppid ?? 1, p.group ?? 0].map((n) => String(n).padStart(5)).join(' ') + ` ${p.state ?? 'Ss'}   ${p.start}`];
    });
    // ps -A always lists ps itself; -p fails for a process that has gone.
    return { status: args[0] === '-p' && lines.length === 0 ? 1 : 0, stdout: lines.map((line) => `${line}\n`).join(''), stderr: '' };
  };
}

function fakeDarwinInfo(table) {
  return (pids) => ({ status: 0, stdout: JSON.stringify(pids.map((pid) => {
    const p = table.get(pid);
    return p === undefined ? false : {
      pid, ppid: p.ppid ?? 1, group: p.group ?? 0, state: p.state ?? 'S',
      start: p.nativeStart ?? `darwin:1792746781.${String(pid).padStart(6, '0')}`,
    };
  })) });
}

test('signals to processes go through a pidfd where Python and the kernel have them, and otherwise follow a check', () => {
  const table = () => new Map([[10, { start: '500' }], [11, { start: '510', state: 'Z' }], [12, { start: '999' }]]);
  const targets = [{ pid: 10, start: '500' }, { pid: 11, start: '510' }, { pid: 12, start: '520' }, { pid: 13, start: '530' }];
  // With pidfds, the helper gets every target, and nothing else signals.
  let signals = [];
  let fake = fakeProcesses(table(), { pidfd: true, signals, kill: (pid, name) => signals.push([pid, name]) });
  fake.signalProcesses(targets, 'SIGTERM');
  assert.deepEqual(signals, [['pidfd', 'SIGTERM', [[10, '500'], [11, '510'], [12, '520'], [13, '530']]]]);
  assert.equal(fake.pidfdSupport(), true);
  // Without them, only a process that still has its start time, and isn't a
  // zombie, is signaled, and the helper isn't tried again.
  signals = [];
  fake = fakeProcesses(table(), { signals, kill: (pid, name) => signals.push([pid, name]) });
  fake.signalProcesses(targets, 'SIGTERM');
  fake.signalProcesses(targets, 'SIGKILL');
  assert.deepEqual(signals, [[10, 'SIGTERM'], [10, 'SIGKILL']]);
  assert.equal(fake.pidfdSupport(), 'python3 is not on PATH');
});

test('a process that the pidfd helper couldn\'t open a pidfd for or signal through one gets the signal after a check', () => {
  // The helper sent the signal to 10, found 11 gone and 12 another process,
  // and couldn't use a pidfd for the rest; of those, 15 has exited since, 16's
  // PID is another process's, and 17 can't be signaled.
  const table = new Map([[10, { start: '500' }], [12, { start: '999' }], [13, { start: '530' }], [14, { start: '540' }], [16, { start: '999' }], [17, { start: '570' }]]);
  const targets = [10, 11, 12, 13, 14, 15, 16, 17].map((pid) => ({ pid, start: String(pid * 10 + 400) }));
  const helper = ['sent', 'gone', 'other', 'error EPERM', 'error ENOSYS', 'error EMFILE', 'error EPERM', 'error EPERM'];
  const kills = [];
  const fake = load('processes.cjs', {
    'node:fs': fakeProc(table),
    'node:child_process': { spawnSync: () => ({ status: 0, stdout: `${JSON.stringify(helper)}\n` }) },
  }, {
    pid: 1,
    platform: 'linux',
    kill: (pid, name) => {
      if (pid === 17) throw Object.assign(new Error('kill EPERM'), { code: 'EPERM' });
      kills.push([pid, name]);
    },
  });
  assert.deepEqual(fake.signalProcesses(targets, 'SIGTERM'), ['sent', 'gone', 'other', 'sent', 'sent', 'gone', 'other', 'failed EPERM']);
  assert.deepEqual(kills, [[13, 'SIGTERM'], [14, 'SIGTERM']]);
  assert.equal(fake.pidfdSupport(), true);
});

test('the pidfd helper\'s answer decides whether signals go through pidfds', () => {
  for (const [helper, support] of [
    [{ status: 0, stdout: 'null\n' }, 'python3 or the kernel has no pidfds'],
    [{ status: 1, stdout: '', stderr: 'Traceback\n' }, 'the pidfd helper failed: Traceback'],
    [{ status: 0, stdout: '["sent"]\n' }, true],
  ]) {
    const kills = [];
    const fake = load('processes.cjs', {
      'node:fs': fakeProc(new Map([[10, { start: '500' }]])),
      'node:child_process': { spawnSync: () => helper },
    }, { pid: 1, platform: 'linux', kill: (pid, name) => kills.push([pid, name]) });
    fake.signalProcesses([{ pid: 10, start: '500' }], 'SIGTERM');
    assert.equal(fake.pidfdSupport(), support);
    assert.deepEqual(kills, support === true ? [] : [[10, 'SIGTERM']]);
  }
});

for (const code of ['EACCES', 'EPERM']) {
  test(`identity-checked signals skip a process whose stat returns ${code}`, () => {
    const table = new Map([[99, { start: '100' }]]);
    const proc = fakeProc(table);
    proc.readFileSync = () => { throw Object.assign(new Error(code), { code }); };
    const signals = [];
    const fake = fakeProcesses(table, { proc, kill: (...args) => signals.push(args) });
    assert.deepEqual(fake.signalProcesses([{ pid: 99, start: '100' }], 'SIGTERM'), ['gone']);
    assert.deepEqual(signals, []);
  });
}

for (const code of ['EIO', undefined]) {
  test(`process stat lookups and scans surface unexpected errors (${code})`, () => {
    const table = new Map([[99, { start: '100' }]]);
    const proc = fakeProc(table);
    const error = Object.assign(new Error('stat failed'), { code });
    proc.readFileSync = () => { throw error; };
    const fake = fakeProcesses(table, { proc });
    assert.throws(() => fake.processInfo(99), (caught) => caught === error);
    assert.throws(() => fake.processTable(), (caught) => caught === error);
  });
}

test('when macOS libproc is unavailable, ps start times retain the one-second fallback limit', () => {
  const start = 'Thu Oct 23 17:13:01 2026';
  const table = new Map([[10, { start, group: 10 }], [11, { start, group: 10, state: 'Z+' }], [12, { start: 'Thu Oct 23 17:14:02 2026', group: 12 }]]);
  const kills = [];
  const fake = fakeProcesses(new Map(), { platform: 'darwin', ps: fakePs(table), kill: (pid, name) => kills.push([pid, name]) });
  assert.deepEqual(JSON.parse(JSON.stringify(fake.processTable().processes)), [
    { pid: 10, state: 'S', ppid: 1, group: 10, start }, { pid: 11, state: 'Z', ppid: 1, group: 10, start },
    { pid: 12, state: 'S', ppid: 1, group: 12, start: 'Thu Oct 23 17:14:02 2026' },
  ]);
  assert.equal(fake.processInfo(13), undefined);
  fake.signalProcesses([{ pid: 10, start }, { pid: 11, start }, { pid: 12, start }, { pid: 13, start }], 'SIGTERM');
  assert.deepEqual(kills, [[10, 'SIGTERM']]);
  assert.equal(fake.pidfdSupport(), 'pidfds are a Linux feature');
  // A limitation, which clients/vscode/README.md describes: a process that is
  // given the PID of one that has exited, within the second that that one
  // started, has its start time, as ps gives it, and gets its signals.
  table.set(10, { start, group: 99 });
  fake.signalProcesses([{ pid: 10, start }], 'SIGKILL');
  assert.deepEqual(kills, [[10, 'SIGTERM'], [10, 'SIGKILL']]);
});

test('macOS records and checks microsecond identities, and never mixes native and ps precision', () => {
  const table = new Map([[10, { start: 'Thu Oct 23 17:13:01 2026', group: 10 }], [11, { start: 'Thu Oct 23 17:13:01 2026' }]]);
  let native = [{ pid: 10, ppid: 1, group: 10, state: 'S', start: 'darwin:1792746781.123456' }, false];
  const kills = [];
  const fake = load('processes.cjs', {
    'node:child_process': { spawnSync: (command, args) => command === 'ps' ? fakePs(table)(args)
      : { status: 0, stdout: JSON.stringify(native === null ? null : JSON.parse(args.at(-1)).map((pid) => native[pid - 10])) } },
  }, { platform: 'darwin', kill: (...args) => kills.push(args) });
  const recorded = fake.processInfo(10);
  assert.equal(recorded.start, 'darwin:1792746781.123456');
  assert.equal(fake.processInfo(11), undefined);
  assert.equal(fake.processTable().complete, false, 'a native ESRCH makes a scan inconclusive');
  assert.deepEqual(JSON.parse(JSON.stringify(fake.processTable().processes)), [native[0]]);
  assert.deepEqual(fake.signalProcesses([recorded], 'SIGTERM'), ['sent']);
  // A replacement with the same PID and start second cannot pass the check.
  native[0].start = 'darwin:1792746781.123457';
  assert.deepEqual(fake.signalProcesses([recorded], 'SIGKILL'), ['other']);
  // Loss of native support cannot downgrade a recorded exact identity.
  native = null;
  assert.deepEqual(fake.signalProcesses([recorded], 'SIGKILL'), ['gone']);
  assert.equal(fake.processInfo(10), null);
  assert.equal(fake.processTable().unavailable, true);
  assert.deepEqual(JSON.parse(JSON.stringify(fake.processTable().processes)), []);
  native = [{ ...recorded }];
  assert.deepEqual(fake.signalProcesses([recorded], 'SIGKILL'), ['sent']);
  assert.deepEqual(kills, [[10, 'SIGTERM'], [10, 'SIGKILL']]);
});

test('macOS group scans do not read unrelated processes owned by other users', () => {
  const start = 'Thu Oct 23 17:13:01 2026';
  const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }], [99, { start, group: 99 }]]);
  const setup = fakeGroup(table);
  const fake = fakeProcesses(table, {
    ...setup, platform: 'darwin', ps: fakePs(table),
    darwinInfo: (pids) => {
      assert.ok(!pids.includes(99), 'an unrelated unreadable process must not block the group');
      return fakeDarwinInfo(table)(pids);
    },
    darwinPids: () => [...table].filter(([, p]) => p.group === 50).map(([pid]) => pid),
  });
  const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
  assert.equal(group.look().processes[0].pid, 51);
  table.delete(51);
  assert.equal(group.look().empty, true);
  group.detach();
});

test('macOS keeps ps identities and timed confirmation when the first native probe fails', () => {
  const start = 'Thu Oct 23 17:13:01 2026';
  const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
  const clock = fakeClock();
  const setup = fakeGroup(table);
  let available = false;
  let probes = 0;
  const fake = fakeProcesses(table, {
    ...setup, platform: 'darwin', ps: fakePs(table), globals: clock.globals,
    darwinInfo: (pids) => {
      probes++;
      return available ? fakeDarwinInfo(table)(pids) : { status: 0, stdout: 'null' };
    },
    darwinPids: () => assert.fail('ps mode must use timed confirmation'),
  });
  const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
  available = true;
  assert.equal(fake.processInfo(51).start, start);
  assert.equal(fake.processTable().processes.find((p) => p.pid === 51).start, start);
  assert.deepEqual(fake.signalProcesses([{ pid: 51, start }], 'SIGTERM'), ['sent']);
  assert.deepEqual(setup.signals, [[51, 'SIGTERM']]);
  table.delete(51);
  assert.equal(group.look().empty, false);
  clock.advance(49);
  assert.equal(group.look().empty, false);
  clock.advance(1);
  assert.equal(group.look().empty, true);
  assert.equal(probes, 1);
  group.detach();
});

test('macOS retries transient native failures during the first look without losing ownership', () => {
  const failures = [
    { error: Object.assign(new Error('spawn failed'), { code: 'ENOENT' }) },
    { status: 1, stdout: '' },
    { status: 0, stdout: 'null' },
    { status: 0, stdout: '-1' },
    { status: 0, stdout: '[' },
    { status: 0, stdout: '[]' },
    { status: 0, stdout: '[null,null]' },
  ];
  for (const site of ['batch', 'keeper', 'group']) {
    for (const failure of failures) {
      const start = 'Thu Oct 23 17:13:01 2026';
      const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
      const setup = fakeGroup(table);
      const clock = fakeClock();
      let failing = false;
      const fake = fakeProcesses(table, {
        ...setup, platform: 'darwin', ps: fakePs(table), globals: clock.globals,
        darwinInfo: (pids) => failing && ((site === 'batch' && pids.length > 1) || (site === 'keeper' && pids.length === 1 && pids[0] === 50))
          ? failure : fakeDarwinInfo(table)(pids),
        darwinPids: () => failing && site === 'group' ? null : [...table.keys()],
      });
      const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
      failing = true;
      for (let i = 0; i < 2; i++) {
        const result = group.look();
        assert.equal(result?.empty, false, site);
        assert.equal(result.processes.length, 0, site);
        clock.advance(100);
      }
      failing = false;
      assert.equal(group.look().processes[0].start, 'darwin:1792746781.000051', site);
      table.delete(51);
      assert.equal(group.look().empty, true, site);
      assert.deepEqual(setup.signals, []);
      group.detach();
    }
  }
});

test('macOS recovers a failed initial native keeper lookup and completes normally', async () => {
  const start = 'Thu Oct 23 17:13:01 2026';
  const table = new Map([[1, { start, group: 1 }], [50, { start, group: 50 }], [51, { start, group: 50 }]]);
  const setup = fakeGroup(table);
  let firstKeeper = true;
  const fake = fakeProcesses(table, {
    ...setup, platform: 'darwin', ps: fakePs(table),
    darwinInfo: (pids) => {
      if (pids.length === 1 && pids[0] === 50 && firstKeeper) {
        firstKeeper = false;
        return { status: 0, stdout: 'null' };
      }
      return fakeDarwinInfo(table)(pids);
    },
    darwinPids: () => [...table].filter(([, p]) => p.group === 50).map(([pid]) => pid),
  });
  assert.equal(fake.processInfo(1).start, 'darwin:1792746781.000001');
  let exited;
  const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: (result) => { exited = result; } });
  table.get(50).ppid = 99;
  assert.equal(group.look()?.unavailable, true);
  table.get(50).ppid = 1;
  table.get(50).group = 99;
  assert.equal(group.look()?.unavailable, true);
  table.get(50).group = 50;
  assert.equal(group.look().processes[0].pid, 51);
  table.delete(51);
  setup.keeper.exit(0, null);
  assert.equal(group.look().empty, true);
  assert.deepEqual([exited.code, exited.signal], [0, null]);
  assert.equal(await group.release(), true);
  assert.deepEqual(setup.signals, []);
});

test('macOS release watchdog recovers a missing initial native keeper identity', async () => {
  const start = 'Thu Oct 23 17:13:01 2026';
  const table = new Map([[1, { start, group: 1 }], [50, { start, group: 50 }]]);
  const setup = fakeGroup(table);
  const clock = fakeClock();
  let firstKeeper = true;
  const fake = fakeProcesses(table, {
    ...setup, platform: 'darwin', ps: fakePs(table), globals: clock.globals,
    darwinInfo: (pids) => {
      if (pids.length === 1 && pids[0] === 50 && firstKeeper) {
        firstKeeper = false;
        return { status: 0, stdout: 'null' };
      }
      return fakeDarwinInfo(table)(pids);
    },
  });
  fake.processInfo(1);
  const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
  setup.keeper.stopped = true;
  const released = group.release();
  clock.advance(1000);
  assert.deepEqual(setup.signals, [[50, 'SIGKILL']]);
  assert.equal(await released, true);
  assert.equal(table.has(50), false);
});

test('macOS ends ownership when an unrecorded keeper exits before recovery', async () => {
  for (const replacement of [false, true]) {
    const start = 'Thu Oct 23 17:13:01 2026';
    const table = new Map([[1, { start, group: 1 }], [50, { start, group: 50 }]]);
    const setup = fakeGroup(table);
    let available = false;
    const fake = fakeProcesses(table, {
      ...setup, platform: 'darwin', ps: fakePs(table),
      darwinInfo: (pids) => !available && pids.length === 1 && pids[0] === 50
        ? { status: 0, stdout: 'null' } : fakeDarwinInfo(table)(pids),
      darwinPids: () => [...table].filter(([, p]) => p.group === 50).map(([pid]) => pid),
    });
    fake.processInfo(1);
    const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
    assert.equal(group.look()?.unavailable, true);
    setup.keeper.exit(0, null);
    table.delete(50);
    setup.keeper.reap();
    available = true;
    if (replacement) table.set(50, { start, group: 50, nativeStart: 'darwin:1792746781.999999' });
    assert.equal(group.look(), undefined);
    group.signal('SIGKILL');
    assert.deepEqual(setup.signals, []);
    assert.equal(table.has(50), replacement);
    assert.equal(await group.release(), true);
    assert.equal(group.detach(), true);
  }
});

test('macOS observes an omitted child in scans initiated inside the process group', () => {
  for (const trigger of ['spawn', 'signal']) {
    const start = 'Thu Oct 23 17:13:01 2026';
    const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
    const setup = fakeGroup(table);
    const clock = fakeClock();
    let omitted = false;
    let observed;
    const fake = fakeProcesses(table, {
      ...setup, platform: 'darwin', globals: clock.globals,
      ps: (args) => {
        if (args[0] === '-A' && !omitted) {
          omitted = true;
          return fakePs(new Map([[50, table.get(50)]]))(args);
        }
        return fakePs(table)(args);
      },
      darwinInfo: fakeDarwinInfo(table), darwinPids: () => [...table.keys()],
    });
    const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
    const look = group.look;
    group.look = () => {
      const was = omitted;
      const result = look();
      if (!was && omitted) observed = result;
      return result;
    };
    if (trigger === 'spawn') setup.keeper.command(51);
    else {
      setup.keeper.stopped = true;
      group.signal('SIGTERM');
      clock.advance(1000);
    }
    assert.equal(omitted, true, trigger);
    assert.equal(observed?.empty, false, trigger);
    assert.equal(observed.processes.length, 0, trigger);
    assert.equal(group.look().processes[0].pid, 51);
    assert.equal(table.has(51), true);
    group.detach();
  }
});

test('macOS failed native looks never replace remembered identities or remember partial scans', () => {
  for (const site of ['batch', 'partial', 'keeper', 'group']) {
    const start = 'Thu Oct 23 17:13:01 2026';
    const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
    const setup = fakeGroup(table);
    let failing = false;
    let offline = false;
    const fake = fakeProcesses(table, {
      ...setup, platform: 'darwin', ps: fakePs(table),
      darwinInfo: (pids) => {
        if (offline || (failing && ((site === 'batch' && pids.length > 1) || (site === 'keeper' && pids.length === 1 && pids[0] === 50)))) {
          return { status: 0, stdout: 'null' };
        }
        const info = fakeDarwinInfo(table)(pids);
        if (failing && site === 'partial' && pids.length > 1) {
          const records = JSON.parse(info.stdout);
          records[1] = null;
          info.stdout = JSON.stringify(records);
        }
        return info;
      },
      darwinPids: () => failing && site === 'group' ? null : [...table.keys()],
    });
    const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
    assert.equal(group.look().processes[0].start, 'darwin:1792746781.000051');
    table.set(52, { start, group: 50 });
    failing = true;
    assert.equal(group.look().empty, false, site);
    table.get(51).nativeStart = 'darwin:1792746781.000052';
    table.delete(50);
    setup.keeper.reap('SIGKILL');
    offline = true;
    group.signal('SIGTERM');
    assert.deepEqual(setup.signals, [], site);
    offline = false;
    failing = false;
    assert.equal(group.look(), undefined, site);
    group.signal('SIGKILL');
    assert.deepEqual(setup.signals, [], site);
    assert.equal(table.has(51), true);
    assert.equal(table.has(52), true);
    group.detach();
  }
});

test('macOS preserves ownership when a remembered native member cannot be read', () => {
  const start = 'Thu Oct 23 17:13:01 2026';
  const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }], [99, { start, group: 99 }]]);
  const setup = fakeGroup(table);
  let failing = false;
  const fake = fakeProcesses(table, {
    ...setup, platform: 'darwin', ps: fakePs(table),
    darwinInfo: (pids) => failing && pids.length === 1 && pids[0] === 51
      ? { status: 0, stdout: '[null]' } : fakeDarwinInfo(table)(pids),
    darwinPids: () => [...table].filter(([, p]) => p.group === 50).map(([pid]) => pid),
  });
  const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
  assert.equal(group.look().processes.length, 1);
  table.delete(50);
  setup.keeper.reap('SIGKILL');
  failing = true;
  assert.equal(group.look()?.empty, false);
  group.signal('SIGTERM');
  assert.deepEqual(setup.signals, []);
  failing = false;
  assert.equal(group.look().processes[0].start, 'darwin:1792746781.000051');
  group.signal('SIGTERM');
  assert.deepEqual(setup.signals, [[51, 'SIGTERM']]);
  group.detach();
});

test('macOS detach does not report an unconfirmed native keeper as gone', () => {
  const table = new Map([[50, { start: 'Thu Oct 23 17:13:01 2026', group: 50 }]]);
  const setup = fakeGroup(table);
  let failing = false;
  const fake = fakeProcesses(table, {
    ...setup, platform: 'darwin', ps: fakePs(table),
    darwinInfo: (pids) => failing ? { status: 0, stdout: 'null' } : fakeDarwinInfo(table)(pids),
  });
  const group = fake.startGroup('code', [], { env: {}, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => {} });
  failing = true;
  assert.equal(group.detach(), false);
});

test('the Extension Host retries an inconclusive native look before checking for servers left running', async () => {
  const start = 'Thu Oct 23 17:13:01 2026';
  const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }],
    [52, { start, group: 50, on: () => table.delete(52) }]]);
  const setup = fakeGroup(table);
  const clock = fakeClock();
  let failing = false;
  const owned = fakeProcesses(table, {
    ...setup, platform: 'darwin', ps: fakePs(table), globals: clock.globals,
    darwinInfo: (pids) => failing ? { status: 0, stdout: 'null' } : fakeDarwinInfo(table)(pids),
    darwinPids: () => [...table.keys()],
  });
  const runner = load('run.cjs', {
    '@vscode/test-electron': {}, './processes.cjs': owned,
    'node:child_process': { spawnSync: () => ({ status: 0, stdout: table.has(52) ? '52 50 /build/virgil-lsp\n' : '' }) },
  }, { pid: 1, platform: 'darwin' }, clock.globals);
  let result;
  const controller = new AbortController();
  runner.runGroup('code', [], { env: {}, stdin: 'ignore', signal: controller.signal, servers: ['/build/virgil-lsp'] })
    .then((value) => { result = value; });
  setup.keeper.command(51);
  table.delete(51);
  failing = true;
  setup.keeper.exit(0, null);
  assert.equal(setup.keeper.released, undefined);
  failing = false;
  clock.advance(100);
  await new Promise((resolve) => setImmediate(resolve));
  clock.advance(5000);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(setup.signals, [['keeper', 'SIGTERM']]);
  clock.advance(50);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(result.stopped, 'left virgil-lsp (pid 52) running 5 seconds after VS Code exited');
  assert.equal(setup.keeper.released, 'release\n');
});

test('the macOS ctypes helper reads fixed-width fields and handles missing, denied, and short records', { skip: pythonSkip }, () => {
  const setup = `
import ctypes, errno, types
def info(pid, flavor, arg, address, size):
    assert flavor == 3 and arg == 1 and size == 136
    if pid == 11:
        ctypes.set_errno(errno.ESRCH)
        return 0
    if pid == 12:
        ctypes.set_errno(errno.EPERM)
        return 0
    if pid == 13:
        return 120
    buf = address._obj
    buf.pbi_pid, buf.pbi_ppid, buf.pbi_pgid = pid, 8, 9
    buf.pbi_status = 4 if pid == 14 else 5
    buf.pbi_start_tvsec, buf.pbi_start_tvusec = 1792746781, 123456
    return size
ctypes.CDLL = lambda *args, **kwargs: types.SimpleNamespace(proc_pidinfo=info)
`;
  const helper = spawnSync('python3', ['-I', '-c', setup + processes.DARWIN_PROCESS_INFO, '[10,11,12,13,14]'], { encoding: 'utf8' });
  assert.equal(helper.status, 0, helper.stderr);
  assert.deepEqual(JSON.parse(helper.stdout), [
    { pid: 10, ppid: 8, group: 9, state: 'Z', start: 'darwin:1792746781.123456' },
    false, null, null,
    { pid: 14, ppid: 8, group: 9, state: 'T', start: 'darwin:1792746781.123456' },
  ]);
});

test('the macOS group helper counts PIDs, retries a full buffer, and reports failure', { skip: pythonSkip }, () => {
  for (const mode of ['grow', 'empty', 'EPERM', 'ENOMEM', 'negative', 'grow-empty', 'grow-EPERM', 'grow-ENOMEM', 'grow-negative']) {
    const setup = `
import ctypes, errno, types
ctypes.set_errno(errno.EPERM)
def listing(group, buf, size):
    assert group == 50 and size == ctypes.sizeof(buf)
    assert ctypes.get_errno() == 0
    if '${mode}'.startswith('grow-') and len(buf) == 256:
        ctypes.set_errno(errno.EPERM)
        return 256
    mode = '${mode}'.split('-')[-1]
    if mode == 'empty':
        return 0
    if mode in ('EPERM', 'ENOMEM'):
        ctypes.set_errno(getattr(errno, mode))
        return 0
    if mode == 'negative':
        return -1
    if len(buf) == 256:
        ctypes.set_errno(errno.EPERM)
        return 256
    assert len(buf) == 512
    buf[0], buf[1] = 50, 53
    return 2
def library(name, *, use_errno):
    assert use_errno is True
    return types.SimpleNamespace(proc_listpgrppids=listing)
ctypes.CDLL = library
`;
    const helper = spawnSync('python3', ['-I', '-c', setup + processes.DARWIN_GROUP_PIDS, '50'], { encoding: 'utf8' });
    assert.equal(helper.status, 0, helper.stderr);
    assert.deepEqual(JSON.parse(helper.stdout), mode === 'grow' ? [50, 53] : mode.endsWith('empty') ? [] : null);
  }
});

test('macOS live processes have distinct sub-second identities in records and signal checks', { skip: macSkip }, async () => {
  // Both Darwin ABIs must agree with the ctypes layout, independently of the
  // runner's Python architecture or whether the server runs under Rosetta.
  const directory = fs.mkdtempSync(path.join(__dirname, '.processes-'));
  try {
    const source = path.join(directory, 'layout.c');
    fs.writeFileSync(source, `#include <sys/proc_info.h>
#include <stddef.h>
_Static_assert(sizeof(struct proc_bsdinfo) == 136, "sizeof");
_Static_assert(offsetof(struct proc_bsdinfo, pbi_pid) == 12, "pid");
_Static_assert(offsetof(struct proc_bsdinfo, pbi_ppid) == 16, "ppid");
_Static_assert(offsetof(struct proc_bsdinfo, pbi_pgid) == 100, "pgid");
_Static_assert(offsetof(struct proc_bsdinfo, pbi_start_tvsec) == 120, "seconds");
_Static_assert(offsetof(struct proc_bsdinfo, pbi_start_tvusec) == 128, "microseconds");
`);
    const compiled = spawnSync('cc', ['-arch', 'arm64', '-arch', 'x86_64', '-c', source, '-o', path.join(directory, 'layout.o')], { encoding: 'utf8' });
    assert.equal(compiled.status, 0, compiled.stderr);
    let matched = false;
    for (let attempt = 0; attempt < 10 && !matched; attempt++) {
      // The CI runner's inherited group can also hold privileged processes.
      // Give each child its own group, as the real runners do with keepers.
      const first = await ownProcess('sleep', ['30'], { detached: true });
      let second;
      try {
        second = await ownProcess('sleep', ['30'], { detached: true });
        assert.match(first.start, /^darwin:\d+\.\d{6}$/);
        assert.match(second.start, /^darwin:\d+\.\d{6}$/);
        if (first.start.split('.')[0] !== second.start.split('.')[0]) continue;
        assert.notEqual(first.start, second.start);
        const group = processes.processInfo(first.child.pid).group;
        const listed = processes.processTable(group).processes.find((p) => p.pid === first.child.pid);
        assert.equal(listed?.start, first.start);
        assert.deepEqual(processes.signalProcesses([{ pid: first.child.pid, start: second.start }], 'SIGTERM'), ['other']);
        assert.equal(first.child.signalCode, null);
        assert.deepEqual(processes.signalProcesses([{ pid: first.child.pid, start: first.start }], 'SIGTERM'), ['sent']);
        assert.equal(await first.exited, 'SIGTERM');
        matched = true;
      } finally {
        first.child.kill('SIGKILL');
        second?.child.kill('SIGKILL');
        await Promise.all([first.exited, second?.exited]);
      }
    }
    assert.equal(matched, true, 'no two live processes started in the same second');
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('a process whose main thread has exited while its other threads run gets signals after a check, unlike a zombie', () => {
  // Linux lists both as zombies: 11 with another thread still running, and 12
  // with none.
  const table = new Map([[11, { start: '510', state: 'Z', threads: 2 }], [12, { start: '520', state: 'Z' }]]);
  const kills = [];
  const fake = fakeProcesses(table, { kill: (pid, name) => kills.push([pid, name]) });
  assert.deepEqual(fake.signalProcesses([{ pid: 11, start: '510' }, { pid: 12, start: '520' }], 'SIGKILL'), ['sent', 'gone']);
  assert.deepEqual(kills, [[11, 'SIGKILL']]);
});

test('a process is identified by a thread that outlives the choice of whose files to read, and reported if none does', () => {
  const files = { exe: 'link', environ: 'latin1', cmdline: 'utf8' };
  // Its main thread has exited; the first live thread whose files the scan
  // reads exits meanwhile, and the next one still identifies it.
  assert.deepEqual(processes.processFiles(fakeThreadedProc(), 100, files), {
    pid: 100, ppid: 1, start: '12345', files: { exe: '/work/python3', environ: 'RUN=1', cmdline: 'python3\0-c\0pass' },
  });
  // With every live thread gone by then, while the process still runs, it is
  // neither identified nor known to have gone.
  assert.deepEqual(processes.processFiles(fakeThreadedProc({ vanishing: ['101', '102'] }), 100, files), { pid: 100, ppid: 1, start: '12345', unreadable: true });
  // A process that has gone, or is a zombie, has no files to identify it.
  assert.equal(processes.processFiles(fakeProc(new Map([[100, { start: '12345', state: 'Z' }]])), 100, files), undefined);
});

test('identifying files never combine identities when a PID is reused during a read', () => {
  for (const pivot of ['stat', 'exe', 'environ', 'cmdline']) {
    const table = new Map([[100, { ppid: 50, start: '100', exe: '/work/old', environ: ['RUN=1'] }]]);
    const proc = fakeProc(table);
    let replaced = false;
    const replace = (file) => {
      if (!replaced && file === `/proc/100/${pivot}`) {
        table.set(100, { ppid: 1, start: '200', exe: '/usr/bin/new' });
        replaced = true;
      }
    };
    const racing = { ...proc, readdirSync: () => [],
      readFileSync: (file, ...args) => {
        const result = proc.readFileSync(file, ...args);
        replace(file);
        return result;
      },
      readlinkSync: (file) => {
        const result = proc.readlinkSync(file);
        replace(file);
        return result;
      },
    };
    const files = { exe: 'link', environ: 'latin1' };
    assert.deepEqual(processes.processFiles(racing, 100, files), { pid: 100, ppid: 1, start: '200', unreadable: true }, pivot);
    assert.deepEqual(processes.processFiles(racing, 100, files), {
      pid: 100, ppid: 1, start: '200', files: { exe: '/usr/bin/new', environ: '', cmdline: '' },
    }, pivot);
  }
});

test('identifying files rejects a thread ID reused while its process remains live', () => {
  const proc = fakeThreadedProc();
  let replaced = false;
  const racing = { ...proc,
    readFileSync: (file, ...args) => {
      const result = proc.readFileSync(file, ...args);
      return replaced && file === '/proc/100/task/102/stat' ? result.replace(' 12345 ', ' 12346 ') : result;
    },
    readlinkSync: (file) => {
      const result = proc.readlinkSync(file);
      replaced = true;
      return result;
    },
  };
  assert.deepEqual(processes.processFiles(racing, 100, { exe: 'link' }), { pid: 100, ppid: 1, start: '12345', unreadable: true });
  assert.equal(processes.processFiles(racing, 100, { exe: 'link' }).files.exe, '/work/python3');
});

test('a file that a process doesn\'t have, or that its user may not read, is left out of its files', () => {
  // A kernel thread, which has no exe, and another user's process, whose exe
  // and environ this user may not read; cmdline is always read.
  const stat = (pid, name) => `${pid} (${name}) S 1 0 ${Array(14).fill(0).join(' ')} 1 0 12345 0 0`;
  const proc = {
    readdirSync: () => [],
    readFileSync: (file) => {
      if (file === '/proc/100/stat') return stat(100, 'kthreadd');
      if (file === '/proc/100/environ' || file === '/proc/100/cmdline') return '';
      if (file === '/proc/101/stat') return stat(101, 'ssh-agent');
      if (file === '/proc/101/cmdline') return 'ssh-agent\0-l\0/tmp/agent';
      if (file === '/proc/103/stat') return stat(103, 'python3');
      if (file === '/proc/103/environ') return 'RUN=1';
      throw Object.assign(new Error('EACCES'), { code: 'EACCES' });
    },
    readlinkSync: (file) => {
      if (file === '/proc/103/exe') return '/work/python3';
      throw Object.assign(new Error('ENOENT'), { code: file.startsWith('/proc/101/') ? 'EACCES' : 'ENOENT' });
    },
  };
  assert.deepEqual(processes.processFiles(proc, 100, { exe: 'link', environ: 'latin1' }), { pid: 100, ppid: 1, start: '12345', files: { environ: '', cmdline: '' } });
  assert.deepEqual(processes.processFiles(proc, 101, { exe: 'link', environ: 'latin1' }), { pid: 101, ppid: 1, start: '12345', files: { cmdline: 'ssh-agent\0-l\0/tmp/agent' } });
  // cmdline only proves liveness: a failure leaves it out, and the other
  // files still identify the process.
  assert.deepEqual(processes.processFiles(proc, 103, { exe: 'link', environ: 'latin1' }), { pid: 103, ppid: 1, start: '12345', files: { exe: '/work/python3', environ: 'RUN=1' } });
  // With no file readable at all, a live process is not identified; one
  // whose threads have all gone is neither identified nor known to be gone.
  const denied = { readdirSync: () => [], readFileSync: (file) => {
    if (file === '/proc/102/stat') return stat(102, 'sshd');
    throw Object.assign(new Error('EACCES'), { code: 'EACCES' });
  }, readlinkSync: () => { throw Object.assign(new Error('EACCES'), { code: 'EACCES' }); } };
  assert.deepEqual(processes.processFiles(denied, 102, { exe: 'link', environ: 'latin1' }), { pid: 102, ppid: 1, start: '12345', unreadable: true });
});

// Starts a real process that the test owns, and returns it with its start
// time, once it runs.
async function ownProcess(command, args, options = {}) {
  const child = spawn(command, args, { ...options, stdio: 'ignore' });
  await new Promise((resolve) => child.on('spawn', resolve));
  const exited = new Promise((resolve) => child.on('exit', (code, signal) => resolve(signal ?? code)));
  return { child, exited, start: processes.processInfo(child.pid).start };
}

test('the pidfd helper signals a process only while it has its start time (real processes)', { skip: pidfdSkip }, async () => {
  const { child, exited, start } = await ownProcess('sleep', ['30']);
  try {
    assert.deepEqual(processes.signalProcesses([{ pid: child.pid, start: String(Number(start) - 1) }], 'SIGTERM'), ['other']);
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(child.exitCode, null);
    assert.equal(child.signalCode, null);
    assert.deepEqual(processes.signalProcesses([{ pid: child.pid, start }], 'SIGTERM'), ['sent']);
    assert.equal(await exited, 'SIGTERM');
    assert.deepEqual(processes.signalProcesses([{ pid: child.pid, start }], 'SIGTERM'), ['gone']);
    assert.equal(processes.pidfdSupport(), true);
  } finally {
    child.kill('SIGKILL');
  }
});

test('where the pidfd helper can\'t use a pidfd for a process, the process gets the signal after a check (real processes)', { skip: pidfdSkip }, async () => {
  // Python code that runs before the helper's, to refuse some of its system
  // calls with an errno, as a system call filter might.
  const refuse = (errno, calls) => `import errno, os, signal
def refuse(*args):
    raise OSError(errno.${errno}, 'refused')
send, open_ = signal.pidfd_send_signal, os.pidfd_open
${calls}`;
  const real = require('node:child_process');
  for (const [code, output] of [
    // pidfd_send_signal refuses signals, but not the helper's check with 0.
    [refuse('ENOSYS', 'signal.pidfd_send_signal = lambda fd, sig: send(fd, sig) if sig == 0 else refuse()'), '["error ENOSYS"]'],
    [refuse('EPERM', 'signal.pidfd_send_signal = lambda fd, sig: send(fd, sig) if sig == 0 else refuse()'), '["error EPERM"]'],
    // pidfd_open refuses every process but the helper.
    [refuse('EPERM', 'os.pidfd_open = lambda pid: open_(pid) if pid == os.getpid() else refuse()'), '["error EPERM"]'],
    // Either refuses everything.
    [refuse('ENOSYS', 'os.pidfd_open = refuse'), 'null'],
    [refuse('EPERM', 'signal.pidfd_send_signal = refuse'), 'null'],
  ]) {
    const outputs = [];
    const fake = load('processes.cjs', {
      'node:child_process': {
        ...real,
        spawnSync: (command, args, options) => {
          if (command !== 'python3') return real.spawnSync(command, args, options);
          const helper = real.spawnSync(command, [args[0], args[1], `${code}\n${args[2]}`, args[3]], options);
          outputs.push(helper.stdout.trim());
          return helper;
        },
      },
    });
    const { child, exited, start } = await ownProcess('sleep', ['30']);
    try {
      assert.deepEqual(fake.signalProcesses([{ pid: child.pid, start }], 'SIGTERM'), ['sent'], code);
      assert.equal(await exited, 'SIGTERM');
      assert.deepEqual(outputs, [output], code);
    } finally {
      child.kill('SIGKILL');
    }
  }
});

// A command, in Python, whose main thread exits at once while another thread
// runs on, for the tests here and of the Remote-SSH test's discoveries (see
// support/threads.cjs).
const { THREAD_COMMAND, THREAD_SKIP, runs } = require('./support/threads.cjs');

test('a process whose main thread has exited while its other threads run gets signals, through a pidfd or after a check (real processes)', { skip: THREAD_SKIP }, async () => {
  const real = require('node:child_process');
  const unchecked = load('processes.cjs', {
    'node:child_process': { ...real, spawnSync: () => ({ error: Object.assign(new Error('spawnSync python3 ENOENT'), { code: 'ENOENT' }) }) },
  });
  for (const fake of pidfdSkip ? [unchecked] : [processes, unchecked]) {
    const { child, exited, start } = await ownProcess('python3', ['-I', '-c', THREAD_COMMAND]);
    try {
      const info = await until(() => { const now = processes.processInfo(child.pid); return now?.state === 'Z' ? now : undefined; }, 5000);
      assert.equal(info?.threads, 2);
      assert.deepEqual(fake.signalProcesses([{ pid: child.pid, start }], 'SIGKILL'), ['sent']);
      assert.equal(await exited, 'SIGKILL');
    } finally {
      child.kill('SIGKILL');
    }
  }
  assert.equal(unchecked.pidfdSupport(), 'python3 is not on PATH');
});

test('a command in a group gets the runner\'s own standard streams where they are inherited (real processes)', async () => {
  const runner = spawn(process.execPath, ['-e', `
    const { runGroup } = require(${JSON.stringify(path.join(__dirname, 'e2e/run.cjs'))});
    runGroup('sh', ['-c', 'echo out; echo err >&2; read line; echo "got $line"; exit 4'], {
      env: process.env, stdin: 'inherit', signal: new AbortController().signal,
    }).then((result) => console.log(JSON.stringify(result)));
  `], { stdio: ['pipe', 'pipe', 'pipe'] });
  runner.stdin.end('typed\n');
  const output = { stdout: '', stderr: '' };
  runner.stdout.on('data', (data) => { output.stdout += data; });
  runner.stderr.on('data', (data) => { output.stderr += data; });
  await new Promise((resolve) => runner.on('close', resolve));
  assert.deepEqual(output.stdout.trim().split('\n'), ['out', 'got typed', '{"code":4,"signal":null}']);
  assert.equal(output.stderr, 'err\n');
});

test('the keeper stops its group when the runner goes without releasing it (real processes)', async () => {
  // A runner that starts a group with a command that leaves a process
  // running, prints the group's processes, and waits; the test then kills it.
  const runner = spawn(process.execPath, ['-e', `
    const { startGroup } = require(${JSON.stringify(path.join(__dirname, 'e2e/processes.cjs'))});
    const group = startGroup('sh', ['-c', 'sleep 30 & exec sleep 30'], { env: process.env, stdio: ['ignore', 'ignore', 'ignore'], onExit() {} });
    const report = () => {
      const found = group.look()?.processes ?? [];
      if (found.length < 2) return setTimeout(report, 50);
      console.log(JSON.stringify({ keeper: group.pid, found }));
    };
    report();
    setInterval(() => {}, 1000);
  `], { stdio: ['ignore', 'pipe', 'inherit'] });
  const { keeper, found } = await new Promise((resolve) => {
    let text = '';
    runner.stdout.on('data', (data) => {
      text += data;
      if (text.includes('\n')) resolve(JSON.parse(text));
    });
  });
  // One read per check: a process can exit between two reads.
  const running = (p) => { const info = processes.processInfo(p.pid); return info?.start === p.start && info.state !== 'Z'; };
  const keeperInfo = { pid: keeper, start: processes.processInfo(keeper).start };
  try {
    runner.kill('SIGKILL');
    for (const deadline = Date.now() + 3000; found.some(running) && Date.now() < deadline;) await new Promise((resolve) => setTimeout(resolve, 50));
    assert.deepEqual(found.filter(running), []);
    // The keeper follows with the group's SIGKILL, five seconds later.
    for (const deadline = Date.now() + 8000; running(keeperInfo) && Date.now() < deadline;) await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(running(keeperInfo), false);
  } finally {
    processes.signalProcesses([...found, keeperInfo], 'SIGKILL');
  }
});

test('the keeper sends its group the signals that the runner asks for (real processes)', async () => {
  // The command and its sleep ignore SIGTERM. The runner's own kill() and the
  // Python helpers that it starts are recorded.
  const attempted = [];
  const helpers = [];
  const real = require('node:child_process');
  const fake = load('processes.cjs', {
    'node:child_process': {
      ...real,
      spawnSync: (command, args, options) => {
        if (command === 'python3') helpers.push(args[2]);
        return real.spawnSync(command, args, options);
      },
    },
  }, {
    kill: (target, name) => {
      attempted.push([target, name]);
      process.kill(target, name);
    },
  });
  const wait = async (done) => {
    for (const deadline = Date.now() + 5000; !done() && Date.now() < deadline;) await new Promise((resolve) => setTimeout(resolve, 20));
    assert.ok(done());
  };
  let exited;
  const group = fake.startGroup('sh', ['-c', 'trap "" TERM; sleep 30 & wait'], {
    env: process.env, stdio: ['ignore', 'ignore', 'ignore'], onExit: (result) => { exited = result; },
  });
  let found = [];
  try {
    await wait(() => (found = group.look()?.processes ?? []).length === 2);
    group.signal('SIGTERM');
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(group.look().processes.length, 2);
    assert.deepEqual(helpers.filter((helper) => helper === processes.PIDFD_SIGNAL), []);
    assert.deepEqual(new Set(helpers), new Set(process.platform === 'darwin'
      ? [processes.DARWIN_PROCESS_INFO, processes.DARWIN_GROUP_PIDS] : []));
    // The keeper ends with its group, before it can say that it sent SIGKILL,
    // so the runner also sends it to the processes that it saw there, which
    // have gone.
    group.signal('SIGKILL');
    await wait(() => exited !== undefined);
    assert.deepEqual([exited.code, exited.signal], [null, 'SIGKILL']);
    await wait(() => found.every((p) => processes.processInfo(p.pid)?.start !== p.start));
    assert.deepEqual(attempted.filter(([target]) => target < 0), []);
    await group.release();
  } finally {
    processes.signalProcesses(found, 'SIGKILL');
  }
});

test('both runners stop a group whose keeper has been stopped, keeper and all, within the grace period (real processes)', async () => {
  // Something sends the keeper SIGSTOP, so it can't answer the runner, and
  // then the runner is interrupted. The runner's own kill() is recorded.
  const stop = async (file, name) => {
    const attempted = [];
    let group;
    const fake = load('processes.cjs', {}, {
      kill: (target, signal) => {
        attempted.push([target, signal]);
        process.kill(target, signal);
      },
    });
    const runner = load(file, { [name]: { ...fake, startGroup: (...args) => (group = fake.startGroup(...args)) } }, {}, { setInterval, clearInterval });
    const controller = new AbortController();
    let result;
    const running = runner.runGroup('sleep', ['30'], { env: process.env, stdin: 'ignore', timeout: 60000, signal: controller.signal })
      .then((value) => { result = value; });
    const wait = async (done, ms) => {
      for (const deadline = Date.now() + ms; !done() && Date.now() < deadline;) await new Promise((resolve) => setTimeout(resolve, 20));
      return done();
    };
    const alive = (p) => { const info = processes.processInfo(p.pid); return info?.start === p.start && info.state !== 'Z'; };
    let found = [];
    let keeper;
    try {
      assert.ok(await wait(() => (found = processes.processTable(group.pid).processes.filter((p) => p.group === group.pid && p.pid !== group.pid)).length > 0, 5000), file);
      keeper = processes.processInfo(group.pid);
      processes.signalProcesses([keeper], 'SIGSTOP');
      assert.ok(await wait(() => processes.processInfo(keeper.pid)?.state === 'T', 2000), file);
      const at = Date.now();
      controller.abort();
      // SIGKILL follows SIGTERM after five seconds. The run's own timers, not
      // this one, keep the test going while it runs.
      await Promise.race([running, new Promise((resolve) => setTimeout(resolve, 8000).unref())]);
      assert.notEqual(result, undefined, `${file} hadn't finished ${Date.now() - at} ms after the interrupt`);
      assert.deepEqual([result.stopped, result.signal], ['was interrupted', 'SIGKILL'], file);
      assert.ok(await wait(() => ![keeper, ...found].some(alive), 1000), file);
      assert.deepEqual(attempted.filter(([target]) => target < 0), [], file);
    } finally {
      processes.signalProcesses([...found, keeper].filter(Boolean), 'SIGKILL');
      await running;
    }
  };
  const outcomes = await Promise.allSettled([stop('run.cjs', './processes.cjs'), stop('remote-ssh/run.cjs', '../processes.cjs')]);
  assert.deepEqual(outcomes.flatMap((outcome) => (outcome.status === 'rejected' ? [outcome.reason.message] : [])), []);
});

// A command, in Python, for a runner's group: it starts a helper, which starts
// a child and then leaves the group, as a daemon might, and never reaps the
// child, which stays there; with the argument linger, it also starts a process
// that stays in the group and ignores SIGTERM. Once the helper has given the
// child's PID, the command writes each process's PID, as JSON, to pids in
// directory, its first argument, and exits. The child exits once directory has
// exit, and the helper reaps it and exits once directory has reap. None of them
// holds the runner's output, or waits for a file longer than 30 seconds, and
// the lingering process ends after 30 seconds.
const ZOMBIE_COMMAND = `
import json, os, signal, sys, time
directory, linger = sys.argv[1], sys.argv[2] == 'linger'
def wait(name):
    until = time.monotonic() + 30
    while not os.path.exists(os.path.join(directory, name)):
        if time.monotonic() > until:
            os._exit(1)
        time.sleep(0.01)
def write(name, value):
    with open(os.path.join(directory, name + '.tmp'), 'w') as file:
        json.dump(value, file)
    os.rename(os.path.join(directory, name + '.tmp'), os.path.join(directory, name))
def quiet():
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
pids = {'helper': os.fork()}
if pids['helper'] == 0:
    quiet()
    child = os.fork()
    if child == 0:
        wait('exit')
        os._exit(0)
    os.setpgid(0, 0)
    write('child', child)
    wait('reap')
    os.waitpid(child, 0)
    os._exit(0)
if linger:
    pids['linger'] = os.fork()
    if pids['linger'] == 0:
        quiet()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.alarm(30)
        while True:
            signal.pause()
wait('child')
with open(os.path.join(directory, 'child')) as file:
    pids['child'] = json.load(file)
write('pids', pids)
`;

// Waits up to ms milliseconds for done() to return a truthy value, and returns
// its last value.
async function until(done, ms) {
  for (const deadline = Date.now() + ms; ; await new Promise((resolve) => setTimeout(resolve, 20))) {
    const value = done();
    if (value || Date.now() > deadline) return value;
  }
}

// Runs ZOMBIE_COMMAND, with linger or not, under file's runGroup. Once a look
// of the runner's has found the helper's child alive in the group, so that the
// runner remembers it, the child exits, and stays a zombie there; with linger,
// the run is then interrupted. Checks that the run finishes all the same,
// within 3 seconds, or with linger within 8, which covers the 5 seconds before
// SIGKILL, and leaves the zombie and the helper as they were.
async function zombieRun(file, linger) {
  const directory = fs.mkdtempSync(path.join(__dirname, '.processes-'));
  const mark = (name) => fs.writeFileSync(path.join(directory, name), '');
  // The processes that the runner's looks have found alive, by PID and start
  // time; and the keeper.
  const found = new Set();
  let keeper;
  const fake = {
    ...processes,
    startGroup: (...args) => {
      const group = processes.startGroup(...args);
      keeper = processes.processInfo(group.pid);
      const look = group.look;
      group.look = () => {
        const result = look();
        for (const p of result?.processes ?? []) found.add(`${p.pid} ${p.start}`);
        return result;
      };
      return group;
    },
  };
  const runner = load(file, { [file === 'run.cjs' ? './processes.cjs' : '../processes.cjs']: fake }, {}, { setInterval, clearInterval });
  const controller = new AbortController();
  let result;
  const running = runner.runGroup('python3', ['-I', '-c', ZOMBIE_COMMAND, directory, linger ? 'linger' : 'alone'], {
    env: process.env, stdin: 'ignore', timeout: 60000, signal: controller.signal,
  }).then((value) => { result = value; });
  // p's state, while it is still that process.
  const state = (p) => {
    const info = processes.processInfo(p.pid);
    return info?.start === p.start ? info.state : undefined;
  };
  const alive = (p) => ![undefined, 'Z'].includes(state(p));
  let started = [];
  try {
    const pids = await until(() => {
      try {
        return JSON.parse(fs.readFileSync(path.join(directory, 'pids'), 'utf8'));
      } catch {
        return undefined;
      }
    }, 5000);
    assert.notEqual(pids, undefined, `${file}: the command didn't start its processes`);
    const { helper, child, linger: lingering } = Object.fromEntries(Object.entries(pids).map(([name, pid]) => [name, processes.processInfo(pid)]));
    started = [helper, child, lingering].filter(Boolean);
    assert.ok(await until(() => found.has(`${child.pid} ${child.start}`), 5000), `${file}: no look found the child`);
    mark('exit');
    assert.ok(await until(() => state(child) === 'Z', 5000), `${file}: the child didn't exit`);
    const at = Date.now();
    if (linger) controller.abort();
    await Promise.race([running, new Promise((resolve) => setTimeout(resolve, linger ? 8000 : 3000))]);
    assert.notEqual(result, undefined, `${file} hadn't finished ${Date.now() - at} ms after ${linger ? 'the interrupt' : 'the child exited'}`);
    assert.deepEqual([result.code, result.stopped], [0, linger ? 'was interrupted' : undefined], file);
    // The keeper has gone, and so has the lingering process, which ignores
    // SIGTERM; the child is still a zombie, which the helper hasn't reaped.
    assert.deepEqual([keeper, lingering].filter((p) => p !== undefined && alive(p)), [], file);
    assert.deepEqual([state(child), alive(helper)], ['Z', true], file);
  } finally {
    mark('reap');
    controller.abort();
    processes.signalProcesses(started, 'SIGKILL');
    await running;
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('both runners finish when all that is left in the group is a zombie that its parent never reaps (real processes)', { skip: pythonSkip }, async () => {
  const outcomes = await Promise.allSettled(['run.cjs', 'remote-ssh/run.cjs'].map((file) => zombieRun(file, false)));
  assert.deepEqual(outcomes.flatMap((outcome) => (outcome.status === 'rejected' ? [outcome.reason.message] : [])), []);
});

test('both runners stop an interrupted group within the grace period when it holds a zombie that its parent never reaps (real processes)', { skip: pythonSkip }, async () => {
  const outcomes = await Promise.allSettled(['run.cjs', 'remote-ssh/run.cjs'].map((file) => zombieRun(file, true)));
  assert.deepEqual(outcomes.flatMap((outcome) => (outcome.status === 'rejected' ? [outcome.reason.message] : [])), []);
});

// Runs THREAD_COMMAND under file's runGroup. Once the command's main thread
// has exited, something sends the keeper SIGSTOP, so that it can't answer the
// runner, and then the run is interrupted. Checks that the run finishes within
// 8 seconds, which covers the 5 seconds before SIGKILL, and leaves neither the
// command nor the keeper running.
async function threadRun(file) {
  let group;
  const fake = { ...processes, startGroup: (...args) => (group = processes.startGroup(...args)) };
  const runner = load(file, { [file === 'run.cjs' ? './processes.cjs' : '../processes.cjs']: fake }, {}, { setInterval, clearInterval });
  const controller = new AbortController();
  let result;
  const running = runner.runGroup('python3', ['-I', '-c', THREAD_COMMAND], {
    env: process.env, stdin: 'ignore', timeout: 60000, signal: controller.signal,
  }).then((value) => { result = value; });
  const keeper = processes.processInfo(group.pid);
  let command;
  try {
    command = await until(() => processes.processTable(group.pid).processes
      .find((p) => p.group === keeper.pid && p.pid !== keeper.pid && p.state === 'Z' && p.threads > 1), 5000);
    assert.notEqual(command, undefined, `${file}: the command's main thread didn't exit`);
    processes.signalProcesses([keeper], 'SIGSTOP');
    assert.ok(await until(() => processes.processInfo(keeper.pid)?.state === 'T', 2000), `${file}: the keeper didn't stop`);
    const at = Date.now();
    controller.abort();
    // The run's own timers, not these, keep the test going while it runs.
    await Promise.race([running, new Promise((resolve) => setTimeout(resolve, 8000).unref())]);
    assert.notEqual(result, undefined, `${file} hadn't finished ${Date.now() - at} ms after the interrupt`);
    assert.deepEqual([result.stopped, result.signal], ['was interrupted', 'SIGKILL'], file);
    assert.ok(await until(() => ![command, keeper].some(runs), 1000), `${file} left the command or the keeper running`);
  } finally {
    // Not through processes.cjs, whose signals may be what failed.
    for (const p of [command, keeper]) if (p !== undefined && runs(p)) process.kill(p.pid, 'SIGKILL');
    controller.abort();
    await Promise.race([running, new Promise((resolve) => setTimeout(resolve, 10000).unref())]);
  }
}

test('both runners stop a group whose keeper has been stopped when it holds a process whose main thread has exited (real processes)', { skip: THREAD_SKIP, timeout: 30000 }, async () => {
  const outcomes = await Promise.allSettled(['run.cjs', 'remote-ssh/run.cjs'].map(threadRun));
  assert.deepEqual(outcomes.flatMap((outcome) => (outcome.status === 'rejected' ? [outcome.reason.message] : [])), []);
});

// A runner of its own, a Node.js program whose argument is a runner's file,
// such as run.cjs, that runs `sleep 30` under that file's runGroup, on a fake
// clock. Its signals to single processes reach none, as if each were in an
// uninterruptible sleep. Once the command runs, something sends the keeper
// SIGSTOP, so that it can't answer the runner or signal its group, and the run
// is interrupted. The program prints, a line of JSON each, the keeper and the
// command, and the result once runGroup has given up on the group; it should
// then exit, though the keeper and the command, which hold every pipe that
// the runner had, run on.
const ABANDONED_RUN = `
const real = require('node:child_process');
const { fakeClock, load } = require(${JSON.stringify(path.join(__dirname, 'support/fakes.cjs'))});
const processes = require(${JSON.stringify(path.join(__dirname, 'e2e/processes.cjs'))});
const file = process.argv[1];
const unsignaled = load('processes.cjs', {
  'node:child_process': { ...real, spawnSync: (command, ...rest) => (command === 'python3'
    ? { error: Object.assign(new Error('spawnSync python3 ENOENT'), { code: 'ENOENT' }) } : real.spawnSync(command, ...rest)) },
}, { kill() {} });
let group;
const fake = { ...unsignaled, startGroup: (...args) => (group = unsignaled.startGroup(...args)) };
const clock = fakeClock();
const runner = load(file, { [file === 'run.cjs' ? './processes.cjs' : '../processes.cjs']: fake }, {}, clock.globals);
const controller = new AbortController();
const running = runner.runGroup('sleep', ['30'], { env: process.env, stdin: 'ignore', timeout: 600000, signal: controller.signal });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
(async () => {
  let command;
  while ((command = processes.processTable(group.pid).processes.find((p) => p.group === group.pid && p.pid !== group.pid)) === undefined) await pause(20);
  const keeper = processes.processInfo(group.pid);
  processes.signalProcesses([keeper], 'SIGSTOP');
  while (processes.processInfo(keeper.pid)?.state !== 'T') await pause(20);
  console.log(JSON.stringify({ keeper, command }));
  controller.abort();
  // The keeper doesn't answer the SIGTERM within its second; SIGKILL five
  // seconds after SIGTERM, and 30 seconds after that, the runner gives up.
  await pause(1200);
  // Each poll reads real processes. Model delayed turns at the deadlines,
  // rather than replaying 700 ps scans while the real sleep can expire.
  clock.advance(5100, { coalesce: true });
  clock.advance(30001, { coalesce: true });
  console.log(JSON.stringify(await running));
})();
`;

test('both runners exit once they give up on a group, though its keeper and its command, which SIGKILL doesn\'t end, hold every pipe (real processes)', async () => {
  const abandoned = async (file) => {
    const runner = spawn(process.execPath, ['-e', ABANDONED_RUN, file], { stdio: ['ignore', 'pipe', 'pipe'] });
    let exit;
    runner.on('exit', (code, signal) => { exit = { code, signal }; });
    let output = '';
    let errors = '';
    runner.stdout.on('data', (data) => { output += data; });
    runner.stderr.on('data', (data) => { errors += data; });
    // The nth line of the runner's output, once it has printed all of it.
    const line = (n) => {
      const lines = output.split('\n');
      return lines.length > n + 1 ? JSON.parse(lines[n]) : undefined;
    };
    let started;
    try {
      started = await until(() => line(0), 10000);
      assert.notEqual(started, undefined, `${file}: the command didn't start: ${errors}`);
      const result = await until(() => line(1) ?? exit, 10000);
      assert.equal(result?.stopped, `was interrupted, and hadn't ended 30 seconds after SIGKILL, nor had its keeper (pid ${started.keeper.pid})`, `${file}: ${errors}`);
      assert.deepEqual(await until(() => exit, 3000), { code: 0, signal: null }, `${file} didn't exit after its result: ${errors}`);
      assert.equal(processes.processInfo(started.keeper.pid)?.state, 'T', `${file}: the keeper didn't stay`);
      assert.ok(runs(started.command), `${file}: the command didn't stay`);
    } finally {
      if (exit === undefined) runner.kill('SIGKILL');
      if (started !== undefined) processes.signalProcesses([started.keeper, started.command], 'SIGKILL');
      await until(() => exit, 5000);
    }
  };
  const outcomes = await Promise.allSettled(['run.cjs', 'remote-ssh/run.cjs'].map(abandoned));
  assert.deepEqual(outcomes.flatMap((outcome) => (outcome.status === 'rejected' ? [outcome.reason.message] : [])), []);
});

test('a group whose keeper Node.js has reaped is never signaled by its number, even before the keeper\'s exit event (real processes)', async (t) => {
  // Two keepers exit together. Node.js reaps both before it runs the first
  // one's exit listeners, so the second's ChildProcess still looks unreaped
  // while its group's number is already free. The runner's own kill() is
  // recorded and forwarded only to single processes.
  const keepers = [];
  const attempted = [];
  const real = require('node:child_process');
  const fake = load('processes.cjs', {
    'node:child_process': { ...real, spawn: (...args) => keepers[keepers.push(real.spawn(...args)) - 1] },
  }, {
    kill: (target, name) => {
      attempted.push([target, name]);
      if (target > 0) process.kill(target, name);
    },
  });
  const groups = [0, 1].map(() => new Promise((resolve) => {
    const group = fake.startGroup('true', [], { env: process.env, stdio: ['ignore', 'ignore', 'ignore'], onExit: () => resolve(group) });
  }));
  const [, second] = await Promise.all(groups);
  const closed = keepers.map((keeper) => new Promise((resolve) => keeper.stdout.on('close', resolve)));
  const batch = new Promise((resolve) => keepers[0].on('exit', () => {
    resolve({ exitCode: keepers[1].exitCode, signalCode: keepers[1].signalCode, present: processes.processInfo(second.pid) !== undefined });
    second.signal('SIGTERM');
  }));
  for (const keeper of keepers) process.kill(keeper.pid, 'SIGKILL');
  // Both have exited before the event loop runs again.
  for (const until = Date.now() + 100; Date.now() < until;);
  const observed = await batch;
  await Promise.all(closed);
  await new Promise((resolve) => setImmediate(resolve));
  if (observed.exitCode !== null || observed.present) t.diagnostic(`Node.js didn't reap the keepers together here: ${JSON.stringify(observed)}`);
  assert.deepEqual(attempted.filter(([target]) => target < 0), []);
});

// run.cjs's runGroup, as remote-ssh.cjs's runGroup does it for
// remote-ssh/run.cjs: fake processes, by default from a fake /proc, or from
// ps with platform 'darwin', and a fake clock and group (see fakeGroup), whose
// keeper is PID 50.
function localRunGroup(table, { platform = 'linux', pidfd = false, servers = [], file = 'run.cjs', proc, ps = fakePs(table), darwinPids, darwinInfo } = {}) {
  const { keeper, kill, deliver, signals } = fakeGroup(table);
  const clock = fakeClock();
  const runner = load(file, {
    [file === 'run.cjs' ? './processes.cjs' : '../processes.cjs']: fakeProcesses(table, { keeper, kill, deliver, pidfd, signals, platform, ps, proc, darwinPids, darwinInfo, globals: clock.globals }),
    'node:child_process': { spawnSync: () => ({ status: 0, stdout: [...table].map(([pid, p]) => `${pid} ${p.group} ${p.args ?? 'x'}\n`).join('') }) },
  }, { pid: 1, kill, platform }, clock.globals);
  const controller = new AbortController();
  let result;
  runner.runGroup('code', [], { env: {}, stdin: 'ignore', timeout: 60000, signal: controller.signal, servers }).then((value) => { result = value; });
  return {
    signals, keeper, timers: clock.timers, advance: clock.advance, interrupt: () => controller.abort(),
    result: () => new Promise((resolve) => setImmediate(() => resolve(result))),
  };
}

for (const file of ['run.cjs', 'remote-ssh/run.cjs']) {
  for (const code of ['EACCES', 'EPERM']) {
    test(`${file} completes successfully with a foreign stat entry returning ${code}`, async () => {
      const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }], [99, { start: '200', group: 99 }]]);
      const proc = fakeProc(table);
      const read = proc.readFileSync;
      proc.readFileSync = (name) => {
        if (name === '/proc/99/stat') throw Object.assign(new Error(code), { code });
        return read(name);
      };
      const run = localRunGroup(table, { file, proc });
      run.keeper.command(51);
      table.delete(51);
      run.keeper.exit(0, null);
      run.keeper.close();
      const result = await run.result();
      assert.equal(result?.code, 0);
      assert.equal(result.stopped, undefined);
      assert.equal(run.keeper.released, 'release\n');
      assert.deepEqual(run.signals, []);
      assert.equal(table.has(99), true);
    });
  }

  for (const state of ['ENOENT', 'ESRCH', 'Z']) {
    test(`${file} rescans when a listed helper forks and becomes ${state} before its stat is read`, async () => {
      const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }]]);
      const proc = fakeProc(table);
      let racing = false;
      const read = proc.readFileSync;
      proc.readFileSync = (name) => {
        if (racing && name === '/proc/52/stat') {
          racing = false;
          table.set(53, { start: '120', group: 50, on: () => table.delete(53) });
          if (state === 'Z') table.get(52).state = 'Z';
          else {
            table.delete(52);
            if (state === 'ESRCH') throw Object.assign(new Error(state), { code: state });
          }
        }
        return read(name);
      };
      const run = localRunGroup(table, { file, proc });
      run.keeper.command(51);
      table.set(52, { start: '110', group: 50 });
      table.delete(51);
      racing = true;
      run.keeper.exit(0, null);
      run.keeper.close();
      assert.equal(run.keeper.released, undefined);
      run.advance(50);
      assert.equal(await run.result(), undefined);
      table.delete(52);
      run.interrupt();
      run.advance(50);
      assert.equal((await run.result()).stopped, 'was interrupted');
      assert.deepEqual(run.signals, [['keeper', 'SIGTERM']]);
      assert.equal(table.has(53), false);
    });
  }

  test(`${file} rescans when a macOS helper forks and becomes a zombie during a ps listing, and finds its child`, async () => {
    const start = 'Thu Oct 23 17:13:01 2026';
    const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
    let racing = false;
    // The kernel has listed 52's PID; 52 starts 53, which the listing misses,
    // and exits before ps looks it up.
    const ps = fakePs(table, (pid) => {
      if (racing && pid === 52) {
        racing = false;
        table.get(52).state = 'Z';
        table.set(53, { start, group: 50 });
      }
    });
    const run = localRunGroup(table, { file, platform: 'darwin', ps });
    run.keeper.command(51);
    table.delete(51);
    table.set(52, { start, group: 50 });
    racing = true;
    run.keeper.exit(0, null);
    run.keeper.close();
    assert.equal(run.keeper.released, undefined);
    run.advance(50);
    assert.equal(await run.result(), undefined);
    table.delete(52);
    table.delete(53);
    run.advance(50);
    run.advance(50);
    assert.equal((await run.result()).code, 0);
    assert.deepEqual(run.signals, []);
  });

  for (const native of [false, true]) {
    test(`${file} finds and stops the child of a macOS helper that forks and is reaped during a ps listing (native: ${native})`, async () => {
      // The kernel has listed 52's PID; 52 starts 53, which the listing misses, and is reaped
      // before ps looks it up, so ps leaves it out without saying so. The group
      // looks empty for one scan while 53 runs.
      const start = 'Thu Oct 23 17:13:01 2026';
      const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
      let racing = false;
      const ps = fakePs(table, (pid) => {
        if (racing && pid === 52) {
          racing = false;
          table.delete(52);
          table.set(53, { start, group: 50 });
        }
      });
      const run = localRunGroup(table, { file, platform: 'darwin', ps,
        darwinPids: native ? () => [...table.keys()] : undefined, darwinInfo: native ? fakeDarwinInfo(table) : undefined });
      run.keeper.command(51);
      table.delete(51);
      table.set(52, { start, group: 50 });
      racing = true;
      run.keeper.exit(0, null);
      run.keeper.close();
      assert.equal(await run.result(), undefined);
      assert.equal(run.keeper.released, undefined);
      run.advance(49);
      assert.equal(await run.result(), undefined);
      run.advance(1);
      assert.equal(await run.result(), undefined);
      run.interrupt();
      run.advance(5100);
      run.advance(50);
      assert.equal((await run.result()).stopped, 'was interrupted');
      assert.equal(table.has(53), false);
      assert.deepEqual(run.signals, [['keeper', 'SIGTERM'], ['keeper', 'SIGKILL']]);
    });

  }

  test(`${file} counts a zombie as exited once a later look finds it still a zombie`, async () => {
    for (const platform of ['linux', 'darwin']) {
      const start = platform === 'linux' ? '100' : 'Thu Oct 23 17:13:01 2026';
      // 52 and 53 are in the group, and their parents have left it.
      const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }], [52, { start, ppid: 60, group: 50 }], [53, { start, ppid: 61, group: 50 }]]);
      const run = localRunGroup(table, { file, platform });
      run.keeper.command(51);
      // They exit as VS Code does. The first look to find them zombies can't
      // tell whether they started a process, which it missed, before they did.
      table.delete(51);
      table.get(52).state = 'Z';
      table.get(53).state = 'Z';
      run.keeper.exit(0, null);
      run.keeper.close();
      assert.equal(run.keeper.released, undefined, platform);
      // 52's parent reaps it before the next look; 53's never does.
      table.delete(52);
      run.advance(platform === 'darwin' ? 100 : 50);
      const result = await run.result();
      assert.deepEqual([result?.code, result?.stopped], [0, undefined], platform);
      assert.equal(run.keeper.released, 'release\n', platform);
      assert.deepEqual(run.signals, [], platform);
      assert.equal(table.get(53).state, 'Z', platform);
    }
  });

  test(`${file} waits for a process whose main thread has exited until its other threads have`, async () => {
    // Linux lists the process as a zombie meanwhile.
    const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }], [52, { start: '110', ppid: 60, group: 50 }]]);
    const run = localRunGroup(table, { file });
    run.keeper.command(51);
    table.delete(51);
    Object.assign(table.get(52), { state: 'Z', threads: 2 });
    run.keeper.exit(0, null);
    run.keeper.close();
    run.advance(1000);
    assert.equal(await run.result(), undefined);
    // Its last thread exits, and its parent never reaps it.
    table.get(52).threads = 1;
    run.advance(50);
    assert.equal(await run.result(), undefined);
    run.advance(50);
    assert.equal((await run.result())?.code, 0);
    assert.deepEqual(run.signals, []);
  });

  test(`${file} signals a process whose main thread has exited as any other, once the keeper doesn't answer`, async () => {
    for (const pidfd of [false, true]) {
      // VS Code's main thread has exited before any look, while its other
      // threads, which ignore SIGTERM, run on. The keeper is stopped; then an
      // interrupt.
      const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50, state: 'Z', threads: 2 }]]);
      const run = localRunGroup(table, { file, pidfd });
      run.keeper.command(51);
      run.keeper.close();
      run.keeper.stopped = true;
      table.get(50).state = 'T';
      run.interrupt();
      run.advance(1050);
      const both = (name) => (pidfd ? [['pidfd', name, [[51, '100'], [50, '90']]]] : [[51, name], [50, name]]);
      assert.deepEqual(run.signals, both('SIGTERM'), `pidfd ${pidfd}`);
      run.advance(4100);
      assert.deepEqual(run.signals, [...both('SIGTERM'), ...both('SIGKILL')], `pidfd ${pidfd}`);
      const result = await run.result();
      assert.deepEqual([result?.stopped, result?.code, result?.signal], ['was interrupted', null, 'SIGKILL'], `pidfd ${pidfd}`);
      assert.deepEqual([...table.keys()], []);
    }
  });

  test(`${file} gives up on a group that SIGKILL doesn't end, 30 seconds later`, async () => {
    // VS Code ignores SIGTERM, and a process in its group that the runner's
    // user may not signal stays; then an interrupt.
    const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50, stays: true }]]);
    const run = localRunGroup(table, { file });
    run.keeper.command(51);
    run.keeper.close();
    run.interrupt();
    run.advance(5100);
    assert.deepEqual(run.signals, [['keeper', 'SIGTERM'], ['keeper', 'SIGKILL']]);
    // Node.js reaps the keeper.
    assert.equal(await run.result(), undefined);
    run.advance(29900);
    assert.equal(await run.result(), undefined);
    run.advance(100);
    const result = await run.result();
    assert.deepEqual([result?.stopped, result?.code, result?.signal], ['was interrupted, and hadn\'t ended 30 seconds after SIGKILL', null, 'SIGKILL']);
    // The keeper went before it could say that it had sent SIGKILL, so the
    // runner sent it to the processes that it saw in the group too.
    assert.deepEqual(run.signals.slice(2), [[52, 'SIGKILL']]);
    assert.deepEqual([...table.keys()], [52]);
  });

  test(`${file} gives up on a group whose keeper SIGKILL doesn't end, 30 seconds later, without waiting for the keeper`, async () => {
    // The keeper has stopped answering, as in an uninterruptible sleep, which
    // SIGKILL ends only once it leaves it; then an interrupt.
    const table = new Map([[50, { start: '90', group: 50, state: 'D', stays: true }], [51, { start: '100', group: 50 }]]);
    const run = localRunGroup(table, { file });
    run.keeper.command(51);
    run.keeper.stopped = true;
    run.interrupt();
    run.advance(5100);
    assert.deepEqual(run.signals, [[51, 'SIGTERM'], [50, 'SIGTERM'], [51, 'SIGKILL'], [50, 'SIGKILL']]);
    run.keeper.close();
    run.advance(29900);
    assert.equal(await run.result(), undefined);
    run.advance(100);
    const result = await run.result();
    assert.equal(result?.stopped, 'was interrupted, and hadn\'t ended 30 seconds after SIGKILL, nor had its keeper (pid 50)');
    // The runner neither released the keeper nor sent it another signal, and
    // Node.js waits neither for it nor for any of the pipes.
    assert.equal(run.keeper.released, undefined);
    assert.deepEqual(run.signals.slice(4), []);
    assert.equal(run.keeper.keeper.referenced, false);
    assert.deepEqual(run.keeper.keeper.stdio.map((pipe) => pipe?.destroyed), [true, true, undefined, true, true, true]);
    assert.deepEqual([...table.keys()], [50]);
  });

  test(`${file} observes an interrupt during the keeper release wait`, async () => {
    const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }]]);
    const run = localRunGroup(table, { file });
    run.keeper.command(51);
    run.keeper.stopped = true;
    table.delete(51);
    run.keeper.exit(0, null);
    run.keeper.close();
    assert.equal(run.keeper.released, 'release\n');
    run.advance(500);
    run.interrupt();
    run.advance(499);
    assert.equal(await run.result(), undefined);
    run.advance(1);
    const result = await run.result();
    assert.deepEqual([result.stopped, result.code, result.signal], ['was interrupted', 0, null]);
    assert.deepEqual(run.signals, [[50, 'SIGKILL']]);
  });

  test(`${file} keeps the armed stop bound while releasing a keeper that survives SIGKILL`, async () => {
    const table = new Map([[50, { start: '90', group: 50, state: 'D', stays: true }], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50 }]]);
    const run = localRunGroup(table, { file });
    run.keeper.command(51);
    run.keeper.stopped = true;
    table.delete(51);
    run.keeper.exit(0, null);
    run.keeper.close();
    run.interrupt();
    run.advance(5100);
    assert.equal(table.has(52), false);
    assert.equal(run.keeper.released, 'release\n');
    run.advance(29900);
    assert.equal(await run.result(), undefined);
    run.advance(100);
    const result = await run.result();
    assert.equal(result?.code, 0);
    assert.equal(result?.stopped, "was interrupted, and hadn't ended 30 seconds after SIGKILL, nor had its keeper (pid 50)");
    assert.equal(run.keeper.keeper.referenced, false);
    assert.deepEqual(run.keeper.keeper.stdio.map((pipe) => pipe?.destroyed), [true, true, undefined, true, true, true]);
    assert.equal(run.timers.size, 0);
  });

  test(`${file} bounds a clean exit's keeper release after the watchdog's SIGKILL`, async () => {
    const table = new Map([[50, { start: '90', group: 50, state: 'D', stays: true }], [51, { start: '100', group: 50 }]]);
    const run = localRunGroup(table, { file });
    run.keeper.command(51);
    run.keeper.stopped = true;
    table.delete(51);
    run.keeper.exit(0, null);
    run.keeper.close();
    run.advance(1000);
    assert.deepEqual(run.signals, [[50, 'SIGKILL']]);
    run.advance(29999);
    assert.equal(await run.result(), undefined);
    run.advance(1);
    const result = await run.result();
    assert.equal(result?.code, 0);
    assert.equal(result?.stopped, "its keeper (pid 50) hadn't ended 30 seconds after its release watchdog's SIGKILL");
    assert.equal(run.keeper.keeper.referenced, false);
    assert.deepEqual(run.keeper.keeper.stdio.map((pipe) => pipe?.destroyed), [true, true, undefined, true, true, true]);
    assert.equal(run.timers.size, 0);
  });

  test(`${file} succeeds when the keeper releases before its bound`, async () => {
    const table = new Map([[50, { start: '90', group: 50, stays: true }], [51, { start: '100', group: 50 }]]);
    const run = localRunGroup(table, { file });
    run.keeper.command(51);
    run.keeper.stopped = true;
    table.delete(51);
    run.keeper.exit(0, null);
    run.keeper.close();
    run.advance(30000);
    assert.equal(await run.result(), undefined);
    run.keeper.resume();
    const result = await run.result();
    assert.equal(result?.code, 0);
    assert.equal(result?.stopped, undefined);
    assert.equal(run.keeper.keeper.referenced, true);
    assert.deepEqual(run.keeper.keeper.stdio.map((pipe) => pipe?.destroyed), [false, false, undefined, false, false, false]);
    assert.equal(run.timers.size, 0);
  });

  test(`${file} keeps a clean keeper release successful when an unrelated process disappears during a scan`, async () => {
    const table = new Map([[50, { start: '90', group: 50, stays: true }], [51, { start: '100', group: 50 }]]);
    const proc = fakeProc(table);
    const read = proc.readFileSync;
    let racing = false;
    proc.readFileSync = (name) => {
      if (racing && name === '/proc/99/stat') {
        racing = false;
        table.delete(99);
      }
      return read(name);
    };
    const run = localRunGroup(table, { file, proc });
    run.keeper.command(51);
    run.keeper.stopped = true;
    table.delete(51);
    run.keeper.exit(0, null);
    run.keeper.close();
    assert.equal(run.keeper.released, 'release\n');
    run.advance(10000);
    // An incomplete scan doesn't prove that a member joined the empty group
    // while its keeper was being released.
    table.set(99, { start: '200', group: 99 });
    racing = true;
    run.advance(50);
    run.keeper.resume();
    const result = await run.result();
    assert.equal(result?.code, 0);
    assert.equal(result?.stopped, undefined);
    if (file !== 'run.cjs') assert.equal(result.groupLeft, false);
    assert.equal(run.timers.size, 0);
  });

  for (const interrupted of [false, true]) {
    test(`${file} lets Node exit after ${interrupted ? 'an interrupted' : 'a clean'} command's keeper survives release`, () => {
      const script = `
        const assert = require('node:assert/strict');
        const { fakeGroup, fakeProcesses, load } = require(${JSON.stringify(path.join(__dirname, 'support/fakes.cjs'))});
        const file = process.argv[1];
        const interrupted = process.argv[2] === 'true';
        const table = new Map([[50, { start: '90', group: 50, stays: true }], [51, { start: '100', group: 50 }]]);
        if (interrupted) table.set(52, { start: '110', group: 50 });
        const { keeper, kill, deliver } = fakeGroup(table);
        const globals = {
          Date: { now: () => Date.now() * 100 },
          setTimeout: (fn, ms) => setTimeout(fn, ms / 100), clearTimeout,
          setInterval: (fn, ms) => setInterval(fn, ms / 100), clearInterval,
        };
        const fake = fakeProcesses(table, { keeper, kill, deliver, globals });
        const runner = load(file, { [file === 'run.cjs' ? './processes.cjs' : '../processes.cjs']: fake }, {}, globals);
        const controller = new AbortController();
        runner.runGroup('code', [], { env: {}, stdin: 'ignore', timeout: 600000, signal: controller.signal }).then((result) => {
          assert.equal(result.code, 0);
          assert.ok(result.stopped.includes('keeper (pid 50)'));
          assert.equal(keeper.keeper.referenced, false);
          assert.deepEqual(keeper.keeper.stdio.map((pipe) => pipe?.destroyed), [true, true, undefined, true, true, true]);
          console.log(JSON.stringify(result));
        });
        keeper.command(51);
        keeper.stopped = true;
        table.delete(51);
        keeper.exit(0, null);
        keeper.close();
        if (interrupted) controller.abort();
      `;
      const child = spawnSync(process.execPath, ['-e', script, file, String(interrupted)], { encoding: 'utf8', timeout: 5000 });
      assert.equal(child.error, undefined, child.stderr);
      assert.equal(child.status, 0, child.stderr);
      assert.match(JSON.parse(child.stdout).stopped, /keeper/);
    });
  }
}

test('the Extension Host test has the keeper signal its group, and waits for the group', async () => {
  for (const platform of ['linux', 'darwin']) {
    const start = platform === 'linux' ? '100' : 'Thu Oct 23 17:13:01 2026';
    // VS Code ignores SIGTERM, and a process that it started just before it
    // exits outlives it.
    const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
    table.get(51).on = () => {};
    const run = localRunGroup(table, { platform });
    run.keeper.command(51);
    run.interrupt();
    assert.deepEqual(run.signals, [['keeper', 'SIGTERM']]);
    table.set(52, { start, group: 50, on: () => {} });
    table.delete(51);
    run.keeper.exit(0, null);
    run.advance(4000);
    assert.equal(await run.result(), undefined);
    run.advance(1100);
    assert.deepEqual(run.signals, [['keeper', 'SIGTERM'], ['keeper', 'SIGKILL']], platform);
    await run.result();
    run.advance(100);
    const result = await run.result();
    assert.equal(result.stopped, 'was interrupted');
    assert.equal(result.code, 0);
    assert.deepEqual([...table.keys()], []);
  }
});

test('a keeper that doesn\'t answer in time, as when it has been stopped, gets the group\'s signals with the processes seen there', async () => {
  for (const pidfd of [false, true]) {
    // The keeper is stopped, and VS Code ignores SIGTERM; then an interrupt.
    const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50, on: () => {} }]]);
    const run = localRunGroup(table, { pidfd });
    run.keeper.command(51);
    run.keeper.stopped = true;
    table.get(50).state = 'T';
    run.interrupt();
    run.advance(950);
    assert.deepEqual(run.signals, []);
    // A second after the request, the processes seen there and the keeper,
    // which ignores it, get SIGTERM; SIGKILL follows without a request.
    run.advance(100);
    const both = (name) => (pidfd ? [['pidfd', name, [[51, '100'], [50, '90']]]] : [[51, name], [50, name]]);
    assert.deepEqual(run.signals, both('SIGTERM'));
    run.advance(4100);
    assert.deepEqual(run.signals, [...both('SIGTERM'), ...both('SIGKILL')]);
    const result = await run.result();
    assert.deepEqual([result.stopped, result.code, result.signal], ['was interrupted', null, 'SIGKILL']);
    assert.deepEqual([...table.keys()], []);
  }

  // The keeper sends SIGTERM, and is then stopped, so it doesn't answer
  // SIGKILL by going: a second later, the runner sends it.
  let table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50, on: () => {} }]]);
  let run = localRunGroup(table);
  run.keeper.command(51);
  run.interrupt();
  run.keeper.stopped = true;
  run.advance(5100);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM']]);
  run.advance(950);
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM'], [51, 'SIGKILL'], [50, 'SIGKILL']]);
  assert.equal((await run.result()).signal, 'SIGKILL');

  // VS Code exits, and the keeper says so, but is then stopped, so it doesn't
  // exit once released: a second later, the runner sends it SIGKILL.
  table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }]]);
  run = localRunGroup(table);
  run.keeper.command(51);
  run.keeper.stopped = true;
  table.delete(51);
  run.keeper.exit(0, null);
  assert.equal(run.keeper.released, 'release\n');
  run.advance(950);
  assert.equal(await run.result(), undefined);
  run.advance(100);
  assert.deepEqual(run.signals, [[50, 'SIGKILL']]);
  const result = await run.result();
  assert.deepEqual([result.stopped, result.code, result.signal], [undefined, 0, null]);
});

test('a keeper\'s answer that comes after its second has passed is credited to no request, and later signals still go to the processes seen there', async () => {
  for (const pidfd of [false, true]) {
    // As above, the keeper is stopped, VS Code ignores SIGTERM, and an
    // interrupt's SIGTERM goes to them a second after the request.
    const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50, on: () => {} }]]);
    const run = localRunGroup(table, { pidfd });
    run.keeper.command(51);
    run.keeper.stopped = true;
    table.get(50).state = 'T';
    run.interrupt();
    run.advance(1050);
    const both = (name) => (pidfd ? [['pidfd', name, [[51, '100'], [50, '90']]]] : [[51, name], [50, name]]);
    assert.deepEqual(run.signals, both('SIGTERM'));
    // The keeper resumes, sends its group the SIGTERM that it was asked for,
    // and says so, too late: the runner takes that as an answer to nothing,
    // and still doesn't ask the keeper for SIGKILL, which comes on time.
    table.get(50).state = 'S';
    run.keeper.resume();
    run.advance(3900);
    assert.deepEqual(run.signals, [...both('SIGTERM'), ['keeper', 'SIGTERM']]);
    run.advance(200);
    assert.deepEqual(run.signals, [...both('SIGTERM'), ['keeper', 'SIGTERM'], ...both('SIGKILL')]);
    assert.deepEqual(run.keeper.requests, ['SIGTERM']);
    const result = await run.result();
    assert.deepEqual([result.stopped, result.code, result.signal], ['was interrupted', null, 'SIGKILL']);
    assert.deepEqual([...table.keys()], []);
  }
});

test('without /proc, the keeper is known by its parent as well as by its start time, which ps gives only to the second', async () => {
  for (const ppid of [1, 7]) {
    const start = 'Thu Oct 23 17:13:01 2026';
    const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
    const run = localRunGroup(table, { platform: 'darwin' });
    run.keeper.command(51);
    // The keeper is reaped, and within the same second its PID is given to
    // another process, which makes a group of it with another; VS Code has
    // left the group, and exits.
    table.set(50, { start, ppid, group: 50 });
    table.set(52, { start, ppid, group: 50 });
    table.set(51, { start, group: 51 });
    run.keeper.exit(0, null);
    run.advance(100);
    const result = await run.result();
    // Only another child of the runner, given the keeper's PID within the
    // second in which the keeper started, would pass for the keeper.
    assert.equal(result?.code, ppid === 1 ? undefined : 0, `parent ${ppid}`);
    assert.deepEqual(run.signals, []);
  }
});

test('the Extension Host test waits for its group to empty, then releases the keeper, and stops servers left running', async () => {
  // VS Code exits; its server exits a second later.
  let table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50, args: '/build/virgil-lsp --stdio' }]]);
  let run = localRunGroup(table, { servers: ['/build/virgil-lsp'] });
  run.keeper.command(51);
  table.delete(51);
  run.keeper.exit(0, null);
  run.advance(1000);
  assert.equal(await run.result(), undefined);
  table.delete(52);
  run.advance(100);
  assert.equal((await run.result()).code, 0);
  assert.deepEqual(run.signals, []);
  assert.equal(run.keeper.released, 'release\n');

  // The server stays: after SERVER_EXIT_MS the group is stopped.
  table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50, args: '/build/virgil-lsp --stdio' }]]);
  table.get(52).on = (name) => {
    if (name === 'SIGTERM') table.delete(52);
  };
  run = localRunGroup(table, { servers: ['/build/virgil-lsp'] });
  run.keeper.command(51);
  table.delete(51);
  run.keeper.exit(0, null);
  // The check for servers waits on the clock between its looks.
  for (let i = 0; i < 60; i++) {
    await run.result();
    run.advance(100);
  }
  const result = await run.result();
  assert.deepEqual(run.signals, [['keeper', 'SIGTERM']]);
  assert.match(result.stopped, /^left virgil-lsp \(pid 52\) running 5 seconds after VS Code exited$/);
});

test('the Extension Host test\'s check for servers left running ends once the group is no longer the run\'s', async () => {
  // VS Code exits, and its server runs on in the group.
  const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50, args: '/build/virgil-lsp --stdio' }]]);
  const run = localRunGroup(table, { servers: ['/build/virgil-lsp'] });
  run.keeper.command(51);
  table.delete(51);
  run.keeper.exit(0, null);
  // The check for servers waits on the clock between its looks; the last
  // before its limit, at 5 seconds, finds the server.
  for (let i = 0; i <= 50; i++) {
    await run.result();
    run.advance(100);
  }
  // Just before the check's next look, something kills the keeper, and the
  // server exits. Another process is given the group's number, and makes a
  // group of it with a process that runs the same server.
  table.delete(50);
  run.keeper.reap('SIGKILL');
  table.delete(52);
  table.set(50, { start: '200', group: 50 });
  table.set(53, { start: '210', group: 50, args: '/build/virgil-lsp --stdio' });
  for (let i = 0; i < 10; i++) {
    await run.result();
    run.advance(100);
  }
  const result = await run.result();
  assert.deepEqual([result?.code, result?.stopped], [0, undefined]);
  assert.deepEqual(run.signals, []);
  assert.equal(table.has(53), true);
});

test('once its keeper has gone, the Extension Host test never signals the group\'s number, only the processes it saw there', async () => {
  for (const pidfd of [false, true]) {
    // Something kills the keeper; a process of VS Code's leaves the group;
    // VS Code exits; another process is given the group's number, and makes
    // a group of it with another process.
    const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }], [52, { start: '110', group: 50 }]]);
    const run = localRunGroup(table, { pidfd });
    run.keeper.command(51);
    table.delete(50);
    run.keeper.reap('SIGKILL');
    await run.result();
    table.set(52, { start: '110', group: 52 });
    table.delete(51);
    table.set(50, { start: '200', group: 50 });
    table.set(53, { start: '210', group: 50 });
    run.advance(100);
    const result = await run.result();
    assert.match(result.error.message, /keeper exited with SIGKILL first/);
    run.interrupt();
    assert.deepEqual(run.signals, []);
    assert.equal(table.has(50) && table.has(53), true);
  }
  // While a process that it saw is still in the group, it is waited for, and
  // the processes that it saw are stopped one by one.
  const table = new Map([[50, { start: '90', group: 50 }], [51, { start: '100', group: 50 }]]);
  table.get(51).on = () => {};
  const run = localRunGroup(table);
  run.keeper.command(51);
  table.delete(50);
  run.keeper.reap('SIGKILL');
  await run.result();
  run.interrupt();
  run.advance(5100);
  await run.result();
  run.advance(100);
  assert.deepEqual(run.signals, [[51, 'SIGTERM'], [51, 'SIGKILL']]);
  assert.equal((await run.result()).stopped, 'was interrupted');
});

test('the Extension Host test waits for a process that joins its group just before the command exits (real processes)', async () => {
  const { runGroup } = require('./e2e/run.cjs');
  const started = Date.now();
  const marker = path.join(fs.mkdtempSync(path.join(__dirname, '.processes-')), 'done');
  try {
    const result = await runGroup('sh', ['-c', `(sleep 0.5; touch '${marker}') & exit 3`], {
      env: process.env, stdin: 'ignore', signal: new AbortController().signal,
    });
    assert.equal(result.code, 3);
    assert.ok(Date.now() - started >= 450, `resolved after ${Date.now() - started} ms`);
    assert.ok(fs.existsSync(marker));
  } finally {
    fs.rmSync(path.dirname(marker), { recursive: true, force: true });
  }
});
