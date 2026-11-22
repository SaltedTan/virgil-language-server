// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The result stream of remote-ssh/run.cjs --results: one remote-ssh run's
// JSON Lines events, by the contract in test/e2e/RESULTS.md. run.cjs, the
// outer launcher, calls it, and it alone writes the stream. In each launch,
// bootstrap.cjs records its ssh.identity checks, and suite.cjs its vscode
// checks, in an observations file on the host (see ../observations.cjs),
// which run.cjs reads over SSH once the launch has ended. The launcher's own
// checks come from what run.cjs observed: the preflight, the client's pins,
// the server's start on the host, the probe, the remote installation, each
// launch and the language servers after it, the evidence, and the teardown.

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const shared = require('../results.cjs');

const SUITE = 'remote-ssh';
const PREFLIGHT = 'ssh.infrastructure.preflight';
const CLIENT_PINS = 'ssh.infrastructure.client-pins';
const ARTIFACT_START = 'ssh.infrastructure.artifact-start';
const PROBE = 'ssh.infrastructure.probe';
const REMOTE_INSTALL = 'ssh.infrastructure.remote-install';
const LAUNCH = 'ssh.infrastructure.launch';
const HOST_EXIT = 'ssh.shutdown.remote-host-exit';
const EVIDENCE = 'ssh.infrastructure.evidence';
const TEARDOWN = 'ssh.infrastructure.teardown';
// The checks that run.cjs observes, each in its profile, if it has one only;
// bootstrap.cjs and suite.cjs observe the others.
const LAUNCHER = new Map([
  [PREFLIGHT, 'wrapper'], [CLIENT_PINS, 'wrapper'], [ARTIFACT_START, 'wrapper'], [PROBE, 'probe'],
  [REMOTE_INSTALL, 'wrapper'], [LAUNCH, undefined], [HOST_EXIT, undefined], [EVIDENCE, 'wrapper'], [TEARDOWN, 'wrapper'],
]);
// A connection token of the VS Code Server, as publishLogs in run.cjs finds
// them, which the stream mustn't hold.
const TOKEN = /((?:connection[-_]?token|tkn)\W{1,6})[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
// The largest time that a JavaScript Date holds.
const MAX_TIME = 8.64e15;
// The checkout, whose paths the stream gives as <repo>/...
const REPO = path.resolve(path.dirname(shared.MANIFEST), '../..');
// The placeholders of the run's directories and of its inputs outside the
// checkout, in text from the run; the work directory is <work>.
const PLACEHOLDERS = {
  sockets: '<sockets>', out: '<out>', remote: '<remote>', vsix: '<vsix>', server: '<server>', archive: '<archive>',
  virgilRoot: '<virgil-root>', target: '<target>', clientVsix: '<client-vsix>',
};
const INPUTS = new Set(['vsix', 'server', 'archive', 'virgilRoot', 'target', 'clientVsix']);

function runner() {
  // Loaded when used: run.cjs imports this module.
  return require('./run.cjs');
}

function sha256Of(data) {
  return crypto.createHash('sha256').update(data).digest('hex');
}

// The planned observations of a remote-ssh run, from the manifest: each check
// of the suite in each of its profiles, once per repetition of a repeated
// profile. Every condition of a check must hold in every run of the suite,
// as vsix does.
function plan(manifest, repeat) {
  const suite = manifest.suites.find((s) => s.id === SUITE);
  const profiles = new Map(suite.profiles.map((profile) => [profile.id, profile]));
  const always = new Set(manifest.conditions.filter((c) => c.always.includes(SUITE)).map((c) => c.id));
  const keys = [];
  const levels = {};
  for (const check of manifest.checks) {
    if (!check.suites.includes(SUITE)) continue;
    if (check.when !== null && !always.has(check.when)) {
      throw new Error(`remote-ssh/results.cjs: ${check.id} has the condition ${check.when}, which the Remote-SSH writer doesn't decide`);
    }
    levels[check.id] = check.level;
    for (const profile of check.profiles.filter((id) => profiles.has(id))) {
      const iterations = profiles.get(profile).repeated ? repeat : 1;
      for (let iteration = 1; iteration <= iterations; iteration++) keys.push({ id: check.id, profile, iteration });
    }
  }
  const order = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  keys.sort((a, b) => order(a.id, b.id) || order(a.profile, b.profile) || a.iteration - b.iteration);
  return { keys, levels };
}

// remote-ssh/run.cjs's command, with the repository's paths relative to its
// root and other paths as placeholders.
function commandOf(args, cwd, env) {
  return shared.commandWords(args, cwd, {
    words: env.npm_lifecycle_event === 'test:e2e:remote-ssh' ? ['npm', 'run', 'test:e2e:remote-ssh', '--']
      : ['node', 'clients/vscode/test/e2e/remote-ssh/run.cjs'],
    placeholders: {
      '--vsix': '<vsix>', '--server': '<server>', '--archive': '<archive>', '--virgil-root': '<virgil-root>',
      '--out': '<out>', '--work-dir': '<work-dir>', '--sshd': '<sshd>', '--target': '<target>', '--client-vsix': '<client-vsix>',
    },
    valued: ['--repeat', '--release', '--purpose'],
    positional: '<argument>',
  });
}

// The host of a separate-host run, from its `uname -srm`.
function hostMachine(kernel) {
  const [, release, arch] = typeof kernel === 'string' ? kernel.trim().split(/\s+/) : [];
  return { os: 'linux', release: release || 'unknown', arch: arch || 'unknown', cpu: null, translation: null };
}

// The server's fields that come from its executables, or unavailable for
// reason: its target and SHA-256, and its worker's.
function executableFields(server, worker) {
  return {
    sha256: sha256Of(server),
    target: shared.targetOf(server.subarray(0, 20), () => server.toString('latin1')),
    workerSha256: worker === undefined ? shared.unavailable('virgil-lsp-worker is missing beside the server') : sha256Of(worker),
  };
}

// The server's fields from its --version text, or unavailable for reason.
function versionFields(text, reason) {
  if (typeof text !== 'string' || text === '') {
    return { versionText: shared.unavailable(reason), version: null, sourceRevision: null, virgilRevision: null };
  }
  return {
    versionText: text,
    version: /^virgil-lsp (\S+)$/.exec(text.split('\n')[0])?.[1] ?? null,
    sourceRevision: /^\s*source revision: (\S+)\s*$/m.exec(text)?.[1] ?? null,
    virgilRevision: /^\s*virgil revision: (\S+)\s*$/m.exec(text)?.[1] ?? null,
  };
}

class Results {
  // file: the stream, which this empties at once. repeat: the run's
  // repetitions. run: the launcher's Run, whose manifest and values the
  // run-start's provenance comes from (see start). pins: the Remote-SSH
  // extensions that the client installs. now, env, and machine stand in for
  // the clock, the environment, and this machine's details in tests.
  constructor({ file, repeat = 1, release = null, purpose = 'development', command, editorVersion, pins = [],
    manifest = shared.MANIFEST, now = Date.now, env = process.env, machine = shared.machine }) {
    if (!shared.PURPOSES.includes(purpose)) throw new Error(`--purpose takes one of ${shared.PURPOSES.join(', ')}, not ${purpose}`);
    if (release === '') throw new Error('--release takes a version, not an empty string');
    fs.writeFileSync(file, '');
    this.file = file;
    this.release = release;
    this.purpose = purpose;
    this.command = command;
    this.editorVersion = editorVersion;
    this.pins = pins;
    this.manifestFile = manifest;
    ({ keys: this.keys, levels: this.levels } = plan(JSON.parse(fs.readFileSync(manifest, 'utf8')), repeat));
    this.now = now;
    this.env = env;
    this.machine = machine;
    this.launchedAt = now();
    this.run = { manifest: {}, values: {} };
    this.paths = {};
    this.directories({});
    // The launcher's check in progress, which an exception fails.
    this.current = { id: PREFLIGHT, startedAt: this.launchedAt };
    // What run.cjs has learned for the run-start's provenance.
    this.configuration = {};
    this.started = false;
    this.seq = 0;
    // Events before the run-start, which start() writes after it.
    this.queue = [];
    this.reported = new Map();
    this.firstFailure = undefined;
    this.issues = [];
    this.attempts = {};
    // The failures that a check reported, which aren't issues of the run.
    this.attributed = new Set();
    this.unavailableProvenance = false;
  }

  // The run's directories and inputs, which the stream names by placeholders
  // (see PLACEHOLDERS), as run.cjs learns them: the local work directory, the
  // sockets' directory, the output directory, and the host's work directory,
  // and --vsix, --server, --archive, --virgil-root, --target, and
  // --client-vsix, unless they are in the checkout. Each call adds to the
  // earlier ones.
  directories(paths) {
    for (const [name, value] of Object.entries(paths)) if (value !== undefined) this.paths[name] = value;
    const outside = (file) => {
      const inside = path.relative(REPO, file);
      return inside === '..' || inside.startsWith(`..${path.sep}`) || path.isAbsolute(inside);
    };
    const pairs = Object.entries(PLACEHOLDERS)
      .filter(([name]) => typeof this.paths[name] === 'string' && (!INPUTS.has(name) || outside(this.paths[name])))
      .map(([name, placeholder]) => [this.paths[name], placeholder]);
    const base = shared.scrubber(this.paths.work, pairs);
    this.scrub = (text) => {
      let result = String(text).replace(TOKEN, '$1<redacted>');
      for (const token of this.run.tokens ?? []) result = result.split(token).join('<redacted>');
      return base(result);
    };
  }

  write(event) {
    const line = { schema: 1, event: event.event, runId: this.runId, seq: this.seq, ...event };
    fs.appendFileSync(this.file, `${JSON.stringify(line)}\n`);
    this.seq += 1;
  }

  // Starts the launcher's check id, which an exception then fails (see fail).
  begin(id) {
    this.current = { id, startedAt: this.now() };
  }

  // Records the launcher's check id as passed, from when it began.
  passed(id, observation, { iteration = 1, profile = LAUNCHER.get(id), startedAt, endedAt } = {}) {
    if (this.current?.id === id) {
      startedAt ??= this.current.startedAt;
      this.current = undefined;
    }
    this.check(id, profile, iteration, 'pass', { startedAt, endedAt, observation });
  }

  // Records the launcher's check id as failed, with reason.
  failed(id, reason, failureCode, { iteration = 1, profile = LAUNCHER.get(id), startedAt, endedAt } = {}) {
    if (this.current?.id === id) {
      startedAt ??= this.current.startedAt;
      this.current = undefined;
    }
    this.check(id, profile, iteration, 'fail', { startedAt, endedAt, observation: reason, reason, failureCode });
  }

  // Whether a check reported error, or the error that run.cjs's Run.record
  // wrapped in it.
  reportedError(error) {
    return this.attributed.has(error) || (error?.cause !== undefined && this.attributed.has(error.cause));
  }

  // Fails the launcher's check in progress with error, which ended the test,
  // unless a check has reported it already or it is an interrupt.
  fail(error) {
    if (this.reportedError(error) || error?.kind === 'interrupted' || this.current === undefined) return;
    this.attributed.add(error);
    const code = this.current.id === PREFLIGHT ? 'preflight'
      : error?.kind === 'infra' ? 'infrastructure' : error?.kind === 'fail' ? 'failure' : 'error';
    this.failed(this.current.id, String(error?.message ?? error) || 'the check failed', code);
  }

  // The server's executables, before they are uploaded: file and worker, and
  // for an archive, its extracted directory, which may have manifest.json.
  server({ file, worker, archiveRoot }) {
    const read = (name) => (fs.existsSync(name) ? fs.readFileSync(name) : undefined);
    this.serverFields = executableFields(fs.readFileSync(file), read(worker));
    if (archiveRoot !== undefined) {
      const manifest = read(path.join(archiveRoot, 'manifest.json'));
      this.archiveManifestSha256 = manifest === undefined ? null : sha256Of(manifest);
    }
  }

  // The client's settings, as each launch writes them, and the host's
  // machine settings, as the single-file and workspace launches write them.
  configure(clientSettings, machineSettings) {
    this.configuration = {
      probe: sha256Of(clientSettings),
      'single-file': sha256Of(`${clientSettings}\n${machineSettings}`),
      workspace: sha256Of(`${clientSettings}\n${machineSettings}`),
    };
  }

  // Writes the run-start, from what the launcher learned, then the events
  // before it. run.cjs calls it once the extension is installed on the host;
  // a run that stops before that gets it from end().
  start() {
    if (this.started) return;
    const { manifest: m, values } = this.run;
    const { sha256, readZipEntry, zipEntries } = runner();
    const stopped = 'the launcher stopped before it found the server';
    let executables = this.serverFields;
    let virgilRootSha256 = m.virgilRoot?.libTreeSha256 ?? null;
    if (values.bundled) {
      try {
        const worker = readZipEntry(values.vsix, 'extension/server/virgil-lsp-worker', true);
        executables = executableFields(readZipEntry(values.vsix, 'extension/server/virgil-lsp'), worker);
        const prefix = 'extension/server/virgil/lib/';
        const files = zipEntries(values.vsix).filter((e) => e.name.startsWith(prefix) && !e.name.endsWith('/'))
          .map((e) => [e.name.slice(prefix.length), e]).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
        virgilRootSha256 = files.length === 0 ? null
          : sha256Of(files.map(([name, e]) => `${sha256Of(e.read())}  ${name}\n`).join(''));
      } catch (error) {
        const missing = shared.unavailable(this.scrub(`the VSIX's bundled server could not be read: ${error.message}`));
        executables = { sha256: missing, target: missing, workerSha256: missing };
      }
    } else if (executables === undefined) {
      const missing = shared.unavailable(m.server?.sha256 === undefined ? stopped : 'virgil-lsp or virgil-lsp-worker is missing');
      const digest = (name) => (typeof m.server?.sha256?.[name] === 'string' ? m.server.sha256[name] : missing);
      executables = { sha256: digest('virgil-lsp'), target: missing, workerSha256: digest('virgil-lsp-worker') };
    }
    const server = {
      ...versionFields(this.versionText, this.scrub(this.versionReason ?? stopped)),
      target: executables.target, sha256: executables.sha256, workerSha256: executables.workerSha256,
    };
    const rawVsix = values.vsix === undefined ? shared.unavailable('no VSIX was given') : shared.vsixProvenance(values.vsix);
    const vsix = shared.isUnavailable(rawVsix) ? shared.unavailable(this.scrub(rawVsix.unavailable)) : rawVsix;
    const fixtureSha256 = this.fixtureSha256 ?? shared.unavailable('the launcher stopped before it uploaded the fixture');
    const separate = values.target !== undefined;
    const client = this.machine(null);
    const host = separate ? hostMachine(m.target?.os?.kernel) : client;
    const commit = m.client?.vscode?.commit;
    const pins = this.pins.map(({ id, version, sha256: digest }) => ({ name: id, version, commit: null, sha256: digest }));
    if (m.client?.vsix !== undefined) {
      let version = null;
      try {
        version = JSON.parse(readZipEntry(m.client.vsix.path, 'extension/package.json')).version ?? null;
      } catch {
        // The pin keeps its SHA-256.
      }
      pins.push({ name: 'client-vsix', version, commit: null, sha256: m.client.vsix.sha256 });
    }
    const planned = new Set(this.keys.map((k) => k.profile));
    const ssh = m.client?.ssh?.version;
    const started = this.now();
    this.runId = `remote-ssh-${shared.utc(started).replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')}-${crypto.randomBytes(4).toString('hex')}`;
    this.unavailableProvenance = [...Object.values(server), vsix, fixtureSha256].some(shared.isUnavailable);
    this.write({
      event: 'run-start', time: shared.utc(started), release: this.release, purpose: this.purpose, suite: SUITE,
      setup: separate ? 'ssh-mac-client' : 'ssh-linux-client',
      lane: this.env.GITHUB_ACTIONS === 'true' && this.env.GITHUB_JOB ? this.env.GITHUB_JOB : 'local',
      command: this.command, coverageSha256: sha256(this.manifestFile),
      expected: this.keys, facts: {},
      provenance: {
        server,
        archive: values.archive === undefined ? null : {
          name: path.basename(values.archive), sha256: m.server?.archiveSha256 ?? sha256(values.archive),
          manifestSha256: this.archiveManifestSha256 ?? null,
        },
        vsix, clientMode: 'vsix',
        harness: shared.harness(),
        editor: {
          name: 'vscode', version: this.editorVersion ?? null,
          commit: typeof commit === 'string' && /^[0-9a-f]{40}$/.test(commit) ? commit : null, arch: null,
        },
        client, host,
        transport: { kind: 'ssh', topology: separate ? 'separate-host' : 'loopback', extensionHost: 'remote' },
        pins,
        configurationSha256: Object.fromEntries(Object.entries(this.configuration).filter(([profile]) => planned.has(profile))),
        fixtureSha256,
        virgilRootSha256: typeof virgilRootSha256 === 'string' ? virgilRootSha256 : null,
        runtimes: { node: process.versions.node, ...(typeof ssh === 'string' && ssh !== '' ? { ssh } : {}) },
        ci: shared.ci(this.env),
      },
      attestation: null,
    });
    this.started = true;
    for (const event of this.queue.splice(0)) this.write(event);
  }

  planned(id, profile, iteration) {
    return typeof id === 'string' && this.keys.some((k) => k.id === id && k.profile === profile && k.iteration === iteration);
  }

  // Records one observation, unless it is unplanned or a repeat, which are
  // issues of the run.
  check(id, profile, iteration, status, { startedAt, endedAt = this.now(), observation, reason = null, failureCode = null }) {
    const key = `${id} ${profile} ${iteration}`;
    const label = `${id} (${profile}, iteration ${iteration})`;
    if (!this.planned(id, profile, iteration)) {
      this.issues.push(`${label} was observed, but the run doesn't plan it`);
      return;
    }
    if (this.reported.has(key)) {
      this.issues.push(`${label} was observed again`);
      return;
    }
    this.reported.set(key, status);
    if (status === 'fail') this.firstFailure ??= id;
    const event = {
      event: 'check', time: shared.utc(endedAt), id, profile, iteration, status, level: this.levels[id],
      startedAt: shared.utc(startedAt ?? endedAt), durationMs: Math.max(0, Math.round(endedAt - (startedAt ?? endedAt))),
      reason: reason === null ? null : this.scrub(reason) || status, expectation: null, failureCode,
      observation: this.scrub(observation) || status, artifacts: [],
    };
    if (this.started) this.write(event);
    else this.queue.push(event);
  }

  // A new attempt of the launch called name, the mode's launch in the
  // repetition iteration: run.cjs records what it observes in the returned
  // object, with the text of the launch's observations file. A retry replaces
  // the attempt that failed, whose observations don't count.
  attempt(name, mode, iteration = 1) {
    const observed = { mode, iteration, attempt: (this.attempts[name]?.attempt ?? 0) + 1, startedAt: this.now(), stage: 'launch' };
    this.attempts[name] = observed;
    return observed;
  }

  // Writes the checks of the launch called name, from its last attempt, once
  // it has ended or thrown error.
  launch(name, error) {
    const observed = this.attempts[name];
    // An interrupted run's stream gets no run-end, so it is incomplete anyway.
    if (observed === undefined || error?.kind === 'interrupted') return;
    const { mode: profile, iteration } = observed;
    if (observed.observationsError !== undefined) {
      this.issues.push(`the ${name} launch's observations could not be read: ${this.scrub(observed.observationsError)}`);
    }
    if (observed.observations !== undefined) {
      const { found, issues } = shared.parseObservations(observed.observations, {
        time: (value) => Number.isFinite(value) && value > 0 && value < MAX_TIME, scrub: this.scrub,
        writer: 'bootstrap.cjs or suite.cjs',
      });
      this.issues.push(...issues);
      for (const entry of found) {
        if (LAUNCHER.has(entry.id) || !this.planned(entry.id, profile, iteration)) {
          this.issues.push(`the ${name} launch reported ${entry.id}, which it doesn't plan`);
        } else {
          this.check(entry.id, profile, iteration, entry.status, entry);
        }
      }
    }
    if (profile === 'probe') return;
    const retried = observed.attempt > 1 ? ', in its retry' : '';
    const at = { iteration, profile };
    if (observed.launch !== undefined) {
      const { problems, codes, startedAt, endedAt, seconds, passLines } = observed.launch;
      if (problems.length === 0) {
        this.passed(LAUNCH, `bootstrap.cjs started and passed, VS Code exited with status 0 after ${seconds.toFixed(1)} seconds, `
          + `the suite printed ${passLines} PASS line(s) and no FAIL line, and VS Code's processes didn't outlive it${retried}`,
        { ...at, startedAt, endedAt });
      } else {
        this.failed(LAUNCH, problems.join('; '), codes[0], { ...at, startedAt, endedAt });
      }
    } else if (error !== undefined && observed.stage !== 'leak-check') {
      this.attributed.add(error);
      this.failed(LAUNCH, `the launch failed: ${error.message}`, error.retryOutcome === undefined ? 'error' : 'not-started',
        { ...at, startedAt: observed.startedAt });
    }
    if (observed.hostExit !== undefined) {
      const { problem, lingered, startedAt, endedAt } = observed.hostExit;
      const seconds = `${runner().SERVER_EXIT_MS / 1000} seconds`;
      if (problem === undefined) {
        this.passed(HOST_EXIT, lingered
          ? `VS Code left the remote extension host running; the test stopped it, and no language server was left on the host ${seconds} later`
          : `no language server was left on the host ${seconds} after the window's client exited`, { ...at, startedAt, endedAt });
      } else {
        this.failed(HOST_EXIT, problem, 'servers-left', { ...at, startedAt, endedAt });
      }
    } else if (error !== undefined && observed.stage === 'leak-check') {
      this.attributed.add(error);
      this.failed(HOST_EXIT, `the leak check failed: ${error.message}`, 'leak-check', { ...at, startedAt: observed.exitedAt });
    }
  }

  // The errors that no check reported, which are issues of the run.
  unattributed(errors) {
    return errors.filter((error) => !this.reportedError(error)).map((error) => error.message);
  }

  // Ends the run: the run-start if it is still missing, a skip for each
  // planned check that didn't run after a failure, and the run-end. exitCode
  // is what run.cjs would exit with without --results, cleanup whether the
  // teardown confirmed that nothing of the run is left and the work
  // directory is gone, and issues the launcher's own. Returns run.cjs's exit
  // status: 0 exactly when the run passed.
  end({ exitCode, cleanup, issues = [] }) {
    this.start();
    this.issues.push(...issues.map((issue) => this.scrub(issue)));
    const missing = this.keys.filter((k) => !this.reported.has(`${k.id} ${k.profile} ${k.iteration}`));
    if (this.firstFailure !== undefined) {
      for (const key of missing) {
        this.check(key.id, key.profile, key.iteration, 'skip', { observation: 'not run', reason: `not run: ${this.firstFailure} failed` });
      }
    } else if (missing.length > 0) {
      const more = missing.length > 1 ? ` and ${missing.length - 1} more` : '';
      this.issues.push(`${missing[0].id} (${missing[0].profile}, iteration ${missing[0].iteration})${more} have no check event`);
    }
    const counts = { pass: 0, fail: 0, xfail: 0, xpass: 0, skip: 0 };
    for (const status of this.reported.values()) counts[status] += 1;
    const complete = this.reported.size === this.keys.length;
    const preflightFailed = this.reported.get(`${PREFLIGHT} wrapper 1`) === 'fail';
    const passed = complete && counts.pass === this.keys.length && exitCode === 0 && cleanup === 'verified'
      && this.issues.length === 0 && !this.unavailableProvenance;
    const status = passed ? 'pass' : this.unavailableProvenance || preflightFailed ? 'unavailable' : 'fail';
    const final = passed ? 0 : exitCode || 1;
    const finished = this.now();
    this.write({
      event: 'run-end', time: shared.utc(finished), complete, status, durationMs: Math.max(0, Math.round(finished - this.launchedAt)),
      expectedCount: this.keys.length, checkCount: this.reported.size, counts, exitCode: final, signal: null, cleanup,
      issues: this.issues,
    });
    const outcome = { pass: 'passed', fail: 'failed', unavailable: 'unavailable' }[status];
    console.log(`Results: ${this.file}: run ${outcome} (${this.reported.size} of ${this.keys.length} observations): `
      + Object.entries(counts).map(([name, count]) => `${count} ${name}`).join(', '));
    return final;
  }

  // Leaves the stream without a run-end after an interrupt, so that it is
  // incomplete: the run-start and the checks so far, if they can be written.
  abandon() {
    try {
      this.start();
    } catch (error) {
      console.error(`remote-ssh/results.cjs: could not write the run-start: ${error.message}`);
    }
    console.error(`remote-ssh/results.cjs: the run was interrupted, so ${this.file} has no run-end`);
  }
}

module.exports = {
  ARTIFACT_START, CLIENT_PINS, EVIDENCE, HOST_EXIT, LAUNCH, LAUNCHER, PREFLIGHT, PROBE, REMOTE_INSTALL, TEARDOWN,
  Results, commandOf, plan,
};
