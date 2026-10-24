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
const { fakeClock, fakeGroup, fakeProc, fakeProcesses, load } = require('./support/fakes.cjs');

const processes = require('./e2e/processes.cjs');

const pidfdSkip = process.platform !== 'linux' ? 'pidfds are a Linux feature'
  : processes.pidfdSupport() === true ? false : `no pidfds here: ${processes.pidfdSupport()}`;
const pythonSkip = spawnSync('python3', ['-I', '-c', '']).status === 0 ? false : 'python3 is not on PATH';

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

test('a process whose main thread has exited while its other threads run gets signals after a check, unlike a zombie', () => {
  // Linux lists both as zombies: 11 with another thread still running, and 12
  // with none.
  const table = new Map([[11, { start: '510', state: 'Z', threads: 2 }], [12, { start: '520', state: 'Z' }]]);
  const kills = [];
  const fake = fakeProcesses(table, { kill: (pid, name) => kills.push([pid, name]) });
  assert.deepEqual(fake.signalProcesses([{ pid: 11, start: '510' }, { pid: 12, start: '520' }], 'SIGKILL'), ['sent', 'gone']);
  assert.deepEqual(kills, [[11, 'SIGKILL']]);
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

// A command, in Python, whose main thread exits at once while another thread
// runs on for 30 seconds; it ignores SIGTERM. Linux lists it as a zombie with
// two threads meanwhile.
const THREAD_COMMAND = `
import ctypes, signal, threading, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
threading.Thread(target=time.sleep, args=(30,)).start()
ctypes.CDLL(None).pthread_exit(None)
`;
const threadSkip = process.platform !== 'linux' ? 'only Linux lists a process whose main thread has exited as a zombie' : pythonSkip;

// Whether p, a process given by PID and start time, still runs: it hasn't
// exited, or only its main thread has.
function runs(p) {
  const info = processes.processInfo(p.pid);
  return info?.start === p.start && !(info.state === 'Z' && !(info.threads > 1));
}

test('a process whose main thread has exited while its other threads run gets signals, through a pidfd or after a check (real processes)', { skip: threadSkip }, async () => {
  const real = require('node:child_process');
  const unchecked = load('processes.cjs', {
    'node:child_process': { ...real, spawnSync: () => ({ error: Object.assign(new Error('spawnSync python3 ENOENT'), { code: 'ENOENT' }) }) },
  });
  for (const fake of pidfdSkip ? [unchecked] : [processes, unchecked]) {
    const { child, exited, start } = await ownProcess('python3', ['-I', '-c', THREAD_COMMAND]);
    try {
      const info = await until(() => (processes.processInfo(child.pid)?.state === 'Z' ? processes.processInfo(child.pid) : undefined), 5000);
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
    command = await until(() => processes.processTable().processes
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

test('both runners stop a group whose keeper has been stopped when it holds a process whose main thread has exited (real processes)', { skip: threadSkip, timeout: 30000 }, async () => {
  const outcomes = await Promise.allSettled(['run.cjs', 'remote-ssh/run.cjs'].map(threadRun));
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
    assert.equal((await run.result()).code, 0);
    assert.deepEqual(run.signals, []);
  });

  test(`${file} misses the child of a macOS helper that forks and is reaped during a ps listing`, async () => {
    // A limitation, which clients/vscode/README.md describes: the kernel has
    // listed 52's PID; 52 starts 53, which the listing misses, and is reaped
    // before ps looks it up, so ps leaves it out without saying so. The group
    // looks empty while 53 runs.
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
    const run = localRunGroup(table, { file, platform: 'darwin', ps });
    run.keeper.command(51);
    table.delete(51);
    table.set(52, { start, group: 50 });
    racing = true;
    run.keeper.exit(0, null);
    run.keeper.close();
    assert.equal((await run.result())?.code, 0);
    assert.equal(run.keeper.released, 'release\n');
    assert.deepEqual(run.signals, []);
    assert.equal(table.has(53), true);
  });

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
      run.advance(50);
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
