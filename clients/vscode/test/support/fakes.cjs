// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Fakes for the tests of the VS Code test runners (e2e/): a loader that
// replaces a module's modules, process properties, and globals; a fake /proc;
// a fake clock; and a fake of the process groups' keeper (e2e/keeper.cjs),
// alone or with its group.

const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const vm = require('node:vm');

const e2e = path.join(__dirname, '..', 'e2e');

// Loads file, relative to e2e/, with some modules, process properties, and
// globals replaced.
function load(file, modules = {}, overrides = {}, globals = {}) {
  const filename = path.join(e2e, file);
  const module = { exports: {} };
  const localRequire = createRequire(filename);
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, exports: module.exports, __dirname: path.dirname(filename), Buffer, AbortController, setTimeout, clearTimeout,
    process: Object.create(process, Object.getOwnPropertyDescriptors(overrides)),
    require: (name) => modules[name] ?? localRequire(name),
    ...globals,
  });
  return module.exports;
}

// node:fs with a fake /proc of processes, by PID: each has a start time, and
// may have a state, a parent, a process group, a number of threads, an
// executable, and an environment.
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
      if (name === 'stat') return `${pid} (x) ${p.state ?? 'S'} ${p.ppid ?? 1} ${p.group ?? 0} ${Array(14).fill(0).join(' ')} ${p.threads ?? 1} 0 ${p.start} 0 0`;
      if (name === 'environ') return (p.environ ?? []).join('\0');
      return '';
    },
    readlinkSync: (file) => entry(file)[1].exe ?? '/usr/bin/other',
  };
}

// A fake /proc of one process whose main thread has exited while other
// threads run, for the race between choosing a thread and reading its files
// (see processFiles in e2e/processes.cjs): the threads in vanishing exit once
// their stat has been read, as one does when the discovery has just chosen
// it. tids are the process's thread IDs, the main thread's first; Linux lists
// a process whose main thread has exited as a zombie with its number of
// threads, while each thread has its own state and files. The last thread
// still there has exe, environ, and cmdline. The process's stat keeps its
// thread count, so that a process whose threads have all vanished during the
// scan is still live, as one that starts a thread then is.
function fakeThreadedProc({ pid = 100, start = '12345', tids = ['100', '101', '102'], vanishing = ['101'],
  exe = '/work/python3', environ = 'RUN=1', cmdline = 'python3 -c pass' } = {}) {
  const gone = new Set();
  const missing = (file) => Object.assign(new Error(`ENOENT: ${file}`), { code: 'ENOENT' });
  const line = (id, state, threads) => `${id} (python3) ${state} 1 0 ${Array(14).fill(0).join(' ')} ${threads} 0 ${start} 0 0`;
  const task = (file) => /^\/proc\/\d+\/task\/(\d+)\/(\w+)$/.exec(file);
  return {
    readdirSync: (file) => {
      if (file === '/proc') return [String(pid)];
      if (file === `/proc/${pid}/task`) return [...tids];
      throw missing(file);
    },
    readFileSync: (file) => {
      if (file === `/proc/${pid}/stat`) return line(pid, 'Z', tids.length);
      const match = task(file);
      if (match === null || gone.has(match[1]) || match[1] === tids[0]) throw missing(file);
      if (match[2] === 'stat') {
        if (vanishing.includes(match[1])) gone.add(match[1]);
        return line(match[1], 'S', 1);
      }
      if (match[2] === 'environ') return environ;
      if (match[2] === 'cmdline') return cmdline.split(' ').join('\0');
      throw missing(file);
    },
    readlinkSync: (file) => {
      const match = /^\/proc\/\d+\/task\/(\d+)\/exe$/.exec(file);
      if (match === null || gone.has(match[1]) || match[1] === tids[0]) throw missing(file);
      return exe;
    },
  };
}

// A fake clock: globals for load(), and advance(ms), which runs the timers
// that are due in the next ms milliseconds.
function fakeClock() {
  let now = 0;
  let next = 1;
  const timers = new Map();
  const add = (fn, ms, every) => {
    timers.set(next, { at: now + ms, fn, every });
    return next++;
  };
  const clear = (id) => timers.delete(id);
  return {
    timers,
    globals: {
      Date: { now: () => now },
      setTimeout: (fn, ms) => add(fn, ms), setInterval: (fn, ms) => add(fn, ms, ms), clearTimeout: clear, clearInterval: clear,
    },
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
  };
}

