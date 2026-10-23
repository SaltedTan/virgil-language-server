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
// Each launch, including VS Code's own startup, must finish within this time,
// and so must each installation of a VSIX.
const LAUNCH_TIMEOUT_MS = 120000;
// How long a stopped process group gets after SIGTERM before SIGKILL.
const STOP_GRACE_MS = 5000;
// How long the servers that a launch started get to exit after VS Code has.
const SERVER_EXIT_MS = 5000;
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
  // From here on, an interrupt stops the running process group, and the
  // temporary directory is removed once the group has exited.
  const controller = new AbortController();
  const interrupt = () => controller.abort();
  process.on('SIGINT', interrupt).on('SIGTERM', interrupt);
  // Keep edits and VS Code state out of the checkout's fixture and the user's
  // home. Physical paths match the URIs that the server reports. VS Code's IPC
  // socket goes in --user-data-dir, and a Unix socket path has at most 103
  // characters on macOS, which its long os.tmpdir() under /var/folders exceeds.
  const base = process.platform === 'darwin' ? '/tmp' : os.tmpdir();
  const work = fs.mkdtempSync(path.join(fs.realpathSync(base), 'virgil-vscode-e2e-'));
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
      // Where the server offers no completion items, as in a comment, VS Code
      // would offer the document's words instead.
      'editor.wordBasedSuggestions': 'off',
    };
    console.log(`VS Code Extension Host test: VS Code ${VSCODE_VERSION}, ${server}`
      + (vsix === undefined ? '' : `, extension installed from ${vsix}`));
    const servers = [server, path.join(path.dirname(server), 'virgil-lsp-worker')];
    const options = { executable, settings, project, vsix, servers, signal: controller.signal };
    let passed = await launch(options, path.join(work, 'single-file'));
    if (passed) passed = await launch(options, path.join(work, 'workspace'), project);
    if (!passed) process.exitCode = 1;
  } finally {
    process.off('SIGINT', interrupt).off('SIGTERM', interrupt);
    fs.rmSync(work, { recursive: true, force: true });
  }
}

// The environment for VS Code and its command-line interface. A terminal
// inside VS Code sets VSCODE_* variables that describe that editor: with
// VSCODE_IPC_HOOK_CLI, as in a Remote-SSH terminal, bin/code hands the command
// to the running editor, which ignores --user-data-dir and --extensions-dir.
// None of them belong to the isolated instance. ELECTRON_RUN_AS_NODE would make
// Electron run as plain Node.js.
function isolatedEnv(variables) {
  const env = { ...process.env, ...variables };
  for (const name of Object.keys(env)) {
    if (name.startsWith('VSCODE_')) delete env[name];
  }
  delete env.ELECTRON_RUN_AS_NODE;
  // The suite checks that the Virgil root comes from the setting.
  delete env.VIRGIL_LOC;
  return env;
}

// Starts VS Code with its own user data and extensions directories under dir,
// and returns whether the suite passed. The suite opens a folder only if it is
// given one.
async function launch({ executable, settings, project, vsix, servers, signal }, dir, folder) {
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
  const env = isolatedEnv({
    VIRGIL_E2E_PROJECT: project,
    VIRGIL_E2E_LOGS: path.join(userData, 'logs'),
    // The suite checks that VS Code loaded the installed copy.
    ...(vsix === undefined ? {} : { VIRGIL_E2E_EXTENSIONS_DIR: extensions }),
  });
  const name = path.basename(dir);
  if (vsix !== undefined) {
    const installation = await runGroup(resolveCliPathFromVSCodeExecutablePath(executable), [
      `--user-data-dir=${userData}`,
      `--extensions-dir=${extensions}`,
      '--install-extension',
      vsix,
    ], {
      // Inside WSL, bin/code asks for confirmation unless this is set; with
      // no input, nothing can wait for an answer.
      env: { ...env, DONT_PROMPT_WSL_INSTALL: '1' },
      stdin: 'ignore',
      signal,
    });
    if (!succeeded(installation)) {
      console.error(`FAIL: ${name} installation of ${vsix} ${outcome(installation)}`);
      return false;
    }
  }
  const result = await runGroup(executable, args, { env, stdin: 'inherit', signal, servers });
  if (!succeeded(result)) {
    console.error(`FAIL: ${name} launch ${outcome(result)}`);
    printChannelLogs(userData);
    return false;
  }
  // Closing the window deactivates the extension, which sends shutdown and
  // exit. The channel may close before the server exits, so its log need not
  // record the exit.
  const exit = lastServerExit(userData);
  if (exit !== undefined && !exit.includes('Server process exited successfully')) {
    console.error(`FAIL: VS Code window close (${name} launch): the server did not exit cleanly: ${exit}`);
    printChannelLogs(userData);
    return false;
  }
  console.log(`PASS: VS Code window close (${name} launch): no virgil-lsp or virgil-lsp-worker left running, `
    + 'and no unclean server exit in the log');
  return true;
}

