// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The test runners' process groups and signals (e2e/processes.cjs and
// e2e/keeper.cjs), and the Extension Host test's runGroup (e2e/run.cjs), and
// with a stopped keeper the Remote-SSH test's too, mostly with a fake /proc
// or ps, clock, and keeper (see support/fakes.cjs); some checks run real
// processes.

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { fakeClock, fakeGroup, fakeProc, fakeProcesses, load } = require('./support/fakes.cjs');

const processes = require('./e2e/processes.cjs');

const pidfdSkip = process.platform !== 'linux' ? 'pidfds are a Linux feature'
  : processes.pidfdSupport() === true ? false : `no pidfds here: ${processes.pidfdSupport()}`;

// ps's output for a fake process table on a system without /proc: each
// process has a start time, as ps prints it, and may have a state, a parent,
// and a group.
function fakePs(table) {
  return (args) => {
    const selected = args[0] === '-p' ? [[Number(args[1]), table.get(Number(args[1]))]].filter(([, p]) => p) : [...table];
    const lines = selected.map(([pid, p]) => [pid, p.ppid ?? 1, p.group ?? 0].map((n) => String(n).padStart(5)).join(' ')
      + ` ${p.state ?? 'Ss'}   ${p.start}`);
    // ps -A always lists ps itself; -p fails for a process that has gone.
    return { status: args[0] === '-p' && lines.length === 0 ? 1 : 0, stdout: lines.map((line) => `${line}\n`).join(''), stderr: '' };
  };
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

test('without /proc, processes and their start times come from ps, and signals follow a check', () => {
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

// Starts a real process that the test owns, and returns it with its start
// time, once it runs.
async function ownProcess(command, args) {
  const child = spawn(command, args, { stdio: 'ignore' });
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
  const running = (p) => processes.processInfo(p.pid)?.start === p.start && processes.processInfo(p.pid).state !== 'Z';
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
  // pidfd helpers that it starts are recorded.
  const attempted = [];
  const helpers = [];
  const real = require('node:child_process');
  const fake = load('processes.cjs', {
    'node:child_process': {
      ...real,
      spawnSync: (command, ...rest) => {
        helpers.push(command);
        return real.spawnSync(command, ...rest);
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
    assert.deepEqual(helpers.filter((command) => command === 'python3'), []);
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
    const alive = (p) => processes.processInfo(p.pid)?.start === p.start && processes.processInfo(p.pid).state !== 'Z';
    let found = [];
    let keeper;
    try {
      assert.ok(await wait(() => (found = processes.processTable().processes.filter((p) => p.group === group.pid && p.pid !== group.pid)).length > 0, 5000), file);
      keeper = processes.processInfo(group.pid);
      processes.signalProcesses([keeper], 'SIGSTOP');
      assert.ok(await wait(() => processes.processInfo(keeper.pid)?.state === 'T', 2000), file);
      const at = Date.now();
      controller.abort();
      // SIGKILL follows SIGTERM after five seconds.
      await Promise.race([running, new Promise((resolve) => setTimeout(resolve, 8000))]);
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
function localRunGroup(table, { platform = 'linux', pidfd = false, servers = [], file = 'run.cjs', proc, ps = fakePs(table) } = {}) {
  const { keeper, kill, deliver, signals } = fakeGroup(table);
  const clock = fakeClock();
  const runner = load(file, {
    [file === 'run.cjs' ? './processes.cjs' : '../processes.cjs']: fakeProcesses(table, { keeper, kill, deliver, pidfd, signals, platform, ps, proc, globals: clock.globals }),
    'node:child_process': { spawnSync: () => ({ status: 0, stdout: [...table].map(([pid, p]) => `${pid} ${p.group} ${p.args ?? 'x'}\n`).join('') }) },
  }, { pid: 1, kill, platform }, clock.globals);
  const controller = new AbortController();
  let result;
  runner.runGroup('code', [], { env: {}, stdin: 'ignore', timeout: 60000, signal: controller.signal, servers }).then((value) => { result = value; });
  return {
    signals, keeper, advance: clock.advance, interrupt: () => controller.abort(),
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

  test(`${file} rescans a macOS snapshot with a zombie helper and finds its surviving child`, async () => {
    const start = 'Thu Oct 23 17:13:01 2026';
    const table = new Map([[50, { start, group: 50 }], [51, { start, group: 50 }]]);
    const snapshot = fakePs(table);
    let racing = false;
    const ps = (args) => {
      const result = snapshot(args);
      if (racing && args[0] === '-A') {
        racing = false;
        table.delete(52);
        table.set(53, { start, group: 50 });
      }
      return result;
    };
    const run = localRunGroup(table, { file, platform: 'darwin', ps });
    run.keeper.command(51);
    table.delete(51);
    table.set(52, { start, group: 50, state: 'Z' });
    racing = true;
    run.keeper.exit(0, null);
    run.keeper.close();
    assert.equal(run.keeper.released, undefined);
    run.advance(50);
    assert.equal(await run.result(), undefined);
    table.delete(53);
    run.advance(50);
    assert.equal((await run.result()).code, 0);
    assert.deepEqual(run.signals, []);
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
