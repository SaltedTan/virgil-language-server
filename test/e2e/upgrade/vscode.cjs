// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The VS Code side of one upgrade rehearsal phase, for run.py: installs the
// phase's VSIX into the rehearsal's one profile, then runs suite.cjs in a new
// VS Code Extension Host, which only observes the installed extension. It
// reuses the Extension Host test's isolation and process groups (see runGroup
// in clients/vscode/test/e2e/run.cjs) and never downloads VS Code. Usage:
//
//   node vscode.cjs locate           prints the cached VS Code executable
//   node vscode.cjs phase REQUEST    runs one phase; see run.py's Rehearsal.code

const fs = require('node:fs');
const path = require('node:path');

const e2e = path.resolve(__dirname, '../../../clients/vscode/test/e2e');
const { VSCODE_VERSION, cachedVSCode, isolatedEnv, runGroup } = require(path.join(e2e, 'run.cjs'));
const { resolveCliPathFromVSCodeExecutablePath } = require(require.resolve('@vscode/test-electron', { paths: [e2e] }));

const EXTENSION = 'virgil-language-server.virgil-lsp-vscode';
const CHANNEL_LOG = 'Virgil Language Server.log';
// Written into a new profile only; nothing configures the Virgil extension.
const SETTINGS = {
  'telemetry.telemetryLevel': 'off',
  'chat.disableAIFeatures': true,
  'update.mode': 'none',
  'extensions.autoUpdate': false,
  'extensions.autoCheckUpdates': false,
  'workbench.startupEditor': 'none',
  'files.autoSave': 'off',
};

function locate() {
  const executable = cachedVSCode(path.resolve(e2e, '../../.vscode-test'));
  if (executable === undefined) {
    throw new Error(`no cached VS Code ${VSCODE_VERSION}; run the Extension Host test once (see clients/vscode/README.md), `
      + 'or pass --vscode');
  }
  console.log(executable);
}

function outcome(result) {
  if (result.error !== undefined) return `could not start: ${result.error.message}`;
  if (result.stopped !== undefined) return result.stopped;
  return result.code === 0 ? undefined : `exited with ${result.code ?? result.signal}`;
}

async function phase(request) {
  const { executable, userData, extensions, vsix, force, project, expected, evidence, logs } = request;
  const controller = new AbortController();
  const interrupt = (name) => controller.abort(new Error(`interrupted by ${name}`));
  process.on('SIGINT', () => interrupt('SIGINT')).on('SIGTERM', () => interrupt('SIGTERM'));
  const signal = controller.signal;
  const settings = path.join(userData, 'User', 'settings.json');
  if (!fs.existsSync(settings)) {
    fs.mkdirSync(path.dirname(settings), { recursive: true });
    fs.writeFileSync(settings, JSON.stringify(SETTINGS, null, 2));
  }
  const env = isolatedEnv({});
  const profile = [`--user-data-dir=${userData}`, `--extensions-dir=${extensions}`];
  const cli = resolveCliPathFromVSCodeExecutablePath(executable);
  console.log(`Installing ${vsix}${force ? ' with --force' : ''}`);
  const install = await runGroup(cli, [...profile, '--install-extension', vsix, ...(force ? ['--force'] : [])], {
    env: { ...env, DONT_PROMPT_WSL_INSTALL: '1' }, stdin: 'ignore', signal,
  });
  if (outcome(install) !== undefined) throw new Error(`installing ${vsix} ${outcome(install)}`);
  const listed = await runGroup(cli, [...profile, '--list-extensions', '--show-versions'], { env, stdin: 'ignore', signal });
  if (outcome(listed) !== undefined) throw new Error(`listing the extensions ${outcome(listed)}`);
  // Every installed copy's server, so that none may outlive VS Code.
  const servers = fs.readdirSync(extensions).filter((name) => name.startsWith(`${EXTENSION}-`))
    .flatMap((name) => ['virgil-lsp', 'virgil-lsp-worker'].map((file) => path.join(extensions, name, 'server', file)));
  // VS Code runs a test only in extension development mode; this one holds no extension.
  const development = path.join(path.dirname(userData), 'no-extension');
  fs.mkdirSync(development, { recursive: true });
  const channelLogs = path.join(userData, 'logs');
  let result;
  try {
    result = await runGroup(executable, [
      project,
      ...profile,
      '--disable-workspace-trust',
      '--disable-updates',
      '--skip-welcome',
      '--skip-release-notes',
      '--no-sandbox',
      '--disable-gpu-sandbox',
      `--extensionDevelopmentPath=${development}`,
      `--extensionTestsPath=${path.join(__dirname, 'suite.cjs')}`,
    ], {
      env: {
        ...env,
        UPGRADE_EXPECTED: JSON.stringify(expected),
        UPGRADE_EXTENSIONS_DIR: extensions,
        UPGRADE_PROJECT: project,
        UPGRADE_EVIDENCE: evidence,
        UPGRADE_LOGS: channelLogs,
      },
      stdin: 'ignore',
      signal,
      servers,
    });
  } finally {
    // Keep this phase's VS Code logs, and start the next phase's afresh in
    // the same profile.
    if (fs.existsSync(channelLogs)) {
      fs.cpSync(channelLogs, logs, { recursive: true });
      fs.rmSync(channelLogs, { recursive: true, force: true });
    }
  }
  if (outcome(result) !== undefined) throw new Error(`VS Code ${outcome(result)}`);
  // The client logs how the last server it started exited, if the channel
  // was still open.
  const log = fs.existsSync(logs) ? fs.readdirSync(logs, { recursive: true })
    .filter((name) => path.basename(name) === CHANNEL_LOG)
    .map((name) => fs.readFileSync(path.join(logs, name), 'utf8')).join('') : '';
  const exit = log.split('\n').find((line) => line.includes('Server process exited'));
  if (exit !== undefined && !exit.includes('Server process exited successfully')) {
    throw new Error(`the server did not exit cleanly: ${exit}`);
  }
  console.log(`PASS: extension ${expected.extensionVersion} with its bundled server ${expected.serverVersion}; `
    + 'no virgil-lsp or virgil-lsp-worker left running');
}

const [command, request] = process.argv.slice(2);
(async () => {
  if (command === 'locate') locate();
  else if (command === 'phase') await phase(JSON.parse(request));
  else throw new Error('usage: node vscode.cjs locate | phase REQUEST');
})().catch((error) => {
  console.error(`FAIL: ${error.message}`);
  process.exitCode = 1;
});
