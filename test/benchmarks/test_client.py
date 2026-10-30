#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Failures must never become successful low-latency samples."""
import argparse
import copy
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts/bench-lsp.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.project = json.loads(bench.corpus.MANIFEST.read_text())["projects"]["small"]
        self.block = bench.Block(ROOT / "build/virgil-lsp", self.project,
                                 argparse.Namespace(warmups=0, operations=1), {})
        self.snapshot = {"generation": 1, "diagnostics": [], "documentVersions": [
            {"uri": uri, "version": 1 if uri == self.block.uri else None, "current": True}
            for uri in self.block.uris]}

    def test_snapshot_rejects_empty_stale_and_wrong_version(self):
        self.block.snapshot(self.snapshot)
        for result in (None, {**self.snapshot, "documentVersions": []}):
            # None normally asks for inspection; use a fake reply to avoid a process.
            self.block.request = lambda *args: None
            with self.assertRaises(AssertionError):
                self.block.snapshot(result)
        for field, value in (("version", 0), ("current", False)):
            stale = copy.deepcopy(self.snapshot)
            stale["documentVersions"][0][field] = value
            with self.assertRaises(AssertionError):
                self.block.snapshot(stale)

    def test_reused_analysis_must_advance_generation(self):
        self.block.analysis(self.snapshot)
        with self.assertRaises(AssertionError):
            self.block.analysis(self.snapshot)

    def test_definition_rejects_empty_and_wrong_target(self):
        good = bench.location(self.project["definition"])
        self.block.definition(good)
        for result in (None, [], {**good, "uri": self.block.uris[1]}):
            with self.assertRaises(AssertionError):
                self.block.definition(result)

    def test_completion_rejects_fast_keyword_fallback_and_wrong_detail(self):
        item = {"label": self.project["completion_label"], "kind": 6,
                "detail": self.project["hover_prefix"]}
        self.block.validate_completion({"items": [item]})
        for result in (None, {"items": []}, {"items": [{**item, "kind": 14}]},
                       {"items": [{**item, "detail": "wrong"}]}):
            with self.assertRaises(AssertionError):
                self.block.validate_completion(result)

    def test_rename_requires_all_edits_and_current_version(self):
        good = {"documentChanges": [{"textDocument": {"uri": self.block.uri, "version": 1},
                                    "edits": [{"newText": self.project["rename_to"], "range": s["range"]}
                                              for s in self.project["references"]]}]}
        self.block.validate_rename(good)
        missing = copy.deepcopy(good)
        missing["documentChanges"][0]["edits"].pop()
        stale = copy.deepcopy(good)
        stale["documentChanges"][0]["textDocument"]["version"] = 0
        for result in (None, {"documentChanges": []}, missing, stale):
            with self.assertRaises(AssertionError):
                self.block.validate_rename(result)

    def test_diagnostics_reject_clear_wrong_error_and_stale_version(self):
        good = {"uri": self.block.uri, "version": 1,
                "diagnostics": [self.project["edits"]["semantic"]["expected"]]}
        self.block.validate_diagnostic("semantic", good)
        for result in ({**good, "version": 0}, {**good, "diagnostics": []},
                       {**good, "diagnostics": [self.project["edits"]["syntax"]["expected"]]}):
            with self.assertRaises(AssertionError):
                self.block.validate_diagnostic("semantic", result)

    def test_error_analysis_requires_expected_diagnostic_and_current_sources(self):
        diagnostics = [{"path": "main.v3", "kind": "UnresolvedIdentifier", "message": "missing name"}]
        good = {**self.snapshot, "diagnostics": diagnostics}
        self.block.analysis(good, diagnostics)
        for result in ({**good, "generation": 2, "diagnostics": []},
                       {**good, "generation": 2, "documentVersions": []},
                       {**good, "generation": 2, "diagnostics": [{**diagnostics[0], "kind": "ParseError"}]}):
            with self.assertRaises(AssertionError):
                self.block.analysis(result, diagnostics)
        with self.assertRaises(AssertionError):
            self.block.analysis(good, diagnostics)  # Repeated generation.

    def test_pinned_coordinates_and_source_order_are_checked(self):
        pinned, projects = bench.corpus.validate(strict=False)
        current = bench.corpus.generate(projects, pinned["virgil_source_revision"])
        self.assertEqual(current, {**pinned, "projects": {name: pinned["projects"][name] for name in projects}})
        shifted = copy.deepcopy(current)
        shifted["projects"]["small"]["query"]["range"]["start"]["line"] += 1
        with self.assertRaises(AssertionError):
            bench.check(current == shifted, "moved query anchor")

    def test_raw_probe_records_do_not_merge_server_and_worker(self):
        parsed = bench.probes("BENCH server 7 gc_count 1\nBENCH worker 7 gc_count 0\n")
        self.assertEqual([(p["process"], p["value"]) for p in parsed], [("server", 1), ("worker", 0)])

    def publications(self, *params):
        # A fake session: receive() replays publications, then times out as Session does.
        queue = [{"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": p} for p in params]
        def receive(deadline):
            assert queue, "timed out waiting for a protocol reply"
            return queue.pop(0)
        self.block.s = mock.Mock(receive=receive)
        return self.block.first_publication(0, 0)

    def test_first_semantic_publication_ignores_parser_clear_wrong_version_and_uri(self):
        expected = self.project["edits"]["semantic"]["expected"]
        good = {"uri": self.block.uri, "version": 1, "diagnostics": [expected]}
        earlier = ({**good, "diagnostics": []}, {**good, "version": 2}, {**good, "version": None},
                   {**good, "uri": self.block.uris[1]},
                   {**good, "diagnostics": [self.project["edits"]["syntax"]["expected"]]},
                   {**good, "diagnostics": [{**expected, "range": self.project["query"]["range"]}]},
                   {**good, "diagnostics": [expected, expected]})
        _, seen = self.publications(*earlier, good)
        self.assertEqual([(p["uri"], p["version"], p["codes"]) for p in seen],
                         [(p["uri"], p["version"], [d["code"] for d in p["diagnostics"]]) for p in earlier])
        for bad in earlier:
            with self.subTest(bad=bad), self.assertRaisesRegex(AssertionError, "timed out"):
                self.publications(bad)

    def test_first_semantic_publication_rejects_server_requests(self):
        self.block.s = mock.Mock(receive=lambda deadline: {"jsonrpc": "2.0", "id": 1, "method": "workspace/configuration"})
        with self.assertRaisesRegex(AssertionError, "unexpected pending request"):
            self.block.first_publication(0, 0)


