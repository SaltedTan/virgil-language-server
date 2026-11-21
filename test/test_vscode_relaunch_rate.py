#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/check-vscode-relaunch-rate.py."""
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-vscode-relaunch-rate.py"

spec = importlib.util.spec_from_file_location("check_vscode_relaunch_rate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def relaunch(message="the single-file launch exited with 1"):
    return {"annotation_level": "warning", "title": "Extension Host relaunch (#205)",
            "message": message}


def job(name, conclusion="success", **fields):
    job_id = fields.pop("id", 100)
    # Base the default times on the id so each synthetic job is distinct under
    # the (run_id, name, started_at, completed_at) deduplication key too.
    hours, minutes = divmod(job_id, 60)
    base = {"id": job_id, "run_id": fields.pop("run_id", 10),
            "run_attempt": fields.pop("run_attempt", 1), "status": "completed",
            "conclusion": conclusion, "name": name,
            "started_at": fields.pop("started_at", f"2026-11-22T{10 + hours:02d}:{minutes:02d}:00Z"),
            "completed_at": fields.pop("completed_at", f"2026-11-22T{10 + hours:02d}:{minutes:02d}:30Z")}
    base.update(fields)
    return base


def record(job_object, annotations=()):
    return {"job": job_object, "annotations": list(annotations)}


def metrics(output):
    """The Markdown report's metric table, as {metric: value}."""
    rows = {}
    for line in output.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] not in ("Metric", "---"):
            rows[cells[0]] = cells[1]
    return rows


def events(output):
    """The Markdown report's relaunch events, as (run, job, name, message) rows."""
    _, _, table = output.partition("### Relaunch events")
    return [tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
            for line in table.splitlines()[3:] if line.startswith("|") and "---" not in line]


def run(records, *extra, out=None):
    """Runs main() on records serialized as a JSON list, returning (exit, stdout)."""
    out = io.StringIO() if out is None else out
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(records, handle)
        path = handle.name
    try:
        argv = ["--input", path, *extra]
        code = module.main(argv, environment={}, out=out)
    finally:
        Path(path).unlink()
    return code, out.getvalue()


