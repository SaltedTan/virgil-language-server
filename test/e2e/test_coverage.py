#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Checks the editor coverage contracts (RESULTS.md, "Adding a check"),
without reading the tests that the manifest describes."""
import copy
import importlib.util
import json
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "editor_results", HERE.parent.parent / "scripts" / "editor-results.py")
RESULTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESULTS)
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
CHECK_ID = re.compile(rf"({NAME.pattern})\.({NAME.pattern})\.({NAME.pattern})")
GAP_ID = re.compile(rf"({NAME.pattern})\.{NAME.pattern}")
COMMIT = re.compile(r"[0-9a-f]{40}")
ISSUE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/[0-9]+")
# Steps 1 to 17 of the VS Code manual checklist (clients/vscode/README.md#manual-checklist),
# described in docs/manual-testing.md#vs-code. Declared here, apart from the manifest's steps,
# so that deleting a step fails the test; change it together with the checklist.
CHECKLIST_STEPS = range(1, 18)


def load(name):
    """Parses a JSON file, rejecting duplicate keys and NaN or Infinity."""
    def pairs(items):
        keys = [key for key, _ in items]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise ValueError(f"{name}: duplicate keys {repeated}")
        return dict(items)

    def constant(value):
        raise ValueError(f"{name}: {value} is not JSON")

    with (HERE / name).open(encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=pairs, parse_constant=constant)


def by_id(items):
    return {item["id"]: item for item in items}


def repeated(values):
    return sorted({value for value in values if values.count(value) > 1})


def unavailable_paths(document):
    consumer = RESULTS.Schema(document)
    accepted = set()

    def walk(node, path, field=False, base=None):
        """base is the schema that node refines in an if, then, or else branch,
        so that a field there is tested with the constraints it refines."""
        if base is False:
            return
        if field:
            refined = node if base is None else {"allOf": [base, node]}
            probe = RESULTS.Schema({
                "$schema": document["$schema"],
                "$defs": document["$defs"],
                "allOf": [refined],
            })
            if not probe.errors({"value": None, "unavailable": "prerequisite failed"}):
                accepted.add(path)
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            walk(consumer.schemas[consumer.references[node["$ref"]]], path, base=base)
        base = base if isinstance(base, dict) else {}
        for name, child in node.get("properties", {}).items():
            walk(child, f"{path}.{name}" if path else name, True,
                 base=base.get("properties", {}).get(name, base.get("additionalProperties")))
        if "items" in node:
            walk(node["items"], path + "[]", True, base=base.get("items"))
        if "additionalProperties" in node and node["additionalProperties"] is not False:
            walk(node["additionalProperties"], path + ".*", True,
                 base=base.get("additionalProperties"))
        for keyword in ("oneOf", "allOf"):
            for child in node.get(keyword, []):
                walk(child, path, base=base)
        for keyword in ("if", "then", "else"):
            if keyword in node:
                walk(node[keyword], path, base=node)

    walk(document, "")
    return accepted


class CoverageTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.manifest = load("coverage.json")
        cls.schema = load("results-v1.schema.json")
        m = cls.manifest
        cls.evidence = by_id(m["evidence"])
        cls.levels = by_id(m["levels"])
        cls.areas = by_id(m["areas"])
        cls.suites = by_id(m["suites"])
        cls.profiles = {s["id"]: by_id(s["profiles"]) for s in m["suites"]}
        cls.conditions = by_id(m["conditions"])
        cls.scopes = by_id(m["scopes"])
        cls.setups = by_id(m["setups"])
        cls.checks = by_id(m["checks"])
        cls.gaps = by_id(m["gaps"])
        cls.features = [a["id"] for a in m["areas"] if a["matrix"]]

    def area(self, check_id):
        match = CHECK_ID.fullmatch(check_id)
        return match and match.group(2)

    def test_versions(self):
        self.assertEqual(1, self.manifest["version"])
        self.assertEqual({"const": 1}, self.schema["$defs"]["schema"])

    def test_keys_are_unique(self):
        m = self.manifest
        problems = []
        for section in ["evidence", "levels", "areas", "suites", "conditions", "scopes", "setups",
                        "checks", "gaps", "gapKinds", "xfails"]:
            ids = [item["id"] for item in m[section]]
            problems += [f"{section}: {key} repeated" for key in repeated(ids)]
            problems += [f"{section}: {key} is not a name" for key in ids
                         if section not in ("checks", "gaps") and not NAME.fullmatch(key)]
        prefixes = {prefix for suite in m["suites"] for prefix in suite["prefixes"]}
        for gap in m["gaps"]:
            match = GAP_ID.fullmatch(gap["id"])
            if not match or match.group(1) not in prefixes:
                problems.append(f"gaps: {gap['id']} is not a harness prefix and a name")
        for suite in m["suites"]:
            ids = [p["id"] for p in suite["profiles"]]
            problems += [f"suite {suite['id']}: profile {key} repeated" for key in repeated(ids)]
            problems += [f"suite {suite['id']}: profile {key} is not a name" for key in ids if not NAME.fullmatch(key)]
        self.assertEqual([], problems)

    def test_check_ids_are_well_formed(self):
        problems = []
        for check in self.manifest["checks"]:
            key = check["id"]
            match = CHECK_ID.fullmatch(key)
            if not match:
                problems.append(f"{key}: not harness.area.subcase in lowercase words and hyphens")
                continue
            prefix, area, _ = match.groups()
            if area not in self.areas:
                problems.append(f"{key}: unknown area {area}")
            if not check["suites"] or repeated(check["suites"]):
                problems.append(f"{key}: suites must be a nonempty list without repeats")
            for suite in check["suites"]:
                if suite not in self.suites:
                    problems.append(f"{key}: unknown suite {suite}")
                    continue
                if prefix not in self.suites[suite]["prefixes"]:
                    problems.append(f"{key}: suite {suite} doesn't run {prefix}.* checks")
                problems += [f"{key}: suite {suite} has no profile {p}" for p in check["profiles"]
                             if p not in self.profiles[suite]]
            if not check["profiles"] or repeated(check["profiles"]):
                problems.append(f"{key}: profiles must be a nonempty list without repeats")
            if check["level"] not in self.levels:
                problems.append(f"{key}: unknown level {check['level']}")
            if check["when"] is not None and check["when"] not in self.conditions:
                problems.append(f"{key}: unknown condition {check['when']}")
            if not isinstance(check["required"], bool):
                problems.append(f"{key}: required must be true or false")
            if not check["source"] or not check["description"]:
                problems.append(f"{key}: source and description must not be empty")
            if key in self.manifest["retired"]:
                problems.append(f"{key}: retired IDs can't be reused")
        problems += [f"retired {key}: not a check ID" for key in self.manifest["retired"]
                     if not CHECK_ID.fullmatch(key)]
        self.assertEqual([], problems)

    def test_every_level_has_its_wording(self):
        order = [e["id"] for e in self.manifest["evidence"]]
        problems = []
        for level in self.manifest["levels"]:
            tokens = level["evidence"]
            if not tokens or any(t not in self.evidence for t in tokens):
                problems.append(f"level {level['id']}: unknown or no evidence {tokens}")
                continue
            if tokens != sorted(set(tokens), key=order.index):
                problems.append(f"level {level['id']}: evidence must be unique, in the catalogue's order")
            wording = "Pass (" + " + ".join(self.evidence[t]["label"] for t in tokens) + ")"
            if level["wording"] != wording:
                problems.append(f"level {level['id']}: wording {level['wording']!r}, not {wording!r}")
        sets = [tuple(level["evidence"]) for level in self.manifest["levels"]]
        problems += [f"two levels have the evidence {list(s)}" for s in repeated(sets)]
        problems += [f"two levels have the wording {w!r}" for w in
                     repeated([level["wording"] for level in self.manifest["levels"]])]
        titles = [(choice["const"], choice["title"]) for choice in self.schema["$defs"]["level"]["oneOf"]]
        manifest = [(level["id"], level["wording"]) for level in self.manifest["levels"]]
        if titles != manifest:
            problems.append(f"the schema's levels {titles} differ from the manifest's {manifest}")
        self.assertEqual([], problems)

    def test_no_required_check_is_manual_only(self):
        manual = {t for t, e in self.evidence.items() if e["manual"]}
        problems = []
        for check in self.manifest["checks"]:
            level = self.levels.get(check["level"])
            manual_only = level is not None and set(level["evidence"]) <= manual
            if check["required"] and manual_only:
                problems.append(f"{check['id']}: required, but its level {check['level']} is manual only")
            if check["required"] and "manual" in check["suites"]:
                problems.append(f"{check['id']}: required, but a manual record")
        self.assertEqual([], problems)

    def test_every_matrix_cell_is_mapped_not_covered_or_out_of_scope(self):
        matrix = self.manifest["matrix"]
        shown = {t for t, e in self.evidence.items() if e["matrix"]}
        problems = []
        cells = {}
        for cell in matrix["cells"]:
            where = f"cell {cell['setup']}/{cell['feature']}"
            if (cell["setup"], cell["feature"]) in cells:
                problems.append(f"{where}: repeated")
            cells[(cell["setup"], cell["feature"])] = cell
            setup = self.setups.get(cell["setup"])
            if setup is None or cell["feature"] not in self.features:
                problems.append(f"{where}: unknown setup or a feature outside the matrix")
                continue
            for key in cell["checks"]:
                check = self.checks.get(key)
                if check is None:
                    problems.append(f"{where}: unknown check {key}")
                elif setup["suite"] not in check["suites"] or self.area(key) != cell["feature"]:
                    problems.append(f"{where}: {key} doesn't run in {setup['suite']} for {cell['feature']}")
            problems += [f"{where}: unknown gap {g}" for g in cell["notCovered"] if g not in self.gaps]
            problems += [f"{where}: {k} repeated" for k in repeated(cell["checks"] + cell["notCovered"])]
            if not cell["checks"]:
                if cell["level"] is not None or not cell["notCovered"]:
                    problems.append(f"{where}: with no checks, the level must be null and a gap not covered")
                continue
            evidence = set()
            for key in cell["checks"]:
                if key in self.checks and self.checks[key]["level"] in self.levels:
                    evidence |= set(self.levels[self.checks[key]["level"]]["evidence"]) & shown
            level = self.levels.get(cell["level"])
            if level is None or set(level["evidence"]) != evidence:
                problems.append(f"{where}: level {cell['level']}, but its checks give the evidence {sorted(evidence)}")
        for setup in self.manifest["setups"]:
            scope = self.scopes.get(setup["scope"])
            if scope is None or setup["suite"] not in self.suites:
                problems.append(f"setup {setup['id']}: unknown scope or suite")
                continue
            present = [f for f in self.features if (setup["id"], f) in cells]
            if setup["scope"] == "automated":
                problems += [f"setup {setup['id']}: no cell for {f}" for f in self.features if f not in present]
            else:
                problems += [f"setup {setup['id']}: out of scope ({setup['scope']}), but has a cell for {f}" for f in present]
                if not scope["wording"]:
                    problems.append(f"scope {scope['id']}: an out-of-scope row needs its wording")
            if setup["scope"] == "deferred" and not (setup["issue"] and ISSUE.fullmatch(setup["issue"])):
                problems.append(f"setup {setup['id']}: deferred without an issue")
        # A matrix check counts in every automated setup that runs it.
        for check in self.manifest["checks"]:
            feature = self.area(check["id"])
            if feature not in self.features:
                continue
            for setup in self.manifest["setups"]:
                cell = cells.get((setup["id"], feature))
                if setup["scope"] == "automated" and setup["suite"] in check["suites"] \
                        and (cell is None or check["id"] not in cell["checks"]):
                    problems.append(f"{check['id']}: missing from cell {setup['id']}/{feature}")
        self.assertEqual([], problems)

    def test_every_checklist_step_is_mapped_or_not_covered(self):
        checklist = self.manifest["checklist"]
        steps = [step["step"] for step in checklist["steps"]]
        self.assertEqual(list(CHECKLIST_STEPS), steps,
                         "the checklist must have every step from 1 to 17, in order")
        problems = []
        for suite in checklist["suites"]:
            if suite not in self.suites:
                problems.append(f"checklist: unknown suite {suite}")
        for step in checklist["steps"]:
            where = f"step {step['step']}"
            if not step["summary"]:
                problems.append(f"{where}: no summary")
            for key in step["checks"]:
                check = self.checks.get(key)
                if check is None or not set(check["suites"]) & set(checklist["suites"]):
                    problems.append(f"{where}: {key} is not a check of {checklist['suites']}")
            problems += [f"{where}: unknown gap {g}" for g in step["notCovered"] if g not in self.gaps]
            problems += [f"{where}: {k} repeated" for k in repeated(step["checks"] + step["notCovered"])]
            for suite in checklist["suites"]:
                if not step["notCovered"] and not any(
                        suite in self.checks[key]["suites"] for key in step["checks"] if key in self.checks):
                    problems.append(f"{where}: neither a check of {suite} nor a gap not covered")
        self.assertEqual([], problems)

    def test_gaps_and_conditions(self):
        kinds = {kind["id"] for kind in self.manifest["gapKinds"]}
        used = {g for cell in self.manifest["matrix"]["cells"] for g in cell["notCovered"]}
        used |= {g for step in self.manifest["checklist"]["steps"] for g in step["notCovered"]}
        problems = []
        for gap in self.manifest["gaps"]:
            if gap["kind"] not in kinds or not gap["what"]:
                problems.append(f"gap {gap['id']}: unknown kind or no description")
            if gap["planned"] is not None and not ISSUE.fullmatch(gap["planned"]):
                problems.append(f"gap {gap['id']}: planned must be null or an issue URL")
            if gap["id"] not in used:
                problems.append(f"gap {gap['id']}: not used by a cell or a step")
        when = {check["when"] for check in self.manifest["checks"]}
        for condition in self.manifest["conditions"]:
            other = condition["alternative"]
            if condition["id"] not in when:
                problems.append(f"condition {condition['id']}: no check uses it")
            if other is not None and (other not in self.conditions or self.conditions[other]["alternative"] != condition["id"]):
                problems.append(f"condition {condition['id']}: its alternative {other} doesn't name it back")
            # The suites in which the condition holds in every run.
            if repeated(condition["always"]):
                problems.append(f"condition {condition['id']}: always repeats a suite")
            for suite in condition["always"]:
                if suite not in self.suites:
                    problems.append(f"condition {condition['id']}: always names the unknown suite {suite}")
                elif not any(check["when"] == condition["id"] and suite in check["suites"]
                             for check in self.manifest["checks"]):
                    problems.append(f"condition {condition['id']}: always names suite {suite}, "
                                    "which runs none of its checks")
                elif other in self.conditions and suite in self.conditions[other]["always"]:
                    problems.append(f"condition {condition['id']} and its alternative {other} both always hold "
                                    f"in suite {suite}")
        self.assertEqual([], problems)

    def test_a_fact_decides_each_alternative(self):
        run_start = self.schema["$defs"]["runStart"]
        self.assertIn("facts", run_start["required"], "the run-start requires the facts of version 1")
        defined = run_start["properties"]["facts"]["properties"]
        possible = {}
        for name, node in defined.items():
            values = []
            for branch in node.get("oneOf", [node]):
                if "$ref" not in branch:
                    values += branch.get("enum", [])
            possible[name] = values
        problems = []
        used = {}
        for condition in self.manifest["conditions"]:
            key = condition["id"]
            other = condition["alternative"]
            decided = condition.get("decidedBy")
            if other is None:
                if decided is not None:
                    problems.append(f"condition {key}: has decidedBy without an alternative")
                continue
            if not isinstance(decided, dict) or set(decided) != {"fact", "values"}:
                problems.append(f"condition {key}: decidedBy names a fact and its values")
                continue
            if decided["fact"] not in defined:
                problems.append(f"condition {key}: its fact {decided['fact']} is not defined in the schema")
            values = decided["values"]
            if not isinstance(values, list) or not values:
                problems.append(f"condition {key}: decidedBy.values must be a nonempty array")
                continue
            if len({json.dumps(value, sort_keys=True) for value in values}) != len(values):
                problems.append(f"condition {key}: decidedBy.values repeats a value")
            used.setdefault(decided["fact"], {})[key] = values
        for fact, sides in used.items():
            if fact not in defined:
                continue
            allowed = possible[fact]
            covered = []
            for key, values in sides.items():
                for value in values:
                    if not any(RESULTS.same(value, option) for option in allowed):
                        problems.append(f"condition {key}: {json.dumps(value)} is not a value of fact {fact}")
            for key, values in sides.items():
                other = self.conditions[key]["alternative"]
                if key > other:
                    continue
                theirs = used.get(fact, {}).get(other)
                if theirs is None:
                    problems.append(f"condition {key}: its alternative {other} doesn't name the fact {fact}")
                    continue
                for value in values:
                    if any(RESULTS.same(value, mine) for mine in theirs):
                        problems.append(f"condition {key} and its alternative {other} both take "
                                        f"{json.dumps(value)}")
                    covered.append(value)
                covered += theirs
            missing = [value for value in allowed
                       if not any(RESULTS.same(value, mine) for mine in covered)]
            if missing:
                problems.append(f"fact {fact}: no side takes "
                                + ", ".join(json.dumps(value) for value in missing))
        self.assertEqual([], problems)

    def test_a_condition_always_holds_only_in_suites_that_run_its_checks(self):
        for changes, message in (
                ({"vsix": ["remote-ssh", "remote-ssh"]}, "always repeats a suite"),
                ({"vsix": ["nowhere"]}, "always names the unknown suite nowhere"),
                ({"vsix": ["nvim"]}, "always names suite nvim, which runs none of its checks"),
                ({"file-watching": ["lazyvim"], "no-file-watching": ["lazyvim"]},
                 "both always hold in suite lazyvim")):
            with self.subTest(changes=changes):
                mutant = CoverageTest("test_gaps_and_conditions")
                mutant.manifest = copy.deepcopy(self.manifest)
                mutant.conditions = by_id(mutant.manifest["conditions"])
                for condition_id, always in changes.items():
                    mutant.conditions[condition_id]["always"] = always
                with self.assertRaisesRegex(AssertionError, message):
                    mutant.test_gaps_and_conditions()

    def test_the_expected_failure_rule(self):
        rules = self.manifest["xfails"]
        self.assertEqual(1, len(rules), "the only expected failure is noice.nvim's missing-root warning")
        rule = rules[0]
        # The maintainer approved only this warning-only exception (2026-10-23). These are
        # literals, not values read from the manifest, so that moving the rule to another check
        # or changing what it excuses is a deliberate edit.
        approved = {
            "id": "noice-1074-7bfd942",
            "check": "lazyvim.configuration.warning",
            "profile": "noroot",
            "plugin": "noice.nvim",
            "commit": "7bfd942445fb63089b59f97ca487d605e715f155",
            "issue": "https://github.com/folke/noice.nvim/issues/1074",
            "failureCode": "warning-not-observed",
            "workaroundProfile": "noroot-noice-workaround",
            "requires": {
                "lazyvim.startup.ui-ready",
                "lazyvim.configuration.rootless-attach",
                "lazyvim.configuration.missing-root",
            },
        }
        problems = []
        for field, value in approved.items():
            found = rule.get(field)
            if field == "requires":
                found = set(found or [])
            if found != value:
                problems.append(
                    f"{field}: {found!r}, not the approved {value!r}; the maintainer approved only "
                    "the noice.nvim missing-root exception (2026-10-23)")
        # A noice.nvim pin bump changes coverage.json and this guard together, after the
        # XFAIL is re-verified.
        self.assertIsInstance(rule["check"], str, "a rule names exactly one check")
        self.assertIsInstance(rule["commit"], str, "a rule names exactly one commit")
        self.assertRegex(rule["commit"], COMMIT.pattern + r"\Z")
        self.assertRegex(rule["failureCode"], NAME.pattern + r"\Z")
        self.assertRegex(rule["issue"], ISSUE.pattern + r"\Z")
        self.assertTrue(rule["plugin"])
        # Generated tables name the rule by its label (RESULTS.md, "The matrix").
        self.assertIsInstance(rule.get("label"), str, "a rule has a label")
        self.assertTrue(rule["label"].strip(), "a rule's label isn't empty")
        check = self.checks.get(rule["check"])
        self.assertIsNotNone(check, f"unknown check {rule['check']}")
        for suite in check["suites"]:
            profiles = self.profiles[suite]
            stock, workaround = profiles.get(rule["profile"]), profiles.get(rule["workaroundProfile"])
            if stock is None or stock["workaround"] or rule["profile"] not in check["profiles"]:
                problems.append(f"profile {rule['profile']}: must be a stock profile of {rule['check']}")
            if workaround is None or not workaround["workaround"] or rule["workaroundProfile"] not in check["profiles"]:
                problems.append(f"profile {rule['workaroundProfile']}: must be a workaround profile of {rule['check']}")
        for key in rule["requires"]:
            other = self.checks.get(key)
            if other is None or key == rule["check"] or rule["profile"] not in other["profiles"]:
                problems.append(f"requires {key}: must be another check in the profile {rule['profile']}")
        if not rule["requires"]:
            problems.append("the rule requires no checks to pass beside it")
        self.assertEqual([], problems)

    def test_changed_expected_failure_identity_is_rejected(self):
        for field, value in [("commit", "0" * 40),
                             ("issue", "https://github.com/folke/noice.nvim/issues/1075")]:
            with self.subTest(field=field):
                mutant = CoverageTest("test_the_expected_failure_rule")
                mutant.manifest = copy.deepcopy(self.manifest)
                mutant.manifest["xfails"][0][field] = value
                with self.assertRaisesRegex(AssertionError, "not the approved"):
                    mutant.test_the_expected_failure_rule()

    def test_unavailable_provenance_contract(self):
        defs = self.schema["$defs"]
        self.assertEqual({"$ref": "#/$defs/provenance"},
                         defs["runStart"]["properties"]["provenance"])
        self.assertEqual({
            "type": "object",
            "properties": {
                "value": {"type": "null"},
                "unavailable": {"$ref": "#/$defs/nonEmpty"},
            },
            "required": ["value", "unavailable"],
            "additionalProperties": False,
        }, {key: value for key, value in defs["unavailableProvenance"].items()
            if key != "description"})
        provenance = defs["provenance"]["properties"]
        server = provenance["server"]["properties"]
        vsix = {
            "type": "object",
            "properties": {
                "name": {"$ref": "#/$defs/nonEmpty"},
                "sha256": {"$ref": "#/$defs/sha256"},
                "version": {"$ref": "#/$defs/nonEmpty"},
            },
            "required": ["name", "sha256", "version"],
            "additionalProperties": False,
        }
        fields = [
            (server["versionText"], [{"$ref": "#/$defs/nonEmpty"}]),
            (server["target"], [{"enum": ["x86-64-linux", "x86-64-darwin"]}]),
            (server["sha256"], [{"$ref": "#/$defs/sha256"}]),
            (server["workerSha256"], [{"$ref": "#/$defs/sha256"}]),
            (provenance["vsix"], [{"type": "null"}, vsix]),
            (provenance["fixtureSha256"], [{"$ref": "#/$defs/sha256"}]),
        ]
        for field, available in fields:
            self.assertEqual(available + [{"$ref": "#/$defs/unavailableProvenance"}],
                             field["oneOf"])
        # RESULTS.md lists exactly these fields and facts.
        self.assertEqual({
            "provenance.server.versionText",
            "provenance.server.target",
            "provenance.server.sha256",
            "provenance.server.workerSha256",
            "provenance.vsix",
            "provenance.fixtureSha256",
            "facts.neovimBuiltinLspCommand",
            "facts.didChangeWatchedFilesDynamicRegistration",
        }, unavailable_paths(self.schema),
            "unavailable fields differ from RESULTS.md's list")

    def test_unavailable_provenance_paths_detect_widened_fields(self):
        expected = unavailable_paths(self.schema)
        for path, location in (
                ("provenance.server.version", ("provenance", "properties", "server", "properties", "version")),
                ("provenance.client.release", ("machine", "properties", "release")),
                ("provenance.pins[].version", ("provenance", "properties", "pins", "items", "properties", "version")),
                ("provenance.runtimes.*", ("provenance", "properties", "runtimes", "additionalProperties")),
                ("observation", ("check", "properties", "observation")),
                ("issues[]", ("runEnd", "properties", "issues", "items"))):
            for inline in (False, True):
                with self.subTest(path=path, inline=inline):
                    mutant = copy.deepcopy(self.schema)
                    node = mutant["$defs"]
                    for part in location[:-1]:
                        node = node[part]
                    unavailable = mutant["$defs"]["unavailableProvenance"] if inline else \
                        {"$ref": "#/$defs/unavailableProvenance"}
                    node[location[-1]] = {"oneOf": [node[location[-1]], unavailable]}
                    added = {path}
                    if location[0] == "machine":
                        added = {"provenance.client.release", "provenance.host.release"}
                    self.assertEqual(expected | added, unavailable_paths(mutant))

    def test_unavailable_provenance_paths_descend_into_conditions(self):
        unavailable = {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/unavailableProvenance"}]}
        for keyword in ("if", "then", "else"):
            for additional, allowed in ((False, False), (True, True), (None, True),
                                        ({"type": "integer"}, False), (unavailable, True)):
                with self.subTest(keyword=keyword, additional=additional):
                    mutant = copy.deepcopy(self.schema)
                    end = mutant["$defs"]["runEnd"]
                    counts = end["properties"]["counts"]
                    if additional is None:
                        del counts["additionalProperties"]
                    else:
                        counts["additionalProperties"] = additional
                    expected = unavailable_paths(mutant)
                    branch = {"properties": {"note": unavailable}}
                    end.setdefault(keyword, {}).setdefault("properties", {})["counts"] = branch
                    probe = RESULTS.Schema({
                        "$schema": mutant["$schema"], "$defs": mutant["$defs"],
                        "allOf": [counts, branch],
                    })
                    value = dict.fromkeys(counts["required"], 0)
                    value["note"] = {"value": None, "unavailable": "prerequisite failed"}
                    self.assertEqual(allowed, not probe.errors(value))
                    self.assertEqual(expected | ({"counts.note"} if allowed else set()),
                                     unavailable_paths(mutant))

    def test_unavailable_provenance_paths_preserve_forbidden_children(self):
        for keyword in ("if", "then", "else"):
            for child in ("properties", "additionalProperties", "items"):
                with self.subTest(keyword=keyword, child=child):
                    mutant = copy.deepcopy(self.schema)
                    end = mutant["$defs"]["runEnd"]
                    nested = {"properties": {"detail": {"$ref": "#/$defs/unavailableProvenance"}}}
                    field = "issues" if child == "items" else "counts"
                    if child == "items":
                        end["properties"][field]["items"] = False
                    branch = {child: {"note": nested} if child == "properties" else nested}
                    end.setdefault(keyword, {}).setdefault("properties", {})[field] = branch
                    self.assertEqual(unavailable_paths(self.schema), unavailable_paths(mutant))

    def test_unavailable_provenance_paths_detect_missing_fields(self):
        mutant = copy.deepcopy(self.schema)
        mutant["$defs"]["provenance"]["properties"]["fixtureSha256"] = {"$ref": "#/$defs/sha256"}
        self.assertEqual(unavailable_paths(self.schema) - {"provenance.fixtureSha256"},
                         unavailable_paths(mutant))

    def test_suite_provenance_covers_every_suite(self):
        self.assertEqual(sorted(suite["id"] for suite in self.manifest["suites"]),
                         sorted(RESULTS.SUITE_PROVENANCE),
                         "SUITE_PROVENANCE must name exactly the manifest's suites; "
                         "update the table in scripts/editor-results.py")

    def test_schema_agrees_with_the_manifest(self):
        defs = self.schema["$defs"]
        self.assertEqual([s["id"] for s in self.manifest["suites"]], defs["suite"]["enum"])
        references = []

        def walk(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    references.append(node["$ref"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.schema)
        names = {ref.removeprefix("#/$defs/") for ref in references}
        self.assertEqual([], sorted(ref for ref in references if not ref.startswith("#/$defs/")))
        self.assertEqual([], sorted(names - set(defs)), "references to missing definitions")
        self.assertEqual([], sorted(set(defs) - names), "unused definitions")

    def test_a_passing_run_has_no_failures_unexpected_passes_or_skips(self):
        run_end = self.schema["$defs"]["runEnd"]
        self.assertEqual({"const": "pass"}, run_end["if"]["properties"]["status"],
                         "the run-end pass constraints must apply when status is pass")
        counts = run_end["then"]["properties"]["counts"]["properties"]
        for status in ["fail", "xpass", "skip"]:
            self.assertEqual({"const": 0}, counts.get(status),
                             f"a passing run must constrain counts.{status} to 0")


if __name__ == "__main__":
    unittest.main()
