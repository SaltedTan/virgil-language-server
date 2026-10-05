// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

import { constants } from 'node:fs';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as vscode from 'vscode';
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from 'vscode-languageclient/node';

const CHANNEL_NAME = 'Virgil Language Server';
const SERVER_PATH_SETTING = 'virgil.server.path';
const VIRGIL_ROOT_SETTING = 'virgil.virgilRoot';
// Settings read only when the server starts.
const RESTART_SETTINGS = [SERVER_PATH_SETTING, VIRGIL_ROOT_SETTING];
// The server starts this executable from the directory of its own resolved
// path (/proc/self/exe on Linux, proc_pidpath on macOS).
const WORKER_NAME = 'virgil-lsp-worker';
// Files whose changes on disk the server needs to hear about: project files
// and sources. The server registers the same watchers dynamically.
const WATCHED_FILES = ['**/.virgil-lsp.json', '**/*.v3'];

// A startup problem the user can fix. Its message says how.
class StartupError extends Error {}

let output: vscode.LogOutputChannel;
let client: QueuedLanguageClient | undefined;
// Starts and stops run one at a time, in the order they were requested.
let queue: Promise<void> = Promise.resolve();

class QueuedLanguageClient extends LanguageClient {
  // The client stops listening to its file watchers but doesn't dispose them.
  watchers: vscode.Disposable[] = [];

  override start(): Promise<void> {
    return enqueue(async () => {
      if (client === this) await this.startInQueue();
    });
  }

  startInQueue(): Promise<void> {
    return super.start();
  }
}

export function activate(context: vscode.ExtensionContext): Promise<void> {
  const channel = vscode.window.createOutputChannel(CHANNEL_NAME, { log: true });
  output = ignoreWriteFailures(channel);
  context.subscriptions.push(
    channel,
    vscode.commands.registerCommand('virgil.server.restart', () => enqueue(restart)),
    vscode.workspace.onDidChangeConfiguration((event) => {
      const changed = RESTART_SETTINGS.find((setting) => event.affectsConfiguration(setting));
      if (changed !== undefined) {
        output.info(`${changed} changed; restarting the server`);
        void enqueue(restart);
      }
    }),
  );
  return enqueue(start);
}

// VS Code waits for the returned promise, so the server gets `shutdown` and
// `exit` before the extension host goes away.
export function deactivate(): Promise<void> {
  return enqueue(stop);
}

function enqueue(task: () => Promise<void>): Promise<void> {
  queue = queue.then(task, task);
  return queue;
}

async function restart(): Promise<void> {
  await stop();
  await start();
}

async function start(): Promise<void> {
  let executable: string;
  try {
    executable = await resolveServer();
  } catch (error) {
    reportStartupFailure(error);
    return;
  }
  const serverOptions: ServerOptions = {
    command: executable,
    // The stdio transport appends `--stdio` to the arguments.
    transport: TransportKind.stdio,
  };
  const virgilRoot = resolveVirgilRoot();
  const watchers = WATCHED_FILES.map((pattern) => vscode.workspace.createFileSystemWatcher(pattern));
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: 'file', language: 'virgil' },
      { scheme: 'untitled', language: 'virgil' },
      // Project files, so that configuration diagnostics follow unsaved edits.
      { scheme: 'file', pattern: '**/.virgil-lsp.json' },
    ],
    initializationOptions: virgilRoot === undefined ? {} : { virgilRoot },
    // Changes made outside the editor, such as a checkout, refresh projects.
    // The client batches these events with those of the server's own
    // registration, and the server handles a repeated event once.
    synchronize: { fileEvents: watchers },
    // The client also writes the server's stderr here and, while the
    // channel's log level is Trace, the message trace.
    outputChannel: output,
  };
  // The client ID `virgil` makes the client read `virgil.trace.server`.
  const started = new QueuedLanguageClient('virgil', CHANNEL_NAME, serverOptions, clientOptions);
  started.watchers = watchers;
  client = started;
  output.info(`Starting ${executable} --stdio`);
  try {
    await started.startInQueue();
  } catch (error) {
    reportStartupFailure(error);
    return;
  }
  const info = started.initializeResult?.serverInfo;
  output.info(`Started ${info?.name ?? 'the server'} ${info?.version ?? '(no version reported)'}`);
}

async function stop(): Promise<void> {
  const running = client;
  client = undefined;
  if (running === undefined) return;
  try {
    if (running.isRunning()) await running.stop();
  } catch (error) {
    output.error(`Stopping the server failed: ${describe(error)}`);
  } finally {
    (running.visibleDocuments as typeof running.visibleDocuments & vscode.Disposable).dispose();
    for (const watcher of running.watchers) watcher.dispose();
  }
}