class ComputationTest(unittest.TestCase):
    def test_zero_jobs_is_zero_percent_and_passes(self):
        code, output = run([])
        self.assertEqual(code, 0)
        self.assertIn("Status:** PASS", output)
        self.assertIn("| Extension Host jobs | 0 |", output)
        self.assertIn("| Relaunch rate | 0.00% |", output)

    def test_jobs_without_relaunches_pass(self):
        records = [record(job("VS Code Extension Host test (built server, linux)", id=i), [])
                   for i in range(5)]
        code, output = run(records)
        self.assertEqual(code, 0)
        self.assertIn("| Extension Host jobs | 5 |", output)
        self.assertIn("| Relaunch annotations | 0 |", output)

    def test_boundary_1_of_50_is_exactly_2_percent_and_passes(self):
        records = [record(job("Extension Host", id=i),
                          [relaunch()] if i == 0 else [])
                   for i in range(50)]
        code, output = run(records)
        self.assertEqual(code, 0)
        self.assertIn("| Relaunch rate | 2.00% |", output)
        self.assertIn("Status:** PASS", output)

    def test_just_over_1_of_49_exceeds_and_fails_without_github_actions_error(self):
        records = [record(job("Extension Host", id=i),
                          [relaunch()] if i == 0 else [])
                   for i in range(49)]
        out = io.StringIO()
        code, output = run(records, out=out)
        self.assertEqual(code, 1)
        self.assertIn("Status:** FAIL", output)
        self.assertNotIn("::error title=", output)

    def test_github_actions_prints_error_workflow_command_when_over(self):
        records = [record(job("Extension Host", id=i),
                          [relaunch()] if i == 0 else [])
                   for i in range(49)]
        out = io.StringIO()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(records, handle)
            path = handle.name
        try:
            code = module.main(["--input", path], environment={"GITHUB_ACTIONS": "true"}, out=out)
        finally:
            Path(path).unlink()
        self.assertEqual(code, 1)
        self.assertIn("::error title=Extension Host relaunch rate exceeded (#205)::", out.getvalue())
        self.assertIn("exceeded the 2.00% ceiling", out.getvalue())

    def test_multiple_relaunches_in_one_job_count_separately_and_share_jobs_with_relaunch(self):
        records = [record(job("Extension Host", id=1), [relaunch("a"), relaunch("b"), relaunch("c")])]
        records += [record(job("Extension Host", id=i + 2), []) for i in range(99)]
        code, output = run(records)
        table = metrics(output)
        self.assertEqual(table["Extension Host jobs"], "100")
        self.assertEqual(table["Relaunch annotations"], "3")
        self.assertEqual(table["Jobs with a relaunch"], "1")
        self.assertEqual(table["Relaunch rate"], "3.00%")
        self.assertIn("Status:** FAIL", output)
        self.assertEqual(code, 1)

    def test_non_extension_host_jobs_are_ignored(self):
        records = [
            record(job("Build and test (linux)"), [relaunch()]),
            record(job("VS Code client", id=2), [relaunch()]),
            record(job("VS Code Extension Host test", id=3), []),
        ]
        code, output = run(records)
        self.assertEqual(code, 0)
        self.assertIn("| Extension Host jobs | 1 |", output)
        self.assertIn("| Relaunch annotations | 0 |", output)

    def test_jvm_archive_and_vsix_jobs_count(self):
        records = [record(job("JVM archive and VSIX (macOS Apple Silicon)"), [relaunch()])]
        _, output = run(records)
        table = metrics(output)
        self.assertEqual(table["Extension Host jobs"], "1")
        self.assertEqual(table["Relaunch annotations"], "1")

    def test_remote_ssh_jobs_count(self):
        records = [record(job("Editor tests / VS Code Remote-SSH test (bundled VSIX, linux)"),
                          [relaunch()]),
                   record(job("Editor tests / VS Code Extension Host test (bundled VSIX, macOS)",
                              id=2), [])]
        _, output = run(records)
        table = metrics(output)
        self.assertEqual(table["Extension Host jobs"], "2")
        self.assertEqual(table["Relaunch annotations"], "1")

    def test_non_205_warnings_are_ignored(self):
        other = {"annotation_level": "warning", "title": "Some other warning",
                 "message": "stuff"}
        notice = {"annotation_level": "notice", "title": "Extension Host relaunch (#205)",
                  "message": "x"}
        records = [record(job("Extension Host"), [other, notice])]
        code, output = run(records)
        self.assertEqual(code, 0)
        self.assertIn("| Relaunch annotations | 0 |", output)

    def test_cancelled_and_skipped_jobs_are_excluded_from_denominator(self):
        records = [
            record(job("Extension Host", id=1, conclusion="cancelled"), []),
            record(job("Extension Host", id=2, conclusion="skipped"), []),
            record(job("Extension Host", id=3, conclusion="success"), [relaunch()]),
        ]
        code, output = run(records)
        table = metrics(output)
        self.assertEqual(table["Extension Host jobs"], "1")
        self.assertEqual(table["Relaunch annotations"], "1")
        self.assertEqual(table["Relaunch rate"], "100.00%")
        self.assertEqual(code, 1)

    def test_failed_and_timed_out_jobs_count_in_denominator(self):
        records = [
            record(job("Extension Host", id=1, conclusion="failure"), []),
            record(job("Extension Host", id=2, conclusion="timed_out"), []),
            record(job("Extension Host", id=3, conclusion="success"), []),
        ]
        _, output = run(records)
        self.assertEqual(metrics(output)["Extension Host jobs"], "3")

    def test_duplicate_jobs_by_id_are_counted_once(self):
        records = [
            record(job("Extension Host", id=42, run_attempt=1), [relaunch("a")]),
            record(job("Extension Host", id=42, run_attempt=2), [relaunch("b"), relaunch("c")]),
        ]
        _, output = run(records)
        table = metrics(output)
        self.assertEqual(table["Extension Host jobs"], "1")
        self.assertEqual(table["Relaunch annotations"], "1")
        self.assertEqual([row[3] for row in events(output)], ["a"])

    def test_duplicate_jobs_by_times_without_id_are_counted_once(self):
        records = [
            record({"run_id": 1, "run_attempt": 1, "status": "completed",
                    "conclusion": "success", "name": "Extension Host",
                    "started_at": "2026-11-22T10:00:00Z",
                    "completed_at": "2026-11-22T10:10:00Z"},
                   [relaunch("first")]),
            record({"run_id": 1, "run_attempt": 2, "status": "completed",
                    "conclusion": "success", "name": "Extension Host",
                    "started_at": "2026-11-22T10:00:00Z",
                    "completed_at": "2026-11-22T10:10:00Z"},
                   [relaunch("second")]),
        ]
        _, output = run(records)
        table = metrics(output)
        self.assertEqual(table["Extension Host jobs"], "1")
        self.assertEqual(table["Relaunch annotations"], "1")
        self.assertEqual([row[3] for row in events(output)], ["first"])


