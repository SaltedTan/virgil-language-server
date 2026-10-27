// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

import { constants } from 'node:fs';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { clearTimeout, setTimeout } from 'node:timers';
import * as vscode from 'vscode';
import {
  ErrorAction,
  ErrorCodes,
  ErrorHandler,
  LanguageClient,
  LanguageClientOptions,
  MessageSignature,
  ProtocolRequestType,
  ProtocolRequestType0,
  RequestParam,
  RequestType,
  RequestType0,
  ResponseError,
  ServerOptions,
  State,
  TransportKind,
} from 'vscode-languageclient/node';

const CHANNEL_NAME = 'Virgil Language Server';
const SERVER_PATH_SETTING = 'virgil.server.path';
const VIRGIL_ROOT_SETTING = 'virgil.virgilRoot';
const SEMANTIC_TOKENS_SETTING = 'virgil.semanticTokens';
// Settings read only when the server starts.
const RESTART_SETTINGS = [SERVER_PATH_SETTING, VIRGIL_ROOT_SETTING, 'virgil.inlayHints.types', 'virgil.inlayHints.parameterNames',
  SEMANTIC_TOKENS_SETTING];
// Virgil's own extension, which colors Virgil with its own semantic tokens.
// VS Code shows one provider's tokens, not both, so `auto` defers to it.
const VIRGIL_EXTENSION = 'titzer.virgil';
// The server starts this executable from the directory of its own resolved
// path (/proc/self/exe on Linux, proc_pidpath on macOS).
const WORKER_NAME = 'virgil-lsp-worker';
// Files whose changes on disk the server needs to hear about: project files
// and sources. The server registers the same watchers dynamically.
const WATCHED_FILES = ['**/.virgil-lsp.json', '**/*.v3'];
// Matches vscode-languageclient 11.0.0's default stop() shutdown timeout.
const NOTIFICATION_DRAIN_TIMEOUT_MS = 2000;
// The codes of the errors after which the connection to the server closes: a
// read or a write of the server's pipe that fails, as when the server has
// gone (EPIPE, or ECONNRESET if it left input unread), and a write after
// that (ERR_STREAM_DESTROYED). Node destroys the pipe on a failed read or
// write, and the connection closes with it, even if the server still runs.
const CLOSING_ERRORS = new Set(['EPIPE', 'ECONNRESET', 'ERR_STREAM_DESTROYED']);

// A startup problem the user can fix. Its message says how.
class StartupError extends Error {}

let output: vscode.LogOutputChannel;
let extensionPath: string;
let client: QueuedLanguageClient | undefined;
// Starts and stops run one at a time, in the order they were requested.
let queue: Promise<void> = Promise.resolve();

class QueuedLanguageClient extends LanguageClient {
  // The client stops listening to its file watchers but doesn't dispose them.
  watchers: vscode.Disposable[] = [];
  notificationDrainTimeout = NOTIFICATION_DRAIN_TIMEOUT_MS;
  private readonly pendingNotifications = new Set<Promise<void>>();
  private readonly pendingRequests = new Set<(error: ResponseError<void>) => void>();
  private requestFailure: ResponseError<void> | undefined;

