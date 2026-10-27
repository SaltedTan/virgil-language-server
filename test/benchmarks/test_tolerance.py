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
                                        "body-boundary-escape", "excluded-error-limit",
                                        "repair-needs-brace", "repair-in-header", "repair-in-field-initializer",
                                        "repair-in-two-bodies", "repair-call-arity",
                                        "repair-unterminated-string", "repair-deleted-header",
                                        "repair-missing-return-limit", "brace-swallow-field",
                                        "brace-two-closers", "brace-split-header", "brace-indentation-disagrees",
                                        "brace-mixed-width", "brace-unterminated-string",
                                        "brace-closer-deeper-field", "brace-braceless-if-then-while",
                                        "brace-inner-closer", "brace-method-closer", "brace-tab-stops",
                                        "brace-and-blank-two-bodies", "brace-closer-then-blank",
                                        "brace-opener-missing-return-limit",
                                        "brace-closer-missing-return-limit", "brace-closer-deeper-continuation",
                                        "brace-closer-deeper-def", "brace-closer-deeper-method",
                                        "brace-closer-deeper-constructor", "brace-closer-deeper-case",
                                        "brace-closer-same-line-field", "brace-closer-continued-member",
                                        "brace-opener-deeper-method", "brace-closer-local-function",
                                        "brace-closer-local-function-returns", "brace-closer-local-function-lines",
                                        "brace-closer-local-value", "brace-inner-closer-local-function",
                                        "brace-opener-bodiless-holder", "brace-method-closer-private",
                                        "brace-inner-closer-private", "brace-closer-then-blank-private",
                                        "brace-closer-missing-return-limit-private"})
        for name, case in CONTROLS.items():
            with self.subTest(case=name):
                results = [tolerance.score(key.split(":")[-1], old, case["oracle"][key], True)
                           for key, old in case["predecessor"].items()]
                self.assertTrue(any(row["wrong"] for row in results))
                if name == "excluded-error-limit":
                    self.assertTrue(tolerance.score("diagnostics", case["predecessor"]["diagnostics"],
                                                    case["oracle"]["diagnostics"], True)["wrong"])
                else:
                    # Hover exposes the binding violations, even when the
                    # unchanged identifier ranges happen to map perfectly.
                    self.assertTrue(tolerance.score("hover", case["predecessor"]["0:hover"],
                                                    case["oracle"]["0:hover"], True)["wrong"])
                for key, old in case["predecessor"].items():
                    if key.split(":")[-1] in ("references", "diagnostics"):
                        expected = case["oracle"][key]
                        if old == expected:
                            continue  # Some signature edits leave locations identical.
                        if all(item in (expected or []) for item in old):
                            # The captured independent Observer error is a safe
                            # subset. Move its range to model a stale diagnostic,
                            # rather than demanding that safe suppression fail.
                            self.assertTrue(tolerance.score("diagnostics", old, expected)["partial"])
                            old = copy.deepcopy(old)
                            old[0]["diagnostic"]["range"]["start"]["character"] += 1
                        self.assertTrue(any(item not in (expected or []) for item in old),
                                        (name, key, "stale set must contain a foreign item"))
                        self.assertTrue(tolerance.score(key.split(":")[-1], old,
                                                        expected, True)["wrong"])

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
            "available": False, "correct": False, "partial": False, "missing": True,
            "wrong": False, "matches_oracle": False})
        self.assertEqual(tolerance.score("hover", None, expected, True), {
            "available": False, "correct": True, "partial": False, "missing": True,
            "wrong": False, "matches_oracle": False})

    def test_legitimate_empty_results_are_available_and_correct(self):
        for feature, empty in (("definition", None), ("hover", None),
                               ("references", []), ("diagnostics", [])):
            with self.subTest(feature=feature):
                self.assertEqual(tolerance.score(feature, empty, empty), {
                    "available": True, "correct": True, "partial": False, "missing": False,
                    "wrong": False, "matches_oracle": True})
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

    def test_reference_and_diagnostic_items_compare_exactly(self):
        case = CONTROLS["changed-signature"]
        for feature, key in (("references", "0:references"), ("diagnostics", "diagnostics")):
            expected = case["oracle"][key]
            self.assertGreaterEqual(len(expected), 2)
            self.assertTrue(tolerance.score(feature, list(reversed(expected)), expected)["correct"])
            self.assertEqual(tolerance.score(feature, expected[:-1], expected), {
                "available": True, "correct": False, "partial": True, "missing": False,
                "wrong": False, "matches_oracle": False})
            foreign = {**expected[0], "uri": "project:///foreign.v3"}
            self.assertTrue(tolerance.score(feature, expected[:-1] + [foreign], expected)["wrong"])
            self.assertTrue(tolerance.score(feature, expected + [expected[0]], expected)["wrong"])
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

    def test_summary_counts_partial_answers_separately(self):
        verdict = tolerance.score("references", [{"uri": "one"}], [{"uri": "one"}, {"uri": "two"}])
        result = tolerance.summary([{"suite": "repair", "mode": "warm", "edit_kind": "body",
                                     "scores": {"0:references": verdict}, "samples": [],
                                     "worker_replacements": 0}])["features"][0]
        self.assertEqual((result["correct"], result["partial"], result["missing"], result["wrong"]), (0, 1, 0, 0))

    def test_utf16_coordinates_include_non_ascii_before_query(self):
        text = '/* é😀 */ return A.f(1);\r\n'
        q = tolerance.states.query("B.v3", text, "A.f(1)", "f")
        at = text.index("A.f") + 2
        self.assertEqual(q["position"], {"line": 0, "character": at + 1})
        self.assertEqual(tolerance.states.corpus.position(text, len(text)), {"line": 1, "character": 0})


