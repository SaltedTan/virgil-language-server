#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Structural scoring must reject plausible stale protocol answers."""
import copy
import contextlib
import importlib.util
import itertools
import io
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
spec = importlib.util.spec_from_file_location("tolerance_check", ROOT / "scripts/check-tolerance.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
CONTROLS = json.loads(Path(__file__).with_name("tolerance-negative-oracles.json").read_text())["cases"]

SCORECARD_ANSWERS = {
    "0:definition": {"uri": "project:///Observer.v3", "range": {}},
    "0:hover": {"contents": "bound", "range": {}},
    "0:references": [{"uri": "project:///Observer.v3", "range": {}}],
    "diagnostics": [{"uri": "project:///Observer.v3", "diagnostic": {"code": "TypeError"}}],
}
# What a server that lost every answer returns.
MISSING_ANSWERS = {"0:definition": None, "0:hover": None, "0:references": [], "diagnostics": []}
# A predecessor's binding, which every injection must reject.
STALE_ANSWERS = {**SCORECARD_ANSWERS, "0:definition": {"uri": "project:///Stale.v3", "range": {}}}
PIDS = itertools.count(1000)
# An oracle whose hover shows its target's documentation (#357), and that hover
# without it, as the server answers for a target in a substituted file.
DOCUMENTED = {**SCORECARD_ANSWERS, "0:definition": {"uri": "project:///A.v3", "range": {}},
              "0:hover": {"contents": {"kind": "plaintext", "value": "class C\n\nThe class.\n\nMore."},
                          "range": {}}}
UNDOCUMENTED = {**DOCUMENTED, "0:hover": {"contents": {"kind": "plaintext", "value": "class C"}, "range": {}}}


def scored(answers, expected=SCORECARD_ANSWERS, allow_absent=False):
    """The verdicts bench-tolerance.py records for {answers}."""
    return {key: tolerance.score(key.split(":")[-1], value, expected[key], allow_absent)
            for key, value in answers.items()}


def scorecard_state(state_id, mode, answers=None, expectation=(), oracle_id=0,
                    expected=SCORECARD_ANSWERS):
    answers = copy.deepcopy(SCORECARD_ANSWERS if answers is None else answers)
    return {"id": state_id, "suite": "repair", "edit_kind": "body", "mode": mode,
            "oracle_id": oracle_id, "answers": answers, "scores": scored(answers, expected),
            "samples": [{"feature": "definition", "latency_ms": 1}],
            "state_latency_ms": 1.5, "worker_replacements": 0,
            "expectation_mismatches": list(expectation),
            "configuration": {"current": {"version": 1, "projects": [
                {"name": "tolerance", "sources": ["A.v3"]}]}},
            "texts": {"current": {"A.v3": {"bytes": 1}}},
            "changed": ["A.v3"], "server_pid": next(PIDS),
            # Warm changes the edited overlay once; cold opens it once.
            "snapshot": {"projectConfiguration": {"current": True,
                                                    "uri": "file:///project/.virgil-lsp.json"},
                         "documentVersions": [
                {"uri": "file:///project/A.v3", "version": 2 if mode == "warm" else 1,
                 "current": True}]}}


def scorecard_oracle(oracle_id, answers=None, stale=None):
    oracle = {"id": oracle_id, "answers": dict(SCORECARD_ANSWERS) if answers is None else answers,
              "queries": [{}], "configuration": {}, "diagnostic_paths": []}
    if stale is not None:
        oracle["stale_control"] = stale
    return oracle


def scorecard_capture(records, *, command=None, smoke=None, oracles=None, sessions=(),
                      session_oracles=()):
    if oracles is None:
        state_ids = []
        for row in records:
            if row["id"] not in state_ids:
                state_ids.append(row["id"])
        for row in records:
            row["oracle_id"] = state_ids.index(row["id"])
        oracles = [scorecard_oracle(index) for index in range(len(state_ids))]
    capture = {
        "schema": 1, "suite": "tolerance", "started_utc": "2026-10-29T00:00:00+00:00",
        "command": command or ["python3", "scripts/bench-tolerance.py"],
        "capture": {"smoke": False, "suite": None, "only": None, "full": True}
        if smoke is None else smoke,
        "server_revision": "revision", "host": {}, "corpus": {},
        "corpus_sha256": "0" * 64, "driver_sha256": {}, "builds": [{}],
        "input_sha256": {}, "wall_seconds": 1,
        "oracles": oracles,
        "states": records, "sessions": list(sessions), "session_oracles": list(session_oracles),
        "summary": {}, "session_summary": {},
    }
    return capture


def scorecard_inventory(*state_ids, heldout=(), negative=(), sessions=(), oracles=None):
    rows = [{"id": state_id, "suite": "repair", "edit_kind": "body", "queries": 1}
            for state_id in state_ids]
    for state_id, group, kind in heldout:
        rows.append({"id": state_id, "suite": "heldout", "edit_kind": "body", "queries": 1,
                     "group": group, "heldout_kind": kind})
    for state_id in negative:
        rows.append({"id": state_id, "suite": "negative", "edit_kind": "body", "queries": 1,
                     "allow_absent": True})
    for index, row in enumerate(rows):
        # One intended-text group per state unless a test names a sharing.
        row["oracle"] = index if oracles is None else oracles.get(row["id"], index)
    return {"schema": 1, "states": rows, "sessions": list(sessions)}


def heldout_state(state_id, mode, answers=None):
    row = scorecard_state(state_id, mode, answers)
    row["suite"] = "heldout"
    return row


def session_step(step_id, mode, *, oracle=False, answers=None, actions=True):
    answers = copy.deepcopy(SCORECARD_ANSWERS if answers is None else answers)
    record = {"id": step_id, "suite": "session", "mode": mode, "edit_kind": step_id,
              "answers": answers,
              "samples": [{"feature": "hover", "latency_ms": 1}],
              "queries": [{"path": "Observer.v3", "position": {"line": 0, "character": 0}}],
              "snapshot": {"projectConfiguration": {"current": True,
                                                      "uri": "file:///project/.virgil-lsp.json"},
                           "documentVersions": [{"uri": "file:///project/Observer.v3",
                                                 "current": True, "version": 1}]},
              "state_latency_ms": 1.5,
              "measured_probes": []}
    if actions:
        record["actions"] = [{"op": "change", "path": "A.v3", "text": "broken",
                              "intended": "fixed"}]
    if oracle:
        record["stale_control"] = stale_control()
    else:
        record["scores"] = scored(answers)
        record["substitution_kinds"] = {"A.v3": "repair"}
        record["oracle_step"] = 0
    return record


def stale_control(answers=None, scores=None, allow_absent=True):
    """An injection and its recorded verdicts; negative controls allow absence."""
    answers = copy.deepcopy(STALE_ANSWERS if answers is None else answers)
    return {"answers": answers, "scores": scored(answers, allow_absent=allow_absent) if scores is None else scores}


def scorecard_sessions(mode):
    """One measured session record and its intended-text oracle, both modes."""
    step = session_step("one", mode)
    oracle_step = session_step("one", "warm", oracle=True)
    return ({"id": "s", "suite": "session", "mode": mode, "steps": [step],
             "server_pid": next(PIDS), "worker_replacements": 0},
            {"id": "s", "mode": "oracle", "steps": [oracle_step], "server_pid": next(PIDS)})


def negative_capture(stale):
    inventory = scorecard_inventory(negative=["control"])
    records = [scorecard_state("control", "warm"), scorecard_state("control", "cold")]
    for row in records:
        row["suite"] = "negative"
    return scorecard_capture(records, oracles=[scorecard_oracle(0, stale=stale)]), inventory


def session_capture():
    inventory = scorecard_inventory(sessions=[{"id": "s", "steps": [
        {"id": "one", "queries": 1, "stale": True}]}])
    measured, oracle = scorecard_sessions("warm")
    cold, _ = scorecard_sessions("cold")
    oracle["steps"][0]["stale_control"] = stale_control(allow_absent=False)
    return scorecard_capture([], sessions=[measured, cold], session_oracles=[oracle]), inventory


def met_floors(floors):
    """Measurements that meet every floor exactly, for a focused floor test."""
    return {"modes": {mode: {section: {name: [minimum, expected]
                                       for name, (minimum, expected) in requirements.items()}
                             for section, requirements in floors.items()}
                      for mode in checker.MODES}}



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
                                        "brace-closer-missing-return-limit-private",
                                        "brace-opener-holder-comment-name", "brace-opener-holder-comment-def",
                                        "brace-opener-holder-split-def", "brace-opener-holder-split-name",
                                        "brace-opener-holder-private-comment", "brace-closer-match-case",
                                        "brace-closer-lambda-if", "brace-closer-match-case-missing-return-limit",
                                        "brace-closer-match-case-then-blank", "brace-closer-equivalent"})
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

    def test_substituted_hover_target_expects_no_documentation(self):
        def snapshot(substituted):
            entry = {"uri": "file:///project/A.v3", "current": True, "version": 1}
            if substituted:
                entry["substitution"] = {"effectiveEnd": 10, "currentEnd": 10}
            return {"projectConfiguration": {"current": True, "uri": "file:///project/.virgil-lsp.json"},
                    "documentVersions": [entry, {"uri": "file:///project/Observer.v3", "current": True,
                                                 "version": 1, "substitution": {}}]}
        queries = [{"path": "Observer.v3"}]
        # The target's file is substituted: only the hover without documentation is correct.
        expected = tolerance.expected_answers(DOCUMENTED, snapshot(True), queries)
        self.assertEqual(expected["0:hover"], UNDOCUMENTED["0:hover"])
        self.assertEqual(expected["0:definition"], DOCUMENTED["0:definition"])
        self.assertTrue(tolerance.score("hover", UNDOCUMENTED["0:hover"], expected["0:hover"])["correct"])
        self.assertTrue(tolerance.score("hover", DOCUMENTED["0:hover"], expected["0:hover"])["wrong"])
        # Only the queried file is substituted: the documentation is still expected.
        expected = tolerance.expected_answers(DOCUMENTED, snapshot(False), queries)
        self.assertEqual(expected, DOCUMENTED)
        self.assertTrue(tolerance.score("hover", UNDOCUMENTED["0:hover"], expected["0:hover"])["wrong"])
        # Without a definition, the target is the queried file.
        nameless = {**DOCUMENTED, "0:definition": None}
        self.assertEqual(tolerance.expected_answers(nameless, snapshot(False), [{"path": "Observer.v3"}])["0:hover"],
                         UNDOCUMENTED["0:hover"])
        self.assertEqual(tolerance.expected_answers(nameless, snapshot(False), [{"path": "A.v3"}]), nameless)

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


