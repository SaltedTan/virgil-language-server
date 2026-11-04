// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Keeps the macOS client's VS Code from holding the user's focus (#186).
// VS Code activates itself when it opens a window, whether it is started
// directly or through LaunchServices with open -g, and no setting, flag, or
// LaunchServices runtime key (CanBecomeFrontmost) stops that. So the guard
// listens for LaunchServices' becameFrontmost notifications, and when the
// test's VS Code, identified by its bundle path, becomes the front
// application, it activates the application that was in front before, the
// notification's LSOtherASN, with open -a, which goes through LaunchServices
// and needs no Automation or Accessibility permission. It never acts on any
// other application, such as the user's own VS Code, which has the same
// bundle identifier. Each episode is recorded with how long the test's VS Code
// was in front, from LaunchServices' notifications.
//
// The guard runs in a process of its own (see spawnGuard): the runner's event
// loop, which samples process groups with blocking helpers on macOS, delayed
// its reaction from about 40 ms to about 450 ms. Usage of that process:
//
//   node focus.cjs APP WORK
//
// WORK, the run's work directory, only puts the process among the run's for
// the client teardown. It prints READY once it listens, then a line for each
// episode, and, when its standard input closes, the result as JSON, and exits.

const { execFile, spawn } = require('node:child_process');
const path = require('node:path');

const ASN = /^ASN:0x[0-9a-f]+-0x[0-9a-f]+:$/;

function lsappinfo(args) {
  return new Promise((resolve) => {
    execFile('lsappinfo', args, { encoding: 'utf8', timeout: 5000 }, (error, stdout) => resolve(error ? '' : stdout));
  });
}

// The name, bundle path, and bundle identifier in lsappinfo info's output.
function parseInfo(text) {
  const field = (pattern) => pattern.exec(text)?.[1] ?? null;
  return {
    name: field(/^\s*"([^"]*)"/),
    bundlePath: field(/^\s*bundle path="(.*)"\s*$/m),
    bundleId: field(/^\s*bundleID="(.*)"\s*$/m),
  };
}

// The ASN that became frontmost, and the one that lost it, in one line of
// lsappinfo listen's output; undefined for other lines.
function parseNotification(line) {
  if (!line.startsWith('Notification: kLSNotifyBecameFrontmost')) return undefined;
  const asn = /"LSASN"=(ASN:0x[0-9a-f]+-0x[0-9a-f]+:)/.exec(line)?.[1];
  const other = /"LSOtherASN"=(ASN:0x[0-9a-f]+-0x[0-9a-f]+:)/.exec(line)?.[1];
  return asn === undefined ? undefined : { asn, other };
}

function within(file, app) {
  return file === app || (file ?? '').startsWith(`${app}/`);
}

// Starts the guard for the VS Code at app, the app bundle's path; resolves
// once LaunchServices listens. stop() ends the listener, by its own child
// process, and returns what happened.
async function start({ app, log = () => {} }) {
  const episodes = [];
  const errors = [];
  let open;
  let queue = Promise.resolve();
  const child = spawn('lsappinfo', ['listen', '+becameFrontmost', 'forever'], { stdio: ['ignore', 'pipe', 'ignore'] });
  child.on('error', (error) => errors.push(`lsappinfo listen did not start: ${error.message}`));
  const handle = async ({ asn, other }, at) => {
    if (open !== undefined && asn !== open.asn) {
      open.backAt = at;
      open.ms = at - open.at;
      log(`focus: the test's VS Code was in front for ${open.ms} ms; ${open.returnedTo ?? 'another application'} is in front again`);
      open = undefined;
      return;
    }
    if (open !== undefined) return;
    const front = parseInfo(await lsappinfo(['info', asn]));
    if (!within(front.bundlePath, app)) return;
    const previous = other !== undefined && ASN.test(other) ? parseInfo(await lsappinfo(['info', other])) : undefined;
    open = { at, asn, returnedTo: previous?.name ?? null };
    episodes.push(open);
    if (previous?.bundlePath && !within(previous.bundlePath, app)) {
      execFile('open', ['-a', previous.bundlePath], (error) => {
        if (error) errors.push(`open -a ${previous.bundlePath} failed: ${error.message}`);
      });
    } else {
      open.returnedTo = null;
      errors.push(`the test's VS Code became frontmost, but the previous application ${previous?.name ?? other ?? '(none)'} has no bundle to activate`);
    }
  };
  let pending = '';
  child.stdout.on('data', (data) => {
    const at = Date.now();
    pending += data;
    const lines = pending.split('\n');
    pending = lines.pop();
    for (const line of lines) {
      const notification = parseNotification(line);
      if (notification !== undefined) queue = queue.then(() => handle(notification, at), (error) => errors.push(error.message));
    }
  });
  // lsappinfo registers for notifications as it starts.
  await new Promise((resolve) => setTimeout(resolve, 300));
  return {
    stop: async () => {
      await queue.catch(() => {});
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
      const total = episodes.reduce((sum, e) => sum + (e.ms ?? 0), 0);
      return {
        episodes: episodes.map(({ at, backAt, ms, returnedTo }) => ({ at: new Date(at).toISOString(), ms: ms ?? null, back: backAt !== undefined, returnedTo })),
        frontMs: total,
        unreturned: episodes.filter((e) => e.backAt === undefined).length,
        errors,
      };
    },
  };
}

// Starts the guard in a process of its own, for the VS Code at app; resolves
// once it listens. stop() closes its input and resolves with its result.
function spawnGuard({ app, work, log = () => {} }) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [__filename, app, work], { stdio: ['pipe', 'pipe', 'inherit'] });
    let pending = '';
    let result;
    let ready = false;
    const exited = new Promise((done) => child.on('close', done));
    const stop = async () => {
      child.stdin.end();
      const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
      await exited;
      clearTimeout(timer);
      return result ?? { episodes: [], frontMs: 0, unreturned: 0, errors: ['the focus guard exited without a result'] };
    };
    child.on('error', () => {});
    child.stdin.on('error', () => {});
    child.stdout.on('data', (data) => {
      pending += data;
      const lines = pending.split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (line === 'READY' && !ready) {
          ready = true;
          resolve({ stop });
        } else if (line.startsWith('RESULT ')) {
          result = JSON.parse(line.slice(7));
        } else if (line !== '') {
          log(line);
        }
      }
    });
    void exited.then(() => {
      if (!ready) resolve({ stop: async () => ({ episodes: [], frontMs: 0, unreturned: 0, errors: ['the focus guard did not start'] }) });
    });
  });
}

if (require.main === module) {
  const [app] = process.argv.slice(2);
  void (async () => {
    const guard = await start({ app: path.resolve(app), log: (line) => process.stdout.write(`${line}\n`) });
    process.stdout.write('READY\n');
    let stopping = false;
    const finish = async () => {
      if (stopping) return;
      stopping = true;
      process.stdout.write(`RESULT ${JSON.stringify(await guard.stop())}\n`);
      process.exit(0);
    };
    process.stdin.on('end', finish).on('close', finish).on('error', finish);
    process.stdin.resume();
    for (const name of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(name, finish);
  })();
}

module.exports = { parseInfo, parseNotification, spawnGuard, start, within };