// A fake of keeper.cjs's process, with the PID pid, for spawn to return to
// processes.cjs's startGroup. command(pid), exit(code, signal), and
// fail(code) report the command's start, exit, or failure, as the keeper
// does; data(name, text) and close() are the command's output; reap(signal)
// is Node.js reaping the keeper and running its exit listeners, which it
// does as soon as the keeper is released, before a fake clock can run out
// the keeper's time to exit (see KEEPER_REPLY_MS in processes.cjs). A signal
// that the runner asks for is added to requests, and goes to group(name),
// which returns whether the keeper was there to send it; the keeper then says
// that it sent it, unless it was SIGKILL. A keeper that has been stopped, with
// stopped, does neither, and doesn't exit once released, until resume(), as
// SIGCONT would resume it: it then sends the signals that it was asked for
// meanwhile, in order, says so, and exits if released. spawned holds spawn's
// arguments. The keeper's referenced, and each pipe's destroyed, say whether
// the runner has let Node.js exit without them, with unref() and destroy().
function fakeKeeper(pid) {
  const keeper = Object.assign(new EventEmitter(), { pid, exitCode: null, signalCode: null, referenced: true });
  keeper.unref = () => { keeper.referenced = false; };
  const pipe = () => Object.assign(new EventEmitter(), { destroyed: false, end() {}, destroy() { this.destroyed = true; } });
  // The signals that the keeper was asked for while it was stopped.
  const waiting = [];
  const send = (name) => {
    if (fake.group(name) && name !== 'SIGKILL') line({ signaled: name });
  };
  const fake = {
    keeper,
    released: undefined,
    spawned: undefined,
    stopped: false,
    requests: [],
    group: () => false,
    spawn: (...args) => {
      fake.spawned = args;
      return keeper;
    },
    command: (child) => line({ spawn: child }),
    exit: (code, signal) => line({ exit: [code, signal] }),
    fail: (code) => line({ error: { message: `spawn command ${code}`, code } }),
    data: (name, text) => keeper.stdio[name === 'stdout' ? 4 : 5].emit('data', Buffer.from(text)),
    close: () => {
      keeper.stdio[4].emit('close');
      keeper.stdio[5].emit('close');
    },
    reap: (signal) => {
      if (keeper.exitCode !== null || keeper.signalCode !== null) return;
      stdin.writable = false;
      if (signal) keeper.signalCode = signal;
      else keeper.exitCode = 0;
      keeper.emit('exit', keeper.exitCode, keeper.signalCode);
      keeper.stdout.emit('close');
    },
    resume: () => {
      fake.stopped = false;
      for (const name of waiting.splice(0)) send(name);
      if (fake.released !== undefined) fake.reap(null);
    },
  };
  const stdin = Object.assign(pipe(), {
    writable: true,
    write: (text) => {
      for (const name of String(text).split('\n').filter(Boolean)) {
        fake.requests.push(name);
        if (fake.stopped) waiting.push(name);
        else send(name);
      }
    },
    end: (text) => {
      stdin.writable = false;
      fake.released = String(text);
      if (!fake.stopped) fake.reap(null);
    },
  });
  keeper.stdin = stdin;
  keeper.stdout = pipe();
  keeper.stdio = [stdin, keeper.stdout, null, pipe(), pipe(), pipe()];
  const line = (message) => keeper.stdout.emit('data', Buffer.from(`${JSON.stringify(message)}\n`));
  return fake;
}

// processes.cjs over fake processes (see fakeProc), with a fake keeper for
// startGroup, and kill as process.kill. With pidfd, the pidfd helper "runs":
// signals gets ['pidfd', name, targets] for each call, and deliver(pid, name)
// each signal to a target that is still that process, alive; without it,
// python3 isn't found. platform is process.platform, and ps, if given, gives
// spawnSync's result for ps's arguments; globals, such as a fake clock's, are
// load()'s.
function fakeProcesses(processes, { keeper, kill, pidfd = false, signals = [], deliver = () => {}, platform = 'linux', ps, globals, proc = fakeProc(processes) } = {}) {
  const spawnSync = (command, args) => {
    if (command === 'ps') return ps(args);
    if (!pidfd) return { error: Object.assign(new Error(`spawnSync ${command} ENOENT`), { code: 'ENOENT' }) };
    const [name, targets] = JSON.parse(args.at(-1));
    signals.push(['pidfd', name, targets]);
    const results = targets.map(([pid, start]) => {
      const p = processes.get(pid);
      if (p?.start !== start) return p === undefined ? 'gone' : 'other';
      if (p.state === 'Z' && !(p.threads > 1)) return 'gone';
      deliver(pid, name);
      return 'sent';
    });
    return { status: 0, stdout: JSON.stringify(results) };
  };
  return load('processes.cjs', {
    'node:fs': proc,
    'node:child_process': { spawn: (...args) => keeper.spawn(...args), spawnSync },
  }, { pid: 1, kill, platform }, globals);
}

// A fake keeper, with the PID 50, of a group among fake processes (see
// fakeProc), and kill as process.kill. kill sends a signal to a process, or to
// a group by its negated number, which a runner never should; the keeper sends
// one to its whole group while it is in processes, as the same entry. signals
// gets [target, name] for each kill, and ['keeper', name] for each of the
// keeper's. The keeper ignores every signal but SIGKILL, after which Node.js
// reaps it; other processes go on SIGKILL, unless they have stays, as a
// process that the runner's user may not signal does, and may say, with
// on(name), what happens on others.
function fakeGroup(processes, signals = []) {
  const keeper = fakeKeeper(50);
  const entry = processes.get(50);
  const deliver = (pid, name) => {
    const p = processes.get(pid);
    if (p === undefined || (pid === 50 && name !== 'SIGKILL')) return;
    p.on?.(name);
    if (name !== 'SIGKILL' || p.stays) return;
    processes.delete(pid);
    if (pid === 50) queueMicrotask(() => keeper.reap('SIGKILL'));
  };
  const inGroup = (group) => [...processes].filter(([, p]) => p.group === group).map(([pid]) => pid);
  keeper.group = (name) => {
    if (processes.get(50) !== entry) return false;
    signals.push(['keeper', name]);
    for (const pid of inGroup(50)) deliver(pid, name);
    return true;
  };
  const kill = (target, name) => {
    signals.push([target, name]);
    for (const pid of target < 0 ? inGroup(-target) : [target]) deliver(pid, name);
  };
  return { keeper, kill, deliver, signals };
}

module.exports = { fakeClock, fakeGroup, fakeKeeper, fakeProc, fakeProcesses, fakeThreadedProc, load };
