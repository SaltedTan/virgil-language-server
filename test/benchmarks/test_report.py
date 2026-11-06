#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check advisory flags, GC accounting, and the recorded Linux baseline table."""
import datetime
import hashlib
import importlib.util
import json
import math
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


POLICY = ROOT / "test/benchmarks/resource-policy.json"
PIN = "b86f43f102406e94dc2ac4a85909c38ef414e214"
HEAP = 402_653_184
# Worker allocation near the measured Aeneas, util, and small analyses.
ALLOCATED = {"small": 56_424, "util": 9_798_920, "aeneas": 188_284_408}
RESOURCE_SCENARIOS = ("fresh_analysis", "reused_analysis", "completion_miss", "rename", "semantic_diagnostic")


def resource_result(pins=(PIN, PIN)):
    """A two-build comparison with one warmup and two measured transactions per case.

    As in a real run, each fresh analysis has its own server and worker, and
    the later scenarios number their transactions on the last fresh worker."""
    builds = [{"exe": f"/runner/_temp/bench-builds/{name}/build/bench/virgil-lsp",
               "version": f"virgil-lsp 0.1.0\n  source revision: {revision}\n  virgil revision: {pin}",
               "build": {"server_revision": revision, "benchmark_probes": True, "target": "x86-64-linux",
                         "worker_heap": "384m", "server_heap": "runtime default", "compiler": {"sha256": "c" * 64}}}
              for name, revision, pin in (("base", "a" * 40, pins[0]), ("next", "b" * 40, pins[1]))]
    blocks = []
    for build in (0, 1):
        for project, allocated in ALLOCATED.items():
            samples, pid = [], 1000 * (len(blocks) + 1)
            for scenario in RESOURCE_SCENARIOS + ("definition",):
                for i in range(3):
                    probes = []
                    if scenario == "fresh_analysis":
                        pid, id = pid + 2, 0
                        probes += [{"process": "server", "analysis_id": 1, "metric": metric, "value": value}
                                   for metric, value in (("worker_heap_bytes", HEAP), ("worker_pid", pid + 1))]
                    if scenario != "definition":
                        id += 1
                        probes += [{"process": "worker", "analysis_id": id, "metric": metric, "value": value}
                                   for metric, value in (
                            ("allocated_bytes", allocated + 8 * i), ("result_bytes", allocated // 40),
                            ("gc_count", 0), ("heap_after_handle_bytes", allocated + 1_000_000))]
                        probes.append({"process": "server", "analysis_id": id, "metric": "gc_count", "value": i % 2})
                    samples.append({"scenario": scenario, "iteration": i, "warmup": i == 0, "server_pid": pid,
                                    "latency_ms": 10.0 + i, "probes": probes})
            blocks.append({"block": 0, "build": build, "project": project, "samples": samples, "processes": []})
    result = {"schema": 1, "started_utc": "2026-11-03T00:00:00+00:00", "root": "/runner/work/tooling",
              "host": {"cpu": "test CPU", "kernel": "Linux", "affinity": {}}, "corpus_sha256": "f" * 64,
              "configuration": {"blocks": 1, "operations": 2, "warmups": 1, "error_result": False,
                                "typing_burst": False, "inlay_refresh": False, "first_semantic": False,
                                "parse_work": False},
              "builds": builds, "blocks": blocks}
    result["summary"] = bench.summaries(blocks)
    return result


def set_worker(result, build, project, scenario, metric, value):
    """Sets a worker probe in every measured transaction of one case."""
    for block in result["blocks"]:
        if block["build"] == build and block["project"] == project:
            for sample in block["samples"]:
                if sample["scenario"] == scenario and not sample["warmup"]:
                    for probe in sample["probes"]:
                        if probe["process"] == "worker" and probe["metric"] == metric:
                            probe["value"] = value(probe["value"])


def approved(policy):
    return {**policy, "status": "approved", "approval": "test approval"}


class ResourceTest(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(POLICY.read_text())
        self.result = resource_result()

    def check(self, result=None, policy=None, enforce=False):
        text, verdicts = report.resources(result or self.result, policy or self.policy, enforce)
        return text, [(severity, message.split(":")[0]) for severity, message in verdicts], verdicts

    def cli(self, result, policy=None, mode="report"):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        (directory / "comparison.json").write_text(json.dumps(result))
        (directory / "policy.json").write_text(json.dumps(policy or self.policy))
        return subprocess.run([sys.executable, ROOT / "scripts/bench-report.py", directory / "comparison.json",
                               "--resources", directory / "policy.json", "--mode", mode, "--annotations"],
                              text=True, capture_output=True)

    def test_reviewed_policy_is_approved_and_covers_the_scheduled_scenarios(self):
        # The maintainer approved the starting values as proposed (#347).
        self.assertEqual(self.policy["status"], "approved")
        self.assertIn("#347", self.policy["approval"])
        report.load_policy(self.policy)
        groups = {group["name"]: group for group in self.policy["groups"]}
        self.assertEqual((groups["clean_analysis"]["allocation_increase"], groups["clean_analysis"]["new_worker_gc"]),
                         (0.05, "fail"))
        self.assertEqual(self.policy["headroom_warning_bytes"], 2 * 1024 * 1024)
        self.assertEqual(set(report.SCENARIOS), {s for g in groups.values() if g["required"] for s in g["scenarios"]}
                         | set(self.policy["excluded"]) - {"typing_burst", "refresh_analysis"})
        proposed = {**self.policy, "status": "proposed", "approval": None}
        with self.assertRaisesRegex(ValueError, "not approved"):
            report.resources(self.result, proposed, enforce=True)
        with self.assertRaisesRegex(ValueError, "approval"):
            report.load_policy({**self.policy, "approval": None})

    def test_shipped_baselines_are_approved_generator_output_for_both_ci_targets(self):
        # One approved baseline per CI target, each covering every required
        # scenario of every project, with the ceilings resource_baseline() derives:
        # the median plus the group's allowance, and no collection where a new one fails.
        baselines = self.policy["baselines"]
        self.assertEqual(sorted(b["compatibility"]["target"] for b in baselines), ["x86-64-darwin", "x86-64-linux"])
        groups = report.load_policy(self.policy)
        required = {f"{project}/{scenario}" for project in self.policy["projects"]
                    for scenario, group in groups.items() if group["required"]}
        for baseline in baselines:
            self.assertTrue(baseline["approval"])
            self.assertEqual(baseline["compatibility"]["options"], self.policy["options"])
            self.assertEqual(set(baseline["scenarios"]), required)
            for name, case in baseline["scenarios"].items():
                group = groups[name.split("/", 1)[1]]
                self.assertEqual(case["ceiling"]["allocated_bytes"],
                                 math.ceil(case["allocated_bytes"]["median"] * (1 + group["allocation_increase"])), name)
                self.assertEqual(case["ceiling"]["worker_gc"],
                                 0 if group["new_worker_gc"] == "fail" and case["worker_gc_max"] == 0 else None, name)

    def test_same_build_reports_only_the_pending_baseline(self):
        text, verdicts, _ = self.check()
        self.assertEqual(verdicts, [("warn", "absolute baseline pending")])
        self.assertIn("| aeneas / `fresh_analysis` | clean_analysis | 188,284,420 (188,284,416–188,284,424) |", text)
        self.assertIn("| +0.00% | 4,707,110 / 4,707,110 | 0/0 | 12,042,184 |  |", text)
        self.assertIn("201,326,592-byte semispace", text)
        self.assertNotIn("definition", text)
        result = self.cli(self.result)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, text)
        self.assertEqual(result.stderr.count("::warning title=Resource check::"), 1)

    def test_result_bytes_are_reported_only_and_pending_is_always_a_warning(self):
        set_worker(self.result, 1, "aeneas", "fresh_analysis", "result_bytes", lambda v: v * 3)
        _, verdicts, _ = self.check(policy=approved(self.policy), enforce=True)
        self.assertEqual(verdicts, [("warn", "absolute baseline pending")])
        for field, value in (("result_bytes_increase", 0.05), ("pending_baseline", "fail")):
            with self.assertRaisesRegex(ValueError, "unsupported resource-policy fields"):
                report.load_policy({**self.policy, field: value})

    def test_increased_allocation_would_fail_and_fails_only_when_enforced(self):
        set_worker(self.result, 1, "aeneas", "fresh_analysis", "allocated_bytes", lambda v: v * 106 // 100)
        for build, value in ((0, 1_000_000), (1, 1_050_000), (0, 2_000_000), (1, 2_100_001)):
            set_worker(self.result, build, "util" if value < 2_000_000 else "small", "reused_analysis",
                       "allocated_bytes", lambda v, value=value: value)
        text, verdicts, messages = self.check()
        self.assertIn(("fail", "aeneas/fresh_analysis"), verdicts)
        self.assertNotIn(("fail", "util/reused_analysis"), verdicts)  # Exactly 5% is not over the limit.
        self.assertIn(("fail", "small/reused_analysis"), verdicts)
        self.assertIn("aeneas/fresh_analysis: worker allocation +6.00%, over the 5% limit", [m for _, m in messages])
        self.assertIn("- WOULD FAIL: aeneas/fresh_analysis", text)
        result = self.cli(self.result)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning title=Resource check (would fail)::aeneas/fresh_analysis: worker allocation +6.00%25", result.stderr)
        refused = self.cli(self.result, {**self.policy, "status": "proposed", "approval": None}, mode="enforce")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("not approved", refused.stderr)
        enforced = self.cli(self.result, approved(self.policy), mode="enforce")
        self.assertEqual(enforced.returncode, 1)
        self.assertIn("::error title=Resource check::aeneas/fresh_analysis", enforced.stderr)
        self.assertIn("- FAIL: aeneas/fresh_analysis", enforced.stdout)
        passed = self.cli(resource_result(), approved(self.policy), mode="enforce")
        self.assertEqual(passed.returncode, 0, passed.stderr)

    def test_report_mode_warns_when_the_check_cannot_run(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        comparison = directory / "comparison.json"
        comparison.write_text(json.dumps(self.result))
        (directory / "malformed.json").write_text("{")
        (directory / "schema2.json").write_text(json.dumps({**self.policy, "schema": 2}))
        (directory / "truncated.json").write_text(json.dumps(self.result)[:100])
        cases = [(comparison, directory / "missing.json"), (comparison, directory / "malformed.json"),
                 (comparison, directory / "schema2.json"), (directory / "truncated.json", POLICY),
                 (directory / "absent.json", POLICY)]
        for source, policy in cases:
            command = [sys.executable, ROOT / "scripts/bench-report.py", source, "--resources", policy, "--annotations"]
            with self.subTest(source=source.name, policy=policy.name):
                result = subprocess.run(command, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("**Not checked.** The resource checks could not run:", result.stdout)
                self.assertIn("::warning title=Resource check::the resource checks could not run:", result.stderr)
                enforced = subprocess.run(command + ["--mode", "enforce"], text=True, capture_output=True)
                self.assertNotEqual(enforced.returncode, 0)
                self.assertNotIn("Not checked", enforced.stdout)

    def test_new_worker_collection_fails_only_in_clean_analyses(self):
        for scenario in ("reused_analysis", "completion_miss"):
            set_worker(self.result, 1, "util", scenario, "gc_count", lambda v: 1)
        _, verdicts, _ = self.check()
        self.assertIn(("fail", "util/reused_analysis"), verdicts)
        self.assertIn(("warn", "util/completion_miss"), verdicts)
        text, _, _ = self.check()
        self.assertIn("| 0/2 | collected |", text)
        # A collection the base already had is not new.
        set_worker(self.result, 0, "util", "reused_analysis", "gc_count", lambda v: 1)
        _, verdicts, _ = self.check()
        self.assertNotIn(("fail", "util/reused_analysis"), verdicts)

    def test_server_collections_alone_are_never_checked(self):
        before = self.check()
        for block in self.result["blocks"]:
            for sample in block["samples"]:
                for probe in sample["probes"]:
                    if probe["process"] == "server" and probe["metric"] == "gc_count" and block["build"] == 1:
                        probe["value"] = 5
        self.assertEqual(self.check(), before)
        self.assertEqual(before[1], [("warn", "absolute baseline pending")])

    def test_worker_probes_must_be_one_transactions_own(self):
        def sample(result, project, scenario, iteration, build=1):
            block = next(b for b in result["blocks"] if b["build"] == build and b["project"] == project)
            return next(s for s in block["samples"] if s["scenario"] == scenario and s["iteration"] == iteration)
        def probe(sample, metric):
            return next(p for p in sample["probes"] if p["process"] == "worker" and p["metric"] == metric)
        def mismatched(result):
            probe(sample(result, "aeneas", "reused_analysis", 1), "gc_count")["analysis_id"] = 999999
        def borrowed(result):
            # A measured transaction's collection replaced by another's zero.
            target, source = sample(result, "util", "reused_analysis", 2), sample(result, "util", "rename", 1)
            probe(target, "gc_count")["value"] = 1
            target["probes"] = [p for p in target["probes"] if p["metric"] != "gc_count" or p["process"] != "worker"]
            target["probes"].append(dict(probe(source, "gc_count")))
        def duplicated(result):
            # A whole transaction's probes copied into another sample of the same process.
            target, source = sample(result, "small", "rename", 2), sample(result, "small", "rename", 1)
            target["probes"] = [dict(p) for p in source["probes"]]
        def unnumbered(result):
            del probe(sample(result, "small", "fresh_analysis", 2, build=0), "result_bytes")["analysis_id"]
        for change, detail in ((mismatched, "worker probes from transactions [3, 999999]"),
                               (borrowed, "worker probes from transactions"),
                               (duplicated, "worker transaction 9 also belongs to ('small', 'rename', 1)"),
                               (unnumbered, "without a transaction number")):
            with self.subTest(change=change.__name__):
                result = resource_result()
                change(result)
                text, verdicts, messages = self.check(result)
                self.assertIn("Qualification failed", text)
                self.assertTrue(verdicts and all(severity == "fail" for severity, _ in verdicts), verdicts)
                self.assertTrue(any(detail in message for _, message in messages), messages)

    def test_omitted_probe_scenario_or_metadata_fails_qualification(self):
        def omit(result):
            block = next(b for b in result["blocks"] if b["build"] == 1 and b["project"] == "small")
            sample = next(s for s in block["samples"] if s["scenario"] == "rename" and not s["warmup"])
            sample["probes"] = [p for p in sample["probes"] if p["metric"] != "result_bytes"]
        def drop(result):
            for block in result["blocks"]:
                if block["build"] == 1 and block["project"] == "aeneas":
                    block["samples"] = [s for s in block["samples"] if s["scenario"] != "completion_miss"]
        def duplicate(result):
            block = next(b for b in result["blocks"] if b["build"] == 0 and b["project"] == "util")
            sample = next(s for s in block["samples"] if s["scenario"] == "fresh_analysis" and not s["warmup"])
            sample["probes"] += [p for p in sample["probes"] if p["metric"] == "allocated_bytes"]
        def unrooted(result):
            del result["root"]
        def options(result):
            result["configuration"]["error_result"] = True
        def heapless(result):
            for block in result["blocks"]:
                for sample in block["samples"]:
                    sample["probes"] = [p for p in sample["probes"] if p["metric"] != "worker_heap_bytes"]
        for change, detail in ((omit, "0 worker result_bytes probes"), (duplicate, "2 worker allocated_bytes probes"),
                               (drop, "aeneas/completion_miss: measured in builds [0]"),
                               (unrooted, "missing root_bytes"), (options, "scenario options"),
                               (heapless, "missing worker_heap_bytes")):
            with self.subTest(change=change.__name__):
                result = resource_result()
                change(result)
                text, verdicts, messages = self.check(result)
                self.assertIn("Qualification failed", text)
                self.assertNotIn("| Project / scenario |", text)
                self.assertTrue(verdicts and all(severity == "fail" for severity, _ in verdicts), verdicts)
                self.assertTrue(any(detail in message for _, message in messages), messages)

    def test_mismatched_pin_heap_or_path_layout_is_incomparable(self):
        def pin(result):
            result["builds"][1]["version"] = result["builds"][1]["version"].replace(PIN, "0" * 40)
        def heap(result):
            result["builds"][1]["build"]["worker_heap"] = "1g"
            for block in result["blocks"]:
                for sample in block["samples"]:
                    for probe in sample["probes"]:
                        if probe["metric"] == "worker_heap_bytes" and block["build"] == 1:
                            probe["value"] = 1 << 30
        def layout(result):
            result["builds"][1]["exe"] = "/runner/_temp/bench-builds/newer/build/bench/virgil-lsp"
        for change, key in ((pin, "virgil_revision"), (heap, "worker_heap, worker_heap_bytes"), (layout, "build_dir_bytes")):
            with self.subTest(change=change.__name__):
                result = resource_result()
                change(result)
                set_worker(result, 1, "aeneas", "fresh_analysis", "allocated_bytes", lambda v: v * 2)
                text, verdicts, messages = self.check(result)
                self.assertEqual(verdicts, [("warn", "base and new builds are incomparable"),
                                            ("warn", "absolute baseline pending")])
                self.assertIn(key, messages[0][1])
                rows = [line for line in text.splitlines() if line.startswith("| aeneas /")]
                self.assertTrue(rows and all("| incomparable |" in row and "%" not in row for row in rows), rows)

    def test_timing_only_changes_do_not_change_resource_verdicts(self):
        before = self.check()
        for block in self.result["blocks"]:
            if block["build"] == 1:
                for sample in block["samples"]:
                    sample["latency_ms"] *= 3
        self.result["summary"] = bench.summaries(self.result["blocks"])
        _, warnings = report.comparison(self.result)
        self.assertTrue(warnings)  # The advisory timing comparison flags it.
        self.assertEqual(self.check(), before)
        enforced = self.cli(self.result, approved(self.policy), mode="enforce")
        self.assertEqual(enforced.returncode, 0, enforced.stderr)

    def test_headroom_under_two_mib_warns_from_the_actual_heap(self):
        set_worker(self.result, 1, "aeneas", "reused_analysis", "heap_after_handle_bytes", lambda v: HEAP // 2 - 450_000)
        _, verdicts, messages = self.check(enforce=False)
        self.assertIn(("warn", "aeneas/reused_analysis"), verdicts)
        self.assertIn("450,000 bytes of semispace room", "".join(m for _, m in messages))
        self.assertEqual(self.cli(self.result, approved(self.policy), mode="enforce").returncode, 0)
        # The room is half the worker's reported heap, not a fixed byte count.
        for block in self.result["blocks"]:
            for sample in block["samples"]:
                for probe in sample["probes"]:
                    if probe["metric"] == "worker_heap_bytes":
                        probe["value"] = 1 << 30
        text, verdicts, _ = self.check()
        self.assertNotIn(("warn", "aeneas/reused_analysis"), verdicts)
        self.assertIn(f"{(1 << 29) - HEAP // 2 + 450_000:,}", text)

    def test_pin_upgrade_without_and_with_an_approved_baseline(self):
        upgrade = "1" * 40
        candidate = report.resource_baseline(resource_result((upgrade, upgrade)), self.policy)
        self.assertIsNone(candidate["approval"])
        self.assertEqual(candidate["scenarios"]["aeneas/fresh_analysis"]["ceiling"],
                         {"allocated_bytes": math.ceil(188_284_420 * 1.05), "worker_gc": 0})
        self.assertEqual(candidate["scenarios"]["aeneas/rename"]["ceiling"]["worker_gc"], None)
        self.assertEqual(candidate["compatibility"]["virgil_revision"], upgrade)
        result = resource_result((PIN, upgrade))
        set_worker(result, 1, "aeneas", "fresh_analysis", "allocated_bytes", lambda v: v * 104 // 100)
        # Without an approved baseline: incomparable and pending, never a regression.
        unapproved = {**self.policy, "baselines": [candidate]}
        for policy in (self.policy, unapproved):
            _, verdicts, _ = self.check(result, policy)
            self.assertEqual(verdicts, [("warn", "base and new builds are incomparable"),
                                        ("warn", "absolute baseline pending")])
        # With one, the new build is checked against its approved ceilings.
        adopted = {**self.policy, "baselines": [{**candidate, "approval": "test approval"}]}
        text, verdicts, _ = self.check(result, adopted)
        self.assertEqual(verdicts, [("warn", "base and new builds are incomparable")])
        self.assertIn("Absolute baseline: approved test approval", text)
        set_worker(result, 1, "aeneas", "fresh_analysis", "allocated_bytes", lambda v: v * 102 // 100)
        _, verdicts, messages = self.check(result, adopted)
        self.assertIn(("fail", "aeneas/fresh_analysis"), verdicts)
        self.assertIn("over the approved ceiling", messages[-1][1])
        # A baseline for another corpus or path layout doesn't apply.
        for key, value in (("corpus_sha256", "e" * 64), ("root_bytes", 1)):
            other = {**candidate, "approval": "test approval",
                     "compatibility": {**candidate["compatibility"], key: value}}
            _, verdicts, _ = self.check(result, {**self.policy, "baselines": [other]})
            self.assertIn(("warn", "absolute baseline pending"), verdicts)

    def test_unreviewed_worker_scenario_warns(self):
        for block in self.result["blocks"]:
            # Each a transaction of its own process.
            block["samples"] += [{**s, "scenario": "new_analysis", "server_pid": s["server_pid"] + 500}
                                 for s in block["samples"] if s["scenario"] == "fresh_analysis"]
        _, verdicts, _ = self.check()
        self.assertIn(("warn", "new_analysis"), verdicts)


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
