// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The Remote-SSH target for run.cjs --target: a host that host.cjs
// provisioned on another machine, which it also takes down, described by the
// bundle's target.json beside the client's key. This module writes the
// client's SSH configuration, which pins the host's key and uses only the
// bundle's key, into the run's work directory, and, as its teardown, stops
// the client's processes of the run that are left on this machine. It never
// reads or changes the user's ~/.ssh: ssh gets the configuration with -F, and
// that names the run's own known_hosts and key.
//
// The client teardown runs on macOS, which has no /proc: it lists processes
// with ps, and takes as the run's those whose command line names a path
// under the work or sockets directory, and their descendants, never processes
// found by name. Each is identified by its PID and start time (see
// processInfo in ../processes.cjs), and signals go only to a process that
// still has both (see signalProcesses).
//
// Starting VS Code, or opening its bundle, registers it and its helper apps
// with LaunchServices, under VS Code's own bundle identifier, which the user's
// VS Code shares. So the teardown also unregisters every LaunchServices record
// under the test's app bundle, the app's and each nested helper's, since
// lsregister -u on the app leaves the helpers registered, and fails unless
// lsregister -dump then lists none. Last, it puts back the keys of VS Code's
// preferences domain, which the user's VS Code shares too, that the run
// changed (see prefs.cjs); savePreferences saves the domain before the
// run's first VS Code launch.

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { processInfo, signalProcesses } = require('../processes.cjs');
const prefs = require('./prefs.cjs');

const STOP_TIMEOUT_MS = 5000;
const LSREGISTER = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister';

