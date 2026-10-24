#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Failures must never become successful low-latency samples."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

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

    def test_pinned_coordinates_and_source_order_are_checked(self):
        current = bench.corpus.generate()
        self.assertEqual(current, json.loads(bench.corpus.MANIFEST.read_text()))
        shifted = copy.deepcopy(current)
        shifted["projects"]["small"]["query"]["range"]["start"]["line"] += 1
        with self.assertRaises(AssertionError):
            bench.check(current == shifted, "moved query anchor")

    def test_raw_probe_records_do_not_merge_server_and_worker(self):
        parsed = bench.probes("BENCH server 7 gc_count 1\nBENCH worker 7 gc_count 0\n")
        self.assertEqual([(p["process"], p["value"]) for p in parsed], [("server", 1), ("worker", 0)])


if __name__ == "__main__":
    unittest.main()
