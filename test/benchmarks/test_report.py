#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check advisory flags, GC accounting, and the recorded Linux baseline table."""
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
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
bench = load("bench-lsp")
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

    def test_added_quantiles_leave_default_reports_unchanged(self):
        before = (report.baseline(self.result, 1), report.comparison(self.result))
        for row in self.result["summary"]:
            row.update(p50=row["median"], p95=row["p90"])
        self.assertEqual((report.baseline(self.result, 1), report.comparison(self.result)), before)

    def test_first_semantic_results_compare_each_open_order(self):
        def sample(build, order, warmup, latency):
            return {"scenario": "first_semantic_" + order, "warmup": warmup, "latency_ms": latency,
                    "open_latency_ms": latency - 1, "config_open_latency_ms": latency - 1,
                    "initialize_ms": 0.1, "discarded_analyses": int(order == "config_first"),
                    "probes": [{"process": "worker", "metric": "gc_count", "value": build + 1}]}
        def process(order, worker_hwm):
            # A replacement worker and a worker without a known HWM.
            phase = "first_semantic_" + order
            return {"resource_summary": {"processes": [
                {"role": "server", "phases": {phase: {"max_rss_bytes": 30, "hwm_bytes": 31}}},
                {"role": "worker", "phases": {phase: {"max_rss_bytes": 200, "hwm_bytes": worker_hwm}}},
                {"role": "worker", "phases": {phase: {"max_rss_bytes": 150, "hwm_bytes": 160}}}]}}
        self.result["blocks"] = [
            {"build": build, "project": "aeneas",
             "samples": [sample(build, order, True, 1000)] +
                        [sample(build, order, False, latency + 10 * build) for latency in (600, 700)],
             "processes": [process(order, None), process(order, 210)]}
            for build in (0, 1) for order in ("config_first", "source_first")]
        self.result["summary"] = bench.summaries(self.result["blocks"])
        text, warnings = report.comparison(self.result)
        rows = [line for line in text.splitlines() if line.startswith("| aeneas /")]
        self.assertEqual([row.split("|")[1].strip() for row in rows],
                         ["aeneas / `first_semantic_config_first`", "aeneas / `first_semantic_source_first`"])
        self.assertIn("| 650.000 (610.000–690.000) | 660.000 (620.000–700.000) | +1.5% | —/2 | —/4 |", rows[0])
        self.assertEqual(warnings, [])
        indexed = report.rows(self.result)
        self.assertEqual(indexed[(0, "aeneas", "first_semantic_source_first", "latency_ms")]["p95"], 695)
        hwm = indexed[(0, "aeneas", "first_semantic_source_first", "worker.hwm_bytes")]
        self.assertEqual((hwm["n"], hwm["min"]), (3, 160))  # The unknown HWM is not a zero.
        with self.assertRaises(KeyError):
            report.baseline(self.result, 1)  # The reference table needs the default scenarios.

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

    def commit(self, date, tooling=True, pin="pinned"):
        # A commit with the benchmark tooling and a Virgil submodule entry
        # whose commit, as in the real repository, isn't in this one.
        script = self.path / "scripts/bench-build-info.py"
        if tooling:
            script.parent.mkdir(exist_ok=True)
            script.write_text("")
        elif script.exists():
            script.unlink()
        self.git("add", "-A", "--", ".", ":!vendor")
        gitlink = hashlib.sha1(pin.encode()).hexdigest()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{gitlink},vendor/virgil")
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
                         (old, self.git("rev-parse", "HEAD"), None))

    def test_no_week_old_commit_falls_back_to_reachable_release_tag(self):
        release = self.commit("2026-10-24T00:00:00Z")
        self.git("tag", "v0.1.0-alpha.3")
        new = self.commit("2026-10-25T00:00:00Z")
        self.git("tag", "no-mistakes-abandoned/example")
        self.assertEqual(compare.previous_week(new, self.now), release)

    def test_dispatch_resolves_inputs_and_pr_uses_actual_head_and_base(self):
        base = self.commit("2026-10-17T00:00:00Z")
        new = self.commit("2026-10-25T00:00:00Z")
        self.assertEqual(compare.select_refs("workflow_dispatch", {}, base, new, self.now), (base, new, None))
        self.git("update-ref", "refs/remotes/origin/newer", new)
        self.assertEqual(compare.select_refs("workflow_dispatch", {}, base, "newer", self.now), (base, new, None))
        event = {"pull_request": {"base": {"sha": base}, "head": {"sha": new}}}
        self.assertEqual(compare.select_refs("pull_request", event, "ignored", "ignored", self.now), (base, new, None))
        with self.assertRaises(ValueError):
            compare.resolve("--help")

    def test_pr_pin_change_reports_skip_without_building(self):
        base = self.commit("2026-10-17T00:00:00Z", pin="older")
        new = self.commit("2026-10-25T00:00:00Z")
        script = self.path / "scripts/bench-compare.py"
        shutil.copyfile(ROOT / "scripts/bench-compare.py", script)
        event = self.path / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": new}}}))
        for tooling in (new, base):
            with self.subTest(tooling=tooling):
                self.git("checkout", "-q", tooling)
                output = self.path / tooling
                summary = self.path / (tooling + ".md")
                work = self.path / "builds"
                env = {**os.environ, "GITHUB_EVENT_NAME": "pull_request",
                       "GITHUB_EVENT_PATH": str(event), "GITHUB_STEP_SUMMARY": str(summary)}
                result = subprocess.run([sys.executable, script, "--work-dir", work,
                                         "--output-dir", output], env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                refs = json.loads((output / "refs.json").read_text())
                self.assertEqual((refs["base"], refs["new"]), (base, new))
                self.assertIn("skipped", refs["note"])
                self.assertIn(refs["note"], summary.read_text())
                self.assertIn("::notice::" + refs["note"], result.stdout)
                self.assertFalse(work.exists())
                self.assertFalse((output / "comparison.json").exists())

    def test_compatible_pr_still_compares_and_explicit_pin_change_fails(self):
        older = self.commit("2026-10-16T00:00:00Z", pin="older")
        base = self.commit("2026-10-17T00:00:00Z")
        new = self.commit("2026-10-25T00:00:00Z")
        event = self.path / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": new}}}))
        work, output = self.path / "builds", self.path / "results"
        env = {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(event)}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(sys, "argv", ["bench-compare.py", "--work-dir", str(work),
                                                "--output-dir", str(output)]), \
                mock.patch.object(compare, "compare") as benchmark:
            compare.main()
        benchmark.assert_called_once_with(base, new, work.resolve(), output.resolve(), None)
        with self.assertRaisesRegex(ValueError, "same pinned Virgil"):
            compare.compare(older, new, work, output)

    def test_weekly_base_without_tooling_moves_forward_on_first_parent(self):
        requested = self.commit("2026-10-16T00:00:00Z", tooling=False)
        self.commit("2026-10-19T00:00:00Z", tooling=False)
        self.git("checkout", "-qb", "side")
        self.commit("2026-10-20T00:00:00Z")  # Supported, but only on a side branch.
        self.git("checkout", "-q", "-")
        first = self.commit("2026-10-21T00:00:00Z")
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-24T00:00:00Z",
               "GIT_COMMITTER_DATE": "2026-10-24T00:00:00Z"}
        self.git("merge", "-q", "--no-ff", "side", "-m", "merge side", env=env)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        base, new, note = compare.select_refs("schedule", {}, "ignored", "ignored", self.now)
        self.assertEqual((base, new), (first, self.git("rev-parse", "HEAD")))
        self.assertIn(requested, note)
        self.assertIn(first, note)
        self.assertEqual(compare.select_refs("workflow_dispatch", {}, "last-week", new, self.now)[0], first)

    def test_weekly_base_with_another_pin_moves_forward(self):
        self.commit("2026-10-16T00:00:00Z", pin="older")
        first = self.commit("2026-10-20T00:00:00Z")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.assertEqual(compare.select_refs("schedule", {}, "ignored", "ignored", self.now)[0], first)

    def test_no_comparable_revision_fails_and_explicit_refs_never_move(self):
        base = self.commit("2026-10-16T00:00:00Z", tooling=False)
        new = self.commit("2026-10-24T00:00:00Z", tooling=False)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        with self.assertRaises(ValueError):
            compare.select_refs("schedule", {}, "ignored", "ignored", self.now)
        self.assertEqual(compare.select_refs("workflow_dispatch", {}, base, new, self.now), (base, new, None))


if __name__ == "__main__":
    unittest.main()