function quoted(value) {
  if (/["\\\n]/.test(value)) throw new Error(`unsupported character in ${value}`);
  return `"${value}"`;
}

// Reads the bundle's target.json and checks its fields.
function readDescriptor(file) {
  const descriptor = JSON.parse(fs.readFileSync(file, 'utf8'));
  for (const name of ['alias', 'hostName', 'port', 'user', 'hostKey', 'root', 'marker', 'node']) {
    if (descriptor[name] === undefined) throw new Error(`${file} has no ${name}`);
  }
  if (!/^[A-Za-z0-9._-]+$/.test(descriptor.alias) || !/^[A-Za-z0-9._:-]+$/.test(descriptor.hostName)) {
    throw new Error(`${file} has an unsupported alias or host name`);
  }
  if (!Number.isInteger(descriptor.port) || !/^VIRGIL_REMOTE_SSH_RUN=[0-9a-f-]+$/.test(descriptor.marker)) {
    throw new Error(`${file} has an unsupported port or marker`);
  }
  if (!/^[A-Za-z0-9._/-]+$/.test(descriptor.root)) throw new Error(`${file} has an unsupported root`);
  return descriptor;
}

// The paths of LaunchServices records at or under app, an app bundle.
function registrations(app) {
  const dump = spawnSync(LSREGISTER, ['-dump'], { encoding: 'utf8', maxBuffer: 1024 * 1024 * 1024 });
  if (dump.error !== undefined || dump.status !== 0) throw new Error(`lsregister -dump failed: ${dump.error?.message ?? dump.stderr}`);
  const found = new Set();
  for (const [, file] of dump.stdout.matchAll(/^path:\s+(.*?) \(0x[0-9a-f]+\)$/gm)) {
    if (file === app || file.startsWith(`${app}/`)) found.add(file);
  }
  return [...found].sort();
}

// Unregisters every LaunchServices record under app; returns what it
// unregistered and what is still registered.
function unregister(app) {
  const unregistered = registrations(app);
  for (const file of unregistered) spawnSync(LSREGISTER, ['-u', file]);
  return { unregistered, left: registrations(app) };
}

function create({ file, work, sockets, app, workDir }) {
  const descriptor = readDescriptor(file);
  const ssh = path.join(work, 'ssh');
  let preferences;
  const target = {
    mode: 'external',
    alias: descriptor.alias,
    root: descriptor.root,
    node: descriptor.node,
    marker: descriptor.marker,
    sshConfig: path.join(ssh, 'config'),
    details: {
      host: descriptor.hostName, address: descriptor.address ?? null, port: descriptor.port, user: descriptor.user,
      hostKeySha256: descriptor.hostKeySha256 ?? null, sshd: descriptor.sshd ?? null, allowFrom: descriptor.allowFrom ?? null,
    },
    clientEnv: {},
    logs: [],
    start: async () => {
      fs.mkdirSync(ssh, { recursive: true, mode: 0o700 });
      const key = path.join(ssh, 'client');
      fs.copyFileSync(path.join(path.dirname(file), 'client'), key);
      fs.chmodSync(key, 0o600);
      fs.writeFileSync(path.join(ssh, 'known_hosts'), `[${descriptor.hostName}]:${descriptor.port} ${descriptor.hostKey}\n`);
      // As localhost.cjs's: only the run's key and the pinned host key, no
      // agent, keychain, prompts, or connection sharing.
      fs.writeFileSync(target.sshConfig, `Host ${descriptor.alias}
  HostName ${descriptor.hostName}
  Port ${descriptor.port}
  User ${descriptor.user}
  IdentityFile ${quoted(key)}
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
  ServerAliveInterval 15
  LogLevel ERROR
`, { mode: 0o600 });
    },
    savePreferences: () => {
      preferences = prefs.save({ workDir, app });
      return preferences.dir;
    },
    teardown: () => teardown({ work, sockets, app, preferences }),
  };
  return target;
}

// The client's processes of the run: those whose command line names a path
// under the work or sockets directory, and their descendants, with their
// identities. Leaves out this process and its ancestors.
function runProcesses({ work, sockets }) {
  const ps = spawnSync('ps', ['-A', '-ww', '-o', 'pid=', '-o', 'ppid=', '-o', 'command='], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  if (ps.error !== undefined || ps.status !== 0) throw new Error(`ps failed: ${ps.error?.message ?? ps.stderr}`);
  const table = ps.stdout.split('\n').flatMap((line) => {
    const [, pid, ppid, command] = /^\s*(\d+)\s+(\d+)\s(.*)$/.exec(line) ?? [];
    return pid === undefined ? [] : [{ pid: Number(pid), ppid: Number(ppid), command: command.trim() }];
  });
  const byPid = new Map(table.map((p) => [p.pid, p]));
  const ancestors = new Set();
  for (let pid = process.pid; pid > 1 && !ancestors.has(pid); pid = byPid.get(pid)?.ppid ?? 0) ancestors.add(pid);
  const roots = [work, sockets].map((dir) => `${dir}/`);
  const owned = new Set(table.filter((p) => !ancestors.has(p.pid) && roots.some((root) => p.command.includes(root))).map((p) => p.pid));
  for (let added = true; added;) {
    added = false;
    for (const p of table) {
      if (!owned.has(p.pid) && !ancestors.has(p.pid) && owned.has(p.ppid)) {
        owned.add(p.pid);
        added = true;
      }
    }
  }
  return table.filter((p) => owned.has(p.pid)).flatMap((p) => {
    const info = processInfo(p.pid);
    if (info === undefined) return [];
    if (info === null) return [{ pid: p.pid, unidentified: true, command: p.command }];
    if (info.state === 'Z') return [];
    return [{ pid: p.pid, start: info.start, exe: p.command.split(' ')[0], command: p.command }];
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function failed(result, message) {
  result.ok = false;
  result.error = result.error === undefined ? message : `${result.error}; ${message}`;
}

// Stops the client's processes of the run that are left, as localhost.cjs's
// teardown does: SIGSTOP until a look finds no new one, then SIGKILL. The
// host's own teardown is host.cjs's.
async function teardown({ work, sockets, app, preferences }) {
  const result = { ok: true, stopped: [], left: [], portClosed: null, side: 'client' };
  const key = (p) => `${p.pid}@${p.start}`;
  try {
    const frozen = new Map();
    for (let round = 0; round < 25; round++) {
      const fresh = runProcesses({ work, sockets }).filter((p) => !p.unidentified && !frozen.has(key(p)));
      if (fresh.length === 0) break;
      signalProcesses(fresh, 'SIGSTOP');
      for (const p of fresh) frozen.set(key(p), p);
    }
    result.stopped = [...frozen.values()];
    signalProcesses(result.stopped, 'SIGKILL');
    const deadline = Date.now() + STOP_TIMEOUT_MS;
    for (;;) {
      const left = runProcesses({ work, sockets });
      if (left.length === 0) break;
      if (Date.now() > deadline) {
        result.ok = false;
        result.left = left;
        break;
      }
      signalProcesses(left.filter((p) => !p.unidentified), 'SIGKILL');
      await sleep(200);
    }
  } catch (error) {
    failed(result, `could not list the run's processes: ${error.message}`);
  }
  if (app !== undefined && process.platform === 'darwin') {
    try {
      result.launchServices = unregister(app);
      if (result.launchServices.left.length > 0) {
        failed(result, `LaunchServices still has ${result.launchServices.left.join(', ')}`);
      }
    } catch (error) {
      failed(result, `could not unregister ${app} from LaunchServices: ${error.message}`);
    }
  }
  // After the processes, so that the test's VS Code can no longer write the
  // domain.
  if (preferences !== undefined) {
    result.preferences = prefs.restore(preferences);
    if (result.preferences.outcome === 'failed') {
      failed(result, `could not put back ${prefs.DOMAIN}: ${result.preferences.error}`);
    }
  }
  return result;
}

module.exports = { create, readDescriptor, registrations, runProcesses, teardown, unregister };
