#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class FixtureCleanupTest(unittest.TestCase):
    def test_make_clean_removes_restricted_fixtures(self):
        with tempfile.TemporaryDirectory(prefix="project-clean-", dir=Path.cwd()) as parent:
            build = (Path(parent) / "output").relative_to(Path.cwd())
            subprocess.run(
                [sys.executable, "test/fixtures/projects/prepare.py", str(build)],
                check=True,
            )
            restricted = [
                build / "project-fixtures/unreadable-directories/blocked",
                build / "project-fixtures/ignored-entries/build",
            ]
            try:
                for directory in restricted:
                    self.assertEqual(0, stat.S_IMODE(directory.stat().st_mode))
                subprocess.run(["make", "clean", f"BUILD={build}"], check=True)
                self.assertFalse(build.exists())
            finally:
                for directory in restricted:
                    if directory.exists():
                        directory.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