class SummaryTest(unittest.TestCase):
    def test_quantiles_interpolate_raw_samples_without_trimming(self):
        row = bench.summarize([10, 1, 4, 3, 2, 9, 5, 8, 7, 6])
        self.assertEqual((row["n"], row["min"], row["max"]), (10, 1, 10))
        self.assertAlmostEqual(row["p10"], 1.9)
        self.assertAlmostEqual(row["p50"], 5.5)
        self.assertEqual(row["p50"], row["median"])
        self.assertAlmostEqual(row["p90"], 9.1)
        self.assertAlmostEqual(row["p95"], 9.55)
        self.assertEqual(row["mad"], 2.5)
        single = bench.summarize([7])
        self.assertEqual([single[k] for k in ("p10", "p50", "p90", "p95", "mad")], [7, 7, 7, 7, 0])
        self.assertAlmostEqual(bench.summarize([0, 100])["p95"], 95)

    def test_first_semantic_metrics_exclude_warmups_and_unknown_hwm(self):
        def sample(warmup, latency):
            return {"scenario": "first_semantic_source_first", "warmup": warmup, "latency_ms": latency,
                    "open_latency_ms": latency - 1, "config_open_latency_ms": latency - 2,
                    "initialize_ms": 0.1, "discarded_analyses": 0, "probes": []}
        def process(phase, worker_hwm):
            return {"resource_summary": {"processes": [
                {"role": "server", "phases": {phase: {"max_rss_bytes": 10, "hwm_bytes": 11}}},
                {"role": "worker", "phases": {phase: {"max_rss_bytes": 20, "hwm_bytes": worker_hwm}}}]}}
        blocks = [{"build": 0, "project": "small", "samples": [sample(True, 99), sample(False, 5), sample(False, 7)],
                   "processes": [process("warmup", 99), process("first_semantic_source_first", 21),
                                 process("first_semantic_source_first", None), {"resources": None}]}]
        rows = {row["metric"]: row for row in bench.summaries(blocks)}
        self.assertEqual(rows["latency_ms"]["n"], 2)
        self.assertEqual(rows["latency_ms"]["p50"], 6)
        self.assertEqual(rows["open_latency_ms"]["max"], 6)
        self.assertEqual((rows["server.max_rss_bytes"]["n"], rows["worker.max_rss_bytes"]["n"]), (2, 2))
        # Only the known HWM is summarized; the unknown one is neither zero nor invented.
        self.assertEqual((rows["worker.hwm_bytes"]["n"], rows["worker.hwm_bytes"]["min"]), (1, 21))
        self.assertFalse(any("total" in metric for metric in rows))

    def test_default_samples_keep_their_summary_metrics(self):
        blocks = [{"build": 0, "project": "small", "processes": [{"resources": []}], "samples": [
            {"scenario": "definition", "warmup": False, "latency_ms": 1.5, "probes": []}]}]
        self.assertEqual([row["metric"] for row in bench.summaries(blocks)], ["latency_ms"])

    def test_first_semantic_cannot_be_combined_with_other_workloads(self):
        for flag in ("--error-result", "--typing-burst", "--inlay-refresh"):
            result = subprocess.run([sys.executable, ROOT / "scripts/bench-lsp.py", "--first-semantic", flag,
                                     "--output", "/dev/null"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("--first-semantic runs alone", result.stderr)


class ResourceTest(unittest.TestCase):
    def sampler(self, *passes):
        reads = list(passes)
        clock = iter(range(100))
        return bench.ResourceSampler(1, read=lambda: reads.pop(0), clock=lambda: next(clock))

    def test_replacement_keeps_each_pid_and_never_sums_separate_maxima(self):
        sampler = self.sampler({1: ("server", 30, 31), 2: ("worker", 200, 210)},
                               {1: ("server", 40, 41), 2: ("worker", 100, 210)},
                               {1: ("server", 35, 41), 3: ("worker", 150, 160)},
                               {1: ("server", 35, 41), 3: ("worker", 120, 160)})
        self.assertTrue(sampler.sample())  # Both PIDs are new.
        self.assertFalse(sampler.sample())
        sampler.mark("error_analysis")
        self.assertFalse(sampler.sample())
        summary = sampler.stop(worker_starts=2)
        server, old, new = summary["processes"]
        self.assertEqual([(p["pid"], p["role"]) for p in summary["processes"]], [(1, "server"), (2, "worker"), (3, "worker")])
        self.assertEqual((server["max_rss_bytes"], server["hwm_bytes"], server["samples"]), (40, 41, 4))
        self.assertEqual((old["max_rss_bytes"], old["hwm_bytes"], old["seen_at_stop"]), (200, 210, False))
        self.assertEqual((new["max_rss_bytes"], new["hwm_bytes"], new["seen_at_stop"]), (150, 160, True))
        self.assertEqual(old["phases"], {"session": {"samples": 2, "max_rss_bytes": 200, "hwm_bytes": 210}})
        self.assertEqual(new["phases"], {"error_analysis": {"samples": 2, "max_rss_bytes": 150, "hwm_bytes": 160}})
        # Simultaneous total comes from one pass only, not 40 + 200 + 150.
        self.assertEqual(summary["max_sampled_total_rss_bytes"], 230)
        self.assertEqual(summary["unobserved_workers"], 0)

    def test_pid_transition_is_reported(self):
        sampler = self.sampler({1: ("server", 1, 1)}, {1: ("server", 2, 2)},
                               {1: ("server", 1, 2), 2: ("worker", 1, 1)}, {1: ("server", 1, 2)})
        self.assertEqual([sampler.sample() for _ in range(4)], [True, False, True, True])

    def test_unobserved_and_unknown_resources_stay_unknown(self):
        sampler = self.sampler(None, {1: ("server", 30, None), 2: ("worker", 200, None)})
        sampler.sample()
        sampler.sample()
        summary = sampler.stop(worker_starts=3)
        self.assertEqual((summary["passes"], summary["failed_passes"]), (1, 1))
        self.assertFalse(summary["hwm_available"])
        self.assertEqual([p["hwm_bytes"] for p in summary["processes"]], [None, None])
        self.assertEqual(summary["unobserved_workers"], 2)
        self.assertIsNone(self.sampler().stop()["unobserved_workers"])
        empty = self.sampler(None).stop(worker_starts=1)
        self.assertEqual((empty["processes"], empty["max_sampled_total_rss_bytes"], empty["hwm_available"]),
                         ([], None, False))
        self.assertEqual(empty["unobserved_workers"], 1)

    def test_missing_process_listing_is_none(self):
        with mock.patch.object(bench.Path, "is_dir", return_value=False), \
                mock.patch.object(bench, "command", side_effect=OSError("no ps")):
            self.assertIsNone(bench.process_tree(1))
            self.assertIsNone(bench.resources(1))
        with mock.patch.object(bench.Path, "is_dir", return_value=False), \
                mock.patch.object(bench, "command", return_value=" 5 1 64 S\n 6 5 32 S+\n 7 1 128 R\n 8 1 0 Z\n"), \
                mock.patch.object(bench, "optional_file", return_value=None):
            # macOS shape: RSS from ps, and no HWM rather than a zero. The
            # exited, unreaped worker 8 is left out rather than given a peak of 0.
            self.assertEqual(bench.process_tree(1), {5: ("worker", 64 * 1024, None), 7: ("worker", 128 * 1024, None)})
            self.assertEqual(bench.resources(5), [{"role": "server", "pid": 5, "rss_bytes": 65536, "peak_rss_bytes": None},
                                                  {"role": "worker", "pid": 6, "rss_bytes": 32768, "peak_rss_bytes": None}])

    def test_ps_listing_omits_an_exited_unreaped_worker(self):
        # A worker that exits before the first sample, and that its server
        # hasn't reaped yet, stays unobserved: its peak is unknown, not 0.
        server = subprocess.Popen([sys.executable, "-c", "import subprocess, sys, time\n"
                                   "worker = subprocess.Popen([sys.executable, '-c', 'pass'])\n"
                                   "print(worker.pid, flush=True)\n"
                                   "time.sleep(30)\n"], stdout=subprocess.PIPE, text=True)
        try:
            worker = int(server.stdout.readline())
            deadline = time.monotonic() + 10
            while not bench.command("ps", "-o", "stat=", "-p", str(worker)).startswith("Z"):
                self.assertLess(time.monotonic(), deadline, "the worker did not exit")
                time.sleep(0.01)
            with mock.patch.object(bench.Path, "is_dir", return_value=False):
                sampler = bench.ResourceSampler(server.pid)
                sampler.sample()
                summary = sampler.stop(worker_starts=1)
            self.assertEqual([(p["pid"], p["role"]) for p in summary["processes"]], [(server.pid, "server")])
            self.assertEqual(summary["unobserved_workers"], 1)
        finally:
            server.kill()
            server.wait()
            server.stdout.close()

    @unittest.skipUnless(Path("/proc/self/status").exists(), "Linux /proc is required")
    def test_live_sampler_reads_children_and_stops_with_the_session(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            tree = bench.process_tree(os.getpid())
            self.assertEqual(tree[os.getpid()][0], "server")
            self.assertEqual(tree[child.pid][0], "worker")
            self.assertTrue(all(rss > 0 and hwm >= rss for _, rss, hwm in tree.values()))
            sampler = bench.ResourceSampler(os.getpid()).start()
            child.kill()
            child.wait()
            summary = sampler.stop()
        finally:
            child.kill()
            child.wait()
        self.assertIsNone(sampler.thread)
        self.assertFalse(any(t.name == "resource-sampler" for t in threading.enumerate()))
        worker = next(p for p in summary["processes"] if p["pid"] == child.pid)
        self.assertFalse(worker["seen_at_stop"])
        self.assertTrue(summary["hwm_available"])


class CorpusPinTest(unittest.TestCase):
    def setUp(self):
        self.pinned = json.loads(bench.corpus.MANIFEST.read_text())
        self.revision = self.pinned["virgil_source_revision"]

    def validate(self, gitlink, checkout, strict=False):
        notice = io.StringIO()
        with mock.patch.object(bench.corpus, "revisions", return_value=(gitlink, checkout)), \
                contextlib.redirect_stderr(notice):
            result = bench.corpus.validate(strict=strict)
        return result[1], notice.getvalue()

    def test_pinned_checkout_checks_every_project(self):
        # Keep policy cases independent of the real vendor checkout (advisory CI).
        with mock.patch.object(bench.corpus, "generate", return_value=self.pinned) as generated:
            projects, notice = self.validate(self.revision, self.revision)
            self.assertEqual(projects, list(bench.corpus.PROJECTS))
            self.assertEqual(notice, "")
            generated.assert_called_once_with(projects, self.revision)
            self.validate(self.revision, self.revision, strict=True)

    def test_manifest_must_list_exactly_the_reviewed_projects(self):
        for change, expected in (("missing", "missing util"), ("extra", "unexpected extra")):
            changed = copy.deepcopy(self.pinned)
            if change == "missing":
                changed["projects"].pop("util")
            else:
                changed["projects"]["extra"] = changed["projects"]["small"]
            with mock.patch.object(bench.corpus, "MANIFEST") as manifest:
                manifest.read_text.return_value = json.dumps(changed)
                for gitlink, checkout, strict in ((self.revision, self.revision, False),
                                                  (self.revision, self.revision, True),
                                                  (self.revision, "a" * 40, False),
                                                  ("unknown", "unknown", False)):
                    with self.subTest(change=change, gitlink=gitlink, checkout=checkout, strict=strict):
                        with mock.patch.object(bench.corpus, "revisions", return_value=(gitlink, checkout)):
                            with self.assertRaises(AssertionError) as error:
                                bench.corpus.validate(strict=strict)
                        message = str(error.exception)
                        for detail in ("small, util, aeneas", expected,
                                       "python3 scripts/bench-corpus.py --write"):
                            self.assertIn(detail, message)

    def test_gitlink_is_read_from_the_index(self):
        with mock.patch.object(bench.corpus, "git_revision",
                               side_effect=[f"160000 {self.revision} 0\tvendor/virgil", self.revision]) as git:
            self.assertEqual(bench.corpus.revisions(), (self.revision, self.revision))
        self.assertEqual(git.call_args_list[0].args[1:], ("ls-files", "-s", "vendor/virgil"))

    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_unborn_head_with_staged_gitlink(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", "--initial-branch=main", directory], check=True)
            subprocess.run(["git", "-C", directory, "update-index", "--add", "--cacheinfo",
                            f"160000,{self.revision},vendor/virgil"], check=True)
            with mock.patch.object(bench.corpus, "ROOT", Path(directory)):
                self.assertEqual(bench.corpus.revisions()[0], self.revision)

    def test_advisory_and_export_check_small_without_reading_vendor(self):
        generate = bench.corpus.generate
        for gitlink, checkout in ((self.revision, "a" * 40), ("unknown", "unknown")):
            with mock.patch.object(bench.corpus, "generate", wraps=generate) as generated, \
                    mock.patch.object(bench.corpus, "site", wraps=bench.corpus.site) as sites:
                projects, notice = self.validate(gitlink, checkout)
                generated.assert_called_once_with(["small"], self.revision)
                self.assertTrue(all(call.args[0].startswith("test/") for call in sites.call_args_list))
            self.assertEqual(projects, ["small"])
            self.assertEqual(len(notice.splitlines()), 1)
            self.assertIn("skipping vendor projects (util, aeneas)", notice)
            with self.assertRaisesRegex(AssertionError, "requires pinned Virgil"):
                self.validate(gitlink, checkout, strict=True)

    def test_gitlink_bump_requires_regeneration_even_if_checkout_is_old(self):
        for checkout in (self.revision, "a" * 40):
            with self.assertRaises(AssertionError) as error:
                self.validate("a" * 40, checkout)
            message = str(error.exception)
            for detail in (self.revision, "a" * 40, "python3 scripts/bench-corpus.py --write",
                           "CONTRIBUTING.md#updating-the-pinned-virgil-revision"):
                self.assertIn(detail, message)

    def test_small_hash_and_anchor_changes_still_fail_when_vendor_is_skipped(self):
        changed = copy.deepcopy(self.pinned)
        changed["projects"]["small"]["files"][0]["sha256"] = "0" * 64
        with mock.patch.object(bench.corpus, "MANIFEST") as manifest:
            manifest.read_text.return_value = json.dumps(changed)
            with self.assertRaisesRegex(AssertionError, "corpus sources or anchors changed"):
                self.validate(self.revision, "a" * 40)


if __name__ == "__main__":
    unittest.main()
