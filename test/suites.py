#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Run test suites, several at once, each printed whole once it finishes.

Usage: test/suites.py [--jobs=N] COMMAND ...

Each COMMAND is a shell command that runs one suite. They start in the order
given, so the longest should come first, and up to N run at once (default:
one for each CPU this process may use, at most DEFAULT_JOBS). With more than
one at once, a suite's output, stdout and stderr together, goes to a
temporary file that is printed whole, after a line with the suite's result
and time, once it finishes; with one at a time it streams as it is written.
Every suite runs, even after one fails. Each runs in a session of its own,
with no input. SIGTERM, SIGHUP, and SIGINT are forwarded to each running
suite's process group and stop new suites from starting. A second signal or
ten seconds after the first signal kills the remaining groups. Suites own
cleanup of processes they move into separate sessions. Afterwards it lists
each suite's time, slowest first.
"""
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

# Suites check timing (exits within seconds, workers stopped at their limits),
# so they don't run more at once than CI runners have CPUs.
DEFAULT_JOBS = 4


def send(process, signum):
    """Signals an owned process group, returning whether it still exists."""
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS can report EPERM for an exited process group.
        if process.poll() is None:
            raise
        return False
    return True


class Suite:
    def __init__(self, command):
        self.command = command
        self.seconds = 0.0
        self.status = None


class Suites:
    """Hands the suites to the threads in turn and owns their processes. A
    signal is recorded and forwarded to every running suite's process group,
    rather than raising during a launch. Launch and registration share the
    signal handler's lock, so cancellation prevents further launches."""

    def __init__(self, commands, live):
        self.waiting = [Suite(command) for command in commands]
        self.ran = []
        self.live = live  # stream each suite's output, with one at a time
        self.lock = threading.RLock()
        self.signum = 0
        self.deadline = None
        self.force = False
        self.processes = []

    def stop(self, signum, _frame):
        # Only workers hold this lock outside handlers; nested signals can reenter it.
        with self.lock:
            if not self.signum:
                self.signum = signum
                self.deadline = time.monotonic() + 10
            else:
                self.force = True
                self.deadline = None
            for process in self.processes:
                send(process, signal.SIGKILL if self.force else self.signum)

    def thread(self):
        while self.run():
            pass

    def run(self):
        try:
            with self.lock:
                if self.signum or not self.waiting:
                    return False
                suite = self.waiting.pop(0)
                self.ran.append(suite)
                output = None if self.live else tempfile.TemporaryFile()
                started = time.monotonic()
                process = subprocess.Popen(["/bin/sh", "-c", suite.command], stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT if output else None,
                                           start_new_session=True)
                self.processes.append(process)
        except OSError as error:
            suite.status = 127
            self.write(suite, f"cannot run it: {error}\n".encode())
            return True
        suite.status = process.wait()
        suite.seconds = time.monotonic() - started
        with self.lock:
            if not self.signum:
                self.processes.remove(process)
        if output is None:
            self.write(suite, b"")
        else:
            output.seek(0)
            self.write(suite, output.read())
            output.close()
        return True

    def write(self, suite, output):
        result = "passed" if suite.status == 0 else f"FAILED (exit status {suite.status})"
        with self.lock:
            sys.stdout.buffer.write(f"== {suite.command}: {result} in {suite.seconds:.1f} s\n".encode())
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()


def cpus():
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def main(jobs, commands):
    jobs = max(1, min(jobs or min(cpus(), DEFAULT_JOBS), len(commands)))
    suites = Suites(commands, jobs == 1)
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, suites.stop)
    started = time.monotonic()
    threads = []
    try:
        for _ in range(jobs):
            if suites.signum:
                break
            thread = threading.Thread(target=suites.thread)
            thread.start()
            threads.append(thread)
        # A timed join lets the main thread run signal handlers.
        for thread in threads:
            while thread.is_alive():
                thread.join(0.1)
                if suites.deadline is not None and time.monotonic() >= suites.deadline:
                    suites.stop(signal.SIGKILL, None)
        while suites.signum and not suites.force:
            suites.processes[:] = [process for process in suites.processes if send(process, 0)]
            if not suites.processes:
                break
            if suites.deadline is not None and time.monotonic() >= suites.deadline:
                suites.stop(signal.SIGKILL, None)
            else:
                time.sleep(0.1)
    finally:
        for process in list(suites.processes):
            send(process, signal.SIGKILL)
        for thread in threads:
            thread.join()
    if suites.signum:
        return 128 + suites.signum
    elapsed = time.monotonic() - started

    print("\nsuite times:")
    for suite in sorted(suites.ran, key=lambda suite: suite.seconds, reverse=True):
        print(f"{suite.seconds:8.1f} s  {suite.command}")
    failed = [suite for suite in suites.ran if suite.status != 0]
    print(f"suites: {len(suites.ran) - len(failed)} passed, {len(failed)} failed, "
          f"{jobs} at once, {elapsed:.1f} s")
    for suite in failed:
        print(f"FAIL: {suite.command}")
    # A signal during the report is recorded too, and still decides the status.
    return 128 + suites.signum if suites.signum else int(bool(failed))


if __name__ == "__main__":
    arguments = sys.argv[1:]
    jobs = 0
    if arguments and arguments[0].startswith("--jobs="):
        jobs = int(arguments.pop(0)[len("--jobs="):])
    if not arguments:
        sys.exit(__doc__.split("\n\n")[1])
    sys.exit(main(jobs, arguments))
