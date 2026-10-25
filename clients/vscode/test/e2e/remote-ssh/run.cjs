// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Runs the Extension Host suite, ../suite.cjs, unchanged, in the remote
// extension host of a VS Code Remote-SSH window: official VS Code with
// Microsoft's Remote-SSH extension connects to an SSH host, the packaged
// extension is installed on that host with the VS Code Server's own CLI, and
// the language server runs there. Usage, from clients/vscode on Linux:
//
//   node test/e2e/remote-ssh/run.cjs --vsix PATH [--bundled | --server PATH | --archive PATH]
//     [--virgil-root DIR] [--out DIR] [--repeat N] [--work-dir DIR] [--keep-work]
//     [--sshd PATH]
//
// localhost.cjs provisions the host: an SSH server of the test's own on
// 127.0.0.1, under the same user, with its own keys, and a private X display.
// Everything after provisioning reaches the host only through SSH, and
// bootstrap.cjs runs the suite there. See the README's "Remote-SSH test".
//
// Exit status: 0 if every check passed, 1 if a check failed (the suite, the
// remote installation, or a language server left running), 2 for bad
// arguments, 3 for an infrastructure failure (a prerequisite, a download, the
// SSH server, the connection, or the evidence), 130 or 143 when interrupted,
// even during the log collection or the teardown, which an interrupt waits
// for. The last line of output, and manifest.json in the output directory,
// say which.

const { execFileSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseArgs } = require('node:util');
const zlib = require('node:zlib');
const { resolveCliPathFromVSCodeExecutablePath } = require('@vscode/test-electron');
const { VSCODE_VERSION, attempt, isolatedEnv, windowError } = require('../run.cjs');
const { KEEPER_KILL_WAIT_MS, PIDFD_SIGNAL, SETTLED, isZombie, pidfdSupport, processFiles, runOwned, startGroup } = require('../processes.cjs');
const localhost = require('./localhost.cjs');

// The Remote-SSH extension that the client installs from the Marketplace,
// and the members of its extension pack, which VS Code's CLI would otherwise
// install at their latest versions, with the SHA-256 of each VSIX that the CLI
// downloads. Recheck the connection settings in clientSettings when changing
// Remote-SSH's version.
const REMOTE_SSH = [
  { id: 'ms-vscode-remote.remote-ssh-edit', version: '0.87.0', sha256: 'c9e5fa440265d3b77e4ae63219014b64d714cd5280b263c5c932849fecb71ae3' },
  { id: 'ms-vscode.remote-explorer', version: '0.5.0', sha256: '04db277ada5d771bf763d323644454e633aad48da0e81345175ac3ec08299abf' },
  { id: 'ms-vscode-remote.remote-ssh', version: '0.129.2026100915', sha256: '11b085290fea80fb486f67679802b47dc52cb1107bd1f1f91f888b5ae33287d5' },
];
const REMOTE_SSH_VERSION = REMOTE_SSH.at(-1).version;
const EXTENSION_ID = 'virgil-language-server.virgil-lsp-vscode';
// Deadlines: VS Code's download; the first connection, which downloads the
// VS Code Server; each later launch; each installation; and each other
// command over SSH.
const DOWNLOAD_TIMEOUT_MS = 600000;
const PROBE_TIMEOUT_MS = 300000;
const LAUNCH_TIMEOUT_MS = 180000;
const INSTALL_TIMEOUT_MS = 180000;
const SSH_TIMEOUT_MS = 60000;
// How long a stopped process group gets after SIGTERM before SIGKILL, and how
// long the rest of a group gets after its first process has exited.
const STOP_GRACE_MS = 5000;
const GROUP_EXIT_MS = 10000;
// How long a group gets to end after SIGKILL before the test gives up on it
// (see KILL_WAIT_MS in ../run.cjs).
const KILL_WAIT_MS = 30000;
// How long the language servers get to exit after their window has closed,
// or after the test has stopped a remote extension host that VS Code left
// running; and how long that host gets after SIGTERM, and then SIGKILL.
const SERVER_EXIT_MS = 5000;
const HOST_STOP_MS = 3000;

const STATUS = { PASS: 0, FAIL: 1, USAGE: 2, INFRA: 3 };
const SIGNAL_STATUS = { SIGINT: 130, SIGTERM: 143, SIGHUP: 129 };

const extension = path.resolve(__dirname, '../../..');
const repo = path.resolve(extension, '../..');

// A failure that ends the run: 'infra' for the environment, 'fail' for the
// product, or 'interrupted'.
class Failure extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind;
  }
}
const infra = (message) => new Failure('infra', message);

function infrastructure(what, step) {
  try {
    return step();
  } catch (error) {
    if (error instanceof Failure) throw error;
    throw infra(`could not ${what}: ${error.message}`);
  }
}

class UsageError extends Error {}

const usage = 'usage: node test/e2e/remote-ssh/run.cjs --vsix PATH [--bundled | --server PATH | --archive PATH] '
  + '[--virgil-root DIR] [--out DIR] [--repeat N] [--work-dir DIR] [--keep-work] [--sshd PATH]';

function options() {
  let parsed;
  try {
    parsed = parseArgs({
      options: {
        vsix: { type: 'string' },
        bundled: { type: 'boolean', default: false },
        server: { type: 'string' },
        archive: { type: 'string' },
        'virgil-root': { type: 'string' },
        out: { type: 'string' },
        repeat: { type: 'string', default: '1' },
        'work-dir': { type: 'string' },
        'keep-work': { type: 'boolean', default: false },
        sshd: { type: 'string', default: '/usr/sbin/sshd' },
      },
    }).values;
  } catch (error) {
    throw new UsageError(error.message);
  }
  if (process.platform !== 'linux') throw new UsageError('the Remote-SSH test runs on Linux only');
  // npm runs scripts in clients/vscode; relative arguments are relative to
  // where npm was started.
  const cwd = process.env.INIT_CWD ?? process.cwd();
  const resolve = (value) => (value === undefined ? undefined : path.resolve(cwd, value));
  const values = {
    vsix: resolve(parsed.vsix),
    bundled: parsed.bundled,
    server: resolve(parsed.server),
    archive: resolve(parsed.archive),
    virgilRoot: resolve(parsed['virgil-root']) ?? path.join(repo, 'vendor/virgil'),
    out: resolve(parsed.out),
    workDir: resolve(parsed['work-dir']) ?? os.tmpdir(),
    keepWork: parsed['keep-work'],
    sshd: parsed.sshd,
  };
  if (values.vsix === undefined) throw new UsageError('--vsix is required');
  if (!fs.existsSync(values.vsix)) throw new UsageError(`no VSIX at ${values.vsix}; package the extension first`);
  if (values.server !== undefined && values.archive !== undefined) throw new UsageError('give --server or --archive, not both');
  if (values.bundled && (values.server !== undefined || values.archive !== undefined || parsed['virgil-root'] !== undefined)) {
    throw new UsageError('--bundled cannot be combined with an external server or root');
  }
  if (!values.bundled && values.archive === undefined) values.server ??= path.join(repo, 'build/virgil-lsp');
  if (values.archive !== undefined && !/\.tar\.gz$/.test(values.archive)) throw new UsageError(`not a .tar.gz archive: ${values.archive}`);
  if (values.archive !== undefined && !fs.existsSync(values.archive)) throw new UsageError(`no archive at ${values.archive}`);
  if (values.server !== undefined && !fs.existsSync(path.dirname(values.server))) throw new UsageError(`no directory ${path.dirname(values.server)}`);
  if (!/^[1-9][0-9]*$/.test(parsed.repeat)) throw new UsageError(`--repeat takes a positive whole number, not ${parsed.repeat}`);
  values.repeat = Number(parsed.repeat);
  if (!values.bundled && !fs.existsSync(path.join(values.virgilRoot, 'lib/util/StringBuilder.v3'))) {
    throw new UsageError(`no Virgil sources at ${values.virgilRoot}; run: git submodule update --init vendor/virgil`);
  }
  if (!values.bundled) values.virgilRoot = fs.realpathSync(values.virgilRoot);
  if (!fs.existsSync(values.workDir)) throw new UsageError(`no directory ${values.workDir}`);
  values.workDir = fs.realpathSync(values.workDir);
  // Remote-SSH's installation script and the SSH configuration take these
  // paths unquoted in places.
  if (!/^[A-Za-z0-9._/-]+$/.test(values.workDir)) {
    throw new UsageError(`the work directory's path must have only letters, digits, and . _ / -: ${values.workDir}; use --work-dir`);
  }
  if (!path.isAbsolute(values.sshd)) throw new UsageError(`--sshd must be an absolute path: ${values.sshd}`);
  if (values.out !== undefined) {
    const entries = infrastructure('prepare the output directory', () => {
      fs.mkdirSync(values.out, { recursive: true });
      values.out = fs.realpathSync(values.out);
      return fs.readdirSync(values.out);
    });
    if (entries.length > 0) throw new UsageError(`the output directory is not empty: ${values.out}`);
    // The work directory holds the run's keys and Microsoft's software.
    if (`${values.workDir}/`.startsWith(`${values.out}/`)) {
      throw new UsageError(`the work directory can't be in the output directory: ${values.workDir}`);
    }
  }
  return values;
}

function findOnPath(name) {
  for (const dir of (process.env.PATH ?? '').split(':')) {
    const file = path.join(dir || '.', name);
    try {
      fs.accessSync(file, fs.constants.X_OK);
      return file;
    } catch {
      // Not in this directory.
    }
  }
  return undefined;
}

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

