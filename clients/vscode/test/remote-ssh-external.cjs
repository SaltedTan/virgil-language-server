// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Tests for the Remote-SSH test's prototype macOS client (#186): the external
// target that run.cjs --target reads, and the platform checks of run.cjs and
// host.cjs.

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const external = require('./e2e/remote-ssh/external.cjs');

const dir = path.join(__dirname, 'e2e', 'remote-ssh');

function descriptor(overrides = {}) {
  return {
    alias: 'virgil-remote-ssh-test',
    hostName: 'pc-ubuntu',
    address: '100.64.0.1',
    port: 40000,
    user: 'someone',
    hostKey: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKgtsQSbwp16HhrertEGgYDhrBlSSyKjlAMOnL1q+BY5 virgil-remote-ssh-host',
    hostKeySha256: 'SHA256:x',
    root: '/home/someone/w/vrsh-abc/remote',
    marker: 'VIRGIL_REMOTE_SSH_RUN=12-0123456789abcdef',
    node: '/usr/bin/node',
    ...overrides,
  };
}

function bundle(t, value) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'virgil-external-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'bundle'));
  fs.writeFileSync(path.join(root, 'bundle', 'target.json'), JSON.stringify(value));
  fs.writeFileSync(path.join(root, 'bundle', 'client'), 'KEY\n');
  fs.mkdirSync(path.join(root, 'work'));
  return { file: path.join(root, 'bundle', 'target.json'), work: path.join(root, 'work'), sockets: path.join(root, 'sockets') };
}

test('an external target pins the host key and uses only the bundle\'s key, in the work directory', async (t) => {
  const { file, work, sockets } = bundle(t, descriptor());
  const target = external.create({ file, work, sockets });
  await target.start();
  assert.equal(target.mode, 'external');
  assert.equal(target.root, '/home/someone/w/vrsh-abc/remote');
  assert.equal(target.node, '/usr/bin/node');
  const config = fs.readFileSync(target.sshConfig, 'utf8');
  assert.match(config, /^Host virgil-remote-ssh-test\n {2}HostName pc-ubuntu\n {2}Port 40000\n {2}User someone\n/);
  assert.match(config, new RegExp(`IdentityFile "${work}/ssh/client"`));
  assert.match(config, new RegExp(`UserKnownHostsFile "${work}/ssh/known_hosts"`));
  for (const line of ['IdentitiesOnly yes', 'IdentityAgent none', 'BatchMode yes', 'StrictHostKeyChecking yes', 'GlobalKnownHostsFile /dev/null']) {
    assert.ok(config.includes(`  ${line}\n`), line);
  }
  assert.doesNotMatch(config, /~|\.ssh\/(config|id_)/);
  assert.equal(fs.readFileSync(path.join(work, 'ssh', 'known_hosts'), 'utf8'), `[pc-ubuntu]:40000 ${descriptor().hostKey}\n`);
  assert.equal(fs.statSync(path.join(work, 'ssh', 'client')).mode & 0o777, 0o600);
});

test('an external target refuses a description it cannot use safely', async (t) => {
  for (const [name, overrides] of [
    ['a missing marker', { marker: undefined }],
    ['a marker of another form', { marker: 'PATH=/tmp' }],
    ['a root with a space', { root: '/home/some one/remote' }],
    ['a host name with a space', { hostName: 'pc ubuntu' }],
    ['a port that is not a number', { port: '22' }],
  ]) {
    await t.test(name, (tt) => {
      const { file } = bundle(tt, descriptor(overrides));
      assert.throws(() => external.readDescriptor(file));
    });
  }
});

test('run.cjs takes --target only on macOS, and --client-vsix only with --target', { skip: process.platform === 'darwin' }, () => {
  const run = (...args) => spawnSync(process.execPath, [path.join(dir, 'run.cjs'), ...args], { encoding: 'utf8' });
  const target = run('--vsix', 'x.vsix', '--target', 'target.json');
  assert.equal(target.status, 2);
  assert.match(target.stderr, /--target is a prototype for a macOS client, and runs on macOS only/);
  const client = run('--vsix', 'x.vsix', '--client-vsix', 'y.vsix');
  assert.equal(client.status, 2);
  assert.match(client.stderr, process.platform === 'linux' ? /--client-vsix needs --target/ : /runs on Linux only, or on macOS with --target/);
});

test('host.cjs requires its address, host name, and bundle', { skip: process.platform !== 'linux' }, () => {
  const result = spawnSync(process.execPath, [path.join(dir, 'host.cjs'), '--listen', '127.0.0.1'], { encoding: 'utf8' });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /--host-name is required/);
});

test('the focus guard reads LaunchServices notifications and app information', () => {
  const focus = require('./e2e/remote-ssh/focus.cjs');
  const line = 'Notification: kLSNotifyBecameFrontmost time=11/04/2026 00:20:21.675 dataRef={ "ApplicationType"="Foreground", '
    + '"CFBundleIdentifier"="com.microsoft.VSCode", "LSASN"=ASN:0x0-0x7f8af83:, "LSFrontApplicationSeed"=13526, '
    + '"LSOtherASN"=ASN:0x0-0x7f57f50: } affectedASN="Code" ASN:0x0-0x7f8af83:  context=0';
  assert.deepEqual(focus.parseNotification(line), { asn: 'ASN:0x0-0x7f8af83:', other: 'ASN:0x0-0x7f57f50:' });
  assert.equal(focus.parseNotification(line.replace('BecameFrontmost', 'LostFrontmost')), undefined);
  const info = '"T3 Code (Nightly)" ASN:0x0-0x7f57f50: (in front) \n    bundleID="com.t3tools.t3code"\n'
    + '    bundle path="/Applications/T3 Code (Nightly).app"\n    executable path="/Applications/T3 Code (Nightly).app/Contents/MacOS/T3"\n';
  assert.deepEqual(focus.parseInfo(info), { name: 'T3 Code (Nightly)', bundlePath: '/Applications/T3 Code (Nightly).app', bundleId: 'com.t3tools.t3code' });
  // Only the test's own copy counts, never the user's VS Code of the same bundle identifier.
  const app = '/Users/u/study/.vscode-test/vscode-darwin-arm64-1.142.0/Visual Studio Code.app';
  assert.ok(focus.within(app, app));
  assert.ok(focus.within(`${app}/Contents/Frameworks/Code Helper.app`, app));
  assert.ok(!focus.within('/Applications/Visual Studio Code.app', app));
  assert.ok(!focus.within(`${app} 2`, app));
  assert.ok(!focus.within(null, app));
});
