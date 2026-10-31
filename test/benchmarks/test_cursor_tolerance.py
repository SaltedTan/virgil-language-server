#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The cursor tolerance histories' inventory, anchors, and verdicts.

These checks need no server. Run them by hand with
`python3 test/benchmarks/test_cursor_tolerance.py`; they are not part of
`make test`.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cursor", ROOT / "scripts/bench-cursor-tolerance.py")
cursor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cursor)
INVENTORY = json.loads(cursor.INVENTORY.read_text())
HISTORIES = {h["id"]: h for h in INVENTORY["histories"]}
THIRD = HISTORIES["third-damaged-body"]
VENDORED = (cursor.VIRGIL / "aeneas/src/vst/Verifier.v3").exists()


def build(corpus_type=None):
    with tempfile.TemporaryDirectory() as temp:
        corpus = (corpus_type or cursor.states.Corpus)(None, Path(temp))
        return json.loads(json.dumps(cursor.build(corpus)))


def item(label, kind, detail=None):
    value = {"label": label, "kind": kind, "textEdit": {"newText": label}}
    if detail is not None:
        value["detail"] = detail
    return value


def help(label, parameters, active):
    return {"signatures": [{"label": label, "parameters": [{"label": p} for p in parameters]}],
            "activeSignature": 0, "activeParameter": active}


def probe(history, step_id, probe_id):
    st = next(s for s in history["steps"] if s["id"] == step_id)
    return next(p for p in st["probes"] if p["id"] == probe_id)


SUM = "def sum(left: int, amount: int) -> int"
ADD = "def add(left: int, right: int) -> int"


def records(inventory=INVENTORY, blocks=1):
    """A complete capture in which every measured answer is exact."""
    result = []
    for block in range(blocks):
        for history in inventory["histories"]:
            for mode in cursor.MODES:
                steps = []
                for index, st in enumerate(history["steps"]):
                    if mode == "cold" and index == 0:
                        steps.append({"id": st["id"], "state": st["state"], "status": "not-run"})
                        continue
                    measured = {"status": "measured", "verdict": "exact", "latency_ms": 1.0}
                    steps.append({"id": st["id"], "state": st["state"], "status": "measured",
                                  "step_latency_ms": 2.0, "worker_replacements": 0,
                                  "probes": [{"id": p["id"], "kind": p["kind"], **measured} for p in st["probes"]],
                                  "observers": [{"id": o["id"], "role": o["role"], **(measured if o.get("current") else {
                                      "status": "inapplicable"})} for o in st["observers"]]})
                result.append({"block": block, "history": history["id"], "mode": mode, "steps": steps})
    return result