// A digest of a directory tree: the SHA-256 of its sorted "<sha256>  <path>"
// lines, with a symbolic link's target in place of its contents.
function treeSha256(dir) {
  const lines = fs.readdirSync(dir, { recursive: true }).sort().flatMap((name) => {
    const file = path.join(dir, name);
    const stat = fs.lstatSync(file);
    if (stat.isSymbolicLink()) return [`link ${fs.readlinkSync(file)}  ${name}\n`];
    return stat.isFile() ? [`${sha256(file)}  ${name}\n`] : [];
  });
  return crypto.createHash('sha256').update(lines.join('')).digest('hex');
}

// One file from a zip archive, such as a VSIX: its central directory gives
// the entry's local header, whose data is stored or deflated.
function readZipEntry(file, name, optional = false) {
  const zip = fs.readFileSync(file);
  let end = zip.length - 22;
  while (end >= 0 && zip.readUInt32LE(end) !== 0x06054b50) end--;
  if (end < 0) throw new Error(`${file} is not a zip archive`);
  let at = zip.readUInt32LE(end + 16);
  for (let i = zip.readUInt16LE(end + 10); i > 0; i--) {
    if (zip.readUInt32LE(at) !== 0x02014b50) throw new Error(`${file} has a damaged central directory`);
    const method = zip.readUInt16LE(at + 10);
    const size = zip.readUInt32LE(at + 20);
    const nameLength = zip.readUInt16LE(at + 28);
    const local = zip.readUInt32LE(at + 42);
    if (zip.toString('utf8', at + 46, at + 46 + nameLength) === name) {
      const data = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
      const raw = zip.subarray(data, data + size);
      return method === 0 ? raw : zlib.inflateRawSync(raw);
    }
    at += 46 + nameLength + zip.readUInt16LE(at + 30) + zip.readUInt16LE(at + 32);
  }
  if (!optional) throw new Error(`${file} has no ${name}`);
}

