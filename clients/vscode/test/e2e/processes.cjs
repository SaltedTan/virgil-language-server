// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Process groups and signals for the VS Code test runners, run.cjs and
// remote-ssh/run.cjs, and the Remote-SSH test's teardown: a command in a
// process group of its own that a runner can stop as a whole, and signals to
// processes known by their PID and start time, neither of which can reach a
// process or group that another program has since been given the number of.

const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const KEEPER = path.join(__dirname, 'keeper.cjs');
// How long a group's keeper gets to answer a request (see startGroup).
const KEEPER_REPLY_MS = 1000;

// A process's state, parent, process group, and start time; undefined if it
// has gone. On Linux they are the third, fourth, fifth, and 22nd fields of
// /proc/<pid>/stat, and the start time is in clock ticks after boot;
// elsewhere ps gives them, and the start time only to the second.
function processInfo(pid) {
  if (process.platform !== 'linux') return psTable(['-p', String(pid)]).find((p) => p.pid === Number(pid));
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
    return { pid: Number(pid), state: fields[0], ppid: Number(fields[1]), group: Number(fields[2]), start: fields[19] };
  } catch {
    return undefined;
  }
}

// Every process, as processInfo gives it.
function processTable() {
  if (process.platform !== 'linux') return psTable(['-A']);
  return fs.readdirSync('/proc').flatMap((name) => {
    const info = /^[0-9]+$/.test(name) ? processInfo(name) : undefined;
    return info === undefined ? [] : [info];
  });
}

// The processes that ps lists with selection, which -p's exit status of 1
// for a process that has gone leaves empty.
function psTable(selection) {
  const ps = spawnSync('ps', [...selection, '-o', 'pid=', '-o', 'ppid=', '-o', 'pgid=', '-o', 'stat=', '-o', 'lstart='], { encoding: 'utf8' });
  if (ps.error !== undefined || (ps.status !== 0 && selection[0] !== '-p')) {
    throw new Error(`ps failed: ${ps.error?.message ?? ps.stderr}`);
  }
  return ps.stdout.split('\n').flatMap((line) => {
    const [, pid, ppid, group, state, start] = /^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S.*?)\s*$/.exec(line) ?? [];
    return pid === undefined ? [] : [{ pid: Number(pid), state: state[0], ppid: Number(ppid), group: Number(group), start }];
  });
}

// A Python program that sends a signal to processes, each given by its PID
// and start time, through a pidfd: it opens the pidfd and only then checks the
// start time, in /proc, so the signal reaches that process or none, never
// another that has been given the PID. Its argument is JSON, [signal,
// [[pid, start], ...]]. It prints, as JSON, null if this Python or kernel
// can't open pidfds and send signals through them, which needs Python 3.9 and
// Linux 5.3, and otherwise what happened to each process: "sent"; "gone", if
// it has exited; "other", if its PID is another process's; or "error" and the
// errno's name, such as "error EPERM", if a pidfd couldn't be opened for it
// or couldn't signal it.
const PIDFD_SIGNAL = `
import errno, json, os, signal, sys
name, targets = json.loads(sys.argv[1])
try:
    fd = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(fd, 0)
    finally:
        os.close(fd)
except (AttributeError, OSError):
    print('null')
    sys.exit()
def failed(error):
    return 'error ' + errno.errorcode.get(error.errno, str(error.errno))
def outcome(pid, start):
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return 'gone'
    except OSError as error:
        return failed(error)
    try:
        with open('/proc/%d/stat' % pid) as file:
            stat = file.read()
        fields = stat[stat.rindex(')') + 2:].split(' ')
        if fields[19] != start:
            return 'other'
        if fields[0] == 'Z':
            return 'gone'
        signal.pidfd_send_signal(fd, getattr(signal, name))
        return 'sent'
    except (FileNotFoundError, ProcessLookupError):
        return 'gone'
    except OSError as error:
        return failed(error)
    finally:
        os.close(fd)
print(json.dumps([outcome(pid, start) for pid, start in targets]))
`;

// PIDFD_SIGNAL's results that need no other attempt.
const SETTLED = ['sent', 'gone', 'other'];

// Whether signals to processes go through pidfds: undefined until the first
// signal or pidfdSupport(), then true, or why not.
let pidfds;

// Runs PIDFD_SIGNAL; returns what happened to each target, or undefined, and
// then sets pidfds, if signals can't go through pidfds.
function pidfdSignal(name, targets) {
  if (process.platform !== 'linux') {
    pidfds = 'pidfds are a Linux feature';
    return undefined;
  }
  const helper = spawnSync('python3', ['-I', '-c', PIDFD_SIGNAL, JSON.stringify([name, targets])], { encoding: 'utf8' });
  let results;
  try {
    results = JSON.parse(helper.stdout);
  } catch {
    // Reported below.
  }
  if (helper.status === 0 && Array.isArray(results) && results.length === targets.length) {
    pidfds = true;
    return results;
  }
  if (helper.error?.code === 'ENOENT') pidfds = 'python3 is not on PATH';
  else if (helper.status === 0 && results === null) pidfds = 'python3 or the kernel has no pidfds';
  else pidfds = `the pidfd helper failed: ${(helper.error?.message ?? helper.stderr ?? '').trim() || `status ${helper.status}`}`;
  return undefined;
}