class IoAndFormatTest(unittest.TestCase):
    def test_markdown_output_lists_per_job_breakdown_when_relaunches_happened(self):
        records = [
            record(job("Extension Host (linux)", id=5, run_id=99),
                   [relaunch("bad window")]),
            record(job("Extension Host (macos)", id=6, run_id=99), []),
        ]
        _, output = run(records)
        self.assertEqual(events(output), [("99", "5", "Extension Host (linux)", "bad window")])

    def test_input_accepts_concatenated_pages(self):
        pages = [[record(job("Extension Host", id=1), [])], [record(job("Extension Host", id=2), [])]]
        text = "\n".join(json.dumps(page) for page in pages)
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", "-"],
                                input=text, text=True, capture_output=True, check=True)
        table = metrics(result.stdout)
        self.assertEqual(table["Extension Host jobs"], "2")
        self.assertEqual(table["Relaunch annotations"], "0")

    def test_input_accepts_one_record_per_line(self):
        text = "\n".join(json.dumps(record(job("Extension Host", id=i), [relaunch()] if i == 0 else []))
                         for i in range(3))
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", "-", "--max-rate", "0.5"],
                                input=text, text=True, capture_output=True, check=True)
        table = metrics(result.stdout)
        self.assertEqual(table["Extension Host jobs"], "3")
        self.assertEqual(table["Relaunch annotations"], "1")


class ErrorsTest(unittest.TestCase):
    def test_malformed_json_exits_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
            path = handle.name
        try:
            out = io.StringIO()
            code = module.main(["--input", path], environment={}, out=out)
        finally:
            Path(path).unlink()
        self.assertEqual(code, 2)

    def test_missing_input_file_exits_2(self):
        out = io.StringIO()
        code = module.main(["--input", "/no/such/file.json"], environment={}, out=out)
        self.assertEqual(code, 2)

    def test_invalid_max_rate_exits_2(self):
        for bad in ("not-a-number", "-0.1", "1.5"):
            with self.subTest(bad=bad):
                out = io.StringIO()
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
                    json.dump([], handle)
                    path = handle.name
                try:
                    code = module.main(["--input", path, "--max-rate", bad],
                                       environment={}, out=out)
                finally:
                    Path(path).unlink()
                self.assertEqual(code, 2, bad)

    def test_record_not_object_exits_2(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(["not an object"], handle)
            path = handle.name
        try:
            out = io.StringIO()
            code = module.main(["--input", path], environment={}, out=out)
        finally:
            Path(path).unlink()
        self.assertEqual(code, 2)

    def test_plain_job_object_without_job_key_exits_2(self):
        code, _ = run([dict(job("Extension Host"), annotations=[relaunch()])])
        self.assertEqual(code, 2)


class CustomThresholdTest(unittest.TestCase):
    def test_passes_above_default_when_max_rate_is_raised(self):
        records = [record(job("Extension Host", id=i),
                          [relaunch()] if i < 5 else [])
                   for i in range(100)]
        # 5% > 2% default, but <= 10%
        code, output = run(records, "--max-rate", "0.10")
        self.assertEqual(code, 0)
        self.assertIn("Status:** PASS", output)

    def test_fails_under_default_when_max_rate_is_lowered(self):
        records = [record(job("Extension Host", id=i),
                          [relaunch()] if i == 0 else [])
                   for i in range(100)]
        code, output = run(records, "--max-rate", "0.005")
        self.assertEqual(code, 1)
        self.assertIn("Status:** FAIL", output)


if __name__ == "__main__":
    unittest.main()
