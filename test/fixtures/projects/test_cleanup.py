#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check that the fixture permissions never keep the build directory from
being removed, after passing and failing test runs."""
from contextlib import contextmanager
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PREPARE = Path(__file__).resolve().with_name("prepare.py")
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


if __name__ == "__main__":
    unittest.main()