class Inventory(unittest.TestCase):
    @unittest.skipUnless(VENDORED, "vendor/virgil is not initialized")
    def test_checked_in_inventory_matches_its_definitions(self):
        self.assertEqual(cursor.frozen(build()), INVENTORY)

    def test_inventory_has_the_seventeen_named_histories(self):
        self.assertEqual(list(HISTORIES), [
            "member-with-prefix", "call-open-arguments", "call-trailing-comma", "member-in-field-initializer",
            "member-in-component-field", "syntax-error-in-other-file", "parameter-type", "return-type",
            "typed-initializer", "inferred-initializer", "open-string", "open-block-comment",
            "missing-inner-closer", "missing-method-closer", "third-damaged-body",
            "aeneas-verifier-member", "aeneas-arm64-member"])
        self.assertEqual(INVENTORY["smoke"], ["member-with-prefix", "call-open-arguments"])
        for history in INVENTORY["histories"]:
            ids = [s["id"] for s in history["steps"]]
            expected = {"seed": ["baseline", "damaged", "restored", "repeat"],
                        "small": ["baseline", "removed", "extra", "restored", "repeat"],
                        "aeneas": ["baseline", "dot", "prefix", "restored", "repeat"]}[history["origin"]]
            self.assertEqual(ids, expected, history["id"])
            self.assertTrue(history["role"], history["id"])
            # The first step is a valid baseline, and the restoration and its
            # repeat return to the intended program.
            self.assertEqual([s["state"] for s in history["steps"]][-2:], ["valid", "valid"], history["id"])
            self.assertEqual(history["steps"][0]["state"], "valid", history["id"])
            self.assertIn("damaged", [s["state"] for s in history["steps"]], history["id"])

    def test_small_histories_share_one_three_file_fixture(self):
        fixture = {p: cursor.sha256(t.encode()) for p, t in cursor.FIXTURE.items()}
        small = [h for h in INVENTORY["histories"] if h["origin"] == "small"]
        self.assertEqual(len(small), 9)
        for history in small:
            self.assertEqual(history["initial"], fixture, history["id"])
        for text in cursor.FIXTURE.values():
            self.assertIn(cursor.sha256(text.encode()), INVENTORY["texts"])
        self.assertIn("def add(left: int, right: int) -> int", cursor.UTIL)
        self.assertIn("\t\tvar s = \"text\";", cursor.A)
        self.assertIn("\t\tvar x = 0;", cursor.A)
        self.assertIn("return A.sibling();", cursor.CONSUMER)

    def test_every_recorded_text_hash_holds_its_text(self):
        for digest, text in INVENTORY["texts"].items():
            self.assertEqual(cursor.sha256(text.encode()), digest)
        for history in INVENTORY["histories"]:
            if history["origin"] == "aeneas":
                continue
            for st in history["steps"]:
                for digest in list(st["texts"].values()) + list(st["intended"].values()):
                    self.assertIn(digest, INVENTORY["texts"], (history["id"], st["id"]))

    def test_anchors_resolve_to_their_recorded_positions(self):
        texts = INVENTORY["texts"]
        for history in INVENTORY["histories"]:
            if history["origin"] == "aeneas":
                continue
            for st in history["steps"]:
                for p in st["probes"]:
                    current, intended = texts[st["texts"][p["path"]]], texts[st["intended"][p["path"]]]
                    at = cursor.locate(current, p["anchor"], p["id"])
                    self.assertEqual(cursor.bench.corpus.position(current, at), p["position"])
                    at = cursor.locate(intended, p["intended_anchor"], p["id"])
                    self.assertEqual(cursor.bench.corpus.position(intended, at), p["intended_position"])
                for o in st["observers"]:
                    if o.get("status") == "inapplicable":
                        self.assertTrue(o["reason"], (history["id"], st["id"], o["id"]))
                        continue
                    at = cursor.locate(texts[st["texts"][o["path"]]], o["anchor"], o["id"])
                    self.assertEqual(cursor.bench.corpus.position(texts[st["texts"][o["path"]]], at),
                                     o["current"]["position"])

    def test_missing_or_repeated_anchors_are_refused(self):
        for text, anchor in (("var a = x.y; var b = x.y;", "x.|y"), ("var a = x.y;", "z.|y"),
                             ("var a = x.y;", "x.y"), ("var a = x.y;", "x|.|y")):
            with self.assertRaises(AssertionError):
                cursor.locate(text, anchor, "test")
        with self.assertRaises(AssertionError):
            cursor.edit("f(1); f(1);", "f(1)", "f(2)", "test")
        with self.assertRaises(AssertionError):
            cursor.edit("f(1);", "g(1)", "g(2)", "test")
        self.assertEqual(cursor.locate("var a = x.y;", "x.|y", "test"), 10)

    @unittest.skipUnless(VENDORED, "vendor/virgil is not initialized")
    def test_a_changed_fixture_is_refused(self):
        class Changed(cursor.states.Corpus):
            def remember(self, path):
                text = super().remember(path)
                if Path(path).name == "member-with-prefix.v3":
                    self.inputs[str(Path(path).relative_to(ROOT))] = cursor.sha256(b"changed")
                return text
        with self.assertRaisesRegex(AssertionError, "fixture differs from its seed hash"):
            build(Changed)

    @unittest.skipUnless(VENDORED, "vendor/virgil is not initialized")
    def test_a_changed_definition_is_refused(self):
        changed = copy.deepcopy(INVENTORY)
        changed["histories"][6]["steps"][1]["edits"][0][2] = "def edit(n: bool)"
        built = build()
        with mock.patch.object(cursor.INVENTORY.__class__, "read_text", return_value=json.dumps(changed)):
            with self.assertRaisesRegex(AssertionError, "cursor histories changed"):
                cursor.frozen(built)

    @unittest.skipUnless(VENDORED, "vendor/virgil is not initialized")
    def test_real_histories_pin_the_compiler_and_its_sources(self):
        self.assertEqual(INVENTORY["virgil_revision"],
                         cursor.bench.corpus.git_revision(cursor.VIRGIL, "rev-parse", "HEAD"))
        for history in (HISTORIES["aeneas-verifier-member"], HISTORIES["aeneas-arm64-member"]):
            self.assertEqual(history["project_sources"], len(cursor.states.repair.aeneas_files()))
            for path, digest in history["sources"].items():
                self.assertEqual(cursor.sha256((cursor.VIRGIL / path).read_bytes()), digest, path)
            # The intended text of every step is the upstream file.
            original = history["sources"][history["aeneas_path"]]
            self.assertEqual({st["intended"][history["aeneas_path"]] for st in history["steps"]}, {original})
            self.assertEqual({p["kind"] for st in history["steps"] for p in st["probes"]}, {"completion"})


