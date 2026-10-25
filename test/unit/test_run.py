#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check test/unit/run.py with a stand-in for the unit-test executable.

Each test copies run.py and the project fixtures, prepare.py among them, into a
temporary tree whose test/unit registers two groups, Alpha (two tests) and
Beta (one), so the runner scans those instead of the real sources. Tests run
one group at a time unless they say otherwise.
"""
import errno
import os
from pathlib import Path
import runpy
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

DIR = Path(__file__).resolve().parent
PROJECTS = DIR.parent / "fixtures" / "projects"
RESTRICTED = ["unreadable-directories/blocked", "ignored-entries/build"]
SOURCES = {
    "AlphaTest.v3": 'def T = UnitTests.register;\ndef x = [\n\tT("Alpha:one", one),\n\tT("Alpha:two", two)\n];\n',
    "BetaTest.v3": 'def y = UnitTests.register("Beta:one", one);\n',
}
# Stands in for build/unit-tests. Asked for every test ("*"), it prints the
# count and then waits, as the real one runs its first test. Asked for a group,
# it logs, in one line, the modes of the restricted directories next to it, the
# group, its process, and its directory, then runs what ALPHA or BETA says.
STAND_IN = """\
#!/bin/sh
if [ "$1" = '*' ]; then
    printf '##>%s unit tests\\n' {total}
    exec sleep 60
fi
group=${{1%:*}}
here=$(cd "$(dirname "$0")" && pwd)
line=
for directory in {restricted}; do
    line="$line$(ls -ld "$here/project-fixtures/$directory" | cut -c1-10) "
done
echo "$line$group $$ $here" >> {log}
case "$group" in
Alpha) {alpha} ;;
Beta) {beta} ;;
esac
"""
ALPHA_PASSES = "printf '##>2 unit tests\\n##+Alpha:one\\n##-ok\\n##+Alpha:two\\n##-ok\\n'"
BETA_PASSES = "printf '##>1 unit tests\\n##+Beta:one\\n##-ok\\n'"
SLEEPS = "printf '##>1 unit tests\\n##+{group}:one\\n'; exec sleep 60"


def meeting(group, other, header, tests):
    """Waits up to ten seconds for the other group to start, so that the
    group's tests pass only if both groups run at once."""
    return (f"touch {{log}}.{group}; i=0; "
            f"while [ ! -e {{log}}.{other} ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i + 1)); done; "
            f"printf '{header}'; for t in {tests}; do printf '##+{group}:%s\\n' $t; "
            f"if [ -e {{log}}.{other} ]; then echo '##-ok'; else echo '##-fail (alone)'; fi; done")


class RunTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="unit-run-")
        root = Path(self.temporary.name)
        unit = root / "test" / "unit"
        unit.mkdir(parents=True)
        shutil.copy(DIR / "run.py", unit / "run.py")
        for name, text in SOURCES.items():
            (unit / name).write_text(text, encoding="utf-8")
        shutil.copytree(PROJECTS, root / "test" / "fixtures" / "projects")
        self.run_py = unit / "run.py"
        self.build = root / "build"
        self.restricted = [self.build / "project-fixtures" / relative for relative in RESTRICTED]
        for directory in self.restricted:
            directory.mkdir(parents=True)
        self.log = root / "log"
        self.log.touch()
        self.executable = self.build / "unit-tests"

    def tearDown(self):
        for directory in self.build.rglob("*"):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o700)
        self.temporary.cleanup()

    def stand_in(self, total=3, alpha=ALPHA_PASSES, beta=BETA_PASSES):
        log = f"'{self.log}'"
        self.executable.write_text(STAND_IN.format(
            total=total,
            restricted=" ".join(RESTRICTED),
            log=log,
            alpha=alpha.replace("{log}", log),
            beta=beta.replace("{log}", log),
        ), encoding="utf-8")
        self.executable.chmod(0o755)

    def command(self, *patterns, jobs=1):
        return [sys.executable, str(self.run_py), f"--jobs={jobs}", str(self.build), str(self.executable), *patterns]

    def run_unit_tests(self, *patterns, jobs=1):
        return subprocess.run(self.command(*patterns, jobs=jobs), capture_output=True, text=True, timeout=60,
                              check=False)

    def logged(self):
        return [line.split() for line in self.log.read_text(encoding="utf-8").splitlines()]

    def assert_restored(self):
        for directory in self.restricted:
            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode), directory)
        self.assertEqual(["project-fixtures", "unit-tests"], sorted(path.name for path in self.build.iterdir()))

    def test_each_group_runs_restricted_in_its_own_process(self):
        self.stand_in()
        result = self.run_unit_tests()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        runs = self.logged()
        self.assertEqual(["Alpha", "Beta"], [run[2] for run in runs])
        self.assertNotEqual(runs[0][3], runs[1][3], "one process for each group")
        for run in runs:
            self.assertEqual(["d---------", "d---------"], run[:2])
            self.assertEqual(str(self.build), run[4])
        self.assertIn("slowest 3 unit tests:", result.stdout)
        self.assertIn("unit tests: 3 passed, 0 failed, in 2 groups, 1 at once", result.stdout)
        self.assert_restored()

    def test_groups_run_at_once_in_slots_of_their_own(self):
        self.stand_in(alpha=meeting("Alpha", "Beta", "##>2 unit tests\\n", "one two"),
                      beta=meeting("Beta", "Alpha", "##>1 unit tests\\n", "one"))
        result = self.run_unit_tests(jobs=2)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        runs = {run[2]: run for run in self.logged()}
        self.assertEqual({"Alpha", "Beta"}, set(runs))
        self.assertEqual({str(self.build), str(self.build / "unit-slots" / "1")}, {run[4] for run in runs.values()})
        for run in runs.values():
            self.assertEqual(["d---------", "d---------"], run[:2])
        self.assertIn("unit tests: 3 passed, 0 failed, in 2 groups, 2 at once", result.stdout)
        # A group's output is printed whole, not interleaved with the other's.
        lines = [line for line in result.stdout.splitlines() if line.startswith("##")]
        alpha = lines.index("##>2 unit tests")
        self.assertEqual(["##+Alpha:one", "##-ok", "##+Alpha:two", "##-ok"], lines[alpha + 1:alpha + 5])
        self.assert_restored()

    def test_slots_an_interrupted_run_left_are_removed(self):
        stale = self.build / "unit-slots" / "1" / "project-fixtures" / RESTRICTED[0]
        stale.mkdir(parents=True)
        stale.chmod(0)
        self.stand_in()
        result = self.run_unit_tests(jobs=2)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_restored()

    def test_parallel_groups_keep_stdout_and_stderr_together(self):
        self.stand_in(
            alpha="echo '##stderr Alpha' >&2; " + meeting("Alpha", "Beta", "##>2 unit tests\\n", "one two"),
            beta="echo '##stderr Beta' >&2; " + meeting("Beta", "Alpha", "##>1 unit tests\\n", "one"),
        )
        result = subprocess.run(self.command(jobs=2), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=60, check=False)
        self.assertEqual(0, result.returncode, result.stdout)
        lines = [line for line in result.stdout.splitlines() if line.startswith("##")]
        alpha = ["##>2 unit tests", "##+Alpha:one", "##-ok", "##+Alpha:two", "##-ok", "##stderr Alpha"]
        beta = ["##>1 unit tests", "##+Beta:one", "##-ok", "##stderr Beta"]
        self.assertIn(lines, [alpha + beta, beta + alpha])
        self.assert_restored()

    def test_stale_fixture_symlink_does_not_restore_outside_slots(self):
        outside = self.build.parent / "outside-fixtures"
        restricted = [outside / relative for relative in RESTRICTED]
        for directory in restricted:
            directory.mkdir(parents=True)
            directory.chmod(0)
        stale = self.build / "unit-slots" / "stale"
        stale.mkdir(parents=True)
        (stale / "project-fixtures").symlink_to(outside, target_is_directory=True)
        self.stand_in()
        try:
            result = self.run_unit_tests(jobs=2)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            for directory in restricted:
                self.assertEqual(0, stat.S_IMODE(directory.stat().st_mode), directory)
            self.assert_restored()
        finally:
            for directory in restricted:
                directory.chmod(0o700)

    def test_preparation_failures_reap_every_launched_child(self):
        runner = runpy.run_path(str(self.run_py))
        popen = subprocess.Popen
        for failure in ("exit", "launch"):
            with self.subTest(failure=failure):
                children = []

                def launch(command, **kwargs):
                    if failure == "launch" and len(children) == 2:
                        raise OSError(errno.EAGAIN, "cannot launch preparer")
                    code = ("raise SystemExit(1)" if failure == "exit" and not children else
                            "from pathlib import Path; import sys; Path(sys.argv[1], 'ready').touch()")
                    child = popen([sys.executable, "-c", code, command[-1]], **kwargs)
                    children.append(child)
                    return child

                try:
                    with patch.object(subprocess, "Popen", side_effect=launch):
                        with self.assertRaisesRegex(SystemExit, "preparing the slots' fixtures failed"):
                            runner["slots"](str(self.build), str(self.executable), 4)
                    self.assertEqual(3 if failure == "exit" else 2, len(children))
                    for index, child in enumerate(children, 1):
                        self.assertIsNotNone(child.returncode)
                        with self.assertRaises(ChildProcessError):
                            os.waitpid(child.pid, os.WNOHANG)
                        if failure != "exit" or index != 1:
                            self.assertTrue((self.build / "unit-slots" / str(index) / "ready").exists())
                finally:
                    for child in children:
                        child.kill()
                        child.wait()
                    shutil.rmtree(self.build / "unit-slots")

    def test_failing_test_fails_the_run(self):
        self.stand_in(beta="printf '##>1 unit tests\\n##+Beta:one\\n##-fail (boom)\\n'; exit 1")
        result = self.run_unit_tests()
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("unit tests: 2 passed, 1 failed", result.stdout)
        self.assertIn("FAIL: Beta:one", result.stdout)
        self.assert_restored()

    def test_relative_build_runs_groups(self):
        self.stand_in()
        result = subprocess.run([sys.executable, str(self.run_py), "--jobs=2", "build", "build/unit-tests"],
                                cwd=self.build.parent, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["Alpha", "Beta"], sorted(run[2] for run in self.logged()))
        self.assert_restored()

    def test_crash_before_the_last_test_fails_the_run(self):
        self.stand_in(total=4, beta="printf '##>2 unit tests\\n##+Beta:one\\n##-ok\\n##+Beta:two\\n'; kill -KILL $$")
        result = self.run_unit_tests()
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("error: Beta: 1 of 2 tests did not finish (exit status -9)", result.stdout)
        self.assert_restored()

    def test_tests_outside_the_scanned_groups_fail_the_run(self):
        self.stand_in(total=4)
        result = self.run_unit_tests()
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("error: the groups hold 3 tests, but", result.stdout)
        self.assertIn("has 4; register each test", result.stdout)
        self.assert_restored()

    def test_patterns_run_once_restricted_with_the_status(self):
        self.stand_in(beta="printf '##>1 unit tests\\n##+Beta:one\\n##-fail (boom)\\n'; exit 3")
        result = self.run_unit_tests("Beta:*")
        self.assertEqual(3, result.returncode, result.stdout + result.stderr)
        runs = self.logged()
        self.assertEqual([["d---------", "d---------", "Beta"]], [run[:3] for run in runs])
        self.assertNotIn("slowest", result.stdout)
        self.assert_restored()

    def test_termination_stops_the_group_and_restores(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                self.check_termination(signum)

    def test_termination_stops_every_slot_and_restores(self):
        self.stand_in(alpha=SLEEPS.replace("{group}", "Alpha"), beta=SLEEPS.replace("{group}", "Beta"))
        process = subprocess.Popen(self.command(jobs=2), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True)
        try:
            while len(self.logged()) < 2:
                self.assertIsNone(process.poll(), "the runner waits for its groups")
                time.sleep(0.05)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(128 + signal.SIGTERM, process.wait(timeout=10))
            for run in self.logged():
                try:
                    os.kill(int(run[3]), 0)
                except ProcessLookupError:
                    continue
                os.kill(int(run[3]), signal.SIGKILL)
                self.fail(f"{run[2]}'s process outlived the runner")
        finally:
            process.kill()
            process.stdout.close()
            process.wait()
        self.assert_restored()

    def check_termination(self, signum):
        self.stand_in(beta="printf '##>1 unit tests\\n##+Beta:one\\n'; exec sleep 60")
        process = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            for line in process.stdout:
                if line.startswith("##+Beta:one"):
                    break
            process.send_signal(signum)
            self.assertEqual(128 + signum, process.wait(timeout=10))
            # The runner has reaped the group's process, not left it running.
            sleeper = int(self.logged()[-1][3])
            try:
                os.kill(sleeper, 0)
            except ProcessLookupError:
                pass
            else:
                os.kill(sleeper, signal.SIGKILL)
                self.fail("the group's process outlived the runner")
        finally:
            process.kill()
            process.stdout.close()
            process.wait()
        self.assert_restored()

    def test_signals_during_cleanup_wait_for_restoration(self):
        prepare = self.run_py.parent.parent / "fixtures" / "projects" / "prepare.py"
        prepare.rename(prepare.with_name("real_prepare.py"))
        prepare.write_text(
            "import runpy, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[2] == '--cleanup':\n"
            "    print('cleanup-ready', flush=True)\n"
            "    sys.stdin.readline()\n"
            "runpy.run_path(str(Path(__file__).with_name('real_prepare.py')), run_name='__main__')\n",
            encoding="utf-8",
        )
        for stopping_group in (False, True):
            for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                with self.subTest(stopping_group=stopping_group, signal=signum):
                    self.log.write_text("", encoding="utf-8")
                    self.stand_in(beta="printf '##>1 unit tests\\n##+Beta:one\\n'; exec sleep 60"
                                  if stopping_group else BETA_PASSES)
                    process = subprocess.Popen(
                        self.command(),
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        start_new_session=True,
                    )
                    try:
                        for line in process.stdout:
                            if stopping_group and line.startswith("##+Beta:one"):
                                process.send_signal(signal.SIGTERM)
                            if line.strip() == "cleanup-ready":
                                break
                        else:
                            self.fail("runner exited before cleanup")
                        for directory in self.restricted:
                            self.assertEqual(0, stat.S_IMODE(directory.stat().st_mode))
                        if stopping_group:
                            with self.assertRaises(ProcessLookupError):
                                os.kill(int(self.logged()[-1][3]), 0)
                        os.killpg(process.pid, signum)
                        with self.assertRaises(subprocess.TimeoutExpired):
                            process.wait(timeout=0.1)
                        output, error = process.communicate(input="\n", timeout=10)
                        first_signal = signal.SIGTERM if stopping_group else signum
                        self.assertEqual(128 + first_signal, process.returncode, output + error)
                        self.assert_restored()
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=10)

    def test_signals_at_launch_and_probe_reaping_leave_no_children(self):
        runner = runpy.run_path(str(self.run_py))
        popen, killpg = subprocess.Popen, os.killpg
        handlers = {signum: signal.getsignal(signum)
                    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
        try:
            for boundary in ("group-launch", "probe-launch", "probe-reap", "probe-exited", "restrict-launch"):
                for signum in handlers:
                    with self.subTest(boundary=boundary, signal=signum):
                        self.stand_in()
                        children = []

                        def launch(command, **kwargs):
                            child = popen(command, **kwargs)
                            target = {"group-launch": "Alpha:*", "restrict-launch": "--restrict"}.get(boundary, "*")
                            if command[-1] == target:
                                children.append(child)
                                if boundary != "probe-reap":
                                    os.kill(os.getpid(), signum)
                            return child

                        def kill_group(pid, sig):
                            if boundary == "probe-exited":
                                children[0].wait(timeout=10)
                                # macOS can report EPERM for an exited process group.
                                raise PermissionError(errno.EPERM, os.strerror(errno.EPERM))
                            killpg(pid, sig)
                            if boundary == "probe-reap":
                                os.kill(os.getpid(), signum)

                        try:
                            with patch.object(subprocess, "Popen", side_effect=launch), \
                                    patch.object(os, "killpg", side_effect=kill_group):
                                status = runner["main"](1, str(self.build), str(self.executable), [])
                            self.assertEqual(128 + signum, status)
                            self.assertEqual(1, len(children))
                            with self.assertRaises(ChildProcessError):
                                os.waitpid(children[0].pid, os.WNOHANG)
                            self.assert_restored()
                        finally:
                            for child in children:
                                child.kill()
                                child.wait()
                                if child.stdout is not None:
                                    child.stdout.close()
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    unittest.main()
