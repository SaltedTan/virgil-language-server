// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// How suite.cjs reports its checks, by their IDs in test/e2e/coverage.json.
// A check starts with begin(id), and every line of the suite after it belongs
// to the check that began last, as in test/e2e/nvim/check.lua, until passed()
// or failed() ends it. begin(id) again resumes a check that hasn't ended, so
// that a check can be interleaved with another. Each outcome is a PASS or FAIL
// line on standard output, and, when run.cjs --results sets
// VIRGIL_E2E_OBSERVATIONS, also a JSON line appended to that file, which
// run.cjs writes into its result stream (see results.cjs). Without it, nothing
// is written.

const fs = require('node:fs');

// Marks an error whose check has reported it, so that it isn't reported again
// as it goes up the stack.
const REPORTED = Symbol('reported');

function failureCode(error) {
  return String(error?.message).includes('timed out waiting for') ? 'wait-timeout' : 'error';
}

function reporter({ file = process.env.VIRGIL_E2E_OBSERVATIONS, out = console, now = Date.now } = {}) {
  let current;
  const started = new Map();
  const ended = new Set();
  let deferred;
  const record = (entry) => {
    if (file) fs.appendFileSync(file, `${JSON.stringify(entry)}\n`);
  };
  const end = (status, fields) => {
    const id = current;
    current = undefined;
    ended.add(id);
    return { id, status, startedAt: started.get(id), endedAt: now(), ...fields };
  };
  const emit = (entry) => {
    if (entry.status === 'pass') out.log(`PASS: ${entry.id}`);
    record(entry);
  };
  const flush = () => {
    if (deferred === undefined) return;
    emit(deferred);
    deferred = undefined;
  };
  return {
    begin(id) {
      if (ended.has(id)) throw new Error(`${id} has already reported its outcome`);
      if (!started.has(id)) started.set(id, now());
      current = id;
    },
    passed(observation = 'every assertion of the check passed') {
      if (current === undefined) throw new Error('no check has begun');
      emit(end('pass', { observation }));
    },
    // Ends the current check as passed, but reports it only at the next
    // flush(), or before a failure: vscode.startup.installed-copy passes before
    // the extension activates, and a launch that fails then may be retried.
    passLater(observation = 'every assertion of the check passed') {
      if (current === undefined) throw new Error('no check has begun');
      deferred = end('pass', { observation });
    },
    flush,
    // Reports error as the current check's failure, unless it has no current
    // check or the error was reported already. Returns error, marked as
    // reported, for the caller to throw.
    failed(error, code = failureCode(error)) {
      flush();
      if (current !== undefined && !error?.[REPORTED]) {
        const id = current;
        out.error(`FAIL: ${id}: ${error?.stack ?? error}`);
        const message = String(error?.message ?? error) || 'the check failed';
        record(end('fail', { observation: message, reason: message, failureCode: code }));
      }
      if (error !== null && typeof error === 'object') error[REPORTED] = true;
      return error;
    },
    get current() {
      return current;
    },
  };
}

module.exports = { REPORTED, failureCode, reporter };
