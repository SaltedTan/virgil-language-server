// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Tests for the Remote-SSH test's prototype macOS client (#186): the external
// target that run.cjs --target reads, the platform checks of run.cjs and
// host.cjs, and the focus and preferences guards.

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

test('an external target pins the host key and uses only the bundle\'s key, in the work directory', {
  skip: spawnSync('ssh', ['-V']).error?.code === 'ENOENT' ? 'ssh is not on PATH' : false,
}, async (t) => {
  const { file, work, sockets } = bundle(t, descriptor());
  const target = external.create({ file, work, sockets });
  await target.start();
  assert.equal(target.mode, 'external');
  assert.equal(target.root, '/home/someone/w/vrsh-abc/remote');
  assert.equal(target.node, '/usr/bin/node');
  const resolved = spawnSync('ssh', ['-G', '-F', target.sshConfig, target.alias], { encoding: 'utf8' });
  assert.equal(resolved.status, 0, resolved.stderr);
  const config = new Map();
  for (const line of resolved.stdout.trim().split('\n')) {
    const at = line.indexOf(' ');
    const key = line.slice(0, at);
    config.set(key, [...(config.get(key) ?? []), line.slice(at + 1)]);
  }
  for (const [key, value] of Object.entries({
    hostname: 'pc-ubuntu', port: '40000', user: 'someone', identityfile: path.join(work, 'ssh', 'client'),
    identitiesonly: 'yes', userknownhostsfile: path.join(work, 'ssh', 'known_hosts'), globalknownhostsfile: '/dev/null',
    batchmode: 'yes', identityagent: 'none', stricthostkeychecking: 'true',
  })) assert.deepEqual(config.get(key), [value], key);
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

// Tagged values, as prefs.cjs's Python gives them.
const tagged = {
  string: (value) => ({ string: value }),
  integer: (value) => ({ integer: String(value) }),
  real: (value) => ({ real: String(value) }),
  bool: (value) => ({ bool: value }),
  data: (value) => ({ data: Buffer.from(value).toString('base64') }),
  date: (value) => ({ date: value }),
  dict: (value) => ({ dict: value }),
  array: (...values) => ({ array: values }),
};

test('the preferences guard puts back only the keys that the run changed, added, or removed', () => {
  const prefs = require('./e2e/remote-ssh/prefs.cjs');
  const before = {
    kept: tagged.string('same'),
    checked: tagged.real('1793750000.5'),
    window: tagged.dict({ frame: tagged.string('0 0 800 600'), screens: tagged.array(tagged.integer(1), tagged.integer(2)) }),
    blob: tagged.data('\u0000\u0001'),
    when: tagged.date('2026-11-04T01:02:03'),
    gone: tagged.bool(true),
  };
  const after = {
    // Key order doesn't matter.
    window: tagged.dict({ screens: tagged.array(tagged.integer(1), tagged.integer(3)), frame: tagged.string('0 0 800 600') }),
    kept: tagged.string('same'),
    checked: tagged.real('1793758126.25'),
    blob: tagged.data('\u0000\u0002'),
    when: tagged.date('2026-11-04T02:00:00'),
    fresh: tagged.integer(7),
  };
  const plan = prefs.restorePlan(before, after);
  assert.deepEqual(plan.changed, ['blob', 'checked', 'when', 'window']);
  assert.deepEqual(plan.added, ['fresh']);
  assert.deepEqual(plan.removed, ['gone']);
  assert.deepEqual(plan.remove, ['fresh']);
  assert.deepEqual(Object.keys(plan.put), ['blob', 'checked', 'gone', 'when', 'window']);
  assert.ok(!Object.hasOwn(plan.put, 'kept'), 'an unchanged key is left alone');
  assert.ok(prefs.same(prefs.applyPlan(after, plan), before));
  assert.equal(prefs.describe(plan), 'changed blob, checked, when, window; added fresh; removed gone');
  // A value's type counts, not only its text.
  assert.deepEqual(prefs.compare({ n: tagged.integer(1) }, { n: tagged.real('1') }).changed, ['n']);
  assert.deepEqual(prefs.compare(before, structuredClone(before)), { changed: [], added: [], removed: [] });
  assert.equal(prefs.describe(prefs.compare(before, before)), 'no keys');
});

test('the preferences guard deletes every key of a domain that didn\'t exist before the run', () => {
  const prefs = require('./e2e/remote-ssh/prefs.cjs');
  // defaults export gives an empty dictionary for a domain that doesn't exist.
  const after = { SKPurchaseIntentUpdatesLastChecked: tagged.real('1793758126.25'), nested: tagged.dict({ a: tagged.bool(false) }) };
  const plan = prefs.restorePlan({}, after);
  assert.deepEqual(plan.added, ['SKPurchaseIntentUpdatesLastChecked', 'nested']);
  assert.deepEqual(plan.put, {});
  assert.deepEqual(prefs.applyPlan(after, plan), {});
});

test('the preferences guard\'s Python keeps each value\'s type through a property list', { skip: spawnSync('python3', ['-I', '-c', '']).status === 0 ? false : 'python3 is not on PATH' }, (t) => {
  const prefs = require('./e2e/remote-ssh/prefs.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'virgil-prefs-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const value = {
    s: tagged.string('ünïcode "quoted" <tag>'),
    i: tagged.integer('18446744073709551615'),
    r: tagged.real('0.1'),
    whole: tagged.real('2.0'),
    b: tagged.bool(false),
    d: tagged.data('\u0000ÿ binary'),
    t: tagged.date('2026-11-04T01:02:03'),
    a: tagged.array(tagged.dict({ deep: tagged.array(tagged.integer(-1)) }), tagged.string('x')),
    empty: tagged.dict({}),
  };
  const file = path.join(root, 'domain.plist');
  const python = (args, input) => spawnSync('python3', ['-I', '-c', prefs.PYTHON, ...args], { encoding: 'utf8', input });
  const write = python(['write', file], JSON.stringify({ dict: value }));
  assert.equal(write.status, 0, write.stderr);
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.match(fs.readFileSync(file, 'utf8'), /<real>2\.0<\/real>/);
  const read = python(['read', file]);
  assert.equal(read.status, 0, read.stderr);
  const back = JSON.parse(read.stdout).dict;
  assert.ok(prefs.same(back, value), JSON.stringify(back));
  assert.notEqual(python(['write', file], '{"dict":{}}').status, 0, 'never overwrites a file');
});

test('the preferences guard restores a domain through defaults', { skip: process.platform === 'darwin' ? false : 'macOS only' }, (t) => {
  // A domain given by its path, in a temporary directory, never VS Code's.
  const prefs = require('./e2e/remote-ssh/prefs.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'virgil-prefs-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const domain = path.join(root, 'domain');
  const run = (...args) => assert.equal(spawnSync('defaults', args).status, 0, args.join(' '));
  run('write', domain, 'kept', '-string', 'same');
  run('write', domain, 'changed', '-float', '1.5');
  run('write', domain, 'nested', '-dict', 'a', '-int', '1', 'b', '-int', '2');
  run('write', domain, 'gone', '-data', '0001');
  const options = { workDir: root, app: path.join(root, 'Test.app'), domain, bundleId: 'org.virgil-lsp.test.none', userApp: path.join(root, 'User.app') };
  const state = prefs.save(options);
  run('write', domain, 'changed', '-float', '2.5');
  run('write', domain, 'nested', '-dict', 'a', '-int', '1');
  run('delete', domain, 'gone');
  run('write', domain, 'fresh', '-date', '2026-11-04 01:02:03 +0000');
  const result = prefs.restore(state);
  assert.equal(result.outcome, 'restored', result.error);
  assert.deepEqual([result.changed, result.added, result.removed], [['changed', 'nested'], ['fresh'], ['gone']]);
  assert.equal(prefs.restore(prefs.save(options)).outcome, 'unchanged');
  for (const name of ['before.plist', 'after.plist', 'restore.plist', 'check.plist']) {
    assert.equal(fs.statSync(path.join(state.dir, name)).mode & 0o777, 0o600, name);
  }
});

test('the focus guard stops its listener and descendants after delayed notifications', { skip: process.platform !== 'linux' }, async (t) => {
  const focus = require('./e2e/remote-ssh/focus.cjs');
  const { processInfo, signalProcesses, zombie } = require('./e2e/processes.cjs');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'virgil-focus-'));
  const pids = path.join(root, 'pids.jsonl');
  const originalPath = process.env.PATH;
  const originalPids = process.env.FOCUS_TEST_PIDS;
  const originalDelay = process.env.FOCUS_TEST_DELAY;
  t.after(() => {
    for (const line of fs.readFileSync(pids, 'utf8').trim().split('\n')) {
      const identity = JSON.parse(line);
      signalProcesses([identity], 'SIGKILL');
    }
    process.env.PATH = originalPath;
    if (originalDelay === undefined) delete process.env.FOCUS_TEST_DELAY;
    else process.env.FOCUS_TEST_DELAY = originalDelay;
    if (originalPids === undefined) delete process.env.FOCUS_TEST_PIDS;
    else process.env.FOCUS_TEST_PIDS = originalPids;
    fs.rmSync(root, { recursive: true, force: true });
  });
  fs.writeFileSync(pids, '');
  fs.writeFileSync(path.join(root, 'lsappinfo'), `#!${process.execPath}
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const { processInfo } = require(${JSON.stringify(path.join(__dirname, 'e2e/processes.cjs'))});
const record = (pid) => fs.appendFileSync(process.env.FOCUS_TEST_PIDS, JSON.stringify(processInfo(pid)) + '\\n');
record(process.pid);
if (process.argv[2] === 'listen') {
  process.on('SIGTERM', () => {});
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  record(child.pid);
  if (process.env.FOCUS_TEST_DELAY === '1') for (let i = 0; i < 3; i++) console.log('Notification: kLSNotifyBecameFrontmost "LSASN"=ASN:0x0-0x1:');
}
setInterval(() => {}, 1000);
`, { mode: 0o700 });
  process.env.PATH = `${root}:${originalPath}`;
  process.env.FOCUS_TEST_PIDS = pids;
  for (const delay of ['0', '1']) {
    fs.writeFileSync(pids, '');
    process.env.FOCUS_TEST_DELAY = delay;
    const guard = await focus.spawnGuard({ app: path.join(root, 'Test.app'), work: root });
    const result = await guard.stop();
    assert.equal(result.cleanupOk, true, JSON.stringify(result));
    if (delay === '1') {
      assert.ok(result.errors.includes('the focus guard exited without a result'), 'queued lookups forced the stop timeout');
    } else {
      assert.deepEqual(result.errors, []);
    }
    const identities = fs.readFileSync(pids, 'utf8').trim().split('\n').map(JSON.parse);
    assert.ok(identities.length >= (delay === '1' ? 3 : 2), 'the listener and its descendants ran');
    for (const identity of identities) {
      const current = processInfo(identity.pid);
      assert.ok(current === undefined || current.start !== identity.start || zombie(current), `pid ${identity.pid} survived`);
    }
  }
});

test('unrestored preference reasons report key names for failures and skips', () => {
  const { notRestoredReason } = require('./e2e/remote-ssh/prefs.cjs');
  for (const outcome of ['failed', 'skipped']) {
    assert.equal(notRestoredReason({ outcome, changed: ['z'], added: ['a'], removed: ['b'] }),
      'the VS Code preferences were not restored: a, b, z');
  }
  for (const outcome of ['restored', 'unchanged']) assert.equal(notRestoredReason({ outcome }), undefined);
  assert.equal(notRestoredReason(undefined), undefined);
});

test('the Mac driver reports evidence failures without overriding FAIL or interrupts', { skip: process.platform !== 'linux' }, async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'virgil-mac-driver-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const bin = path.join(root, 'bin');
  fs.mkdirSync(bin);
  const tool = (name, source) => fs.writeFileSync(path.join(bin, name), `#!${process.execPath}\n${source}`, { mode: 0o700 });
  tool('node', `
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const args = process.argv.slice(2);
if (args[0].endsWith('/host.cjs')) {
  const bundle = args[args.indexOf('--bundle') + 1];
  fs.mkdirSync(bundle);
  for (const name of ['client', 'target.json']) fs.writeFileSync(path.join(bundle, name), '{}');
  console.log('READY ' + bundle);
  process.stdin.resume();
  process.stdin.on('end', () => {
    fs.writeFileSync(path.join(bundle, 'host-teardown.json'), '{"ok":true}');
    fs.writeFileSync(path.join(bundle, 'sshd.log'), 'host log');
  });
} else {
  if (process.env.DRIVER_SCENARIO === 'assembly') process.exit(1);
  const result = spawnSync(process.execPath, args, { input: fs.readFileSync(0), stdio: ['pipe', 'inherit', 'inherit'] });
  process.exit(result.status);
}
`);
  tool('ssh', `process.exit(process.argv.at(-1).includes('exec node') ? Number(process.env.DRIVER_STATUS) : 0);`);
  tool('scp', `
const fs = require('node:fs');
const path = require('node:path');
if (!process.argv.includes('-r')) process.exit(0);
if (process.env.DRIVER_SCENARIO === 'transfer') process.exit(1);
const out = process.argv.at(-1);
fs.mkdirSync(out);
if (process.env.DRIVER_SCENARIO === 'missing') process.exit(0);
const preference = process.env.DRIVER_SCENARIO;
const status = Number(process.env.DRIVER_STATUS);
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify({
  result: status === 0 ? 'PASS' : 'FAIL', reasons: [],
  teardown: { preferences: { outcome: preference, changed: ['SKPurchaseIntentUpdatesLastChecked'], added: [], removed: [] } },
}));
`);
  tool('cp', `
const { spawnSync } = require('node:child_process');
if (process.env.DRIVER_SCENARIO === 'copy') process.exit(1);
process.exit(spawnSync('cp', process.argv.slice(2), { env: { ...process.env, PATH: process.env.DRIVER_ORIGINAL_PATH } }).status);
`);
  for (const [scenario, status, expected, reason] of [
    ['ok', 0, 0],
    ['transfer', 0, 3, 'could not copy the Mac results'],
    ['copy', 0, 3, 'could not copy the host evidence'],
    ['missing', 0, 3, 'the Mac wrote no manifest'],
    ['assembly', 0, 3, 'could not assemble the combined manifest'],
    ['transfer', 1, 1, 'could not copy the Mac results'],
    ['assembly', 130, 130, 'could not assemble the combined manifest'],
    ['copy', 143, 143, 'could not copy the host evidence'],
    ['failed', 1, 1, 'the VS Code preferences were not restored: SKPurchaseIntentUpdatesLastChecked'],
    ['failed', 0, 3, 'the VS Code preferences were not restored: SKPurchaseIntentUpdatesLastChecked'],
    ['skipped', 0, 0, 'the VS Code preferences were not restored: SKPurchaseIntentUpdatesLastChecked'],
  ]) {
    await t.test(`${scenario}, Mac status ${status}`, () => {
      const out = path.join(root, `${scenario}-${status}`);
      const result = spawnSync('bash', [path.join(dir, 'mac-client.sh'), '--mac', 'test-mac', '--study', '/study',
        '--listen', '127.0.0.1', '--host-name', 'test-host', '--allow-from', '127.0.0.1', '--vsix', 'test.vsix',
        '--work-dir', root, '--out', out], {
        encoding: 'utf8', timeout: 15000,
        env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, DRIVER_ORIGINAL_PATH: process.env.PATH,
          DRIVER_SCENARIO: scenario, DRIVER_STATUS: String(status) },
      });
      assert.equal(result.status, expected, result.stdout + result.stderr);
      const manifest = JSON.parse(fs.readFileSync(path.join(out, 'manifest.json'), 'utf8'));
      assert.equal(manifest.exitStatus, expected);
      assert.equal(manifest.result, { 0: 'PASS', 1: 'FAIL', 3: 'INFRA', 130: 'INTERRUPTED', 143: 'INTERRUPTED' }[expected]);
      if (reason !== undefined) assert.ok(manifest.reasons.includes(reason), JSON.stringify(manifest));
      if (['failed', 'skipped'].includes(scenario)) assert.match(result.stdout + result.stderr, /^PREFERENCES NOT RESTORED:/m);
    });
  }
});


