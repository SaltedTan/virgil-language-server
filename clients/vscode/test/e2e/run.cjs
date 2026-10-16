// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Runs suite.cjs in a real VS Code Extension Host against the built server, on
// a copy of the Neovim smoke test's fixture: once with the fixture's bad.v3
// opened without a folder, and once with the fixture as the workspace folder.
// Usage, from clients/vscode after `npm run compile`:
//
//   node test/e2e/run.cjs [--vsix path/to/extension.vsix] [path/to/virgil-lsp]
//
// The default is build/virgil-lsp at the repository root. By default VS Code
// loads the extension from this directory as an extension under development.
// With --vsix, each launch instead installs the packaged extension into its
// empty extensions directory with --install-extension, as a user would, and
// runs the suite against the installed copy. Without a display, run it under
// xvfb-run on Linux.

const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseArgs } = require('node:util');
const { downloadAndUnzipVSCode, resolveCliPathFromVSCodeExecutablePath } = require('@vscode/test-electron');

// Keep the tested version in docs/compatibility.md in step.
const VSCODE_VERSION = '1.142.0';
// Each launch, including VS Code's own startup, must finish within this time.
const LAUNCH_TIMEOUT_MS = 120000;
const CHANNEL_LOG = 'Virgil Language Server.log';

const extension = path.resolve(__dirname, '../..');
const repo = path.resolve(extension, '../..');