// Quotes a word for the POSIX shell that runs a command over SSH.
function sh(word) {
  return `'${String(word).replaceAll("'", "'\\''")}'`;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Runs command in a process group of its own (see startGroup in
// ../processes.cjs), with input on its standard input, and collects its
// output, which onData also receives, with the stream's name. A timeout or an
// interrupt stops the group: SIGTERM, then SIGKILL after STOP_GRACE_MS. If the
// group outlives the command by GROUP_EXIT_MS, it is stopped the same way and
// the result's groupLeft says so. Also waits for the output pipes to close, so
// buffered archive bytes and diagnostics aren't lost after command exit.
// Resolves once the whole group has gone, which it looks at every 50
// milliseconds, or is no longer the run's. The group's keeper stays until
// then, or until the group's SIGKILL, so the group stays the run's: a process
// that joins it, even just before the command exits, is waited for, and
// stopped with the group. If the group or its output hasn't ended
// KILL_WAIT_MS after SIGKILL, it resolves all the same, at once, and the
// result's stopped says so: it detaches from the group, and so stops reading
// the output, so that the test can exit while the group, even its keeper, or
// a process that holds the output pipes runs on (see detach in
// ../processes.cjs). Keeper release has its own bound, even after a clean
// exit (see release in ../processes.cjs).
function runGroup(command, args, { env, input, timeout, signal, onData, onExit }) {
  return new Promise((resolve) => {
    const output = { stdout: [], stderr: [] };
    let stopped;
    let exited;
    let open = 2;
    let finished = false;
    let releasing = false;
    let deadline;
    // When the test gives up on the group, KILL_WAIT_MS after its SIGKILL.
    let abandon;
    let groupLeft = false;
    const started = Date.now();
    const group = startGroup(command, args, {
      env,
      stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'],
      onExit: (result) => {
        exited = { ...result, at: Date.now() };
        if (result.error === undefined) onExit?.(exited);
        finish();
      },
    });
    // With anyway, resolves at once, without the keeper.
    const finish = (anyway = false) => {
      if (finished || (!anyway && (releasing || exited === undefined || open > 0 || group.look()?.empty === false))) return;
      clearTimeout(timer);
      const done = () => {
        if (finished) return;
        finished = true;
        clearInterval(poll);
        signal.removeEventListener('abort', interrupt);
        resolve({
          ...exited, stopped, groupLeft, seconds: (Date.now() - started) / 1000,
          stdout: Buffer.concat(output.stdout), stderr: Buffer.concat(output.stderr).toString(),
        });
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
    };
    const stop = (reason) => {
      if (stopped !== undefined || group.pid === undefined) return;
      stopped = reason;
      group.signal('SIGTERM');
      deadline = Date.now() + STOP_GRACE_MS;
    };
    const poll = setInterval(() => {
      const empty = group.look()?.empty !== false;
      if (deadline !== undefined && Date.now() > deadline) {
        deadline = undefined;
        group.signal('SIGKILL');
        abandon = Date.now() + KILL_WAIT_MS;
      } else if (exited !== undefined && empty) {
        finish();
      } else if (!releasing && exited !== undefined && stopped === undefined && Date.now() > exited.at + GROUP_EXIT_MS) {
        groupLeft = true;
        stop(`left processes running ${GROUP_EXIT_MS / 1000} seconds after it exited`);
      }
      if (!finished && abandon !== undefined && Date.now() > abandon) {
        stopped += `, and hadn't ended ${KILL_WAIT_MS / 1000} seconds after SIGKILL`;
        if (!group.detach()) stopped += `, nor had its keeper (pid ${group.pid})`;
        finish(true);
      }
    }, 50);
    const timer = setTimeout(() => stop(`exceeded ${timeout / 1000} seconds`), timeout);
    const interrupt = () => stop('was interrupted');
    signal.addEventListener('abort', interrupt);
    if (signal.aborted) interrupt();
    for (const name of ['stdout', 'stderr']) {
      const stream = group[name];
      if (stream === null) {
        open--;
        continue;
      }
      stream.on('data', (data) => {
        output[name].push(data);
        onData?.(data, name);
      });
      stream.on('close', () => {
        open--;
        finish();
      });
    }
    if (input !== undefined) {
      group.stdin.on('error', () => {});
      group.stdin.end(input);
    }
  });
}

function succeeded(result) {
  return result.error === undefined && result.stopped === undefined && result.code === 0;
}

function outcome(result) {
  if (result.error !== undefined) return `could not start: ${result.error.message}`;
  return result.stopped ?? `exited with ${result.code ?? result.signal}`;
}

// The test's run: its inputs, environment, and evidence.
class Run {
  constructor(values) {
    this.values = values;
    this.controller = new AbortController();
    this.signal = this.controller.signal;
    this.marker = `VIRGIL_REMOTE_SSH_RUN=${process.pid}-${crypto.randomBytes(8).toString('hex')}`;
    this.failures = [];
    // What the results' reader should know, which isn't a failure.
    this.notes = [];
    // What ended the test, or spoiled its evidence, as Failures.
    this.errors = [];
    // The test, then the log collection and the teardown, which a signal
    // doesn't stop, and the report.
    this.phase = 'test';
    // The VS Code Server's connection tokens, which the output mustn't hold.
    this.tokens = new Set();
    this.manifest = {
      result: undefined,
      started: new Date().toISOString(),
      repository: repository(),
      notCovered: [
        'Checklist step 17\'s window close during a recovery: VS Code sometimes quits before it tells the remote extension host to terminate, '
          + 'so the extension need not deactivate',
        'What VS Code shows: the status bar, views, popups, notifications, prompts, and the suggestion list',
        'A macOS client: Remote-SSH from a Mac to Linux is checked separately',
      ],
      launches: [],
    };
  }

  // Throws if the run was interrupted, so that the next step doesn't start.
  checkpoint() {
    if (this.signal.aborted) throw this.signal.reason;
  }

  fail(message) {
    this.failures.push(message);
    console.log(`FAIL: ${message}`);
  }

  note(message) {
    this.notes.push(message);
    console.log(`INFO: ${message}`);
  }

  // Records what ended the test or spoiled its evidence: a Failure as it is,
  // anything else as the runner's own failure. The signal handler records
  // interrupts.
  record(error) {
    const failure = error instanceof Failure ? error : new Failure('fail', `the runner failed: ${error.stack}`);
    if (failure.kind === 'interrupted' || this.errors.includes(failure)) return;
    this.errors.push(failure);
    console.log(`${failure.kind === 'infra' ? 'INFRA' : 'FAIL'}: ${failure.message}`);
  }

  // Runs command over SSH on the target, in the user's login shell there.
  // An SSH error (status 255) is an infrastructure failure.
  async remote(command, { input, timeout = SSH_TIMEOUT_MS, allowFailure = false, signal = this.signal } = {}) {
    const result = await runGroup('ssh', ['-F', this.target.sshConfig, this.target.alias, '--', command], {
      env: this.env, input, timeout, signal,
    });
    if (signal === this.signal) this.checkpoint();
    result.data = result.stdout;
    result.stdout = result.stdout.toString();
    if (result.error !== undefined || result.stopped !== undefined || result.code === 255) {
      throw infra(`SSH to ${this.target.alias} ${outcome(result)}: ${result.stderr.trim()}`);
    }
    if (result.code !== 0 && !allowFailure) {
      throw new Failure('fail', `on ${this.target.alias}, ${command.slice(0, 200)} exited with ${result.code}: ${result.stderr.trim()}`);
    }
    return result;
  }

  // Copies the names in localDir into remoteDir on the target, through tar.
  async upload(localDir, names, remoteDir) {
    const archive = await runGroup('tar', ['-c', '-h', '-f', '-', '-C', localDir, '--', ...names], {
      env: this.env, timeout: SSH_TIMEOUT_MS, signal: this.signal,
    });
    this.checkpoint();
    if (!succeeded(archive)) throw infra(`tar of ${names.join(', ')} in ${localDir} ${outcome(archive)}: ${archive.stderr}`);
    await this.remote(`mkdir -p ${sh(remoteDir)} && tar -x -f - -C ${sh(remoteDir)}`, { input: archive.stdout });
  }
}

function repository() {
  const git = (...args) => {
    try {
      return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    } catch {
      return undefined;
    }
  };
  const commit = git('rev-parse', 'HEAD');
  return { path: repo, commit: commit ?? null, modified: commit === undefined ? null : git('--no-optional-locks', 'status', '--porcelain') !== '' };
}

function osDetails() {
  const release = fs.existsSync('/etc/os-release') ? fs.readFileSync('/etc/os-release', 'utf8') : '';
  const cpu = fs.existsSync('/proc/cpuinfo') ? /^model name\s*:\s*(.*)$/m.exec(fs.readFileSync('/proc/cpuinfo', 'utf8'))?.[1] : undefined;
  return {
    name: /^PRETTY_NAME="?(.*?)"?$/m.exec(release)?.[1] ?? null,
    kernel: `${os.type()} ${os.release()} ${os.machine()}`,
    cpu: cpu ?? null,
  };
}

async function serverFiles(run) {
  const { server, archive } = run.values;
  const details = {};
  let dir;
  let names;
  let sources;
  if (archive !== undefined) {
    details.archive = archive;
    details.archiveSha256 = sha256(archive);
    const extracted = path.join(run.work, 'input');
    infrastructure('prepare the server input directory', () => fs.mkdirSync(extracted));
    const result = await runGroup('tar', ['-x', '-z', '-f', archive, '-C', extracted], { env: run.env, timeout: SSH_TIMEOUT_MS, signal: run.signal });
    run.checkpoint();
    if (!succeeded(result)) throw new Failure('fail', `could not extract ${archive}: ${outcome(result)}: ${result.stderr}`);
    const top = path.basename(archive, '.tar.gz');
    if (fs.readdirSync(extracted).join() !== top) throw new Failure('fail', `${archive} does not hold just the directory ${top}`);
    dir = path.join(extracted, top);
    names = fs.readdirSync(dir);
    sources = Object.fromEntries(['virgil-lsp', 'virgil-lsp-worker'].map((name) => [name, path.join(dir, name)]));
  } else {
    // The server starts the worker beside its own resolved path.
    const resolved = fs.existsSync(server) ? fs.realpathSync(server) : server;
    details.server = server;
    sources = { 'virgil-lsp': resolved, 'virgil-lsp-worker': path.join(path.dirname(resolved), 'virgil-lsp-worker') };
    dir = path.join(run.work, 'input');
    names = Object.keys(sources);
  }
  details.sha256 = {};
  for (const name of ['virgil-lsp', 'virgil-lsp-worker']) {
    const file = sources[name];
    if (fs.existsSync(file)) {
      details.sha256[name] = sha256(file);
    } else {
      details.sha256[name] = null;
    }
  }
  run.manifest.server = details;
  const missing = Object.keys(details.sha256).filter((name) => details.sha256[name] === null);
  if (missing.length > 0) throw new Failure('fail', `missing server executable(s): ${missing.map((name) => sources[name]).join(', ')}`);
  if (archive === undefined) {
    infrastructure('stage the server executables', () => {
      fs.mkdirSync(dir);
      for (const [name, file] of Object.entries(sources)) fs.copyFileSync(file, path.join(dir, name));
    });
  }
  return { dir, names, details };
}

// Downloads VS Code into the Extension Host test's cache, unless it is there,
// with ../download.cjs, in a process group of its own (see runGroup): an
// interrupt then stops the download, and the tar that extracts it, before the
// runner exits. @vscode/test-electron marks a finished extraction, and
// replaces a version's directory that has no mark, as one that an interrupt
// stopped. Returns VS Code's executable.
async function downloadVSCode(run) {
  const result = await runGroup(process.execPath, [path.join(__dirname, '..', 'download.cjs'), VSCODE_VERSION, path.join(extension, '.vscode-test')], {
    env: run.env, timeout: DOWNLOAD_TIMEOUT_MS, signal: run.signal,
    onData: (data, name) => {
      if (name === 'stderr') process.stderr.write(data);
    },
  });
  run.checkpoint();
  const executable = result.stdout.toString().trim();
  if (!succeeded(result) || executable === '') {
    throw infra(`could not download VS Code ${VSCODE_VERSION}: download.cjs ${outcome(result)}: ${result.stderr.trim()}`);
  }
  return executable;
}

// The client's user settings. Remote-SSH reads only its application settings
// from the client: the run's SSH configuration, the VS Code Server under the
// target's work directory, and a connection without the local server or the
// exec server, which the prototype of this test proved, with no extensions
// to add remotely. The Virgil settings are machine settings, so they go in
// the remote's settings instead (see launch).
function clientSettings(target, sshPath) {
  return {
    'remote.SSH.configFile': target.sshConfig,
    'remote.SSH.path': sshPath,
    'remote.SSH.serverInstallPath': { [target.alias]: path.posix.join(target.root, 'server') },
    'remote.SSH.remotePlatform': { [target.alias]: 'linux' },
    'remote.SSH.useLocalServer': false,
    'remote.SSH.useExecServer': false,
    'remote.SSH.localServerDownload': 'off',
    'remote.SSH.connectTimeout': 60,
    'remote.SSH.showLoginTerminal': false,
    'remote.SSH.enableRemoteCommand': false,
    'remote.SSH.defaultExtensions': [],
    'telemetry.telemetryLevel': 'off',
    'chat.disableAIFeatures': true,
    'update.mode': 'none',
    'extensions.autoCheckUpdates': false,
    'extensions.autoUpdate': false,
    'workbench.startupEditor': 'none',
    'files.autoSave': 'off',
    // Where the server offers no completion items, as in a comment, VS Code
    // would offer the document's words instead.
    'editor.wordBasedSuggestions': 'off',
  };
}

// The files on the target, under its work directory. With
// remote.SSH.useExecServer off, Remote-SSH installs the VS Code Server in
// .vscode-server under remote.SSH.serverInstallPath.
function remoteLayout(root, commit, vsix) {
  const agent = path.posix.join(root, 'server', '.vscode-server');
  const extensions = path.posix.join(agent, 'extensions');
  return {
    agent,
    serverNode: path.posix.join(agent, 'bin', commit, 'node'),
    cli: path.posix.join(agent, 'bin', commit, 'bin', 'code-server'),
    product: path.posix.join(agent, 'bin', commit, 'product.json'),
    data: path.posix.join(agent, 'data'),
    machineSettings: path.posix.join(agent, 'data', 'Machine', 'settings.json'),
    extensions,
    extensionPath: path.posix.join(extensions, `${vsix.id}-${vsix.version}`.toLowerCase()),
    server: path.posix.join(root, 'virgil-lsp'),
    virgilRoot: path.posix.join(root, 'virgil'),
    fixture: path.posix.join(root, 'fixture'),
    project: path.posix.join(root, 'project'),
    tests: path.posix.join(root, 'tests'),
    results: path.posix.join(root, 'results'),
    inputs: path.posix.join(root, 'inputs'),
  };
}

function remoteUri(target, file) {
  return `vscode-remote://ssh-remote+${target.alias}${file.split('/').map(encodeURIComponent).join('/')}`;
}

// Prints the suite's PASS and FAIL lines, and a failure's stack, as VS Code
// passes them on from the remote extension host.
function suiteLines() {
  let pending = '';
  let failing = false;
  return (data) => {
    pending += data;
    const lines = pending.split('\n');
    pending = lines.pop();
    for (const line of lines) {
      if (/^(PASS|FAIL): /.test(line)) {
        failing = line.startsWith('FAIL');
        console.log(`   ${line}`);
      } else if (failing && /^\s+\S/.test(line)) {
        console.log(`   ${line}`);
      } else {
        failing = false;
      }
    }
  };
}

function isVSCodeServer(exe, agent, extensionPath) {
  return (exe ?? '').startsWith(`${agent}/`)
    && !(extensionPath !== undefined && exe.startsWith(`${extensionPath}/server/`));
}

// A Node.js program for the target's VS Code Server, whose argument is JSON:
// it prints, as JSON, the run's processes (see runOwned), other than itself
// and zombies, with their parents' PIDs and their start times, and
// whether host, a process given by PID, start time, and executable, is gone
// (false), identified and running (true), or unidentified (null). A process
// whose main thread has exited is no zombie while its other threads run, and
// processFiles reads its identifying files from whichever thread still runs,
// as it does for the teardown (see isZombie and processFiles in
// ../processes.cjs); it also
// reports, in unidentified, each live process that no thread's files could
// describe, which the caller retries rather than counting as gone. A process
// that hides its executable, as a non-dumpable one does, is still found if the
// caller already identified it by its PID and start time, as it did host and
// the processes in kill, if its parent is the run's, or if an argument of its
// readable command line names a path under root (see runOwned in
// ../processes.cjs). Other readable executables outside root stay out unless
// their identity is already known. It sends signal, if given, to that host,
// and SIGKILL to those of the processes in kill, each given as PID@start,
// that still have that start time
// and whose executable is hidden or not under agent, the VS Code Server's
// directory: exactly those, never processes found by name or another process
// that now has the PID. Each signal goes through a pidfd where the target's
// Python and kernel have them (see PIDFD_SIGNAL in ../processes.cjs), and
// otherwise, or where a pidfd couldn't be opened for that process or couldn't
// signal it, follows a second check of the start time, and of whether it is a
// zombie, which it isn't while threads other than its main thread run.
const REMOTE_PROCESSES = `
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const { root, agent, extensionPath, kill, host, signal } = JSON.parse(process.argv[1]);
${isZombie}
${processFiles}
${runOwned}
${isVSCodeServer}
const found = [];
const killed = [];
const unidentified = [];
let hostRuns = false;
let signaledAt;
const stat = (pid) => {
  const text = fs.readFileSync('/proc/' + pid + '/stat', 'utf8');
  return text.slice(text.lastIndexOf(')') + 2).split(' ');
};
const send = (name, targets) => {
  if (targets.length === 0) return;
  const helper = spawnSync('python3', ['-I', '-c', ${JSON.stringify(PIDFD_SIGNAL)}, JSON.stringify([name, targets])], { encoding: 'utf8' });
  let results;
  try {
    results = helper.status === 0 ? JSON.parse(helper.stdout) : undefined;
  } catch {}
  const complete = Array.isArray(results) && results.length === targets.length;
  let sentAt = complete && results.includes('sent') ? Date.now() : undefined;
  targets.forEach(([pid, start], i) => {
    if (complete && ${JSON.stringify(SETTLED)}.includes(results[i])) return;
    try {
      const fields = stat(pid);
      if (!isZombie(fields[0], Number(fields[17])) && fields[19] === start) {
        process.kill(pid, name);
        sentAt = Date.now();
      }
    } catch {}
  });
  return sentAt;
};
const table = [];
for (const pid of fs.readdirSync('/proc').filter((name) => /^[0-9]+$/.test(name) && Number(name) !== process.pid)) {
  try {
    const identified = processFiles(fs, pid, { exe: 'link' });
    if (identified === undefined) continue;
    if (identified.unreadable) {
      unidentified.push(identified.pid);
      if (host && identified.pid === host.pid && identified.start === host.start) hostRuns = null;
      continue;
    }
    table.push({ pid: identified.pid, ppid: identified.ppid, start: identified.start, files: identified.files });
  } catch {}
}
const known = kill.map((identity) => {
  const [pid, start] = identity.split('@');
  return { pid: Number(pid), start };
});
if (host) known.push(host);
const byPid = new Map(table.map((entry) => [entry.pid, entry]));
const owned = new Set();
// Whether a process descends from one of the run's: its parent's chain
// reaches an owned process, each parent having started no later than its
// child, so that a reused PID breaks the chain. A readable process between
// them, such as a system shell or Python that isn't itself a leftover, doesn't
// break it, so a hidden child of the server's still counts (see runOwned,
// which asks this only of a process whose executable can't be read).
const parent = (ppid, start) => {
  let childStart = Number(start);
  for (let pid = ppid, depth = 0; depth < 64; depth++) {
    const entry = byPid.get(pid);
    if (entry === undefined || Number(entry.start) > childStart) return false;
    if (owned.has(entry.pid)) return true;
    childStart = Number(entry.start);
    pid = entry.ppid;
  }
  return false;
};
for (let added = true; added;) {
  added = false;
  for (const entry of table) {
    if (owned.has(entry.pid)) continue;
    if (runOwned({ ...entry, roots: [root], known, parent })) {
      owned.add(entry.pid);
      added = true;
    }
  }
}
for (const entry of table) {
  if (!owned.has(entry.pid)) continue;
  const identity = known.find((candidate) => candidate.pid === entry.pid && candidate.start === entry.start);
  const exe = entry.files.exe ?? identity?.exe;
  const start = entry.start;
  if (host && entry.pid === host.pid && start === host.start && exe === host.exe) hostRuns = true;
  if (kill.includes(entry.pid + '@' + start) && !isVSCodeServer(exe, agent, extensionPath)) killed.push([entry.pid, start]);
  found.push(exe === undefined ? { pid: entry.pid, ppid: entry.ppid, start } : { pid: entry.pid, ppid: entry.ppid, start, exe });
}
if (hostRuns && signal) signaledAt = send(signal, [[host.pid, host.start]]);
send('SIGKILL', killed);
console.log(JSON.stringify({ found, host: hostRuns, signaledAt, unidentified, sampledAt: Date.now() }));`;

// The run's processes on the target, identified by REMOTE_PROCESSES, split
// into the VS Code Server's, which stay running between windows, and leftovers;
// whether host, a window's remote extension host, still runs; and live
// processes that could not be identified. Sends signal to host, then kills
// the processes in kill, subject to REMOTE_PROCESSES's identity checks.
async function remoteProcesses(run, layout, { kill = [], host, signal } = {}) {
  const options = { root: run.target.root, agent: layout.agent, extensionPath: layout.extensionPath, kill: kill.map((p) => `${p.pid}@${p.start}`), host, signal };
  const result = await run.remote(`${sh(layout.serverNode)} -e ${sh(REMOTE_PROCESSES)} ${sh(JSON.stringify(options))}`, { allowFailure: true });
  let listing;
  try {
    listing = JSON.parse(result.stdout);
  } catch {
    throw infra(`could not list the processes on ${run.target.alias}: ${result.stderr.trim()}`);
  }
  const server = (p) => isVSCodeServer(p.exe, layout.agent, layout.extensionPath);
  return {
    found: listing.found, left: listing.found.filter((p) => !server(p)), vscodeServer: listing.found.filter(server).length,
    host: listing.host, signaledAt: listing.signaledAt, unidentified: listing.unidentified ?? [], sampledAt: listing.sampledAt,
  };
}

// The remote extension host that bootstrap.cjs ran in, by its PID, start
// time, and executable, the VS Code Server's node; undefined if bootstrap.cjs
// didn't record it.
function extensionHost(evidence, layout) {
  if (!Number.isInteger(evidence?.pid) || typeof evidence.start !== 'string') return undefined;
  return { pid: evidence.pid, start: evidence.start, exe: layout.serverNode };
}

// Waits up to SERVER_EXIT_MS for the language servers to exit, and for host
// too, if given; returns what still runs then. A sample that finds a live
// process that no thread's files described is not proof of shutdown, at any
// time: it is sampled again within the bound, and if none within it describes
// the process, the check fails with its PID, since it may still run.
async function settle(run, layout, at, host, firstSample) {
  const deadline = at + SERVER_EXIT_MS;
  let timely = false;
  if (!Number.isFinite(deadline)) throw infra('leak check could not be verified in time');
  for (;;) {
    const processes = firstSample ?? await remoteProcesses(run, layout, { host });
    firstSample = undefined;
    processes.host = host !== undefined && processes.unidentified.includes(host.pid) ? null
      : host !== undefined && processes.found.some((p) => p.pid === host.pid && p.start === host.start
        && (p.exe === undefined || p.exe === host.exe));
    if (!Number.isFinite(processes.sampledAt)) throw infra('leak check could not be verified in time');
    timely ||= processes.sampledAt <= deadline;
    if (!timely) throw infra('leak check could not be verified in time');
    if (processes.unidentified.length > 0) {
      if (processes.sampledAt >= deadline) throw infra(`leak check could not identify the host's process with pid ${processes.unidentified.join(', ')}`);
    } else if (processes.left.length === 0 && !processes.host) {
      if (processes.sampledAt > deadline) throw infra('leak check could not be verified in time');
      return processes;
    }
    if (processes.sampledAt >= deadline) return processes;
    if (Date.now() < deadline) await sleep(Math.min(200, deadline - Date.now()));
  }
}

// The processes in processes that descend from the process with pid.
function descendants(processes, pid) {
  const found = new Set([pid]);
  for (let added = true; added;) {
    added = false;
    for (const p of processes) {
      if (!found.has(p.pid) && found.has(p.ppid)) {
        found.add(p.pid);
        added = true;
      }
    }
  }
  return processes.filter((p) => found.has(p.pid));
}

// Stops a remote extension host that VS Code left running, and returns the
// signal that stopped it, or null if it exited first, and how long it took.
// The host doesn't handle SIGTERM, so SIGTERM ends it at once, without
// deactivating its extensions, as a crash would: its language client neither
// stops its server nor can restart it, and the server sees its input close.
// SIGKILL follows if the host still runs HOST_STOP_MS after SIGTERM is sent.
// An unidentified sample retains the pending signal and host identity; its
// identification is retried within HOST_STOP_MS, and the signal's wait starts
// only when it is sent. A host still unidentified at the deadline fails with
// its PID rather than counting as stopped or as having survived a signal.
async function stopHost(run, layout, host) {
  const started = Date.now();
  let sent = null;
  let at = started;
  for (const signal of ['SIGTERM', 'SIGKILL']) {
    let deadline = Date.now() + HOST_STOP_MS;
    for (;;) {
      const processes = await remoteProcesses(run, layout, { host, signal: sent === signal ? undefined : signal });
      if (processes.host === false) return { signal: sent, seconds: (Date.now() - started) / 1000, at, processes };
      if (processes.signaledAt !== undefined) {
        sent = signal;
        at = processes.signaledAt;
        deadline = at + HOST_STOP_MS;
        // This sample observed the host before sending the signal. Even if
        // the SSH response used up the wait, observe it again before escalation
        // or a survival verdict.
        if (Date.now() >= deadline) continue;
      }
      if (Date.now() >= deadline) {
        if (processes.host === null) throw infra(`leak check could not identify the host's process with pid ${host.pid}`);
        if (sent !== signal) throw infra(`could not send ${signal} to the remote extension host (pid ${host.pid})`);
        break;
      }
      await sleep(Math.min(100, deadline - Date.now()));
    }
  }
  throw infra(`the remote extension host (pid ${host.pid}) that VS Code left running still ran ${HOST_STOP_MS / 1000} seconds after SIGKILL`);
}

// What happened to a launch's remote extension host that VS Code left
// running, for the output; undefined if VS Code didn't leave it running.
function hostStopped(host) {
  if (!host?.leftRunning) return undefined;
  if (host.stopSeconds === undefined) return 'while stopping the remote extension host that VS Code left running';
  if (host.stoppedWith === null) return 'the remote extension host that VS Code left running exited before the test stopped it';
  return `stopped the remote extension host that VS Code left running, with ${host.stoppedWith}`;
}

// One start of VS Code in a Remote-SSH window, which runs bootstrap.cjs in
// the remote extension host and exits when it finishes. mode is 'probe',
// 'single-file', or 'workspace'. Returns the launch's record for the
// manifest; an infrastructure failure throws.
async function launch(run, name, mode, options) {
  let record;
  let failed;
  await attempt({
    name, work: path.join(run.work, 'client'), logs: path.join(run.values.out, 'client'),
    started: 'the tests started in the remote extension host',
    // collectLogs keeps both attempts, including the remote logs, before
    // publishLogs redacts their connection tokens. Never copy raw logs out.
    saveLogs: async () => {},
    beforeRetry: async () => {
      await stopForRetry(run, options.layout);
      const result = path.posix.join(options.layout.results, `${record.name}.json`);
      const report = await readBootstrapReport(run, result);
      if (report.code !== 44) {
        run.note(`${record.name}: bootstrap started after the window exited or its result file is unreadable; not retrying`);
        throw failed;
      }
      record.retriedAs = `${name}-retry`;
    },
    launch: async (dir) => {
      failed = undefined;
      record = {
        name: path.basename(dir), mode,
        ...(options.repetition === undefined ? {} : { repetition: options.repetition }),
      };
      run.manifest.launches.push(record);
      try {
        await launchSteps(run, record, options);
        return { passed: record.result === 'PASS' };
      } catch (error) {
        record.result ??= { infra: 'INFRA', interrupted: 'INTERRUPTED' }[error.kind] ?? 'FAIL';
        if (error.retryOutcome === undefined) throw error;
        failed = error;
        return error.retryOutcome;
      }
    },
  });
  if (failed !== undefined) throw failed;
  return record;
}

// A retry must not reconnect to anything the failed window left running,
// even a VS Code Server or an extension host whose bootstrap never ran.
// Keep its logs first, then stop only processes identified under this run's
// remote root, by PID and start time, using the host shutdown's bounds.
async function stopForRetry(run, layout) {
  const errors = run.errors.length;
  await collectLogs(run);
  if (run.errors.length !== errors) throw infra('could not keep the failed launch logs before retrying');
  // The target is this machine and user. Its copy of the runner's Node is
  // available over SSH even when the probe failed before installing the
  // VS Code Server (and therefore before layout.serverNode exists).
  const scanner = { ...layout, serverNode: process.execPath };
  const deadline = Date.now() + KILL_WAIT_MS;
  for (;;) {
    run.checkpoint();
    const processes = await remoteProcesses(run, scanner);
    if (processes.unidentified.length > 0) throw infra(`could not identify processes before retry: ${processes.unidentified.join(', ')}`);
    if (processes.found.length === 0) return;
    if (Date.now() >= deadline) throw infra('remote processes remained before retry');
    for (const member of processes.found) {
      if (member.exe === undefined) throw infra(`could not identify the executable of pid ${member.pid} before retry`);
      await stopHost(run, scanner, member);
    }
  }
}

function readBootstrapReport(run, result) {
  return run.remote(`if [ -e ${sh(result)} ]; then cat ${sh(result)}; else exit 44; fi`, { allowFailure: true });
}

async function launchSteps(run, record, { executable, layout, vsix }) {
  const { target } = run;
  const { name, mode } = record;
  const result = path.posix.join(layout.results, `${name}.json`);
  const config = {
    launch: name,
    mode,
    result,
    serverNode: layout.serverNode,
    serverLogs: path.posix.join(layout.data, 'logs'),
    extensionPath: layout.extensionPath,
    extensionVersion: vsix.version,
    project: layout.project,
    bundled: run.values.bundled,
    // The workspace folders that VS Code must have, as the remote extension
    // host sees them: suite.cjs runs its single-file tests without any, and
    // its workspace tests otherwise.
    folders: mode === 'workspace' ? [`file://${layout.project}`] : null,
  };
  // A fresh copy of the fixture, and the Virgil settings in the remote's
  // machine settings, which the suite changes and restores.
  if (mode !== 'probe') {
    await run.remote(`rm -rf ${sh(layout.project)} && cp -R ${sh(layout.fixture)} ${sh(layout.project)} `
      + `&& mkdir -p ${sh(path.posix.dirname(layout.machineSettings))} && cat > ${sh(layout.machineSettings)}`, {
      input: `${JSON.stringify(run.values.bundled ? {} : {
        'virgil.server.path': path.posix.join(layout.server, 'virgil-lsp'),
        'virgil.virgilRoot': layout.virgilRoot,
      }, null, 2)}\n`,
    });
  }
  await run.remote(`cat > ${sh(path.posix.join(layout.tests, 'run-config.json'))}`, { input: JSON.stringify(config, null, 2) });

  // Each launch has new client user data, so that VS Code doesn't restore
  // the previous window.
  const userData = path.join(run.work, 'client', name, 'user-data');
  infrastructure(`prepare ${name}'s client settings`, () => {
    fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
    fs.writeFileSync(path.join(userData, 'User', 'settings.json'), JSON.stringify(run.settings, null, 2));
  });
  const args = [
    `--user-data-dir=${userData}`,
    `--extensions-dir=${run.clientExtensions}`,
    '--remote', `ssh-remote+${target.alias}`,
    ...(mode === 'workspace' ? ['--folder-uri', remoteUri(target, layout.project)] : ['--new-window']),
    // The development path, an empty directory, puts VS Code in test mode
    // without loading an extension from it. Both URIs are remote, so the
    // tests run in the remote extension host. --disable-extensions would
    // disable Remote-SSH too.
    `--extensionDevelopmentPath=${remoteUri(target, path.posix.join(layout.tests, 'no-extension'))}`,
    `--extensionTestsPath=${remoteUri(target, path.posix.join(layout.tests, 'bootstrap.cjs'))}`,
    '--disable-workspace-trust',
    '--disable-updates',
    '--skip-welcome',
    '--skip-release-notes',
    '--no-sandbox',
    '--disable-gpu-sandbox',
  ];
  const logFile = path.join(run.evidence, 'launches', `${name}.log`);
  const log = infrastructure(`open launches/${name}.log`, () => {
    fs.mkdirSync(path.dirname(logFile), { recursive: true });
    return fs.openSync(logFile, 'w');
  });
  const print = suiteLines();
  console.log(`== ${name}`);
  const timeout = mode === 'probe' ? PROBE_TIMEOUT_MS : LAUNCH_TIMEOUT_MS;
  let unwritten;
  let exit;
  let sampling;
  let firstSample;
  try {
    infrastructure(`write launches/${name}.log`, () => fs.writeSync(log, `${JSON.stringify([executable, ...args])}\n`));
    exit = await runGroup(executable, args, {
      env: run.env, timeout, signal: run.signal,
      onExit: () => {
        if (mode !== 'probe') {
          sampling = remoteProcesses(run, layout).then((processes) => ({ processes }), (error) => ({ error }));
        }
      },
      onData: (data) => {
        try {
          fs.writeSync(log, data);
        } catch (error) {
          unwritten ??= error;
        }
        print(data);
      },
    });
  } finally {
    firstSample = await sampling;
    infrastructure(`close launches/${name}.log`, () => fs.closeSync(log));
  }
  record.seconds = exit.seconds;
  record.exit = exit.error?.message ?? exit.stopped ?? exit.code ?? exit.signal;
  run.checkpoint();
  if (firstSample?.error !== undefined) throw firstSample.error;
  if (unwritten !== undefined) throw infra(`could not write launches/${name}.log: ${unwritten.message}`);

  // Distinguish an absent report (44) from one that exists but can't be
  // read, which must not make a started bootstrap eligible for a retry.
  const report = await readBootstrapReport(run, result);
  let evidence;
  try {
    if (report.code !== 44) evidence = JSON.parse(report.stdout);
  } catch {
    evidence = { outcome: 'unreadable', error: `${result} is not JSON` };
  }
  record.bootstrap = evidence ?? null;
  const output = exit.stdout.toString() + exit.stderr;
  record.passLines = (output.match(/^PASS: /gm) ?? []).length;
  record.failLines = (output.match(/^FAIL: /gm) ?? []).length;
  record.suiteSeconds = evidence?.seconds ?? null;

  // Closing the window ends its remote extension host, whose extension stops
  // its language server: none may be left SERVER_EXIT_MS later. But VS Code
  // can quit before it tells the host to terminate, and the VS Code Server
  // then keeps the host, with its language client and server, for its
  // reconnection grace time, three hours, as it should for a window that may
  // reconnect. So a host that still runs then is stopped, and its language
  // server must then exit by itself within SERVER_EXIT_MS, as when its client
  // has gone. Stopping it also keeps its client from restarting the server
  // during a later launch.
  if (mode !== 'probe') {
    const host = extensionHost(evidence, layout);
    let processes = await settle(run, layout, exit.at, host, firstSample?.processes);
    if (host !== undefined) record.remoteExtensionHost = { pid: host.pid, start: host.start, leftRunning: processes.host };
    if (processes.host) {
      record.remoteExtensionHost.processes = descendants(processes.left, host.pid);
      record.info = [`VS Code didn't terminate the remote extension host (pid ${host.pid}); stopping it`];
      run.note(`${name}: ${record.info[0]}`);
      const stopped = await stopHost(run, layout, host);
      Object.assign(record.remoteExtensionHost, { stoppedWith: stopped.signal, stopSeconds: stopped.seconds });
      processes = await settle(run, layout, stopped.at, undefined, stopped.processes);
    }
    record.leftRunning = processes.left;
    record.vscodeServerProcesses = processes.vscodeServer;
    if (processes.left.length > 0) await remoteProcesses(run, layout, { kill: processes.left });
  }

  const problems = [];
  if (evidence === undefined) {
    // The connection, or VS Code itself, failed before the tests started.
    if (!succeeded(exit)) {
      const error = infra(`${name}: VS Code ${outcome(exit)} before the tests started in the remote extension host; see launches/${name}.log`);
      // Only the bootstrap creates this launch's result file, before it
      // checks or runs anything. An incomplete or unreadable file still
      // proves it started; only an absent file permits a retry.
      error.retryOutcome = {
        passed: false, kind: 'launch', detail: outcome(exit), activated: false,
        exited: exit.error === undefined && exit.stopped === undefined && !exit.groupLeft
          && !(record.leftRunning?.length > 0),
        windowError: infrastructure(`read ${name}'s main log`, () => windowError(userData)),
      };
      throw error;
    }
    problems.push('no test ran: VS Code exited with 0, but bootstrap.cjs never started in the remote extension host');
  } else if (JSON.stringify(evidence.folders) !== JSON.stringify(config.folders)) {
    // bootstrap.cjs refuses to run the suite then.
    problems.push(`scenario mismatch: the ${mode} launch had the workspace folders ${JSON.stringify(evidence.folders)}, `
      + `not ${JSON.stringify(config.folders)}, so suite.cjs would have run the wrong tests`);
  } else {
    if (!succeeded(exit)) problems.push(`VS Code ${outcome(exit)}`);
    if (evidence.outcome !== 'pass') {
      problems.push(`the tests ${evidence.outcome === 'started' ? 'did not finish' : 'failed'}: ${evidence.error ?? ''}`.trim());
    }
    if (mode !== 'probe' && record.passLines === 0) problems.push('the suite printed no PASS line');
    if (record.failLines > 0) problems.push(`the suite printed ${record.failLines} FAIL line(s)`);
  }
  if (exit.groupLeft) problems.push(`VS Code's processes outlived it by ${GROUP_EXIT_MS / 1000} seconds`);
  const lingered = record.remoteExtensionHost?.leftRunning === true;
  if (record.leftRunning?.length > 0) {
    problems.push(`left running ${SERVER_EXIT_MS / 1000} seconds after `
      + `${lingered ? 'the test stopped the remote extension host that VS Code left running' : 'the window closed'}, then killed: `
      + record.leftRunning.map((p) => `${p.exe ?? 'a process that hides its executable'} (pid ${p.pid})`).join(', '));
  }
  record.result = problems.length === 0 ? 'PASS' : 'FAIL';
  record.problems = problems;
  const suite = record.suiteSeconds === null || mode === 'probe' ? '' : `, suite ${record.suiteSeconds.toFixed(1)} s`;
  const host = lingered ? `; ${hostStopped(record.remoteExtensionHost)}` : '';
  const left = record.leftRunning?.length === 0 ? `; ${lingered ? 'then ' : ''}no language server left running` : '';
  console.log(`-- ${name}: ${record.result} in ${record.seconds.toFixed(1)} s${suite}${host}${left}`);
  if (mode === 'probe' && record.result !== 'PASS') {
    throw infra(`the first connection did not run its probe in the VS Code Server: ${problems.join('; ')}`);
  }
  for (const problem of problems) run.fail(`${name}: ${problem}`);
  return record;
}

function readVSIX(run) {
  const { values } = run;
  const vsixManifest = JSON.parse(readZipEntry(values.vsix, 'extension/package.json'));
  const vsix = {
    path: values.vsix,
    sha256: sha256(values.vsix),
    id: `${vsixManifest.publisher}.${vsixManifest.name}`,
    version: vsixManifest.version,
    extensionKind: vsixManifest.extensionKind ?? null,
    target: JSON.parse(readZipEntry(values.vsix, 'extension/server-target.json', true) ?? '{}').target,
  };
  if (vsix.id !== EXTENSION_ID) throw new Failure('fail', `${values.vsix} holds ${vsix.id}, not ${EXTENSION_ID}`);
  if (values.bundled && vsix.target !== 'linux-x64') throw new Failure('fail', 'Remote-SSH bundled test needs linux-x64');
  run.manifest.vsix = vsix;
  return vsix;
}

async function locateExtension(run, layout, vsix) {
  const name = `${vsix.id}-${vsix.version}`;
  const names = [name, ...(vsix.target === undefined ? [] : [`${name}-${vsix.target}`])];
  const locate = `const fs = require('fs'); const path = require('path');
    const root = ${JSON.stringify(layout.extensions)};
    const names = ${JSON.stringify(names)};
    const matches = fs.readdirSync(root).filter(n => names.includes(n));
    if (matches.length !== 1) process.exit(1); console.log(path.join(root, matches[0]));`;
  return (await run.remote(`${sh(layout.serverNode)} -e ${sh(locate)}`)).stdout.trim();
}

async function test(run) {
  const { values } = run;
  const vsix = readVSIX(run);
  const server = values.bundled ? undefined : await serverFiles(run);
  if (values.bundled) run.manifest.server = { bundled: true, vsixSha256: vsix.sha256 };
  if (!values.bundled) run.manifest.virgilRoot = { path: values.virgilRoot, libTreeSha256: treeSha256(path.join(values.virgilRoot, 'lib')) };
  const suite = path.join(__dirname, '..', 'suite.cjs');
  run.manifest.tests = { suiteSha256: sha256(suite), bootstrapSha256: sha256(path.join(__dirname, 'bootstrap.cjs')) };
  console.log(`VS Code Remote-SSH test: VS Code ${VSCODE_VERSION}, Remote-SSH ${REMOTE_SSH_VERSION}, `
    + `${vsix.id} ${vsix.version} from ${values.vsix}, server from ${values.bundled ? 'the VSIX bundle' : values.archive ?? values.server}`);

  const executable = await downloadVSCode(run);
  const product = JSON.parse(fs.readFileSync(path.join(path.dirname(executable), 'resources/app/product.json'), 'utf8'));
  run.manifest.client = {
    os: osDetails(),
    node: process.version,
    vscode: { version: product.version, commit: product.commit, quality: product.quality, executable },
    pidfds: pidfdSupport(),
  };

  // The target, and the SSH client that reaches it.
  const sshPath = findOnPath('ssh');
  const sshVersion = await runGroup(sshPath, ['-V'], { env: run.env, timeout: SSH_TIMEOUT_MS, signal: run.signal });
  run.manifest.client.ssh = { path: sshPath, version: sshVersion.stderr.trim() };
  // Provisioning takes seconds, so an interrupt waits for it; the teardown
  // then stops what it started.
  const target = localhost.create({
    work: run.work, sockets: run.sockets, env: run.env, marker: run.marker, sshd: values.sshd,
    log: (line) => console.log(line),
  });
  run.target = target;
  try {
    await target.start();
  } catch (error) {
    throw infra(`could not provision the host: ${error.message}`);
  }
  run.checkpoint();
  run.env = { ...run.env, ...target.clientEnv };
  const preflight = await run.remote('printf "%s\\n" "$HOME" "$VIRGIL_REMOTE_SSH_RUN"; uname -srm; '
    + 'sed -n "s/^PRETTY_NAME=//p" /etc/os-release | tr -d \'"\'');
  const [home, marker, kernel, name] = preflight.stdout.split('\n');
  if (home !== path.posix.join(target.root, 'home') || `VIRGIL_REMOTE_SSH_RUN=${marker}` !== run.marker) {
    throw infra(`the SSH session's environment is not the run's: HOME=${home}`);
  }
  run.connected = true;
  run.manifest.target = { mode: target.mode, alias: target.alias, root: target.root, ...target.details, os: { name, kernel } };
  console.log(`-- connected over SSH to ${target.details.user}@${target.details.host}:${target.details.port}: ${name}, ${kernel}`);

  // Remote-SSH on the client, from the Marketplace, with VS Code's own CLI.
  run.clientExtensions = path.join(run.work, 'client', 'extensions');
  const installUserData = path.join(run.work, 'client', 'install');
  const cli = resolveCliPathFromVSCodeExecutablePath(executable);
  const install = await runGroup(cli, [
    `--user-data-dir=${installUserData}`,
    `--extensions-dir=${run.clientExtensions}`,
    ...REMOTE_SSH.flatMap(({ id, version }) => ['--install-extension', `${id}@${version}`]),
  ], { env: run.env, timeout: INSTALL_TIMEOUT_MS, signal: run.signal });
  infrastructure('write remote-ssh-install.log', () => fs.writeFileSync(path.join(run.evidence, 'remote-ssh-install.log'), install.stdout.toString() + install.stderr));
  run.checkpoint();
  if (!succeeded(install)) throw infra(`installing Remote-SSH ${REMOTE_SSH_VERSION} ${outcome(install)}; see remote-ssh-install.log`);
  const installed = fs.readdirSync(run.clientExtensions).filter((name) => name !== 'extensions.json' && !name.startsWith('.')).sort();
  run.manifest.client.extensions = REMOTE_SSH.map(({ id, version, sha256: expected }) => {
    const vsixFile = path.join(installUserData, 'CachedExtensionVSIXs', `${id}-${version}`);
    const actual = fs.existsSync(vsixFile) ? sha256(vsixFile) : null;
    if (actual !== expected) throw infra(`the Marketplace's ${id} ${version} has SHA-256 ${actual}, not ${expected}`);
    return { id, version, sha256: actual };
  });
  const expected = REMOTE_SSH.map(({ id, version }) => `${id}-${version}`).sort();
  if (JSON.stringify(installed) !== JSON.stringify(expected)) {
    throw infra(`VS Code's CLI installed ${installed.join(', ')}, not ${expected.join(', ')}`);
  }
  run.settings = clientSettings(target, sshPath);
  run.manifest.client.settings = run.settings;
  console.log(`-- installed ${REMOTE_SSH.map(({ id, version }) => `${id} ${version}`).join(', ')}, each with its pinned SHA-256`);

  // The files that the tests need on the target.
  const layout = remoteLayout(target.root, product.commit, vsix);
  if (!values.bundled) {
    await run.upload(server.dir, server.names, layout.server);
    await run.upload(values.virgilRoot, ['lib'], layout.virgilRoot);
  }
  await run.upload(path.join(repo, 'test/e2e/nvim/fixture'), ['.'], layout.fixture);
  await run.upload(path.dirname(suite), ['suite.cjs', 'shutdown.cjs'], layout.tests);
  await run.upload(__dirname, ['bootstrap.cjs'], layout.tests);
  await run.upload(path.dirname(values.vsix), [path.basename(values.vsix)], layout.inputs);
  await run.remote(`mkdir -p ${sh(path.posix.join(layout.tests, 'no-extension'))} ${sh(layout.results)}`);
  if (!values.bundled) {
    const version = await run.remote(`${sh(path.posix.join(layout.server, 'virgil-lsp'))} --version`, { allowFailure: true });
    run.manifest.server.version = version.code === 0 ? version.stdout.trim() : null;
    console.log(`-- uploaded the server, the Virgil root's lib, the fixture, and the tests to ${target.root}`);
    if (version.code !== 0) run.fail(`virgil-lsp --version on ${target.alias} exited with ${version.code}: ${version.stderr.trim()}`);
  }

  // The first connection installs the VS Code Server that matches the
  // client's commit, which the VSIX installation needs.
  const probe = await launch(run, 'probe', 'probe', { executable, layout, vsix });
  run.manifest.probe = { seconds: probe.seconds };
  const serverProduct = JSON.parse((await run.remote(`cat ${sh(layout.product)}`)).stdout);
  run.manifest.target.vscodeServer = {
    version: serverProduct.version, commit: serverProduct.commit, quality: serverProduct.quality, path: path.posix.dirname(layout.product),
  };
  if (serverProduct.commit !== product.commit) throw infra(`the VS Code Server's commit is ${serverProduct.commit}, not the client's ${product.commit}`);

  // The VSIX, installed on the target with the VS Code Server's CLI, as in a
  // remote terminal.
  const remoteVsix = path.posix.join(layout.inputs, path.basename(values.vsix));
  const flags = `--extensions-dir=${sh(layout.extensions)} --user-data-dir=${sh(layout.data)}`;
  const installation = await run.remote(`${sh(layout.cli)} ${flags} --install-extension ${sh(remoteVsix)} --force`, {
    timeout: INSTALL_TIMEOUT_MS, allowFailure: true,
  });
  infrastructure('write vsix-install.log', () => fs.writeFileSync(path.join(run.evidence, 'vsix-install.log'), installation.stdout + installation.stderr));
  if (installation.code !== 0) throw new Failure('fail', `installing ${remoteVsix} on ${target.alias} exited with ${installation.code}; see vsix-install.log`);
  const listed = (await run.remote(`${sh(layout.cli)} ${flags} --list-extensions --show-versions`)).stdout.split('\n').map((line) => line.trim());
  layout.extensionPath = await locateExtension(run, layout, vsix);
  const manifest = JSON.parse((await run.remote(`cat ${sh(path.posix.join(layout.extensionPath, 'package.json'))}`, { allowFailure: true })).stdout || '{}');
  const checks = {
    listed: listed.includes(`${vsix.id}@${vsix.version}`),
    path: layout.extensionPath,
    version: manifest.version ?? null,
    extensionKind: manifest.extensionKind ?? null,
  };
  run.manifest.target.extension = checks;
  if (!checks.listed || checks.version !== vsix.version || JSON.stringify(checks.extensionKind) !== '["workspace"]') {
    throw new Failure('fail', `the installed extension on ${target.alias} is not ${vsix.id} ${vsix.version} with extensionKind ["workspace"] `
      + `at ${layout.extensionPath}: ${JSON.stringify(checks)}`);
  }
  console.log(`-- installed ${vsix.id} ${vsix.version} on ${target.alias} at ${layout.extensionPath}, extensionKind ["workspace"]`);

  for (let repetition = 1; repetition <= values.repeat; repetition++) {
    for (const mode of ['single-file', 'workspace']) {
      await launch(run, `${repetition}-${mode}`, mode, { executable, layout, vsix, repetition });
    }
  }
}

// Gathers the client's and the remote's logs, and the SSH server's and
// Xvfb's, into the evidence directory beside the launches' output, while the
// target is still up. A signal doesn't stop this; each command over SSH has
// its own timeout. What can't be gathered is an infrastructure failure, and
// the rest is still gathered.
async function collectLogs(run) {
  if (run.evidence === undefined) return;
  const signal = new AbortController().signal;
  const gather = async (what, step) => {
    try {
      await step();
    } catch (error) {
      run.record(infra(`could not collect ${what}: ${error.message}`));
    }
  };
  for (const [name, file] of run.target?.logs ?? []) {
    await gather(name, () => {
      if (fs.existsSync(file)) fs.copyFileSync(file, path.join(run.evidence, name));
    });
  }
  const clients = path.join(run.work, 'client');
  for (const name of fs.existsSync(clients) ? fs.readdirSync(clients) : []) {
    const logs = path.join(clients, name, 'user-data', 'logs');
    await gather(`the client's logs of ${name}`, () => {
      if (fs.existsSync(logs)) fs.cpSync(logs, path.join(run.evidence, 'client', name), { recursive: true });
    });
  }
  if (run.connected) {
    await gather('the remote logs', async () => {
      const agent = path.posix.join(run.target.root, 'server', '.vscode-server');
      const found = await run.remote(`cd ${sh(agent)} 2>/dev/null && cat .*.token`, { allowFailure: true, signal });
      for (const token of found.stdout.split(/\s+/)) if (token.length >= 8) run.tokens.add(token);
      // Some of these, or all, don't exist if the test stopped early. tar
      // still archives the others when it can't read one, but exits with 2,
      // so what it sent is kept, and the failure recorded. GNU tar exits with
      // 1 when creating an archive only if a file changed while it read it:
      // the VS Code Server still runs and logs, and the archive is whole.
      const archive = await run.remote(`cd ${sh(run.target.root)} && files=$(ls -d results `
        + 'server/.vscode-server/data/logs server/.vscode-server/.*.log 2>/dev/null); [ -z "$files" ] || tar -c -f - $files',
      { allowFailure: true, signal });
      if (archive.code === 1) {
        run.note(`a remote log changed while it was archived: ${archive.stderr.trim()}`);
      } else if (archive.code !== 0) {
        run.record(infra(`could not collect all of the remote logs: their archive on ${run.target.alias} `
          + `exited with ${archive.code}: ${archive.stderr.trim()}`));
      }
      if (archive.data.length === 0) return;
      const remote = path.join(run.evidence, 'remote');
      fs.mkdirSync(remote, { recursive: true });
      const extract = await runGroup('tar', ['-x', '-f', '-', '-C', remote], {
        env: run.env, input: archive.data, timeout: SSH_TIMEOUT_MS, signal,
      });
      if (!succeeded(extract)) throw new Error(`tar ${outcome(extract)}: ${extract.stderr.trim()}`);
    });
  }
}

// Copies the evidence into the output directory without the VS Code Server's
// connection tokens. Remote-SSH's installation script, which VS Code logs in
// several places, and its log name a connection token for each connection,
// in several spellings and escapes, and only the last is in a token file. So
// every value after any of them is removed from every file. Only redacted
// copies reach the output directory, which CI uploads whatever happens: every
// file is read and searched before any is written, so one that can't be read
// keeps all of them out.
function publishLogs(run) {
  if (run.evidence === undefined || !fs.existsSync(run.evidence)) return;
  const { out } = run.values;
  const names = fs.readdirSync(run.evidence, { recursive: true })
    .filter((name) => fs.lstatSync(path.join(run.evidence, name)).isFile());
  const named = /(?:connection_?token|tkn)\W{1,6}([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/gi;
  for (const name of names) {
    for (const match of fs.readFileSync(path.join(run.evidence, name), 'latin1').matchAll(named)) run.tokens.add(match[1]);
  }
  for (const name of names) {
    let text = fs.readFileSync(path.join(run.evidence, name), 'latin1');
    for (const token of run.tokens) text = text.replaceAll(token, '<redacted>');
    fs.mkdirSync(path.dirname(path.join(out, name)), { recursive: true });
    fs.writeFileSync(path.join(out, name), text, 'latin1');
  }
}

// Checks the prerequisites, and makes the run's directories and environment.
function prepare(run) {
  const { values } = run;
  for (const tool of ['ssh', 'ssh-keygen', 'Xvfb', 'tar']) {
    if (findOnPath(tool) === undefined) throw infra(`${tool} is not on PATH; the Remote-SSH test needs OpenSSH's client and server, Xvfb, and tar`);
  }
  try {
    fs.accessSync(values.sshd, fs.constants.X_OK);
  } catch {
    throw infra(`no SSH server at ${values.sshd}; install OpenSSH's server, or pass --sshd`);
  }
  // VS Code's state, the SSH keys, the remote's files, and the evidence until
  // it is redacted go in a work directory; Unix sockets, whose paths must be
  // short, in /tmp.
  run.work = fs.mkdtempSync(path.join(values.workDir, 'virgil-remote-ssh-'));
  run.sockets = fs.mkdtempSync('/tmp/vrs-');
  console.log(`work directory: ${run.work}`);
  run.evidence = path.join(run.work, 'evidence');
  fs.mkdirSync(run.evidence);
  const home = path.join(run.work, 'client', 'home');
  fs.mkdirSync(path.join(run.sockets, 'c'), { mode: 0o700 });
  for (const dir of ['config', 'cache', 'data', 'state']) fs.mkdirSync(path.join(home, dir), { recursive: true });
  // Every process of the run gets the marker, and a home and directories
  // of its own, so that neither VS Code nor SSH uses the user's.
  const [markerName, markerValue] = run.marker.split('=');
  run.env = isolatedEnv({
    HOME: home,
    XDG_CONFIG_HOME: path.join(home, 'config'),
    XDG_CACHE_HOME: path.join(home, 'cache'),
    XDG_DATA_HOME: path.join(home, 'data'),
    XDG_STATE_HOME: path.join(home, 'state'),
    XDG_RUNTIME_DIR: path.join(run.sockets, 'c'),
    TMPDIR: path.join(run.sockets, 'c'),
    [markerName]: markerValue,
    // Inside WSL, bin/code asks for confirmation unless this is set.
    DONT_PROMPT_WSL_INSTALL: '1',
  });
  for (const name of ['SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY']) {
    delete run.env[name];
  }
}

// Stops the target, if the run made one, and returns what the teardown found;
// one that throws confirmed nothing.
async function stopTarget(run) {
  if (run.target === undefined) return undefined;
  try {
    return await run.target.teardown();
  } catch (error) {
    return { ok: false, stopped: [], left: [], portClosed: null, error: error.message };
  }
}

// The run's result and exit status, once its processes are stopped: an
// interrupt's first, whatever else happened, then a failed check's, then an
// infrastructure failure's, which includes evidence that couldn't be
// collected and a teardown that couldn't confirm that nothing is left.
function verdict(run, teardown) {
  if (run.interrupted !== undefined) return ['INTERRUPTED', SIGNAL_STATUS[run.interrupted.signal]];
  if (run.failures.length > 0 || run.errors.some((error) => error.kind === 'fail')) return ['FAIL', STATUS.FAIL];
  if (run.errors.length > 0 || (teardown !== undefined && !teardown.ok)) return ['INFRA', STATUS.INFRA];
  return ['PASS', STATUS.PASS];
}

async function main() {
  let values;
  try {
    values = options();
  } catch (error) {
    if (!(error instanceof UsageError)) throw error;
    console.error(`run.cjs: ${error.message}\n${usage}`);
    process.exitCode = STATUS.USAGE;
    return;
  }
  infrastructure('prepare the output directory', () => {
    if (values.out === undefined) values.out = fs.mkdtempSync(path.join(os.tmpdir(), 'virgil-remote-ssh-results-'));
    values.out = fs.realpathSync(values.out);
  });
  const run = new Run(values);
  // A signal stops the test's current step. During the log collection and
  // the teardown, it waits for them; either way the run's processes are
  // stopped before the runner exits, and the signal decides its exit status.
  const onSignal = (name) => {
    console.error(`run.cjs: interrupted by ${name}`);
    run.interrupted ??= { signal: name, during: run.phase };
    if (run.phase === 'test') run.controller.abort(new Failure('interrupted', `interrupted by ${name}`));
  };
  for (const name of Object.keys(SIGNAL_STATUS)) process.on(name, () => onSignal(name));
  // An exception that nothing catches, as from an event handler, is the
  // runner's failure, which stops the test as a signal does. Output that
  // can't be written, as to a pipe whose reader has gone, is dropped; the
  // output directory still gets the results.
  process.on('uncaughtException', (error) => {
    const failure = new Failure('fail', `the runner failed: ${error.stack}`);
    if (run.phase === 'test' && !run.signal.aborted) run.controller.abort(failure);
    else run.record(failure);
  });
  for (const stream of [process.stdout, process.stderr]) stream.on('error', () => {});
  console.log(`results: ${values.out}`);

  let teardown;
  try {
    try {
      infrastructure('prepare isolated directories', () => prepare(run));
      await test(run);
    } catch (error) {
      run.record(error);
    }
    if (run.signal.aborted) run.record(run.signal.reason);
    // The evidence first, while the target is still up.
    run.phase = 'log collection';
    await collectLogs(run);
  } catch (error) {
    run.record(infra(`could not collect the logs: ${error.message}`));
  } finally {
    run.phase = 'teardown';
    teardown = await stopTarget(run);
  }
  run.phase = 'report';
  run.manifest.teardown = teardown ?? null;
  let keep = values.keepWork;
  if (teardown !== undefined && !teardown.ok) {
    keep = true;
    console.log(`INFRA: the teardown could not confirm that nothing of the run is left${teardown.error ? `: ${teardown.error}` : ''}`
      + `${teardown.left.map((p) => `\n   ${p.unidentified ? `pid ${p.pid}: could not be identified` : `pid ${p.pid}: ${p.command}`}`).join('')}`
      + `${teardown.portClosed === false ? `\n   port ${run.target.details.port} is still open` : ''}`);
  } else if (teardown !== undefined) {
    const port = run.target.details.port === undefined ? '' : `, and port ${run.target.details.port} is closed`;
    console.log(`-- teardown: stopped ${teardown.stopped.length} processes of the run; none left${port}`);
  }
  try {
    publishLogs(run);
  } catch (error) {
    run.record(infra(`could not copy the logs into ${values.out}: ${error.message}`));
  }
  try {
    if (run.work !== undefined) {
      if (keep) console.log(`work directory kept: ${run.work}`);
      else fs.rmSync(run.work, { recursive: true, force: true });
    }
    if (run.sockets !== undefined && !keep) fs.rmSync(run.sockets, { recursive: true, force: true });
  } catch (error) {
    run.record(infra(`could not remove the work directory: ${error.message}`));
  }

  // A signal that came during the steps above, which don't wait for events,
  // is handled while this waits.
  await sleep(100);
  let [result, status] = verdict(run, teardown);
  const reasons = [
    ...(run.interrupted === undefined ? [] : [`interrupted by ${run.interrupted.signal} during the ${run.interrupted.during}`]),
    ...run.failures,
    ...run.errors.map((error) => error.message),
  ];
  if (teardown !== undefined && !teardown.ok) reasons.push('the teardown could not confirm that nothing of the run is left');
  const summary = () => [
    ...run.manifest.launches.map((l) => {
      const host = hostStopped(l.remoteExtensionHost);
      return `${l.name}: ${l.result ?? 'not finished'}${host === undefined ? '' : ` (${host})`}, ${l.seconds?.toFixed(1) ?? '?'} s`
        + `${l.suiteSeconds == null || l.mode === 'probe' ? '' : ` (suite ${l.suiteSeconds.toFixed(1)} s)`}`
        + `${l.leftRunning === undefined ? '' : `, ${l.leftRunning.length} language server process(es) left running`}`;
    }),
    ...run.notes.map((note) => `info: ${note}`),
    ...reasons.map((reason) => `reason: ${reason}`),
    `VS Code Remote-SSH test: ${result}`,
  ].join('\n');
  // A results file that can't be written makes a passing run's result INFRA,
  // and is removed, so that no part of it says otherwise. The summary goes
  // first, so that the manifest, last, has the result that its failure makes;
  // if the manifest fails, the summary is written again, with that result.
  const write = (name, text) => {
    const file = path.join(values.out, name);
    try {
      fs.writeFileSync(file, text);
      return true;
    } catch (error) {
      reasons.push(`could not write ${name} into ${values.out}: ${error.message}`);
      if (status === STATUS.PASS) [result, status] = ['INFRA', STATUS.INFRA];
      try {
        fs.rmSync(file, { force: true });
      } catch {
        // The console still has the result.
      }
      return false;
    }
  };
  const summaryWritten = write('summary.txt', `${summary()}\n`);
  Object.assign(run.manifest, {
    result, exitStatus: status, reasons, info: run.notes, interrupted: run.interrupted ?? null, finished: new Date().toISOString(),
  });
  if (!write('manifest.json', `${JSON.stringify(run.manifest, null, 2)}\n`) && summaryWritten) write('summary.txt', `${summary()}\n`);
  console.log(`== summary\n${summary()}\nresults: ${values.out}\nRESULT: ${result}`);
  process.exit(status);
}

if (require.main === module) {
  main().catch((error) => {
    // Only a fault in the runner itself, before the run starts or after its
    // teardown, gets here.
    const result = error.kind === 'infra' ? 'INFRA' : 'FAIL';
    console.error(`run.cjs: ${error.stack}\nRESULT: ${result}`);
    process.exit(STATUS[result]);
  });
}

module.exports = { REMOTE_PROCESSES, downloadVSCode, runGroup, serverFiles, readVSIX, locateExtension, settle, stopHost, options, launch, main };
