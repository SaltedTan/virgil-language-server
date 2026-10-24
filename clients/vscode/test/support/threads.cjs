// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Real processes whose main thread has exited while another thread runs, for
// the tests of process discovery: the Python commands, and the functions that
// start them and wait for that state.

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

// THREAD_COMMAND's process, but non-dumpable, as one that has executed a setuid
// program or called prctl(PR_SET_DUMPABLE, 0) is: other processes of the same
// user can't read its executable or environment, while its command line stays
// readable.
const HIDDEN_THREAD_COMMAND = `
import ctypes, signal, threading, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
assert ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) == 0
threading.Thread(target=time.sleep, args=(30,)).start()
ctypes.CDLL(None).pthread_exit(None)
`;

// A command, in Python, that starts HIDDEN_THREAD_COMMAND's process, given as
// its argument, under python3 from PATH, with no environment, prints its PID,
// and runs on for 30 seconds. The hidden process's parent is the run's, which
// identifies it when its own command line doesn't.
const PARENT_COMMAND = `
import shutil, subprocess, sys, time
child = subprocess.Popen(['python3', '-I', '-c', sys.argv[1]], executable=shutil.which('python3') or sys.executable, env={})
print(child.pid, flush=True)
time.sleep(30)
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

// A copy of the interpreter in exeDir, which a process runs so that its
// executable is under that directory, as the Remote-SSH leak check finds the
// run's processes.
function copyInterpreter(exeDir) {
  const command = path.join(exeDir, 'python3');
  fs.copyFileSync(interpreter(), command);
  fs.chmodSync(command, 0o755);
  return command;
}

// Waits until the process with this PID has exited its main thread while
// another thread runs, and returns its start time; throws if it doesn't within
// five seconds. It doesn't stop the process.
async function waitThread(pid) {
  for (const deadline = Date.now() + 5000; ; await new Promise((resolve) => setTimeout(resolve, 20))) {
    const info = processes.processInfo(pid);
    if (info?.state === 'Z' && info.threads > 1) return info.start;
    if (Date.now() > deadline) throw new Error('the main thread did not exit');
  }
}

// Starts text, THREAD_COMMAND's command by default, as a process that the test
// owns, and returns it with its start time and executable once its main thread
// has exited and another thread runs. With exeDir, it runs a copy of the
// interpreter there, so that the process's executable is under that directory,
// as the Remote-SSH leak check finds the run's processes. env is the process's
// environment.
async function startThread({ exeDir, env = process.env, text = THREAD_COMMAND } = {}) {
  const command = exeDir === undefined ? 'python3' : copyInterpreter(exeDir);
  const child = spawn(command, ['-I', '-c', text], { env, stdio: 'ignore' });
  await new Promise((resolve) => child.on('spawn', resolve));
  const exited = new Promise((resolve) => child.on('exit', (code, signal) => resolve(signal ?? code)));
  try {
    return { child, exited, start: await waitThread(child.pid), exe: command };
  } catch (error) {
    child.kill('SIGKILL');
    throw error;
  }
}

// Starts PARENT_COMMAND's process under a copy of the interpreter in exeDir,
// which makes it the run's, and returns it with the hidden process that it
// started, once that process's main thread has exited while another thread
// runs. The hidden process runs python3 from PATH with no environment, so
// neither its command line nor its environment names the run: only its parent
// does.
async function startHiddenChild({ exeDir, env = process.env, text = HIDDEN_THREAD_COMMAND } = {}) {
  const command = copyInterpreter(exeDir);
  const parent = spawn(command, ['-I', '-c', PARENT_COMMAND, text], { env, stdio: ['ignore', 'pipe', 'ignore'] });
  await new Promise((resolve) => parent.on('spawn', resolve));
  const parentStart = processes.processInfo(parent.pid)?.start;
  if (parentStart === undefined) {
    parent.kill('SIGKILL');
    throw new Error('the parent did not start');
  }
  const parentExited = new Promise((resolve) => parent.on('exit', (code, signal) => resolve(signal ?? code)));
  let pid;
  try {
    pid = await new Promise((resolve, reject) => {
      let output = '';
      parent.stdout.on('data', (data) => {
        output += data;
        const line = /^(\d+)\n/.exec(output);
        if (line) resolve(Number(line[1]));
      });
      parent.on('error', reject);
      parent.on('exit', () => reject(new Error('the parent exited before it started its child')));
    });
    return { parent: { pid: parent.pid, start: parentStart }, parentExited, pid, start: await waitThread(pid) };
  } catch (error) {
    // The child is the parent's, so stop it only while it still is.
    if (pid !== undefined && processes.processInfo(pid)?.ppid === parent.pid) {
      try {
        process.kill(pid, 'SIGKILL');
      } catch {
        // It has gone.
      }
    }
    parent.kill('SIGKILL');
    throw error;
  }
}

// Stops the process with this PID and start time, if it still runs, with
// SIGKILL, and waits up to five seconds for it to go. Its parent must have
// exited first, so that it isn't left a zombie.
async function stop({ pid, start }) {
  if (processes.processInfo(pid)?.start === start) {
    try {
      process.kill(pid, 'SIGKILL');
    } catch {
      // It has gone.
    }
  }
  for (const deadline = Date.now() + 5000; ; await new Promise((resolve) => setTimeout(resolve, 20))) {
    if (processes.processInfo(pid) === undefined) return;
    if (Date.now() > deadline) throw new Error(`the process with pid ${pid} did not exit`);
  }
}

module.exports = {
  HIDDEN_THREAD_COMMAND, THREAD_COMMAND, THREAD_SKIP, runs, startHiddenChild, startThread, stop,
};