class Verdicts(unittest.TestCase):
    def test_completion_outcomes_are_distinct(self):
        facts = probe(HISTORIES["parameter-type"], "removed", "cursor")["facts"]
        self.assertEqual(cursor.completion_verdict(None, facts)[0], "missing")
        self.assertEqual(cursor.completion_verdict({"isIncomplete": True, "items": []}, facts)[0], "missing")
        keywords = {"isIncomplete": False, "items": [item("var", 14), item("def", 14)]}
        self.assertEqual(cursor.completion_verdict(keywords, facts)[0], "keywords-only")
        names = {"items": [item("A", 9, "component A"), item("var", 14)]}
        self.assertEqual(cursor.completion_verdict(names, facts)[0], "missing")
        exact = {"items": [item("A", 9, "component A"), item("int", 7), item("var", 14)]}
        self.assertEqual(cursor.completion_verdict(exact, facts)[0], "exact")
        self.assertEqual(cursor.completion_verdict({"items": [item("int", 2, "def int()")]}, facts)[0], "wrong")

    def test_partial_completion_has_some_required_candidates(self):
        facts = probe(HISTORIES["member-in-field-initializer"], "damaged", "cursor")["facts"]
        self.assertEqual(len(facts["required"]), 2)
        reply = {"items": [item("x", 5, "def x: int"), item("y", 5, "def y: int")]}
        self.assertEqual(cursor.completion_verdict(reply, facts)[0], "partial")
        reply["items"].append(item("norm", 2, "def norm() -> int"))
        self.assertEqual(cursor.completion_verdict(reply, facts)[0], "exact")

    def test_another_receivers_members_are_wrong(self):
        facts = probe(HISTORIES["open-block-comment"], "removed", "cursor")["facts"]
        self.assertTrue(facts["member"])
        self.assertEqual(cursor.completion_verdict({"items": [item("length", 5, "def length: int")]}, facts)[0], "wrong")

    def test_stale_candidates_are_rejected(self):
        facts = probe(THIRD, "extra", "third")["facts"]
        self.assertEqual(facts["forbidden"], ["add"])
        current = {"items": [item("sum", 2, SUM)]}
        self.assertEqual(cursor.completion_verdict(current, facts)[0], "exact")
        self.assertEqual(cursor.completion_verdict({"items": [item("add", 2, ADD)]}, facts)[0], "wrong")
        # The renamed candidate beside the old one, and with the old parameter.
        self.assertEqual(cursor.completion_verdict({"items": [item("sum", 2, SUM), item("add", 2, ADD)]}, facts)[0],
                         "wrong")
        stale_parameter = {"items": [item("sum", 2, "def sum(left: int, right: int) -> int")]}
        self.assertEqual(cursor.completion_verdict(stale_parameter, facts)[0], "wrong")

    def test_stale_signatures_are_rejected(self):
        facts = probe(THIRD, "removed", "edit-call")["facts"]
        self.assertEqual(cursor.signature_verdict(help(SUM, ["left: int", "amount: int"], 1), facts)[0], "exact")
        self.assertEqual(cursor.signature_verdict(help(ADD, ["left: int", "right: int"], 1), facts)[0], "wrong")
        renamed_only = help("def sum(left: int, right: int) -> int", ["left: int", "right: int"], 1)
        self.assertEqual(cursor.signature_verdict(renamed_only, facts)[0], "wrong")
        mislabeled = help(SUM, ["left: int", "right: int"], 1)
        self.assertEqual(cursor.signature_verdict(mislabeled, facts)[0], "wrong")

    def test_active_parameter_is_checked(self):
        facts = probe(HISTORIES["typed-initializer"], "extra", "cursor")["facts"]
        self.assertEqual(facts["active"], 1)
        self.assertEqual(cursor.signature_verdict(help(ADD, ["left: int", "right: int"], 1), facts)[0], "exact")
        for active in (0, 2):
            self.assertEqual(cursor.signature_verdict(help(ADD, ["left: int", "right: int"], active), facts)[0], "wrong")
        # A signature's own active parameter takes precedence over the reply's.
        reply = help(ADD, ["left: int", "right: int"], 1)
        reply["signatures"][0]["activeParameter"] = 0
        self.assertEqual(cursor.signature_verdict(reply, facts)[0], "wrong")
        self.assertEqual(cursor.signature_verdict(help(ADD, ["left: int"], 1), facts)[0], "partial")
        self.assertEqual(cursor.signature_verdict(None, facts)[0], "missing")
        self.assertEqual(cursor.signature_verdict({"signatures": []}, facts)[0], "missing")
        out_of_range = help(ADD, ["left: int", "right: int"], 1)
        out_of_range["activeSignature"] = 1
        self.assertEqual(cursor.signature_verdict(out_of_range, facts)[0], "wrong")

    def test_observers_need_the_reviewed_binding(self):
        expected = {"uri": "project:///Util.v3", "range": {"start": {"line": 1, "character": 5},
                                                           "end": {"line": 1, "character": 8}}}
        self.assertEqual(cursor.observer_verdict(expected, expected)[0], "exact")
        self.assertEqual(cursor.observer_verdict([expected], expected)[0], "exact")
        self.assertEqual(cursor.observer_verdict([], expected)[0], "missing")
        self.assertEqual(cursor.observer_verdict(None, expected)[0], "missing")
        moved = {**expected, "range": {**expected["range"], "start": {"line": 1, "character": 4}}}
        self.assertEqual(cursor.observer_verdict(moved, expected)[0], "wrong")
        self.assertEqual(cursor.observer_verdict([expected, moved], expected)[0], "wrong")

    def test_stale_control_must_reject_every_sensitive_probe(self):
        def stale_record(reply_for):
            return {"steps": [{"probes": [{"id": p["id"], "reply": reply_for(p)} for p in st["probes"]]}
                              for st in THIRD["steps"]]}
        old = lambda p: help(ADD, ["left: int", "right: int"], 1) if p["kind"] == "signature" else {
            "items": [item("add", 2, ADD)]}
        rejected = cursor.stale_control(THIRD, stale_record(old))
        self.assertEqual(len(rejected), sum(p["stale_sensitive"] for st in THIRD["steps"] for p in st["probes"]))
        current = lambda p: help(SUM, ["left: int", "amount: int"], 1) if p["kind"] == "signature" else {
            "items": [item("sum", 2, SUM)]}
        with self.assertRaisesRegex(AssertionError, "stale answer was not rejected"):
            cursor.stale_control(THIRD, stale_record(current))


