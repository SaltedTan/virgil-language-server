// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Puts back the macOS client's VS Code preferences after a run (#186). The
// test's VS Code has the bundle identifier of the user's own VS Code, so both
// use the defaults domain com.microsoft.VSCode, and cfprefsd ignores
// CFFIXED_USER_HOME. Each launch rewrites the domain: StoreKit, which
// Electron links, records SKPurchaseIntentUpdatesLastChecked as an app starts,
// and no VS Code setting or flag stops it.
//
// So, before the run's first VS Code launch, save() exports the domain. After
// the test's last VS Code process has exited, restore() exports it again and
// puts back only the keys that the run changed: each changed or removed key's
// old value, with defaults import, which replaces the top-level keys that its
// file has and keeps the others, and defaults delete for each key that the
// run added. It then exports the domain once more, and succeeds only if that
// equals the saved copy, compared as parsed property lists. If the user's own
// VS Code runs at the start or at the end, it may have changed the domain too,
// so restore() changes nothing and reports the run's changes.
//
// The domain is read and written only with defaults, which goes through
// cfprefsd, never by its file. The exports stay in a directory of mode 700 in
// the run's --work-dir, each of mode 600; the results name only keys and how
// they changed, never their values. Python 3's plistlib parses the exports
// into tagged JSON, which keeps each value's type: {dict}, {array}, {string},
// {integer} and {real} as strings, {bool}, {data} in base64, and {date} in
// ISO 8601.

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { parseInfo, within } = require('./focus.cjs');

const DOMAIN = 'com.microsoft.VSCode';
const USER_APP = '/Applications/Visual Studio Code.app';

const PYTHON = String.raw`
import base64, datetime, json, os, plistlib, sys

def encode(value):
    if isinstance(value, dict):
        return {'dict': {key: encode(item) for key, item in value.items()}}
    if isinstance(value, list):
        return {'array': [encode(item) for item in value]}
    if isinstance(value, bool):
        return {'bool': value}
    if isinstance(value, int):
        return {'integer': str(value)}
    if isinstance(value, float):
        return {'real': repr(value)}
    if isinstance(value, str):
        return {'string': value}
    if isinstance(value, bytes):
        return {'data': base64.b64encode(value).decode('ascii')}
    if isinstance(value, datetime.datetime):
        return {'date': value.isoformat()}
    raise SystemExit('unsupported value type ' + type(value).__name__)

def decode(tagged):
    [(kind, value)] = tagged.items()
    if kind == 'dict':
        return {key: decode(item) for key, item in value.items()}
    if kind == 'array':
        return [decode(item) for item in value]
    if kind == 'bool':
        return bool(value)
    if kind == 'integer':
        return int(value)
    if kind == 'real':
        return float(value)
    if kind == 'string':
        return value
    if kind == 'data':
        return base64.b64decode(value)
    if kind == 'date':
        return datetime.datetime.fromisoformat(value)
    raise SystemExit('unsupported tag ' + kind)

mode, name = sys.argv[1], sys.argv[2]
if mode == 'read':
    with open(name, 'rb') as f:
        value = plistlib.load(f)
    if not isinstance(value, dict):
        raise SystemExit('the export is not a dictionary')
    json.dump(encode(value), sys.stdout)
else:
    value = decode(json.load(sys.stdin))
    with os.fdopen(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
        plistlib.dump(value, f)
`;

// A tagged value with its dictionaries' keys sorted, for comparison.
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  }
  return value;
}

function same(a, b) {
  return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
}

// The top-level keys of a domain, given as {key: tagged value}, that differ
// from before to after, by kind, each list sorted.
function compare(before, after) {
  const result = { changed: [], added: [], removed: [] };
  for (const key of [...new Set([...Object.keys(before), ...Object.keys(after)])].sort()) {
    if (!Object.hasOwn(before, key)) result.added.push(key);
    else if (!Object.hasOwn(after, key)) result.removed.push(key);
    else if (!same(before[key], after[key])) result.changed.push(key);
  }
  return result;
}

// What puts back the keys that changed from before to after: put, the old
// values of the changed and removed keys, for defaults import; and remove,
// the added keys, for defaults delete. A domain that didn't exist is {}.
function restorePlan(before, after) {
  const changes = compare(before, after);
  const put = Object.fromEntries([...changes.changed, ...changes.removed].sort().map((key) => [key, before[key]]));
  return { ...changes, put, remove: changes.added };
}

// The domain that a plan makes of current, as defaults import and delete do.
function applyPlan(current, plan) {
  const result = Object.fromEntries(Object.entries(current).filter(([key]) => !plan.remove.includes(key)));
  for (const [key, value] of Object.entries(plan.put)) result[key] = value;
  return result;
}

function command(file, args, options = {}) {
  const result = spawnSync(file, args, { encoding: 'utf8', timeout: 30000, maxBuffer: 64 * 1024 * 1024, ...options });
  if (result.error !== undefined) throw new Error(`${file} ${args[0]} failed: ${result.error.message}`);
  return result;
}

