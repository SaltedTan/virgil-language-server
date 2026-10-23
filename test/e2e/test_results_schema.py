#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Exercise result events with jsonschema's Draft 2020-12 validator."""
import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


class ResultsSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with Path(__file__).with_name("results-v1.schema.json").open(encoding="utf-8") as f:
            schema = json.load(f)
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def setUp(self):
        machine = {"os": "linux", "release": "6.8", "arch": "x86_64",
                   "cpu": None, "translation": None}
        self.event = {
            "schema": 1, "event": "run-start", "runId": "prerequisites", "seq": 0,
            "time": "2026-10-23T00:00:00Z", "release": None, "purpose": "development",
            "suite": "vscode", "setup": "vscode-linux", "lane": "local",
            "command": ["node", "test/e2e/run.cjs"], "coverageSha256": "a" * 64,
            "expected": [{"id": "vscode.infrastructure.preflight",
                          "profile": "wrapper", "iteration": 1}],
            "attestation": None,
            "provenance": {
                "server": {
                    "versionText": "virgil-lsp 0.1.0", "version": "0.1.0",
                    "sourceRevision": None, "virgilRevision": None,
                    "target": "x86-64-linux", "sha256": "b" * 64,
                    "workerSha256": "c" * 64,
                },
                "archive": None, "vsix": None, "clientMode": "source",
                "harness": {"revision": None, "tree": None, "dirty": False},
                "editor": {"name": "vscode", "version": None, "commit": None, "arch": None},
                "client": machine, "host": copy.deepcopy(machine),
                "transport": {"kind": "local", "topology": "local", "extensionHost": "local"},
                "pins": [], "configurationSha256": {}, "fixtureSha256": "d" * 64,
                "virgilRootSha256": None, "runtimes": {"node": "22"}, "ci": None,
            },
        }

    def prerequisite_fields(self):
        provenance = self.event["provenance"]
        return [(provenance["server"], key)
                for key in ("versionText", "target", "sha256", "workerSha256")] + [
                    (provenance, "fixtureSha256")]

    def test_complete_provenance_validates(self):
        self.validator.validate(self.event)

    def test_unavailable_prerequisite_provenance_validates(self):
        for parent, key in self.prerequisite_fields():
            with self.subTest(field=key):
                available = parent[key]
                parent[key] = {"value": None, "unavailable": "Prerequisite check failed"}
                self.validator.validate(self.event)
                parent[key] = available
        for parent, key in self.prerequisite_fields():
            parent[key] = {"value": None, "unavailable": "Server and fixture unavailable"}
        self.validator.validate(self.event)

    def test_unavailable_provenance_requires_a_reason_and_null_value(self):
        for parent, key in self.prerequisite_fields():
            available = parent[key]
            for invalid in [None, {"value": None}, {"value": None, "unavailable": ""},
                            {"unavailable": "Prerequisite check failed"},
                            {"value": available, "unavailable": "Prerequisite check failed"}]:
                with self.subTest(field=key, value=invalid):
                    parent[key] = invalid
                    self.assertFalse(self.validator.is_valid(self.event))
            parent[key] = available


if __name__ == "__main__":
    unittest.main()