class Records(unittest.TestCase):
    def check(self, result, blocks=1):
        cursor.check_records(INVENTORY, result, blocks, list(HISTORIES))

    def test_accepts_a_complete_capture(self):
        self.check(records(blocks=2), blocks=2)
        summary = cursor.summarize(records())
        self.assertEqual({row["mode"] for row in summary["probes"]}, set(cursor.MODES))
        self.assertEqual(cursor.consistency(records(blocks=2)), [])

    def test_refuses_a_missing_history_step(self):
        result = records()
        del result[4]["steps"][2]
        with self.assertRaisesRegex(AssertionError, "missing or reordered history steps"):
            self.check(result)

    def test_refuses_reordered_steps(self):
        result = records()
        steps = result[0]["steps"]
        steps[1], steps[2] = steps[2], steps[1]
        with self.assertRaisesRegex(AssertionError, "missing or reordered history steps"):
            self.check(result)

    def test_refuses_a_step_without_its_probes(self):
        result = records()
        result[3]["steps"][1]["probes"] = []
        with self.assertRaisesRegex(AssertionError, "missing step evidence"):
            self.check(result)

    def test_refuses_a_missing_mode_or_block(self):
        result = [r for r in records() if not (r["history"] == "open-string" and r["mode"] == "saved")]
        with self.assertRaisesRegex(AssertionError, "missing or duplicate history records"):
            self.check(result)
        with self.assertRaisesRegex(AssertionError, "missing or duplicate history records"):
            self.check(records(), blocks=2)
        with self.assertRaisesRegex(AssertionError, "missing or duplicate history records"):
            self.check(records() + records()[:1])

    def test_cold_baseline_must_be_not_run(self):
        result = records()
        cold = next(r for r in result if r["mode"] == "cold")
        warm = next(r for r in result if r["mode"] == "warm" and r["history"] == cold["history"])
        cold["steps"][0] = copy.deepcopy(warm["steps"][0])
        with self.assertRaisesRegex(AssertionError, "cold baseline must be not-run"):
            self.check(result)

    def test_refuses_an_unknown_verdict(self):
        result = records()
        result[0]["steps"][0]["probes"][0]["verdict"] = "available"
        with self.assertRaises(AssertionError):
            self.check(result)

    def test_unsupported_and_inapplicable_are_counted_apart(self):
        result = records()
        result[2]["steps"][1]["probes"][0] = {"id": "cursor", "kind": "completion", "status": "unsupported"}
        self.check(result)
        summary = cursor.summarize(result)
        mode = result[2]["mode"]
        row = next(r for r in summary["probes"] if r["mode"] == mode and r.get("unsupported"))
        self.assertEqual(row["unsupported"], 1)
        self.assertGreater(sum(r.get("inapplicable", 0) for r in summary["observers"]), 0)

    def test_inconsistent_blocks_are_reported(self):
        result = records(blocks=2)
        result[-1]["steps"][-1]["probes"][0]["verdict"] = "missing"
        found = cursor.consistency(result)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["outcomes"], ["exact", "missing"])


if __name__ == "__main__":
    unittest.main()