// Whether signals to processes go through pidfds: true, or why not.
function pidfdSupport() {
  if (pidfds === undefined) pidfdSignal('SIGTERM', []);
  return pidfds;
}

// Sends a signal to a process, given by its PID and start time, if a check
// just before finds that it is still that process and not a zombie; returns
// what happened, as PIDFD_SIGNAL does, or "failed" and the error's code.
function checkedSignal({ pid, start }, name) {
  const info = processInfo(pid);
  if (info === undefined || info.state === 'Z') return 'gone';
  if (info.start !== start) return 'other';
  try {
    process.kill(pid, name);
    return 'sent';
  } catch (error) {
    return error.code === 'ESRCH' ? 'gone' : `failed ${error.code}`;
  }
}

// Sends a signal to each of processes, each given by its PID and start time,
// that is still that process and not a zombie, wherever it is now; returns
// what happened to each (see checkedSignal). Where pidfds are available (see
// PIDFD_SIGNAL) the signal goes through one. Where they aren't, or a pidfd
// couldn't be opened for a process or couldn't signal it, it follows a check
// a few system calls before it, so a process that exits between them, and
// whose PID another process is given at once, could still get it. ps gives
// start times only to the second, so off Linux, a process that is given the
// PID of one that has exited within the same second as that one started gets
// it too.
function signalProcesses(processes, name) {
  if (processes.length === 0) return [];
  const results = pidfds === undefined || pidfds === true ? pidfdSignal(name, processes.map((p) => [p.pid, p.start])) : undefined;
  return processes.map((p, i) => (SETTLED.includes(results?.[i]) ? results[i] : checkedSignal(p, name)));
}