  constructor(id: string, name: string, serverOptions: ServerOptions, clientOptions: LanguageClientOptions) {
    // The library's default handler, created once the client exists, keeps
    // its count of crashes for the client's whole life.
    let defaultHandler: ErrorHandler | undefined;
    super(id, name, serverOptions, {
      ...clientOptions,
      errorHandler: {
        // The default handler stops the client on an error without a count,
        // such as the EPIPE of a write to a server that has just died (#216),
        // and a stopping client ignores the connection's close, which is what
        // restarts a crashed server. After an error that closes the
        // connection, the close decides instead; the library still logs the
        // error, without a notification. Any other error, such as output
        // from the server that isn't a message, can leave the connection
        // open, with no close to decide, so the default handler does: it
        // reports the error and, for a read error like that one, stops the
        // client.
        error: async (error, message, count) => {
          if (CLOSING_ERRORS.has((error as NodeJS.ErrnoException).code ?? '')) {
            return { action: ErrorAction.Continue, handled: true };
          }
          const result = await defaultHandler!.error(error, message, count);
          if (result.action === ErrorAction.Shutdown) {
            // A lost response also makes shutdown time out. In 11.0.0 that
            // path never disposes the connection, leaving requests pending
            // even after the server is killed. End our callers' waits now;
            // the default handler still reports the fault and stops the server.
            const failure = new ResponseError<void>(ErrorCodes.MessageReadError,
              `The language server connection failed: ${error.message}`);
            this.requestFailure = failure;
            for (const reject of this.pendingRequests) reject(failure);
            this.pendingRequests.clear();
          }
          return result;
        },
        // The library's policy: restart, unless the server crashed 5 times in
        // 3 minutes; then stop, with its message.
        closed: () => defaultHandler!.closed(),
      },
      middleware: {
        ...clientOptions.middleware,
        // The library checks the client's state when a request arrives, then
        // waits for the start and the pending didOpen and full-text changes
        // before it calls this, right before the write (11.0.0). A stop that
        // began during that wait has already sent shutdown, and the request
        // would follow it (#212). Reject it as that first check does, so
        // providers return their default without a notification, and nothing
        // is written for it, not even a $/cancelRequest.
        sendRequest: (type, param, token, next) => {
          if (this.state !== State.Running) {
            return Promise.reject(new ResponseError(ErrorCodes.ConnectionInactive, 'Client is not running'));
          }
          return next(type, param, token);
        },
      },
    });
    defaultHandler = this.createDefaultErrorHandler();
  }

  // The library's overloads, so that callers keep their result and parameter
  // types.
  override sendRequest<R, PR, E, RO>(type: ProtocolRequestType0<R, PR, E, RO>, token?: vscode.CancellationToken): Promise<R>;
  override sendRequest<P, R, PR, E, RO>(type: ProtocolRequestType<P, R, PR, E, RO>, params: NoInfer<RequestParam<P>>, token?: vscode.CancellationToken): Promise<R>;
  override sendRequest<R, E>(type: RequestType0<R, E>, token?: vscode.CancellationToken): Promise<R>;
  override sendRequest<P, R, E>(type: RequestType<P, R, E>, params: NoInfer<RequestParam<P>>, token?: vscode.CancellationToken): Promise<R>;
  override sendRequest<R>(method: string, token?: vscode.CancellationToken): Promise<R>;
  override sendRequest<R>(method: string, param: any, token?: vscode.CancellationToken): Promise<R>;
  override sendRequest<R>(type: any, ...params: [any?, vscode.CancellationToken?]): Promise<R> {
    if (this.requestFailure !== undefined) return Promise.reject(this.requestFailure);
    // Include requests waiting for document synchronization, not just those
    // already written. Both outcomes of the library's promise stay handled
    // if its answer arrives after we have rejected the caller's request.
    return new Promise<R>((resolve, reject) => {
      this.pendingRequests.add(reject);
      const forget = () => this.pendingRequests.delete(reject);
      super.sendRequest<R>(type, ...params).then(
        (value) => { forget(); resolve(value); },
        (error) => { forget(); reject(error); },
      );
    });
  }

  override handleFailedRequest<T>(
    type: MessageSignature, token: vscode.CancellationToken | undefined, error: any, defaultValue: T,
    showNotification = true, throwOnCancel = false,
  ): T {
    // JSON-RPC also rejects a request whose write failed, dropping the
    // original error code. The connection handler above also rejects pending
    // requests on a read error. Let that handler decide whether to notify;
    // keep each request's log and rejection.
    return super.handleFailedRequest(type, token, error, defaultValue,
      showNotification && !(error instanceof ResponseError
        && (error.code === ErrorCodes.MessageWriteError || error.code === ErrorCodes.MessageReadError)), throwOnCancel);
  }

  override start(): Promise<void> {
    // The client also calls start before each request and notification. While
    // it is starting or running, that call only waits for the current start,
    // and must not wait behind a restart that stops this client. Other calls,
    // such as automatic recovery, start a server, so they join the queue.
    if (this.state === State.Starting || this.state === State.Running) return super.start();
    return enqueue(async () => {
      if (client === this) await this.startInQueue();
    });
  }

  startInQueue(): Promise<void> {
    this.requestFailure = undefined;
    return super.start();
  }

