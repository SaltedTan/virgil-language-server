// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Runs suite.cjs in a real VS Code Extension Host against the built server, on
// a copy of the Neovim smoke test's fixture: once with the fixture's bad.v3
// opened without a folder, once with the fixture as the workspace folder, and
// once more with the folder to close the window while the client recovers
// from a server that the suite killed.
// Usage, from clients/vscode after `npm run compile`:
//
//   node test/e2e/run.cjs [--vsix path/to/extension.vsix] [--logs DIR] [path/to/virgil-lsp]
//
// Without --vsix the default is build/virgil-lsp at the repository root.
// With --vsix and no server argument, test the bundled server with no server
// or root setting and no external server on PATH. Pass a server argument
// for a universal VSIX. By default VS Code
// loads the extension from this directory as an extension under development.
// With --vsix, each launch instead installs the packaged extension into its
// empty extensions directory with --install-extension, as a user would, and
// runs the suite against the installed copy. With --logs, a new or empty
// directory, a launch or installation that fails leaves its VS Code and
// Chromium logs there (see keepLogs). A launch that fails before the extension
// activated, at an event at which VS Code's command-line extension test exits,
// is retried once (see attempt). Without a display, run it under xvfb-run on
// Linux.

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { parseArgs } = require('node:util');
const { resolveCliPathFromVSCodeExecutablePath } = require('@vscode/test-electron');
const { KEEPER_KILL_WAIT_MS, startGroup } = require('./processes.cjs');
const { fromShutdown } = require('./shutdown.cjs');

// Keep the tested version in docs/compatibility.md in step.
const VSCODE_VERSION = '1.142.0';
// Each launch, including VS Code's own startup, must finish within this time,
// and so must each installation of a VSIX.
const LAUNCH_TIMEOUT_MS = 120000;
// How long a stopped process group gets after SIGTERM before SIGKILL.
const STOP_GRACE_MS = 5000;
// How long a group gets to end after SIGKILL before the test gives up on it.
// SIGKILL ends a process at once, or as soon as it leaves an uninterruptible
// sleep, so this bounds only a group with a process that the test can't
// stop, such as one that its user may not signal.
const KILL_WAIT_MS = 30000;
// How long the servers that a launch started get to exit after VS Code has.
const SERVER_EXIT_MS = 5000;
const CHANNEL_LOG = 'Virgil Language Server.log';
// VS Code's log of its main process, among its logs.
const MAIN_LOG = 'main.log';
// Chromium's own log, among VS Code's, with --logs (see launch).
const CHROMIUM_LOG = 'chromium.log';

const extension = path.resolve(__dirname, '../..');
const repo = path.resolve(extension, '../..');