// Starts command, with args, in a process group of its own, under keeper.cjs,
// which is the group's first process and runs the command there: the group's
// number is the keeper's PID. While the keeper is in the group, alive or a
// zombie, Linux and macOS give no other process or group that number. Node.js
// may have reaped the keeper before its exit event, since it reaps every child
// that has exited before it runs any of their exit listeners, so the runner
// can't tell from the keeper's ChildProcess whether it has been reaped. So the
// runner never signals the group by its number: it asks the keeper, over the
// keeper's standard input, and the keeper, which is in the group as it
// signals it, signals its own group. If the keeper has gone, or goes before
// it says that it has sent the signal, the runner signals instead each process
// that it saw in the group (see signalProcesses). If the keeper does neither
// within KEEPER_REPLY_MS, as when it has been stopped, the runner signals
// those processes and the keeper itself, by its PID and start time, and sends
// every later signal the same way; SIGKILL ends a stopped keeper too. A keeper
// that hasn't exited KEEPER_REPLY_MS after its release gets SIGKILL the same
// way. The keeper survives SIGINT, SIGTERM, and SIGHUP, which the rest of the
// group gets as usual, so the group stays the run's until the runner releases
// the keeper, or asks for SIGKILL; if the runner goes, the keeper stops the
// group. stdio is spawn's for the command's standard input, output, and
// error, each 'pipe', 'inherit', or 'ignore'; the returned group's stdin,
// stdout, and stderr are the pipes. onExit gets the command's exit as
// { code, signal }, or { error } if it didn't start.
function startGroup(command, args, { env, stdio, onExit }) {
  // For the keeper, 'inherit' would mean the runner's descriptors 3 to 5; the
  // command's are the runner's own standard streams.
  const passed = stdio.map((s, i) => (s === 'inherit' ? i : s));
  const keeper = spawn(process.execPath, [KEEPER, JSON.stringify({ command, args, given: stdio.map((s) => s !== 'ignore') })], {
    env, detached: true, stdio: ['pipe', 'pipe', 'inherit', ...passed],
  });
  const number = keeper.pid;
  // The keeper's parent and start time. Node.js can't have reaped it before
  // this turn of the event loop ends.
  const leader = number === undefined ? undefined : processInfo(number);
  // The processes that the runner has seen in the group, other than the
  // keeper, each by PID and start time.
  const seen = new Map();
  // The signals that the runner has asked the keeper for, in order, and that
  // the keeper hasn't said it sent, each with the timer for its answer.
  const requested = [];
  // Whether the keeper has failed to answer a request in time.
  let unresponsive = false;
  let lost = leader === undefined;
  let killed = false;
  let reported = false;
  let keeperGone = false;
  let reportsEnded = keeper.stdout === null;
  // Whether the keeper hasn't been reaped: its PID still has its parent, the
  // runner, and its start time. ps gives start times only to the second, so
  // off Linux another process would pass for the keeper only if it were
  // another child of the runner, given the keeper's PID within the second in
  // which the keeper started.
  const led = () => {
    const info = processInfo(number);
    return info !== undefined && info.start === leader.start && info.ppid === leader.ppid;
  };
  const report = (result) => {
    if (reported) return;
    reported = true;
    onExit(result);
  };
  // The group's processes other than the keeper, and not zombies, while the
  // group is the run's, which the runner then remembers; undefined once it
  // isn't. The group is the run's while the keeper hasn't been reaped, or,
  // once it has, while a process that the runner saw there in an earlier look
  // is still in it, the same process by PID and start time, alive or a zombie:
  // it has kept the number since, as a process can't come back to a session
  // that it has left. Once neither is, another process may have been given the
  // number, and the group is never the run's again. That is checked after the
  // look, so that it covers the whole look.
  const look = () => {
    if (lost) return undefined;
    const found = processTable().filter((p) => p.group === number && p.state !== 'Z' && p.pid !== number);
    const held = ([pid, start]) => {
      const info = processInfo(pid);
      return info?.start === start && info.group === number;
    };
    if (!led() && ![...seen].some(held)) {
      lost = true;
      return undefined;
    }
    for (const p of found) seen.set(p.pid, p.start);
    return found;
  };
  // Sends a signal to each process that the runner saw in the group, wherever
  // it is now, and, with alsoKeeper, last to the keeper, if it hasn't been
  // reaped (see signalProcesses).
  const signalSeen = (name, alsoKeeper = false) => {
    look();
    const targets = [...seen].map(([pid, start]) => ({ pid, start }));
    if (alsoKeeper && leader !== undefined && led()) targets.push({ pid: number, start: leader.start });
    signalProcesses(targets, name);
  };
  // The keeper hasn't answered the first of the requests in time, so it can't
  // answer any of them: they go to the processes that the runner saw in the
  // group and to the keeper instead, as every later one does.
  const unanswered = () => {
    unresponsive = true;
    for (const { name, timer } of requested.splice(0)) {
      clearTimeout(timer);
      signalSeen(name, true);
    }
  };
  // The keeper has gone without saying how the command ended: the group's
  // SIGKILL ended both, or something else ended the keeper.
  const keeperEnded = () => {
    if (!keeperGone || !reportsEnded || reported) return;
    if (killed) report({ code: null, signal: 'SIGKILL' });
    else report({ error: new Error(`its process group's keeper exited with ${keeper.exitCode ?? keeper.signalCode} first`) });
  };
  let pending = '';
  keeper.stdout?.on('data', (data) => {
    pending += data;
    const lines = pending.split('\n');
    pending = lines.pop();
    for (const line of lines) {
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        continue;
      }
      if (message.spawn !== undefined) look();
      else if (message.signaled !== undefined) clearTimeout(requested.shift()?.timer);
      else if (message.exit !== undefined) report({ code: message.exit[0], signal: message.exit[1] });
      else if (message.error !== undefined) report({ error: Object.assign(new Error(message.error.message), message.error) });
    }
  });
  // The keeper has gone, and every report of its has been read.
  keeper.stdout?.on('close', () => {
    reportsEnded = true;
    for (const { name, timer } of requested.splice(0)) {
      clearTimeout(timer);
      signalSeen(name);
    }
    keeperEnded();
  });
  keeper.stdin?.on('error', () => {});
  keeper.on('exit', () => {
    keeperGone = true;
    keeperEnded();
  });
  keeper.on('error', (error) => {
    lost = true;
    keeperGone = true;
    report({ error });
  });
  return {
    pid: number,
    stdin: keeper.stdio[3],
    stdout: keeper.stdio[4],
    stderr: keeper.stdio[5],
    look,
    // Asks the keeper to send a signal to the group; once the keeper has gone,
    // sends it instead to each process that the runner saw in the group, and
    // once it has failed to answer, to those and to the keeper. The keeper
    // answers SIGKILL by going.
    signal: (name) => {
      killed ||= name === 'SIGKILL';
      if (reportsEnded || !keeper.stdin?.writable) {
        signalSeen(name);
      } else if (unresponsive) {
        signalSeen(name, true);
      } else {
        requested.push({ name, timer: setTimeout(unanswered, KEEPER_REPLY_MS) });
        keeper.stdin.write(`${name}\n`);
      }
    },
    // Lets the keeper exit, once the runner has seen the command exit and no
    // other process left in the group; resolves once Node.js has reaped it.
    release: () => new Promise((resolve) => {
      if (keeperGone || number === undefined) {
        resolve();
        return;
      }
      const timer = setTimeout(() => {
        if (led()) signalProcesses([{ pid: number, start: leader.start }], 'SIGKILL');
      }, KEEPER_REPLY_MS);
      keeper.on('exit', () => {
        clearTimeout(timer);
        resolve();
      });
      keeper.stdin.end('release\n');
    }),
  };
}

module.exports = { PIDFD_SIGNAL, SETTLED, pidfdSupport, processInfo, processTable, signalProcesses, startGroup };
