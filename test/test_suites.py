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
        ready, cleaned = self.directory / "ready", self.directory / "cleaned"
        # The suite cleans up on TERM and ignores HUP and INT.
        suite = (f"trap 'touch {cleaned}' TERM; trap '' HUP INT; echo $$ > {ready}.tmp; mv {ready}.tmp {ready}; "
                 'i=0; while [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done')
        runner = subprocess.Popen(self.command(suite), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "suite started")
            deadline = time.monotonic() + 10
            runner.send_signal(signal.SIGTERM)
            # Send the second signal only once the runner has forwarded the
            # first: sent sooner, it can arrive before or during the runner's
            # handler for the first, which changes the exit status.
            while not cleaned.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(cleaned.exists(), "first signal forwarded")
            self.assertIsNone(runner.poll(), "first signal allows cleanup")
            runner.send_signal(signal.SIGHUP)
            self.assertEqual(128 + signal.SIGTERM, runner.wait(timeout=5))
            self.assertFalse(alive(int(ready.read_text())), "suite reaped after second signal")
            self.assertLess(time.monotonic(), deadline, "second signal ended the run before the grace timeout")
        finally:
            if runner.poll() is None:
                runner.send_signal(signal.SIGTERM)
                runner.wait(timeout=15)

    def run_nested(self, directory, mode, *suites):
        """Runs the runner through NESTED in {mode}, sends it SIGTERM once every
        suite has written its pid to {directory}/ready-*, and checks that the
        second signal and an EPERM came, that it exits 128 + SIGTERM without a
        traceback, and that the suites and the pid in {directory}/child are
        reaped."""
        script = self.directory / "nested.py"
        script.write_text(NESTED)
        runner = subprocess.Popen([sys.executable, str(script), str(SUITES), mode, *suites],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 10
            while len(list(directory.glob("ready-*"))) < len(suites) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(suites), len(list(directory.glob("ready-*"))), "suites started")
            runner.send_signal(signal.SIGTERM)
            output = runner.communicate(timeout=20)[0]
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.communicate()
        self.assertIn("nested: second signal", output)
        self.assertIn("nested: EPERM", output)
        self.assertEqual(128 + signal.SIGTERM, runner.returncode, output)
        self.assertNotIn("Traceback", output)
        pids = [int(path.read_text()) for path in directory.glob("ready-*")]
        pids += [int(path.read_text()) for path in directory.glob("child")]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(alive(pid) for pid in pids):
            time.sleep(0.05)
        self.assertEqual([], [pid for pid in pids if alive(pid)], "suites reaped")

    def test_second_signal_during_the_first_handler(self):
        # Each suite survives TERM and ignores HUP, so only SIGKILL ends it.
        suite = (f"trap '' HUP; trap : TERM; echo $$ > {self.directory}/ready-$$; "
                 'i=0; while [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done')
        self.run_nested(self.directory, "handler", suite, suite)

    def test_second_signal_during_a_deadline_check(self):
        for mode in ("deadline-join", "deadline-drain"):
            with self.subTest(mode=mode):
                directory = self.directory / mode
                directory.mkdir()
                if mode == "deadline-join":
                    # The suite survives TERM, so its thread still waits.
                    suite = (f"trap '' HUP; trap : TERM; echo $$ > {directory}/ready-$$; "
                             'i=0; while [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done')
                else:
                    # The suite exits on TERM and leaves a process in its group
                    # that ignores TERM and HUP, so the runner drains the group.
                    suite = (f"(trap '' TERM HUP; exec sleep 30 </dev/null >/dev/null 2>&1) & "
                             f"echo $! > {directory}/child; trap 'exit 0' TERM; echo $$ > {directory}/ready-$$; wait")
                self.run_nested(directory, mode, suite)


# Runs test/suites.py with a second signal, SIGHUP, delivered at a chosen point
# while the runner handles the first. In "handler" mode it arrives while the
# runner forwards SIGTERM, and every later signal to a group sent SIGKILL gets
# EPERM, as macOS answers once the group's leader is an unreaped zombie. A long
# switch interval keeps each worker inside process.wait() meanwhile, where
# poll() can't see that its suite exited. In "deadline-join" and
# "deadline-drain" modes it arrives between the main thread's check that a
# deadline is set and its comparison, in the loop that joins the threads or
# the one that drains the process groups.
NESTED = '''import importlib.util
import os
import signal
import sys
import time

spec = importlib.util.spec_from_file_location("suites", sys.argv[1])
suites = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suites)
mode = sys.argv[2]
killed = set()
nested = []
draining = []  # the runner checks the groups that outlive their suites


def second_signal(done):
    """Sends SIGHUP to this process and runs its handler before returning."""
    nested.append(True)
    print("nested: second signal", file=sys.stderr, flush=True)
    os.kill(os.getpid(), signal.SIGHUP)
    deadline = real_monotonic() + 5
    while not done() and real_monotonic() < deadline:
        pass


real_killpg = os.killpg


def killpg(pgid, signum):
    if pgid in killed:
        print("nested: EPERM", file=sys.stderr, flush=True)
        raise PermissionError(1, "Operation not permitted")
    if signum == 0:
        draining.append(True)
    real_killpg(pgid, signum)
    if signum == signal.SIGKILL:
        killed.add(pgid)
    elif signum == signal.SIGTERM and mode == "handler" and not nested:
        second_signal(lambda: killed)


real_monotonic = time.monotonic


def monotonic():
    caller = sys._getframe(1)
    if mode != "handler" and not nested and caller.f_code.co_name == "main":
        runner = caller.f_locals["suites"]
        if runner.deadline is not None and bool(draining) == (mode == "deadline-drain"):
            second_signal(lambda: runner.force)
    return real_monotonic()


os.killpg = killpg
time.monotonic = monotonic
sys.setswitchinterval(1000)
sys.exit(suites.main(len(sys.argv) - 3, sys.argv[3:]))
'''


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