// Runs command in a process group of its own, so that a timeout or an
// interrupt can stop all of it: VS Code and the server, or the installer,
// which bin/code starts without exec on Linux. A stopped group gets SIGTERM,
// then SIGKILL, and the promise resolves only after the whole group has exited,
// before the caller removes the temporary directory. If the group still runs
// one of the servers SERVER_EXIT_MS after the command itself has exited, the
// group is stopped as well.
function runGroup(command, args, { env, stdin, signal, servers = [] }) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { env, stdio: [stdin, 'inherit', 'inherit'], detached: true });
    let stopped;
    let exited;
    let deadline;
    const finish = (result) => {
      clearTimeout(timer);
      clearInterval(poll);
      signal.removeEventListener('abort', interrupt);
      resolve({ ...result, stopped });
    };
    const stop = (reason) => {
      // Without a pid, the process didn't start, and its error event follows.
      if (stopped !== undefined || child.pid === undefined) return;
      stopped = reason;
      signalGroup(child.pid, 'SIGTERM');
      deadline = Date.now() + STOP_GRACE_MS;
    };
    const poll = setInterval(() => {
      if (exited !== undefined && !groupExists(child.pid)) {
        finish(exited);
      } else if (deadline !== undefined && Date.now() > deadline) {
        deadline = undefined;
        signalGroup(child.pid, 'SIGKILL');
      }
    }, 50);
    const timer = setTimeout(() => stop(`exceeded ${LAUNCH_TIMEOUT_MS / 1000} seconds`), LAUNCH_TIMEOUT_MS);
    const interrupt = () => stop('was interrupted');
    signal.addEventListener('abort', interrupt);
    child.on('spawn', () => {
      if (signal.aborted) interrupt();
    });
    child.on('error', (error) => finish({ error }));
    // VS Code starts the servers in its own group, where they outlive it only
    // if they don't exit.
    const expectServersGone = async () => {
      const limit = Date.now() + SERVER_EXIT_MS;
      for (;;) {
        let running;
        try {
          running = groupProcesses(child.pid, servers);
        } catch (error) {
          stop(`could not be checked for servers left running: ${error.message}`);
          return;
        }
        if (running.length === 0 || stopped !== undefined) return;
        if (Date.now() > limit) {
          const left = running.map((p) => `${path.basename(p.executable)} (pid ${p.pid})`).join(', ');
          stop(`left ${left} running ${SERVER_EXIT_MS / 1000} seconds after VS Code exited`);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    };
    child.on('exit', (code, exitSignal) => {
      exited = { code, signal: exitSignal };
      if (!groupExists(child.pid)) finish(exited);
      else if (servers.length > 0) void expectServersGone();
    });
  });
}

// The processes in process group pgid that run one of executables, by the
// first argument that ps lists: the client starts virgil-lsp, and virgil-lsp
// its worker, with an absolute path.
function groupProcesses(pgid, executables) {
  const ps = spawnSync('ps', ['-A', '-o', 'pid=', '-o', 'pgid=', '-o', 'args='], { encoding: 'utf8' });
  if (ps.status !== 0) throw new Error(`ps failed: ${ps.error?.message ?? ps.stderr}`);
  return ps.stdout.split('\n').flatMap((line) => {
    const [, pid, group, args] = /^\s*(\d+)\s+(\d+)\s+(.*)$/.exec(line) ?? [];
    const executable = executables.find((file) => args === file || args?.startsWith(`${file} `));
    return Number(group) === pgid && executable !== undefined ? [{ pid: Number(pid), executable }] : [];
  });
}

function signalGroup(pid, name) {
  try {
    process.kill(-pid, name);
  } catch (error) {
    if (error.code !== 'ESRCH') throw error;
  }
}

function groupExists(pid) {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    return error.code !== 'ESRCH';
  }
}

function succeeded(result) {
  return result.error === undefined && result.stopped === undefined && result.code === 0;
}

function outcome(result) {
  if (result.error !== undefined) return `could not start: ${result.error.message}`;
  return result.stopped ?? `exited with ${result.code ?? result.signal}`;
}

function channelLogs(userData) {
  const logs = path.join(userData, 'logs');
  return fs.existsSync(logs)
    ? fs.readdirSync(logs, { recursive: true }).filter((file) => path.basename(file) === CHANNEL_LOG)
      .map((file) => path.join(logs, file))
    : [];
}

// The output channel's log also holds the server's stderr.
function printChannelLogs(userData) {
  const files = channelLogs(userData);
  if (files.length === 0) console.error(`No "${CHANNEL_LOG}" in ${path.join(userData, 'logs')}`);
  for (const file of files) {
    console.error(`--- ${path.relative(path.join(userData, 'logs'), file)} ---`);
    console.error(fs.readFileSync(file, 'utf8'));
  }
}

// The line of the output channel's log that records how the last server to
// start exited, if any: the client's report of its exit status, or the
// server's error for input that ended before shutdown.
function lastServerExit(userData) {
  const lines = channelLogs(userData).flatMap((file) => fs.readFileSync(file, 'utf8').split('\n'));
  const start = lines.findLastIndex((line) => line.includes(' [info] Starting '));
  return lines.slice(start + 1).find((line) => /Server process exited|the input ended before a shutdown request/.test(line));
}

// The Remote-SSH test, remote-ssh/run.cjs, uses the same VS Code version and
// isolation.
if (require.main === module) {
  main().catch((error) => {
    console.error(`FAIL: ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = { VSCODE_VERSION, isolatedEnv };
