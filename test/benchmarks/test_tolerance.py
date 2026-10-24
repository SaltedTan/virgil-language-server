#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Structural scoring must reject plausible stale protocol answers."""
import copy
import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tolerance", ROOT / "scripts/bench-tolerance.py")
tolerance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tolerance)
CONTROLS = json.loads(Path(__file__).with_name("tolerance-negative-oracles.json").read_text())["cases"]


class Scoring(unittest.TestCase):
    def test_negative_controls_reject_predecessor_answers(self):
        self.assertEqual(set(CONTROLS), {"deleted-declaration", "changed-signature", "shadowing-local",
                                        "body-boundary-escape"})
        for name, case in CONTROLS.items():
            with self.subTest(case=name):
                results = [tolerance.score(key.split(":")[-1], old, case["oracle"][key], True)
                           for key, old in case["predecessor"].items()]
                self.assertTrue(any(row["wrong"] for row in results))
                # Hover alone exposes all four safety violations, even when
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


class Cleanup(unittest.TestCase):
    def test_interrupted_reply_preserves_written_failed_record(self):
        for phase in ("capture", "warm", "cold"):
            for teardown in ("shutdown", "close"):
                with self.subTest(phase=phase, teardown=teardown), contextlib.ExitStack() as stack:
                    temp = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT)))
                    output = temp / "result.json"
                    project = {"base": temp, "config": {}, "sources": {
                        "A.v3": "original", "Observer.v3": "observer"}}
                    (temp / "A.v3").write_text("original")
                    state = {"id": "member-with-prefix", "suite": "repair", "project": project,
                             "predecessor": {"A.v3": "predecessor"}, "current": {"A.v3": "broken"},
                             "queries": [{"path": "Observer.v3"}], "disk": True}
                    corpus = mock.Mock()
                    corpus.fixtures.return_value = [state]
                    corpus.controls.return_value = []
                    session = mock.Mock()
                    session.transcript = []
                    session.proc.pid = 123
                    original = KeyboardInterrupt("interrupted reply")
                    secondary = RuntimeError("pending reply during " + teardown)

                    def send(method, params=None):
                        session.transcript.append(("send", {"id": 1, "method": method}))
                        return 1

                    def interrupted_reply(request_id, timeout):
                        session.buffer = b'Content-Length: 50\r\n\r\n{"jsonrpc":'
                        raise original

                    session.send.side_effect = send
                    session.result.side_effect = interrupted_reply
                    getattr(session, teardown).side_effect = secondary
                    patches = [
                        mock.patch.object(sys, "argv", ["bench-tolerance.py", "--smoke", "--cpu", "none",
                                                       "--work-dir", str(temp), "--output", str(output)]),
                        mock.patch.object(tolerance.bench.corpus, "validate", return_value=({}, None)),
                        mock.patch.object(tolerance.bench.corpus, "git_revision", return_value="revision"),
                        mock.patch.object(tolerance.bench, "pin_cpu", return_value={}),
                        mock.patch.object(tolerance.bench, "host_info", return_value={}),
                        mock.patch.object(tolerance.bench, "binary_info", return_value={}),
                        mock.patch.object(tolerance.states, "Corpus", return_value=corpus),
                        mock.patch.object(tolerance.bench, "Session", return_value=session),
                        mock.patch.object(tolerance.bench, "logs", return_value="server evidence"),
                    ]
                    if phase != "capture":
                        patches.append(mock.patch.object(tolerance, "capture", return_value=({"0:hover": "oracle"}, {})))
                    if phase == "cold":
                        execute = tolerance.execute
                        patches.append(mock.patch.object(tolerance, "execute", side_effect=
                            lambda exe, state, mode, oracle: {} if mode == "warm" else execute(exe, state, mode, oracle)))
                    for patch in patches:
                        stack.enter_context(patch)
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        tolerance.main()
                    self.assertIs(raised.exception, original)
                    failure = json.loads(Path(str(output) + ".failure.json").read_text())
                    self.assertEqual(failure["error"], repr(original))
                    self.assertEqual(failure["failed_record"], json.loads(json.dumps(original.tolerance_record)))
                    self.assertEqual(failure["failed_record"]["transcript"][0][1]["method"], "initialize")
                    self.assertIn(repr(secondary), failure["failed_record"]["cleanup_errors"])
                    self.assertEqual(failure["failed_record"]["stderr"], "server evidence")
                    session.close.assert_called_once_with()
                    self.assertEqual((temp / "A.v3").read_text(), "original")

    def test_shutdown_failure_survives_log_and_close_failures(self):
        row = {}
        session = mock.Mock()
        original = RuntimeError("shutdown failed")
        log_error = OSError("log read failed")
        close_error = OSError("close failed")
        session.shutdown.side_effect = original
        session.close.side_effect = close_error
        with mock.patch.object(tolerance.bench, "Session", return_value=session), \
                mock.patch.object(tolerance.bench, "logs", side_effect=log_error):
            run = tolerance.Run(Path("server"), {}, row)
            with self.assertRaises(RuntimeError) as raised:
                run.close()
        self.assertIs(raised.exception, original)
        self.assertEqual(row["cleanup_errors"], [repr(log_error), repr(close_error)])
        session.close.assert_called_once_with()

    def test_cleanup_failure_after_success_is_reported(self):
        session = mock.Mock()
        session.transcript = []
        session.proc.pid = 123
        original = RuntimeError("shutdown failed")
        session.shutdown.side_effect = original
        with mock.patch.object(tolerance.bench, "Session", return_value=session), \
                mock.patch.object(tolerance.bench, "logs", return_value="server evidence"), \
                mock.patch.object(tolerance.Run, "start"), \
                mock.patch.object(tolerance.Run, "answers", return_value=({"0:hover": "answer"}, [])):
            with self.assertRaises(RuntimeError) as raised:
                tolerance.capture(Path("server"), {"project": {}}, {}, [], [])
        self.assertIs(raised.exception, original)
        self.assertEqual(original.tolerance_record["stderr"], "server evidence")
        session.close.assert_called_once_with()

    def test_failure_file_error_preserves_original_exception(self):
        original = KeyboardInterrupt("interrupted reply")
        row = {"transcript": []}
        original.tolerance_record = row
        try:
            raise original
        except KeyboardInterrupt:
            with tolerance.cleanup(row):
                raise OSError("failure file unavailable")
            self.assertIs(sys.exc_info()[1], original)
        self.assertEqual(row["cleanup_errors"], ["OSError('failure file unavailable')"])


if __name__ == "__main__":
    unittest.main()
