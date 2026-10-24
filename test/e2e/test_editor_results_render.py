#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Tests scripts/editor-results.py render, which renders a release's results
tables from the streams that a selection lists (RESULTS.md, "Rendering a
release"). It builds the streams with test_editor_results.py's builder, from
coverage.json, and the documents in memory. It also checks that the example
in results-example/ is up to date; with --write-example, it regenerates it."""
import contextlib
import hashlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import test_editor_results as builder
from test_editor_results import RULE, encode, fail, find, only, rebuild, results, stream

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent.parent / "scripts" / "editor-results.py"
EXAMPLE = HERE / "results-example"
EXAMPLE_RELEASE = "0.0.0-example"
RELEASE = "0.1.0-alpha.4"
MANIFEST = results.Manifest.load()
SCHEMA = results.Schema.load()
NOICE = "missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074))"
KNOWN_LIMITATION = f"Known limitation: {NOICE}; workaround profile separate"


def run_of(variant, number=1, lane="local", release=RELEASE, **start):
    """A passing run of a builder variant, with its own run ID, a lane, and the release."""
    events = stream(variant)
    for event in events:
        event["runId"] = f"run-{number}"
    events[0].update(release=release, lane=lane, **start)
    return events


def at_version(events, version):
    """The run's events at an editor version. A LazyVim run on Neovim 0.11,
    which has no built-in :lsp command, restarts with :LspRestart."""
    events[0]["provenance"]["editor"]["version"] = version
    if events[0]["suite"] == "lazyvim" and (version or "").startswith("0.11."):
        rename = {"lazyvim.restart.builtin": "lazyvim.restart.legacy"}
        for event in events[1:-1]:
            event["id"] = rename.get(event["id"], event["id"])
        events[0]["expected"] = sorted(({**key, "id": rename.get(key["id"], key["id"])}
                                        for key in events[0]["expected"]),
                                       key=lambda key: (key["id"], key["profile"], key["iteration"]))
    return rebuild(events)


def leg(version, number=1, **start):
    """A passing run of LazyVim on Linux in the lazyvim-linux lane, the CI job
    that runs a leg at each pinned Neovim version."""
    return at_version(run_of("lazyvim", number, lane="lazyvim-linux", **start), version)


def validated(events):
    return results.validate_stream(encode(events), MANIFEST, SCHEMA)


def cells(*streams):
    """The matrix's cells, by setup and feature, from these streams."""
    runs = [validated(events) for events in streams]
    return {(setup["id"], feature): results.cell_text(setup, feature, runs, MANIFEST)
            for setup in MANIFEST.setups.values() for feature in MANIFEST.features}


def row(events, release=RELEASE):
    return results.run_cells(validated(events), release, MANIFEST)


def document(release=RELEASE, runs="", matrix="", before="", after=""):
    """A document with the release's section and its markers, the blocks' old
    interiors, and prose before and after them."""
    return (f"# Manual testing\n\nProse with a | pipe, a <!-- comment -->, and caf\u00e9.  \n\n## Results\n{before}\n"
            f"### {release}\n\nHand-written prose.\n\n"
            f"<!-- editor-results:{release}:runs:begin -->\n{runs}<!-- editor-results:{release}:runs:end -->\n\n"
            "More prose, which stays.\n\n"
            f"<!-- editor-results:{release}:matrix:begin -->\n{matrix}<!-- editor-results:{release}:matrix:end -->\n"
            f"{after}\n### 0.1.0-alpha.3\n\nPast results stay hand-written.\n")


def matrix_row(text, label):
    """A setup's cells in a document's rendered matrix, by feature."""
    line = next(line for line in text.split("\n") if line.startswith(f"| {label} |"))
    return dict(zip(MANIFEST.features, line[2:-2].split(" | ")[1:]))


def unescaped_pipes(line):
    """The number of a table row's pipes that end a cell."""
    return len(re.findall(r"(?<!\\)\|", line))


def call(*args):
    """Runs the script's main in this process: its exit status, stdout, and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = results.main(list(args))
        except SystemExit as exit:
            status = exit.code
    return status, out.getvalue(), err.getvalue()


class RenderTest(unittest.TestCase):
    """Renders into a document in a temporary directory, from streams that it writes there."""
    maxDiff = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.doc = self.directory / "manual-testing.md"
        self.doc.write_text(document())

    def select(self, *streams, release=RELEASE, directory="results", names=None, entries=None):
        """Writes the streams, each events or bytes, and a selection that lists
        them, or the entries if given. Returns the selection's path."""
        where = self.directory / directory
        where.mkdir(parents=True, exist_ok=True)
        listed = []
        for index, events in enumerate(streams):
            name = names[index] if names else f"stream-{index}.jsonl"
            data = events if isinstance(events, bytes) else encode(events)
            (where / name).parent.mkdir(parents=True, exist_ok=True)
            (where / name).write_bytes(data)
            listed.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
        selection = where / "selection.json"
        selection.write_text(json.dumps({"version": 1, "release": release,
                                         "streams": listed if entries is None else entries}, indent=2))
        return selection

    def render(self, selection, *options, doc=None, cli=False):
        args = ("render", "--selection", str(selection), "--doc", str(doc or self.doc), *(options or ("--write",)))
        if not cli:
            return call(*args)
        result = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.directory,
                                capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

    def assertMalformed(self, selection, message, doc=None, cli=False, options=()):
        """Asserts that render exits with 2 and the message, and leaves the document as it was."""
        before = (doc or self.doc).read_bytes()
        for mode in ("--write", "--check"):
            with self.subTest(mode):
                status, out, err = self.render(selection, mode, *options, doc=doc, cli=cli)
                self.assertEqual((2, ""), (status, out), err)
                self.assertIn(message, err)
                self.assertTrue(err.startswith("editor-results.py: "), err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(before, (doc or self.doc).read_bytes())


class MatrixWordingTest(unittest.TestCase):
    """Each case of the matrix's wording (RESULTS.md, "The matrix")."""
    maxDiff = None

    def test_a_passing_cell_prints_its_level(self):
        found = cells(run_of("nvim"), run_of("lazyvim", 2), run_of("vscode-vsix", 3))
        self.assertEqual("Pass (API)", found[("nvim-linux", "symbols")])
        self.assertEqual("Pass (API + UI state)", found[("lazyvim-linux", "definition")])
        self.assertEqual("Pass (UI state)", found[("lazyvim-linux", "completion")])
        self.assertEqual("Pass (API)", found[("vscode-macos", "rename")])
        # Every passing cell prints its manifest level's wording, never a bare Pass.
        for (setup, feature), cell in found.items():
            if cell.startswith("Pass"):
                level = MANIFEST.cells[(setup, feature)]["level"]
                self.assertTrue(cell.startswith(MANIFEST.levels[level]["wording"]), cell)

    def test_a_passing_cell_names_its_behavior_gaps_not_its_visual_ones(self):
        found = cells(run_of("nvim"), run_of("vscode", 2))
        self.assertEqual("Pass (API); not covered: a change to the project file on disk; a missing Virgil root, "
                         "with its MissingVirgilRoot diagnostic and Invalid project file warning",
                         found[("nvim-linux", "configuration")])
        self.assertEqual("Pass (API); not covered: the jump with CTRL-\\]", found[("nvim-linux", "definition")])
        # The hover window is a visual gap.
        self.assertEqual("Pass (API)", found[("nvim-linux", "hover")])
        self.assertEqual("Pass (API)", found[("vscode-linux", "completion")])
        visual = [gap["what"] for gap in MANIFEST.gaps.values() if gap["kind"] == "visual"]
        for cell in found.values():
            for what in visual:
                self.assertNotIn(results.text(what), cell)

    def test_not_covered_tested_or_run(self):
        found = cells(run_of("nvim"))
        self.assertEqual("Not covered", found[("nvim-linux", "restart")])
        self.assertEqual("Not covered", found[("nvim-macos", "restart")])
        self.assertEqual("Not yet run", found[("nvim-macos", "hover")])
        self.assertEqual("Not yet run", found[("ssh-linux-client", "hover")])
        for setup in ("vscode-wsl", "nvim-wsl", "lazyvim-wsl"):
            self.assertEqual("Not tested (best effort)", found[(setup, "hover")])
        self.assertEqual("Not tested (deferred)", found[("ssh-mac-client", "hover")])
        # A setup outside the automated scope has no cells, even with a run.
        events = run_of("nvim")
        events[0]["setup"] = "nvim-wsl"
        for side in ("client", "host"):
            events[0]["provenance"][side]["os"] = "wsl"
        self.assertEqual("Not tested (best effort)", cells(events)[("nvim-wsl", "hover")])
        self.assertEqual({"Not yet run", "Not covered", "Not tested (best effort)", "Not tested (deferred)"},
                         set(cells().values()))

    def test_a_failed_or_unexpectedly_passing_check(self):
        events = run_of("nvim")
        fail(events, find(events, "nvim.hover.response"))
        self.assertEqual("Fail: `nvim.hover.response`", cells(rebuild(events, status="fail"))[("nvim-linux", "hover")])
        events = run_of("lazyvim")
        events[find(events, RULE["check"], RULE["profile"])].update(status="xpass", reason="shown", failureCode=None)
        self.assertEqual(f"Fail: `{RULE['check']}` (unexpected pass)",
                         cells(rebuild(events, status="fail"))[("lazyvim-linux", "configuration")])
        # A cell names its first three failed checks, and counts the rest.
        events = run_of("lazyvim")
        for check_id in ("lazyvim.signature.complete", "lazyvim.signature.auto-open", "lazyvim.signature.unclosed",
                         "lazyvim.signature.insert-key", "lazyvim.signature.normal-key"):
            fail(events, find(events, check_id))
        self.assertEqual("Fail: `lazyvim.signature.auto-open`, `lazyvim.signature.complete`, "
                         "`lazyvim.signature.insert-key`, and 2 more",
                         cells(rebuild(events, status="fail"))[("lazyvim-linux", "signature")])

    def test_partial_cells(self):
        # Skipped after a failure in another cell.
        events = run_of("nvim")
        events.insert(1, events.pop(find(events, "nvim.infrastructure.preflight")))
        fail(events, 1)
        events[find(events, "nvim.hover.response")].update(status="skip", reason="no server")
        found = cells(rebuild(events, status="fail"))
        self.assertEqual("Partial (skipped)", found[("nvim-linux", "hover")])
        self.assertEqual("Pass (API)", found[("nvim-linux", "symbols")])
        # Not observed: a run stopped before the check.
        events = run_of("nvim")
        del events[find(events, "nvim.hover.response")]
        found = cells(rebuild(events, status="aborted", exitCode=None, signal="SIGTERM"))
        self.assertEqual("Partial (not observed)", found[("nvim-linux", "hover")])
        # Both.
        events = run_of("lazyvim")
        events.insert(1, events.pop(find(events, "lazyvim.infrastructure.preflight")))
        fail(events, 1)
        events[find(events, "lazyvim.signature.complete")].update(status="skip", reason="r")
        del events[find(events, "lazyvim.signature.unclosed")]
        found = cells(rebuild(events, status="aborted", exitCode=None, signal="SIGKILL"))
        self.assertEqual("Partial (skipped, not observed)", found[("lazyvim-linux", "signature")])

    def test_a_profile_left_out_is_not_observed(self):
        # The noroot profile has the configuration cell's rootless checks.
        found = cells(only(run_of("lazyvim"), {"main", "wrapper"}))
        self.assertEqual("Partial (not observed)", found[("lazyvim-linux", "configuration")])
        self.assertEqual("Pass (API + UI state)", found[("lazyvim-linux", "hover")])
        found = cells(only(run_of("remote-ssh"), {"probe", "wrapper"}))
        self.assertEqual("Partial (not observed)", found[("ssh-linux-client", "hover")])

    def test_the_expected_failure(self):
        found = cells(run_of("lazyvim"), run_of("lazyvim-macos", 2))
        self.assertEqual(KNOWN_LIMITATION, found[("lazyvim-linux", "configuration")])
        self.assertEqual(KNOWN_LIMITATION, found[("lazyvim-macos", "configuration")])
        # The rule's label and issue come from the manifest.
        rule = MANIFEST.xfails[RULE["id"]]
        self.assertEqual(f"Known limitation: {rule['label']} ([folke/noice.nvim#1074]({rule['issue']})); "
                         "workaround profile separate", KNOWN_LIMITATION)
        # Without the rule's commit, the warning is a pass.
        events = run_of("lazyvim")
        for pin in events[0]["provenance"]["pins"]:
            if pin["name"] == RULE["plugin"]:
                pin["commit"] = builder.OTHER_COMMIT
        events[find(events, RULE["check"], RULE["profile"])].update(
            status="pass", reason=None, expectation=None, failureCode=None)
        found = cells(rebuild(events))
        self.assertEqual("Pass (API + UI state)", found[("lazyvim-linux", "configuration")])

    def test_the_expected_failure_with_failures(self):
        # Another check of the cell failed: the cell fails.
        events = run_of("lazyvim")
        fail(events, find(events, "lazyvim.configuration.unsaved"))
        self.assertEqual("Fail: `lazyvim.configuration.unsaved`",
                         cells(rebuild(events, status="fail"))[("lazyvim-linux", "configuration")])
        # A prerequisite failed, so the rule doesn't excuse the warning.
        events = run_of("lazyvim")
        fail(events, find(events, "lazyvim.configuration.missing-root", RULE["profile"]))
        fail(events, find(events, RULE["check"], RULE["profile"]), RULE["failureCode"])
        self.assertEqual(f"Fail: `lazyvim.configuration.missing-root`, `{RULE['check']}`",
                         cells(rebuild(events, status="fail"))[("lazyvim-linux", "configuration")])
        # A failure and a skip beside the expected failure: the cell fails.
        # A skip alone, after a failure in another cell: the cell is partial.
        events = run_of("lazyvim")
        index = find(events, "lazyvim.configuration.valid-root")
        fail(events, find(events, "lazyvim.configuration.disk-next-edit"))
        events[index].update(status="skip", reason="r")
        self.assertEqual("Fail: `lazyvim.configuration.disk-next-edit`",
                         cells(rebuild(events, status="fail"))[("lazyvim-linux", "configuration")])
        events = run_of("lazyvim")
        events.insert(1, events.pop(find(events, "lazyvim.infrastructure.preflight")))
        fail(events, 1)
        events[find(events, "lazyvim.configuration.valid-root")].update(status="skip", reason="r")
        self.assertEqual("Partial (skipped)", cells(rebuild(events, status="fail"))[("lazyvim-linux", "configuration")])

    def test_a_workaround_profile_never_counts(self):
        workaround = RULE["workaroundProfile"]
        # Its failure doesn't change the cell, though the run fails.
        events = run_of("lazyvim")
        fail(events, find(events, RULE["check"], workaround), RULE["failureCode"])
        fail(events, find(events, "lazyvim.configuration.missing-root", workaround))
        events = rebuild(events, status="fail")
        self.assertEqual(KNOWN_LIMITATION, cells(events)[("lazyvim-linux", "configuration")])
        self.assertTrue(row(events)[5].startswith("Failed"))
        # Its passes don't stand in for the stock configuration's.
        found = cells(only(run_of("lazyvim"), {workaround, "wrapper"}))
        self.assertEqual("Partial (not observed)", found[("lazyvim-linux", "configuration")])
        self.assertEqual("Partial (not observed)", found[("lazyvim-linux", "hover")])

    def test_runs_of_a_setup_count_together(self):
        # Two legs of the lazyvim-linux lane, at Neovim 0.11.6 and 0.12.6,
        # each with one side of the restart command's alternatives: neither
        # lacks the other side.
        found = cells(leg("0.11.6", 1), leg("0.12.6", 2))
        self.assertEqual("Pass (API)", found[("lazyvim-linux", "restart")])
        self.assertEqual(KNOWN_LIMITATION, found[("lazyvim-linux", "configuration")])
        # A failure in either leg fails the cell, whether its check is one
        # that both legs plan or only that leg's side.
        for version, check_id, feature in (("0.11.6", "lazyvim.hover.popup", "hover"),
                                           ("0.12.6", "lazyvim.hover.popup", "hover"),
                                           ("0.11.6", "lazyvim.restart.legacy", "restart"),
                                           ("0.12.6", "lazyvim.restart.builtin", "restart")):
            with self.subTest(version=version, check=check_id):
                legs = {"0.11.6": leg("0.11.6", 1), "0.12.6": leg("0.12.6", 2)}
                events = legs[version]
                fail(events, find(events, check_id))
                rebuild(events, status="fail")
                found = cells(*legs.values())
                self.assertEqual(f"Fail: `{check_id}`", found[("lazyvim-linux", feature)])

    def test_every_repetition_counts(self):
        events = run_of("remote-ssh")
        fail(events, find(events, "vscode.hover.response", iteration=3))
        found = cells(rebuild(events, status="fail"))
        self.assertEqual("Fail: `vscode.hover.response`", found[("ssh-linux-client", "hover")])
        events = run_of("remote-ssh")
        del events[find(events, "vscode.hover.response", iteration=2)]
        found = cells(rebuild(events, status="aborted", exitCode=None, signal="SIGTERM"))
        self.assertEqual("Partial (not observed)", found[("ssh-linux-client", "hover")])
        self.assertEqual("Pass (API)", found[("ssh-linux-client", "definition")])


class RunsTableTest(unittest.TestCase):
    maxDiff = None

    def test_a_passing_run(self):
        events = run_of("nvim", lane="test", purpose="release", command=["make", "test"])
        provenance = events[0]["provenance"]
        provenance["server"]["version"] = RELEASE
        provenance["editor"]["version"] = "0.11.6"
        provenance["client"].update(release="24.04")
        provenance["ci"] = {"runUrl": "https://github.com/o/r/actions/runs/123", "jobUrl": None, "attempt": 2,
                            "runnerImage": "ubuntu-24.04"}
        self.assertEqual(["2026-10-24", "`2222222`",
                          "Neovim, Linux; `test`; Linux 24.04, x86_64; "
                          "[run 123](https://github.com/o/r/actions/runs/123), attempt 2",
                          "`make test`", "Neovim 0.11.6", "Passed (21 of 21 observations): 21 pass"], row(events))

    def test_the_expected_failure(self):
        cells_ = row(run_of("lazyvim", lane="lazyvim-linux"))
        self.assertEqual("Passed (57 of 57 observations): 56 pass, 1 xfail; XFAIL: " + NOICE, cells_[5])
        self.assertEqual("LazyVim, Linux; `lazyvim-linux`; Linux 1, x86_64; development run", cells_[2])
        self.assertEqual("`2222222`, server 0.1.0-alpha.3", cells_[1])

    def test_runs_that_didnt_pass(self):
        events = run_of("nvim")
        index = find(events, "nvim.hover.response")
        fail(events, index)
        events[index + 1].update(status="skip", reason="r")
        self.assertEqual("Failed (21 of 21 observations): 19 pass, 1 fail, 1 skip; failed: `nvim.hover.response`; "
                         "exit code 1; cleanup failed; 2 issues",
                         row(rebuild(events, status="fail", exitCode=1, cleanup="failed", issues=["a", "b"]))[5])
        events = run_of("remote-ssh")[:40] + run_of("remote-ssh")[-1:]
        self.assertEqual("Aborted (39 of 150 observations): 39 pass; signal SIGTERM; cleanup unknown",
                         row(rebuild(events, status="aborted", exitCode=None, signal="SIGTERM", cleanup="unknown"))[5])
        events = rebuild(run_of("nvim"), status="fail", exitCode=None)
        self.assertEqual("Failed (21 of 21 observations): 21 pass; no exit code", row(events)[5])
        events = run_of("lazyvim")
        events[find(events, RULE["check"], RULE["profile"])].update(status="xpass", reason="shown", failureCode=None)
        self.assertEqual("Failed (57 of 57 observations): 56 pass, 1 xpass; passed unexpectedly: "
                         f"`{RULE['check']}`", row(rebuild(events, status="fail"))[5])
        # A run whose preflight failed, without its worker.
        events = run_of("nvim")
        events[0]["provenance"]["server"].update(version=None, workerSha256={"value": None, "unavailable": "none"})
        events.insert(1, events.pop(find(events, "nvim.infrastructure.preflight")))
        fail(events, 1)
        for event in events[2:-1]:
            event.update(status="skip", reason="the preflight failed")
        self.assertEqual(["`2222222`, server version unknown",
                          "Unavailable (21 of 21 observations): 1 fail, 20 skip; failed: "
                          "`nvim.infrastructure.preflight`; exit code 2; provenance unavailable"],
                         [row(rebuild(events, status="unavailable", exitCode=2))[i] for i in (1, 5)])
        # A cap on the failed checks it names.
        events = run_of("nvim")
        for index in range(1, 6):
            fail(events, index)
        first = sorted(event["id"] for event in events[1:6])[:3]
        self.assertIn("; failed: " + ", ".join(f"`{check_id}`" for check_id in first) + ", and 2 more; exit code 1",
                      row(rebuild(events, status="fail", exitCode=1))[5])

    def test_editors_and_machines(self):
        events = run_of("vscode-vsix", lane="vscode-vsix-macos")
        provenance = events[0]["provenance"]
        provenance["editor"]["version"] = "1.142.0"
        for side in ("client", "host"):
            provenance[side].update(release="15.5", arch="arm64", cpu="Apple M1")
        provenance["host"]["translation"] = "rosetta-2"
        provenance["harness"]["dirty"] = True
        found = row(events)
        self.assertEqual("VS Code 1.142.0, extension 0.1.2 installed from a VSIX", found[4])
        self.assertEqual("VS Code, macOS; `vscode-vsix-macos`; macOS 15.5, arm64, Apple M1, the server under "
                         "rosetta-2; development run", found[2])
        self.assertEqual("`2222222`, with local changes, server 0.1.0-alpha.3", found[1])
        self.assertEqual("VS Code 1, extension from source", row(run_of("vscode"))[4])
        events = run_of("remote-ssh", lane="vscode-remote-ssh-linux")
        self.assertEqual("VS Code Remote-SSH, Linux client; `vscode-remote-ssh-linux`; Linux 1, x86_64, over SSH to "
                         "the same machine; development run", row(events)[2])
        events[0]["provenance"]["transport"]["topology"] = "separate-host"
        events[0]["provenance"]["host"]["release"] = "26.04"
        self.assertIn("over SSH to Linux 26.04, x86_64", row(events)[2])
        events = builder.unreadable_vsix(("probe", "wrapper"))
        events[0]["release"] = RELEASE
        self.assertEqual("VS Code 1, extension installed from a VSIX", row(events)[4])
        events = run_of("nvim")
        events[0]["provenance"]["editor"]["version"] = None
        events[0]["provenance"]["harness"].update(revision=None, dirty=None)
        self.assertEqual(["Unknown, server 0.1.0-alpha.3", "Neovim, version unknown"],
                         [row(events)[i] for i in (1, 4)])
        events[0]["provenance"]["clientMode"] = "manual"
        self.assertEqual("Neovim, version unknown, a person's own setup", row(events)[4])

    def test_cells_are_escaped(self):
        events = run_of("nvim", lane="a|b *c* <d> [e](f) `g` $h$ ~i~ &amp;_j_ k_l \\|\n\u202e")
        events[0]["command"] = ["run.sh", "--name", "a`b | c", "two words", "x\ny"]
        provenance = events[0]["provenance"]
        provenance["client"]["cpu"] = "<script>|*"
        provenance["ci"] = {"runUrl": "https://github.com/o/r/actions/runs/1/x) [y]|z%41", "jobUrl": None, "attempt": 1,
                            "runnerImage": None}
        found = row(events)
        self.assertEqual("``a\\|b *c* <d> [e](f) `g` $h$ ~i~ &amp;_j_ k_l \\\\|\\u000a\\u202e``", found[2].split("; ")[1])
        self.assertEqual("``run.sh --name 'a`b \\| c' 'two words' 'x\\u000ay'``", found[3])
        self.assertIn("Linux 1, x86_64, \\<script\\>\\|\\*", found[2])
        self.assertIn("[run 1](https://github.com/o/r/actions/runs/1/x%29%20%5By%5D%7Cz%41)", found[2])
        line = results.table(results.RUNS_HEADER, [found]).strip().split("\n")[2]
        self.assertEqual(7, unescaped_pipes(line), line)
        self.assertNotIn("\n", line)
        self.assertEqual("\\*a\\* \\_b\\_ c_d \\<e\\> \\[f\\] \\| \\` \\$ \\~ \\& \\\\ x\\_ \\_y",
                         results.text("*a* _b_ c_d <e> [f] | ` $ ~ & \\ x_ _y"))
        # A URL that isn't https is code, not a link.
        provenance["ci"]["runUrl"] = "javascript:alert(1)"
        self.assertIn("CI run `javascript:alert(1)`", row(events)[2])

    def test_rows_are_sorted(self):
        runs = [validated(events) for events in (
            run_of("lazyvim", 1, lane="b"), run_of("nvim", 2, lane="z"), run_of("lazyvim", 3, lane="a"),
            run_of("vscode", 4, lane="m"), run_of("lazyvim-macos", 5, lane="a"))]
        table = results.render_blocks(RELEASE, runs, MANIFEST)["runs"]
        lanes = re.findall(r"; `([a-z])`; ", table)
        # By the manifest's order of the setups, then the lane.
        self.assertEqual(["m", "z", "a", "b", "a"], lanes)
        # Then the editor version, by its numbers, before the run ID.
        runs = [validated(at_version(run_of("nvim", number, lane="test"), version))
                for number, version in ((3, "0.10.0"), (1, "0.12.6"), (4, "0.9.5"), (2, "0.11.6"))]
        table = results.render_blocks(RELEASE, runs, MANIFEST)["runs"]
        self.assertEqual(["0.9.5", "0.10.0", "0.11.6", "0.12.6"], re.findall(r"\| Neovim ([0-9.]+) \|", table))


class SelectionTest(RenderTest):
    def test_a_long_editor_version(self):
        # All are schema-valid strings. Numeric tokens can exceed Python's
        # integer conversion limit, and leading zeros do not change their value.
        versions = ["0.9", "0.10", "8" * 5000, "000" + "9" * 5000, "1" + "0" * 5000]
        selection = self.select(*[at_version(run_of("nvim", number), version)
                                  for number, version in enumerate(reversed(versions), 1)])
        status, out, err = self.render(selection, cli=True)
        self.assertEqual((0, ""), (status, err), out)
        self.assertEqual(versions, re.findall(r"\| Neovim ([0-9.]+) \|", self.doc.read_text()))
        self.assertEqual(0, self.render(selection, "--check", cli=True)[0])

    def test_renders_the_selected_streams(self):
        selection = self.select(run_of("nvim"), run_of("lazyvim", 2))
        status, out, err = self.render(selection)
        self.assertEqual((0, f"{self.doc}: rendered the results of {RELEASE}\n", ""), (status, out, err))
        text = self.doc.read_text()
        self.assertIn("| Neovim, Linux | Pass (API); not covered: undoing the fix", text)
        self.assertIn(f"| LazyVim, Linux | Pass (API) | Pass (API) | Pass (API + UI state) | Pass (API + UI state) | "
                      f"{KNOWN_LIMITATION} |", text)

    def test_hash_mismatch(self):
        selection = self.select(run_of("nvim"))
        stream_path = selection.parent / "stream-0.jsonl"
        stream_path.write_bytes(stream_path.read_bytes().replace(b'"observed"', b'"Observed"', 1))
        self.assertMalformed(selection, f"{stream_path}: its SHA-256 is ")

    def test_release_mismatch(self):
        self.assertMalformed(self.select(run_of("nvim", release="0.1.0-alpha.5")),
                             'its release is "0.1.0-alpha.5", not the selection\'s "0.1.0-alpha.4"')
        self.assertMalformed(self.select(run_of("nvim", release=None)), "its release is null, not the selection's")

    def test_invalid_stream(self):
        events = run_of("nvim")[:-1]
        selection = self.select(run_of("lazyvim", 2), events)
        self.assertMalformed(selection, f"{selection.parent / 'stream-1.jsonl'}: invalid: line 23: incomplete: no "
                                        "run-end")
        events = run_of("nvim")
        events[0]["coverageSha256"] = "0" * 64
        self.assertMalformed(self.select(events, directory="other"), "planned with another coverage.json")

    def test_duplicate_slots(self):
        # Two attempts of a slot: the renderer doesn't choose the latest.
        selection = self.select(run_of("nvim", 1, lane="test"), run_of("nvim", 2, lane="test"))
        self.assertMalformed(selection, "stream-1.jsonl: suite nvim, setup nvim-linux, lane test, and editor version 1 "
                                        f"are also those of {selection.parent / 'stream-0.jsonl'}: select one run for "
                                        "each")
        # Two attempts of a leg of a lane, at the same editor version.
        selection = self.select(leg("0.12.6", 1), leg("0.11.6", 2), leg("0.12.6", 3), directory="legs")
        self.assertMalformed(selection, "stream-2.jsonl: suite lazyvim, setup lazyvim-linux, lane lazyvim-linux, and "
                                        f"editor version 0.12.6 are also those of {selection.parent / 'stream-0.jsonl'}")
        # Another lane, setup, or editor version is another slot.
        events = run_of("nvim", 3, lane="test")
        events[0]["setup"] = "nvim-macos"
        for side in ("client", "host"):
            events[0]["provenance"][side]["os"] = "macos"
        selection = self.select(run_of("nvim", 1, lane="test"), run_of("nvim", 2, lane="local"), events,
                                at_version(run_of("nvim", 4, lane="test"), "0.12.6"))
        self.assertEqual(0, self.render(selection)[0])

    def test_legs_of_a_lane(self):
        # The lazyvim-linux job's legs, at Neovim 0.12.6 and 0.11.6, each in
        # its own row, by version, and both in the matrix's cells.
        for attempt, names in enumerate((["a.jsonl", "b.jsonl"], ["b.jsonl", "a.jsonl"])):
            with self.subTest(names):
                selection = self.select(leg("0.12.6", 1), leg("0.11.6", 2), names=names, directory=f"order-{attempt}")
                status, out, err = self.render(selection)
                self.assertEqual((0, ""), (status, err), out)
                text = self.doc.read_text()
                self.assertEqual(["0.11.6", "0.12.6"], re.findall(r"`lazyvim-linux`;.* \| Neovim ([0-9.]+) \|", text))
                self.assertEqual("Pass (API)", matrix_row(text, "LazyVim, Linux")["restart"])
        # A failure in either leg fails its cells, and the render.
        for version in ("0.11.6", "0.12.6"):
            with self.subTest(version):
                legs = {"0.11.6": leg("0.11.6", 1), "0.12.6": leg("0.12.6", 2)}
                fail(legs[version], find(legs[version], "lazyvim.hover.popup"))
                rebuild(legs[version], status="fail")
                selection = self.select(*legs.values(), directory=f"failed-{version}")
                self.assertEqual(1, self.render(selection)[0])
                row_ = matrix_row(self.doc.read_text(), "LazyVim, Linux")
                self.assertEqual(("Fail: `lazyvim.hover.popup`", "Pass (API)"), (row_["hover"], row_["restart"]))

    def test_an_unknown_editor_version(self):
        # A stream without its editor version could be of any leg of its
        # lane, so it shares each one's slot, whichever comes first.
        unknown = "the editor version of one or both is unknown: select one run for each"
        for case, streams in (("first", [leg(None, 1), leg("0.12.6", 2)]),
                              ("last", [leg("0.11.6", 1), leg("0.12.6", 2), leg(None, 3)]),
                              ("both", [leg(None, 1), leg(None, 2)])):
            with self.subTest(case):
                selection = self.select(*streams, directory=case)
                self.assertMalformed(selection, f"{selection.parent / f'stream-{len(streams) - 1}.jsonl'}: suite "
                                                "lazyvim, setup lazyvim-linux, and lane lazyvim-linux are also those "
                                                f"of {selection.parent / 'stream-0.jsonl'}, and {unknown}")
        # Alone in its lane, it renders.
        selection = self.select(leg(None, 1), at_version(run_of("lazyvim", 2), "0.12.6"), directory="alone")
        self.assertEqual(0, self.render(selection)[0])
        self.assertIn("| Neovim, version unknown |", self.doc.read_text())

    def test_duplicate_run_ids(self):
        selection = self.select(run_of("nvim", 1, lane="a"), run_of("lazyvim", 1, lane="b"))
        self.assertMalformed(selection, "its run run-1 is also that of")

    def test_every_stream_in_the_directory_is_listed(self):
        selection = self.select(run_of("nvim"))
        stray = selection.parent / "lanes" / "retry.jsonl"
        stray.parent.mkdir()
        stray.write_bytes(encode(run_of("nvim", 2)))
        self.assertMalformed(selection, f"{stray}: the selection {selection} doesn't list this stream")
        stray.rename(selection.parent / "retry.jsonl")
        self.assertMalformed(selection, "retry.jsonl: the selection")
        # Other files may be there.
        (selection.parent / "retry.jsonl").rename(selection.parent / "retry.log")
        (selection.parent / "notes.json").write_text("{}")
        self.assertEqual(0, self.render(selection)[0])
        # A listed stream may be in a subdirectory.
        selection = self.select(run_of("nvim"), run_of("lazyvim", 2), directory="nested",
                                names=["linux/nvim.jsonl", "linux/.lazyvim.jsonl"])
        self.assertEqual(0, self.render(selection)[0])

    def test_a_symlinked_directory_cannot_hide_a_failed_retry(self):
        external = self.select(run_of("nvim"), directory="external", names=["selected.jsonl"]).parent
        failed = run_of("nvim", 2)
        fail(failed, find(failed, "nvim.hover.response"))
        (external / "retry.jsonl").write_bytes(encode(rebuild(failed, status="fail")))
        selected = external / "selected.jsonl"
        selection = self.select(entries=[{"path": "lane/selected.jsonl",
                                          "sha256": hashlib.sha256(selected.read_bytes()).hexdigest()}])
        lane = selection.parent / "lane"
        lane.symlink_to("../external", target_is_directory=True)
        self.assertMalformed(selection, f"{lane}: symlinks are not allowed", cli=True)

    def test_symlinks_anywhere_under_the_selection_are_rejected(self):
        for name, target, directory in (("directory", ".", True),
                                         ("file", "stream-0.jsonl", False),
                                         ("dangling", "missing", False)):
            with self.subTest(name):
                selection = self.select(run_of("nvim"), directory=name)
                link = selection.parent / "unlisted"
                link.symlink_to(target, target_is_directory=directory)
                self.assertMalformed(selection, f"{link}: symlinks are not allowed", cli=True)

    def test_symlinks_in_listed_paths_are_rejected(self):
        selection = self.select(run_of("nvim"))
        stream = selection.parent / "stream-0.jsonl"
        external = self.directory / "external.jsonl"
        stream.rename(external)
        stream.symlink_to(external)
        self.assertMalformed(selection, f"{stream}: symlinks are not allowed", cli=True)

    def test_symlinks_in_the_selection_path_are_rejected(self):
        selection = self.select(run_of("nvim"))
        alias = self.directory / "alias"
        alias.symlink_to(selection.parent, target_is_directory=True)
        self.assertMalformed(alias / selection.name, f"{alias}: symlinks are not allowed", cli=True)
        alias.unlink()
        alias.symlink_to(selection)
        self.assertMalformed(alias, f"{alias}: symlinks are not allowed", cli=True)

    def test_missing_and_repeated_streams(self):
        selection = self.select(run_of("nvim"))
        entries = json.loads(selection.read_text())["streams"]
        selection.write_text(json.dumps({"version": 1, "release": RELEASE,
                                         "streams": entries + [dict(entries[0], path="gone.jsonl")]}))
        self.assertMalformed(selection, f"{selection.parent / 'gone.jsonl'}: can't read it: No such file")
        selection.write_text(json.dumps({"version": 1, "release": RELEASE, "streams": entries * 2}))
        self.assertMalformed(selection, "lists stream-0.jsonl twice")

    def test_selection_schema(self):
        good = {"path": "stream-0.jsonl", "sha256": "0" * 64}
        cases = [
            ("version", {"version": 2, "release": RELEASE, "streams": [good]}, "version: 2 is not 1"),
            ("unknown member", {"version": 1, "release": RELEASE, "streams": [good], "policy": "release"},
             'has the member "policy"'),
            ("no streams", {"version": 1, "release": RELEASE, "streams": []}, "fewer than 1"),
            ("release", {"version": 1, "release": "alpha", "streams": [good]}, "release"),
            ("release with a colon", {"version": 1, "release": "1.0.0-a:b", "streams": [good]}, "release"),
            ("parent path", {"version": 1, "release": RELEASE, "streams": [dict(good, path="../x.jsonl")]},
             "streams[0].path"),
            ("dot path", {"version": 1, "release": RELEASE, "streams": [dict(good, path="./x.jsonl")]},
             "streams[0].path"),
            ("absolute path", {"version": 1, "release": RELEASE, "streams": [dict(good, path="/tmp/x.jsonl")]},
             "streams[0].path"),
            ("not a stream", {"version": 1, "release": RELEASE, "streams": [dict(good, path="x.json")]},
             "streams[0].path"),
            ("hash", {"version": 1, "release": RELEASE, "streams": [dict(good, sha256="A" * 64)]},
             "streams[0].sha256"),
            ("unknown stream member", {"version": 1, "release": RELEASE, "streams": [dict(good, runId="r")]},
             'has the member "runId"'),
        ]
        for name, value, message in cases:
            with self.subTest(name):
                selection = self.select(run_of("nvim"))
                selection.write_text(json.dumps(value))
                self.assertMalformed(selection, message)
        selection = self.select(run_of("nvim"))
        for data, message in ((b"{", "is not JSON"), (b'{"version": 1, "version": 1}', 'the key "version" is repeated'),
                              (b"\xff", "is not JSON")):
            with self.subTest(message):
                selection.write_bytes(data)
                self.assertMalformed(selection, message)
        self.assertMalformed(selection.parent / "nothing.json", "can't read the selection")

    def test_the_document_must_be_readable(self):
        selection = self.select(run_of("nvim"))
        self.doc.write_bytes(document().encode().replace(b"caf\xc3\xa9", b"caf\xe9"))
        self.assertMalformed(selection, "is not UTF-8: byte")
        self.doc.write_bytes(document().replace("\n", "\r\n", 2).encode())
        self.assertMalformed(selection, f"{self.doc}: line 1 has a CR")
        self.doc.unlink()
        status, out, err = self.render(selection)
        self.assertEqual((2, ""), (status, out))
        self.assertIn("can't read it", err)


class MarkerTest(RenderTest):
    def write(self, text):
        self.doc.write_text(text)
        return self.select(run_of("nvim"))

    def test_marker_errors(self):
        begin = f"<!-- editor-results:{RELEASE}:runs:begin -->\n"
        end = f"<!-- editor-results:{RELEASE}:runs:end -->\n"
        matrix_begin = f"<!-- editor-results:{RELEASE}:matrix:begin -->\n"
        good = document()
        cases = [
            ("no heading", good.replace(f"### {RELEASE}\n", "### 0.1.0-alpha.9\n"), f"no heading ### {RELEASE}"),
            ("two headings", document(after=f"\n### {RELEASE}\n"), f"a second heading ### {RELEASE}, after line"),
            ("heading of another level", good.replace(f"### {RELEASE}\n", f"## {RELEASE}\n"), "no heading"),
            ("missing begin", good.replace(begin, ""), "lacks the marker <!-- editor-results:0.1.0-alpha.4:runs:begin -->"),
            ("missing end", good.replace(end, ""), "lacks the marker <!-- editor-results:0.1.0-alpha.4:runs:end -->"),
            ("missing matrix", good.replace(matrix_begin, ""), "lacks the marker <!-- editor-results:0.1.0-alpha.4:"
                                                                "matrix:begin -->"),
            ("duplicate", good.replace(end, end + end), "the marker runs:end repeats the one on line"),
            ("reversed", good.replace(begin, "@").replace(end, begin).replace("@", end),
             "the marker runs:end comes before runs:begin"),
            ("interleaved", good.replace(end, "@").replace(matrix_begin, end).replace("@", matrix_begin),
             "the marker matrix:begin comes before runs:end"),
            ("outside, before", document(before="\n" + begin), "line 7: the marker runs:begin is outside the "
                                                               f"section ### {RELEASE}, lines 9 to 20"),
            ("outside, after", good + "\n" + end, "the marker runs:end is outside"),
            ("in the next section", good.replace("Past results", end + "Past results"), "is outside"),
            ("indented", good.replace(begin, "  " + begin), "a marker must be a line of its own"),
            ("trailing text", good.replace(begin, begin[:-1] + " x\n"), "a marker must be a line of its own"),
            ("two on a line", good.replace(begin + end, begin[:-1] + end), "a marker must be a line of its own"),
            ("without a space", good.replace(begin, begin.replace("<!-- ", "<!--")), "must be a line of its own"),
            ("of this release, elsewhere", document(before=f"\ntext editor-results:{RELEASE}: here\n"),
             "must be a line of its own"),
            ("another release's", good.replace("More prose", "<!-- editor-results:0.1.0-alpha.3:runs:begin -->\n"),
             "a marker of release 0.1.0-alpha.3 is in the section"),
            ("unknown block", good.replace("More prose", f"<!-- editor-results:{RELEASE}:summary:begin -->\n"),
             "the block summary is neither runs nor matrix"),
        ]
        for name, text, message in cases:
            with self.subTest(name):
                self.assertMalformed(self.write(text), message)

    def test_commonmark_headings_end_the_release_section(self):
        headings = [" ### 0.1.0-alpha.3", "###\t0.1.0-alpha.3", "   ###\t0.1.0-alpha.3 ###",
                    "  ## Older results", "   # Results", "###", "##\t", " #  "]
        for heading in headings:
            with self.subTest(heading=heading):
                text = document().replace("Hand-written prose.", f"Hand-written prose.\n\n{heading}")
                self.assertMalformed(self.write(text), "the marker runs:begin is outside", cli=True)

    def test_commonmark_release_headings(self):
        for heading in (f" ### {RELEASE}", f"###\t{RELEASE}", f"   ###  {RELEASE}  ###\t"):
            with self.subTest(heading=heading):
                text = document().replace(f"### {RELEASE}", heading)
                self.assertEqual(0, self.render(self.write(text), cli=True)[0])
                self.assertIn(heading + "\n", self.doc.read_text())

    def test_headings_in_fences_do_not_end_the_section(self):
        for fence, info, other in (("````", "markdown", "~~~~"), ("~~~~", "info `with backticks`", "````")):
            with self.subTest(fence=fence):
                # Short, mismatched, and non-whitespace-suffixed fences do
                # not close the block. A longer matching fence does.
                example = (f"   {fence}{info}\n### old\n{fence[:3]}\n### {RELEASE}\n"
                           f"{other}\n## example\n{fence} text\n# example\n  {fence * 2}\t \n")
                text = document().replace("Hand-written prose.", example)
                self.assertEqual(0, self.render(self.write(text), cli=True)[0])
                self.assertIn(example, self.doc.read_text())

    def test_markers_in_fences_are_rejected(self):
        for fence in ("```markdown", "   ~~~~ markdown"):
            for where in ("whole document", "inside section", "another release"):
                with self.subTest(fence=fence, where=where):
                    if where == "whole document":
                        text = f"{fence}\n{document()}\n"
                    elif where == "inside section":
                        text = document().replace("Hand-written prose.", f"Hand-written prose.\n{fence}")
                    else:
                        text = document() + f"\n{fence}\n<!-- editor-results:0.1.0-alpha.3:runs:begin -->\n"
                    self.assertMalformed(self.write(text), "a marker is inside a fenced code block", cli=True)

    def test_other_releases_markers_stay(self):
        other = ("<!-- editor-results:0.1.0-alpha.3:runs:begin -->\n| old |\n"
                 "<!-- editor-results:0.1.0-alpha.3:runs:end -->\n")
        text = document().replace("Past results stay hand-written.\n", "Past results stay hand-written.\n" + other)
        selection = self.write(text)
        self.assertEqual(0, self.render(selection)[0])
        self.assertTrue(self.doc.read_text().endswith(other))

    def test_bytes_outside_the_markers_stay(self):
        old_runs, old_matrix = "| stale | runs |\n\nprose inside\n", "| stale |\n"
        text = document(runs=old_runs, matrix=old_matrix, before="\n\t\u00a0trailing  \n<!-- other -->\n",
                        after="\n\u2028 line separator, \x0c form feed\n")
        text = text + "no final newline"
        selection = self.write(text)
        self.assertEqual(0, self.render(selection)[0])
        new = self.doc.read_text()
        pieces = re.split(r"(<!-- editor-results:[^ ]+ -->\n)", text)
        rendered = re.split(r"(<!-- editor-results:[^ ]+ -->\n)", new)
        self.assertEqual(9, len(pieces))
        # Each piece outside a marker pair, and each marker, is as it was;
        # each interior is a block.
        for index in (0, 1, 3, 4, 5, 7, 8):
            self.assertEqual(pieces[index], rendered[index])
        blocks = results.render_blocks(RELEASE, [validated(run_of("nvim"))], MANIFEST)
        self.assertEqual([blocks["runs"], blocks["matrix"]], [rendered[2], rendered[6]])
        self.assertNotIn("stale", new)
        # Rendering again changes nothing.
        status, out, _ = self.render(selection)
        self.assertEqual((0, f"{self.doc}: the results of {RELEASE} are up to date\n"), (status, out))
        self.assertEqual(new, self.doc.read_text())

    def test_blocks_are_tables(self):
        selection = self.select(run_of("nvim"), run_of("lazyvim", 2))
        self.render(selection)
        text = self.doc.read_text()
        runs = text.split(f"<!-- editor-results:{RELEASE}:runs:begin -->\n")[1].split("<!-- ")[0]
        matrix = text.split(f"<!-- editor-results:{RELEASE}:matrix:begin -->\n")[1].split("<!-- ")[0]
        for block, rows, columns in ((runs, 2, 6), (matrix, len(MANIFEST.setups), len(MANIFEST.features) + 1)):
            lines = block.split("\n")
            self.assertEqual(["", ""], [lines[0], lines[-2]])
            self.assertEqual(rows + 2, len(lines) - 3)
            for line in lines[1:-2]:
                self.assertEqual(columns + 1, unescaped_pipes(line), line)
        self.assertTrue(matrix.split("\n")[1].startswith("| Setup | Diagnostics | Document symbols | Definition |"))


class DeterminismTest(RenderTest):
    def streams(self):
        events = run_of("nvim", 3, lane="local")
        fail(events, find(events, "nvim.hover.response"))
        return [leg("0.12.6", 1), run_of("lazyvim-macos", 2, lane="lazyvim-macos"),
                rebuild(events, status="fail"), run_of("vscode-vsix", 4, lane="vscode-vsix-macos"),
                run_of("remote-ssh", 5, lane="vscode-remote-ssh-linux"), leg("0.11.6", 6)]

    def test_the_order_of_the_inputs_doesnt_matter(self):
        streams = self.streams()
        names = [f"{index}.jsonl" for index in range(len(streams))]
        outputs = set()
        for attempt in range(4):
            order = list(range(len(streams)))
            random.Random(attempt).shuffle(order)
            # The selection lists them, and the files are created, in another order each time.
            selection = self.select(*[streams[i] for i in order], names=[names[i] for i in order],
                                    directory=f"attempt-{attempt}/{'deep/' * attempt}results")
            doc = self.directory / f"doc-{attempt}.md"
            doc.write_text(document())
            status, out, err = self.render(selection, doc=doc)
            self.assertEqual(1, status, err)
            outputs.add(doc.read_bytes())
            self.assertEqual(f"{selection.parent / '2.jsonl'}: valid, run failed", out.split(" (")[0])
        self.assertEqual(1, len(outputs))

    def test_the_environment_doesnt_matter(self):
        selection = self.select(*self.streams())
        outputs = set()
        for seed, zone, locale in (("0", "UTC", "C"), ("1", "Asia/Kathmandu", "de_DE.UTF-8"),
                                   ("2", "America/St_Johns", "tr_TR.UTF-8")):
            doc = self.directory / f"doc-{seed}.md"
            doc.write_text(document())
            env = dict(os.environ, PYTHONHASHSEED=seed, TZ=zone, LC_ALL=locale, LANG=locale)
            # From another directory, with an absolute path to the selection.
            result = subprocess.run([sys.executable, str(SCRIPT), "render", "--selection", str(selection.resolve()),
                                     "--doc", doc.name, "--write"], cwd=self.directory, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(1, result.returncode, result.stderr)
            outputs.add(doc.read_bytes())
        self.assertEqual(1, len(outputs))
        self.assertNotIn(str(self.directory).encode(), outputs.pop())


class CheckTest(RenderTest):
    def test_check(self):
        selection = self.select(run_of("nvim"), run_of("lazyvim", 2))
        status, out, err = self.render(selection, "--check")
        before = self.doc.read_bytes()
        self.assertEqual((1, ""), (status, err))
        self.assertTrue(out.startswith(f"--- {self.doc}\n+++ {self.doc}, rendered\n@@ "), out)
        self.assertIn("\n+| Neovim, Linux | Pass (API); not covered: undoing the fix of bad.v3", out)
        self.assertTrue(out.endswith(f"{self.doc}: the results of {RELEASE} are out of date; render them with "
                                     "--write\n"), out)
        self.assertEqual(before, self.doc.read_bytes())
        # The diff applies to the document.
        self.assertEqual(0, self.render(selection, "--write")[0])
        self.assertEqual((0, f"{self.doc}: the results of {RELEASE} are up to date\n", ""),
                         self.render(selection, "--check"))
        # A one-byte change inside a block is drift; outside them, it isn't.
        rendered = self.doc.read_text()
        self.doc.write_text(rendered.replace("| LazyVim, Linux | Pass (API) |", "| LazyVim, Linux | Pass (APi) |"))
        status, out, _ = self.render(selection, "--check")
        self.assertEqual(1, status)
        self.assertIn("\n-| LazyVim, Linux | Pass (APi) |", out)
        self.assertIn("\n+| LazyVim, Linux | Pass (API) |", out)
        self.doc.write_text(rendered.replace("More prose", "Mere prose"))
        self.assertEqual(0, self.render(selection, "--check")[0])
        self.assertEqual(0, self.render(selection, "--write")[0])
        self.assertIn("Mere prose", self.doc.read_text())

    def test_a_run_that_didnt_pass(self):
        events = run_of("nvim")
        fail(events, find(events, "nvim.hover.response"))
        selection = self.select(rebuild(events, status="fail"))
        stream_path = selection.parent / "stream-0.jsonl"
        summary = f"{stream_path}: valid, run failed (21 of 21 observations): 20 pass, 1 fail, 0 xfail, 0 xpass, 0 skip\n"
        # It still renders, and says so.
        self.assertEqual((1, summary + f"{self.doc}: rendered the results of {RELEASE}\n", ""), self.render(selection))
        self.assertIn("| Fail: `nvim.hover.response` |", self.doc.read_text())
        self.assertEqual((1, summary + f"{self.doc}: the results of {RELEASE} are up to date\n", ""),
                         self.render(selection, "--check"))

    def test_options(self):
        selection = self.select(run_of("nvim"))
        for options in ((), ("--write", "--check")):
            with self.subTest(options):
                status, _, err = call("render", "--selection", str(selection), "--doc", str(self.doc), *options)
                self.assertEqual(2, status)
                self.assertIn("--write", err)
        status, _, err = call("render", "--manifest", str(self.directory / "none.json"), "--selection",
                              str(selection), "--doc", str(self.doc), "--write")
        self.assertEqual(2, status)
        self.assertIn("can't read the manifest", err)


class ManifestTest(RenderTest):
    def test_malformed_renderer_text_is_rejected_when_loading_the_manifest(self):
        fields = [("xfails", 0, "label"), ("xfails", 0, "issue"), ("setups", 0, "label"),
                  ("areas", 0, "label"), ("levels", 0, "wording"), ("scopes", 1, "wording"),
                  ("gaps", 0, "what"), ("gaps", 0, "kind")]
        for section, index, field in fields:
            for value in (None, "", " \t", 1, [], {}):
                if section == "scopes" and value is None:
                    continue  # A null wording is valid for an automated scope.
                with self.subTest(section=section, field=field, value=value):
                    manifest = json.loads(builder.MANIFEST_BYTES)
                    manifest[section][index][field] = value
                    data = json.dumps(manifest).encode()
                    path = self.directory / "manifest.json"
                    path.write_bytes(data)
                    events = run_of("lazyvim")
                    events[0]["coverageSha256"] = hashlib.sha256(data).hexdigest()
                    selection = self.select(events)
                    self.assertMalformed(selection, f"an entry of {section} has {field} that must be a nonempty "
                                                    "string", cli=True, options=("--manifest", str(path)))

    def test_malformed_matrix_references_are_rejected_when_loading_the_manifest(self):
        cases = [("scope", "missing", "unknown scope"),
                 ("scope-wording", None, "the matrix lacks the cell for vscode-wsl and diagnostics"),
                 ("checks", None, "checks that must be an array"),
                 ("checks", ["missing"], 'unknown checks entry "missing"'),
                 ("notCovered", None, "notCovered that must be an array"),
                 ("notCovered", ["missing"], 'unknown notCovered entry "missing"'),
                 ("level", None, "unknown level null")]
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                manifest = json.loads(builder.MANIFEST_BYTES)
                if field == "scope":
                    manifest["setups"][0][field] = value
                elif field == "scope-wording":
                    manifest["scopes"][1]["wording"] = value
                else:
                    manifest["matrix"]["cells"][0][field] = value
                data = json.dumps(manifest).encode()
                path = self.directory / "manifest.json"
                path.write_bytes(data)
                events = run_of("lazyvim")
                events[0]["coverageSha256"] = hashlib.sha256(data).hexdigest()
                self.assertMalformed(self.select(events), message, cli=True, options=("--manifest", str(path)))


# The example in results-example/: demo runs of three setups, each passing,
# with the LazyVim runs' expected failure. LazyVim on Linux has a leg at each
# of its lane's Neovim versions.
LAZYVIM_COMMAND = ["test/e2e/lazyvim/run.sh", "--archive", "<archive>", "--out", "<dir>"]
EXAMPLE_RUNS = {
    "nvim-linux.jsonl": ("nvim", "nvim-linux", "test", ["make", "test"], "0.11.6"),
    "lazyvim-linux-0.11.6.jsonl": ("lazyvim", "lazyvim-linux", "lazyvim-linux", LAZYVIM_COMMAND, "0.11.6"),
    "lazyvim-linux-0.12.6.jsonl": ("lazyvim", "lazyvim-linux", "lazyvim-linux", LAZYVIM_COMMAND, "0.12.6"),
    "vscode-macos.jsonl": ("vscode-vsix", "vscode-macos", "vscode-vsix-macos",
                           ["npm", "run", "test:e2e", "--", "--vsix", "<vsix>", "<archive>/virgil-lsp"], "1.142.0"),
}


def example_streams():
    """The example's streams, by file name."""
    streams = {}
    for name, (variant, setup, lane, command, version) in EXAMPLE_RUNS.items():
        events = run_of(variant, lane=lane, release=EXAMPLE_RELEASE, purpose="demo", command=command)
        for event in events:
            event["runId"] = f"example-{name[:-len('.jsonl')]}"
        events = at_version(events, version)
        provenance = events[0]["provenance"]
        provenance["server"].update(versionText=f"virgil-lsp {EXAMPLE_RELEASE}", version=EXAMPLE_RELEASE)
        if setup.endswith("macos"):
            provenance["server"]["target"] = "x86-64-darwin"
            for side in ("client", "host"):
                provenance[side].update(release="15.5", arch="arm64", cpu="Apple M1")
            provenance["host"]["translation"] = "rosetta-2"
            provenance["editor"]["arch"] = "arm64"
        else:
            for side in ("client", "host"):
                provenance[side]["release"] = "6.8.0"
        streams[name] = encode(events)
    return streams


def example_selection(streams):
    return (json.dumps({"version": 1, "release": EXAMPLE_RELEASE,
                        "streams": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}
                                    for name, data in sorted(streams.items())]}, indent=2) + "\n").encode()


def write_example():
    """Regenerates the example's streams and selection, and its document's tables."""
    streams = example_streams()
    for name, data in streams.items():
        (EXAMPLE / name).write_bytes(data)
    (EXAMPLE / "selection.json").write_bytes(example_selection(streams))
    status = results.main(["render", "--selection", str(EXAMPLE / "selection.json"), "--doc",
                           str(EXAMPLE / "README.md"), "--write"])
    return 0 if status == 0 else 1


class ExampleTest(unittest.TestCase):
    def test_the_example_is_up_to_date(self):
        regenerate = "; run python3 test/e2e/test_editor_results_render.py --write-example"
        streams = example_streams()
        self.assertEqual(sorted(streams), sorted(path.name for path in EXAMPLE.glob("*.jsonl")))
        for name, data in streams.items():
            self.assertEqual(data, (EXAMPLE / name).read_bytes(), f"{name} is out of date" + regenerate)
        self.assertEqual(example_selection(streams), (EXAMPLE / "selection.json").read_bytes(),
                         "selection.json is out of date" + regenerate)
        status, out, err = call("render", "--selection", str(EXAMPLE / "selection.json"), "--doc",
                                str(EXAMPLE / "README.md"), "--check")
        self.assertEqual((0, ""), (status, err), out + regenerate)


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-example"]:
        sys.exit(write_example())
    unittest.main()
