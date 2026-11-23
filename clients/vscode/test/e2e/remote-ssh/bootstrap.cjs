// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The extension tests that run.cjs starts in the remote extension host of a
// Remote-SSH window. VS Code doesn't pass the client's environment to the
// remote, so this module reads the launch's settings from run-config.json
// beside it, checks that it runs remotely in the VS Code Server, with the
// launch's workspace folders, and that VS Code loaded the extension installed
// from the VSIX there, gives suite.cjs the remote paths through the
// environment variables that run.cjs sets locally, and runs it unchanged. It
// writes what it found, and this extension host's PID and start time, to the
// launch's result file, first when it starts and again when it ends, so that
// run.cjs can tell a suite that failed from one that never started. With
// run.cjs --results, run-config.json names an observations file: this module
// records its own ssh.identity checks there, silently, as observations.cjs
// does, and suite.cjs appends its own checks to it (see remote-ssh/results.cjs).

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vscode = require('vscode');

const EXTENSION_ID = 'virgil-language-server.virgil-lsp-vscode';

// The directory of this extension host's logs, where its output channels
// write: the one whose remoteexthost.log names this process.
function hostLogs(root) {
  const started = `Extension host with pid ${process.pid} started`;
  const found = fs.readdirSync(root, { recursive: true })
    .filter((file) => path.basename(file) === 'remoteexthost.log')
    .map((file) => path.join(root, file))
    .filter((file) => fs.readFileSync(file, 'utf8').includes(started));
  assert.equal(found.length, 1, `expected one remoteexthost.log in ${root} that says "${started}"`);
  return path.dirname(found[0]);
}

// This process's start time, in clock ticks after boot: the 22nd field of
// /proc/self/stat. With the PID, it identifies the extension host to
// run.cjs, which stops the host if VS Code leaves it running. Null without
// /proc.
function startTime() {
  try {
    const stat = fs.readFileSync('/proc/self/stat', 'utf8');
    return stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19];
  } catch {
    return null;
  }
}

async function run() {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'run-config.json'), 'utf8'));
  const started = Date.now();
  const result = {
    launch: config.launch,
    outcome: 'started',
    started: new Date(started).toISOString(),
    pid: process.pid,
    start: startTime(),
    execPath: process.execPath,
    platform: `${process.platform}-${process.arch}`,
    osRelease: os.release(),
    remoteName: vscode.env.remoteName,
    vscodeVersion: vscode.version,
    initialVirgilLoc: process.env.VIRGIL_LOC ?? null,
    folders: vscode.workspace.workspaceFolders?.map((folder) => folder.uri.toString()) ?? null,
  };
  const write = () => fs.writeFileSync(config.result, `${JSON.stringify(result, null, 2)}\n`);
  write();
  // The check reporter, only with an observations file: it prints nothing, so
  // that run.cjs's count of the suite's PASS and FAIL lines stays the suite's.
  const checks = config.observations === undefined ? undefined
    : require('./observations.cjs').reporter({ file: config.observations, out: { log() {}, error() {} } });
  try {
    checks?.begin('ssh.identity.remote-host');
    assert.equal(vscode.env.remoteName, 'ssh-remote', 'the tests do not run in a Remote-SSH window');
    assert.equal(process.execPath, config.serverNode, 'the tests do not run in the VS Code Server under test');
    assert.equal(process.platform, 'linux');
    checks?.passed(`the tests run in a Remote-SSH window (remoteName ${vscode.env.remoteName}), in the VS Code Server's node, on ${process.platform}`);
    // suite.cjs runs its single-file tests when VS Code has no workspace
    // folders, and its workspace tests otherwise.
    checks?.begin('ssh.identity.folder-mode');
    assert.equal(JSON.stringify(result.folders), JSON.stringify(config.folders), `scenario mismatch: the ${config.mode} launch `
      + `has the workspace folders ${JSON.stringify(result.folders)}, not ${JSON.stringify(config.folders)}`);
    checks?.passed(config.folders === null ? 'the window has no workspace folders, as the launch has none'
      : 'the window\'s only workspace folder is the remote fixture');
    if (config.mode === 'probe') {
      result.outcome = 'pass';
      return;
    }
    checks?.begin('ssh.identity.extension-copy');
    const extension = vscode.extensions.getExtension(EXTENSION_ID);
    assert.ok(extension, `${EXTENSION_ID} is not loaded`);
    result.extension = {
      path: extension.extensionPath,
      version: extension.packageJSON.version,
      kind: extension.extensionKind === vscode.ExtensionKind.Workspace ? 'workspace' : extension.extensionKind,
    };
    assert.equal(extension.extensionPath, config.extensionPath, `${EXTENSION_ID} is not the copy installed from the VSIX`);
    assert.equal(extension.packageJSON.version, config.extensionVersion);
    assert.equal(extension.extensionKind, vscode.ExtensionKind.Workspace);
    checks?.passed(`VS Code loaded the copy installed from the VSIX, version ${extension.packageJSON.version}, with the Workspace extension kind`);
    checks?.begin('ssh.identity.host-log');
    result.logs = hostLogs(config.serverLogs);
    checks?.passed('exactly one remoteexthost.log names this extension host\'s PID');
    if (config.bundled) process.env.VIRGIL_E2E_BUNDLED = '1';
    else delete process.env.VIRGIL_E2E_BUNDLED;
    process.env.VIRGIL_E2E_PROJECT = config.project;
    process.env.VIRGIL_E2E_LOGS = result.logs;
    process.env.VIRGIL_E2E_EXTENSIONS_DIR = path.dirname(config.extensionPath);
    // Keep the host environment from overriding the library or launch mode.
    delete process.env.VIRGIL_LOC;
    delete process.env.VIRGIL_E2E_WORKSPACE_PART;
    delete process.env.VIRGIL_E2E_RECOVERY;
    // suite.cjs's reporter reads this when it is loaded.
    if (config.observations !== undefined) process.env.VIRGIL_E2E_OBSERVATIONS = config.observations;
    write();
    await require('./suite.cjs').run();
    result.outcome = 'pass';
  } catch (error) {
    result.outcome = 'fail';
    result.error = error.stack;
    // The identity check in progress, if any; a failure of the suite's own
    // was reported by its reporter.
    checks?.failed(error);
    throw error;
  } finally {
    result.seconds = (Date.now() - started) / 1000;
    write();
  }
}

module.exports = { run };
