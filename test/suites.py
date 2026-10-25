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
with no input, and a signal kills every running suite's processes and starts
no more. Afterwards it lists each suite's time, slowest first.
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


def kill(process):
    """Kills {process}'s group, unless it was reaped and its ID may be reused."""
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can report EPERM for an exited process group.
        if process.poll() is None:
            raise


class Suite:
    def __init__(self, command):
        self.command = command
        self.seconds = 0.0
        self.status = None


class Suites:
    """Hands the suites to the threads in turn and owns their processes. A
    signal is recorded and kills every running suite's process group, and any
    started later, rather than raising in the middle of a launch. Signal
    handlers run on the main thread, between the other threads' list
    operations, so the handler kills a copy of the list."""

    def __init__(self, commands, live):
        self.waiting = [Suite(command) for command in commands]
        self.ran = []
        self.live = live  # stream each suite's output, with one at a time
        self.lock = threading.Lock()
        self.signum = 0
        self.processes = []

    def stop(self, signum, _frame):
        if not self.signum:
            self.signum = signum
        for process in list(self.processes):
            kill(process)

    def next(self):
        with self.lock:
            if self.signum or not self.waiting:
                return None
            suite = self.waiting.pop(0)
            self.ran.append(suite)
            return suite

    def thread(self):
        while (suite := self.next()) is not None:
            self.run(suite)

    def run(self, suite):
        output = None if self.live else tempfile.TemporaryFile()
        started = time.monotonic()
        try:
            process = subprocess.Popen(["/bin/sh", "-c", suite.command], stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT if output else None,
                                       start_new_session=True)
        except OSError as error:
            suite.status = 127
            self.write(suite, f"cannot run it: {error}\n".encode())
            return
        self.processes.append(process)
        if self.signum:
            kill(process)
        suite.status = process.wait()
        suite.seconds = time.monotonic() - started
        with self.lock:
            self.processes.remove(process)
        if output is None:
            self.write(suite, b"")
        else:
            output.seek(0)
            self.write(suite, output.read())
            output.close()

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
    finally:
        for process in list(suites.processes):
            kill(process)
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
    return int(bool(failed))


if __name__ == "__main__":
    arguments = sys.argv[1:]
    jobs = 0
    if arguments and arguments[0].startswith("--jobs="):
        jobs = int(arguments.pop(0)[len("--jobs="):])
    if not arguments:
        sys.exit(__doc__.split("\n\n")[1])
    sys.exit(main(jobs, arguments))
