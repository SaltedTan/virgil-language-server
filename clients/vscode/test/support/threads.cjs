// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// A real process whose main thread has exited while another thread runs, for
// the tests of process discovery: the Python command, and startThread, which
// starts it and waits for that state.

const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const processes = require('../e2e/processes.cjs');

const pythonSkip = spawnSync('python3', ['-I', '-c', '']).status === 0 ? false : 'python3 is not on PATH';

// A command, in Python, whose main thread exits at once while another thread
// runs on for 30 seconds; it ignores SIGTERM. Linux lists it as a zombie with
// two threads meanwhile.
const THREAD_COMMAND = `
import ctypes, signal, threading, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
threading.Thread(target=time.sleep, args=(30,)).start()
ctypes.CDLL(None).pthread_exit(None)
`;

// Why THREAD_COMMAND's process doesn't apply here: only Linux lists a process
// whose main thread has exited as a zombie, and the command needs python3.
const THREAD_SKIP = process.platform !== 'linux' ? 'only Linux lists a process whose main thread has exited as a zombie' : pythonSkip;

// Whether p, a process given by PID and start time, still runs: it hasn't
// exited, or only its main thread has.
function runs(p) {
  const info = processes.processInfo(p.pid);
  return info?.start === p.start && !processes.zombie(info);
}

// The interpreter that python3 runs, whose copy can run under another path.
function interpreter() {
  return spawnSync('python3', ['-I', '-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).stdout.trim();
}

// Starts THREAD_COMMAND as a process that the test owns, and returns it with
// its start time and executable once its main thread has exited and another
// thread runs. With exeDir, it runs a copy of the interpreter there, so that
// the process's executable is under that directory, as the Remote-SSH leak
// check finds the run's processes. env is the process's environment.
async function startThread({ exeDir, env = process.env } = {}) {
  let command = 'python3';
  if (exeDir !== undefined) {
    command = path.join(exeDir, 'python3');
    fs.copyFileSync(interpreter(), command);
    fs.chmodSync(command, 0o755);
  }
  const child = spawn(command, ['-I', '-c', THREAD_COMMAND], { env, stdio: 'ignore' });
  await new Promise((resolve) => child.on('spawn', resolve));
  const exited = new Promise((resolve) => child.on('exit', (code, signal) => resolve(signal ?? code)));
  for (const deadline = Date.now() + 5000; ; await new Promise((resolve) => setTimeout(resolve, 20))) {
    const info = processes.processInfo(child.pid);
    if (info?.state === 'Z' && info.threads > 1) return { child, exited, start: info.start, exe: command };
    if (Date.now() > deadline) {
      child.kill('SIGKILL');
      throw new Error('the main thread did not exit');
    }
  }
}

module.exports = { THREAD_COMMAND, THREAD_SKIP, runs, startThread };
