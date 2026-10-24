#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Structural scoring must reject plausible stale protocol answers."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tolerance", ROOT / "scripts/bench-tolerance.py")
tolerance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tolerance)
CONTROLS = json.loads(Path(__file__).with_name("tolerance-negative-oracles.json").read_text())["cases"]


class Scoring(unittest.TestCase):
    def test_negative_controls_reject_predecessor_answers(self):
        self.assertEqual(set(CONTROLS), {"deleted-declaration", "changed-signature", "shadowing-local"})
        for name, case in CONTROLS.items():
            with self.subTest(case=name):
                results = [tolerance.score(key.split(":")[-1], old, case["oracle"][key], True)
                           for key, old in case["predecessor"].items()]
                self.assertTrue(any(row["wrong"] for row in results))
                # Hover alone exposes all three safety violations, even when
                # unchanged identifier ranges happen to map perfectly.
                self.assertTrue(tolerance.score("hover", case["predecessor"]["0:hover"],
                                                case["oracle"]["0:hover"], True)["wrong"])

    def test_negative_policy_accepts_suppression_or_current_answer(self):
        for case in CONTROLS.values():
            for key, expected in case["oracle"].items():
                feature = key.split(":")[-1]
                empty = [] if feature in ("references", "diagnostics") else None
                with self.subTest(feature=feature):
                    self.assertTrue(tolerance.score(feature, empty, expected, True)["correct"])
                    self.assertTrue(tolerance.score(feature, expected, expected, True)["correct"])

    def test_absence_is_distinct_from_wrong_answer(self):
        expected = CONTROLS["changed-signature"]["oracle"]["0:hover"]
        self.assertEqual(tolerance.score("hover", None, expected), {
            "available": False, "correct": False, "wrong": False, "matches_oracle": False})
        self.assertEqual(tolerance.score("hover", None, expected, True), {
            "available": False, "correct": True, "wrong": False, "matches_oracle": False})

    def test_legitimate_empty_results_are_available_and_correct(self):
        for feature, empty in (("definition", None), ("hover", None),
                               ("references", []), ("diagnostics", [])):
            with self.subTest(feature=feature):
                self.assertEqual(tolerance.score(feature, empty, empty), {
                    "available": True, "correct": True, "wrong": False, "matches_oracle": True})
        # null and [] have different protocol meanings and are not interchangeable.
        self.assertFalse(tolerance.score("references", None, [])["correct"])

    def test_definition_requires_target_uri_and_every_range_coordinate(self):
        location = CONTROLS["changed-signature"]["oracle"]["0:definition"]
        for side in ("start", "end"):
            for coordinate in ("line", "character"):
                changed = copy.deepcopy(location)
                changed["range"][side][coordinate] += 1
                self.assertTrue(tolerance.score("definition", changed, location)["wrong"])
        changed = {**location, "uri": "project:///wrong.v3"}
        self.assertTrue(tolerance.score("definition", changed, location)["wrong"])

    def test_hover_requires_text_kind_and_range(self):
        hover = CONTROLS["changed-signature"]["oracle"]["0:hover"]
        for change in ("value", "kind", "range"):
            actual = copy.deepcopy(hover)
            if change == "range":
                actual["range"]["start"]["character"] += 1
            else:
                actual["contents"][change] += " different"
            self.assertTrue(tolerance.score("hover", actual, hover)["wrong"])

    def test_reference_and_diagnostic_sets_ignore_only_order(self):
        case = CONTROLS["changed-signature"]
        for feature, key in (("references", "0:references"), ("diagnostics", "diagnostics")):
            expected = case["oracle"][key]
            self.assertGreaterEqual(len(expected), 2)
            self.assertTrue(tolerance.score(feature, list(reversed(expected)), expected)["correct"])
            self.assertTrue(tolerance.score(feature, expected[:-1], expected)["wrong"])
            changed = copy.deepcopy(expected)
            changed[0]["uri"] += "wrong"
            self.assertTrue(tolerance.score(feature, changed, expected)["wrong"])
        diagnostic = copy.deepcopy(case["oracle"]["diagnostics"])
        for field in ("code", "message", "severity", "source", "range"):
            actual = copy.deepcopy(diagnostic)
            actual[0]["diagnostic"][field] = "different"
            self.assertTrue(tolerance.score("diagnostics", actual, diagnostic)["wrong"])

    def test_summary_separates_modes_kinds_and_features(self):
        rows = [{"suite": "repair", "mode": mode, "edit_kind": kind,
                 "scores": {"0:hover": tolerance.score("hover", actual, {"text": "answer"})},
                 "samples": [{"feature": "hover", "latency_ms": 10}], "worker_replacements": 1}
                for mode, kind, actual in (("warm", "body", None), ("cold", "header", {"text": "wrong"}))]
        result = tolerance.summary(rows)
        self.assertEqual(len(result["features"]), 2)
        self.assertEqual(sum(r["wrong"] for r in result["features"]), 1)
        self.assertEqual(result["worker_replacements"], 2)
        self.assertTrue(all(row["p50"] == row["p95"] == 10 for row in result["latency_ms"]))

    def test_utf16_coordinates_include_non_ascii_before_query(self):
        text = '/* é😀 */ return A.f(1);\r\n'
        q = tolerance.states.query("B.v3", text, "A.f(1)", "f")
        at = text.index("A.f") + 2
        self.assertEqual(q["position"], {"line": 0, "character": at + 1})
        self.assertEqual(tolerance.states.corpus.position(text, len(text)), {"line": 1, "character": 0})


if __name__ == "__main__":
    unittest.main()
