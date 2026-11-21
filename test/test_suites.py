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
# Prints the name of each test class that unittest loads tests from in the module at argv[1].
LIST_TEST_CLASSES = """
import importlib.util, sys, unittest
spec = importlib.util.spec_from_file_location("listed", sys.argv.pop(1))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def tests(suite):
    for test in suite:
        yield from tests(test) if isinstance(test, unittest.TestSuite) else [test]
print("\\n".join(sorted({type(test).__name__ for test in tests(unittest.TestLoader().loadTestsFromModule(module))})))
"""


def meeting(name, other, directory):
    """A suite that waits up to ten seconds for the other one to start, then
    prints three lines, one of them on stderr, with pauses between them. It
    fails if it ran alone."""
    return (f"touch {directory}/{name}; i=0; "
            f"while [ ! -e {directory}/{other} ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done; "
            f"[ -e {directory}/{other} ] || exit 1; "
            f"echo {name}1; sleep 0.2; echo {name}2 >&2; sleep 0.2; echo {name}3")


def waiting_suite(directory, term_trap=":"):
    return (f"trap '' HUP; trap '{term_trap}' TERM; "
            f"echo $$ > {directory}/$$.tmp; mv {directory}/$$.tmp {directory}/ready-$$; "
            'i=0; while [ "$i" -lt 300 ]; do sleep 0.1; i=$((i + 1)); done')


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
        suite has written its pid to {directory}/ready-*, and checks the exit
        status and the cleanup of the suites and the pid in {directory}/child."""
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
        if mode == "denied-kill":
            self.assertEqual(1, runner.returncode, output)
            self.assertTrue(output.rstrip().endswith("PermissionError: [Errno 1] first SIGKILL denied"), output)
        else:
            self.assertIn("nested: EPERM", output)
            self.assertEqual(128 + signal.SIGTERM, runner.returncode, output)
            self.assertNotIn("Traceback", output)
        pids = [int(path.read_text()) for path in directory.glob("ready-*")]
        pids += [int(path.read_text()) for path in directory.glob("child")]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(alive(pid) for pid in pids):
            time.sleep(0.05)
        self.assertEqual([], [pid for pid in pids if alive(pid)], "suites reaped")
        return output

    def test_second_signal_during_the_first_handler(self):
        for mode in ("lock", "handler"):
            with self.subTest(mode=mode):
                directory = self.directory / mode
                directory.mkdir()
                suite = waiting_suite(directory)
                self.run_nested(directory, mode, suite, suite)

    def test_eperm_on_first_sigkill_to_an_exiting_suite(self):
        suite = waiting_suite(self.directory, term_trap="exit 0")
        output = self.run_nested(self.directory, "first-kill-eperm", suite)
        self.assertIn(f"== {suite}: passed in ", output)

    def test_eperm_on_first_sigkill_to_a_live_suite(self):
        suite = waiting_suite(self.directory)
        output = self.run_nested(self.directory, "denied-kill", suite)
        self.assertIn(f"== {suite}: FAILED (exit status {-signal.SIGKILL})", output)

    def test_second_signal_during_a_grace_period_sigkill(self):
        suite = waiting_suite(self.directory)
        self.run_nested(self.directory, "grace-kill", suite, suite)

    def test_second_signal_during_a_deadline_check(self):
        for mode in ("deadline-join", "deadline-drain"):
            with self.subTest(mode=mode):
                directory = self.directory / mode
                directory.mkdir()
                if mode == "deadline-join":
                    # The suite survives TERM, so its thread still waits.
                    suite = waiting_suite(directory)
                else:
                    # The suite exits on TERM and leaves a process in its group
                    # that ignores TERM and HUP, so the runner drains the group.
                    suite = (f"(trap '' TERM HUP; touch {directory}/child-ready; "
                             "exec sleep 30 </dev/null >/dev/null 2>&1) & "
                             f"echo $! > {directory}/child.tmp; mv {directory}/child.tmp {directory}/child; "
                             "trap 'exit 0' TERM; "
                             f"while [ ! -e {directory}/child-ready ]; do sleep 0.05; done; "
                             f"echo $$ > {directory}/$$.tmp; mv {directory}/$$.tmp {directory}/ready-$$; wait")
                self.run_nested(directory, mode, suite)


# Runs test/suites.py with a second signal, SIGHUP, delivered at a chosen point
# while the runner is stopping suites. The harness simulates macOS EPERM for
# groups with an unreaped leader.
NESTED = '''import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time

spec = importlib.util.spec_from_file_location("suites", sys.argv[1])
suites = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suites)
mode = sys.argv[2]
unreaped = set()
escalated = set()
nested = False
draining = False
denied = False
in_flight = None


def second_signal():
    """Sends SIGHUP to this process and runs its handler before returning."""
    global nested
    nested = True
    print("nested: second signal", file=sys.stderr, flush=True)
    # In grace-kill mode the first group has already received SIGKILL.
    signal.raise_signal(signal.SIGHUP)
    assert len(escalated) == len(sys.argv) - 3, "second signal missed a group"


class InterruptingLock:
    def __init__(self, lock):
        self.lock = lock

    def __enter__(self):
        if threading.current_thread() is threading.main_thread() and not nested:
            second_signal()
        return self.lock.__enter__()

    def __exit__(self, *exc):
        return self.lock.__exit__(*exc)


class SuitesWithInterruptingLock(suites.Suites):
    def __init__(self, commands, live):
        super().__init__(commands, live)
        self.lock = InterruptingLock(self.lock)


real_killpg = os.killpg


def killpg(pgid, signum):
    global denied, draining, in_flight
    assert pgid != in_flight, "group signalled before the previous SIGKILL returned"
    if pgid in unreaped:
        if signum == signal.SIGKILL:
            escalated.add(pgid)
        print("nested: EPERM", file=sys.stderr, flush=True)
        raise PermissionError(1, "Operation not permitted")
    if signum == 0:
        draining = True
    if signum == signal.SIGKILL and mode == "denied-kill" and not denied:
        denied = True
        raise PermissionError(1, "first SIGKILL denied")
    real_killpg(pgid, signum)
    if signum == signal.SIGKILL:
        escalated.add(pgid)
        unreaped.add(pgid)
        if mode == "grace-kill" and not nested:
            in_flight = pgid
            try:
                second_signal()
            finally:
                in_flight = None
    elif signum == signal.SIGTERM and mode in ("handler", "first-kill-eperm", "denied-kill") and not nested:
        if mode == "first-kill-eperm":
            # Let the suite exit on TERM before simulating a zombie group.
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.kill(pgid, 0)
                except ProcessLookupError:
                    break
                assert time.monotonic() < deadline, "suite did not exit on TERM"
                time.sleep(0.01)
            unreaped.add(pgid)
        second_signal()


real_poll = subprocess.Popen.poll


def poll(process):
    return None if process.pid in unreaped else real_poll(process)


real_monotonic = time.monotonic


def monotonic():
    caller = sys._getframe(1)
    if not nested and caller.f_code.co_name == "main":
        runner = caller.f_locals["suites"]
        if runner.deadline is not None:
            if mode == "grace-kill":
                return runner.deadline
            if mode.startswith("deadline-") and draining == (mode == "deadline-drain"):
                second_signal()
    return real_monotonic()


if mode == "lock":
    suites.Suites = SuitesWithInterruptingLock
os.killpg = killpg
subprocess.Popen.poll = poll
time.monotonic = monotonic
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


class ShardsTest(unittest.TestCase):
    """--shard, --times, --list, and --times-from, and the Makefile's list."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shards-")
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def suites(self, *arguments):
        return subprocess.run([sys.executable, str(SUITES), *arguments], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=60, check=False)

    def listed(self, shard, commands, *options):
        result = self.suites(f"--shard={shard}", *options, "--list", *commands)
        self.assertEqual(0, result.returncode, result.stdout)
        prefix = f"shard {shard}: "
        self.assertTrue(all(line.startswith(prefix) for line in result.stdout.splitlines()), result.stdout)
        return [line[len(prefix):] for line in result.stdout.splitlines()]

    def assert_partition(self, commands, count, *options):
        shards = [self.listed(f"{index}/{count}", commands, *options) for index in range(1, count + 1)]
        # Every command in exactly one shard, each shard in the commands' order.
        self.assertEqual(sorted(commands), sorted(sum(shards, [])), shards)
        for shard in shards:
            self.assertEqual([command for command in commands if command in shard], shard)
        return shards

    def test_shards_balance_recorded_times(self):
        times = self.directory / "times.txt"
        times.write_text("# a comment\n    40.0 s  echo a\n    30.0 s  echo b\n    20.0 s  echo c\n"
                         "    10.0 s  echo d\n    10.0 s  echo e\n\n")
        commands = ["echo e", "echo d", "echo c", "echo b", "echo a", "echo new"]
        # "echo new" has no time, so it counts as the median, 20 s: 40 + 10 | 30 + 10 | 20 + 20.
        self.assertEqual([["echo d", "echo a"], ["echo e", "echo b"], ["echo c", "echo new"]],
                         self.assert_partition(commands, 3, f"--times={times}"))

    def test_shards_without_times_take_turns(self):
        commands = [f"echo {index}" for index in range(7)]
        self.assertEqual([["echo 0", "echo 3", "echo 6"], ["echo 1", "echo 4"], ["echo 2", "echo 5"]],
                         self.assert_partition(commands, 3))
        self.assertEqual(["echo 0"], self.listed("1/1", ["echo 0"]))
        self.assertEqual([], self.listed("3/3", ["echo 0", "echo 1"]))

    def test_a_sharded_run_prints_and_runs_only_its_shard(self):
        commands = [f"touch {self.directory}/{name}" for name in ("a", "b", "c")]
        result = self.suites("--jobs=2", "--shard=2/2", *commands)
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(["b"], sorted(path.name for path in self.directory.iterdir()))
        lines = result.stdout.splitlines()
        self.assertEqual(["shard 2/2 runs 1 of 3 suites, 1.0 s by recorded times "
                          "(3 of the 3 have no recorded time and count as 1.0 s each):",
                          f"shard 2/2: {commands[1]}"], lines[:2])
        self.assertIn("suites: 1 passed, 0 failed, 1 at once", result.stdout)

    def test_bad_shards_and_times_are_refused(self):
        bad = self.directory / "bad.txt"
        bad.write_text("    1.0 s  true\nnot a time\n")
        for arguments, message in [(["--shard=0/2"], "expected I/N"), (["--shard=3/2"], "expected I/N"),
                                   (["--shard=1"], "expected I/N"), (["--shard=1/2", f"--times={bad}"],
                                                                     f"{bad}:2: not a suite time"),
                                   (["--list"], "Usage:"), (["--bogus"], "unknown option --bogus")]:
            with self.subTest(arguments=arguments):
                result = self.suites(*arguments, "true")
                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn(message, result.stdout)

    def test_times_from_logs_are_means(self):
        first, second = self.directory / "first.log", self.directory / "second.log"
        first.write_text("    9.0 s  not after the heading\nsuite times:\n    3.0 s  slow\n    1.0 s  fast\n"
                         "suites: 2 passed, 0 failed, 2 at once, 3.0 s\n    7.0 s  not in the list\n")
        # A GitHub Actions job log puts a time before each line.
        second.write_text("2026-11-21T06:32:10.1234567Z suite times:\n"
                          "2026-11-21T06:32:10.1234567Z     5.0 s  slow\n")
        result = self.suites("--times-from", str(first), str(second))
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(["     4.0 s  slow", "     1.0 s  fast"], result.stdout.splitlines()[1:])
        times = self.directory / "times.txt"
        times.write_text(result.stdout)
        self.assertEqual(["slow"], self.listed("1/2", ["fast", "slow"], f"--times={times}"))

    def test_make_suites_shard_into_a_partition_and_cover_the_split_suites(self):
        repository = SUITES.parent.parent
        listing = subprocess.run(["make", "-s", "--no-print-directory", "print-suites"], cwd=repository,
                                 stdout=subprocess.PIPE, text=True, timeout=60, check=True)
        commands = listing.stdout.splitlines()
        self.assertEqual(len(commands), len(set(commands)), "each suite once")
        for count in (2, 3, 4):
            with self.subTest(shards=count):
                self.assert_partition(commands, count, f"--times={repository / 'test' / 'suite-times.txt'}")
        # The parts of the two split suites, with no part left out or run twice: the sections run.sh
        # accepts, and the classes unittest loads tests from.
        script = repository / "test" / "cli" / "run.sh"
        sections = subprocess.run([str(script), "--sections"], stdout=subprocess.PIPE, text=True, timeout=60,
                                  check=True).stdout.split()
        nvim = repository / "test" / "e2e" / "test_nvim_results.py"
        classes = subprocess.run([sys.executable, "-c", LIST_TEST_CLASSES, str(nvim)], cwd=repository,
                                 stdout=subprocess.PIPE, text=True, timeout=60, check=True).stdout.split()
        self.assertTrue(sections and classes, (sections, classes))
        for prefix, parts in [("test/cli/run.sh ", sections), ("python3 test/e2e/test_nvim_results.py ", classes)]:
            with self.subTest(suite=prefix.strip()):
                named = [command.split()[len(prefix.split()) + 1:]
                         for command in commands if command.startswith(prefix)]
                self.assertTrue(all(named), f"every {prefix.strip()} entry names its parts: {named}")
                self.assertEqual(sorted(parts), sorted(sum(named, [])))

if __name__ == "__main__":
    unittest.main()