async function main() {
  const { values, positionals } = parseArgs({
    options: { vsix: { type: 'string' }, logs: { type: 'string' } },
    allowPositionals: true,
  });
  // npm runs scripts in clients/vscode; relative arguments are relative to
  // where npm was started.
  const cwd = process.env.INIT_CWD ?? process.cwd();
  const argument = path.resolve(cwd, positionals[0] ?? path.join(repo, 'build/virgil-lsp'));
  const vsix = values.vsix === undefined ? undefined : path.resolve(cwd, values.vsix);
  if (vsix !== undefined && !fs.existsSync(vsix)) {
    throw new Error(`package the extension before running the test: ${vsix} is missing`);
  }
  // New or empty, so that a run's logs never mix with another's; created only
  // when a launch fails.
  const logs = values.logs === undefined ? undefined : path.resolve(cwd, values.logs);
  if (logs !== undefined && fs.existsSync(logs) && !(fs.statSync(logs).isDirectory() && fs.readdirSync(logs).length === 0)) {
    throw new Error(`--logs takes a new or empty directory, and ${logs} is not one`);
  }
  // The server starts the worker beside its own resolved path.
  const bundled = vsix !== undefined && positionals.length === 0;
  const server = bundled ? undefined : fs.existsSync(argument) ? fs.realpathSync(argument) : argument;
  for (const file of server === undefined ? [] : [server, path.join(path.dirname(server), 'virgil-lsp-worker')]) {
    try {
      fs.accessSync(file, fs.constants.X_OK);
    } catch {
      throw new Error(`build virgil-lsp and virgil-lsp-worker before running the test: ${file} is missing`);
    }
  }
  // Install handlers before the download, which can start an extractor.
  const controller = new AbortController();
  const interrupt = (name) => {
    process.exitCode = name === 'SIGINT' ? 130 : 143;
    controller.abort(new Error(`interrupted by ${name}`));
  };
  const sigint = () => interrupt('SIGINT');
  const sigterm = () => interrupt('SIGTERM');
  process.on('SIGINT', sigint).on('SIGTERM', sigterm);
  let work;
  try {
    // Reuse Remote-SSH's download script, process group, and ten-minute
    // deadline. Load here: that runner imports this module's shared settings.
    const executable = cachedVSCode(path.join(extension, '.vscode-test')) ?? await require('./remote-ssh/run.cjs').downloadVSCode({
      env: process.env,
      signal: controller.signal,
      checkpoint: () => controller.signal.throwIfAborted(),
    });
    // Keep edits and VS Code state out of the checkout's fixture and the user's
    // home. Physical paths match the URIs that the server reports. VS Code's IPC
    // socket goes in --user-data-dir, and a Unix socket path has at most 103
    // characters on macOS, which its long os.tmpdir() under /var/folders exceeds.
    const base = process.platform === 'darwin' ? '/tmp' : os.tmpdir();
    work = fs.mkdtempSync(path.join(fs.realpathSync(base), 'virgil-vscode-e2e-'));
    const project = path.join(work, 'project');
    fs.cpSync(path.join(repo, 'test/e2e/nvim/fixture'), project, { recursive: true });
    const settings = {
      ...(bundled ? {} : {
        'virgil.server.path': server,
        'virgil.virgilRoot': fs.realpathSync(path.join(repo, 'vendor/virgil')),
      }),
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
    const servers = bundled ? [] : [server, path.join(path.dirname(server), 'virgil-lsp-worker')];
    const options = { executable, settings, project, vsix, servers, keep: logs !== undefined, signal: controller.signal };
    // A launch that fails returns a failure (see failure), or throws; one that
    // failed the way the unresponsive-window flake does before the extension
    // activated is retried once (see attempt).
    const run = (name, ...rest) => attempt({
      name,
      work,
      logs,
      launch: (dir) => launch(options, dir, ...rest),
    });
    let passed = (await run('single-file')).passed;
    if (passed) passed = (await run('workspace', project)).passed;
    if (passed) passed = (await run('recovery-close', project, true)).passed;
    if (!passed && !controller.signal.aborted) process.exitCode = 1;
  } finally {
    process.off('SIGINT', sigint).off('SIGTERM', sigterm);
    if (work !== undefined) fs.rmSync(work, { recursive: true, force: true });
  }
}

// The pinned downloader uses this directory and completion marker for a
// stable release. Resolve a completed cache in-process, so repeat runs pay
// no child-process or keeper startup cost. Incomplete extractions must go
// through the interruptible download. Use the library's platform and path
// resolution, including the current macOS app executable name.
function cachedVSCode(cache) {
  const { systemDefaultPlatform, downloadDirToExecutablePath } = require('@vscode/test-electron/out/util');
  const dir = path.join(cache, `vscode-${systemDefaultPlatform}-${VSCODE_VERSION}`);
  return fs.existsSync(path.join(dir, 'is-complete')) ? downloadDirToExecutablePath(dir, systemDefaultPlatform) : undefined;
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
// and returns the launch's outcome: whether the suite passed, or why it did
// not, with what the launch's logs say about an unresponsive window and
// whether the extension activated (see failure and retryable). The suite opens
// a folder only if it is given one. With recovery, the suite closes the
// window during a recovery, with a virgil.server.path in dir/recovery, whose
// records are checked after the window has closed. With keep, Chromium's own
// log goes among VS Code's logs, for keepLogs.
async function launch({ executable, settings, project, vsix, servers, keep, signal }, dir, folder, recovery = false) {
  const userData = path.join(dir, 'user-data');
  const extensions = path.join(dir, 'extensions');
  const records = recovery ? path.join(dir, 'recovery') : undefined;
  if (records !== undefined) fs.mkdirSync(records, { recursive: true });
  // VS Code runs the suite only in extension development mode. With a VSIX,
  // the development path is an empty directory, which holds no extension.
  const development = vsix === undefined ? extension : path.join(dir, 'no-extension');
  fs.mkdirSync(development, { recursive: true });
  fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
  // Breadcrumbs request document symbols when the recovered client registers
  // its provider, and VS Code requests inlay hints for the visible documents.
  // Such a request waits behind the client's pending didOpens, and a stop that
  // begins during the wait, as the window close here is, can send it after
  // shutdown through a race in vscode-languageclient (#212), failing the
  // recovery's strict shutdown sequence. The launch checks the recovery and
  // the stop, not UI work, so it turns the automatic requests off.
  const launchSettings = recovery
    ? { ...settings, 'breadcrumbs.enabled': false, 'editor.inlayHints.enabled': 'off' }
    : settings;
  fs.writeFileSync(path.join(userData, 'User', 'settings.json'), JSON.stringify(launchSettings, null, 2));
  // Chromium's log holds the renderer's console messages, which VS Code's
  // logs don't, and the warnings and errors of Chromium's processes, which
  // then no longer go to standard error. Electron reads these variables as
  // --enable-logging=file and --log-file, which VS Code would warn about on
  // every launch. The file's directory must exist before VS Code starts.
  const chromium = keep ? { ELECTRON_ENABLE_LOGGING: 'file', ELECTRON_LOG_FILE: path.join(userData, 'logs', CHROMIUM_LOG) } : {};
  if (keep) fs.mkdirSync(path.join(userData, 'logs'), { recursive: true });
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
    ...chromium,
    // The suite checks that VS Code loaded the installed copy.
    ...(vsix === undefined ? {} : { VIRGIL_E2E_EXTENSIONS_DIR: extensions }),
    ...(records === undefined ? {} : { VIRGIL_E2E_RECOVERY: records }),
  });
  if (vsix !== undefined && settings['virgil.server.path'] === undefined) {
    env.PATH = (env.PATH ?? '').split(path.delimiter).filter((entry) =>
      !fs.existsSync(path.join(entry, 'virgil-lsp'))).join(path.delimiter);
    env.VIRGIL_E2E_BUNDLED = '1';
  }
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
      return failure(userData, 'installation', outcome(installation));
    }
  }
  if (env.VIRGIL_E2E_BUNDLED) {
    const installed = fs.readdirSync(extensions).find((name) => name.startsWith('virgil-language-server.virgil-lsp-vscode-'));
    if (installed === undefined) throw new Error('installed extension is missing');
    servers = ['virgil-lsp', 'virgil-lsp-worker'].map((name) => path.join(extensions, installed, 'server', name));
    for (const file of servers) fs.accessSync(file, fs.constants.X_OK);
  }
  const result = await runGroup(executable, args, { env, stdin: 'inherit', signal, servers });
  if (!succeeded(result)) {
    console.error(`FAIL: ${name} launch ${outcome(result)}`);
    printChannelLogs(userData);
    return failure(userData, 'launch', outcome(result), result.error === undefined && result.stopped === undefined);
  }
  if (records !== undefined) {
    const document = pathToFileURL(path.join(project, 'bad.v3')).href;
    const problem = recoveryProblem(records, userData, document);
    if (problem !== undefined) {
      console.error(`FAIL: VS Code window close during the recovery (${name} launch): ${problem}`);
      printChannelLogs(userData);
      return failure(userData, 'recovery', problem);
    }
    console.log(`PASS: VS Code window close during the recovery (${name} launch): the recovery finished, the recovered server `
      + 'got a textDocument/didOpen for the open bad.v3 before exit, then shutdown and exit and exited with status 0, and no '
      + 'virgil-lsp or virgil-lsp-worker was left running');
    return { passed: true };
  }
  // Closing the window deactivates the extension, which sends shutdown and
  // exit. The channel may close before the server exits, so its log need not
  // record the exit.
  const exit = lastServerExit(userData);
  if (exit !== undefined && !exit.includes('Server process exited successfully')) {
    console.error(`FAIL: VS Code window close (${name} launch): the server did not exit cleanly: ${exit}`);
    printChannelLogs(userData);
    return failure(userData, 'server', exit);
  }
  console.log(`PASS: VS Code window close (${name} launch): no virgil-lsp or virgil-lsp-worker left running, `
    + 'and no unclean server exit in the log');
  return { passed: true };
}