function defaults(args) {
  const result = command('defaults', args);
  if (result.status !== 0) throw new Error(`defaults ${args[0]} exited with ${result.status}: ${result.stderr.trim().split('\n')[0]}`);
}

// Python's messages could quote a value, so they are not reported.
function python(args, input) {
  const result = command('python3', ['-I', '-c', PYTHON, ...args], { input });
  if (result.status !== 0) throw new Error(`python3 could not ${args[0]} ${path.basename(args[1])}: exit status ${result.status}`);
  return result.stdout;
}

// Exports the domain into file, of mode 600, and returns its keys.
function exportDomain(domain, file) {
  fs.writeFileSync(file, '', { mode: 0o600, flag: 'wx' });
  defaults(['export', domain, file]);
  fs.chmodSync(file, 0o600);
  return JSON.parse(python(['read', file])).dict;
}

// The running copies of VS Code, other than the test's app, that may write
// the domain: applications with its bundle identifier, by LaunchServices,
// and processes whose executable is in the user's app, by ps. Each is
// described by its PID or ASN and its path.
function otherVSCode({ app, bundleId = DOMAIN, userApp = USER_APP }) {
  const found = [];
  const find = command('lsappinfo', ['find', `bundleid=${bundleId}`]);
  if (find.status !== 0) throw new Error(`lsappinfo find exited with ${find.status}`);
  for (const asn of find.stdout.match(/ASN:0x[0-9a-f]+-0x[0-9a-f]+:/g) ?? []) {
    const info = parseInfo(command('lsappinfo', ['info', asn]).stdout);
    if (!within(info.bundlePath, app)) found.push(`${asn} ${info.bundlePath}`);
  }
  for (const p of runningIn(userApp)) found.push(`pid ${p.pid} ${p.path}`);
  return found;
}

// The processes whose executable is in the app bundle at app.
function runningIn(app) {
  const ps = command('ps', ['-A', '-ww', '-o', 'pid=', '-o', 'comm=']);
  if (ps.status !== 0) throw new Error(`ps exited with ${ps.status}`);
  return ps.stdout.split('\n').flatMap((line) => {
    const [, pid, file] = /^\s*(\d+)\s(.*)$/.exec(line) ?? [];
    return pid !== undefined && within(file.trim(), app) ? [{ pid: Number(pid), path: file.trim() }] : [];
  });
}

// Saves the domain into a new directory in workDir, before the run's first
// VS Code launch. app is the test's app bundle.
function save({ workDir, app, domain = DOMAIN, bundleId = domain, userApp = USER_APP }) {
  const dir = fs.mkdtempSync(path.join(workDir, 'vrp-'));
  fs.chmodSync(dir, 0o700);
  const state = { dir, app, domain, bundleId, userApp };
  state.userAtStart = otherVSCode(state);
  state.before = exportDomain(domain, path.join(dir, 'before.plist'));
  return state;
}

// Puts back the keys that the run changed, once the test's VS Code has
// exited; returns the outcome: restored, unchanged, skipped (the user's
// VS Code ran), or failed, with the keys that the run changed.
function restore(state) {
  const result = {
    outcome: 'failed', domain: state.domain, changed: [], added: [], removed: [], exports: state.dir,
    userVSCode: { atStart: state.userAtStart, atEnd: null },
  };
  try {
    const left = runningIn(state.app);
    if (left.length > 0) throw new Error(`the test's VS Code still runs: ${left.map((p) => `pid ${p.pid}`).join(', ')}`);
    const after = exportDomain(state.domain, path.join(state.dir, 'after.plist'));
    const plan = restorePlan(state.before, after);
    Object.assign(result, { changed: plan.changed, added: plan.added, removed: plan.removed });
    result.userVSCode.atEnd = otherVSCode(state);
    if (plan.changed.length + plan.added.length + plan.removed.length === 0) {
      result.outcome = 'unchanged';
      return result;
    }
    if (state.userAtStart.length > 0 || result.userVSCode.atEnd.length > 0) {
      result.outcome = 'skipped';
      return result;
    }
    if (Object.keys(plan.put).length > 0) {
      const file = path.join(state.dir, 'restore.plist');
      python(['write', file], JSON.stringify({ dict: plan.put }));
      defaults(['import', state.domain, file]);
    }
    for (const key of plan.remove) defaults(['delete', state.domain, key]);
    const check = exportDomain(state.domain, path.join(state.dir, 'check.plist'));
    if (!same(check, state.before)) {
      const still = compare(state.before, check);
      throw new Error(`after the restore, the domain differs from the saved copy: ${describe(still)}`);
    }
    result.outcome = 'restored';
  } catch (error) {
    result.outcome = 'failed';
    result.error = error.message;
  }
  return result;
}

// Key names by kind of change, such as "changed a, b; added c".
function describe({ changed, added, removed }) {
  const parts = [['changed', changed], ['added', added], ['removed', removed]].filter(([, keys]) => keys.length > 0);
  return parts.map(([kind, keys]) => `${kind} ${keys.join(', ')}`).join('; ') || 'no keys';
}

module.exports = { DOMAIN, PYTHON, applyPlan, compare, describe, restore, restorePlan, same, save };
