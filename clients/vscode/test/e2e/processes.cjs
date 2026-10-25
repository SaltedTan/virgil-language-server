// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Process groups and signals for the VS Code test runners, run.cjs and
// remote-ssh/run.cjs, and the Remote-SSH test's teardown: a command in a
// process group of its own that a runner can stop as a whole, and signals to
// processes known by their PID and start time. See startGroup for group-number
// safety and signalProcesses for the limits of signals to single processes.

const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const KEEPER = path.join(__dirname, 'keeper.cjs');
// How long a group's keeper gets to answer a request (see startGroup).
const KEEPER_REPLY_MS = 1000;
const KEEPER_KILL_WAIT_MS = 30000;

// A process's state, parent, process group, and start time, and on Linux its
// number of threads; undefined if it has gone, null if inaccessible. On Linux
// they are the third, fourth, fifth, 22nd, and 20th fields of /proc/<pid>/stat,
// and the start time is in clock ticks after boot. macOS uses libproc's
// microsecond start time if the first native probe succeeds; otherwise it uses
// ps for the run, as other platforms do, with start times only to the second.
function processInfo(pid) {
  if (process.platform === 'darwin') {
    const info = darwinInfos([Number(pid)])?.[0];
    if (darwinNative) return info === false ? undefined : info ?? null;
  }
  if (process.platform !== 'linux') return psTable(['-p', String(pid)]).find((p) => p.pid === Number(pid));
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
    return { pid: Number(pid), state: fields[0], ppid: Number(fields[1]), group: Number(fields[2]), start: fields[19], threads: Number(fields[17]) };
  } catch (error) {
    if (['ENOENT', 'ESRCH'].includes(error.code)) return undefined;
    if (['EACCES', 'EPERM'].includes(error.code)) return null;
    throw error;
  }
}

// Whether a process, given the state and thread count that /proc's stat has,
// is a zombie: it has exited, and its parent hasn't reaped it. Linux lists a
// process whose main thread has exited as a zombie while its other threads
// run, and they can still start processes and take signals, so it is no
// zombie until they have exited too. macOS's ps lists a process as a zombie
// only once it has exited, whole.
function isZombie(state, threads) {
  return state === 'Z' && !(threads > 1);
}

// Whether p, a process as processInfo or processTable gives it, is a zombie
// (see isZombie).
function zombie(p) {
  return isZombie(p.state, p.threads);
}

