// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Provisions the Remote-SSH host for run.cjs on this Linux machine, and takes
// it down again: a private X display for the client, and an SSH server of the
// test's own, which listens only on a free 127.0.0.1 port, accepts only a
// key made for the run, and gives each session a home, temporary directory,
// and runtime directory in the work directory. The client's SSH configuration
// pins the server's host key. None of it uses or changes the user's ~/.ssh,
// the system's SSH server, or /etc.
//
// Every process that the run starts, here or in run.cjs, has the run's marker
// in its environment, and the SSH server sets it for each session, so that the
// VS Code Server and the language servers that it starts have it too. The
// teardown stops exactly the processes that have the marker, run an
// executable from the work directory, descend from the servers started here,
// or, when they hide their executable and environment, have a command line
// under the work or sockets directory or such a parent (see runOwned in
// ../processes.cjs), never processes found by name. It knows each by its PID
// and start time, and sends each signal through a pidfd that it opens before
// it checks both again (see ../processes.cjs), so that a PID that a process of
// the run had and another process has since been given is never signaled.
// Where pidfds aren't available, or one can't be used for a process, the check
// is a few system calls before the signal, and the PID could be given to
// another process in between.

const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { isZombie, processFiles, runOwned, signalProcesses } = require('../processes.cjs');

const ALIAS = 'virgil-remote-ssh-test';
const HOST = '127.0.0.1';
// How long the X server and the SSH server get to start.
const START_TIMEOUT_MS = 10000;
// How long the teardown waits for killed processes to go.
const STOP_TIMEOUT_MS = 5000;