// Runs command in a process group of its own, so that a timeout or an
// interrupt can stop all of it: VS Code and the server, or the installer,
// which bin/code starts without exec on Linux. A stopped group gets SIGTERM,
// then SIGKILL, and the promise resolves only after the whole group has exited,
// before the caller removes the temporary directory, or, if it hasn't
// KILL_WAIT_MS after SIGKILL, at once, with the result's stopped saying so,
// and detached from the group, so that the test can exit while the group,
// even its keeper, runs on (see detach in processes.cjs). Keeper release has
// its own bound, even after a clean exit (see release in processes.cjs).
// If the group still runs one of the servers SERVER_EXIT_MS after the
// command itself has exited, the group is stopped as well. The group's keeper
// (see startGroup in processes.cjs) keeps the group's number the run's until then,
// or until the group's SIGKILL, and sends the group the test's signals; if it
// goes before, the test signals only the processes that it saw in the group,
// each by its PID and start time.
function runGroup(command, args, { env, stdin, signal, servers = [] }) {
  return new Promise((resolve) => {
    let stopped;
    let exited;
    let deadline;
    // When the test gives up on the group, KILL_WAIT_MS after its SIGKILL.
    let abandon;
    let finished = false;
    let releasing = false;
    const group = startGroup(command, args, {
      env,
      stdio: [stdin, 'inherit', 'inherit'],
      onExit: (result) => {
        exited = result;
        if (!finish() && result.error === undefined && servers.length > 0) void expectServersGone();
      },
    });
    // Starts finishing once the command has exited and the group holds no
    // process but its keeper, or is no longer the run's. Waits for the bounded
    // keeper release; with anyway, resolves at once, without the keeper.
    // Returns whether finishing has started.
    const finish = (anyway = false) => {
      if (finished) return true;
      if (!anyway && releasing) return true;
      if (!anyway && (exited === undefined || group.look()?.empty === false)) return false;
      clearTimeout(timer);
      const done = () => {
        if (finished) return;
        finished = true;
        clearInterval(poll);
        signal.removeEventListener('abort', interrupt);
        resolve({ ...exited, stopped });
      };
      if (anyway) done();
      else {
        releasing = true;
        void group.release().then((gone) => {
          if (finished) return;
          if (!gone) stopped = `${stopped === undefined ? '' : `${stopped}, and `}its keeper (pid ${group.pid}) hadn't ended ${KEEPER_KILL_WAIT_MS / 1000} seconds after its release watchdog's SIGKILL`;
          done();
        });
      }
      return true;
    };
    const stop = (reason) => {
      // Without a pid, the keeper didn't start, and its result follows.
      if (stopped !== undefined || group.pid === undefined) return;
      stopped = reason;
      group.signal('SIGTERM');
      deadline = Date.now() + STOP_GRACE_MS;
    };
    const poll = setInterval(() => {
      if (deadline !== undefined && Date.now() > deadline) {
        deadline = undefined;
        group.signal('SIGKILL');
        abandon = Date.now() + KILL_WAIT_MS;
      } else if (abandon !== undefined && Date.now() > abandon) {
        stopped += `, and hadn't ended ${KILL_WAIT_MS / 1000} seconds after SIGKILL`;
        if (!group.detach()) stopped += `, nor had its keeper (pid ${group.pid})`;
        finish(true);
      } else finish();
    }, 50);
    const timer = setTimeout(() => stop(`exceeded ${LAUNCH_TIMEOUT_MS / 1000} seconds`), LAUNCH_TIMEOUT_MS);
    const interrupt = () => stop('was interrupted');
    signal.addEventListener('abort', interrupt);
    if (signal.aborted) interrupt();
    // VS Code starts the servers in its own group, where they outlive it only
    // if they don't exit. A look after each listing checks that the group is
    // still the run's, and so was during the listing, as it is never again
    // once it isn't (see startGroup in processes.cjs); once it isn't, another
    // group may have its number, and the check ends.
    const expectServersGone = async () => {
      const limit = Date.now() + SERVER_EXIT_MS;
      for (;;) {
        let running;
        try {
          const listed = groupProcesses(group.pid, servers);
          const scan = group.look();
          if (scan === undefined) return;
          running = scan.unavailable ? undefined : listed.filter((p) => scan.processes.some((member) => member.pid === p.pid));
        } catch (error) {
          stop(`could not be checked for servers left running: ${error.message}`);
          return;
        }
        if (running?.length === 0 || stopped !== undefined) return;
        if (Date.now() > limit) {
          const left = running?.map((p) => `${path.basename(p.executable)} (pid ${p.pid})`).join(', ');
          stop(left === undefined ? `could not check for servers left running ${SERVER_EXIT_MS / 1000} seconds after VS Code exited`
            : `left ${left} running ${SERVER_EXIT_MS / 1000} seconds after VS Code exited`);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    };
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

function succeeded(result) {
  return result.error === undefined && result.stopped === undefined && result.code === 0;
}

function outcome(result) {
  if (result.error !== undefined) return `could not start: ${result.error.message}`;
  return result.stopped ?? `exited with ${result.code ?? result.signal}`;
}

// A failed launch or installation, for attempt's retry decision (see
// retryable): what failed, how, whether it exited by itself, whether the
// extension activated, and the window error that VS Code's main log recorded.
// The extension creates its output channel before it starts the server, so a
// server or extension failure leaves the channel's log, and a launch without
// one ended before the extension activated.
function failure(userData, kind, detail, exited = false) {
  return {
    passed: false,
    kind,
    detail,
    exited,
    activated: channelLogs(userData).length > 0,
    windowError: windowError(userData),
  };
}

// Whether a failed launch may be the unresponsive-window flake that one retry
// may contain: a launch, not an installation or a check of its records, that
// exited by itself, before the extension activated, at an event at which
// VS Code's command-line extension test exits with status 1, as its main log
// records (see WINDOW_ERRORS). Any other failure, such as one that the test
// stopped or an exit with no such event, may be a real one, and is not
// retried.
function retryable({ kind, exited, activated, windowError }) {
  return kind === 'launch' && exited && !activated && windowError !== undefined;
}

// Runs the launch called name, and, if it fails as the unresponsive-window
// flake does before the extension activated (see retryable), the launch once
// more, as name-retry: never more than one retry per launch. launch resolves
// only after a failed launch's process group has gone, so nothing of it runs
// beside the retry (see runGroup). Each failed launch's logs go to
// logs/<name> or logs/<name>-retry, under a name of its own, before its
// temporary directory is removed (see keepLogs). Returns whether a launch
// passed, and whether that was the retry. Remote-SSH supplies its log keeper,
// remote teardown before a retry, and the description of its start evidence.
async function attempt({ name, work, logs, launch, saveLogs = keepLogs, beforeRetry = async () => {},
  started = 'the extension activated' }) {
  for (let tries = 1; ; tries++) {
    const attemptName = tries === 1 ? name : `${name}-retry`;
    const dir = path.join(work, attemptName);
    const target = logs === undefined ? undefined : path.join(logs, attemptName);
    let result;
    try {
      result = await launch(dir);
    } catch (error) {
      if (target !== undefined) await saveLogs(dir, target);
      throw error;
    }
    if (result.passed) return { passed: true, retried: tries > 1 };
    if (target !== undefined) await saveLogs(dir, target);
    if (tries > 1 || !retryable(result)) return { passed: false };
    await beforeRetry();
    const kept = target === undefined
      ? 'Its logs were not kept because --logs was not given'
      : `Its logs are in ${target}`;
    console.error(`RETRY: the ${attemptName} launch ${result.detail} before ${started}, and VS Code's main log `
      + `records ${result.windowError}; launching it once more, as ${name}-retry. ${kept}.`);
  }
}

function channelLogs(userData) {
  return logFiles(userData, CHANNEL_LOG);
}

// The files called name among the launch's VS Code logs, user-data/logs.
function logFiles(userData, name) {
  const logs = path.join(userData, 'logs');
  return fs.existsSync(logs)
    ? fs.readdirSync(logs, { recursive: true }).filter((file) => path.basename(file) === name)
      .map((file) => path.join(logs, file))
    : [];
}

// The window errors at which VS Code's command-line extension tests exit with
// status 1, in VS Code 1.142.0, each with the main log's wording: its
// mainImpl.js kills the test run at the window's first unresponsive event or
// gone renderer. "CodeWindow: recovered from unresponsive" and a failed load
// don't end the run, so they are no evidence of it.
const WINDOW_ERRORS = [
  ['an unresponsive window', 'CodeWindow: detected unresponsive'],
  ['a gone renderer process', 'CodeWindow: renderer process gone'],
];

// The window error among WINDOW_ERRORS that VS Code's main log recorded, if
// any, as its description.
function windowError(userData) {
  const lines = logFiles(userData, MAIN_LOG).flatMap((file) => fs.readFileSync(file, 'utf8').split('\n'));
  return WINDOW_ERRORS.find(([, marker]) => lines.some((line) => line.includes(marker)))?.[0];
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

// Copies the VS Code logs of the launch in dir, dir/user-data/logs, to
// target, and says where: the logs of the main process, the shared process,
// each window, and its extension host, with the output channels', and
// Chromium's (see launch). Nothing else of the launch, such as its
// extensions, caches, or workspace, is copied. A copy that fails is
// reported, and leaves the launch's result as it is.
function keepLogs(dir, target) {
  const name = path.basename(dir);
  const logs = path.join(dir, 'user-data', 'logs');
  try {
    if (!fs.existsSync(logs)) {
      console.error(`No VS Code logs of the ${name} launch to keep: ${logs} is missing`);
      return;
    }
    fs.mkdirSync(target, { recursive: true });
    fs.cpSync(logs, target, { recursive: true });
    console.error(`Kept the VS Code logs of the ${name} launch in ${target}`);
  } catch (error) {
    console.error(`Could not keep the VS Code logs of the ${name} launch in ${target}: ${error.message}`);
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

// What is wrong with the records of the recovery-close launch, if anything.
// The suite started the server from the script in directory, killed it, and
// closed the window while the client's recovery start was held, until the
// extension host logged that it was terminating; see closeDuringRecovery in
// suite.cjs. The output channel closes when the extension deactivates, before
// the recovered server stops, so the script's records of that server's input
// and exit status tell what reached it. The server must have received the
// didOpen for the open bad.v3, whose URI is document, before exit.
function recoveryProblem(directory, userData, document) {
  const recorded = (name) => {
    const file = path.join(directory, name);
    return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter((line) => line !== '') : [];
  };
  const starts = recorded('starts');
  if (starts.length !== 2) return `expected the killed server's start and the recovery's, not these starts: ${starts.join(', ')}`;
  const held = starts[1];
  const log = channelLogs(userData).map((file) => fs.readFileSync(file, 'utf8')).join('');
  if (!log.includes('Connection to server got closed. Server will restart.')) return 'the client did not start a recovery';
  const released = recorded('released').join(', ');
  if (released !== `${held} terminating`) {
    return `the recovery's start went on before the extension host was terminating, not just after (${released || 'never'})`;
  }
  const input = fs.readFileSync(path.join(directory, `input-${held}`));
  const received = messages(input);
  const methods = received.map((message) => message.method);
  if (methods[0] !== 'initialize' || !(methods.indexOf('initialized') > 0)) {
    return `the recovery did not finish before the window closed; the recovered server received ${methods.join(', ')}`;
  }
  if (fromShutdown(methods).join() !== 'shutdown,exit') {
    return `the recovered server did not receive shutdown and then exit last, but ${methods.join(', ')}`;
  }
  if (!openedBeforeExit(received, document)) {
    return `the recovered server got no textDocument/didOpen for the open bad.v3 before exit; it received ${methods.join(', ')}`;
  }
  const exits = recorded('exits').join(', ');
  if (exits !== `${held} 0`) return `the recovered server did not exit with status 0: ${exits || 'no exit recorded'}`;
  return undefined;
}

// The requests and notifications in input, a server's input as the script in
// suite.cjs copied it, in order, without the responses to the server's
// requests.
function messages(input) {
  const found = [];
  let rest = input;
  for (;;) {
    const end = rest.indexOf('\r\n\r\n');
    const length = Number(/Content-Length: *(\d+)/i.exec(rest.subarray(0, Math.max(end, 0)).toString())?.[1]);
    if (end < 0 || !(rest.length >= end + 4 + length)) return found;
    const message = JSON.parse(rest.subarray(end + 4, end + 4 + length).toString());
    if (message.method !== undefined) found.push(message);
    rest = rest.subarray(end + 4 + length);
  }
}

// Whether a server's input has a didOpen for uri before its last exit.
function openedBeforeExit(input, uri) {
  const exit = input.findLastIndex((message) => message.method === 'exit');
  return input.some((message, index) => index < exit
    && message.method === 'textDocument/didOpen' && message.params?.textDocument?.uri === uri);
}

// The Remote-SSH test, remote-ssh/run.cjs, uses the same VS Code version and
// isolation.
module.exports = { VSCODE_VERSION, cachedVSCode, attempt, failure, isolatedEnv, keepLogs, retryable, runGroup, windowError };

if (require.main === module) {
  main().catch((error) => {
    console.error(`FAIL: ${error.message}`);
    process.exitCode ??= 1;
  });
}