// The contents of the /proc files that identify the process with this PID, by
// name: its exe, environ, and cmdline. Reads them from the directory that
// describes the process, its own while its main thread runs, else that of a
// thread that still runs, under /proc/<pid>/task/<tid>/, since a process whose
// main thread has exited keeps none of those itself (see zombie); the teardown
// and the Remote-SSH leak check identify such a process this way. A thread can
// exit between the choice and the reads while another thread still runs, so a
// candidate whose thread has gone once its files have been read gives way to
// the next one, and every file is read from one thread, never some from one
// and some from another. files maps each name to how to read it: 'link' for a
// symlink, such as exe, else the encoding for a read, such as 'latin1' for
// environ. cmdline is always read as 'utf8' too, and returned, as the thread's
// liveness: it needs no ptrace access, unlike exe and environ, so a user may
// read it even where those are denied, and one that has gone has none, so a
// cmdline read that fails with ENOENT or ESRCH shows that the thread went,
// even if a stat of it just before still described it; any other failure
// leaves it out. A candidate with no readable file, as one whose user may
// read none of them, identifies nothing and gives way to the next thread too.
// A file that the process doesn't have, as a kernel thread has no exe, or that
// its user may not read, is otherwise left out.
// Returns undefined if a process stat read reports ENOENT or ESRCH, or it is
// a zombie (see isZombie); other stat errors throw. Otherwise
// { pid, ppid, start, files }, with the
// process's start time in clock ticks after boot and the contents of the files
// that it could read. The thread's PID and start time must match before and
// after the file reads, and the process must still have its initial identity;
// ppid comes from that same process's final stat, so a reused PID cannot mix
// a former parent's ownership with a replacement's files. Otherwise returns
// { pid, ppid, start, unreadable }, if no thread's files could identify the
// process or its identity changed during the reads. This entry describes the
// current process, which is neither identified nor known to have gone. A caller
// reports such a process rather than counting it as gone, and tries again
// within its bounds, since another thread may still run.
function processFiles(fs, pid, files) {
  const stat = (dir) => {
    try {
      const text = fs.readFileSync(`${dir}/stat`, 'utf8');
      const fields = text.slice(text.lastIndexOf(')') + 2).split(' ');
      return { pid: Number(text.slice(0, text.indexOf(' '))), ppid: Number(fields[1]), start: fields[19], state: fields[0], threads: Number(fields[17]) };
    } catch (error) {
      if (['ENOENT', 'ESRCH'].includes(error.code)) return undefined;
      throw error;
    }
  };
  const initial = stat(`/proc/${pid}`);
  if (initial === undefined || isZombie(initial.state, initial.threads)) return undefined;
  let threads = [];
  try {
    threads = fs.readdirSync(`/proc/${pid}/task`);
  } catch (error) {
    // Another user's process may not list its threads; its files below tell.
    if (!['ENOENT', 'ESRCH', 'EACCES', 'EPERM'].includes(error.code)) throw error;
  }
  // Its own directory describes the process while its main thread runs; once
  // that has exited, only a thread's does.
  const dirs = initial.state === 'Z' ? [] : [`/proc/${pid}`];
  for (const tid of threads) dirs.push(`/proc/${pid}/task/${tid}`);
  for (const dir of dirs) {
    const before = stat(dir);
    if (before === undefined || before.state === 'Z') continue;
    const read = {};
    let exited = false;
    for (const [name, how] of Object.entries({ ...files, cmdline: 'utf8' })) {
      try {
        read[name] = how === 'link' ? fs.readlinkSync(`${dir}/${name}`) : fs.readFileSync(`${dir}/${name}`, how);
      } catch (error) {
        // cmdline only proves that the thread still runs (see above): any
        // failure leaves it out.
        if (name !== 'cmdline' && !['ENOENT', 'ESRCH', 'EACCES', 'EPERM'].includes(error.code)) throw error;
        if (name === 'cmdline' && (error.code === 'ENOENT' || error.code === 'ESRCH')) exited = true;
      }
    }
    // It may have exited between the reads, which then described it only in
    // part, or not at all; and a thread that no file describes identifies
    // nothing, so try the next one.
    const after = stat(dir);
    if (exited || after === undefined || after.state === 'Z' || before.pid !== after.pid || before.start !== after.start || Object.keys(read).length === 0) continue;
    const current = stat(`/proc/${pid}`);
    if (current === undefined || isZombie(current.state, current.threads)) return undefined;
    if (current.pid !== initial.pid || current.start !== initial.start) break;
    return { pid: current.pid, ppid: current.ppid, start: current.start, files: read };
  }
  const after = stat(`/proc/${pid}`);
  if (after === undefined || isZombie(after.state, after.threads)) return undefined;
  return { pid: after.pid, ppid: after.ppid, start: after.start, unreadable: true };
}

