#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Tests scripts/editor-results.py, the validator of the editor tests' result
streams (RESULTS.md). It builds a passing stream for each automated suite from
coverage.json, beside this file, and breaks one rule at a time, so that the
streams follow the manifest as checks are added."""
import copy
import hashlib
import importlib.util
import json
import os
import re
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent.parent / "scripts" / "editor-results.py"


def load_script():
    spec = importlib.util.spec_from_file_location("editor_results", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


results = load_script()
MANIFEST_BYTES = (HERE / "coverage.json").read_bytes()
MANIFEST_DATA = json.loads(MANIFEST_BYTES)
SCHEMA_DATA = json.loads((HERE / "results-v1.schema.json").read_bytes())
CHECKS = {check["id"]: check for check in MANIFEST_DATA["checks"]}
PROFILES = {suite["id"]: {p["id"]: p for p in suite["profiles"]} for suite in MANIFEST_DATA["suites"]}
SETUPS = {setup["id"]: setup for setup in MANIFEST_DATA["setups"]}
RULE = MANIFEST_DATA["xfails"][0]
STATUSES = ("pass", "fail", "xfail", "xpass", "skip")
OTHER_COMMIT = "0" * 40
# The address space of the command that CommandTest runs, on Linux, so that
# a regression that sizes its work by a number in a stream fails rather than
# filling the machine's memory.
MEMORY = 1 << 30


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


# A passing run of each automated suite: its setup, the profiles it runs, the
# conditions that hold, each alternative's deciding fact, its client mode, and
# the repetitions of a repeated profile.
VARIANTS = {
    "nvim": dict(suite="nvim", setup="nvim-linux", profiles=("main", "wrapper"), conditions=(),
                 facts={}, client_mode="none"),
    "lazyvim": dict(suite="lazyvim", setup="lazyvim-linux",
                    profiles=("main", "noroot", "noroot-noice-workaround", "wrapper"),
                    conditions=("no-file-watching", "builtin-lsp-command"),
                    facts={"neovimBuiltinLspCommand": True,
                           "didChangeWatchedFilesDynamicRegistration": False}, client_mode="none"),
    "lazyvim-macos": dict(suite="lazyvim", setup="lazyvim-macos",
                          profiles=("main", "noroot", "noroot-noice-workaround", "wrapper"),
                          conditions=("file-watching", "lspconfig-restart-command"),
                          facts={"neovimBuiltinLspCommand": False,
                                 "didChangeWatchedFilesDynamicRegistration": True}, client_mode="none"),
    "vscode": dict(suite="vscode", setup="vscode-linux", profiles=("single-file", "workspace", "wrapper"),
                   conditions=(), facts={}, client_mode="source"),
    "vscode-vsix": dict(suite="vscode", setup="vscode-macos", profiles=("single-file", "workspace", "wrapper"),
                        conditions=("vsix",), facts={}, client_mode="vsix"),
    "remote-ssh": dict(suite="remote-ssh", setup="ssh-linux-client",
                       profiles=("probe", "single-file", "workspace", "wrapper"), conditions=("vsix",),
                       facts={}, client_mode="vsix", repeat=3),
}


def plan(suite, profiles, conditions, repeat=1):
    keys = []
    for check in MANIFEST_DATA["checks"]:
        if suite in check["suites"] and (check["when"] is None or check["when"] in conditions):
            for profile in check["profiles"]:
                if profile in profiles:
                    count = repeat if PROFILES[suite][profile]["repeated"] else 1
                    keys += [(check["id"], profile, iteration) for iteration in range(1, count + 1)]
    return sorted(keys)


def machine(os):
    return {"os": os, "release": "1", "arch": "x86_64", "cpu": None, "translation": None}


def provenance(suite, setup, profiles, client_mode, noice):
    editor = "vscode" if suite in ("vscode", "remote-ssh") else "nvim"
    transport = {"kind": "ssh", "topology": "loopback", "extensionHost": "remote"} if suite == "remote-ssh" \
        else {"kind": "local", "topology": "local", "extensionHost": "local" if editor == "vscode" else None}
    pins = [{"name": "LazyVim/starter", "version": None, "commit": "1" * 40, "sha256": None},
            {"name": RULE["plugin"], "version": None, "commit": noice, "sha256": None}] if suite == "lazyvim" else []
    return {
        "server": {"versionText": "virgil-lsp 0.1.0-alpha.3", "version": "0.1.0-alpha.3", "sourceRevision": None,
                   "virgilRevision": None, "target": "x86-64-linux", "sha256": sha("server"),
                   "workerSha256": sha("worker")},
        "archive": {"name": "virgil-lsp.tar.gz", "sha256": sha("archive"), "manifestSha256": sha("manifest")},
        "vsix": {"name": "virgil.vsix", "sha256": sha("vsix"), "version": "0.1.2"} if client_mode == "vsix" else None,
        "clientMode": client_mode,
        "harness": {"revision": "2" * 40, "tree": "3" * 40, "dirty": False},
        "editor": {"name": editor, "version": "1", "commit": None, "arch": "x86_64"},
        "client": machine(SETUPS[setup]["client"]),
        "host": machine(SETUPS[setup]["host"]),
        "transport": transport,
        "pins": pins,
        "configurationSha256": {profile: sha(profile) for profile in profiles if profile != "wrapper"},
        "fixtureSha256": sha("fixture"),
        "virgilRootSha256": sha("root"),
        "runtimes": {"python3": "3.12"},
        "ci": None,
    }


def check_event(key, status="pass"):
    check_id, profile, iteration = key
    return {"schema": 1, "event": "check", "runId": "run-1", "seq": 0, "time": "2026-10-24T00:00:02Z",
            "id": check_id, "profile": profile, "iteration": iteration, "status": status,
            "level": CHECKS[check_id]["level"] if check_id in CHECKS else "api", "startedAt": "2026-10-24T00:00:01.5Z", "durationMs": 500,
            "reason": None if status == "pass" else f"the check's {status}", "expectation": None,
            "failureCode": "failed" if status == "fail" else None, "observation": "observed", "artifacts": []}


def xfail(event):
    event.update(status="xfail", reason="noice.nvim drops window/showMessage", expectation=RULE["id"],
                 failureCode=RULE["failureCode"])
    return event


def stream(variant, noice=RULE["commit"]):
    """A passing run's events. In lazyvim, the noroot warning is an xfail if
    the noice pin is the rule's commit, and a pass otherwise."""
    v = VARIANTS[variant]
    keys = plan(v["suite"], v["profiles"], v["conditions"], v.get("repeat", 1))
    start = {"schema": 1, "event": "run-start", "runId": "run-1", "seq": 0, "time": "2026-10-24T00:00:00Z",
             "release": None, "purpose": "development", "suite": v["suite"], "setup": v["setup"], "lane": "local",
             "command": ["run.sh"], "coverageSha256": hashlib.sha256(MANIFEST_BYTES).hexdigest(),
             "expected": [{"id": i, "profile": p, "iteration": n} for i, p, n in keys],
             "facts": copy.deepcopy(v["facts"]),
             "provenance": provenance(v["suite"], v["setup"], v["profiles"], v["client_mode"], noice),
             "attestation": None}
    checks = [check_event(key) for key in keys]
    for event in checks:
        if (event["id"], event["profile"]) == (RULE["check"], RULE["profile"]) and noice == RULE["commit"]:
            xfail(event)
    end = {"schema": 1, "event": "run-end", "runId": "run-1", "seq": 0, "time": "2026-10-24T00:01:00Z",
           "complete": True, "status": "pass", "durationMs": 60000, "expectedCount": 0, "checkCount": 0,
           "counts": {}, "exitCode": 0, "signal": None, "cleanup": "verified", "issues": []}
    return rebuild([start] + checks + [end])


def manual_stream(editor="vscode", failed=False):
    """An attested partial selection of features and checklist steps."""
    events = stream("vscode" if editor == "vscode" else "nvim")
    start = events[0]
    start.update(suite="manual", command=["manual"], facts={},
                 expected=[{"feature": "hover"}] + ([{"step": 7}] if editor == "vscode" else []),
                 attestation={"tester": "Example tester", "observedAt": "2026-10-24T00:00:02Z",
                              "recordedAt": "2026-10-24T00:01:00Z",
                              "statement": "I observed the listed targets in this setup.", "reviewUrl": None})
    start["provenance"].update(clientMode="manual", configurationSha256={})
    observations = [{"schema": 1, "event": "observation", "runId": "run-1", "seq": 0,
                     "time": "2026-10-24T00:00:02Z", "target": copy.deepcopy(target),
                     "status": "fail" if failed and index == 0 else "pass", "level": "manual"}
                    for index, target in enumerate(start["expected"])]
    if failed:
        observations[0]["note"] = "The hover popup did not appear."
    return rebuild([start] + observations + events[-1:], status="fail" if failed else "pass")


def result_key(event):
    if "id" in event:
        return event["id"], event["profile"], event["iteration"]
    return tuple(sorted(event.get("target", event).items()))


def rebuild(events, **end):
    """Renumbers seq and recounts the run-end from the checks, then sets the
    run-end's fields in end. The run-end's status is unchanged unless given."""
    for seq, event in enumerate(events):
        event["seq"] = seq
    checks = [event for event in events if event["event"] in ("check", "observation")]
    planned = {result_key(key) for key in events[0]["expected"]}
    last = events[-1]
    last.update(expectedCount=len(events[0]["expected"]), checkCount=len(checks),
                counts={status: sum(check["status"] == status for check in checks) for status in STATUSES},
                complete=planned <= {result_key(c) for c in checks})
    last.update(end)
    return events


def find(events, check_id, profile=None, iteration=1):
    """The index of a check event."""
    for index, event in enumerate(events):
        if event["event"] == "check" and event["id"] == check_id and event["iteration"] == iteration \
                and (profile is None or event["profile"] == profile):
            return index
    raise LookupError(check_id)


def fail(events, index, failure="failed"):
    events[index].update(status="fail", reason="it failed", failureCode=failure, expectation=None)


def only(events, profiles):
    """Leaves out every profile but these, with its observations and digest."""
    events[0]["expected"] = [key for key in events[0]["expected"] if key["profile"] in profiles]
    events[1:-1] = [event for event in events[1:-1] if event["profile"] in profiles]
    provenance = events[0]["provenance"]
    provenance["configurationSha256"] = {profile: digest for profile, digest
                                         in provenance["configurationSha256"].items() if profile in profiles}
    return rebuild(events)


