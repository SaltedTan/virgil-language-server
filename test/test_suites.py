#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check test/suites.py with shell commands standing in for the suites.

Each test runs the runner on commands that leave their traces in a temporary
directory, and reads its stdout and stderr as one stream, as a CI log does.
"""
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SUITES = Path(__file__).resolve().parent / "suites.py"


def meeting(name, other, directory):
    """A suite that waits up to ten seconds for the other one to start, then
    prints three lines, one of them on stderr, with pauses between them. It
    fails if it ran alone."""
    return (f"touch {directory}/{name}; i=0; "
            f"while [ ! -e {directory}/{other} ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done; "
            f"[ -e {directory}/{other} ] || exit 1; "
            f"echo {name}1; sleep 0.2; echo {name}2 >&2; sleep 0.2; echo {name}3")


class SuitesTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="suites-")
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, *suites, jobs=1):
        return [sys.executable, str(SUITES), f"--jobs={jobs}", *suites]

    def run_suites(self, *suites, jobs=1, input=None):
        return subprocess.run(self.command(*suites, jobs=jobs), input=input, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=60, check=False)

    def test_suites_run_at_once_and_print_whole(self):
        alpha, beta = meeting("alpha", "beta", self.directory), meeting("beta", "alpha", self.directory)
        result = self.run_suites(alpha, beta, jobs=2)
        self.assertEqual(0, result.returncode, result.stdout)
        lines = result.stdout.splitlines()
        for name, suite in [("alpha", alpha), ("beta", beta)]:
            header = next(i for i, line in enumerate(lines) if line.startswith(f"== {suite}: passed in "))
            self.assertEqual([f"{name}1", f"{name}2", f"{name}3"], lines[header + 1:header + 4], result.stdout)
        self.assertIn("suites: 2 passed, 0 failed, 2 at once", result.stdout)

    def test_one_at_a_time_streams_in_order(self):
        first = f"echo first; touch {self.directory}/first"
        second = f"[ -e {self.directory}/first ] && echo second"
        result = self.run_suites(first, second)
        self.assertEqual(0, result.returncode, result.stdout)
        lines = result.stdout.splitlines()
        self.assertEqual(["first", "second"], [lines[0], lines[2]], result.stdout)
        self.assertTrue(lines[1].startswith(f"== {first}: passed in "), result.stdout)
        self.assertTrue(lines[3].startswith(f"== {second}: passed in "), result.stdout)
        self.assertIn("suites: 2 passed, 0 failed, 1 at once", result.stdout)

    def test_a_failing_suite_fails_the_run_after_the_rest(self):
        result = self.run_suites("exit 3", "echo still runs", jobs=2)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("== exit 3: FAILED (exit status 3) in ", result.stdout)
        self.assertIn("still runs", result.stdout)
        self.assertIn("suites: 1 passed, 1 failed, 2 at once", result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith("FAIL: exit 3"), result.stdout)

    def test_times_are_listed_slowest_first(self):
        result = self.run_suites("true", "sleep 0.5")
        self.assertEqual(0, result.returncode, result.stdout)
        times = result.stdout.split("suite times:\n")[1].splitlines()
        self.assertEqual(["sleep 0.5", "true"], [line.split(" s  ", 1)[1] for line in times[:2]])

    def test_suites_get_no_input(self):
        result = self.run_suites("cat", input="from the terminal\n")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertNotIn("from the terminal", result.stdout)

    def test_termination_kills_every_suite_process_and_starts_no_more(self):
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=signum):
                pids = self.directory / f"pids-{signum}"
                pids.mkdir()
                # Each suite leaves a process of its own behind it, as a server
                # or a held-open input can.
                suite = (f"sleep 60 & echo $! > {pids}/$$.child; echo $$ > {pids}/$$.shell; "
                         f"touch {pids}/$$.ready; wait")
                late = f"touch {pids}/late"
                runner = subprocess.Popen(self.command(suite, suite, late, jobs=2), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 10
                while len(list(pids.glob("*.ready"))) < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(2, len(list(pids.glob("*.ready"))), "both suites started")
                runner.send_signal(signum)
                self.assertEqual(128 + signum, runner.wait(timeout=20))
                self.assertFalse((pids / "late").exists(), "no suite starts after the signal")
                processes = [int(path.read_text()) for path in pids.glob("*.child")]
                processes += [int(path.read_text()) for path in pids.glob("*.shell")]
                self.assertEqual(4, len(processes))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and any(alive(pid) for pid in processes):
                    time.sleep(0.05)
                self.assertEqual([], [pid for pid in processes if alive(pid)])

    def test_signals_allow_suites_to_clean_up_their_own_sessions(self):
        script = self.directory / "suite.py"
        script.write_text('''import signal
import subprocess
import sys
from pathlib import Path

directory = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         start_new_session=True)

def stop(signum, frame):
    child.terminate()
    child.wait()
    (directory / "stopped").write_text(str(signum))
    sys.exit(0)

for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(signum, stop)
(directory / "ready.tmp").write_text(str(child.pid))
(directory / "ready.tmp").replace(directory / "ready")
signal.pause()
''')
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=signum):
                directory = self.directory / str(signum)
                directory.mkdir()
                suite = "exec " + shlex.join([sys.executable, str(script), str(directory)])
                runner = subprocess.Popen(self.command(suite), stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + 10
                    while not (directory / "ready").exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue((directory / "ready").exists(), "suite started")
                    child = int((directory / "ready").read_text())
                    runner.send_signal(signum)
                    self.assertEqual(128 + signum, runner.wait(timeout=5))
                    self.assertEqual(str(signum), (directory / "stopped").read_text())
                    self.assertFalse(alive(child), "suite reaped its separate session")
                finally:
                    if runner.poll() is None:
                        runner.send_signal(signal.SIGTERM)
                        runner.wait(timeout=15)
                    if (directory / "ready").exists():
                        child = int((directory / "ready").read_text())
                        if alive(child):
                            os.kill(child, signal.SIGKILL)

    def test_second_signal_kills_an_ignoring_suite(self):
        ready = self.directory / "ready"
        suite = f"trap '' TERM HUP INT; echo $$ > {ready}.tmp; mv {ready}.tmp {ready}; exec sleep 60"
        runner = subprocess.Popen(self.command(suite), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "suite started")
            runner.send_signal(signal.SIGTERM)
            time.sleep(0.2)
            self.assertIsNone(runner.poll(), "first signal allows cleanup")
            runner.send_signal(signal.SIGHUP)
            self.assertEqual(128 + signal.SIGTERM, runner.wait(timeout=5))
            self.assertFalse(alive(int(ready.read_text())), "suite reaped after second signal")
        finally:
            if runner.poll() is None:
                runner.send_signal(signal.SIGTERM)
                runner.wait(timeout=15)


def alive(pid):
    """Whether {pid} still runs. A killed process that its new parent has not
    reaped yet still accepts signals, so it counts until it is reaped."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()
