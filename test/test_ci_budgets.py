#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check scripts/check-ci-budgets.py and that scripts/ci-budgets.json matches CI.

The validate tests run on the repository's own budgets and ci.yml, and on a
small workflow and budgets file in a temporary directory, each broken in one
way. The check and summarize tests use the small budgets file and job objects
shaped like the GitHub Actions API's.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-ci-budgets.py"

WORKFLOW = """\
name: CI
on: push
jobs:
  gate:
    name: Gate
    runs-on: ubuntu-24.04
    steps:
      - run: true

  build:
    # A comment.
    name: Build (linux)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
      - name: Start the running-time budget timer
        id: budget
        run: echo "started=$(date +%s)" >> "$GITHUB_OUTPUT"
      - name: Build
        run: make
      - name: Check the running time against its budget
        run: python3 scripts/check-ci-budgets.py check --job build --started "${{ steps.budget.outputs.started }}"
      - uses: actions/upload-artifact@v7
        with:
          name: build

  legs:
    name: Legs (${{ matrix.label }})
    runs-on: ubuntu-24.04
    strategy:
      matrix:
        include:
          - mode: a
            label: first
          - mode: b
            label: second
    steps:
      - name: Start the running-time budget timer
        id: budget
        run: echo "started=$(date +%s)" >> "$GITHUB_OUTPUT"
      - name: Test
        run: make test
      - name: Check the running time against its budget
        run: >-
          python3 scripts/check-ci-budgets.py check --job "legs-${{ matrix.mode }}"
          --started "${{ steps.budget.outputs.started }}"

  package:
    name: Package
    uses: ./.github/workflows/package.yml
"""

BUDGETS = {
    "version": 1,
    "promoteAfterGreenRuns": 3,
    "unbudgeted": {"gate": "Decides whether the other jobs run."},
    "jobs": {
        "build": {"name": "Build (linux)", "budgetSeconds": 60, "mode": "advisory", "basis": "Measured 40 s."},
        "legs-a": {"name": "Legs (first)", "job": "legs", "budgetSeconds": 100, "mode": "advisory",
                   "basis": "Measured 70 s."},
        "legs-b": {"name": "Legs (second)", "job": "legs", "budgetSeconds": 100, "mode": "enforcing",
                   "basis": "Measured 80 s."},
        "package": {"name": "Package", "budgetSeconds": 30, "mode": "advisory", "inJob": False,
                    "basis": "Measured 20 s."},
    },
}


def environment(**extra):
    """The test's environment without the GitHub Actions variables that change
    what check prints and writes, as when make test runs in CI."""
    result = {name: value for name, value in os.environ.items()
              if name not in ("GITHUB_ACTIONS", "GITHUB_STEP_SUMMARY")}
    result.update(extra)
    return result


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ci-budgets-")
        self.directory = Path(self.temporary.name)
        self.budgets_path = self.directory / "budgets.json"
        self.workflow_path = self.directory / "ci.yml"
        self.write(BUDGETS, WORKFLOW)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, budgets, workflow=None):
        self.budgets_path.write_text(budgets if isinstance(budgets, str) else json.dumps(budgets))
        if workflow is not None:
            self.workflow_path.write_text(workflow)

    def run_script(self, *arguments, env=None, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], input=stdin, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env or environment(), timeout=60)