async function main() {
  const { values, positionals } = parseArgs({ options: { vsix: { type: 'string' } }, allowPositionals: true });
  // npm runs scripts in clients/vscode; relative arguments are relative to
  // where npm was started.
  const cwd = process.env.INIT_CWD ?? process.cwd();
  const argument = path.resolve(cwd, positionals[0] ?? path.join(repo, 'build/virgil-lsp'));
  const vsix = values.vsix === undefined ? undefined : path.resolve(cwd, values.vsix);
  if (vsix !== undefined && !fs.existsSync(vsix)) {
    throw new Error(`package the extension before running the test: ${vsix} is missing`);
  }
  // The server starts the worker beside its own resolved path.
  const server = fs.existsSync(argument) ? fs.realpathSync(argument) : argument;
  for (const file of [server, path.join(path.dirname(server), 'virgil-lsp-worker')]) {
    try {
      fs.accessSync(file, fs.constants.X_OK);
    } catch {
      throw new Error(`build virgil-lsp and virgil-lsp-worker before running the test: ${file} is missing`);
    }
  }
  const executable = await downloadAndUnzipVSCode({
    version: VSCODE_VERSION,
    cachePath: path.join(extension, '.vscode-test'),
  });
  // Keep edits and VS Code state out of the checkout's fixture and the user's
  // home. Physical paths match the URIs that the server reports.
  const work = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'virgil-vscode-e2e-'));
  try {
    const project = path.join(work, 'project');
    fs.cpSync(path.join(repo, 'test/e2e/nvim/fixture'), project, { recursive: true });
    const settings = {
      'virgil.server.path': server,
      // The fixture project's virgilDependencies resolve against the pinned checkout.
      'virgil.virgilRoot': fs.realpathSync(path.join(repo, 'vendor/virgil')),
      'telemetry.telemetryLevel': 'off',
      'chat.disableAIFeatures': true,
      'update.mode': 'none',
      'workbench.startupEditor': 'none',
      'files.autoSave': 'off',
    };
    console.log(`VS Code Extension Host test: VS Code ${VSCODE_VERSION}, ${server}`
      + (vsix === undefined ? '' : `, extension installed from ${vsix}`));
    const options = { executable, settings, project, vsix };
    let passed = await launch(options, path.join(work, 'single-file'));
    if (passed) passed = await launch(options, path.join(work, 'workspace'), project);
    if (!passed) process.exitCode = 1;
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

// Starts VS Code with its own user data and extensions directories under dir,
// and returns whether the suite passed. The suite opens a folder only if it is
// given one.
async function launch({ executable, settings, project, vsix }, dir, folder) {
  const userData = path.join(dir, 'user-data');
  const extensions = path.join(dir, 'extensions');
  // VS Code runs the suite only in extension development mode. With a VSIX,
  // the development path is an empty directory, which holds no extension.
  const development = vsix === undefined ? extension : path.join(dir, 'no-extension');
  fs.mkdirSync(development, { recursive: true });
  fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
  fs.writeFileSync(path.join(userData, 'User', 'settings.json'), JSON.stringify(settings, null, 2));
  const args = [
    ...(folder === undefined ? [] : [folder]),
    `--user-data-dir=${userData}`,
    `--extensions-dir=${extensions}`,
    // Built-in extensions, such as JSON's, still load. An installed VSIX is
    // the only other extension in the extensions directory.
    ...(vsix === undefined ? ['--disable-extensions'] : []),
    '--disable-workspace-trust',
    '--disable-updates',
    '--skip-welcome',
    '--skip-release-notes',
    '--no-sandbox',
    '--disable-gpu-sandbox',
    `--extensionDevelopmentPath=${development}`,
    `--extensionTestsPath=${path.join(__dirname, 'suite.cjs')}`,
  ];
  const env = {
    ...process.env,
    VIRGIL_E2E_PROJECT: project,
    VIRGIL_E2E_LOGS: path.join(userData, 'logs'),
    // The suite checks that VS Code loaded the installed copy.
    ...(vsix === undefined ? {} : { VIRGIL_E2E_EXTENSIONS_DIR: extensions }),
  };
  // The suite checks that the Virgil root comes from the setting.
  delete env.VIRGIL_LOC;
  delete env.ELECTRON_RUN_AS_NODE;
  const name = path.basename(dir);
  if (vsix !== undefined && !install(executable, vsix, userData, extensions, env, name)) return false;
  return new Promise((resolve) => {
    // A process group of its own lets a timeout or an interrupt stop VS Code
    // and the server, before the temporary directory is removed.
    const child = spawn(executable, args, { env, stdio: 'inherit', detached: true });
    let stopped;
    const stop = (reason) => {
      stopped = reason;
      process.kill(-child.pid, 'SIGKILL');
    };
    const timer = setTimeout(() => stop(`exceeded ${LAUNCH_TIMEOUT_MS / 1000} seconds`), LAUNCH_TIMEOUT_MS);
    const interrupt = () => stop('was interrupted');
    process.on('SIGINT', interrupt).on('SIGTERM', interrupt);
    const done = (passed) => {
      clearTimeout(timer);
      process.off('SIGINT', interrupt).off('SIGTERM', interrupt);
      resolve(passed);
    };
    child.on('error', (error) => {
      console.error(`FAIL: could not start ${executable}: ${error.message}`);
      done(false);
    });
    child.on('exit', (code, signal) => {
      const passed = stopped === undefined && code === 0;
      if (!passed) {
        console.error(`FAIL: ${name} launch ${stopped ?? `exited with ${code ?? signal}`}`);
        printChannelLogs(userData);
      }
      done(passed);
    });
  });
}

// Installs the VSIX with the downloaded VS Code's command-line interface, and
// returns whether it succeeded.
function install(executable, vsix, userData, extensions, env, name) {
  const cli = resolveCliPathFromVSCodeExecutablePath(executable);
  const result = spawnSync(cli, [
    `--user-data-dir=${userData}`,
    `--extensions-dir=${extensions}`,
    '--install-extension',
    vsix,
  ], { env, stdio: 'inherit', timeout: LAUNCH_TIMEOUT_MS });
  if (result.status === 0) return true;
  console.error(`FAIL: ${name} installation of ${vsix} `
    + (result.error === undefined ? `exited with ${result.status ?? result.signal}` : `failed: ${result.error.message}`));
  return false;
}

// The output channel's log also holds the server's stderr.
function printChannelLogs(userData) {
  const logs = path.join(userData, 'logs');
  const files = fs.existsSync(logs)
    ? fs.readdirSync(logs, { recursive: true }).filter((file) => path.basename(file) === CHANNEL_LOG)
    : [];
  if (files.length === 0) console.error(`No "${CHANNEL_LOG}" in ${logs}`);
  for (const file of files) {
    console.error(`--- ${file} ---`);
    console.error(fs.readFileSync(path.join(logs, file), 'utf8'));
  }
}

main().catch((error) => {
  console.error(`FAIL: ${error.message}`);
  process.exitCode = 1;
});