// Whether a process that processFiles identified is the run's. files are the
// contents that processFiles read; roots are the directories whose paths mark
// the run's executables and command lines, such as its work directory; marker,
// if given, is the environment entry that the run's processes have; known are
// the identities, { pid, start }, that the run already identified or started;
// parent(ppid, start), if given, says whether the process with that PID, which
// started no later than start, is the run's, for the parent chain; descend
// says whether a process with a readable executable under none of roots is
// still the run's when it descends from one, as the teardown counts every
// descendant, while the leak check excludes readable system executables
// identified only by ancestry; and pid, start, and ppid are the
// process's. A process with the marker, or with a readable executable under
// one of roots, is the run's, as is one whose identity is known. A process
// whose executable can't be read, as a non-dumpable one's can't, may also be
// the run's if its parent is, or by its readable command line, if an argument
// names a path under one of roots: that is the evidence left for a process of
// the run that hides both its executable and its environment. With descend
// false, readable system executables don't become leftovers just because of
// ancestry or command-line paths, keeping transient server helpers out. So
// the same-uid non-dumpable processes that every machine has, such as systemd
// --user's, stay out: their command lines name nothing under the run's
// directories, and the run didn't start them.
function runOwned({ pid, start, ppid, files, roots, marker, known = [], parent, descend = false }) {
  const { exe, environ, cmdline = '' } = files;
  if (marker !== undefined && environ !== undefined && environ.split('\0').includes(marker)) return true;
  if (exe !== undefined && roots.some((root) => exe.startsWith(`${root}/`))) return true;
  if (known.some((identity) => identity.pid === pid && identity.start === start)) return true;
  if (parent !== undefined && parent(ppid, start) && (exe === undefined || descend)) return true;
  if (exe !== undefined) return false;
  return roots.some((root) => cmdline.split('\0').some((arg) => arg.startsWith(`${root}/`)));
}

