#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check that the fixture permissions never keep the build directory from
being removed, after passing and failing test runs."""
from contextlib import contextmanager
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PREPARE = Path(__file__).resolve().with_name("prepare.py")
ROOT = Path(__file__).resolve().parents[3]
RESTRICTED = [
    "unreadable-directories/blocked",
    "ignored-entries/build",
]
# Stands in for build/unit-tests in the test recipe. It checks that both
# directories are unreadable while it runs, then exits with the status in its
# last argument.
CONSUMER = """\
import os
import stat
import sys

status = int(sys.argv[-1])
for path in sys.argv[1:-1]:
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode != 0:
        sys.exit(f"{path} has mode {oct(mode)}")
    # Root ignores the mode, as it does for the unit tests that use these
    # directories. The mode check above still proves the restriction.
    if os.geteuid() != 0 and os.access(path, os.R_OK | os.X_OK):
        sys.exit(f"{path} is accessible")
sys.exit(status)
"""
# The command that make's run-unit-tests recipe wraps in the test below. It
# records the modes it sees so the assertion does not depend on output parsing.
STAND_IN = """\
import json
import os
import stat
import sys

paths = {paths!r}
modes = {{}}
for path in paths:
    modes[path] = stat.S_IMODE(os.stat(path).st_mode)
with open({report!r}, "w", encoding="utf-8") as output:
    json.dump(modes, output)
bad = {{path: mode for path, mode in modes.items() if mode != 0}}
if bad:
    sys.exit(f"accessible while running: {{bad}}")
sys.exit({status})
"""
# Runs the stand-in through a shell, not a shebang naming the interpreter, so an
# interpreter path with spaces or past the kernel's shebang limit still works.
TRAMPOLINE = """\
#!/bin/sh
exec {python} {script} "$@"
"""


class FixtureCleanupTest(unittest.TestCase):
    def restricted(self, build):
        return [build / "project-fixtures" / relative for relative in RESTRICTED]

    def restore(self, build):
        for directory in self.restricted(build):
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o700)

    def prepare(self, build):
        subprocess.run([sys.executable, str(PREPARE), str(build)], check=True)

    def run_consumer(self, build, status):
        paths = [str(path) for path in self.restricted(build)]
        return subprocess.run(
            [
                sys.executable, str(PREPARE), str(build), "--run",
                sys.executable, "-c", CONSUMER, *paths, str(status),
            ],
            check=False,
        )

    def stand_in(self, directory, build, status):
        report = directory / f"stand-in-{status}.json"
        source = directory / f"stand-in-{status}.py"
        source.write_text(
            STAND_IN.format(
                paths=[str(path) for path in self.restricted(build)],
                report=str(report),
                status=status,
            ),
            encoding="utf-8",
        )
        script = directory / f"stand-in-{status}"
        script.write_text(
            TRAMPOLINE.format(
                python=shlex.quote(sys.executable),
                script=shlex.quote(str(source)),
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script, report

    def run_unit_tests(self, build, consumer):
        return subprocess.run(
            [
                "make", "--no-print-directory", "run-unit-tests",
                f"BUILD={build}", f"UNIT_TESTS={consumer}",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    @contextmanager
    def build_directory(self, prefix):
        with tempfile.TemporaryDirectory(prefix=prefix, dir=Path.cwd()) as parent:
            build = Path(parent) / "output"
            try:
                yield build
            finally:
                self.restore(build)

    def assert_build_is_removable(self, build):
        directories = [build]
        for parent, names, _ in os.walk(build):
            directories.extend(Path(parent) / name for name in names)
        for directory in directories:
            mode = stat.S_IMODE(directory.stat().st_mode)
            self.assertEqual(0o700, mode & 0o700, f"{directory} lacks owner rwx")
        shutil.rmtree(build)

    def test_make_clean_removes_restricted_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="project-clean-", dir=Path.cwd()) as parent:
            for build in [
                (Path(parent) / "nested/output").relative_to(Path.cwd()),
                Path(parent) / ("long-build-" + "x" * 120) / "output",
            ]:
                with self.subTest(build=build):
                    restricted = self.restricted(build)
                    try:
                        self.prepare(build)
                        socket = build / "project-fixtures/safe/socket.v3"
                        self.assertTrue(stat.S_ISSOCK(socket.stat().st_mode))
                        for directory in restricted:
                            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode))
                        subprocess.run(
                            [sys.executable, str(PREPARE), str(build), "--restrict"],
                            check=True,
                        )
                        for directory in restricted:
                            self.assertEqual(0, stat.S_IMODE(directory.stat().st_mode))
                        subprocess.run(["make", "clean", f"BUILD={build}"], check=True)
                        self.assertFalse(build.exists())
                    finally:
                        for directory in restricted:
                            if directory.exists():
                                directory.chmod(0o700)

    def test_passing_run_leaves_build_removable(self):
        with self.build_directory("project-pass-") as build:
            self.prepare(build)
            result = self.run_consumer(build, 0)
            self.assertEqual(0, result.returncode)
            self.assert_build_is_removable(build)

    def test_failing_run_leaves_build_removable(self):
        with self.build_directory("project-fail-") as build:
            self.prepare(build)
            result = self.run_consumer(build, 3)
            self.assertEqual(3, result.returncode)
            self.assert_build_is_removable(build)

    def test_run_unit_tests_wrapper_restores_permissions(self):
        for status in [0, 3]:
            with self.subTest(status=status):
                with self.build_directory(f"project-wrapper-{status}-") as build:
                    self.prepare(build)
                    script, report = self.stand_in(build.parent, build, status)
                    result = self.run_unit_tests(build, script)
                    output = result.stdout + result.stderr
                    if status == 0:
                        self.assertEqual(0, result.returncode, output)
                    else:
                        self.assertNotEqual(0, result.returncode, output)
                    self.assertEqual(
                        {str(path): 0 for path in self.restricted(build)},
                        json.loads(report.read_text(encoding="utf-8")),
                        output,
                    )
                    self.assert_build_is_removable(build)


if __name__ == "__main__":
    unittest.main()