test('host.cjs tears down its listening target after publication and cleanup failures', { skip: process.platform !== 'linux' }, async (t) => {
  const { processInfo, signalProcesses, zombie } = require('./e2e/processes.cjs');
  const net = require('node:net');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'virgil-host-failures-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const daemon = path.join(root, 'sshd');
  fs.writeFileSync(daemon, `#!${process.execPath}
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { processInfo } = require(${JSON.stringify(path.join(__dirname, 'e2e/processes.cjs'))});
if (process.argv.includes('-t')) process.exit(0);
const file = process.argv[process.argv.indexOf('-f') + 1];
const config = new Map(fs.readFileSync(file, 'utf8').split('\\n').map((line) => {
  const at = line.indexOf(' ');
  return [line.slice(0, at), line.slice(at + 1)];
}));
const server = net.createServer((socket) => socket.end('SSH-2.0-host-cleanup-test\\r\\n'));
server.listen(Number(config.get('Port')), config.get('ListenAddress'), () => {
  fs.writeFileSync(path.join(process.env.HOST_TEST_ROOT, 'server.json'), JSON.stringify({
    ...processInfo(process.pid), port: server.address().port,
  }));
});
`, { mode: 0o700 });
  const preload = path.join(root, 'faults.cjs');
  fs.writeFileSync(preload, `
const fs = require('node:fs');
const path = require('node:path');
const root = process.env.HOST_TEST_ROOT;
const scenario = process.env.HOST_TEST_SCENARIO;
const bundle = path.join(root, 'bundle');
let bundleDirectories = 0;
for (const name of ['mkdirSync', 'copyFileSync', 'chmodSync', 'writeFileSync', 'rmSync']) {
  const operation = fs[name];
  fs[name] = (...args) => {
    const file = String(args[name === 'copyFileSync' ? 1 : 0]);
    if (name === 'mkdirSync' && file === bundle) bundleDirectories++;
    const removal = name === 'rmSync' && ['vrsh-', 'vrss-'].some((prefix) => path.basename(file).startsWith(prefix));
    if (file === bundle || file.startsWith(bundle + '/') || removal) {
      fs.appendFileSync(path.join(root, 'operations.jsonl'), JSON.stringify({ name, file }) + '\\n');
    }
    const fail = (scenario === 'publication-directory' && name === 'mkdirSync' && file === bundle)
      || (scenario === 'publication-key' && name === 'copyFileSync' && file === path.join(bundle, 'client'))
      || (scenario === 'publication-mode' && name === 'chmodSync' && file === path.join(bundle, 'client'))
      || (scenario === 'publication-descriptor' && name === 'writeFileSync' && file === path.join(bundle, 'target.json'))
      || (scenario === 'evidence-directory' && name === 'mkdirSync' && file === bundle && bundleDirectories === 2)
      || (['evidence-manifest', 'multiple'].includes(scenario) && name === 'writeFileSync' && file === path.join(bundle, 'host-teardown.json'))
      || (scenario === 'evidence-log' && name === 'copyFileSync' && file === path.join(bundle, 'sshd.log'))
      || (['remove-work', 'multiple'].includes(scenario) && name === 'rmSync' && path.basename(file).startsWith('vrsh-'))
      || (scenario === 'remove-sockets' && name === 'rmSync' && path.basename(file).startsWith('vrss-'));
    if (fail) throw new Error('injected ' + scenario + ' failure');
    return operation(...args);
  };
}
`);
  for (const scenario of ['ordinary', 'startup', 'publication-directory', 'publication-key', 'publication-mode',
    'publication-descriptor', 'evidence-directory', 'evidence-manifest', 'evidence-log', 'remove-work', 'remove-sockets', 'multiple']) {
    await t.test(scenario, async (tt) => {
      const workDir = path.join(root, scenario);
      fs.mkdirSync(workDir);
      const serverFile = path.join(workDir, 'server.json');
      tt.after(() => {
        if (fs.existsSync(serverFile)) signalProcesses([JSON.parse(fs.readFileSync(serverFile, 'utf8'))], 'SIGKILL');
      });
      const result = spawnSync(process.execPath, ['--require', preload, path.join(dir, 'host.cjs'),
        '--listen', '127.0.0.1', '--host-name', 'test-host', '--bundle', path.join(workDir, 'bundle'),
        '--work-dir', workDir, '--sshd', scenario === 'startup' ? path.join(root, 'missing-sshd') : daemon], {
        encoding: 'utf8', input: '', timeout: 15000,
        env: { ...process.env, HOST_TEST_ROOT: workDir, HOST_TEST_SCENARIO: scenario },
      });
      assert.equal(result.status, scenario === 'ordinary' ? 0 : 3, result.stdout + result.stderr);
      if (scenario !== 'startup') {
        const server = JSON.parse(fs.readFileSync(serverFile, 'utf8'));
        const current = processInfo(server.pid);
        assert.ok(current === undefined || current.start !== server.start || zombie(current), `pid ${server.pid} survived`);
        const open = await new Promise((resolve) => {
          const socket = net.connect(server.port, '127.0.0.1');
          socket.once('connect', () => { socket.destroy(); resolve(true); });
          socket.once('error', () => resolve(false));
          socket.setTimeout(1000, () => { socket.destroy(); resolve(true); });
        });
        assert.equal(open, false, 'the target must no longer listen');
      }
      const operations = fs.readFileSync(path.join(workDir, 'operations.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
      const removals = operations.filter(({ name }) => name === 'rmSync');
      assert.equal(removals.length, 2, 'both directory removals must be attempted');
      for (const { file } of removals) {
        const retained = (['remove-work', 'multiple'].includes(scenario) && path.basename(file).startsWith('vrsh-'))
          || (scenario === 'remove-sockets' && path.basename(file).startsWith('vrss-'));
        assert.equal(fs.existsSync(file), retained, file);
      }
      assert.ok(operations.some(({ name, file }) => name === 'writeFileSync' && path.basename(file) === 'host-teardown.json'));
      if (scenario !== 'startup') assert.ok(operations.some(({ name, file }) => name === 'copyFileSync' && path.basename(file) === 'sshd.log'));
      if (!['publication-directory', 'evidence-manifest', 'multiple'].includes(scenario)) {
        const evidence = JSON.parse(fs.readFileSync(path.join(workDir, 'bundle', 'host-teardown.json'), 'utf8'));
        assert.equal(evidence.ok, true);
        assert.equal(evidence.portClosed, scenario === 'startup' ? null : true);
      }
    });
  }
});