// Every readable process, and whether no listed PID disappeared. Off Linux, ps
// can't tell: on macOS, the kernel lists the PIDs, then looks up each process,
// and leaves out one that has been reaped in between, so a process that starts
// another and is reaped during the listing hides both. On macOS, group limits
// native reads to the group being checked: PROC_PIDTBSDINFO requires the same
// user, so unrelated system processes must not make that group's scan fail.
function processTable(group) {
  if (process.platform !== 'linux') {
    const listed = psTable(['-A']).filter((p) => group === undefined || p.group === group);
    const native = process.platform === 'darwin' ? darwinInfos(listed.map((p) => p.pid)) : undefined;
    if (darwinNative) {
      const unavailable = native === undefined || native.includes(null);
      return {
        processes: unavailable ? [] : native.filter((p) => p !== false),
        complete: !unavailable && !native.includes(false),
        unavailable,
      };
    }
    return {
      processes: listed,
      complete: true,
    };
  }
  let complete = true;
  const processes = fs.readdirSync('/proc').flatMap((name) => {
    if (!/^[0-9]+$/.test(name)) return [];
    const info = processInfo(name);
    if (info === undefined) complete = false;
    return info == null ? [] : [info];
  });
  return { processes, complete };
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

// Unlike KERN_PROC_ALL (ps), XNU's PROC_PGRP_ONLY copies the group's PIDs
// under proc_list_lock, without a later lookup that can silently drop a
// reaped helper. See bsd/kern/proc_info.c's proc_listpids. A full buffer may
// be truncated, so grow it until there is spare room.
const DARWIN_GROUP_PIDS = `
import ctypes, json, sys
try:
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    listing = lib.proc_listpgrppids
    listing.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    listing.restype = ctypes.c_int
    size = 256
    while size <= 1048576:
        buf = (ctypes.c_int * size)()
        ctypes.set_errno(0)
        count = listing(int(sys.argv[1]), buf, ctypes.sizeof(buf))
        if count < 0 or (count == 0 and ctypes.get_errno() != 0):
            break
        if count < size:
            print(json.dumps(list(buf)[:count]))
            sys.exit()
        size *= 2
except (AttributeError, OSError):
    pass
print('null')
`;

function darwinGroupPids(group) {
  const helper = spawnSync('python3', ['-I', '-c', DARWIN_GROUP_PIDS, String(group)], { encoding: 'utf8' });
  try {
    const pids = JSON.parse(helper.stdout);
    if (helper.status === 0 && Array.isArray(pids) && pids.every((pid) => Number.isInteger(pid) && pid > 0)) return pids;
  } catch {}
  return undefined;
}

// Apple's bsd/sys/proc_info.h: PROC_PIDTBSDINFO = 3, proc_bsdinfo.
// All integer fields have fixed widths (including uid_t/gid_t = uint32_t),
// MAXCOMLEN = 16, and uint64_t aligns to 8 on both arm64 and x86-64 Darwin:
// sizeof = 136, start_tvsec at 120, start_tvusec at 128. Do not use c_long or
// a timeval, whose ABI could differ with the Python interpreter's architecture.
const DARWIN_PROCESS_INFO = `
import ctypes, errno, json, sys
class BsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'pbi_flags', 'pbi_status', 'pbi_xstatus', 'pbi_pid', 'pbi_ppid',
        'pbi_uid', 'pbi_gid', 'pbi_ruid', 'pbi_rgid', 'pbi_svuid',
        'pbi_svgid', 'rfu_1')] + [
        ('pbi_comm', ctypes.c_char * 16), ('pbi_name', ctypes.c_char * 32)
    ] + [(name, ctypes.c_uint32) for name in (
        'pbi_nfiles', 'pbi_pgid', 'pbi_pjobc', 'e_tdev', 'e_tpgid')
    ] + [('pbi_nice', ctypes.c_int32), ('pbi_start_tvsec', ctypes.c_uint64),
         ('pbi_start_tvusec', ctypes.c_uint64)]
def main():
    if (ctypes.sizeof(BsdInfo), BsdInfo.pbi_start_tvsec.offset, BsdInfo.pbi_start_tvusec.offset) != (136, 120, 128):
        return None
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    info = lib.proc_pidinfo
    info.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    info.restype = ctypes.c_int
    results = []
    for pid in json.loads(sys.argv[1]):
        buf = BsdInfo()
        ctypes.set_errno(0)
        # A nonzero argument includes zombies (proc_find_zombref in XNU).
        # The native group list includes them too, so ESRCH would otherwise
        # leave an unreaped zombie missing from every scan forever.
        size = info(pid, 3, 1, ctypes.byref(buf), ctypes.sizeof(buf))
        if size != ctypes.sizeof(buf):
            results.append(False if size <= 0 and ctypes.get_errno() == errno.ESRCH else None)
        elif buf.pbi_pid != pid or buf.pbi_start_tvusec >= 1000000:
            results.append(None)
        else:
            results.append(dict(pid=pid, ppid=buf.pbi_ppid, group=buf.pbi_pgid,
                state={1:'I', 2:'R', 3:'S', 4:'T', 5:'Z'}.get(buf.pbi_status, 'I'),
                start='darwin:%d.%06d' % (buf.pbi_start_tvsec, buf.pbi_start_tvusec)))
    return results
try:
    print(json.dumps(main()))
except (AttributeError, OSError):
    print('null')
`;

let darwinNative;

function darwinInfos(pids) {
  if (darwinNative === false) return undefined;
  if (pids.length === 0) return [];
  const helper = spawnSync('python3', ['-I', '-c', DARWIN_PROCESS_INFO, JSON.stringify(pids)], { encoding: 'utf8' });
  try {
    const infos = JSON.parse(helper.stdout);
    if (helper.status === 0 && Array.isArray(infos) && infos.length === pids.length
      && infos.every((p, i) => p === null || p === false || (p.pid === pids[i]
        && Number.isInteger(p.ppid) && Number.isInteger(p.group) && /^[IRSTZ]$/.test(p.state)
        && /^darwin:\d+\.\d{6}$/.test(p.start)))) {
      darwinNative ??= !infos.includes(null);
      return infos;
    }
  } catch {}
  darwinNative ??= false;
  return undefined;
}

// A Python program that sends a signal to processes, each given by its PID
// and start time, through a pidfd: it opens the pidfd and only then checks the
// start time, in /proc, so the signal reaches that process or none, never
// another that has been given the PID. Its argument is JSON, [signal,
// [[pid, start], ...]]. It prints, as JSON, null if this Python or kernel
// can't open pidfds and send signals through them, which needs Python 3.9 and
// Linux 5.3, and otherwise what happened to each process: "sent"; "gone", if
// it has exited, even if it is still a zombie (see zombie); "other", if its
// PID is another process's; or "error" and the errno's name, such as "error
// EPERM", if a pidfd couldn't be opened for it or couldn't signal it.
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
        if fields[0] == 'Z' and int(fields[17]) < 2:
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
// just before finds that it is still that process and not a zombie (see
// zombie); returns what happened, as PIDFD_SIGNAL does, or "failed" and the
// error's code.
function checkedSignal({ pid, start }, name) {
  const info = processInfo(pid);
  if (info == null || zombie(info)) return 'gone';
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
// whose PID another process is given at once, could still get it. Where
// macOS runs in ps mode, or on other non-Linux platforms, ps gives
// start times only to the second: a replacement that reuses a PID within the
// original process's start second also passes that check.
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
// the keeper, or asks for SIGKILL; if the runner goes, or detaches from the
// group (see detach), before releasing the keeper, the keeper stops the group.
// stdio is spawn's for the command's standard input, output, and error,
// each 'pipe', 'inherit', or
// 'ignore'; the returned group's stdin, stdout, and stderr are the pipes.
// onExit gets the command's exit as { code, signal }, or { error } if it
// didn't start.
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
  let leader = number === undefined ? undefined : processInfo(number);
  // The processes that the runner has seen in the group, other than the
  // keeper, each by PID and start time.
  const seen = new Map();
  // The zombies that the last look found in the group, each by PID and start
  // time (see look).
  let zombies = new Map();
  // The first apparently empty macOS scan.
  let emptySince;
  // The signals that the runner has asked the keeper for, in order, and that
  // the keeper hasn't said it sent, each with the timer for its answer.
  const requested = [];
  // Whether the keeper has failed to answer a request in time.
  let unresponsive = false;
  let lost = leader === undefined || (leader === null && !darwinNative);
  let killed = false;
  let reported = false;
  let keeperGone = false;
  let reportsEnded = keeper.stdout === null;
  let releaseDone;
  // Whether the keeper hasn't been reaped: its PID still has its parent, the
  // runner, and its start time. The ps fallback's second-resolution times
  // also need this parent check: only another child of the runner, given the
  // keeper's PID within its start second, could pass for it.
  const led = () => {
    if (darwinNative && leader === null && keeperGone) return false;
    const info = processInfo(number);
    if (darwinNative && leader === null) {
      if (info == null || info.ppid !== process.pid || info.group !== number) return null;
      leader = info;
    }
    if (darwinNative && info === null) return null;
    return leader != null && info != null && info.start === leader.start && info.ppid === leader.ppid;
  };
  const report = (result) => {
    if (reported) return;
    reported = true;
    onExit(result);
  };
  // The group's live processes other than the keeper, which the runner then
  // remembers, and whether it is empty; undefined once it isn't the run's.
  // The group is the run's while the keeper hasn't been reaped, or,
  // once it has, while a process that the runner saw there in an earlier look
  // is still in it, the same process by PID and start time, alive or a zombie:
  // it has kept the number since, as a process can't come back to a session
  // that it has left. Once neither is, another process may have been given the
  // number, and the group is never the run's again. That is checked after the
  // look, so that it covers the whole look.
  // The group is empty if no listed PID disappeared during the look, and it
  // found none in the group but zombies that the last look found there too. A
  // process could start another and exit between the listing and the read,
  // which then finds it gone, or a zombie, while the listing misses the other.
  // But a zombie that the last look found was one before this look's listing,
  // and a zombie can't start a process, so one whose parent never reaps it
  // doesn't keep the group from emptying. On Linux, a process whose main
  // thread has exited is listed as a zombie while its other threads run, and
  // counts as one only once they have exited too (see zombie): until then, it
  // is live, and the runner remembers and signals it as any other.
  const look = () => {
    if (lost) return undefined;
    if (darwinNative && leader === null && led() === null) return { processes: [], empty: false, unavailable: true };
    const { processes, complete, unavailable } = processTable(process.platform === 'darwin' ? number : undefined);
    if (unavailable) return { processes: [], empty: false, unavailable: true };
    const native = process.platform === 'darwin' && darwinNative;
    const pids = native ? darwinGroupPids(number) : undefined;
    if (native && pids === undefined) return { processes: [], empty: false, unavailable: true };
    const members = processes.filter((p) => p.group === number && p.pid !== number);
    const found = members.filter((p) => !zombie(p));
    let inaccessible = false;
    const held = ([pid, start]) => {
      const info = processInfo(pid);
      if (native && info === null) inaccessible = true;
      return info?.start === start && info.group === number;
    };
    const leading = led();
    if (native && leading === null) return { processes: [], empty: false, unavailable: true };
    const holding = !leading && [...seen].some(held);
    if (inaccessible) return { processes: [], empty: false, unavailable: true };
    if (!leading && !holding) {
      lost = true;
      return undefined;
    }
    for (const p of found) seen.set(p.pid, p.start);
    // ps itself normally exits before the native info reads. On macOS the
    // native group listing or timed confirmation below decides completeness.
    let empty = (complete || process.platform === 'darwin') && members.every((p) => zombie(p) && zombies.get(p.pid) === p.start);
    if (process.platform === 'darwin') {
      if (native) {
        emptySince = undefined;
        // A child missed by ps, or a member that is now live, prevents release.
        empty &&= pids.every((pid) => pid === number || members.some((p) => p.pid === pid && zombie(p)));
      } else {
        // Calls can come back-to-back from exit, pipe-close, and poll events;
        // only a scan at least 50 ms later confirms the first empty result.
        const now = Date.now();
        if (!empty) emptySince = undefined;
        else if (emptySince === undefined) emptySince = now;
        empty &&= now - emptySince >= 50;
      }
    }
    zombies = new Map(members.filter(zombie).map((p) => [p.pid, p.start]));
    return { processes: found, empty };
  };
  // Sends a signal to each process that the runner saw in the group, wherever
  // it is now, and, with alsoKeeper, last to the keeper, if it hasn't been
  // reaped (see signalProcesses).
  const signalSeen = (name, alsoKeeper = false) => {
    group.look();
    const targets = [...seen].map(([pid, start]) => ({ pid, start }));
    if (alsoKeeper && led()) targets.push({ pid: number, start: leader.start });
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
      if (message.spawn !== undefined) group.look();
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
  const group = {
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
    // other process left in the group. Resolves with whether Node.js has
    // reaped it, either on exit or on detach. The release watchdog sends
    // SIGKILL after KEEPER_REPLY_MS and detaches KEEPER_KILL_WAIT_MS later if
    // the keeper still hasn't exited; a runner can detach sooner when its
    // already-armed group stop deadline expires.
    release: () => new Promise((resolve) => {
      if (keeperGone || number === undefined) {
        resolve(true);
        return;
      }
      let abandon;
      const done = (gone = true) => {
        clearTimeout(timer);
        clearTimeout(abandon);
        keeper.removeListener('exit', ended);
        releaseDone = undefined;
        resolve(gone);
      };
      const ended = () => done();
      const timer = setTimeout(() => {
        if (led()) signalProcesses([{ pid: number, start: leader.start }], 'SIGKILL');
        abandon = setTimeout(() => group.detach(), KEEPER_KILL_WAIT_MS);
      }, KEEPER_REPLY_MS);
      releaseDone = done;
      keeper.on('exit', ended);
      keeper.stdin.end('release\n');
    }),
    // Gives up on the group, as a runner does on one that its SIGKILL hasn't
    // ended, so that Node.js can exit while the keeper, or a process that
    // holds the command's pipes, runs on: closes the runner's end of every
    // pipe to the keeper and the command, after which none of their output or
    // reports arrive, and onExit is no longer called, and no longer waits for
    // the keeper. Unless already released, a keeper that is still there finds
    // its input ended without a release, as when the runner goes. Returns
    // whether Node.js has reaped the keeper.
    detach: () => {
      const gone = keeperGone || led() === false;
      reported = true;
      for (const { timer } of requested.splice(0)) clearTimeout(timer);
      for (const stream of keeper.stdio) stream?.destroy();
      keeper.unref();
      releaseDone?.(gone);
      return gone;
    },
  };
  return group;
}

module.exports = { DARWIN_GROUP_PIDS, DARWIN_PROCESS_INFO, KEEPER_KILL_WAIT_MS, PIDFD_SIGNAL, SETTLED, isZombie, pidfdSupport, processFiles, processInfo, processTable, runOwned, signalProcesses, startGroup, zombie };