class Scorecard(unittest.TestCase):
    def test_refuses_missing_state_and_missing_mode(self):
        inventory = scorecard_inventory("first", "second", "third")
        missing_mode = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm")])
        failures = checker.validate(missing_mode, inventory)
        self.assertTrue(any("state second has modes" in failure for failure in failures), failures)
        missing_state = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm"), scorecard_state("second", "cold")])
        failures = checker.validate(missing_state, inventory)
        self.assertTrue(any("state third has modes []" in failure for failure in failures), failures)

    def test_refuses_duplicate_replacing_a_missing_state(self):
        inventory = scorecard_inventory("first", "second")
        capture = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm"), scorecard_state("second", "warm")])
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("duplicates state/mode second/" in failure for failure in failures),
                        failures)
        self.assertTrue(any("state second has modes" in failure for failure in failures), failures)

    def test_refuses_false_aggregate_gain_hiding_a_subgroup_loss(self):
        inventory = scorecard_inventory("first", "second")
        capture = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("second", "warm"),
            scorecard_state("first", "cold"),
            scorecard_state("second", "cold", MISSING_ANSWERS)])
        report = checker.evaluate(capture, inventory,
                                  {"primary": {"all_features": (2, 2)}})
        self.assertFalse(report["ok"])
        self.assertTrue(any("cold: primary.all_features is 1/2" in failure
                            for failure in report["failures"]), report["failures"])
        self.assertEqual(report["metrics"]["modes"]["warm"]["primary"]["all_features"], [2, 2])
        self.assertEqual(report["metrics"]["modes"]["cold"]["primary"]["all_features"], [1, 2])

    def test_refuses_a_wrong_answer(self):
        inventory = scorecard_inventory("first")
        answers = {**SCORECARD_ANSWERS, "0:hover": {"contents": "stale", "range": {}}}
        capture = scorecard_capture([scorecard_state("first", "warm", answers),
                                     scorecard_state("first", "cold", answers)])
        report = checker.evaluate(capture, inventory, {})
        self.assertFalse(report["ok"])
        self.assertTrue(any("wrong answers" in failure for failure in report["failures"]),
                        report["failures"])

    def test_rescores_a_substituted_hover_target_without_documentation(self):
        for substituted in (True, False):
            with self.subTest(substituted=substituted):
                inventory = scorecard_inventory("first")
                records = []
                for mode in ("warm", "cold"):
                    row = scorecard_state("first", mode, UNDOCUMENTED)
                    if substituted:
                        row["snapshot"]["documentVersions"][0]["substitution"] = {}
                    expected = tolerance.expected_answers(DOCUMENTED, row["snapshot"], [{}])
                    row["scores"] = scored(UNDOCUMENTED, expected)
                    records.append(row)
                capture = scorecard_capture(records, oracles=[scorecard_oracle(0, dict(DOCUMENTED))])
                floors = {"primary": {"all_features": (1, 1)},
                          "exact": {feature: (1, 1) for feature in checker.FEATURES}}
                report = checker.evaluate(capture, inventory, floors)
                if substituted:
                    self.assertTrue(report["ok"], report["failures"])
                else:
                    self.assertFalse(report["ok"])
                    self.assertTrue(any("wrong answers" in failure for failure in report["failures"]),
                                    report["failures"])
                # Verdicts written against the plain oracle don't survive rescoring.
                for row in records:
                    row["scores"] = scored(UNDOCUMENTED, DOCUMENTED)
                failures = checker.validate(capture, inventory)
                self.assertEqual(substituted, any("0:hover: recorded verdict" in f for f in failures), failures)

    def test_refuses_an_unmet_exclusion_expectation(self):
        inventory = scorecard_inventory("first")
        mismatch = "A.v3: 1 excluded bodies, expected 0"
        capture = scorecard_capture([
            scorecard_state("first", "warm", expectation=[mismatch]),
            scorecard_state("first", "cold")])
        report = checker.evaluate(capture, inventory, {})
        self.assertFalse(report["ok"])
        self.assertTrue(any("expectation mismatches" in failure for failure in report["failures"]),
                        report["failures"])

    def test_refuses_smoke_and_filtered_captures(self):
        inventory = scorecard_inventory("first")
        records = [scorecard_state("first", "warm"), scorecard_state("first", "cold")]
        smoke = scorecard_capture(records, smoke={"smoke": True, "suite": None,
                                                  "only": None, "full": False})
        self.assertTrue(any("not a full capture" in failure
                            for failure in checker.validate(smoke, inventory)))
        for flag in ("--smoke", "--suite", "--only"):
            with self.subTest(flag=flag):
                old = scorecard_capture(records)
                del old["capture"]
                old["command"] = ["python3", "scripts/bench-tolerance.py", flag, "value"]
                self.assertTrue(any("not a full capture" in failure
                                    for failure in checker.validate(old, inventory)))

    def test_accepts_a_complete_capture(self):
        inventory = scorecard_inventory("first", "second")
        capture = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm"), scorecard_state("second", "cold")])
        floors = {"primary": {"all_features": (2, 2)},
                  "exact": {feature: (2, 2) for feature in checker.FEATURES}}
        report = checker.evaluate(capture, inventory, floors)
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["metrics"]["counts"]["state_ids"], 2)
        self.assertEqual(report["metrics"]["counts"]["state_mode_records"], 4)
        self.assertEqual(report["metrics"]["modes"]["warm"]["baselines"]["primary_all_exact"], [2, 2])
        self.assertIn("Tolerance release scorecard: PASS",
                      checker.format_scorecard(report, capture))

    def test_refuses_an_oracle_reused_by_two_intended_texts(self):
        inventory = scorecard_inventory("first", "second")
        capture = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm"), scorecard_state("second", "cold")])
        # Both modes of the second state reuse the first state's oracle, the
        # orphaned oracle is removed, and its successors would be renumbered.
        for row in capture["states"]:
            if row["id"] == "second":
                row["oracle_id"] = 0
        capture["oracles"] = capture["oracles"][:1]
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("oracle 0 stands in for intended text groups [0, 1]" in failure
                            for failure in failures), failures)
        self.assertTrue(any("1 oracles, expected 2" in failure for failure in failures), failures)

    def test_refuses_two_oracles_for_one_intended_text(self):
        inventory = scorecard_inventory("first", "second", oracles={"second": 0})
        capture = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm"), scorecard_state("second", "cold")])
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("share intended text group 0 reference oracles [0, 1]" in failure
                            for failure in failures), failures)

    def test_accepts_one_oracle_for_states_with_identical_text(self):
        inventory = scorecard_inventory("first", "second", oracles={"second": 0})
        capture = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm"), scorecard_state("second", "cold")],
            oracles=[scorecard_oracle(0)])
        for row in capture["states"]:
            row["oracle_id"] = 0
        self.assertEqual(checker.validate(capture, inventory), [])

    def test_refuses_a_warm_oracle_swap(self):
        inventory = scorecard_inventory("first", "second")
        capture = scorecard_capture([
            scorecard_state("first", "warm"), scorecard_state("first", "cold"),
            scorecard_state("second", "warm"), scorecard_state("second", "cold")])
        capture["states"][0]["oracle_id"], capture["states"][2]["oracle_id"] = (
            capture["states"][2]["oracle_id"], capture["states"][0]["oracle_id"])
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("state first references oracles" in failure for failure in failures), failures)

    def test_refuses_an_oracle_without_the_states_answer_keys(self):
        inventory = scorecard_inventory("first")
        capture = scorecard_capture([scorecard_state("first", "warm"),
                                     scorecard_state("first", "cold")])
        capture["oracles"][0]["answers"] = {"bogus": None}
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("answer keys differ" in failure for failure in failures), failures)

    def test_refuses_a_malformed_injection(self):
        # The injected answers are rejected; only their recorded verdicts are malformed.
        exact = scored(SCORECARD_ANSWERS)
        impossible = {key: {"available": True, "correct": True, "partial": False, "missing": False,
                            "wrong": True, "matches_oracle": True} for key in STALE_ANSWERS}
        for name, scores in (("unknown key", {"bogus": {"wrong": "false"}}),
                             ("wrong type", {**exact, "0:definition": {"wrong": "false"}}),
                             ("exact", exact), ("impossible", impossible)):
            with self.subTest(scores=name):
                capture, inventory = negative_capture(stale_control(scores=scores))
                report = checker.evaluate(capture, inventory, {})
                self.assertFalse(report["ok"])
                self.assertTrue(any("injection" in failure for failure in report["failures"]),
                                report["failures"])
        capture, inventory = negative_capture(stale_control())
        self.assertEqual(checker.validate(capture, inventory), [])
        self.assertEqual(checker.aggregate(capture, inventory)["counts"]["negative_injections"], 1)

    def test_refuses_an_injection_that_its_oracle_accepts(self):
        # Impossible verdicts on answers that equal the oracle's, as recorded
        # for a negative control and a session control; and the honest verdicts.
        impossible = {key: {"available": True, "correct": True, "partial": False, "missing": False,
                            "wrong": True, "matches_oracle": True} for key in SCORECARD_ANSWERS}
        for scores in (impossible, None):
            with self.subTest(scores="impossible" if scores else "honest"):
                capture, inventory = negative_capture(stale_control(SCORECARD_ANSWERS, scores))
                failures = checker.validate(capture, inventory)
                self.assertTrue(any("control did not reject its injection" in failure
                                    for failure in failures), failures)
                capture, inventory = session_capture()
                self.assertEqual(checker.validate(capture, inventory), [])
                capture["session_oracles"][0]["steps"][0]["stale_control"] = stale_control(
                    SCORECARD_ANSWERS, scores, allow_absent=False)
                failures = checker.validate(capture, inventory)
                self.assertTrue(any("did not reject its stale predecessor" in failure
                                    for failure in failures), failures)

    def test_refuses_oracles_swapped_between_two_states(self):
        other = {**SCORECARD_ANSWERS, "0:hover": {"contents": "other", "range": {}}}
        inventory = scorecard_inventory("first", "second")
        capture = scorecard_capture([
            scorecard_state("first", "warm", oracle_id=0), scorecard_state("first", "cold", oracle_id=0),
            scorecard_state("second", "warm", other, oracle_id=1, expected=other),
            scorecard_state("second", "cold", other, oracle_id=1, expected=other)],
            oracles=[scorecard_oracle(0), scorecard_oracle(1, other)])
        self.assertEqual(checker.validate(capture, inventory), [])
        # Each group still has one oracle and each oracle one group.
        for row in capture["states"]:
            row["oracle_id"] = 1 - row["oracle_id"]
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("(first warm) 0:hover: recorded verdict" in failure
                            for failure in failures), failures)

    def test_refuses_verdicts_that_null_answers_do_not_support(self):
        inventory = scorecard_inventory("first")
        capture = scorecard_capture([scorecard_state("first", "warm"),
                                     scorecard_state("first", "cold")])
        for key in capture["states"][0]["answers"]:
            capture["states"][0]["answers"][key] = None
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("(first warm) 0:definition: recorded verdict" in failure
                            and "differs from rescoring" in failure for failure in failures), failures)

    def test_refuses_a_session_verdict_that_its_answer_does_not_support(self):
        capture, inventory = session_capture()
        capture["sessions"][0]["steps"][0]["answers"]["0:hover"] = {"contents": "stale", "range": {}}
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("step one 0:hover: recorded verdict" in failure for failure in failures),
                        failures)

    def test_refuses_one_record_filed_under_both_modes(self):
        inventory = scorecard_inventory("first")
        records = [scorecard_state("first", "warm"), scorecard_state("first", "cold")]
        self.assertEqual(checker.validate(scorecard_capture(copy.deepcopy(records)), inventory), [])
        cases = (("process", "one server process", lambda cold, warm: cold.update(server_pid=warm["server_pid"])),
                 ("versions", "past version 1", lambda cold, warm: cold.update(snapshot=warm["snapshot"])),
                 ("warm versions", "not at version 2", lambda cold, warm: warm.update(snapshot=cold["snapshot"])))
        for name, message, mutate in cases:
            with self.subTest(evidence=name):
                capture = scorecard_capture(copy.deepcopy(records))
                warm, cold = capture["states"]
                mutate(cold, warm)
                failures = checker.validate(capture, inventory)
                self.assertTrue(any(message in failure for failure in failures), failures)
        capture, inventory = session_capture()
        capture["sessions"][1]["server_pid"] = capture["sessions"][0]["server_pid"]
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("records share a server process" in failure for failure in failures),
                        failures)

    def test_refuses_snapshot_evidence_that_the_driver_would_reject(self):
        inventory = scorecard_inventory("first")
        records = [scorecard_state("first", "warm"), scorecard_state("first", "cold")]
        self.assertEqual(checker.validate(scorecard_capture(copy.deepcopy(records)), inventory), [])
        two = {"current": {"version": 1, "projects": [{"name": "tolerance", "sources": ["A.v3", "B.v3"]}]}}
        cases = (
            ("no documents", "one document for each configured source",
             lambda row: row["snapshot"].update(documentVersions=[])),
            ("stale document", "is not current",
             lambda row: row["snapshot"]["documentVersions"][0].update(current=False)),
            ("other source", "is not the configured source",
             lambda row: row["snapshot"]["documentVersions"][0].update(uri="file:///project/Other.v3")),
            ("no configuration", "no current configured sources",
             lambda row: row.update(configuration={})),
            ("two directories", "not in one project directory",
             lambda row: (row.update(configuration=two), row["snapshot"]["documentVersions"].append(
                 {"uri": "file:///elsewhere/B.v3", "version": 1, "current": True}))))
        for name, message, mutate in cases:
            with self.subTest(snapshot=name):
                capture = scorecard_capture(copy.deepcopy(records))
                mutate(capture["states"][1])
                failures = checker.validate(capture, inventory)
                self.assertTrue(any(message in failure for failure in failures), failures)

    def test_refuses_missing_or_invalid_snapshot_versions(self):
        inventory = scorecard_inventory("first")
        capture = scorecard_capture([scorecard_state("first", "warm"),
                                     scorecard_state("first", "cold")])
        sessions, session_inventory = session_capture()
        capture.update({key: sessions[key] for key in ("sessions", "session_oracles")})
        inventory["sessions"] = session_inventory["sessions"]
        paths = [("states", 0), ("states", 1), ("session_oracles", 0, "steps", 0),
                 ("sessions", 0, "steps", 0), ("sessions", 1, "steps", 0)]
        self.assertEqual(checker.validate(capture, inventory), [])
        for path in paths:
            for value in ("missing", True, False, 1.0, "1", [], {}):
                with self.subTest(path=path, version=value):
                    broken = copy.deepcopy(capture)
                    row = broken
                    for part in path:
                        row = row[part]
                    document = row["snapshot"]["documentVersions"][0]
                    if value == "missing":
                        document.pop("version")
                        message = "is missing version"
                    else:
                        document["version"] = value
                        message = "version is not null or a non-boolean integer"
                    failures = checker.validate(broken, inventory)
                    self.assertTrue(any(document["uri"] in failure and message in failure
                                        for failure in failures), failures)
                    self.assertFalse(checker.evaluate(broken, inventory, {})["ok"])

    def test_accepts_explicit_null_snapshot_versions(self):
        inventory = scorecard_inventory("closed-file-on-disk/0")
        capture = scorecard_capture([scorecard_state("closed-file-on-disk/0", "warm"),
                                     scorecard_state("closed-file-on-disk/0", "cold")])
        sessions, session_inventory = session_capture()
        capture.update({key: sessions[key] for key in ("sessions", "session_oracles")})
        inventory["sessions"] = session_inventory["sessions"]
        rows = (capture["states"] + [session["steps"][0] for session in
                                    capture["sessions"] + capture["session_oracles"]])
        for row in rows:
            row["snapshot"]["documentVersions"][0]["version"] = None
        self.assertEqual(checker.validate(capture, inventory), [])

    def test_requires_exact_state_edit_versions(self):
        for state_id, disk in (("first", False), ("closed-file-on-disk/0", True)):
            inventory = scorecard_inventory(state_id)
            capture = scorecard_capture([scorecard_state(state_id, "warm"),
                                         scorecard_state(state_id, "cold")])
            if disk:
                for row in capture["states"]:
                    row["snapshot"]["documentVersions"][0]["version"] = None
            self.assertEqual(checker.validate(capture, inventory), [])
            for index in range(2):
                for version in (None, 0, 1, 2, 3):
                    with self.subTest(disk=disk, mode=capture["states"][index]["mode"], version=version):
                        broken = copy.deepcopy(capture)
                        broken["states"][index]["snapshot"]["documentVersions"][0]["version"] = version
                        failures = checker.validate(broken, inventory)
                        expected = None if disk else (2 if index == 0 else 1)
                        if version == expected:
                            self.assertEqual(failures, [])
                        else:
                            self.assertTrue(any("edited documents not at version" in failure
                                                for failure in failures), failures)

    def test_requires_changed_paths_and_matching_mode_lists(self):
        inventory = scorecard_inventory("first")
        capture = scorecard_capture([scorecard_state("first", "warm"),
                                     scorecard_state("first", "cold")])
        for row in capture["states"]:
            row["configuration"]["current"]["projects"][0]["sources"].append("B.v3")
            row["texts"]["current"]["B.v3"] = {"bytes": 1}
            row["changed"].append("B.v3")
            row["snapshot"]["documentVersions"].append(
                {"uri": "file:///project/B.v3", "current": True,
                 "version": 2 if row["mode"] == "warm" else 1})
        self.assertEqual(checker.validate(capture, inventory), [])
        for index in range(2):
            for changed in ([], ["A.v3", "A.v3"], ["Unknown.v3"], [1], [{}]):
                with self.subTest(index=index, changed=changed):
                    broken = copy.deepcopy(capture)
                    broken["states"][index]["changed"] = changed
                    failures = checker.validate(broken, inventory)
                    self.assertTrue(any("changed must be nonempty distinct strings in texts.current"
                                        in failure for failure in failures), failures)
        for changed in (["A.v3"], ["B.v3", "A.v3"]):
            with self.subTest(changed=changed):
                broken = copy.deepcopy(capture)
                broken["states"][1]["changed"] = changed
                failures = checker.validate(broken, inventory)
                self.assertTrue(any("different changed lists" in failure for failure in failures), failures)
        for index in range(2):
            broken = copy.deepcopy(capture)
            broken["states"][index]["texts"]["current"].pop("A.v3")
            failures = checker.validate(broken, inventory)
            self.assertTrue(any("changed must be nonempty distinct strings in texts.current"
                                in failure for failure in failures), failures)
        for row in capture["states"]:
            row["texts"]["current"]["Missing.v3"] = {"bytes": 1}
            row["changed"].append("Missing.v3")
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("edited documents not at version" in failure and "Missing.v3" in failure
                            for failure in failures), failures)

    def test_requires_other_open_state_documents_at_version_one(self):
        inventory = scorecard_inventory("first")
        capture = scorecard_capture([scorecard_state("first", "warm"),
                                     scorecard_state("first", "cold")])
        for row in capture["states"]:
            row["configuration"]["current"]["projects"][0]["sources"].append("Observer.v3")
            row["snapshot"]["documentVersions"].append(
                {"uri": "file:///project/Observer.v3", "current": True, "version": 1})
        self.assertEqual(checker.validate(capture, inventory), [])
        for index in range(2):
            for version in (None, 0, 2):
                with self.subTest(index=index, version=version):
                    broken = copy.deepcopy(capture)
                    broken["states"][index]["snapshot"]["documentVersions"][1]["version"] = version
                    failures = checker.validate(broken, inventory)
                    if version is None:
                        self.assertEqual(failures, [])
                    else:
                        self.assertTrue(any("version 1" in failure for failure in failures), failures)

    def test_refuses_stale_state_configuration(self):
        inventory = scorecard_inventory("first")
        capture = scorecard_capture([scorecard_state("first", "warm"),
                                     scorecard_state("first", "cold")])
        self.assertEqual(checker.validate(capture, inventory), [])
        for mode in range(2):
            for current in (False, 1, None):
                with self.subTest(mode=mode, current=current):
                    broken = copy.deepcopy(capture)
                    broken["states"][mode]["snapshot"]["projectConfiguration"]["current"] = current
                    failures = checker.validate(broken, inventory)
                    self.assertTrue(any("projectConfiguration is not current" in failure
                                        for failure in failures), failures)

    def test_refuses_invalid_session_snapshots(self):
        capture, inventory = session_capture()
        self.assertEqual(checker.validate(capture, inventory), [])
        cases = (
            ("empty", {}, "projectConfiguration is not current"),
            ("configuration", {"projectConfiguration": {"current": False}},
             "projectConfiguration is not current"),
            ("documents", {"documentVersions": []}, "no document versions"),
            ("document type", {"documentVersions": [None]}, "no string uri"),
            ("uri", {"documentVersions": [{"uri": 1, "current": True}]}, "no string uri"),
            ("current", {"documentVersions": [{"uri": "file:///project/Observer.v3",
                                               "current": 1}]}, "is not current"),
            ("query", {"documentVersions": [{"uri": "file:///project/Other.v3",
                                             "current": True}]}, "lacks queried document"),
            ("directory", {"documentVersions": [{"uri": "file:///elsewhere/Observer.v3",
                                                 "current": True}]}, "lacks queried document"))
        for collection, index in (("session_oracles", 0), ("sessions", 0), ("sessions", 1)):
            for name, replacement, message in cases:
                with self.subTest(collection=collection, index=index, snapshot=name):
                    broken = copy.deepcopy(capture)
                    snapshot = broken[collection][index]["steps"][0]["snapshot"]
                    if name == "empty":
                        snapshot.clear()
                    snapshot.update(replacement)
                    failures = checker.validate(broken, inventory)
                    self.assertTrue(any(message in failure for failure in failures), failures)

    def test_refuses_invalid_probe_and_cache_measurements(self):
        capture, inventory = negative_capture(stale_control())
        sessions, session_inventory = session_capture()
        capture.update({key: sessions[key] for key in ("sessions", "session_oracles")})
        inventory["sessions"] = session_inventory["sessions"]
        capture["oracles"][0]["process"] = {}
        capture["oracles"][0]["stale_control"]["process"] = {}
        capture["session_oracles"][0]["steps"][0]["stale_control"]["process"] = {}
        paths = [("states", 0), ("states", 1), ("oracles", 0, "process"),
                 ("oracles", 0, "stale_control", "process"),
                 ("session_oracles", 0, "steps", 0, "stale_control", "process")]
        for collection, index in (("sessions", 0), ("sessions", 1), ("session_oracles", 0)):
            paths.extend(((collection, index), (collection, index, "steps", 0)))
        self.assertEqual(checker.validate(capture, inventory), [])
        for path in paths:
            for field in ("probes", "measured_probes", "worker_replacements", "cache_charge"):
                for value in (0, 1.5, -1, float("nan"), float("inf"), True, "1"):
                    for probe_key in (("value", "analysis_id") if field.endswith("probes") else (None,)):
                        with self.subTest(path=path, field=field, value=value, probe_key=probe_key):
                            broken = copy.deepcopy(capture)
                            row = broken
                            for part in path:
                                row = row[part]
                            if probe_key:
                                row[field] = [{"value": 1, "analysis_id": 0, probe_key: value}]
                            elif field == "cache_charge":
                                row[field] = {"first": {"bytes": value}, "second": [1]}
                            else:
                                row[field] = value
                            failures = checker.validate(broken, inventory)
                            if type(value) in (int, float) and value in (0, 1.5):
                                self.assertEqual(failures, [])
                            else:
                                self.assertTrue(any(field in failure and "finite nonnegative" in failure
                                                    for failure in failures), failures)

    def test_cli_refuses_an_alternate_inventory(self):
        with mock.patch.object(sys, "argv", ["check-tolerance.py", "capture.json",
                                            "--inventory", "alternate.json"]):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    checker.main()
        self.assertEqual(result.exception.code, 2)

    def test_refuses_an_injection_without_every_answer(self):
        # Only the wrong definition is kept; the oracle has every answer.
        truncated = {"0:definition": STALE_ANSWERS["0:definition"]}
        capture, inventory = negative_capture(stale_control(truncated))
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("are not the oracle's" in failure for failure in failures), failures)

    def test_refuses_injection_verdicts_written_as_numbers(self):
        # JSON 0 and 1 compare equal to false and true in Python.
        stale = stale_control()
        stale["scores"] = {key: {field: int(value) for field, value in verdict.items()}
                           for key, verdict in stale["scores"].items()}
        capture, inventory = negative_capture(stale)
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("is not a boolean" in failure for failure in failures), failures)

    def test_refuses_a_measurement_that_is_not_a_finite_nonnegative_number(self):
        for value in (-1, float("nan"), float("inf"), True, "1"):
            with self.subTest(value=value):
                inventory = scorecard_inventory("first")
                capture = scorecard_capture([scorecard_state("first", "warm"),
                                             scorecard_state("first", "cold")])
                capture["states"][0]["samples"][0]["latency_ms"] = value
                failures = checker.validate(capture, inventory)
                self.assertTrue(any("samples[0].latency_ms is not a finite nonnegative number"
                                    in failure for failure in failures), failures)
                capture, inventory = session_capture()
                capture["sessions"][0]["steps"][0]["state_latency_ms"] = value
                failures = checker.validate(capture, inventory)
                self.assertTrue(any("state_latency_ms is not a finite nonnegative number"
                                    in failure for failure in failures), failures)

    def test_refuses_an_incomplete_session_step(self):
        inventory = scorecard_inventory(sessions=[{"id": "s", "steps": [{"id": "one", "queries": 1}]}])
        measured, oracle = scorecard_sessions("warm")
        cold, _ = scorecard_sessions("cold")
        capture = scorecard_capture([], sessions=[measured, cold], session_oracles=[oracle])
        self.assertEqual(checker.validate(capture, inventory), [])
        for field in ("snapshot", "samples", "queries", "actions"):
            with self.subTest(field=field):
                broken = copy.deepcopy(capture)
                broken["sessions"][0]["steps"][0].pop(field)
                failures = checker.validate(broken, inventory)
                self.assertTrue(any("step one is missing " + field in failure for failure in failures),
                                failures)
        broken = copy.deepcopy(capture)
        broken["session_oracles"][0]["steps"][0]["snapshot"] = []
        failures = checker.validate(broken, inventory)
        self.assertTrue(any("step one snapshot is not an object" in failure for failure in failures),
                        failures)

    def test_refuses_a_command_that_contradicts_its_metadata(self):
        inventory = scorecard_inventory("first")
        records = [scorecard_state("first", "warm"), scorecard_state("first", "cold")]
        capture = scorecard_capture(records, command=["python3", "scripts/bench-tolerance.py", "--smoke"])
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("not a full capture: command has --smoke" in failure
                            for failure in failures), failures)
        capture = scorecard_capture(records, smoke={"smoke": True, "suite": None,
                                                    "only": None, "full": False})
        failures = checker.validate(capture, inventory)
        self.assertTrue(any("metadata records filters ['--smoke']" in failure
                            for failure in failures), failures)

    def test_refuses_a_gain_in_one_mode_hiding_a_loss_in_the_other(self):
        inventory = scorecard_inventory("a", "b", "c", "d")
        records = []
        for state_id in ("a", "b", "c", "d"):
            for mode in ("warm", "cold"):
                missing = mode == "cold" and state_id in ("c", "d")
                records.append(scorecard_state(state_id, mode, MISSING_ANSWERS if missing else None))
        report = checker.evaluate(scorecard_capture(records), inventory,
                                  {"primary": {"all_features": (3, 4)}})
        self.assertFalse(report["ok"])
        self.assertEqual(report["metrics"]["modes"]["warm"]["primary"]["all_features"], [4, 4])
        self.assertEqual(report["metrics"]["modes"]["cold"]["primary"]["all_features"], [2, 4])
        # Three before, three after: warm gained what cold lost, so a combined
        # floor would pass and only the per-mode floor can see the regression.
        self.assertEqual(4 + 2, 3 + 3)
        self.assertTrue(any("cold: primary.all_features is 2/4" in failure
                            for failure in report["failures"]), report["failures"])

    def test_refuses_a_gain_in_one_heldout_subgroup_hiding_a_loss_in_another(self):
        inventory = scorecard_inventory(heldout=[
            ("body/x", "body-quarantine", "body"), ("body/y", "body-quarantine", "body"),
            ("if/z", "brace-317", "if-opener"), ("if/w", "brace-317", "if-opener")])
        records = []
        for state_id in ("body/x", "body/y", "if/z", "if/w"):
            for mode in ("warm", "cold"):
                records.append(heldout_state(state_id, mode,
                                             MISSING_ANSWERS if state_id == "if/w" else None))
        floors = {"heldout": {"all_features": (3, 4), "body_quarantine": (2, 2),
                              "lost_openers": (2, 2)}}
        report = checker.evaluate(scorecard_capture(records), inventory, floors)
        self.assertFalse(report["ok"])
        heldout = report["metrics"]["modes"]["warm"]["heldout"]
        self.assertEqual(heldout["all_features"], [3, 4])
        self.assertEqual(heldout["body_quarantine"], [2, 2])
        self.assertEqual(heldout["lost_openers"], [1, 2])
        self.assertTrue(any("heldout.lost_openers is 1/2" in failure
                            for failure in report["failures"]), report["failures"])

    def test_each_heldout_subgroup_floor_gates_its_own_subgroup(self):
        self.assertEqual(set(checker.FLOORS["heldout"]), {
            "all_features", "body_quarantine", "lost_openers",
            "seed_317_block_closers", "seed_320_block_closers", "method_closers"})
        for name, (minimum, expected) in checker.FLOORS["heldout"].items():
            if minimum == 0:
                continue
            with self.subTest(floor=name):
                metrics = met_floors(checker.FLOORS)
                metrics["modes"]["warm"]["heldout"][name] = [minimum - 1, expected]
                failures = checker.check_floors(metrics)
                self.assertTrue(any("warm: heldout." + name in failure for failure in failures),
                                failures)

    def test_refuses_wrong_typed_identifying_fields_without_traceback(self):
        inventory = scorecard_inventory("first")
        records = [scorecard_state("first", "warm"), scorecard_state("first", "cold")]
        for field, value in (("id", 3), ("suite", []), ("mode", []),
                             ("edit_kind", 0), ("oracle_id", "zero")):
            with self.subTest(field=field):
                capture = scorecard_capture(copy.deepcopy(records))
                capture["states"][0][field] = value
                failures = checker.validate(capture, inventory)
                self.assertTrue(any(field + " is not " in failure for failure in failures), failures)
                self.assertFalse(checker.evaluate(capture, inventory, {})["ok"])

    def test_refuses_a_wrong_typed_answer_without_rescoring_it(self):
        inventory = scorecard_inventory("first")
        for where in ("answers", "oracle"):
            with self.subTest(where=where):
                capture = scorecard_capture([scorecard_state("first", "warm"),
                                             scorecard_state("first", "cold")])
                answers = (capture["states"][0]["answers"] if where == "answers"
                           else capture["oracles"][0]["answers"])
                answers["0:references"] = 5
                failures = checker.validate(capture, inventory)
                self.assertTrue(any("0:references" in failure and "type" in failure
                                    for failure in failures), failures)

    def test_scorecard_prints_the_baseline_numerators(self):
        inventory = scorecard_inventory("first")
        capture = scorecard_capture([scorecard_state("first", "warm"),
                                     scorecard_state("first", "cold")])
        report = checker.evaluate(capture, inventory, {"primary": {"all_features": (1, 1)}})
        self.assertTrue(report["ok"], report["failures"])
        scorecard = checker.format_scorecard(report, capture)
        self.assertIn("baseline 153/185", scorecard)
        self.assertIn("baseline 40/94", scorecard)


class Inventory(unittest.TestCase):
    def test_the_committed_inventory_matches_the_corpus_generators(self):
        spec = importlib.util.spec_from_file_location(
            "tolerance_inventory", ROOT / "scripts/tolerance-inventory.py")
        inventory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inventory)
        # The check must replay the recorded parse verdicts, not build a server.
        inventory.DEFAULT_EXE = ROOT / "build/no-server-was-built"
        self.assertEqual(inventory.check(), [])


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