# The provenance of a VSIX that a run was given but couldn't read, and the
# profiles of the Remote-SSH runs that tests stop at their preflight for it:
# the launcher's alone, with the probe's, and every profile.
UNREADABLE_VSIX = {"value": None, "unavailable": "the VSIX isn't a ZIP archive, so it has no extension/package.json"}
UNREADABLE_VSIX_PROFILES = (("wrapper",), ("probe", "wrapper"), ("probe", "single-file", "workspace", "wrapper"))


def unreadable_vsix(profiles, vsix=UNREADABLE_VSIX):
    """A Remote-SSH run of these profiles, given a VSIX that it couldn't read:
    its preflight failed before it had the VSIX's metadata, it skipped every
    check after it but the teardown, which passed, and it created no
    configuration. vsix is what its provenance records for the VSIX."""
    events = only(stream("remote-ssh"), profiles)
    events.insert(1, events.pop(find(events, "ssh.infrastructure.preflight")))
    fail(events, 1, "unreadable-vsix")
    for event in events[2:-1]:
        if event["id"] != "ssh.infrastructure.teardown":
            event.update(status="skip", reason="the preflight failed", failureCode=None)
    events[0]["provenance"].update(vsix=copy.deepcopy(vsix), configurationSha256={})
    return rebuild(events, status="fail", exitCode=1)


def encode(events):
    return b"".join(json.dumps(event, separators=(",", ":")).encode() + b"\n" for event in events)


class ValidatorTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.manifest = results.Manifest.load()
        cls.schema = results.Schema.load()

    def validate(self, events, manifest=None):
        data = events if isinstance(events, bytes) else encode(events)
        return results.validate_stream(data, manifest or self.manifest, self.schema)

    def assertValid(self, events, passed=True):
        run = self.validate(events)
        self.assertEqual(passed, run.passed, run.summary())
        return run

    def assertInvalid(self, events, line, message, manifest=None):
        """Asserts that the stream is invalid, with its first error on the
        line, and a message that has the fragment."""
        with self.assertRaises(results.Invalid) as context:
            self.validate(events, manifest)
        error = context.exception
        self.assertIn(message, error.message)
        self.assertEqual(line, error.line, error.message)
        return error


class PassingStreamTest(ValidatorTest):
    def test_each_suite_passes(self):
        for variant in VARIANTS:
            with self.subTest(variant):
                events = stream(variant)
                run = self.assertValid(events)
                self.assertEqual(len(events) - 2, run.end["checkCount"])
                self.assertIn(", 0 fail, ", run.summary())

    def test_lazyvim_has_the_noice_xfail(self):
        run = self.assertValid(stream("lazyvim"))
        xfails = [(check["id"], check["profile"]) for check in run.checks if check["status"] == "xfail"]
        self.assertEqual([(RULE["check"], RULE["profile"])], xfails)
        self.assertEqual(1, run.end["counts"]["xfail"])

    def test_lazyvim_with_another_noice_commit_passes_the_warning(self):
        run = self.assertValid(stream("lazyvim", noice=OTHER_COMMIT))
        self.assertEqual(0, run.end["counts"]["xfail"])

    def test_vscode_plans_the_vsix_checks_only_from_a_vsix(self):
        for variant, planned in (("vscode", False), ("vscode-vsix", True)):
            with self.subTest(variant):
                ids = {check["id"] for check in self.assertValid(stream(variant)).checks}
                for check_id in ("vscode.startup.installed-copy", "vscode.infrastructure.vsix-install"):
                    self.assertEqual(planned, check_id in ids)

    def test_remote_ssh_repeats_its_launches(self):
        run = self.assertValid(stream("remote-ssh"))
        self.assertEqual({"probe": 1, "single-file": 3, "workspace": 3, "wrapper": 1}, run.plan.iterations)
        ids = {key[0] for key in run.plan.keys if key[2] == 3}
        self.assertIn("ssh.shutdown.remote-host-exit", ids)
        self.assertIn("vscode.recovery.server-path", ids)

    def test_check_order_is_free(self):
        events = stream("lazyvim")
        events[1:-1] = reversed(events[1:-1])
        self.assertValid(rebuild(events))

    def test_a_profile_may_be_left_out(self):
        # Which profiles a release requires comes with the gate.
        events = stream("lazyvim")
        events[0]["expected"] = [key for key in events[0]["expected"] if key["profile"] == "wrapper"]
        events[1:-1] = [event for event in events[1:-1] if event["profile"] == "wrapper"]
        events[0]["provenance"]["configurationSha256"] = {}
        self.assertValid(rebuild(events))


class ManualTest(ValidatorTest):
    def test_attested_features_and_checklist_steps(self):
        for editor in ("vscode", "nvim"):
            with self.subTest(editor):
                run = self.assertValid(manual_stream(editor))
                self.assertEqual([], run.checks)
                self.assertEqual({}, run.plan.iterations)
                self.assertEqual(len(run.plan.keys), len(run.observations))
        run = self.assertValid(manual_stream(failed=True), passed=False)
        self.assertEqual(1, run.end["counts"]["pass"])
        self.assertEqual(1, run.end["counts"]["fail"])

    def test_attestation_is_required_only_for_manual(self):
        for absent in (False, True):
            events = manual_stream()
            if absent:
                del events[0]["attestation"]
            else:
                events[0]["attestation"] = None
            self.assertInvalid(events, 1, "attestation")
        events = stream("vscode")
        events[0]["attestation"] = manual_stream()[0]["attestation"]
        self.assertInvalid(events, 1, "attestation")

    def test_event_kinds_do_not_mix(self):
        events = manual_stream()
        events[1] = stream("vscode")[1]
        self.assertInvalid(events, 2, "a manual run cannot contain check events")
        events = stream("vscode")
        events[1] = manual_stream()[1]
        self.assertInvalid(events, 2, "a vscode run cannot contain observation events")
        events = stream("vscode")
        events[0]["expected"] = [{"feature": "hover"}]
        self.assertInvalid(events, 1, "expected[0]")

    def test_plan_targets(self):
        events = manual_stream("nvim")
        events[0]["expected"].append({"step": 7})
        self.assertInvalid(events, 1, "a checklist step requires the vscode editor")
        events = manual_stream()
        events[0]["expected"] = []
        self.assertInvalid(events, 1, "expected: has 0 items, fewer than 1")
        events = manual_stream()
        events[0]["expected"][0] = {"feature": "startup"}
        self.assertInvalid(events, 1, "startup is not a matrix feature")
        for target in ({"step": 0}, {"step": 18}, {"step": True}, {"step": 7.0},
                       {"feature": "hover", "step": 7}, {"id": "vscode.hover.response"}):
            with self.subTest(target):
                events = manual_stream()
                events[0]["expected"] = [target]
                self.assertInvalid(events, 1, "expected[0]")
        events = manual_stream()
        events[0]["expected"].reverse()
        self.assertInvalid(events, 1, "expected[1] sorts before expected[0]")
        events = manual_stream()
        events[0]["expected"].insert(1, {"feature": "hover"})
        self.assertInvalid(events, 1, "expected[1] repeats expected[0]")
        # Steps compare numerically, features by code point, regardless of
        # the manifest's display order. There is no complete-coverage rule.
        events = manual_stream()
        events[0]["expected"] = [{"feature": "completion"}, {"feature": "diagnostics"},
                                  {"step": 2}, {"step": 10}, {"step": 17}]
        events[1:-1] = [dict(events[1], target=target) for target in events[0]["expected"]]
        self.assertValid(rebuild(events))
        events[0]["expected"][-2:] = reversed(events[0]["expected"][-2:])
        self.assertInvalid(events, 1, "expected[4] sorts before expected[3]")

    def test_observations_are_planned_and_unique(self):
        events = manual_stream()
        events[1]["target"] = {"feature": "definition"}
        self.assertInvalid(events, 2, "feature definition is not in the plan")
        events = manual_stream()
        events.insert(2, copy.deepcopy(events[1]))
        self.assertInvalid(rebuild(events), 3, "feature hover is observed again, after line 2")
        for change, message in (({"level": "api"}, "level"), ({"status": "skip"}, "status"),
                                ({"status": "xfail"}, "status"), ({"note": ""}, "note"),
                                ({"note": "x" * 1001}, "note")):
            with self.subTest(change=change):
                events = manual_stream()
                events[1].update(change)
                self.assertInvalid(events, 2, message)

    def test_run_end_counts_and_missing_targets(self):
        events = manual_stream()
        del events[1]
        self.assertValid(rebuild(events, status="aborted"), passed=False)
        events[-1]["complete"] = True
        self.assertInvalid(events, len(events), "feature hover has no observation")
        events = manual_stream()
        events[-1].update(complete=False, status="aborted")
        self.assertInvalid(events, len(events), "every planned observation has its observation")
        for field, value, message in (("checkCount", 3, "stream has 2 observations"),
                                       ("expectedCount", 3, "run-start plans 2 observations"),
                                       ("counts", dict.fromkeys(STATUSES, 0), "stream has 2 pass observations")):
            with self.subTest(field=field):
                events = manual_stream()
                events[-1][field] = value
                self.assertInvalid(events, len(events), message)
        events = manual_stream(failed=True)
        events[-1].update(status="pass", counts={**events[-1]["counts"], "pass": 2, "fail": 0})
        self.assertInvalid(events, len(events), "counts.pass is 2, but the stream has 1 pass observation")