  override async sendNotification(type: any, params?: any): Promise<void> {
    const sending = super.sendNotification(type, params);
    if (this.state !== State.Starting && this.state !== State.Running) return sending;
    this.pendingNotifications.add(sending);
    try {
      await sending;
    } finally {
      this.pendingNotifications.delete(sending);
    }
  }

  async stopInQueue(): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<void>((resolve) => {
      timer = setTimeout(() => {
        output.warn(`Notification drain timed out (${this.pendingNotifications.size} still pending); stopping the server`);
        resolve();
      }, this.notificationDrainTimeout);
    });
    try {
      await Promise.race([
        (async () => {
          while (this.pendingNotifications.size > 0) {
            await Promise.allSettled(this.pendingNotifications);
          }
        })(),
        deadline,
      ]);
    } finally {
      clearTimeout(timer);
    }
    await super.stop();
  }
}

export function activate(context: vscode.ExtensionContext): Promise<void> {
  extensionPath = context.extensionPath;
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

// Whether the server sends semantic tokens: `on` and `off` decide, and
// `auto`, the default, turns them off while titzer.virgil is enabled.
function semanticTokens(): boolean {
  const setting = vscode.workspace.getConfiguration().get<string>(SEMANTIC_TOKENS_SETTING, 'auto');
  if (setting === 'on' || setting === 'off') return setting === 'on';
  if (vscode.extensions.getExtension(VIRGIL_EXTENSION) === undefined) return true;
  output.info(`Semantic tokens are off: ${VIRGIL_EXTENSION} is enabled, and ${SEMANTIC_TOKENS_SETTING} is auto`);
  return false;
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
    initializationOptions: {
      ...(virgilRoot === undefined ? {} : { virgilRoot }),
      inlayHints: {
        types: vscode.workspace.getConfiguration().get<boolean>('virgil.inlayHints.types', true),
        parameterNames: vscode.workspace.getConfiguration().get<boolean>('virgil.inlayHints.parameterNames', true),
      },
      semanticTokens: semanticTokens(),
    },
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
    if (running.isRunning()) await running.stopInQueue();
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
  const configured = vscode.workspace.getConfiguration().get<string>(SERVER_PATH_SETTING, '').trim();
  let setting = configured;
  if (configured !== '') {
    output.info(`Server selection: ${configured} (explicit ${SERVER_PATH_SETTING})`);
  } else {
    const marker = path.join(extensionPath, 'server-target.json');
    let target: string | undefined;
    try {
      target = JSON.parse(await fs.readFile(marker, 'utf8')).target;
      if (typeof target !== 'string') throw new Error('missing target');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
        throw new StartupError(`Invalid bundled server metadata: ${describe(error)}. Reinstall the VSIX.`);
      }
    }
    if (target !== undefined) {
      const host = `${process.platform}-${process.arch}`;
      if (target !== host) {
        throw new StartupError(`This VSIX bundles a server for ${target}, but the workspace extension host is ${host}. `
          + `Install the VSIX for the workspace host (including Remote-SSH or WSL), or set ${SERVER_PATH_SETTING}.`);
      }
      setting = path.join(extensionPath, 'server', 'virgil-lsp');
      output.info(`Server selection: ${setting} (bundled for ${target}; no explicit ${SERVER_PATH_SETTING})`);
      if (target === 'darwin-arm64') output.info('The bundled x86-64 server requires Rosetta 2 on Apple Silicon.');
    } else {
      setting = 'virgil-lsp';
      output.info(`Server selection: virgil-lsp on PATH (no bundle and no explicit ${SERVER_PATH_SETTING})`);
    }
  }
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
// root that isn't an absolute path. Forward invalid nonempty overrides so
// bundled fallback cannot silently hide the error.
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
    return setting;
  }
  const env = (process.env.VIRGIL_LOC ?? '').trim();
  if (env !== '' && path.isAbsolute(env)) {
    output.info(`Virgil root: ${env} (from VIRGIL_LOC)`);
    return env;
  }
  output.info(
    env === ''
      ? `No Virgil root: neither ${VIRGIL_ROOT_SETTING} nor VIRGIL_LOC is set`
      : `Invalid Virgil root: VIRGIL_LOC is not an absolute path: ${env}`,
  );
  return env === '' ? undefined : env;
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
