// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The Extension Host test's copy of a failed launch's logs (keepLogs in
// e2e/run.cjs), on launch directories that the checks write.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { keepLogs } = require('./e2e/run.cjs');

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