class EncodingTest(ValidatorTest):
    def test_bytes(self):
        good = encode(stream("nvim"))
        first, rest = good.split(b"\n", 1)
        cases = [
            ("empty", b"", 1, "the stream is empty"),
            ("unterminated last line", good[:-1], good.count(b"\n"), "the last line doesn't end with LF"),
            ("CR LF", good.replace(b"\n", b"\r\n"), 1, "ends with CR LF"),
            ("CR inside", first.replace(b",", b",\r", 1) + b"\n" + rest, 1, "has a CR"),
            ("blank line", first + b"\n\n" + rest, 2, "is blank"),
            ("blank line at the end", good + b"\n", good.count(b"\n") + 1, "is blank"),
            ("space-only line", first + b"\n \t\n" + rest, 2, "is blank"),
            ("byte order mark", b"\xef\xbb\xbf" + good, 1, "starts with a byte order mark"),
            ("not UTF-8", first.replace(b'"local"', b'"loc\xe9l"') + b"\n" + rest, 1, "is not UTF-8"),
            ("UTF-16", good.decode().encode("utf-16-le"), 1, "is not JSON"),
            ("duplicate key", first.replace(b'{"schema":1,', b'{"schema":1,"schema":1,') + b"\n" + rest, 1,
             'the key "schema" is repeated'),
            ("NaN", first.replace(b'"schema":1', b'"schema":NaN') + b"\n" + rest, 1, "NaN is not JSON"),
            ("Infinity", first.replace(b'"schema":1', b'"schema":Infinity') + b"\n" + rest, 1,
             "Infinity is not JSON"),
            ("-Infinity", first.replace(b'"schema":1', b'"schema":-Infinity') + b"\n" + rest, 1,
             "-Infinity is not JSON"),
            ("overflow", first.replace(b'"schema":1', b'"schema":1e400') + b"\n" + rest, 1, "too large"),
            ("lone surrogate", first.replace(b'"local"', b'"\\ud800"') + b"\n" + rest, 1, "lone surrogate"),
            ("not JSON", b"{\n" + rest, 1, "is not JSON"),
            ("two objects", first + b" {}\n" + rest, 1, "is not JSON: Extra data"),
            ("array", b"[" + first + b"]\n" + rest, 1, "is not a JSON object"),
        ]
        for name, data, line, message in cases:
            with self.subTest(name):
                self.assertInvalid(data, line, message)

    def test_integer_literal_boundary(self):
        for sign in ("", "-"):
            with self.subTest(sign=sign):
                literal = sign + "9" * 20
                self.assertEqual(int(literal), results.parse_json(literal))
                data = encode(stream("nvim")).replace(b'"durationMs":500',
                                                      ('"durationMs":' + literal + "9").encode(), 1)
                self.assertInvalid(data, 2, "an integer has more than 20 digits")
        events = stream("nvim")
        events[1]["durationMs"] = 10 ** 20 - 1
        self.assertValid(events)

    def test_escaped_text_is_fine(self):
        events = stream("nvim")
        events[1]["observation"] = "caf\u00e9 \U0001f600 \u2028"
        self.assertValid(events)
        data = json.dumps(events[0], ensure_ascii=False).encode() + b"\n" + encode(events[1:])
        self.assertValid(data)