function reportStartupFailure(error: unknown): void {
  const message = describe(error);
  output.error(`The server did not start: ${message}`);
  const action = 'Show Output';
  void vscode.window
    .showErrorMessage(`Virgil language server did not start: ${message}`, action)
    .then((choice) => {
      if (choice === action) output.show();
    });
}

// A closed channel throws on writes, for example while the window closes and
// the client traces `shutdown`. Logging must not abort the shutdown sequence.
function ignoreWriteFailures(channel: vscode.LogOutputChannel): vscode.LogOutputChannel {
  return new Proxy(channel, {
    get(target, property) {
      const value: unknown = Reflect.get(target, property, target);
      if (typeof value !== 'function') return value;
      return (...args: unknown[]) => {
        try {
          return value.apply(target, args);
        } catch {
          return undefined;
        }
      };
    },
  });
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

// Returns the server executable to start, after checking that it and the
// analysis worker beside it can run. Throws a StartupError otherwise.
async function resolveServer(): Promise<string> {
  if (process.platform === 'win32') {
    throw new StartupError(
      'native Windows is not supported. Open the folder in WSL 2 with the WSL extension, '
      + 'and install virgil-lsp inside the WSL distribution.',
    );
  }
  const setting = vscode.workspace.getConfiguration().get<string>(SERVER_PATH_SETTING, 'virgil-lsp').trim();
  const executable = await findExecutable(setting);
  // The server finds the worker beside its resolved path, not beside a symlink.
  const resolved = await fs.realpath(executable);
  const worker = path.join(path.dirname(resolved), WORKER_NAME);
  if (!(await isExecutableFile(worker))) {
    const via = resolved === executable ? '' : ` (${executable} resolves to ${resolved})`;
    throw new StartupError(
      `${worker} is missing or not executable${via}. `
      + `Keep ${WORKER_NAME} in the same directory as virgil-lsp.`,
    );
  }
  return executable;
}

// Returns the Virgil root for projects' virgilDependencies: the setting, or
// else VIRGIL_LOC from the extension host's environment. The server ignores a
// root that isn't an absolute path, so such a value is reported and not sent.
function resolveVirgilRoot(): string | undefined {
  const setting = vscode.workspace.getConfiguration().get<string>(VIRGIL_ROOT_SETTING, '').trim();
  if (setting !== '') {
    if (path.isAbsolute(setting)) {
      output.info(`Virgil root: ${setting} (from ${VIRGIL_ROOT_SETTING})`);
      return setting;
    }
    const message = `${VIRGIL_ROOT_SETTING} must be an absolute path, not ${setting}. `
      + 'Projects with virgilDependencies stay disabled until it is fixed.';
    output.warn(message);
    void vscode.window.showWarningMessage(`Virgil: ${message}`);
    return undefined;
  }
  const env = (process.env.VIRGIL_LOC ?? '').trim();
  if (env !== '' && path.isAbsolute(env)) {
    output.info(`Virgil root: ${env} (from VIRGIL_LOC)`);
    return env;
  }
  output.info(
    env === ''
      ? `No Virgil root: neither ${VIRGIL_ROOT_SETTING} nor VIRGIL_LOC is set`
      : `No Virgil root: ignoring VIRGIL_LOC, which is not an absolute path: ${env}`,
  );
  return undefined;
}

async function findExecutable(setting: string): Promise<string> {
  if (setting === '') {
    throw new StartupError(`${SERVER_PATH_SETTING} is empty. Set it to virgil-lsp or to an absolute path.`);
  }
  if (path.isAbsolute(setting)) {
    if (await isExecutableFile(setting)) return setting;
    throw new StartupError(
      `${setting} (from ${SERVER_PATH_SETTING}) is missing or not an executable file.`,
    );
  }
  if (setting.includes(path.sep)) {
    throw new StartupError(
      `${SERVER_PATH_SETTING} must be a command name or an absolute path, not the relative path ${setting}.`,
    );
  }
  for (const dir of (process.env.PATH ?? '').split(path.delimiter)) {
    // An empty entry would mean the extension host's working directory.
    if (dir === '' || !path.isAbsolute(dir)) continue;
    const file = path.join(dir, setting);
    if (await isExecutableFile(file)) return file;
  }
  throw new StartupError(
    `${setting} was not found on the extension host's PATH (with Remote-SSH or Remote-WSL, the remote machine's). `
    + `Install it there, or set ${SERVER_PATH_SETTING} to its absolute path.`,
  );
}

async function isExecutableFile(file: string): Promise<boolean> {
  try {
    const stats = await fs.stat(file);
    if (!stats.isFile()) return false;
    await fs.access(file, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}
