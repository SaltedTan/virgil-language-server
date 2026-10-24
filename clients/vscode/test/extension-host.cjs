// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The Extension Host test's copy of a failed launch's logs (keepLogs in
// e2e/run.cjs) and its retry decision (attempt, retryable, and windowError
// there), on launch directories that the checks write.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { attempt, keepLogs, retryable, windowError } = require('./e2e/run.cjs');

// A launch directory as VS Code leaves it, under a new directory that the
// test removes, and keepLogs's messages, once it has run.
function launchDir(t) {
  const root = fs.mkdtempSync(path.join(__dirname, '.extension-host-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const messages = [];
  t.mock.method(console, 'error', (message) => messages.push(message));
  return { root, dir: path.join(root, 'work', 'single-file'), messages };
}

function write(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
}

function files(dir) {
  return fs.readdirSync(dir, { recursive: true }).filter((file) => fs.statSync(path.join(dir, file)).isFile()).sort();
}

test('a failed launch keeps its VS Code and Chromium logs, and nothing else of the launch', (t) => {
  const { root, dir, messages } = launchDir(t);
  const logs = [
    'chromium.log',
    '20261024T010203/main.log',
    '20261024T010203/sharedprocess.log',
    '20261024T010203/window1/renderer.log',
    '20261024T010203/window1/exthost/exthost.log',
    '20261024T010203/window1/exthost/virgil-language-server.virgil-lsp-vscode/Virgil Language Server.log',
  ];
  for (const file of logs) write(path.join(dir, 'user-data', 'logs', file), file);
  write(path.join(dir, 'user-data', 'User', 'settings.json'), '{}');
  write(path.join(dir, 'user-data', 'CachedData', 'cache'), 'cache');
  write(path.join(dir, 'extensions', 'extension', 'package.json'), '{}');
  const target = path.join(root, 'kept', 'single-file');
  keepLogs(dir, target);
  assert.deepEqual(files(target), logs.map((file) => path.normalize(file)).sort());
  for (const file of logs) assert.equal(fs.readFileSync(path.join(target, file), 'utf8'), file);
  assert.deepEqual(messages, [`Kept the VS Code logs of the single-file launch in ${target}`]);
});

test('a launch without logs is reported, and keeps nothing', (t) => {
  const { root, dir, messages } = launchDir(t);
  fs.mkdirSync(path.join(dir, 'user-data'), { recursive: true });
  keepLogs(dir, path.join(root, 'kept', 'single-file'));
  assert.equal(fs.existsSync(path.join(root, 'kept')), false);
  assert.deepEqual(messages, [`No VS Code logs of the single-file launch to keep: ${path.join(dir, 'user-data', 'logs')} is missing`]);
});

test('a copy that fails is reported, not thrown', (t) => {
  const { root, dir, messages } = launchDir(t);
  write(path.join(dir, 'user-data', 'logs', 'chromium.log'), 'log');
  // The target's parent is a file.
  write(path.join(root, 'kept'), '');
  const target = path.join(root, 'kept', 'single-file');
  assert.doesNotThrow(() => keepLogs(dir, target));
  assert.equal(messages.length, 1);
  assert.ok(messages[0].startsWith(`Could not keep the VS Code logs of the single-file launch in ${target}: `), messages[0]);
});

// A launch directory and logs directory for attempt, under a new directory
// that the test removes, and attempt's messages, once it has run.
function attemptDir(t) {
  const { root, messages } = launchDir(t);
  return { root, work: path.join(root, 'work'), logs: path.join(root, 'logs'), messages };
}

// A failed launch at the window error, with the main log that records it.
function unresponsive(dir, { activated = false, error = 'an unresponsive window' } = {}) {
  write(path.join(dir, 'user-data', 'logs', '20261024T010203', 'main.log'), `[error] CodeWindow: ${
    error === 'a gone renderer process' ? 'renderer process gone (reason: crashed, code: 133)' : 'detected unresponsive'}\n`);
  return { passed: false, kind: 'launch', detail: 'exited with 1', exited: true, activated, windowError: error };
}

test('a launch that fails unresponsive before activation is retried once, and its pass reports the retry', async (t) => {
  const { work, logs, messages } = attemptDir(t);
  const calls = [];
  const launch = async (dir) => {
    calls.push(path.basename(dir));
    return calls.length === 1 ? unresponsive(dir) : { passed: true };
  };
  assert.deepEqual(await attempt({ name: 'single-file', work, logs, launch }), { passed: true, retried: true });
  assert.deepEqual(calls, ['single-file', 'single-file-retry']);
  assert.deepEqual(files(path.join(logs, 'single-file')), ['20261024T010203/main.log']);
  assert.equal(fs.existsSync(path.join(logs, 'single-file-retry')), false);
  assert.equal(messages.length, 2);
  assert.match(messages[1], /^RETRY: the single-file launch exited with 1 before the extension activated, and VS Code's main log records an unresponsive window; launching it once more, as single-file-retry\. Its logs are in /);
});

test('a launch that fails unresponsive after activation is not retried', async (t) => {
  const { work, logs, messages } = attemptDir(t);
  const calls = [];
  const launch = async (dir) => {
    calls.push(path.basename(dir));
    return unresponsive(dir, { activated: true });
  };
  assert.deepEqual(await attempt({ name: 'workspace', work, logs, launch }), { passed: false });
  assert.deepEqual(calls, ['workspace']);
  assert.deepEqual(files(path.join(logs, 'workspace')), ['20261024T010203/main.log']);
  assert.equal(fs.existsSync(path.join(logs, 'workspace-retry')), false);
  assert.equal(messages.length, 1);
  assert.match(messages[0], /^Kept the VS Code logs of the workspace launch in /);
});

test('a second failure at the window error fails the run, and keeps both attempts\' logs', async (t) => {
  const { work, logs, messages } = attemptDir(t);
  const calls = [];
  const launch = async (dir) => {
    calls.push(path.basename(dir));
    return unresponsive(dir);
  };
  assert.deepEqual(await attempt({ name: 'single-file', work, logs, launch }), { passed: false });
  assert.deepEqual(calls, ['single-file', 'single-file-retry']);
  assert.deepEqual(files(path.join(logs, 'single-file')), ['20261024T010203/main.log']);
  assert.deepEqual(files(path.join(logs, 'single-file-retry')), ['20261024T010203/main.log']);
  assert.equal(messages.length, 3);
  assert.match(messages[1], /^RETRY: /);
});

test('a launch that exits non-zero without the window error is not retried, as it may be a real failure', async (t) => {
  const { work, logs, messages } = attemptDir(t);
  const calls = [];
  const launch = async (dir) => {
    calls.push(path.basename(dir));
    write(path.join(dir, 'user-data', 'logs', '20261024T010203', 'main.log'), '[error] something else\n');
    return { passed: false, kind: 'launch', detail: 'exited with 1', exited: true, activated: false, windowError: undefined };
  };
  assert.deepEqual(await attempt({ name: 'single-file', work, logs, launch }), { passed: false });
  assert.deepEqual(calls, ['single-file']);
  assert.equal(messages.length, 1);
});

test('a retry without --logs keeps nothing and says where the logs would have gone', async (t) => {
  const { work, messages } = attemptDir(t);
  const calls = [];
  const launch = async (dir) => {
    calls.push(path.basename(dir));
    return calls.length === 1 ? unresponsive(dir) : { passed: true };
  };
  assert.deepEqual(await attempt({ name: 'single-file', work, logs: undefined, launch }), { passed: true, retried: true });
  assert.equal(messages.length, 1);
  assert.match(messages[0], /^RETRY: .*Its logs were not kept because --logs was not given\.$/);
});

test('only a launch that failed before activation, at the window error, is retried', () => {
  const flake = { kind: 'launch', exited: true, activated: false, windowError: 'an unresponsive window' };
  assert.equal(retryable(flake), true);
  assert.equal(retryable({ ...flake, windowError: 'a gone renderer process' }), true);
  assert.equal(retryable({ ...flake, activated: true }), false);
  assert.equal(retryable({ ...flake, windowError: undefined }), false);
  assert.equal(retryable({ ...flake, exited: false }), false);
  assert.equal(retryable({ ...flake, kind: 'installation' }), false);
  assert.equal(retryable({ ...flake, kind: 'recovery' }), false);
  assert.equal(retryable({ ...flake, kind: 'server' }), false);
});

test('the retry evidence is the main log\'s unresponsive window or gone renderer, not a recovery or a failed load', (t) => {
  const { root } = launchDir(t);
  const userData = path.join(root, 'launch', 'user-data');
  const main = path.join(userData, 'logs', '20261024T010203', 'main.log');
  write(main, '2026-10-24 16:26:27.296 [info] update#ctor - updates are disabled by the environment\n'
    + '2026-10-24 16:26:43.345 [error] CodeWindow: detected unresponsive\n');
  assert.equal(windowError(userData), 'an unresponsive window');
  write(main, '2026-10-24 16:26:43.345 [error] CodeWindow: recovered from unresponsive\n');
  assert.equal(windowError(userData), undefined);
  write(main, '2026-10-24 16:26:43.345 [error] CodeWindow: failed to load (reason: aborted, code: -3)\n');
  assert.equal(windowError(userData), undefined);
  write(main, '2026-10-24 16:26:43.345 [error] CodeWindow: renderer process gone (reason: crashed, code: 133)\n');
  assert.equal(windowError(userData), 'a gone renderer process');
  assert.equal(windowError(path.join(root, 'missing')), undefined);
});