class OrderTest(ValidatorTest):
    def test_run_start_first(self):
        events = stream("nvim")
        events[0], events[1] = events[1], events[0]
        self.assertInvalid(events, 1, "the first event is a check, not a run-start")

    def test_second_run_start(self):
        events = stream("nvim")
        events.insert(2, copy.deepcopy(events[0]))
        self.assertInvalid(events, 3, "a second run-start")

    def test_run_end_last(self):
        for index, kind in ((1, "check"), (-1, "run-end")):
            with self.subTest(kind):
                events = stream("nvim")
                events.append(dict(events[index], seq=len(events)))
                self.assertInvalid(events, len(events), f"the {kind} comes after the run-end on line "
                                                        f"{len(events) - 1}")

    def test_same_run_id(self):
        events = stream("nvim")
        events[3]["runId"] = "run-2"
        self.assertInvalid(events, 4, 'runId is "run-2", but the run-start\'s is "run-1"')
        events = stream("nvim")
        events[-1]["runId"] = "run-2"
        self.assertInvalid(events, len(events), "runId is")

    def test_seq_counts_without_gaps(self):
        events = stream("nvim")
        events[0]["seq"] = 1
        self.assertInvalid(events, 1, "seq: 1 is not 0")
        events = stream("nvim")
        events[4]["seq"] = 5
        self.assertInvalid(events, 5, "seq is 5, not 4")
        events = stream("nvim")
        events[4]["seq"] = 3
        self.assertInvalid(events, 5, "seq is 3, not 4")

    def test_no_run_end(self):
        events = stream("nvim")[:-1]
        self.assertInvalid(events, len(events) + 1, "incomplete: no run-end")
        self.assertInvalid(stream("nvim")[:1], 2, "incomplete: no run-end")

    def test_killed_mid_line(self):
        data = encode(stream("nvim"))
        cut = data[:data.index(b"\n", len(data) // 2) + 20]
        self.assertInvalid(cut, cut.count(b"\n") + 1, "the last line doesn't end with LF")


class SchemaTest(ValidatorTest):
    """The schema's rules, through the validator's interpreter."""

    def test_event_errors(self):
        def mutate(index, change):
            events = stream("lazyvim")
            change(events[index])
            return events

        def delete(name):
            return lambda event: event.pop(name)

        def put(**fields):
            return lambda event: event.update(fields)

        def server(**fields):
            return lambda event: event["provenance"]["server"].update(fields)

        last = -1
        cases = [
            ("missing member", 0, delete("lane"), 'lacks the member "lane"'),
            ("unknown member", 2, put(extra=1), 'has the member "extra", which the schema doesn\'t allow'),
            ("unknown event", 2, put(event="note"), "is not"),
            ("wrong schema version", 2, put(schema=2), "schema: 2 is not 1"),
            ("fraction for an integer", 2, put(iteration=1.0), "iteration: 1.0 is not an integer"),
            ("boolean for an integer", 2, put(durationMs=True), "durationMs: true is not an integer"),
            ("fraction for a constant", 2, put(schema=1.0), "schema: 1.0 is not 1"),
            ("below the minimum", 2, put(durationMs=-1), "durationMs: -1 is less than 0"),
            ("pattern", 0, server(sha256="A" * 64), "provenance.server.sha256"),
            ("pattern before a final LF", 0, put(runId="run-1\n"), "runId"),
            ("too long", 0, put(runId="r" * 129), "longer than 128 characters"),
            ("empty", 0, put(lane=""), "lane: is empty"),
            ("no items", 0, put(command=[]), "command: has 0 items, fewer than 1"),
            ("enum", 2, put(status="ok"), 'status: "ok" is not one of'),
            ("unknown level", 2, put(level="visual"), "level"),
            ("time pattern", 2, put(time="2026-10-24 00:00:02Z"), "time"),
            ("time without a date", 2, put(startedAt="2026-02-30T00:00:00Z"), "startedAt: \"2026-02-30T00:00:00Z\" "
                                                                             "is not a date"),
            ("leap day", 2, put(startedAt="2027-02-29T00:00:00Z"), "is not a date"),
            ("pass with a failure code", 2, put(failureCode="failed"), "as the schema requires when status is"),
            ("fail without a reason", 2, put(status="fail", failureCode="failed"), "reason: null is not a string"),
            ("skip with an empty reason", 2, put(status="skip", reason=""), "reason: is empty"),
            ("fail with an expectation", 2, put(status="fail", reason="r", expectation=RULE["id"]),
             "expectation"),
            ("xpass with a failure code", 2,
             put(status="xpass", reason="r", expectation=RULE["id"], failureCode="failed"), "failureCode"),
            ("attestation outside manual", 0,
             put(attestation={"tester": "t", "observedAt": "2026-10-24T00:00:00Z",
                              "recordedAt": "2026-10-24T00:00:00Z", "statement": "s", "reviewUrl": None}),
             "attestation"),
            ("unavailable without a reason", 0, server(workerSha256={"value": None}), "workerSha256"),
            ("unavailable with a value", 0, server(sha256={"value": sha("x"), "unavailable": "r"}), "sha256"),
            ("unavailable vsix without a reason", 0,
             lambda event: event["provenance"].update(vsix={"value": None, "unavailable": ""}), "provenance.vsix"),
            ("unavailable elsewhere", 0,
             lambda event: event["provenance"].update(virgilRootSha256={"value": None, "unavailable": "r"}),
             "virgilRootSha256"),
            ("artifact path", 2, put(artifacts=[{"path": "../log", "sha256": sha("log")}]), "artifacts[0].path"),
            ("run-end pass with an exit code", last, put(exitCode=1), "exitCode: 1 is not 0"),
            ("run-end pass with issues", last, put(issues=["late"]), "issues: has 1 item, more than 0"),
            ("run-end pass with a fail count", last,
             lambda event: event["counts"].update(fail=1), "counts.fail: 1 is not 0"),
            ("signal", last, put(signal="TERM"), "signal"),
        ]
        for name, index, change, message in cases:
            with self.subTest(name):
                events = mutate(index, change)
                line = len(events) if index == last else index + 1
                self.assertInvalid(events, line, message)


class PlanTest(ValidatorTest):
    def drop(self, events, check_id, profile=None):
        """Removes a check from the plan and its event."""
        events[0]["expected"] = [key for key in events[0]["expected"]
                                 if key["id"] != check_id or profile not in (None, key["profile"])]
        events[1:-1] = [event for event in events[1:-1]
                        if event["id"] != check_id or profile not in (None, event["profile"])]
        return rebuild(events)

    def add(self, events, *keys):
        """Adds observations to the plan, sorted, and passing checks for them."""
        for key in keys:
            events[0]["expected"].append(dict(zip(("id", "profile", "iteration"), key)))
            events.insert(-1, check_event(key))
        events[0]["expected"].sort(key=lambda k: (k["id"], k["profile"], k["iteration"]))
        return rebuild(events)

    def test_missing_check(self):
        events = self.drop(stream("nvim"), "nvim.hover.response")
        self.assertInvalid(events, 1, "expected lacks nvim.hover.response in profile main, iteration 1, "
                                      "a check of suite nvim in that profile")

    def test_missing_profile_of_a_check(self):
        events = self.drop(stream("lazyvim"), "lazyvim.startup.ui-ready", "noroot")
        self.assertInvalid(events, 1, "expected lacks lazyvim.startup.ui-ready in profile noroot")

    def test_both_alternatives(self):
        events = self.add(stream("lazyvim"), ("lazyvim.configuration.disk-watch", "main", 1))
        self.assertInvalid(events, 1, "expected has checks of both file-watching and its alternative "
                                      "no-file-watching")

    def test_neither_alternative(self):
        events = self.drop(stream("lazyvim"), "lazyvim.restart.builtin")
        self.assertInvalid(events, 1, "of neither builtin-lsp-command nor its alternative")

    def test_alternatives_without_their_profile(self):
        events = stream("lazyvim")
        keep = {"noroot", "wrapper"}
        events[0]["expected"] = [key for key in events[0]["expected"] if key["profile"] in keep]
        events[1:-1] = [event for event in events[1:-1] if event["profile"] in keep]
        events[0]["provenance"]["configurationSha256"] = {"noroot": sha("noroot")}
        self.assertValid(rebuild(events))

    def test_each_restart_side_follows_its_fact(self):
        # 0.12-style: the built-in :lsp command exists, so the run plans its
        # checks. 0.11-style: it doesn't, so the run plans :LspRestart's.
        self.assertValid(stream("lazyvim"))
        self.assertValid(stream("lazyvim-macos"))
        events = stream("lazyvim")
        events[0]["facts"]["neovimBuiltinLspCommand"] = False
        self.assertInvalid(events, 1, "expected plans the builtin-lsp-command checks, but "
                                      "facts.neovimBuiltinLspCommand is false, which selects "
                                      "lspconfig-restart-command")
        events = stream("lazyvim-macos")
        events[0]["facts"]["neovimBuiltinLspCommand"] = True
        self.assertInvalid(events, 1, "expected plans the lspconfig-restart-command checks, but "
                                      "facts.neovimBuiltinLspCommand is true, which selects "
                                      "builtin-lsp-command")

    def test_file_watching_follows_the_declared_capability(self):
        # true declares file watching; false and absent don't.
        for value in (True, False, None):
            with self.subTest(value=value):
                events = stream("lazyvim" if value is not True else "lazyvim-macos")
                events[0]["facts"]["didChangeWatchedFilesDynamicRegistration"] = value
                self.assertValid(events)
        events = stream("lazyvim")
        events[0]["facts"]["didChangeWatchedFilesDynamicRegistration"] = True
        self.assertInvalid(events, 1, "expected plans the no-file-watching checks, but "
                                      "facts.didChangeWatchedFilesDynamicRegistration is true, which selects "
                                      "file-watching")
        for value in (False, None):
            with self.subTest(value=value):
                events = stream("lazyvim-macos")
                events[0]["facts"]["didChangeWatchedFilesDynamicRegistration"] = value
                self.assertInvalid(events, 1, "expected plans the file-watching checks, but "
                                              "facts.didChangeWatchedFilesDynamicRegistration is "
                                              + {False: "false", None: "null"}[value]
                                              + ", which selects no-file-watching")

    def test_a_missing_fact(self):
        events = stream("lazyvim")
        del events[0]["facts"]["neovimBuiltinLspCommand"]
        self.assertInvalid(events, 1, "expected plans the builtin-lsp-command checks, but facts lacks "
                                      "neovimBuiltinLspCommand")
        events = stream("lazyvim-macos")
        del events[0]["facts"]["didChangeWatchedFilesDynamicRegistration"]
        self.assertInvalid(events, 1, "expected plans the file-watching checks, but facts lacks "
                                      "didChangeWatchedFilesDynamicRegistration")

    def test_an_unknown_fact(self):
        events = stream("lazyvim")
        events[0]["facts"]["builtinLsp"] = True
        self.assertInvalid(events, 1, 'facts: has the member "builtinLsp", which the schema doesn\'t allow')

    def test_a_fact_value_that_no_side_takes(self):
        data = json.loads(MANIFEST_BYTES)
        for condition in data["conditions"]:
            if condition["id"] == "no-file-watching":
                condition["decidedBy"]["values"] = [False]
        manifest = results.Manifest(json.dumps(data).encode())
        events = stream("lazyvim")
        events[0]["coverageSha256"] = manifest.sha256
        events[0]["facts"]["didChangeWatchedFilesDynamicRegistration"] = None
        self.assertInvalid(events, 1, "facts.didChangeWatchedFilesDynamicRegistration is null, but no side of "
                                      "file-watching and no-file-watching takes it", manifest=manifest)

    def test_an_unavailable_fact_keeps_a_run_from_passing(self):
        events = stream("lazyvim")
        events[0]["facts"]["neovimBuiltinLspCommand"] = {"value": None,
                                                          "unavailable": "couldn't test for :lsp"}
        self.assertInvalid(events, len(events), "status is pass, but the run-start's "
                                                "facts.neovimBuiltinLspCommand is unavailable")
        self.assertValid(rebuild(events, status="unavailable"), passed=False)

    def test_an_unneeded_fact_is_allowed(self):
        # The nvim suite plans no side of either pair, so it needs no fact,
        # but recording one that its plan doesn't use is not an error.
        events = stream("nvim")
        events[0]["facts"]["neovimBuiltinLspCommand"] = True
        self.assertValid(events)

    def test_part_of_a_condition(self):
        events = self.drop(stream("vscode-vsix"), "vscode.startup.installed-copy", "workspace")
        self.assertInvalid(events, 1, "expected lacks vscode.startup.installed-copy in profile workspace, "
                                      "iteration 1, which has the condition vsix")
        events = self.drop(stream("vscode-vsix"), "vscode.infrastructure.vsix-install")
        self.assertInvalid(events, 1, "expected lacks vscode.infrastructure.vsix-install")

    def test_condition_and_provenance_agree(self):
        vsix = stream("vscode-vsix")[0]["provenance"]["vsix"]
        for variant, fields, message in (
                ("vscode-vsix", {"clientMode": "source", "vsix": None},
                 'expected has the vsix checks, but provenance.clientMode is "source"'),
                ("vscode-vsix", {"vsix": None}, "expected has the vsix checks, but provenance.vsix is null"),
                ("vscode", {"clientMode": "vsix"},
                 'expected lacks the vsix checks, but provenance.clientMode is "vsix"'),
                ("vscode", {"vsix": vsix}, "expected lacks the vsix checks, but provenance.vsix is an object")):
            with self.subTest(message):
                events = stream(variant)
                events[0]["provenance"].update(fields)
                self.assertInvalid(events, 1, message)
        # Without the checks in its profiles, the plan doesn't say whether the
        # condition held, but the provenance still must agree with itself.
        events = only(stream("vscode"), {"wrapper"})
        events[0]["provenance"]["clientMode"] = "vsix"
        self.assertInvalid(events, 1, 'provenance.clientMode is "vsix", but provenance.vsix is null: '
                                      "clientMode is vsix exactly when vsix is not null")
        events[0]["provenance"]["vsix"] = vsix
        self.assertValid(events)

    def test_a_condition_that_always_holds(self):
        # coverage.json says that every Remote-SSH run installs the VSIX.
        events = stream("remote-ssh")
        for check_id, check in CHECKS.items():
            if check["when"] == "vsix":
                events = self.drop(events, check_id)
        events[0]["provenance"].update(clientMode="source", vsix=None)
        self.assertInvalid(events, 1, "expected lacks vscode.startup.installed-copy in profile single-file, "
                                      "iteration 1, which has the condition vsix: vsix holds in every run of "
                                      "suite remote-ssh")
        events = self.drop(stream("remote-ssh"), "vscode.startup.installed-copy", "workspace")
        self.assertInvalid(events, 1, "expected lacks vscode.startup.installed-copy in profile workspace")
        # Its provenance must say so, whichever profiles the run has.
        for profiles in (("probe", "single-file", "workspace", "wrapper"), ("probe", "wrapper")):
            for fields, found in (({"clientMode": "source", "vsix": None}, 'provenance.clientMode is "source"'),
                                  ({"vsix": None}, "provenance.vsix is null")):
                with self.subTest(profiles=profiles, found=found):
                    events = only(stream("remote-ssh"), profiles)
                    self.assertValid(copy.deepcopy(events))
                    events[0]["provenance"].update(fields)
                    self.assertInvalid(events, 1, f"vsix holds in every run of suite remote-ssh, but {found}")

    def test_check_of_another_suite(self):
        events = self.add(stream("nvim"), ("lazyvim.hover.response", "main", 1))
        self.assertInvalid(events, 1, "lazyvim.hover.response is not a check of suite nvim")
        events = self.add(stream("vscode"), ("ssh.identity.remote-host", "workspace", 1))
        self.assertInvalid(events, 1, "ssh.identity.remote-host is not a check of suite vscode")

    def test_unknown_and_retired_checks(self):
        events = self.add(stream("nvim"), ("nvim.hover.popup", "main", 1))
        self.assertInvalid(events, 1, "nvim.hover.popup is not a check in the manifest")
        data = json.loads(MANIFEST_BYTES)
        data["retired"].append("nvim.hover.popup")
        manifest = results.Manifest(json.dumps(data).encode())
        events[0]["coverageSha256"] = manifest.sha256
        with self.assertRaisesRegex(results.Invalid, "nvim.hover.popup is a retired check"):
            self.validate(events, manifest)

    def test_wrong_profile(self):
        events = self.add(stream("nvim"), ("nvim.hover.response", "wrapper", 1))
        self.assertInvalid(events, 1, "nvim.hover.response doesn't run in profile wrapper")
        # A shared check's profile must be one of the run's suite.
        events = self.add(stream("vscode"), ("ssh.identity.remote-host", "probe", 1))
        self.assertInvalid(events, 1, "not a check of suite vscode")
        events = self.add(stream("remote-ssh"), ("vscode.infrastructure.launch", "workspace", 1))
        self.assertInvalid(events, 1, "vscode.infrastructure.launch is not a check of suite remote-ssh")

    def test_iteration_of_a_profile_that_isnt_repeated(self):
        events = self.add(stream("nvim"), ("nvim.hover.response", "main", 2))
        self.assertInvalid(events, 1, "profile main isn't repeated, so its only iteration is 1, not 2")
        events = stream("remote-ssh")
        events[0]["expected"] = [dict(key, iteration=2) if key["profile"] == "probe" else key
                                 for key in events[0]["expected"]]
        self.assertInvalid(events, 1, "profile probe isn't repeated")

    def test_iteration_gap(self):
        events = stream("remote-ssh")
        events[0]["expected"] = [key for key in events[0]["expected"] if key["iteration"] != 2]
        events[1:-1] = [event for event in events[1:-1] if event["iteration"] != 2]
        self.assertInvalid(rebuild(events), 1, "expected has profile single-file in iterations 1, 3, "
                                               "without iteration 2")

    def test_iteration_missing_a_check(self):
        events = stream("remote-ssh")
        events[0]["expected"] = [key for key in events[0]["expected"]
                                 if (key["id"], key["iteration"]) != ("vscode.hover.response", 3)]
        self.assertInvalid(events, 1, "expected lacks vscode.hover.response in profile workspace, iteration 3")

    def test_repeated_profiles_repeat_together(self):
        events = stream("remote-ssh")
        events[0]["expected"] = [key for key in events[0]["expected"]
                                 if (key["profile"], key["iteration"]) != ("workspace", 3)]
        self.assertInvalid(events, 1, "expected has 3 iterations of profile single-file, but 2 of profile "
                                      "workspace")

    def test_sorted_by_id_profile_iteration(self):
        expected = stream("remote-ssh")[0]["expected"]

        def first(differs):
            """The first index whose key differs from the one before it only by differs."""
            return next(i for i in range(1, len(expected)) if differs(expected[i - 1], expected[i]))

        def same_id(a, b):
            return a["id"] == b["id"]

        for name, index in (("id", first(lambda a, b: not same_id(a, b))),
                            ("profile", first(lambda a, b: same_id(a, b) and a["profile"] != b["profile"])),
                            ("iteration", first(lambda a, b: same_id(a, b) and a["profile"] == b["profile"]))):
            with self.subTest(name):
                events = stream("remote-ssh")
                keys = events[0]["expected"]
                keys[index - 1], keys[index] = keys[index], keys[index - 1]
                self.assertInvalid(events, 1, f"expected[{index}] sorts before expected[{index - 1}]")

    def test_duplicate_key(self):
        events = stream("nvim")
        events[0]["expected"].insert(3, dict(events[0]["expected"][3]))
        self.assertInvalid(events, 1, "expected[4] repeats expected[3]")


class CheckTest(ValidatorTest):
    def test_unplanned_observation(self):
        events = stream("nvim")
        events.insert(-1, check_event(("nvim.hover.response", "wrapper", 1)))
        self.assertInvalid(rebuild(events), len(events) - 1,
                           "nvim.hover.response in profile wrapper, iteration 1 is not in the plan")
        events = stream("lazyvim")
        events.insert(-1, check_event(("lazyvim.configuration.disk-watch", "main", 1)))
        self.assertInvalid(rebuild(events), len(events) - 1, "is not in the plan")

    def test_repeated_observation(self):
        events = stream("nvim")
        events.insert(-1, copy.deepcopy(events[2]))
        self.assertInvalid(rebuild(events), len(events) - 1, "is observed again, after line 3")
        events = stream("remote-ssh")
        index = find(events, "vscode.hover.response", iteration=2)
        events[index]["iteration"] = 1
        self.assertInvalid(events, index + 1, "vscode.hover.response in profile workspace, iteration 1 is "
                                              "observed again")

    def test_level(self):
        events = stream("nvim")
        events[2]["level"] = "ui-state"
        self.assertInvalid(events, 3, f"level is ui-state, but the manifest gives {events[2]['id']} the level api")

    def test_skip_follows_a_failure(self):
        events = stream("lazyvim")
        index = find(events, "lazyvim.hover.popup")
        events[index].update(status="skip", reason="hover failed")
        self.assertInvalid(rebuild(events, status="fail"), index + 1, "is skipped, but no check before it failed")
        fail(events, index - 1)
        self.assertValid(rebuild(events, status="fail"), passed=False)
        # A failure in another profile, such as the wrapper's, can skip a check.
        events = stream("lazyvim")
        index = find(events, "lazyvim.hover.popup")
        events[index].update(status="skip", reason="no plugins")
        events.insert(1, events.pop(find(events, "lazyvim.infrastructure.plugins-install")))
        fail(events, 1)
        self.assertValid(rebuild(events, status="fail"), passed=False)
        # A later failure isn't one that the skip followed, nor is an xfail.
        events = stream("lazyvim")
        index = find(events, "lazyvim.hover.popup")
        events[index].update(status="skip", reason="hover failed")
        fail(events, index + 1)
        self.assertInvalid(rebuild(events, status="fail"), index + 1, "no check before it failed")
        events = stream("lazyvim")
        warning = events.pop(find(events, RULE["check"], RULE["profile"]))
        index = find(events, "lazyvim.shutdown.rootless-clean", RULE["profile"])
        events.insert(index, warning)
        events[index + 1].update(status="skip", reason="no warning")
        self.assertInvalid(rebuild(events, status="fail"), index + 2, "no check before it failed")


class ExpectedFailureTest(ValidatorTest):
    def warning(self, events, profile=RULE["profile"]):
        return find(events, RULE["check"], profile)

    def test_xfail_in_another_profile(self):
        events = stream("lazyvim")
        index = self.warning(events, RULE["workaroundProfile"])
        xfail(events[index])
        self.assertInvalid(rebuild(events), index + 1, f"rule {RULE['id']} applies only in profile "
                                                       f"{RULE['profile']}, not {RULE['workaroundProfile']}")

    def test_xfail_of_another_check(self):
        events = stream("lazyvim")
        index = find(events, "lazyvim.hover.popup")
        xfail(events[index])
        self.assertInvalid(rebuild(events), index + 1, f"rule {RULE['id']} is for {RULE['check']}, "
                                                       "not lazyvim.hover.popup")

    def test_xfail_with_another_pin(self):
        events = stream("lazyvim", noice=OTHER_COMMIT)
        index = self.warning(events)
        xfail(events[index])
        self.assertInvalid(rebuild(events), index + 1, f"applies only with {RULE['plugin']} at {RULE['commit']}, "
                                                       f"but the run's pins have {RULE['plugin']} at {OTHER_COMMIT}")
        for pins, found in (([], f"no pin of {RULE['plugin']}"), (None, f"{RULE['plugin']} at no commit")):
            with self.subTest(found):
                events = stream("lazyvim")
                if pins is None:
                    events[0]["provenance"]["pins"][1]["commit"] = None
                else:
                    events[0]["provenance"]["pins"] = pins
                self.assertInvalid(events, self.warning(events) + 1, f"but the run's pins have {found}")

    def test_xfail_with_another_failure_code(self):
        events = stream("lazyvim")
        index = self.warning(events)
        events[index]["failureCode"] = "lua-error"
        self.assertInvalid(events, index + 1, f"excuses only the failure code {RULE['failureCode']}, not lua-error")

    def test_xfail_with_another_expectation(self):
        events = stream("lazyvim")
        index = self.warning(events)
        events[index]["expectation"] = "noice-1074"
        self.assertInvalid(events, index + 1, "expectation noice-1074 is not an expected-failure rule")

    def test_xfail_without_its_prerequisites(self):
        for required in RULE["requires"]:
            for status in ("fail", "skip", "missing"):
                with self.subTest(required=required, status=status):
                    events = stream("lazyvim")
                    index = self.warning(events)
                    prerequisite = find(events, required, RULE["profile"])
                    if status == "missing":
                        del events[prerequisite]
                        message = "it has no check"
                    else:
                        fail(events, prerequisite)
                        if status == "skip":
                            events[prerequisite].update(status="skip", failureCode=None)
                            fail(events, 1)
                        message = f"it is a {status}, on line {prerequisite + 1}"
                    index = self.warning(events)
                    self.assertInvalid(rebuild(events, status="fail"), index + 1,
                                       f"excuses {RULE['check']} only if {required} in profile {RULE['profile']}, "
                                       f"iteration 1 passed, but {message}")

    def test_prerequisites_in_another_profile_dont_count(self):
        events = stream("lazyvim")
        prerequisite = find(events, RULE["requires"][0], RULE["profile"])
        fail(events, prerequisite)
        fail(events, find(events, RULE["requires"][0], RULE["workaroundProfile"]))
        # Only the same profile's prerequisites count: the fail in the other profile doesn't matter,
        # and the passing one in the workaround profile doesn't excuse this one.
        events[prerequisite].update(status="pass", reason=None, failureCode=None)
        self.assertValid(rebuild(events, status="fail"), passed=False)
        fail(events, prerequisite)
        events[find(events, RULE["requires"][0], RULE["workaroundProfile"])].update(
            status="pass", reason=None, failureCode=None)
        self.assertInvalid(rebuild(events, status="fail"), self.warning(events) + 1, "only if")

    def test_prerequisites_may_come_after_the_xfail(self):
        events = stream("lazyvim")
        events.insert(1, events.pop(self.warning(events)))
        self.assertValid(rebuild(events))

    def test_plain_pass_where_the_rule_applies(self):
        events = stream("lazyvim")
        index = self.warning(events)
        events[index].update(status="pass", reason=None, expectation=None, failureCode=None)
        self.assertInvalid(rebuild(events), index + 1, f"passed with {RULE['plugin']} at {RULE['commit']}, "
                                                       f"where rule {RULE['id']} makes a pass an xpass")

    def test_xpass_fails_the_run(self):
        events = stream("lazyvim")
        index = self.warning(events)
        events[index].update(status="xpass", reason="the warning was shown", failureCode=None)
        run = self.assertValid(rebuild(events, status="fail"), passed=False)
        self.assertEqual(1, run.end["counts"]["xpass"])
        self.assertInvalid(rebuild(events, status="pass"), len(events), "counts.xpass: 1 is not 0")
        # An xpass needs the rule as much as an xfail does.
        events = stream("lazyvim", noice=OTHER_COMMIT)
        index = self.warning(events)
        events[index].update(status="xpass", reason="r", expectation=RULE["id"])
        self.assertInvalid(rebuild(events, status="fail"), index + 1, "applies only with")

    def test_fail_where_the_rule_applies(self):
        events = stream("lazyvim")
        index = self.warning(events)
        fail(events, index, RULE["failureCode"])
        self.assertInvalid(rebuild(events, status="fail"), index + 1, f"{RULE['check']} failed with "
                                                                      f"{RULE['failureCode']}, where rule "
                                                                      f"{RULE['id']} makes it an xfail")
        # Another failure, or a failed prerequisite, is a fail.
        fail(events, index, "lua-error")
        self.assertValid(rebuild(events, status="fail"), passed=False)
        fail(events, index, RULE["failureCode"])
        fail(events, find(events, RULE["requires"][1], RULE["profile"]))
        self.assertValid(rebuild(events, status="fail"), passed=False)
        # So is the warning not observed in the workaround profile, or with another pin.
        events = stream("lazyvim")
        fail(events, self.warning(events, RULE["workaroundProfile"]), RULE["failureCode"])
        self.assertValid(rebuild(events, status="fail"), passed=False)
        events = stream("lazyvim", noice=OTHER_COMMIT)
        fail(events, self.warning(events), RULE["failureCode"])
        self.assertValid(rebuild(events, status="fail"), passed=False)

    def test_rule_comes_from_the_manifest(self):
        data = json.loads(MANIFEST_BYTES)
        data["xfails"][0]["commit"] = OTHER_COMMIT
        manifest = results.Manifest(json.dumps(data).encode())
        events = stream("lazyvim", noice=OTHER_COMMIT)
        index = self.warning(events)
        xfail(events[index])
        events[0]["coverageSha256"] = manifest.sha256
        self.assertTrue(self.validate(rebuild(events), manifest).passed)
        events = stream("lazyvim")
        events[0]["coverageSha256"] = manifest.sha256
        with self.assertRaisesRegex(results.Invalid, "applies only with"):
            self.validate(events, manifest)


class RunEndTest(ValidatorTest):
    def test_coverage_sha256(self):
        events = stream("nvim")
        events[0]["coverageSha256"] = sha("another manifest")
        self.assertInvalid(events, 1, f"coverageSha256 is {sha('another manifest')}, but the manifest's is "
                                      f"{self.manifest.sha256}: the run was planned with another coverage.json")
        data = MANIFEST_BYTES + b"\n"
        with self.assertRaisesRegex(results.Invalid, "planned with another coverage.json"):
            self.validate(stream("nvim"), results.Manifest(data))

    def test_counts(self):
        cases = [
            ("expectedCount", {"expectedCount": 20}, "expectedCount is 20, but the run-start plans 30 observations"),
            ("checkCount", {"checkCount": 23}, "checkCount is 23, but the stream has 30 checks"),
            ("counts.pass", {"counts": dict.fromkeys(STATUSES, 0)}, "counts.pass is 0, but the stream has 30 "
                                                                     "pass checks"),
        ]
        for name, fields, message in cases:
            with self.subTest(name):
                events = rebuild(stream("nvim"), **fields)
                self.assertInvalid(events, len(events), message)
        events = stream("nvim")
        fail(events, 3)
        events = rebuild(events, status="fail")
        events[-1]["counts"].update({"pass": 30, "fail": 0})
        self.assertInvalid(events, len(events), "counts.pass is 30, but the stream has 29 pass checks")
        events[-1]["counts"].update({"pass": 29, "skip": 1})
        self.assertInvalid(events, len(events), "counts.fail is 0, but the stream has 1 fail check")

    def test_complete(self):
        events = stream("nvim")
        del events[5]
        rebuild(events, complete=True, status="fail")
        self.assertInvalid(events, len(events), "complete is true, but nvim.")
        rebuild(events, complete=False, status="fail")
        self.assertValid(events, passed=False)
        rebuild(stream("nvim"))
        events = rebuild(stream("nvim"), complete=False, status="aborted")
        self.assertInvalid(events, len(events), "complete is false, but every planned observation has its check")

    def test_pass_requires_everything(self):
        def missing(events):
            del events[3]

        def failure(events):
            fail(events, 3)

        def skip(events):
            fail(events, 2)
            events[3].update(status="skip", reason="r")

        def xpass(events):
            events[find(events, RULE["check"], RULE["profile"])].update(status="xpass", failureCode=None)

        def unavailable(name):
            def change(events):
                where = events[0]["provenance"]
                where = where if name == "fixtureSha256" else where["server"]
                where[name] = {"value": None, "unavailable": "the worker is missing"}
            return change

        def end(**fields):
            return lambda events: events[-1].update(fields)

        n = len(stream("lazyvim")) - 2

        cases = [
            ("missing observation", missing, {}, "complete: false is not true"),
            ("missing but complete", missing, {"complete": True}, "complete is true, but"),
            ("fail", failure, {}, "counts.fail: 1 is not 0"),
            ("fail uncounted", failure, {"counts": {"pass": n - 1, "fail": 0, "xfail": 1, "xpass": 0, "skip": 0}},
             f"counts.pass is {n - 1}, but the stream has {n - 2} pass checks"),
            ("skip", skip, {}, "counts.fail: 1 is not 0"),
            ("xpass", xpass, {}, "counts.xpass: 1 is not 0"),
            ("exit code", end(exitCode=1), {}, "exitCode: 1 is not 0"),
            ("no exit code", end(exitCode=None), {}, "exitCode: null is not 0"),
            ("signal", end(signal="SIGKILL"), {}, 'signal: "SIGKILL" is not null'),
            ("cleanup failed", end(cleanup="failed"), {}, 'cleanup: "failed" is not "verified"'),
            ("cleanup unknown", end(cleanup="unknown"), {}, 'cleanup: "unknown" is not "verified"'),
            ("issues", end(issues=["the deadline passed"]), {}, "issues: has 1 item, more than 0"),
        ] + [(f"unavailable {name}", unavailable(name), {},
              f"status is pass, but the run-start's provenance.{'' if name == 'fixtureSha256' else 'server.'}"
              f"{name} is unavailable")
             for name in ("versionText", "target", "sha256", "workerSha256", "fixtureSha256")]
        for name, change, fields, message in cases:
            with self.subTest(name):
                events = stream("lazyvim")
                change(events)
                events = rebuild(events, **fields)
                self.assertEqual("pass", events[-1]["status"])
                self.assertInvalid(events, len(events), message)

    def test_runs_that_dont_pass(self):
        """Valid streams of unsuccessful runs."""
        # A failed run, with a skip after the failure.
        events = stream("vscode")
        fail(events, 2)
        events[3].update(status="skip", reason="the extension didn't activate")
        run = self.assertValid(rebuild(events, status="fail"), passed=False)
        self.assertIn("valid, run failed", run.summary())
        # An aborted run, stopped by a signal before it had every check.
        events = stream("remote-ssh")[:40] + stream("remote-ssh")[-1:]
        events = rebuild(events, status="aborted", exitCode=None, signal="SIGTERM", cleanup="unknown",
                         issues=["interrupted"])
        self.assertFalse(events[-1]["complete"])
        self.assertIn("valid, run aborted (39 of", self.assertValid(events, passed=False).summary())
        # An unavailable run: no worker, so no worker digest, and its checks
        # after the preflight skipped.
        events = stream("nvim")
        events[0]["provenance"]["server"]["workerSha256"] = {"value": None, "unavailable": "no worker"}
        events.insert(1, events.pop(find(events, "nvim.infrastructure.preflight")))
        fail(events, 1)
        for event in events[2:-1]:
            event.update(status="skip", reason="the preflight failed", failureCode=None)
        events = rebuild(events, status="unavailable", exitCode=2)
        run = self.assertValid(events, passed=False)
        self.assertEqual(["provenance.server.workerSha256"], run.unavailable)
        # Unavailable provenance is enough to keep a run from passing.
        events = stream("nvim")
        events[0]["provenance"]["fixtureSha256"] = {"value": None, "unavailable": "not copied"}
        self.assertValid(rebuild(events, status="unavailable"), passed=False)

    def test_status_says_pass_when_the_run_passed(self):
        for status in ("fail", "aborted", "unavailable"):
            with self.subTest(status):
                events = rebuild(stream("nvim"), status=status)
                self.assertInvalid(events, len(events), f"status is {status}, but the run meets every condition "
                                                        "of a pass")
        # Each condition alone keeps a run from passing.
        for name, fields in (("issues", {"issues": ["the deadline passed"]}), ("cleanup", {"cleanup": "unknown"}),
                             ("exit code", {"exitCode": 1}), ("signal", {"exitCode": None, "signal": "SIGTERM"})):
            with self.subTest(name):
                self.assertValid(rebuild(stream("nvim"), status="fail", **fields), passed=False)


class ProvenanceTest(ValidatorTest):
    def test_setup(self):
        events = stream("nvim")
        events[0]["setup"] = "nvim-freebsd"
        self.assertInvalid(events, 1, "setup nvim-freebsd is not in the manifest")
        events[0]["setup"] = "lazyvim-linux"
        self.assertInvalid(events, 1, "setup lazyvim-linux is a setup of suite lazyvim, not nvim")
        events[0]["setup"] = "nvim-macos"
        self.assertInvalid(events, 1, "provenance.client.os is linux, but setup nvim-macos has a macos client")
        events = stream("remote-ssh")
        events[0]["provenance"]["host"]["os"] = "macos"
        self.assertInvalid(events, 1, "provenance.host.os is macos, but setup ssh-linux-client has a linux host")

    def test_pins_have_unique_names(self):
        events = stream("lazyvim")
        pins = events[0]["provenance"]["pins"]
        pins.append(dict(pins[1], commit=OTHER_COMMIT))
        self.assertInvalid(events, 1, f'provenance.pins[2] repeats the name "{RULE["plugin"]}"')

    def test_client_mode_and_vsix_agree(self):
        vsix = stream("vscode-vsix")[0]["provenance"]["vsix"]
        # A VS Code run that plans only the wrapper's checks has no vsix
        # checks, so no plan rule can catch contradictory provenance.
        events = only(stream("vscode"), {"wrapper"})
        events[0]["provenance"]["vsix"] = copy.deepcopy(vsix)
        self.assertInvalid(events, 1, 'provenance.clientMode is "source", but provenance.vsix is an object: '
                                      "clientMode is vsix exactly when vsix is not null")
        events = only(stream("vscode"), {"wrapper"})
        events[0]["provenance"]["clientMode"] = "vsix"
        self.assertInvalid(events, 1, 'provenance.clientMode is "vsix", but provenance.vsix is null: '
                                      "clientMode is vsix exactly when vsix is not null")
        # A VSIX that a run couldn't read is still a VSIX, in a Neovim run too.
        for variant in ("nvim", "lazyvim"):
            with self.subTest(variant):
                events = stream(variant)
                events[0]["provenance"]["vsix"] = copy.deepcopy(UNREADABLE_VSIX)
                self.assertInvalid(events, 1, f'provenance.clientMode is "{VARIANTS[variant]["client_mode"]}", '
                                              "but provenance.vsix is an object: clientMode is vsix exactly when "
                                              "vsix is not null")

    def test_client_mode_and_editor_agree_with_the_suite(self):
        # A Neovim suite's run uses no extension, and a VS Code one installs it.
        for variant in ("nvim", "lazyvim"):
            with self.subTest(variant):
                events = stream(variant)
                events[0]["provenance"]["clientMode"] = "source"
                self.assertInvalid(events, 1, 'provenance.clientMode is "source", but a run of suite '
                                              f"{VARIANTS[variant]['suite']} records none")
        events = stream("vscode")
        events[0]["provenance"]["clientMode"] = "none"
        self.assertInvalid(events, 1, 'provenance.clientMode is "none", but a run of suite vscode records '
                                      "source or vsix")
        # The editor follows the suite, in a run with vsix checks and one without.
        for variant, editor in (("vscode", "nvim"), ("vscode-vsix", "nvim"), ("nvim", "vscode"),
                                ("lazyvim", "vscode")):
            with self.subTest(variant):
                events = stream(variant)
                events[0]["provenance"]["editor"]["name"] = editor
                suite = VARIANTS[variant]["suite"]
                self.assertInvalid(events, 1, f'provenance.editor.name is "{editor}", but a run of suite {suite} '
                                              f"uses {'nvim' if suite in ('nvim', 'lazyvim') else 'vscode'}")

    def test_manual_mode_is_the_manual_suites_alone(self):
        # An automated suite's run can't call itself manual to skip the
        # agreement: the client mode is manual exactly when the suite is.
        for variant in ("nvim", "lazyvim", "vscode"):
            with self.subTest(variant):
                events = stream(variant)
                events[0]["provenance"].update(clientMode="manual", vsix=None)
                self.assertInvalid(events, 1, 'provenance.clientMode is "manual", but a run of suite '
                                              f"{VARIANTS[variant]['suite']} records "
                                              + ("none" if variant in ("nvim", "lazyvim") else "source or vsix"))
        # The Remote-SSH plan holds its vsix condition in every run, which
        # rejects a manual mode first, so check its provenance directly.
        start = stream("remote-ssh")[0]
        start["provenance"].update(clientMode="manual", vsix=None)
        with self.assertRaises(results.Invalid) as context:
            results.check_suite_provenance(start)
        self.assertEqual('line 1: provenance.clientMode is "manual", but a run of suite remote-ssh records vsix',
                         str(context.exception))

        # A manual record is of a person's own setup, in either editor, and
        # records no VSIX. Check the provenance table directly too.
        def manual_start(editor, vsix=None):
            start = stream("nvim")[0]
            start["suite"] = "manual"
            start["provenance"].update(clientMode="manual", vsix=vsix)
            start["provenance"]["editor"]["name"] = editor
            return start

        for editor in ("nvim", "vscode"):
            with self.subTest(editor):
                results.check_suite_provenance(manual_start(editor))
        start = manual_start("nvim", copy.deepcopy(UNREADABLE_VSIX))
        with self.assertRaises(results.Invalid) as context:
            results.check_suite_provenance(start)
        self.assertEqual('line 1: provenance.clientMode is "manual", but provenance.vsix is an object: '
                         "clientMode is vsix exactly when vsix is not null", str(context.exception))
        start = manual_start("nvim")
        start["provenance"]["clientMode"] = "none"
        with self.assertRaises(results.Invalid) as context:
            results.check_suite_provenance(start)
        self.assertEqual('line 1: provenance.clientMode is "none", but a run of suite manual records manual',
                         str(context.exception))

    def test_a_suite_that_the_table_lacks_is_an_error(self):
        # The manifest may add a suite before the script's table has it. The
        # stream then gets an ordinary error that names the suite, not a
        # KeyError, and test_coverage.py catches the drift first.
        manifest = results.Manifest(MANIFEST_BYTES.replace(b'"nvim"', b'"nvim-next"'))
        schema_data = copy.deepcopy(SCHEMA_DATA)
        schema_data["$defs"]["suite"]["enum"].append("nvim-next")
        schema = results.Schema(schema_data)
        events = stream("nvim")
        events[0].update(suite="nvim-next", coverageSha256=manifest.sha256)
        with self.assertRaises(results.Invalid) as context:
            results.validate_stream(encode(events), manifest, schema)
        self.assertEqual(1, context.exception.line)
        self.assertIn("suite nvim-next is not in SUITE_PROVENANCE", context.exception.message)

    def test_configuration_digests(self):
        events = stream("lazyvim")
        digests = events[0]["provenance"]["configurationSha256"]
        digests[RULE["workaroundProfile"]] = digests["noroot"]
        self.assertInvalid(events, 1, f"gives the workaround profile {RULE['workaroundProfile']} the digest of "
                                      "profile noroot")
        # A passing run has a digest for each profile but the wrapper, since a check ran in each.
        del digests[RULE["workaroundProfile"]]
        line = next(index for index, event in enumerate(events)
                    if event["event"] == "check" and event["profile"] == RULE["workaroundProfile"]) + 1
        self.assertInvalid(events, 1, f"provenance.configurationSha256 lacks profile {RULE['workaroundProfile']}, "
                                      f"but the check on line {line} ran in it")
        events = stream("nvim")
        events[0]["provenance"]["configurationSha256"]["wrapper"] = sha("wrapper")
        self.assertInvalid(events, 1, "provenance.configurationSha256 has profile wrapper, which runs outside "
                                      "the editor")
        events = stream("nvim")
        events[0]["provenance"]["configurationSha256"]["noroot"] = sha("noroot")
        self.assertInvalid(events, 1, "has profile noroot, which the plan doesn't have")

    def preflight_failed(self):
        """A LazyVim run whose preflight failed before it created any editor
        configuration: it skipped every check but the cleanup's, and has no
        configuration digests."""
        events = stream("lazyvim")
        events.insert(1, events.pop(find(events, "lazyvim.infrastructure.preflight")))
        fail(events, 1)
        for event in events[2:-1]:
            if event["id"] != "lazyvim.infrastructure.cleanup":
                event.update(status="skip", reason="Neovim is missing", expectation=None, failureCode=None)
        provenance = events[0]["provenance"]
        provenance.update(configurationSha256={}, pins=[],
                          fixtureSha256={"value": None, "unavailable": "the preflight failed"})
        provenance["editor"]["version"] = None
        return rebuild(events, status="unavailable", exitCode=2)

    def test_a_profile_in_which_no_check_ran_may_lack_its_digest(self):
        run = self.assertValid(self.preflight_failed(), passed=False)
        self.assertIn("valid, run unavailable", run.summary())
        self.assertEqual(1, run.end["counts"]["fail"])
        self.assertEqual(run.end["checkCount"] - 2, run.end["counts"]["skip"])
        # A digest that is there is still checked, against the others that are there.
        events = self.preflight_failed()
        events[0]["provenance"]["configurationSha256"] = {RULE["workaroundProfile"]: sha("noroot")}
        self.assertValid(events, passed=False)
        events[0]["provenance"]["configurationSha256"]["noroot"] = sha("noroot")
        self.assertInvalid(events, 1, f"gives the workaround profile {RULE['workaroundProfile']} the digest")
        # A check that wasn't skipped ran in its profile, whether it passed or not.
        for status in ("pass", "fail"):
            with self.subTest(status):
                events = self.preflight_failed()
                index = find(events, "lazyvim.hover.response", "main")
                if status == "fail":
                    fail(events, index)
                else:
                    events[index].update(status="pass", reason=None)
                self.assertInvalid(rebuild(events), 1, "provenance.configurationSha256 lacks profile main, but "
                                                       f"the check on line {index + 1} ran in it")
        # Nor did a check run that the stream lacks, in a run that stopped before it.
        events = stream("lazyvim")
        later = ("noroot", RULE["workaroundProfile"])
        events[1:-1] = [event for event in events[1:-1] if event["profile"] not in later]
        for profile in later:
            del events[0]["provenance"]["configurationSha256"][profile]
        self.assertValid(rebuild(events, status="aborted", exitCode=None, signal="SIGTERM", cleanup="unknown"),
                         passed=False)

    def test_a_vsix_that_cant_be_read(self):
        # A Remote-SSH run still used a VSIX, so its vsix is unavailable,
        # never null, and its client mode is still vsix.
        for profiles in UNREADABLE_VSIX_PROFILES:
            with self.subTest(profiles=profiles):
                run = self.assertValid(unreadable_vsix(profiles), passed=False)
                self.assertEqual(["provenance.vsix"], run.unavailable)
                self.assertInvalid(unreadable_vsix(profiles, vsix=None), 1,
                                   "vsix holds in every run of suite remote-ssh, but provenance.vsix is null")
                events = unreadable_vsix(profiles)
                events[0]["provenance"]["clientMode"] = "source"
                self.assertInvalid(events, 1, "vsix holds in every run of suite remote-ssh, but "
                                              'provenance.clientMode is "source"')

    def test_an_unavailable_vsix_keeps_a_run_from_passing(self):
        # Wherever the vsix checks are planned, as in a VS Code run from a VSIX.
        for variant in ("remote-ssh", "vscode-vsix"):
            with self.subTest(variant):
                events = stream(variant)
                events[0]["provenance"]["vsix"] = copy.deepcopy(UNREADABLE_VSIX)
                self.assertInvalid(events, len(events), "status is pass, but the run-start's provenance.vsix is "
                                                        "unavailable")
                self.assertValid(rebuild(events, status="unavailable"), passed=False)


def mini(schema):
    """A schema document for the interpreter, with the definitions that the
    validator requires."""
    return dict(schema, **{"$schema": results.DRAFT,
                           "$defs": {"unavailableProvenance": {}, "time": {}}})


class InterpreterTest(unittest.TestCase):
    def test_refuses_other_keywords(self):
        for keyword, value in (("format", "date-time"), ("$comment", "x"), ("unevaluatedProperties", False),
                               ("patternProperties", {}), ("exclusiveMinimum", 0), ("anyOf", [{}]),
                               ("not", {}), ("uniqueItems", True), ("$anchor", "a"), ("default", 1)):
            with self.subTest(keyword):
                document = copy.deepcopy(SCHEMA_DATA)
                document["$defs"]["time"][keyword] = value
                with self.assertRaisesRegex(results.Unusable, f"has the keyword {re.escape(keyword)}, which this "
                                                              "script doesn't interpret"):
                    results.Schema(document)
        for name, change in (("$schema", lambda d: d.update({"$schema": "http://json-schema.org/draft-07/schema#"})),
                             ("tuple items", lambda d: d["$defs"]["runStart"]["properties"]["command"].update(
                                 items=[{}])),
                             ("remote $ref", lambda d: d["oneOf"][0].update({"$ref": "other.json#/x"})),
                             ("dangling $ref", lambda d: d["oneOf"][0].update({"$ref": "#/$defs/nothing"})),
                             ("no unavailable", lambda d: d["$defs"].pop("unavailableProvenance")),
                             ("untranslatable pattern", lambda d: d["$defs"]["runId"].update(pattern="^\\S+$"))):
            with self.subTest(name):
                document = copy.deepcopy(SCHEMA_DATA)
                change(document)
                with self.assertRaises(results.Unusable):
                    results.Schema(document)

    def test_integer(self):
        schema = results.Schema(mini({"type": "integer", "minimum": 1}))
        self.assertEqual([], schema.errors(1))
        self.assertEqual([], schema.errors(10 ** 30))
        for value in (1.0, True, "1", None, 0):
            with self.subTest(value):
                self.assertNotEqual([], schema.errors(value))
        number = results.Schema(mini({"type": "number"}))
        self.assertEqual([], number.errors(1.5))
        self.assertNotEqual([], number.errors(False))

    def test_one_of_means_exactly_one(self):
        schema = results.Schema(mini({"oneOf": [{"type": "integer"}, {"minimum": 0}]}))
        self.assertEqual([], schema.errors(-1))
        self.assertEqual([], schema.errors(0.5))
        self.assertEqual(["matches 2 of the oneOf schemas, not exactly one"], schema.errors(1))
        self.assertNotEqual([], schema.errors(-0.5))

    def test_all_of_and_if(self):
        schema = results.Schema(mini({
            "type": "object",
            "allOf": [{"if": {"properties": {"a": {"const": 1}}}, "then": {"required": ["b"]},
                       "else": {"properties": {"b": {"type": "null"}}}}, {"maxItems": 0}],
        }))
        self.assertEqual([], schema.errors({"a": 1, "b": 2}))
        self.assertEqual([], schema.errors({"a": 2, "b": None}))
        self.assertNotEqual([], schema.errors({"a": 2, "b": 2}))
        # An absent member satisfies its properties, so the if holds, and then requires b.
        self.assertEqual(['lacks the member "b", as the schema requires when a is 1'], schema.errors({}))

    def test_properties(self):
        schema = results.Schema(mini({"properties": {"a": {"const": [1, {"b": None}]}},
                                      "propertyNames": {"pattern": "^[a-c]$"},
                                      "additionalProperties": {"enum": ["x", 2]}}))
        self.assertEqual([], schema.errors({"a": [1, {"b": None}], "b": "x", "c": 2}))
        self.assertEqual([], schema.errors([1]))
        for value in ({"a": [1.0, {"b": None}]}, {"a": [1, {"b": None, "c": 1}]}, {"b": 2.0}, {"d": "x"},
                      {"b": "y"}):
            with self.subTest(value):
                self.assertNotEqual([], schema.errors(value))

    def test_pattern_searches(self):
        schema = results.Schema(mini({"pattern": "b."}))
        self.assertEqual([], schema.errors("abc"))
        self.assertEqual(["\"ab\" doesn't match b."], schema.errors("ab"))

    def test_patterns_are_ecma(self):
        def search(pattern, text):
            return re.search(results.translate(pattern), text) is not None

        cases = [
            ("^a$", "a", True), ("^a$", "a\n", False), ("a", "bab", True), ("^a", "ba", False),
            ("^.$", "\r", False), ("^.$", "\u2028", False), ("^.$", "\u00e9", True), ("^.$", "\U0001f600", True),
            ("^\\d$", "\u0663", False), ("^\\w$", "\u00e9", False), ("^\\W$", "\u00e9", True),
            ("^[\\d.]+$", "1.2", True), ("^[^a-c]$", "d", True), ("^[^a-c]$", "b", False),
            ("^a{2,3}$", "aaaa", False), ("^a{2,}$", "aaaa", True), ("^(?:ab)+?$", "abab", True),
            ("^[a-]$", "-", True), ("^[-a]$", "-", True), ("^\\u0041$", "A", True), ("^\\/\\.$", "/.", True),
            ("^[#]$", "#", True), ("^ $", " ", True), ("^a|b$", "xb", True),
        ]
        for pattern, text, matches in cases:
            with self.subTest(pattern=pattern, text=text):
                self.assertEqual(matches, search(pattern, text))
        for pattern in ("\\s", "\\b", "(?=a)", "(?!a)", "(?<=a)", "(a)\\1", "(?<n>a)", "\\p{L}", "\\x41", "\\cA",
                        "\\0", "\\u{41}", "\\ud800", "{", "a{2", "}", "]", "a**", "*", "(a", "a)", "[a", "[]",
                        "[b-a]", "[\\d-a]", "a{3,2}", "\\", "\\a", "^*"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ValueError):
                    results.translate(pattern)

    def test_every_schema_pattern_translates(self):
        patterns = set()

        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get("pattern"), str):
                    patterns.add(value["pattern"])
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(SCHEMA_DATA)
        self.assertGreaterEqual(len(patterns), 8)
        for pattern in patterns:
            re.compile(results.translate(pattern))


class CommandTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def write(self, name, data):
        path = self.directory / name
        path.write_bytes(data if isinstance(data, bytes) else encode(data))
        return name

    def run_command(self, *args, env=None):
        def limit():
            resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))

        # From another directory, so the manifest and schema are found beside the script.
        result = subprocess.run([sys.executable, str(SCRIPT), "validate", *args], cwd=self.directory,
                                capture_output=True, text=True, env=env, preexec_fn=limit if sys.platform == "linux" else None)
        return result.returncode, result.stdout, result.stderr

    def test_exit_statuses_and_output(self):
        passed = self.write("passed.jsonl", stream("lazyvim"))
        events = stream("nvim")
        fail(events, 2)
        failed = self.write("failed.jsonl", rebuild(events, status="fail"))
        invalid = self.write("invalid.jsonl", stream("nvim")[:-1])
        n = len(stream("lazyvim")) - 2
        passed_line = (f"{passed}: valid, run passed ({n} of {n} observations): {n - 1} pass, 0 fail, 1 xfail, "
                       "0 xpass, 0 skip\n")
        failed_line = f"{failed}: valid, run failed (30 of 30 observations): 29 pass, 1 fail, 0 xfail, 0 xpass, 0 skip\n"
        invalid_line = f"{invalid}: invalid: line 32: incomplete: no run-end\n"
        self.assertEqual((0, passed_line, ""), self.run_command(passed))
        self.assertEqual((1, failed_line, ""), self.run_command(failed))
        self.assertEqual((2, invalid_line, ""), self.run_command(invalid))
        self.assertEqual((1, passed_line + failed_line, ""), self.run_command(passed, failed))
        self.assertEqual((2, invalid_line + passed_line + failed_line, ""),
                         self.run_command(invalid, passed, failed))
        self.assertEqual((2, f"{failed_line}missing.jsonl: invalid: can't read it: No such file or directory\n", ""),
                         self.run_command(failed, "missing.jsonl"))

    def test_long_integer_with_python_limit_disabled(self):
        data = encode(stream("nvim")).replace(b'"durationMs":500', b'"durationMs":' + b"9" * 5000, 1)
        name = self.write("long-integer.jsonl", data)
        self.assertEqual((2, f"{name}: invalid: line 2: an integer has more than 20 digits\n", ""),
                         self.run_command(name, env={**os.environ, "PYTHONINTMAXSTRDIGITS": "0"}))

    def test_a_huge_iteration(self):
        # Finding the first missing iteration doesn't count up to the last.
        events = stream("remote-ssh")
        events[0]["expected"] = [{"id": "ssh.identity.remote-host", "profile": "single-file", "iteration": 10 ** 19}]
        huge = self.write("huge.jsonl", events[:1])
        passed = self.write("passed.jsonl", stream("nvim"))
        self.assertEqual((2, f"{huge}: invalid: line 1: expected has profile single-file in iterations {10 ** 19}, "
                             "without iteration 1\n"
                             f"{passed}: valid, run passed (30 of 30 observations): 30 pass, 0 fail, 0 xfail, "
                             "0 xpass, 0 skip\n", ""),
                         self.run_command(huge, passed))

    def test_a_vsix_that_cant_be_read(self):
        # Remote-SSH runs stopped at their preflight: valid failed runs, unless
        # they record the VSIX as null.
        for vsix, code in ((UNREADABLE_VSIX, 1), (None, 2)):
            with self.subTest(vsix=vsix):
                names, out = [], ""
                for profiles in UNREADABLE_VSIX_PROFILES:
                    events = unreadable_vsix(profiles, vsix)
                    names.append(self.write("-".join(profiles) + ".jsonl", events))
                    n = len(events) - 2
                    out += f"{names[-1]}: " + (
                        f"valid, run failed ({n} of {n} observations): 1 pass, 1 fail, 0 xfail, 0 xpass, {n - 2} skip\n"
                        if vsix else "invalid: line 1: vsix holds in every run of suite remote-ssh, but provenance.vsix "
                                     "is null\n")
                self.assertEqual((code, out, ""), self.run_command(*names))

    def test_options(self):
        manifest = self.write("coverage.json", MANIFEST_BYTES + b" ")
        events = stream("nvim")
        events[0]["coverageSha256"] = hashlib.sha256(MANIFEST_BYTES + b" ").hexdigest()
        planned = self.write("planned.jsonl", events)
        self.assertEqual(0, self.run_command("--manifest", manifest, planned)[0])
        code, out, _ = self.run_command(planned)
        self.assertEqual(2, code)
        self.assertIn("the run was planned with another coverage.json", out)
        document = copy.deepcopy(SCHEMA_DATA)
        document["$defs"]["time"]["format"] = "date-time"
        schema = self.write("schema.json", json.dumps(document).encode())
        code, out, err = self.run_command("--schema", schema, planned)
        self.assertEqual((2, ""), (code, out))
        self.assertIn("has the keyword format, which this script doesn't interpret", err)
        self.assertEqual(2, self.run_command()[0])


if __name__ == "__main__":
    unittest.main()
