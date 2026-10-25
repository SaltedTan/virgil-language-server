// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { processInfo, signalProcesses } = require('./e2e/processes.cjs');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Exercise the real runner and pinned downloader without a network download.
// Only HTTP responses and the cache location are replaced in the preload;
// the downloader itself spawns the tar shim through PATH.
test('SIGTERM to the Extension Host runner alone stops the first download and its tar', {
  skip: process.platform !== 'linux' && 'the tar probe uses Linux /proc', timeout: 30000,
}, async (t) => {
  const root = fs.mkdtempSync(path.join(__dirname, '.download-'));
  const marker = path.join(root, 'pids.json');
  const preload = path.join(root, 'preload.cjs');
  const log = fs.openSync(path.join(root, 'runner.log'), 'w');
  let child;
  let exited;
  let targets = [];
  t.after(async () => {
    if (child?.exitCode === null && child?.signalCode === null) {
      child.kill('SIGKILL');
      await exited;
    }
    signalProcesses(targets, 'SIGKILL');
    fs.closeSync(log);
    fs.rmSync(root, { recursive: true, force: true });
  });
  for (const name of ['virgil-lsp', 'virgil-lsp-worker']) {
    fs.writeFileSync(path.join(root, name), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  }
  fs.writeFileSync(path.join(root, 'tar'), `#!${process.execPath}
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const tar = spawn('/usr/bin/tar', ['-xf', '-'], { stdio: ['pipe', 'ignore', 'ignore'] });
fs.writeFileSync(${JSON.stringify(marker + '.tmp')}, JSON.stringify([process.pid, tar.pid]));
fs.renameSync(${JSON.stringify(marker + '.tmp')}, ${JSON.stringify(marker)});
setInterval(() => {}, 1000);
`, { mode: 0o755 });
  const electron = require.resolve('@vscode/test-electron/out/download');
  const request = require.resolve('@vscode/test-electron/out/request');
  fs.writeFileSync(preload, `
const { Readable } = require('node:stream');
const fs = require('node:fs');
const exists = fs.existsSync;
// Force a miss without changing or extracting into the checkout's cache.
fs.existsSync = (file) => String(file).startsWith(${JSON.stringify(path.resolve(__dirname, '../.vscode-test') + path.sep)}) ? false : exists(file);
const request = require(${JSON.stringify(request)});
request.getJSON = async () => [${JSON.stringify(require('./e2e/run.cjs').VSCODE_VERSION)}];
request.getStream = async (url) => url === 'https://example.invalid/code.tar.gz'
  ? Object.assign(Readable.from([Buffer.from('archive')]), { statusCode: 200, headers: { 'content-length': '7' } })
  : Object.assign(Readable.from([]), { statusCode: 302, headers: { location: 'https://example.invalid/code.tar.gz' } });
const electron = require(${JSON.stringify(electron)});
const download = electron.downloadAndUnzipVSCode;
electron.downloadAndUnzipVSCode = (options) => download({ ...options, cachePath: ${JSON.stringify(path.join(root, '.vscode-test'))} });
`);
  child = spawn(process.execPath, [path.join(__dirname, 'e2e/run.cjs'), path.join(root, 'virgil-lsp')], {
    env: { ...process.env, PATH: `${root}:${process.env.PATH}`, NODE_OPTIONS: `--require="${preload.replace(/[\\"]/g, '\\$&')}"` },
    stdio: ['ignore', log, log],
  });
  exited = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code, signal) => resolve({ code, signal }));
  });
  const deadline = Date.now() + 15000;
  while (!fs.existsSync(marker) && Date.now() < deadline && child.exitCode === null && child.signalCode === null) await sleep(20);
  assert.ok(fs.existsSync(marker), fs.readFileSync(path.join(root, 'runner.log'), 'utf8'));
  targets = JSON.parse(fs.readFileSync(marker)).map((pid) => ({ pid, start: processInfo(pid)?.start }));
  assert.ok(targets.every((target) => target.start !== undefined));
  child.kill('SIGTERM'); // Only the runner's PID, never its process group.
  const result = await exited;
  const running = targets.filter(({ pid, start }) => {
    const stat = processInfo(pid);
    return stat?.start === start && stat.state !== 'Z';
  });
  assert.deepEqual(running, [], `extractor processes remain after runner exit: ${JSON.stringify(result)}`);
  assert.deepEqual(result, { code: 143, signal: null });
});

// Keep the fast path tied to the pinned library's actual cache semantics.
test('a completed stable cache resolves exactly as test-electron does, without downloading', async (t) => {
  const root = fs.mkdtempSync(path.join(__dirname, '.download-cache-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const { cachedVSCode, VSCODE_VERSION } = require('./e2e/run.cjs');
  const { systemDefaultPlatform, downloadDirToExecutablePath } = require('@vscode/test-electron/out/util');
  const dir = path.join(root, `vscode-${systemDefaultPlatform}-${VSCODE_VERSION}`);
  assert.equal(cachedVSCode(root), undefined);
  fs.mkdirSync(dir);
  assert.equal(cachedVSCode(root), undefined);
  fs.writeFileSync(path.join(dir, 'is-complete'), '');
  // On macOS the resolver reads the app's product name from Info.plist.
  if (process.platform === 'darwin') {
    const contents = path.join(dir, 'Visual Studio Code.app', 'Contents');
    fs.mkdirSync(path.join(contents, 'MacOS'), { recursive: true });
    fs.writeFileSync(path.join(contents, 'MacOS', 'Code'), '');
    fs.writeFileSync(path.join(contents, 'Info.plist'), '<plist><dict><key>CFBundleExecutable</key><string>Code</string></dict></plist>');
  }
  const { downloadAndUnzipVSCode, SilentReporter } = require('@vscode/test-electron');
  assert.equal(cachedVSCode(root), await downloadAndUnzipVSCode({
    version: VSCODE_VERSION, cachePath: root, reporter: new SilentReporter(),
  }));
  assert.equal(cachedVSCode(root), downloadDirToExecutablePath(dir, systemDefaultPlatform));
});
