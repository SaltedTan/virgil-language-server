// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The first process of a process group that processes.cjs's startGroup
// starts: it runs a command in its group, and stays until the runner releases
// it, so that the group's number stays the run's (see startGroup). Usage:
//
//   node keeper.cjs '{"command": FILE, "args": [...], "given": [STDIN, STDOUT, STDERR]}'
//
// The command's standard input, output, and error are file descriptors 3, 4,
// and 5, each where given is true, and /dev/null otherwise; it gets no other
// descriptor of the keeper's. Its start, its exit, or why it didn't start
// goes to standard output, one JSON object a line. Standard input is the
// runner's: "release" on it lets the keeper exit. If it ends without one, the
// runner has gone, so the keeper stops its group itself: SIGTERM, then
// SIGKILL STOP_GRACE_MS later.

const { spawn } = require('node:child_process');
const fs = require('node:fs');

const STOP_GRACE_MS = 5000;

const { command, args, given } = JSON.parse(process.argv[2]);
// The group's SIGINT, SIGTERM, and SIGHUP are for the rest of it; only the
// group's SIGKILL ends the keeper early. Node.js gives the command each
// signal's default action.
for (const name of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(name, () => {});
process.stdout.on('error', () => {});
const report = (message) => process.stdout.write(`${JSON.stringify(message)}\n`);
const stdio = given.map((yes, i) => (yes ? 3 + i : 'ignore'));
let child;
try {
  // Node.js moves descriptors 3 to 5 into 0 to 2 in the command, and closes
  // them there.
  child = spawn(command, args, { stdio });
} catch (error) {
  report({ error: { message: error.message, code: error.code } });
}
for (const fd of stdio) if (fd !== 'ignore') fs.closeSync(fd);
child?.on('spawn', () => report({ spawn: child.pid }));
child?.on('error', (error) => {
  report({ error: { message: error.message, code: error.code, errno: error.errno, syscall: error.syscall, path: error.path } });
});
child?.on('exit', (code, signal) => report({ exit: [code, signal] }));

let input = '';
process.stdin.on('data', (data) => {
  input += data;
});
process.stdin.on('error', () => {});
process.stdin.on('close', () => {
  if (input.includes('release')) process.exit(0);
  process.kill(-process.pid, 'SIGTERM');
  setTimeout(() => process.kill(-process.pid, 'SIGKILL'), STOP_GRACE_MS);
});
