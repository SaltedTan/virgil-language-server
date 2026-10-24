#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check advisory flags, GC accounting, and the recorded Linux baseline table."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = load("bench-report")
compare = load("bench-compare")
FIXTURE = Path(__file__).with_name("linux-reference.json")


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.result = json.loads(FIXTURE.read_text())

    def test_recorded_linux_baseline_reproduces_documented_table(self):
        rendered = report.baseline(self.result, 1)
        table = rendered[rendered.index("| Scenario |"):]
        expected = Path(__file__).with_name("linux-reference-table.txt").read_text()
        self.assertEqual(table, expected)
        self.assertIn("ac815fab2c0396accb42b902841ec5e6b82aa0d1", rendered)
        self.assertNotIn(self.result["builds"][0]["build"]["server_revision"], rendered)

    def test_flags_require_both_conditions_in_both_directions(self):
        before = {"median": 100, "p10": 95, "p90": 105}
        cases = [({"median": 126, "p10": 120, "p90": 130}, True),
                 ({"median": 74, "p10": 70, "p90": 80}, True),
                 ({"median": 125, "p10": 120, "p90": 130}, False),
                 ({"median": 75, "p10": 70, "p90": 80}, False),
                 ({"median": 200, "p10": 104, "p90": 250}, False),
                 ({"median": 200, "p10": 105, "p90": 250}, False)]
        for after, expected in cases:
            with self.subTest(after=after):
                relative, flagged = report.change(before, after)
                self.assertAlmostEqual(relative, after["median"] / 100 - 1)
                self.assertEqual(flagged, expected)

    def test_gc_counts_sum_measured_transactions_separately_by_role(self):
        def sample(warmup, server, worker):
            return {"scenario": "reused_analysis", "warmup": warmup,
                    "probes": [{"process": role, "metric": "gc_count", "value": n}
                               for role, n in (("server", server), ("worker", worker))]}
        self.result["blocks"] = [
            {"build": 0, "project": "small", "samples": [sample(True, 99, 99), sample(False, 1, 2)]},
            {"build": 0, "project": "small", "samples": [sample(False, 3, 4)]},
            {"build": 1, "project": "small", "samples": [sample(False, 0, 1)]}]
        text, _ = report.comparison(self.result)
        row = next(line for line in text.splitlines() if "small / `reused_analysis`" in line)
        self.assertIn("| 4/6 | 0/1 |", row)
        cached = next(line for line in text.splitlines() if "small / `definition`" in line)
        self.assertIn("| —/— | —/— |", cached)

    def test_failed_partial_or_mismatched_results_are_rejected(self):
        for result in ({**self.result, "error": "failure"}, {**self.result, "schema": 2},
                       {**self.result, "summary": []}):
            with self.assertRaises(ValueError):
                report.comparison(result)
        self.result["summary"].pop()
        with self.assertRaisesRegex(ValueError, "matching"):
            report.comparison(self.result)

    def test_baseline_requires_all_projects_and_valid_build(self):
        with self.assertRaises(ValueError):
            report.baseline(self.result, 2)
        self.result["summary"].pop()
        with self.assertRaises(KeyError):
            report.baseline(self.result, 1)

    def test_cli_flags_warn_without_nonzero_exit_and_escape_percent(self):
        result = subprocess.run([sys.executable, ROOT / "scripts/bench-report.py", FIXTURE, "--annotations"],
                                text=True, capture_output=True, check=True)
        self.assertIn("## Benchmark comparison", result.stdout)
        self.assertIn("::warning title=Benchmark change::", result.stderr)
        self.assertIn("%25", result.stderr)
        baseline = subprocess.run([sys.executable, ROOT / "scripts/bench-report.py", FIXTURE, "--baseline"],
                                  text=True, capture_output=True, check=True)
        self.assertEqual(baseline.stdout, report.baseline(self.result, 1))


class RefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.patch = mock.patch.object(compare, "ROOT", self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.now = datetime.datetime(2026, 10, 25, tzinfo=datetime.timezone.utc)

    def git(self, *args, **kwargs):
        return subprocess.check_output(["git", *args], cwd=self.path, text=True, **kwargs).strip()

    def commit(self, date):
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        self.git("commit", "-q", "--allow-empty", "-m", date, env=env)
        return self.git("rev-parse", "HEAD")

    def test_weekly_uses_first_parent_and_scans_nonmonotonic_dates(self):
        old = self.commit("2026-10-17T00:00:00Z")
        self.commit("2026-10-24T00:00:00Z")
        self.git("checkout", "-qb", "side")
        self.commit("2026-10-16T00:00:00Z")
        self.git("checkout", "-q", "-")
        self.commit("2026-10-23T00:00:00Z")
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-25T00:00:00Z",
               "GIT_COMMITTER_DATE": "2026-10-25T00:00:00Z"}
        self.git("merge", "-q", "--no-ff", "side", "-m", "merge side", env=env)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(compare.select_refs("schedule", {}, "ignored", "ignored", self.now),
                         (old, self.git("rev-parse", "HEAD")))

    def test_no_week_old_commit_falls_back_to_reachable_release_tag(self):
        release = self.commit("2026-10-24T00:00:00Z")
        self.git("tag", "v0.1.0-alpha.3")
        new = self.commit("2026-10-25T00:00:00Z")
        self.git("tag", "no-mistakes-abandoned/example")
        self.assertEqual(compare.previous_week(new, self.now), release)

    def test_dispatch_resolves_inputs_and_pr_uses_actual_head_and_base(self):
        base = self.commit("2026-10-17T00:00:00Z")
        new = self.commit("2026-10-25T00:00:00Z")
        self.assertEqual(compare.select_refs("workflow_dispatch", {}, base, new, self.now), (base, new))
        self.git("update-ref", "refs/remotes/origin/newer", new)
        self.assertEqual(compare.select_refs("workflow_dispatch", {}, base, "newer", self.now), (base, new))
        event = {"pull_request": {"base": {"sha": base}, "head": {"sha": new}}}
        self.assertEqual(compare.select_refs("pull_request", event, "ignored", "ignored", self.now), (base, new))
        with self.assertRaises(ValueError):
            compare.resolve("--help")


if __name__ == "__main__":
    unittest.main()