class CheckTest(Fixture):
    def check(self, *arguments, env=None):
        return self.run_script("check", "--budgets", str(self.budgets_path), *arguments, env=env)

    def test_under_budget_passes_quietly(self):
        result = self.check("--job", "build", "--seconds", "45", env=environment(GITHUB_ACTIONS="true"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("budget PASS: build ran 45 s (budget 60 s, advisory)\n", result.stdout)

    def test_the_budget_itself_passes(self):
        result = self.check("--job", "build", "--seconds", "60.9")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("budget PASS: build ran 60 s", result.stdout)

    def test_over_an_advisory_budget_warns_and_passes(self):
        result = self.check("--job", "build", "--seconds", "82", env=environment(GITHUB_ACTIONS="true"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["budget OVER (advisory): build ran 82 s > budget 60 s",
                          "::warning title=CI budget exceeded (build)::build running time 82s exceeded its "
                          "60s budget (advisory)"], result.stdout.splitlines())

    def test_annotations_only_in_github_actions(self):
        result = self.check("--job", "build", "--seconds", "82")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("budget OVER (advisory): build ran 82 s > budget 60 s\n", result.stdout)

    def test_over_an_enforcing_budget_fails(self):
        result = self.check("--job", "legs-b", "--seconds", "101", env=environment(GITHUB_ACTIONS="true"))
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(["budget OVER (enforcing): legs-b ran 101 s > budget 100 s",
                          "::error title=CI budget exceeded (legs-b)::legs-b running time 101s exceeded its "
                          "100s budget"], result.stdout.splitlines())

    def test_under_an_enforcing_budget_passes(self):
        result = self.check("--job", "legs-b", "--seconds", "100")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("budget PASS: legs-b ran 100 s (budget 100 s, enforcing)", result.stdout)

    def test_started_counts_from_a_unix_time(self):
        result = self.check("--job", "build", "--started", str(int(time.time()) - 75))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertRegex(result.stdout, r"^budget OVER \(advisory\): build ran 7[56] s > budget 60 s\n$")

    def test_a_start_in_the_future_counts_as_zero(self):
        result = self.check("--job", "build", "--started", str(int(time.time()) + 3600))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("build ran 0 s", result.stdout)

    def test_summary_lines_are_appended(self):
        summary = self.directory / "summary.md"
        summary.write_text("# Earlier\n")
        self.assertEqual(0, self.check("--job", "build", "--seconds", "45", "--summary", str(summary)).returncode)
        variable = environment(GITHUB_STEP_SUMMARY=str(summary))
        self.assertEqual(0, self.check("--job", "build", "--seconds", "70", env=variable).returncode)
        self.assertEqual(["# Earlier",
                          "Running-time budget: `build` ran 45 s of its 60 s budget (advisory), within budget.",
                          "Running-time budget: `build` ran 70 s of its 60 s budget (advisory), over budget."],
                         summary.read_text().splitlines())

    def test_no_summary_without_a_file(self):
        result = self.check("--job", "build", "--seconds", "45")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["budgets.json", "ci.yml"], sorted(path.name for path in self.directory.iterdir()))

    def test_unwritable_summary_file_exits_2(self):
        missing_parent = self.directory / "no-such-dir" / "summary.md"
        result = self.check("--job", "build", "--seconds", "45", "--summary", str(missing_parent))
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertIn("summary.md", result.stderr)

    def test_an_unknown_job_is_refused(self):
        result = self.check("--job", "biuld", "--seconds", "1")
        self.assertEqual(2, result.returncode)
        self.assertIn('no budget "biuld"', result.stderr)
        self.assertEqual("", result.stdout)

    def test_bad_times_and_arguments_are_refused(self):
        for arguments in (["--job", "build"], ["--seconds", "1"],
                          ["--job", "build", "--seconds", "1", "--started", "1"],
                          ["--job", "build", "--seconds", "-1"], ["--job", "build", "--seconds", "nan"],
                          ["--job", "build", "--seconds", "abc"], ["--job", "build", "--started", ""],
                          ["--job", "build", "--started", "-5"]):
            with self.subTest(arguments=arguments):
                result = self.check(*arguments)
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertEqual("", result.stdout)

    def test_a_malformed_budgets_file_is_refused(self):
        broken = copy.deepcopy(BUDGETS)
        broken["jobs"]["build"]["budgetSeconds"] = 0
        for text, message in [("{", "budgets.json: Expecting"),
                              (json.dumps(broken), 'budget "build": "budgetSeconds" is not a positive integer'),
                              ('{"version": 1, "version": 1}', "duplicate keys version")]:
            with self.subTest(text=text[:40]):
                self.write(text)
                # Even another job's budget isn't checked against a malformed file.
                result = self.check("--job", "legs-a", "--seconds", "1")
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn(message, result.stderr)
                self.assertEqual("", result.stdout)


class ValidateTest(Fixture):
    def validate(self):
        return self.run_script("validate", "--budgets", str(self.budgets_path), "--workflow", str(self.workflow_path))

    def assert_problem(self, message, budgets=None, workflow=None):
        self.write(budgets if budgets is not None else BUDGETS, workflow)
        result = self.validate()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(message, result.stderr)

    def test_the_repository_budgets_match_ci(self):
        result = self.run_script("validate")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("scripts/ci-budgets.json matches .github/workflows/ci.yml\n", result.stdout)

    def test_every_repository_budget_starts_advisory(self):
        budgets = json.loads((ROOT / "scripts" / "ci-budgets.json").read_text())
        self.assertEqual(20, budgets["promoteAfterGreenRuns"])
        self.assertEqual({"advisory"}, {entry["mode"] for entry in budgets["jobs"].values()})

    def test_the_fixture_matches(self):
        result = self.validate()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_a_job_without_a_budget(self):
        budgets = copy.deepcopy(BUDGETS)
        del budgets["jobs"]["build"]
        self.assert_problem('job "build" has no budget', budgets)

    def test_an_unbudgeted_job_needs_its_reason(self):
        budgets = copy.deepcopy(BUDGETS)
        del budgets["unbudgeted"]
        self.assert_problem('job "gate" has no budget', budgets)
        budgets["unbudgeted"] = {"gate": " "}
        self.assert_problem('"unbudgeted" is not an object of non-empty reasons', budgets)
        budgets["unbudgeted"] = {"gate": "Reason.", "gone": "Reason."}
        self.assert_problem('unbudgeted job "gone" is not a job of', budgets)

    def test_a_budget_for_a_missing_job(self):
        budgets = copy.deepcopy(BUDGETS)
        budgets["jobs"]["deploy"] = {"name": "Deploy", "budgetSeconds": 5, "mode": "advisory", "basis": "Guess."}
        self.assert_problem('budget "deploy" names job "deploy", which', budgets)

    def test_a_budget_that_no_step_checks(self):
        budgets = copy.deepcopy(BUDGETS)
        budgets["jobs"]["build-extra"] = {"name": "Build (linux)", "job": "build", "budgetSeconds": 5,
                                          "mode": "advisory", "basis": "Guess."}
        self.assert_problem('budget "build-extra" is not checked by a step of job "build"', budgets)

    def test_a_budget_whose_name_differs(self):
        budgets = copy.deepcopy(BUDGETS)
        budgets["jobs"]["build"]["name"] = "Build (Linux)"
        self.assert_problem('budget "build" has name "Build (Linux)", but job "build" is named "Build (linux)"',
                            budgets)

    def test_invalid_fields(self):
        cases = [("mode", "strict", '"mode" is not "advisory" or "enforcing"'),
                 ("budgetSeconds", 0, '"budgetSeconds" is not a positive integer'),
                 ("budgetSeconds", -5, '"budgetSeconds" is not a positive integer'),
                 ("budgetSeconds", 1.5, '"budgetSeconds" is not a positive integer'),
                 ("budgetSeconds", True, '"budgetSeconds" is not a positive integer'),
                 ("basis", "", '"basis" is not a non-empty string'),
                 ("name", None, '"name" is not a non-empty string'),
                 ("inJob", "no", '"inJob" is not true or false'),
                 ("budget", 5, 'unknown field "budget"')]
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                budgets = copy.deepcopy(BUDGETS)
                budgets["jobs"]["build"][field] = value
                self.assert_problem(f'budget "build": {message}', budgets)
        for field, value, message in [("version", 2, '"version" is not 1'),
                                      ("promoteAfterGreenRuns", 0, '"promoteAfterGreenRuns" is not a positive'),
                                      ("jobs", {}, '"jobs" is not a non-empty object')]:
            with self.subTest(field=field):
                budgets = copy.deepcopy(BUDGETS)
                budgets[field] = value
                self.assert_problem(message, budgets)
        self.assert_problem("duplicate keys build", '{"jobs": {"build": {}, "build": {}}}')

    def test_a_typo_in_a_check_job(self):
        self.assert_problem('--job "biuld" has no budget', workflow=WORKFLOW.replace("--job build", "--job biuld"))

    def test_a_check_of_another_jobs_budget(self):
        self.assert_problem('--job "legs-a" is the budget of job "legs", not "build"',
                            workflow=WORKFLOW.replace("--job build", "--job legs-a"))

    def test_a_check_of_a_budget_outside_the_job(self):
        workflow = WORKFLOW.replace("    uses: ./.github/workflows/package.yml\n", """\
    runs-on: ubuntu-24.04
    steps:
      - name: Start the running-time budget timer
        id: budget
        run: echo "started=$(date +%s)" >> "$GITHUB_OUTPUT"
      - name: Check the running time against its budget
        run: python3 scripts/check-ci-budgets.py check --job package --started "${{ steps.budget.outputs.started }}"
""")
        self.assert_problem('--job "package" is a budget with "inJob": false', workflow=workflow)

    def test_a_matrix_check_expands_each_leg(self):
        self.assert_problem('uses something other than the matrix values of job "legs"',
                            workflow=WORKFLOW.replace("matrix.mode }}\"", "matrix.mod }}\""))
        self.assert_problem('--job "legs-c" has no budget', workflow=WORKFLOW.replace("- mode: b", "- mode: c"))

    def test_the_check_steps_are_named_ordered_and_unconditional(self):
        self.assert_problem('the budget check step is not named',
                            workflow=WORKFLOW.replace("- name: Check the running time against its budget\n"
                                                      "        run: python3 scripts/check-ci-budgets.py check "
                                                      "--job build",
                                                      "- name: Budget\n        run: python3 scripts/"
                                                      "check-ci-budgets.py check --job build"))
        self.assert_problem("the budget check step has a condition",
                            workflow=WORKFLOW.replace("        run: python3 scripts/check-ci-budgets.py check "
                                                      "--job build",
                                                      "        if: always()\n        run: python3 scripts/"
                                                      "check-ci-budgets.py check --job build"))
        self.assert_problem('job "build" has no step with id: budget',
                            workflow=WORKFLOW.replace("        id: budget\n        run: echo \"started=$(date +%s)"
                                                      "\" >> \"$GITHUB_OUTPUT\"\n      - name: Build\n",
                                                      "        run: true\n      - name: Build\n"))
        self.assert_problem("doesn't pass --started",
                            workflow=WORKFLOW.replace('--job build --started "${{ steps.budget.outputs.started }}"',
                                                      "--job build --seconds 1"))
        twice = WORKFLOW.replace("      - uses: actions/upload-artifact@v7\n",
                                 "      - run: python3 scripts/check-ci-budgets.py check --job build --seconds 1\n"
                                 "      - uses: actions/upload-artifact@v7\n")
        self.assert_problem('job "build" checks a budget more than once', workflow=twice)

    def test_default_working_directory_requires_root_check_step(self):
        with_default = WORKFLOW.replace(
            "    name: Build (linux)\n    runs-on: ubuntu-24.04\n",
            "    name: Build (linux)\n    runs-on: ubuntu-24.04\n    defaults:\n      run:\n        working-directory: clients/vscode\n",
        )
        self.assert_problem('needs working-directory: .', workflow=with_default)
        fixed = with_default.replace(
            "      - name: Check the running time against its budget\n        run: python3 scripts/check-ci-budgets.py check --job build",
            "      - name: Check the running time against its budget\n        working-directory: .\n        run: python3 scripts/check-ci-budgets.py check --job build",
        )
        self.write(BUDGETS, fixed)
        result = self.validate()
        self.assertEqual(0, result.returncode, result.stderr)


def step(name, start, end, conclusion="success"):
    return {"name": name, "status": "completed", "conclusion": conclusion,
            "started_at": f"2026-11-22T00:{start // 60:02d}:{start % 60:02d}Z",
            "completed_at": f"2026-11-22T00:{end // 60:02d}:{end % 60:02d}Z"}


def job(name, run, seconds, conclusion="success", attempt=1, ident=None, workflow="CI"):
    """A job object whose budget window, between its budget steps, is seconds long."""
    return {"id": ident if ident is not None else run * 100 + attempt * 10 + len(name) % 10,
            "run_id": run, "run_attempt": attempt, "workflow_name": workflow, "name": name,
            "status": "completed", "conclusion": conclusion,
            "started_at": f"2026-11-{run:02d}T00:{attempt:02d}:00Z", "completed_at": f"2026-11-{run:02d}T01:{attempt:02d}:00Z",
            "steps": [step("Set up job", 0, 2), step("Run actions/checkout@v7", 2, 10),
                      step("Start the running-time budget timer", 10, 10),
                      step("Build", 10, 10 + seconds, conclusion),
                      step("Check the running time against its budget", 10 + seconds, 10 + seconds),
                      step("Run actions/upload-artifact@v7", 10 + seconds, 30 + seconds),
                      step("Complete job", 30 + seconds, 31 + seconds)]}


class SummarizeTest(Fixture):
    def summarize(self, jobs, *arguments):
        path = self.directory / "jobs.json"
        path.write_text(json.dumps(jobs))
        result = self.run_script("summarize", "--runs-json", str(path), "--budgets", str(self.budgets_path),
                                 "--json", *arguments)
        self.assertEqual(0, result.returncode, result.stderr)
        return {row["key"]: row for row in json.loads(result.stdout)}

    def test_percentiles_overruns_and_streak(self):
        jobs = [job("Build (linux)", run, seconds) for run, seconds in
                [(1, 30), (2, 70), (3, 40), (4, 50), (5, 45), (6, 55)]]
        row = self.summarize(jobs)["build"]
        self.assertEqual(6, row["jobs"])
        self.assertEqual(47.5, row["p50"])
        self.assertAlmostEqual(66.25, row["p95"])
        self.assertEqual(70, row["max"])
        self.assertEqual(1, row["over"])
        # Runs 3 to 6 are within budget, run 2 is over.
        self.assertEqual(4, row["greenStreak"])
        self.assertTrue(row["readyToEnforce"])
        self.assertEqual(3, row["promoteAfterGreenRuns"])

    def test_failures_and_reruns_break_the_streak_and_cancellations_do_not(self):
        jobs = [job("Build (linux)", 1, 30), job("Build (linux)", 2, 30, "failure"),
                job("Build (linux)", 2, 31, attempt=2), job("Build (linux)", 3, 30),
                job("Build (linux)", 4, 5, "cancelled"), job("Build (linux)", 5, 30)]
        row = self.summarize(jobs)["build"]
        self.assertEqual(2, row["greenStreak"])
        self.assertEqual(4, row["jobs"])
        self.assertFalse(row["readyToEnforce"])
        only_rerun = self.summarize([job("Build (linux)", 1, 30, attempt=2)])["build"]
        self.assertEqual(0, only_rerun["greenStreak"])

    def test_matrix_legs_and_reusable_jobs_find_their_budgets(self):
        jobs = [job("Legs (first)", 1, 70), job("Legs (second)", 1, 120), job("Legs (first)", 2, 75),
                job("Package / Package and check", 1, 0), job("Package / Package and check", 2, 0)]
        rows = self.summarize(jobs)
        self.assertEqual((2, 0, 2), (rows["legs-a"]["jobs"], rows["legs-a"]["over"], rows["legs-a"]["greenStreak"]))
        self.assertEqual((1, 1, 0), (rows["legs-b"]["jobs"], rows["legs-b"]["over"], rows["legs-b"]["greenStreak"]))
        # The enforcing budget is never ready to promote.
        self.assertFalse(rows["legs-b"]["readyToEnforce"])
        self.assertEqual(2, rows["package"]["jobs"])
        self.assertEqual(0, rows["build"]["jobs"])
        self.assertIsNone(rows["build"]["p50"])

    def test_jobs_without_budget_steps_count_their_running_steps(self):
        reusable = {"id": 7, "run_id": 1, "run_attempt": 1, "name": "Package / Package and check",
                    "status": "completed", "conclusion": "success", "started_at": "2026-11-22T00:00:00Z",
                    "steps": [step("Set up job", 0, 3), step("Run actions/checkout@v7", 3, 9),
                              step("Fetch the pinned JDK", 9, 20), step("Install client dependencies", 20, 30),
                              step("Package the archive", 30, 45), step("Check the archive", 45, 46),
                              step("Skipped", 46, 50, "skipped"), step("Post Run actions/checkout@v7", 50, 52)]}
        self.assertEqual(16, self.summarize([reusable])["package"]["max"])

    def test_other_workflows_and_repeated_jobs_count_once_or_not_at_all(self):
        original = job("Build (linux)", 1, 30)
        copied = dict(original, id=999, run_attempt=2)
        other = job("Build (linux)", 2, 500, workflow="Release")
        row = self.summarize([original, original, copied, other])["build"]
        self.assertEqual((1, 30, 1), (row["jobs"], row["max"], row["greenStreak"]))
        everything = self.summarize([original, other], "--workflow-name", "")["build"]
        self.assertEqual(2, everything["jobs"])

    def test_concatenated_pages_from_standard_input_print_a_table(self):
        pages = (json.dumps({"total_count": 1, "jobs": [job("Build (linux)", 1, 30)]})
                 + json.dumps([{"jobs": [job("Legs (first)", 1, 70)]}]))
        result = self.run_script("summarize", "--runs-json", "-", "--budgets", str(self.budgets_path), stdin=pages)
        self.assertEqual(0, result.returncode, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].startswith("| Budget | Mode | Budget s |"), lines)
        self.assertIn("| `build` | advisory | 60 | 1 | 30 s | 30 s | 30 s | 0 | 1/3 | not yet |", lines)
        self.assertIn("| `legs-b` | enforcing | 100 | 0 | - | - | - | 0 | 0/3 | - |", lines)

    def test_bad_input_is_refused(self):
        for text in ("{", '[{"total_count": 1}]', "3"):
            with self.subTest(text=text):
                result = self.run_script("summarize", "--runs-json", "-", "--budgets", str(self.budgets_path),
                                         stdin=text)
                self.assertEqual(2, result.returncode, result.stdout)


if __name__ == "__main__":
    unittest.main()
