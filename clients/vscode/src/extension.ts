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
// The server starts this executable from the directory of its own resolved
// path (/proc/self/exe on Linux, proc_pidpath on macOS).
const WORKER_NAME = 'virgil-lsp-worker';

// A startup problem the user can fix. Its message says how.
class StartupError extends Error {}

let output: vscode.LogOutputChannel;
let client: LanguageClient | undefined;
// Starts and stops run one at a time, in the order they were requested.
let queue: Promise<void> = Promise.resolve();

class QueuedLanguageClient extends LanguageClient {
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
      if (event.affectsConfiguration(SERVER_PATH_SETTING)) {
        output.info(`${SERVER_PATH_SETTING} changed; restarting the server`);
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
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: 'file', language: 'virgil' },
      { scheme: 'untitled', language: 'virgil' },
    ],
    // The client also writes the server's stderr here and, while the
    // channel's log level is Trace, the message trace.
    outputChannel: output,
  };
  // The client ID `virgil` makes the client read `virgil.trace.server`.
  const started = new QueuedLanguageClient('virgil', CHANNEL_NAME, serverOptions, clientOptions);
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
  if (running === undefined || !running.isRunning()) return;
  try {
    await running.stop();
  } catch (error) {
    output.error(`Stopping the server failed: ${describe(error)}`);
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
