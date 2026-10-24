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
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
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