class EditingSessions(unittest.TestCase):
    def test_lifecycle_preserves_notification_order_and_reopen_versions(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            project = {"base": Path(temp)}
            session = mock.Mock()
            with mock.patch.object(tolerance.bench, "Session", return_value=session):
                run = tolerance.Run(Path("server"), project, {})
            events = tolerance.sessions
            for action in (events.action("open", "A.v3", "old"),
                           events.action("change", "A.v3", "broken", "fixed"),
                           events.action("save", "A.v3", "broken", "fixed"),
                           events.action("close", "A.v3"),
                           events.action("open", "A.v3", "broken", "fixed"),
                           events.action("change", "A.v3", "new")):
                run.apply(action, intended=True)
            calls = session.send.call_args_list
            self.assertEqual([c.args[0] for c in calls], [
                "textDocument/didOpen", "textDocument/didChange", "textDocument/didSave",
                "textDocument/didClose", "textDocument/didOpen", "textDocument/didChange"])
            self.assertEqual(calls[1].args[1]["contentChanges"], [{"text": "fixed"}])
            self.assertEqual(calls[4].args[1]["textDocument"]["version"], 1)
            self.assertEqual(calls[5].args[1]["textDocument"]["version"], 2)
            self.assertEqual((Path(temp) / "A.v3").read_text(), "fixed")

    def test_closed_empty_publication_needs_no_overlay_but_stale_errors_fail(self):
        base = Path("/project")
        session = mock.Mock()
        with mock.patch.object(tolerance.bench, "Session", return_value=session):
            run = tolerance.Run(Path("server"), {"base": base}, {})
        uri = (base / "A.v3").as_uri()
        run.diagnostics[uri] = {"version": 2, "diagnostics": []}
        self.assertEqual(run.watched_diagnostics(["A.v3"]), [])
        run.diagnostics[uri]["diagnostics"] = [{"code": "TypeError"}]
        with self.assertRaises(AssertionError):
            run.watched_diagnostics(["A.v3"])

    def test_interval_length_distinguishes_blanking_from_cached_text(self):
        base = Path("/project")
        snapshot = {"documentVersions": [
            {"uri": (base / name).as_uri(), "substitution": {
                "begin": 10, "effectiveEnd": end, "currentEnd": current}}
            for name, end, current in (("cached.v3", 11, 10), ("blanked.v3", 25, 25),
                                       ("old-errors.v3", 300, 10), ("one-byte.v3", 11, 11))]}
        self.assertEqual(tolerance.substitution_kinds(snapshot, base), {
            "cached.v3": "fallback", "blanked.v3": "repair", "old-errors.v3": "fallback", "one-byte.v3": "repair"})

    def test_step_checks_require_eviction_evidence_and_full_restoration(self):
        base = Path("/project")
        record = {"snapshot": {"documentVersions": [
            {"uri": (base / "A.v3").as_uri(), "substitution": {
                "begin": 10, "effectiveEnd": 25, "currentEnd": 25}}]},
                  "scores": {"0:hover": tolerance.score("hover", None, {"binding": "new"})}}
        tolerance.check_session_step({"id": "evicted", "substitution": {"A.v3": "repair"}}, record, base, "warm")
        for checks in ({"substitution": {"A.v3": "fallback"}}, {"full_semantics": True},
                       {"no_substitutions": True}, {"absent_substitutions": ["A.v3"]}):
            with self.subTest(checks=checks), self.assertRaises(AssertionError):
                tolerance.check_session_step({"id": "broken", **checks}, record, base, "warm")

    def test_error_limit_notice_policy_is_checked_independently_of_scoring(self):
        base = Path("/project")
        record = {"snapshot": {"documentVersions": [], "diagnostics": [{}] * 15},
                  "answers": {"diagnostics": [{"diagnostic": {"code": "ErrorLimit"}}]}}
        st = {"id": "limit", "limit_reached": True, "limit_notice": {"warm": True, "cold": False}}
        tolerance.check_session_step(st, record, base, "warm")
        with self.assertRaises(AssertionError):
            tolerance.check_session_step(st, record, base, "cold")
        record["answers"]["diagnostics"] = []
        tolerance.check_session_step(st, record, base, "cold")
        record["snapshot"]["diagnostics"] = [{}] * 14
        with self.assertRaises(AssertionError):
            tolerance.check_session_step(st, record, base, "cold")

    def test_control_expectations_count_bodies_and_limit_notices(self):
        base = Path("/project")
        snapshot = {"documentVersions": [
            {"uri": (base / "A.v3").as_uri(), "substitution": {"bodies": [{"begin": 1, "end": 2}, {"begin": 3, "end": 4}]}},
            {"uri": (base / "B.v3").as_uri(), "substitution": {"bodyBegin": 1, "bodyEnd": 2}},
            {"uri": (base / "C.v3").as_uri()}], "diagnostics": [{}] * 15}
        bodies = tolerance.excluded_bodies(snapshot, base)
        self.assertEqual(bodies, {"A.v3": 2, "B.v3": 1})
        answers = {"diagnostics": []}
        self.assertEqual(tolerance.mismatches({"expected_bodies": {"A.v3": 2, "C.v3": 0}, "expected_limit": True},
                                              bodies, snapshot, answers), [])
        self.assertEqual(len(tolerance.mismatches({"expected_bodies": {"A.v3": 1, "C.v3": 1}}, bodies, snapshot, answers)), 2)
        answers["diagnostics"] = [{"diagnostic": {"code": "ErrorLimit"}}]
        self.assertEqual(len(tolerance.mismatches({"expected_limit": True}, bodies, snapshot, answers)), 1)
        snapshot["diagnostics"] = [{}] * 14
        self.assertEqual(len(tolerance.mismatches({"expected_limit": True}, bodies, snapshot, {"diagnostics": []})), 1)

    def test_session_summaries_do_not_multiply_process_replacements(self):
        st = {"suite": "session", "mode": "cold", "edit_kind": "reopened",
              "scores": {"0:hover": tolerance.score("hover", "current", "current")}, "samples": []}
        result = tolerance.session_summary([{"steps": [st, st], "worker_replacements": 1}])
        self.assertEqual(result["worker_replacements"], 1)
        self.assertEqual(result["features"][0]["correct"], 2)

    def test_replay_uses_one_process_and_retains_failure_step(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            base = Path(temp)
            project = {"base": base, "sources": {"A.v3": "old", "Observer.v3": "observer"}, "config": {}}
            case = {"id": "history", "description": "test", "project": project,
                    "initial": {"A.v3": "old"}, "queries": [{"path": "Observer.v3"}],
                    "steps": [tolerance.sessions.step("first", tolerance.sessions.action("change", "A.v3", "broken", "fixed")),
                              tolerance.sessions.step("second", tolerance.sessions.action("change", "A.v3", "new"))]}
            session = mock.Mock()
            session.proc.pid = 123
            session.transcript = []
            session.err = tempfile.TemporaryFile(dir=base)
            self.addCleanup(session.err.close)
            original = RuntimeError("second-step failure")

            def answers(run, queries, paths, measure=False):
                if run.row.get("id") == "second":
                    raise original
                run.row["snapshot"] = {"documentVersions": [{"uri": (base / "Observer.v3").as_uri()}]}
                return {"0:" + f: ["bound"] for f in tolerance.FEATURES[:3]}, [{"feature": "diagnostics"}]

            def start(run, overlays):
                run.versions = {p: 1 for p in overlays}

            with mock.patch.object(tolerance.bench, "Session", return_value=session) as factory, \
                    mock.patch.object(tolerance.Run, "start", start), \
                    mock.patch.object(tolerance.Run, "answers", answers), \
                    mock.patch.object(tolerance.bench, "logs", return_value="session logs"):
                with self.assertRaises(RuntimeError) as raised:
                    tolerance.execute_session(Path("server"), case, "warm")
            self.assertIs(raised.exception, original)
            factory.assert_called_once_with(Path("server"))
            self.assertEqual([st["id"] for st in original.tolerance_record["steps"]], ["first", "second"])
            self.assertEqual(original.tolerance_record["stderr"], "session logs")
            self.assertEqual(session.send.call_args_list[0].args[1]["contentChanges"], [{"text": "fixed"}])
            session.close.assert_called_once_with()

    def test_watched_writes_and_unknown_actions(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            session = mock.Mock()
            with mock.patch.object(tolerance.bench, "Session", return_value=session):
                run = tolerance.Run(Path("server"), {"base": Path(temp)}, {})
            for event in (1, 2):
                run.apply({**tolerance.sessions.action("write", "New.v3", str(event)), "event_type": event})
            self.assertEqual([c.args[1]["changes"][0]["type"] for c in session.send.call_args_list], [1, 2])
            self.assertEqual((Path(temp) / "New.v3").read_text(), "2")
            with self.assertRaises(ValueError):
                run.apply({"op": "typo", "path": "New.v3"})


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
