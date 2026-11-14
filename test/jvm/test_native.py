#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Exercise native.py's host and process policy with macOS boundary fixtures.

Oracle: #465 requires untranslated execution, not an uninstalled Rosetta.
Hand-written kernel snapshots reject translated helpers, wrong Java runtimes,
and lost descendants or exec transitions. No implementation source is read.
"""
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import native


JAVA = "/expected/runtime/bin/java"
ARM64 = 0x0100000C  # Mach-O CPU_TYPE_ARM64, from mach/machine.h.
X86_64 = 0x01000007


class HostTests(unittest.TestCase):
    def check_host(self, present=True, x86_status=0, machine="arm64", translated=False):
        output = io.StringIO()
        with ExitStack() as stack:
            for name, value in {
                "require_macos": None, "libraries": (None, None),
                "platform.machine": machine, "translated": translated,
                "os.path.exists": present,
            }.items():
                stack.enter_context(patch("native." + name, return_value=value))
            stack.enter_context(patch("native.subprocess.run", side_effect=[
                subprocess.CompletedProcess([], 0, "0\n", ""),
                subprocess.CompletedProcess([], x86_status, "", ""),
            ]))
            with redirect_stdout(output):
                native.host(None)
        return output.getvalue()

    def test_rosetta_present_and_x86_execution_are_reported_but_allowed(self):
        output = self.check_host()
        self.assertIn("present", output)
        self.assertIn("arch -x86_64 /usr/bin/true: status 0", output)

    def test_rosetta_absent_is_also_allowed(self):
        output = self.check_host(present=False, x86_status=1)
        self.assertIn("absent", output)
        self.assertIn("arch -x86_64 /usr/bin/true: status 1", output)

    def test_wrong_machine_or_translated_or_unknown_runner_fails(self):
        for overrides in ({"machine": "x86_64"}, {"translated": True}, {"translated": None}):
            with self.subTest(overrides=overrides), self.assertRaises(SystemExit):
                self.check_host(**overrides)


class RunTests(unittest.TestCase):
    def test_process_parents_from_real_ps(self):
        # Independent oracle for the ps interface: the OS's own parent PID.
        self.assertEqual(native.process_parents()[os.getpid()], os.getppid())

    def check_run(self, frames, min_java=1, status=0, adopt=()):
        # Each entry is pid: (ppid, executable, translated, Mach-O CPU types).
        # The observer consumes every fixture frame before the command exits;
        # no correctness assertion depends on a wall-clock scheduling delay.
        finished = threading.Event()
        current = {}
        remaining = iter(frames)

        def parents():
            nonlocal current
            current = next(remaining, {})
            if not current:
                finished.set()
            return {pid: info[0] for pid, info in current.items()}

        def wait():
            if not finished.wait(5):
                self.fail("the process observer did not consume its snapshots")
            return status

        output = io.StringIO()
        error = None
        with tempfile.TemporaryDirectory(dir=".") as directory, ExitStack() as stack:
            report = Path(directory) / "processes.json"
            probes = {
                "require_macos": lambda: None,
                "libraries": lambda: (None, None),
                "process_parents": parents,
                "pidpath": lambda _, pid: current[pid][1],
                "translated": lambda _, pid: current[pid][2] if pid in current else False,
                "cpu_types": lambda path: next(info[3] for info in current.values() if info[1] == path),
                "process_args": lambda _, pid: [current[pid][1]],
                "alive": lambda pid: True,
            }
            for name, probe in probes.items():
                stack.enter_context(patch.object(native, name, side_effect=probe))
            stack.enter_context(patch("native.subprocess.Popen", return_value=SimpleNamespace(pid=100, wait=wait)))
            args = SimpleNamespace(command=["fixture-command"], java=JAVA, min_java=min_java, adopt=list(adopt),
                                   report=str(report))
            with redirect_stdout(output):
                try:
                    native.run(args)
                except SystemExit as failure:
                    error = str(failure)
            records = json.loads(report.read_text())["processes"]
        return error, records, output.getvalue()

    def test_all_generations_and_only_command_descendants(self):
        error, records, _ = self.check_run([{
            102: (101, JAVA, False, [ARM64]),
            101: (100, "/bin/sh", False, [ARM64, X86_64]),
            100: (1, "/usr/bin/make", False, [ARM64, X86_64]),
            200: (1, "/unrelated/java", True, [X86_64]),
        }])
        self.assertIsNone(error)
        self.assertEqual([record["pid"] for record in records], [100, 101, 102])

    def test_translated_or_unknown_non_java_descendant_fails(self):
        for flag in (True, None):
            with self.subTest(flag=flag):
                error, _, _ = self.check_run([{
                    100: (1, JAVA, False, [ARM64]),
                    101: (100, "/bin/helper", flag, [ARM64, X86_64]),
                }])
                self.assertIn("process 101 runs translated, or its translation is unknown", error)

    def test_exec_transition_to_translated_helper_fails(self):
        error, records, _ = self.check_run([
            {100: (1, JAVA, False, [ARM64]), 101: (100, "/bin/sh", False, [ARM64, X86_64])},
            {100: (1, JAVA, False, [ARM64]), 101: (100, "/bin/helper", True, [X86_64])},
        ])
        self.assertIn("process 101 runs translated", error)
        self.assertEqual(len(records), 3)

    def test_shell_exec_java_and_reparented_descendants_are_kept(self):
        error, records, _ = self.check_run([
            {100: (1, "/bin/sh", False, [ARM64]), 101: (100, "/bin/helper", False, [ARM64])},
            {100: (1, JAVA, False, [ARM64]), 101: (1, "/bin/helper", False, [ARM64]),
             102: (101, JAVA, False, [ARM64])},
        ], min_java=2)
        self.assertIsNone(error)
        self.assertEqual({record["pid"] for record in records if record["path"] == JAVA}, {100, 102})

    def test_wrong_java_path_binary_or_translation_fails(self):
        for path, flag, cpus, message in (
            ("/wrong/java", False, [ARM64], "doesn't match"),
            (JAVA, False, [X86_64], "isn't an arm64 Mach-O file"),
            (JAVA, True, [ARM64], "runs translated"),
        ):
            with self.subTest(path=path, flag=flag, cpus=cpus):
                error, _, _ = self.check_run([{100: (1, path, flag, cpus)}])
                self.assertIn(message, error)

    def test_adopted_new_processes_and_their_descendants_are_followed(self):
        # The first snapshot is taken before the command starts.
        old_tmux = {50: (1, "/usr/bin/tmux", False, [ARM64]), 51: (50, "/old/java", True, [X86_64])}
        error, records, _ = self.check_run([old_tmux, {
            **old_tmux,
            100: (1, "/bin/sh", False, [ARM64]),
            300: (1, "/opt/homebrew/bin/tmux", False, [ARM64]),
            301: (300, "/opt/homebrew/bin/nvim", False, [ARM64]),
            302: (301, JAVA, False, [ARM64]),
            400: (1, "/bin/other", True, [X86_64]),
        }], adopt=["tmux"])
        self.assertIsNone(error)
        self.assertEqual([record["pid"] for record in records], [100, 300, 301, 302])

    def test_adopted_translated_descendant_fails(self):
        error, _, _ = self.check_run([{}, {
            100: (1, "/bin/sh", False, [ARM64]),
            300: (1, "/opt/homebrew/bin/tmux", False, [ARM64]),
            301: (300, "/bin/helper", True, [X86_64]),
            302: (300, JAVA, False, [ARM64]),
        }], adopt=["tmux"])
        self.assertIn("process 301 runs translated", error)

    def test_java_count_and_command_exit_are_still_required(self):
        frame = {100: (1, JAVA, False, [ARM64])}
        error, _, _ = self.check_run([frame, frame], min_java=2)
        self.assertIn("expected at least 2 Java processes, saw 1", error)
        error, _, _ = self.check_run([frame], status=7)
        self.assertIn("the command exited with status 7", error)


if __name__ == "__main__":
    unittest.main()