// The target that run.cjs tests on. work is the run's work directory and
// sockets its directory for Unix sockets, whose paths must be short; env is
// the environment for every process, with the run's marker. start() starts
// the display and the SSH server; teardown() stops them and everything else
// of the run, including what a failed start() left.
function create({ work, sockets, env, marker, sshd, log }) {
  const ssh = path.join(work, 'ssh');
  const root = path.join(work, 'remote');
  const remoteTmp = path.join(sockets, 'r');
  const user = os.userInfo().username;
  const started = [];
  const target = {
    mode: 'localhost',
    alias: ALIAS,
    root,
    sshConfig: path.join(ssh, 'config'),
    details: { host: HOST, user },
    clientEnv: {},
    logs: [['sshd.log', path.join(work, 'sshd.log')], ['xvfb.log', path.join(work, 'xvfb.log')]],
    start: async () => {
      for (const dir of [ssh, remoteTmp]) fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
      const home = path.join(root, 'home');
      for (const dir of ['config', 'cache', 'data', 'state']) fs.mkdirSync(path.join(home, dir), { recursive: true });
      // A non-interactive bash over SSH reads ~/.bashrc, here the empty one.
      fs.writeFileSync(path.join(home, '.bashrc'), '');

      const display = await startXvfb(work, env, started);
      target.clientEnv = { DISPLAY: display.name, XAUTHORITY: display.auth };
      target.details.display = display.name;
      log(`-- display ${display.name}`);

      for (const name of ['host', 'client']) {
        await check(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', `virgil-remote-ssh-${name}`, '-f', path.join(ssh, name)], env);
      }
      fs.copyFileSync(path.join(ssh, 'client.pub'), path.join(ssh, 'authorized_keys'));
      const hostKey = fs.readFileSync(path.join(ssh, 'host.pub'), 'utf8').trim();
      target.details.hostKeySha256 = `SHA256:${crypto.createHash('sha256')
        .update(Buffer.from(hostKey.split(' ')[1], 'base64')).digest('base64').replace(/=+$/, '')}`;

      const server = await startSshd({ ssh, home, remoteTmp, user, marker, sshd, env, hostKey, started });
      target.details.port = server.port;
      target.details.sshd = server.version;
      log(`-- SSH server ${server.version} on ${HOST}:${server.port}, host key ${target.details.hostKeySha256}`);
    },
    teardown: () => teardown({ work, sockets, marker, started, port: target.details.port }),
  };
  return target;
}

// Starts Xvfb on the first free display, which it reports on a pipe, with a
// cookie that only this run's clients have.
async function startXvfb(work, env, started) {
  const auth = path.join(work, 'Xauthority');
  // One entry for any display and address: FamilyWild, no address or display
  // number, and a random MIT-MAGIC-COOKIE-1.
  const field = (data) => Buffer.concat([Buffer.from([data.length >> 8, data.length & 0xff]), data]);
  fs.writeFileSync(auth, Buffer.concat([
    Buffer.from([0xff, 0xff]), field(Buffer.alloc(0)), field(Buffer.alloc(0)),
    field(Buffer.from('MIT-MAGIC-COOKIE-1')), field(crypto.randomBytes(16)),
  ]), { mode: 0o600 });
  const output = fs.openSync(path.join(work, 'xvfb.log'), 'a');
  const child = spawn('Xvfb', ['-displayfd', '3', '-screen', '0', '1280x1024x24', '-nolisten', 'tcp', '-auth', auth], {
    env, stdio: ['ignore', output, output, 'pipe'], detached: true,
  });
  fs.closeSync(output);
  started.push({ name: 'Xvfb', child, identity: identify(child.pid) });
  const number = await new Promise((resolve, reject) => {
    let text = '';
    const timer = setTimeout(() => reject(new Error(`Xvfb did not report a display within ${START_TIMEOUT_MS / 1000} seconds`)), START_TIMEOUT_MS);
    child.on('error', (error) => { clearTimeout(timer); reject(new Error(`Xvfb did not start: ${error.message}`)); });
    child.on('exit', (code, signal) => { clearTimeout(timer); reject(new Error(`Xvfb exited with ${code ?? signal}; see xvfb.log`)); });
    child.stdio[3].on('data', (data) => {
      text += data;
      if (/^\d+\n/.test(text)) {
        clearTimeout(timer);
        resolve(text.trim());
      }
    });
  });
  child.removeAllListeners('exit');
  return { name: `:${number}`, auth };
}

// Quotes a value for ssh_config and sshd_config, which take double quotes.
function quoted(value) {
  if (/["\\\n]/.test(value)) throw new Error(`unsupported character in ${value}`);
  return `"${value}"`;
}

// Starts the SSH server on a free port; another process can take the port
// between the check and the bind, so a failed bind is retried on another.
async function startSshd({ ssh, home, remoteTmp, user, marker, sshd, env, hostKey, started }) {
  for (let attempt = 1; ; attempt++) {
    const port = await freePort();
    // The client's configuration: only the run's key and the pinned host
    // key, no agent, prompts, or connection sharing.
    fs.writeFileSync(path.join(ssh, 'config'), `Host ${ALIAS}
  HostName ${HOST}
  Port ${port}
  User ${user}
  IdentityFile ${quoted(path.join(ssh, 'client'))}
  IdentitiesOnly yes
  IdentityAgent none
  BatchMode yes
  PasswordAuthentication no
  KbdInteractiveAuthentication no
  StrictHostKeyChecking yes
  UserKnownHostsFile ${quoted(path.join(ssh, 'known_hosts'))}
  GlobalKnownHostsFile /dev/null
  UpdateHostKeys no
  CheckHostIP no
  ControlMaster no
  ControlPath none
  ForwardAgent no
  ForwardX11 no
  ConnectTimeout 15
  LogLevel ERROR
`);
    fs.writeFileSync(path.join(ssh, 'known_hosts'), `[${HOST}]:${port} ${hostKey}\n`);
    // The server: this user only, with the run's key, on 127.0.0.1. Each
    // session gets the run's home and directories, and the marker. The work
    // directory's parents may be writable by others, so StrictModes is off.
    const config = path.join(ssh, 'sshd_config');
    fs.writeFileSync(config, `Port ${port}
ListenAddress ${HOST}
AddressFamily inet
HostKey ${quoted(path.join(ssh, 'host'))}
PidFile ${quoted(path.join(ssh, 'sshd.pid'))}
AuthorizedKeysFile ${quoted(path.join(ssh, 'authorized_keys'))}
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
PermitRootLogin no
AllowUsers ${user}
StrictModes no
PermitUserRC no
PermitUserEnvironment no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
PrintMotd no
PrintLastLog no
UseDNS no
LogLevel VERBOSE
Subsystem sftp internal-sftp
SetEnv ${[
      `HOME=${home}`,
      `XDG_CONFIG_HOME=${path.join(home, 'config')}`,
      `XDG_CACHE_HOME=${path.join(home, 'cache')}`,
      `XDG_DATA_HOME=${path.join(home, 'data')}`,
      `XDG_STATE_HOME=${path.join(home, 'state')}`,
      `XDG_RUNTIME_DIR=${remoteTmp}`,
      `TMPDIR=${remoteTmp}`,
      marker,
    ].map(quoted).join(' ')}
`);
    try {
      await check([sshd, '-t', '-f', config], env);
    } catch (error) {
      throw new Error(`${error.message}${privsepHint(error.message)}`);
    }
    const logFile = path.join(path.dirname(ssh), 'sshd.log');
    const output = fs.openSync(logFile, 'a');
    // sshd needs its absolute path to re-execute itself.
    const child = spawn(sshd, ['-D', '-e', '-f', config], { env, stdio: ['ignore', output, output], detached: true });
    fs.closeSync(output);
    const entry = { name: 'sshd', child, identity: identify(child.pid) };
    started.push(entry);
    const version = await new Promise((resolve) => {
      const deadline = Date.now() + START_TIMEOUT_MS;
      let exited = false;
      child.on('error', () => { exited = true; });
      child.on('exit', () => { exited = true; });
      const poll = async () => {
        const banner = exited ? undefined : await readBanner(port).catch(() => undefined);
        if (banner !== undefined || exited || Date.now() > deadline) resolve(banner);
        else setTimeout(poll, 100);
      };
      void poll();
    });
    if (version !== undefined) return { port, version };
    // The teardown covers it too, if it's still running then.
    if (running(entry)) signal([entry.identity], 'SIGKILL');
    const text = fs.readFileSync(logFile, 'utf8');
    if (attempt < 3 && /Address already in use/.test(text)) continue;
    throw new Error(`the SSH server did not start on ${HOST}:${port}; see sshd.log${privsepHint(text)}`);
  }
}

// OpenSSH refuses to start where it needs its privilege separation directory,
// such as /run/sshd, and that is missing. The system's SSH service makes it
// when it starts, and only root can.
function privsepHint(text) {
  const missing = /Missing privilege separation directory: (\S+)/.exec(text);
  return missing === null ? ''
    : `; the SSH server needs its privilege separation directory, ${missing[1]}, which root must create, as CI does: sudo install -d -m 0755 ${missing[1]}`;
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', (error) => reject(new Error(`no free port on ${HOST}: ${error.message}`)));
    server.listen(0, HOST, () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

// The SSH server's identification line, such as "OpenSSH_9.6p1 Ubuntu-3ubuntu13".
function readBanner(port) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, HOST);
    let text = '';
    socket.setTimeout(2000, () => socket.destroy(new Error('no banner')));
    socket.on('data', (data) => {
      text += data;
      const line = /^SSH-2\.0-([^\r\n]*)\r?\n/.exec(text);
      if (line) {
        resolve(line[1]);
        socket.destroy();
      }
    });
    socket.on('error', reject);
    socket.on('close', () => reject(new Error('closed')));
  });
}

function portOpen(port) {
  return new Promise((resolve) => {
    const socket = net.connect(port, HOST);
    socket.on('connect', () => { socket.destroy(); resolve(true); });
    socket.on('error', () => resolve(false));
  });
}

function check(command, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(command[0], command.slice(1), { env, stdio: ['ignore', 'ignore', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (data) => { stderr += data; });
    child.on('error', (error) => reject(new Error(`${command[0]} did not start: ${error.message}`)));
    child.on('exit', (code, signal) => (code === 0 ? resolve()
      : reject(new Error(`${command.join(' ')} exited with ${code ?? signal}: ${stderr.trim()}`))));
  });
}

// A process's state, its parent's PID, its number of threads, and its start
// time, in clock ticks after boot, from /proc/<pid>/stat: its third, fourth,
// 20th, and 22nd fields.
function readStat(pid) {
  const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
  const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
  return { state: fields[0], ppid: Number(fields[1]), threads: Number(fields[17]), start: fields[19] };
}

// A process's identity: its PID and its start time, which a later process
// with the same PID doesn't share. Undefined if the process has gone or is a
// zombie (see isZombie in ../processes.cjs).
function identify(pid) {
  try {
    const { state, threads, start } = readStat(pid);
    return isZombie(state, threads) ? undefined : { pid, start };
  } catch {
    return undefined;
  }
}

// Whether the process with this identity still runs.
function same({ pid, start }) {
  return identify(pid)?.start === start;
}

// Whether a process that start() started still runs: Node hasn't seen it
// exit, and its PID is still that process's.
function running({ child, identity }) {
  return child.exitCode === null && child.signalCode === null && identity !== undefined && same(identity);
}

// Sends a signal to each of the processes, by identity, that still runs; it
// skips the others, whose PIDs may now be other processes'. The teardown's
// last look finds one that couldn't be signaled.
function signal(processes, name) {
  signalProcesses(processes, name);
}

// The run's processes: those whose environment has the marker, those that
// run an executable from the work directory or the sockets directory, the
// servers that start() started and that still run, and all their
// descendants, each with its identity. The SSH server and its sessions write
// their titles over their environment, so only descent finds those. The /proc
// entries of other users' processes can't be read, and processes that exit
// meanwhile are skipped; zombies don't count, but a process whose main thread
// has exited while its other threads run does, and a live thread's /proc
// files identify it (see processFiles in ../processes.cjs), whichever of its
// threads they still describe. A process that hides its executable and
// environment, as a non-dumpable one does, is still the run's if its identity
// is one that start() started, if its parent is the run's, or if an argument
// of its command line names a path under the work or sockets directory (see
// runOwned in ../processes.cjs); a readable executable that is under neither
// settles that it isn't. A live process that no thread's files could describe,
// which a scan that raced its threads' exit may find, is reported as
// unidentified rather than left out, so that the teardown tries again instead
// of counting it as gone. Throws if /proc itself can't be listed.
function runProcesses({ work, sockets, marker, started }) {
  const roots = [work, sockets];
  const table = new Map();
  for (const name of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(name) || Number(name) === process.pid) continue;
    try {
      const { state, ppid, threads } = readStat(name);
      if (isZombie(state, threads)) continue;
      const identified = processFiles(fs, name, { exe: 'link', environ: 'latin1', cmdline: 'utf8' });
      if (identified === undefined) continue;
      if (identified.unreadable) {
        table.set(Number(name), { pid: Number(name), ppid, start: identified.start, unidentified: true });
        continue;
      }
      table.set(Number(name), { pid: Number(name), ppid, start: identified.start, files: identified.files });
    } catch (error) {
      if (!['ENOENT', 'ESRCH', 'EACCES', 'EPERM'].includes(error.code)) throw error;
    }
  }
  // The identities of the servers that start() started and that still run.
  const known = started.flatMap(({ child, identity }) => {
    const entry = identity === undefined ? undefined : table.get(identity.pid);
    return child.exitCode === null && child.signalCode === null && entry?.start === identity.start ? [identity] : [];
  });
  // A parent is the run's only if it started no later than its child, so that
  // a PID given to another process after the real parent exited doesn't
  // qualify. Ownership spreads until no more processes gain it, since a
  // process can be listed before its parent.
  const owned = new Set();
  const parent = (ppid, start) => {
    const entry = table.get(ppid);
    return entry !== undefined && owned.has(entry.pid) && Number(entry.start) <= Number(start);
  };
  for (let added = true; added;) {
    added = false;
    for (const entry of table.values()) {
      if (entry.unidentified || owned.has(entry.pid)) continue;
      if (runOwned({ ...entry, roots, marker, known, parent, descend: true })) {
        owned.add(entry.pid);
        added = true;
      }
    }
  }
  return [...table.values()].flatMap((entry) => {
    if (entry.unidentified) return [{ pid: entry.pid, start: entry.start, unidentified: true }];
    if (!owned.has(entry.pid)) return [];
    const { exe = '', cmdline = '' } = entry.files;
    return [{ pid: entry.pid, start: entry.start, exe, command: cmdline.replaceAll('\0', ' ').trim() }];
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Stops every process of the run and the servers, and reports whether it
// could confirm that none is left and the SSH server's port is closed.
// Processes start while others stop, such as the shells that the VS Code
// Server runs, and an orphan no longer descends from the process that started
// it: so they are frozen with SIGSTOP until a look finds no new one, then
// killed. Xvfb goes last, with SIGTERM, so that it removes its socket and lock
// file. If the processes can't be listed, or a look finds a live process that
// no thread's files could identify (see runProcesses), it tries again five
// times, 200 ms apart, and reports what it found if it still can't, rather
// than taking that for none left.
async function teardown({ work, sockets, marker, started, port }) {
  const result = { ok: true, stopped: [], left: [], portClosed: null };
  const display = started.find((entry) => entry.name === 'Xvfb');
  const xvfb = display !== undefined && running(display) ? display.identity : undefined;
  const key = (p) => `${p.pid}@${p.start}`;
  const list = async () => {
    for (let tries = 1; ; tries++) {
      try {
        const found = runProcesses({ work, sockets, marker, started }).filter((p) => xvfb === undefined || key(p) !== key(xvfb));
        const unknown = found.filter((p) => p.unidentified);
        if (unknown.length === 0) return found;
        if (tries === 5) throw new Error(`could not identify the process(es) with pid ${unknown.map((p) => p.pid).join(', ')}`);
      } catch (error) {
        if (tries === 5) throw error;
      }
      await sleep(200);
    }
  };
  try {
    const frozen = new Map();
    for (let round = 0; round < 25; round++) {
      const fresh = (await list()).filter((p) => !frozen.has(key(p)));
      if (fresh.length === 0) break;
      signal(fresh, 'SIGSTOP');
      for (const p of fresh) frozen.set(key(p), p);
    }
    result.stopped = [...frozen.values()];
    signal(result.stopped, 'SIGKILL');
    const deadline = Date.now() + STOP_TIMEOUT_MS;
    for (;;) {
      const left = await list();
      if (left.length === 0) break;
      if (Date.now() > deadline) {
        result.ok = false;
        result.left = left;
        break;
      }
      signal(left, 'SIGKILL');
      await sleep(200);
    }
    if (xvfb !== undefined && same(xvfb)) {
      result.stopped.push({ ...xvfb, exe: 'Xvfb', command: 'Xvfb' });
      signal([xvfb], 'SIGTERM');
      const limit = Date.now() + STOP_TIMEOUT_MS;
      while (same(xvfb) && Date.now() < limit) await sleep(100);
      signal([xvfb], 'SIGKILL');
    }
    const left = runProcesses({ work, sockets, marker, started });
    if (left.length > 0) {
      result.ok = false;
      result.left = left;
    }
  } catch (error) {
    result.ok = false;
    result.error = `could not list the run's processes: ${error.message}`;
  }
  if (port !== undefined) {
    result.portClosed = !(await portOpen(port));
    if (!result.portClosed) result.ok = false;
  }
  return result;
}

// runProcesses is exported for test/remote-ssh.cjs, and teardown as well.
module.exports = { create, runProcesses, teardown };
