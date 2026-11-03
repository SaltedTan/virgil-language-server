// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Provisions a Remote-SSH host on this Linux machine for a client on another
// machine, such as a Mac, and takes it down again: localhost.cjs's SSH server
// of the test's own, without a display, listening on one address that the
// client can reach, such as this machine's Tailscale address, and accepting
// only the run's key, from allow-from if given. Usage, from clients/vscode:
//
//   node test/e2e/remote-ssh/host.cjs --listen ADDR --host-name NAME --bundle DIR
//     [--allow-from ADDR] [--work-dir DIR] [--sshd PATH]
//
// It writes the client's bundle into DIR, which must be empty or absent:
// target.json, which run.cjs --target reads, and the client's private key.
// Then it prints "READY DIR" and waits until its standard input closes or it
// gets SIGINT, SIGTERM, or SIGHUP. Then it stops everything of the run on this
// machine, by PID and start time (see teardown in localhost.cjs), writes
// host-teardown.json and sshd.log into DIR, and exits with 0 if the teardown
// confirmed that nothing of the run is left and the port is closed, 2 for bad
// arguments, and 3 otherwise.

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseArgs } = require('node:util');
const localhost = require('./localhost.cjs');

const usage = 'usage: node test/e2e/remote-ssh/host.cjs --listen ADDR --host-name NAME --bundle DIR '
  + '[--allow-from ADDR] [--work-dir DIR] [--sshd PATH]';

function options() {
  const { values } = parseArgs({
    options: {
      listen: { type: 'string' },
      'host-name': { type: 'string' },
      bundle: { type: 'string' },
      'allow-from': { type: 'string' },
      'work-dir': { type: 'string' },
      sshd: { type: 'string', default: '/usr/sbin/sshd' },
    },
  });
  if (process.platform !== 'linux') throw new Error('the Remote-SSH host runs on Linux only');
  for (const name of ['listen', 'host-name', 'bundle']) if (values[name] === undefined) throw new Error(`--${name} is required`);
  const cwd = process.env.INIT_CWD ?? process.cwd();
  const workDir = fs.realpathSync(path.resolve(cwd, values['work-dir'] ?? os.tmpdir()));
  if (!/^[A-Za-z0-9._/-]+$/.test(workDir)) throw new Error(`the work directory's path must have only letters, digits, and . _ / -: ${workDir}`);
  const bundle = path.resolve(cwd, values.bundle);
  if (fs.existsSync(bundle) && fs.readdirSync(bundle).length > 0) throw new Error(`the bundle directory is not empty: ${bundle}`);
  return { listen: values.listen, hostName: values['host-name'], bundle, allowFrom: values['allow-from'], workDir, sshd: values.sshd };
}

async function main() {
  let values;
  try {
    values = options();
  } catch (error) {
    console.error(`host.cjs: ${error.message}\n${usage}`);
    process.exit(2);
  }
  const marker = `VIRGIL_REMOTE_SSH_RUN=${process.pid}-${crypto.randomBytes(8).toString('hex')}`;
  // The sockets directory sits beside the work directory, not in /tmp, so that
  // nothing of the run is left in a directory that other users share.
  const work = fs.mkdtempSync(path.join(values.workDir, 'vrsh-'));
  const sockets = fs.mkdtempSync(path.join(values.workDir, 'vrss-'));
  const [markerName, markerValue] = marker.split('=');
  const env = { ...process.env, [markerName]: markerValue };
  for (const name of ['SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY']) delete env[name];
  const target = localhost.create({
    work, sockets, env, marker, sshd: values.sshd, listen: values.listen, display: false, allowFrom: values.allowFrom,
    log: (line) => console.log(line),
  });
  let stopping;
  const stop = () => {
    stopping ??= (async () => {
      const result = await target.teardown().catch((error) => ({ ok: false, stopped: [], left: [], portClosed: null, error: error.message }));
      fs.mkdirSync(values.bundle, { recursive: true });
      fs.writeFileSync(path.join(values.bundle, 'host-teardown.json'), `${JSON.stringify(result, null, 2)}\n`);
      for (const [name, file] of target.logs) if (fs.existsSync(file)) fs.copyFileSync(file, path.join(values.bundle, name));
      console.log(`-- host teardown: ${result.ok ? `stopped ${result.stopped.length} processes of the run; none left, and port ${target.details.port} is closed` : `could not confirm that nothing is left: ${JSON.stringify(result)}`}`);
      if (result.ok) {
        fs.rmSync(work, { recursive: true, force: true });
        fs.rmSync(sockets, { recursive: true, force: true });
      } else {
        console.log(`work directory kept: ${work}`);
      }
      process.exit(result.ok ? 0 : 3);
    })();
  };
  for (const name of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(name, stop);
  try {
    await target.start();
  } catch (error) {
    console.log(`INFRA: could not provision the host: ${error.message}`);
    stop();
    return;
  }
  fs.mkdirSync(values.bundle, { recursive: true, mode: 0o700 });
  fs.copyFileSync(path.join(work, 'ssh', 'client'), path.join(values.bundle, 'client'));
  fs.chmodSync(path.join(values.bundle, 'client'), 0o600);
  const descriptor = {
    alias: target.alias,
    hostName: values.hostName,
    address: values.listen,
    port: target.details.port,
    user: target.details.user,
    hostKey: target.hostKey,
    hostKeySha256: target.details.hostKeySha256,
    sshd: target.details.sshd,
    allowFrom: values.allowFrom ?? null,
    root: target.root,
    marker,
    // The node that run.cjs runs its process scans with on this machine
    // before the VS Code Server is installed.
    node: process.execPath,
  };
  fs.writeFileSync(path.join(values.bundle, 'target.json'), `${JSON.stringify(descriptor, null, 2)}\n`);
  console.log(`READY ${values.bundle}`);
  process.stdin.on('end', stop).on('close', stop).on('error', stop);
  process.stdin.resume();
}

main();
