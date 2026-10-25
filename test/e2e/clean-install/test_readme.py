#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Keep install examples current and their substitution points unambiguous."""
import json
from pathlib import Path
import re
import unittest

from run import examples, extract, substitute

REPO = Path(__file__).resolve().parents[3]


class ReadmeTests(unittest.TestCase):
    def setUp(self):
        self.steps = extract(REPO)
        self.values = examples(self.steps)

    def test_release_versions(self):
        self.assertEqual(self.values["server_version"], (REPO / "VERSION").read_text().strip(),
                         "README server examples and config_url tags must match VERSION")
        package = json.loads((REPO / "clients/vscode/package.json").read_text())
        self.assertEqual(self.values["extension_version"], package["version"],
                         "README extension_version must match clients/vscode/package.json")

    def test_next_release_substitutions(self):
        # A release preparation changes every example before its tag exists.
        steps = {key: body.replace(self.values["server_version"], "8.9.0-alpha.4").replace(
            "extension_version=" + self.values["extension_version"], "extension_version=7.8.9")
            for key, body in self.steps.items()}
        values = examples(steps)
        actual = substitute(steps, values, "9.0.0-alpha.1", "8.0.0", "http://127.0.0.1:12345")
        self.assertIn("server_version=9.0.0-alpha.1\n", actual["server"])
        self.assertIn("extension_version=8.0.0\n", actual["vsix-download"])
        self.assertIn("download_url=http://127.0.0.1:12345\n", actual["server"])
        for step in ["lazyvim", "plain-nvim"]:
            self.assertIn("config_url=http://127.0.0.1:12345/editors/nvim\n", actual[step])
        for step in ["vscode-root", "nvim-root"]:
            self.assertEqual(actual[step], 'export VIRGIL_LOC="$HOME/.local/opt/'
                             'virgil-lsp-9.0.0-alpha.1-x86-64-linux/virgil"\n')

    def test_releasing_example_versions(self):
        markdown = (REPO / "docs/releasing.md").read_text()
        server = (REPO / "VERSION").read_text().strip()
        extension = json.loads((REPO / "clients/vscode/package.json").read_text())["version"]
        version = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?"
        for section in ["Verifying a download", "Installing"]:
            body = markdown.split(f"## {section}\n", 1)[1].split("\n## ", 1)[0]
            servers = re.findall(rf"gh release download v({version})\b", body)
            servers += re.findall(rf"virgil-lsp-({version})-x86-64-", body)
            extensions = re.findall(rf"virgil-lsp-vscode-({version})(?:-linux-x64)?\.vsix", body)
            self.assertTrue(servers, f"no server examples found in {section}")
            self.assertTrue(extensions, f"no extension examples found in {section}")
            for actual in servers:
                self.assertEqual(actual, server, f"{section}: server examples must match VERSION")
            for actual in extensions:
                self.assertEqual(actual, extension,
                                 f"{section}: extension examples must match clients/vscode/package.json")

    def test_reject_malformed_or_duplicate_values(self):
        for step, assignment in [("server", "server_version"), ("server", "download_url"),
                                 ("vsix-download", "extension_version"), ("lazyvim", "config_url"),
                                 ("plain-nvim", "config_url"), ("vscode-root", "export VIRGIL_LOC"),
                                 ("nvim-root", "export VIRGIL_LOC")]:
            for mutation in ["missing", "malformed", "duplicate"]:
                with self.subTest(step=step, assignment=assignment, mutation=mutation):
                    steps = dict(self.steps)
                    line = next(line for line in steps[step].splitlines() if line.startswith(assignment + "="))
                    replacement = {"missing": "", "malformed": assignment + "=unexpected",
                                   "duplicate": line + "\n" + assignment + "=unexpected"}[mutation]
                    steps[step] = steps[step].replace(line, replacement)
                    with self.assertRaisesRegex(ValueError, f"README step {step}:"):
                        examples(steps)

    def test_reject_inconsistent_server_versions(self):
        for step in ["lazyvim", "plain-nvim", "vscode-root", "nvim-root"]:
            with self.subTest(step=step):
                steps = dict(self.steps)
                steps[step] = steps[step].replace(self.values["server_version"], "8.9.0-alpha.4")
                with self.assertRaisesRegex(ValueError, f"README step {step}: server version"):
                    examples(steps)


if __name__ == "__main__":
    unittest.main()
