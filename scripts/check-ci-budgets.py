#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Checks CI jobs' running times against the budgets in scripts/ci-budgets.json.
# A budget covers a job's running time only: from its step "Start the
# running-time budget timer", after checkout, installs, and cache restores, to
# its step "Check the running time against its budget", before artifact
# uploads. Queue wait and setup don't count. See
# docs/development.md#ci-running-time-budgets.
#
#   check-ci-budgets.py check --job KEY --started EPOCH [--budgets FILE]
#     Compares a job's running time, from the Unix time EPOCH to now, with its
#     budget and prints the result. Over budget, an advisory budget prints a
#     GitHub Actions warning and exits 0, and an enforcing one prints an error
#     and exits 1. It appends a line to $GITHUB_STEP_SUMMARY when that is set.
#     Exits 2 for an unknown key, a malformed budgets file, or a bad time.
#
#   check-ci-budgets.py validate [--budgets FILE] [--workflow FILE]
#     Checks the budgets file and that it matches the workflow, ci.yml by
#     default: every job but the unbudgeted ones has a budget, every budget
#     names a job of the workflow by its name, and every in-job budget is
#     checked, by its key, in its own job's check step, once. Lists every
#     problem and exits 1 if there is one.
#
#   check-ci-budgets.py summarize --runs-json FILE [--budgets FILE]
#     Reads the API's runs/RUN/jobs pages of GitHub Actions job objects, one
#     after another, from FILE or - for standard input, each job counted once.
#     For each budget, it prints a Markdown table row of the running time's
#     p50, p95, and maximum over the successful jobs, how many ran over, and
#     how many of the latest runs in a row passed on their first attempt
#     within budget, toward promoteAfterGreenRuns. A budget with "inJob":
#     false is never ready to enforce, since no step checks it.
#
# Only the Python 3 standard library is used.
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGETS = ROOT / "scripts" / "ci-budgets.json"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

TIMER_STEP = "Start the running-time budget timer"
CHECK_STEP = "Check the running time against its budget"
MODES = ("advisory", "enforcing")
KEY = re.compile(r"[a-z0-9][a-z0-9-]*")
ENTRY_FIELDS = {"name", "job", "budgetSeconds", "mode", "basis", "inJob"}
TOP_FIELDS = {"version", "promoteAfterGreenRuns", "unbudgeted", "jobs"}

# For jobs without the timer and check steps, such as jvm-package's, running
# time is the sum of the steps that are neither setup nor teardown, by name:
# the rule of the CI audit's analyze.py, with the pinned Virgil checkout, cache
# keys and restores, and the budget steps themselves as setup too.
SETUP_PREFIXES = ("Set up job", "Run actions/checkout", "Run actions/setup-", "Run actions/download-artifact",
                  "Install", "Fetch the pinned JDK", "Show ", "Version", "Restore", "Initialize containers",
                  "Create the SSH", "Run actions/cache", "Check untranslated Apple Silicon",
                  "Record the archive digest", "Check out the pinned Virgil", "Compute the cache key",
                  TIMER_STEP, CHECK_STEP)
TEARDOWN_PREFIXES = ("Run actions/upload-artifact", "Post ", "Complete job", "Stop containers")
FAILED = ("failure", "timed_out", "startup_failure")


class Usage(Exception):
    """A problem that makes a command exit with status 2."""


def shown(path):
    """A path as messages show it: relative to the repository when inside it."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def schema_problems(budgets):
    """Returns the problems of a parsed budgets file, empty when it is valid."""
    if not isinstance(budgets, dict):
        return ["the budgets file is not a JSON object"]
    problems = [f'unknown top-level field "{field}"' for field in sorted(set(budgets) - TOP_FIELDS)]
    if budgets.get("version") != 1 or not is_int(budgets.get("version")):
        problems.append('"version" is not 1')
    promote = budgets.get("promoteAfterGreenRuns")
    if not is_int(promote) or promote <= 0:
        problems.append('"promoteAfterGreenRuns" is not a positive integer')
    unbudgeted = budgets.get("unbudgeted", {})
    if not isinstance(unbudgeted, dict) or not all(
            isinstance(reason, str) and reason.strip() for reason in unbudgeted.values()):
        problems.append('"unbudgeted" is not an object of non-empty reasons')
    jobs = budgets.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        problems.append('"jobs" is not a non-empty object')
        return problems
    for key, entry in jobs.items():
        where = f'budget "{key}"'
        if not KEY.fullmatch(key):
            problems.append(f"{where}: the key is not lowercase letters, digits, and hyphens")
        if not isinstance(entry, dict):
            problems.append(f"{where}: not an object")
            continue
        problems += [f'{where}: unknown field "{field}"' for field in sorted(set(entry) - ENTRY_FIELDS)]
        for field in ("name", "basis"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                problems.append(f'{where}: "{field}" is not a non-empty string')
        if "job" in entry and (not isinstance(entry["job"], str) or not KEY.fullmatch(entry["job"])):
            problems.append(f'{where}: "job" is not a job ID')
        seconds = entry.get("budgetSeconds")
        if not is_int(seconds) or seconds <= 0:
            problems.append(f'{where}: "budgetSeconds" is not a positive integer')
        if entry.get("mode") not in MODES:
            problems.append(f'{where}: "mode" is not "advisory" or "enforcing"')
        if "inJob" in entry and not isinstance(entry["inJob"], bool):
            problems.append(f'{where}: "inJob" is not true or false')
        elif entry.get("inJob") is False and entry.get("mode") == "enforcing":
            problems.append(f'{where}: a budget with "inJob": false is not checked by a step, so it can\'t be '
                            '"enforcing"')
    return problems


def unique_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate keys {', '.join(duplicates)}")
    return dict(pairs)


def read_budgets(path):
    """Returns the parsed budgets file, refusing duplicate keys."""
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_keys)


def load_budgets(path):
    """Returns the parsed budgets file, or raises Usage if it can't be read or is malformed."""
    try:
        budgets = read_budgets(path)
    except (OSError, ValueError) as error:
        raise Usage(f"{shown(path)}: {error}")
    problems = schema_problems(budgets)
    if problems:
        raise Usage("\n".join(f"{shown(path)}: {problem}" for problem in problems))
    return budgets


def entry_job(key, entry):
    return entry.get("job", key)


def in_job(entry):
    return entry.get("inJob", True)


# check

def escape_property(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(":", "%3A").replace(",", "%2C")


def escape_data(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def elapsed_seconds(started, now):
    """The running time in whole seconds from --started to now."""
    try:
        value = float(started)
    except ValueError:
        raise Usage(f"--started {started!r} is not a number")
    if not math.isfinite(value) or value < 0:
        raise Usage(f"--started {started!r} is not a non-negative number")
    return max(0, int(now) - int(value))


def check(arguments, environment, out):
    budgets = load_budgets(arguments.budgets)
    entry = budgets["jobs"].get(arguments.job)
    if entry is None:
        raise Usage(f'{shown(arguments.budgets)}: no budget "{arguments.job}"')
    elapsed = elapsed_seconds(arguments.started, time.time())
    budget, mode, key = entry["budgetSeconds"], entry["mode"], arguments.job
    over = elapsed > budget
    if over:
        print(f"budget OVER ({mode}): {key} ran {elapsed} s > budget {budget} s", file=out)
        if environment.get("GITHUB_ACTIONS") == "true":
            command = "warning" if mode == "advisory" else "error"
            suffix = " (advisory)" if mode == "advisory" else ""
            title = escape_property(f"CI budget exceeded ({key})")
            message = escape_data(f"{key} running time {elapsed}s exceeded its {budget}s budget{suffix}")
            print(f"::{command} title={title}::{message}", file=out)
    else:
        print(f"budget PASS: {key} ran {elapsed} s (budget {budget} s, {mode})", file=out)
    summary = environment.get("GITHUB_STEP_SUMMARY")
    if summary:
        result = "over budget" if over else "within budget"
        try:
            with open(summary, "a") as file:
                file.write(f"Running-time budget: `{key}` ran {elapsed} s of its {budget} s budget "
                           f"({mode}), {result}.\n")
        except OSError as error:
            raise Usage(f"{summary}: {error}")
    return 1 if over and mode == "enforcing" else 0


# validate

class Step:
    def __init__(self, line):
        self.line, self.name, self.id, self.condition, self.working_directory, self.text = line, None, None, None, None, []

    def run(self):
        """The step's run command: a plain value, or a literal (|) or folded
        (>) block, with the folded block's lines joined by spaces."""
        for index, line in enumerate(self.text):
            match = re.fullmatch(r"(\s*(?:- )?)run:\s*(.*)", line)
            if not match:
                continue
            key, value = len(match.group(1)), match.group(2).strip()
            if not re.fullmatch(r"[|>][-+]?", value):
                return unquote(value)
            block = []
            for other in self.text[index + 1:]:
                if other.strip() and indent(other) <= key:
                    break
                block.append(other.strip())
            return (" " if value.startswith(">") else "\n").join(line for line in block if line)
        return ""


class Job:
    def __init__(self, job_id, line):
        self.id, self.line, self.name, self.uses = job_id, line, None, False
        self.default_working_directory = None
        self.steps, self.matrix = [], []


def unquote(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def indent(line):
    return len(line) - len(line.lstrip(" "))


def significant(line):
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def parse_workflow(text):
    """Returns the workflow's jobs in order: their IDs, names, steps, and
    matrix include legs. It reads only the layout that the repository's
    workflows use: two-space indentation, jobs at indent 2, and job fields at
    indent 4."""
    lines = text.splitlines()
    jobs = []
    start = next((number for number, line in enumerate(lines) if re.fullmatch(r"jobs:\s*(#.*)?", line)), None)
    if start is None:
        return jobs
    job = steps_indent = include_indent = None
    in_defaults = False
    leg = step = None
    for number in range(start + 1, len(lines)):
        line = lines[number]
        if not significant(line):
            if step is not None and line.strip():
                step.text.append(line)
            continue
        level = indent(line)
        if level == 0:
            break
        match = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*(#.*)?", line)
        if level == 2 and match:
            job = Job(match.group(1), number + 1)
            jobs.append(job)
            steps_indent = include_indent = leg = step = None
            in_defaults = False
            continue
        if job is None:
            continue
        field = re.fullmatch(r"\s*(?:- )?([A-Za-z0-9_-]+):\s*(.*)", line)
        if level == 4:
            steps_indent = include_indent = leg = step = None
            in_defaults = False
            if field and field.group(1) == "name":
                job.name = unquote(field.group(2))
            elif field and field.group(1) == "uses":
                job.uses = True
            elif field and field.group(1) == "defaults":
                in_defaults = True
            elif field and field.group(1) == "steps":
                steps_indent = 6
            continue
        if in_defaults:
            if field and level == 8 and field.group(1) == "working-directory":
                job.default_working_directory = unquote(field.group(2))
            continue
        if steps_indent is not None:
            if level == steps_indent and line.lstrip().startswith("- "):
                step = Step(number + 1)
                job.steps.append(step)
            if step is None:
                continue
            step.text.append(line)
            if field and level in (steps_indent, steps_indent + 2):
                name, value = field.group(1), field.group(2)
                if name == "name":
                    step.name = unquote(value)
                elif name == "id":
                    step.id = unquote(value)
                elif name == "if":
                    step.condition = value
                elif name == "working-directory":
                    step.working_directory = unquote(value)
            continue
        if field and field.group(1) == "include" and not field.group(2):
            include_indent = level
            continue
        if include_indent is not None:
            if level <= include_indent:
                include_indent = leg = None
            elif field:
                if level == include_indent + 2 and line.lstrip().startswith("- "):
                    leg = {}
                    job.matrix.append(leg)
                if leg is not None:
                    leg[field.group(1)] = unquote(field.group(2))
    return jobs


TEMPLATE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def expand(text, legs):
    """The values of a template for each matrix leg, or None for a reference
    other than matrix.NAME or to a name a leg lacks."""
    if not TEMPLATE.search(text):
        return [text]
    values = []
    for leg in legs or [{}]:
        def value(match):
            reference = re.fullmatch(r"matrix\.([A-Za-z0-9_-]+)", match.group(1))
            if not reference or reference.group(1) not in leg:
                raise KeyError(match.group(1))
            return leg[reference.group(1)]
        try:
            values.append(TEMPLATE.sub(value, text))
        except KeyError:
            return None
    return values


# A call's arguments run to the end of its line and of each line it continues with a backslash.
CALL = re.compile(r"check-ci-budgets\.py\s+check\b((?:[^\n]*\\\n)*[^\n]*)")
JOB_ARGUMENT = re.compile(r"--job[ =](\"[^\"]*\"|'[^']*'|\S+)")


def validate(budgets_path, workflow_path):
    """Returns the problems of the budgets file and of how the workflow checks it."""
    budgets_label, workflow_label = shown(budgets_path), shown(workflow_path)
    try:
        budgets = read_budgets(budgets_path)
    except (OSError, ValueError) as error:
        return [f"{budgets_label}: {error}"]
    problems = [f"{budgets_label}: {problem}" for problem in schema_problems(budgets)]
    if problems:
        return problems
    try:
        jobs = parse_workflow(Path(workflow_path).read_text())
    except OSError as error:
        return [f"{workflow_label}: {error}"]
    if not jobs:
        return [f"{workflow_label}: no jobs found"]
    entries, unbudgeted = budgets["jobs"], budgets.get("unbudgeted", {})
    by_id = {job.id: job for job in jobs}
    where = workflow_label
    for job_id in unbudgeted:
        if job_id not in by_id:
            problems.append(f'{budgets_label}: unbudgeted job "{job_id}" is not a job of {workflow_label}')
    for job in jobs:
        keys = [key for key, entry in entries.items() if entry_job(key, entry) == job.id]
        if job.id in unbudgeted:
            if keys:
                problems.append(f'{budgets_label}: job "{job.id}" is unbudgeted but has budgets {", ".join(keys)}')
        elif not keys:
            problems.append(f'{where}:{job.line}: job "{job.id}" has no budget in {budgets_label}')
    for key, entry in entries.items():
        job = by_id.get(entry_job(key, entry))
        if job is None:
            problems.append(f'{budgets_label}: budget "{key}" names job "{entry_job(key, entry)}", '
                            f"which {workflow_label} doesn't have")
            continue
        names = {job.name} | set(expand(job.name or "", job.matrix) or [])
        if entry["name"] not in names:
            problems.append(f'{budgets_label}: budget "{key}" has name "{entry["name"]}", but job '
                            f'"{job.id}" is named "{job.name}"')
    checked = {}
    for job in jobs:
        calls = [(step, match) for step in job.steps for match in CALL.finditer(step.run())]
        if not calls:
            continue
        if len(calls) > 1:
            problems.append(f'{where}:{calls[1][0].line}: job "{job.id}" checks a budget more than once')
        step, match = calls[0]
        timers = [index for index, other in enumerate(job.steps) if other.id == "budget"]
        argument = JOB_ARGUMENT.search(match.group(1))
        if step.name != CHECK_STEP:
            problems.append(f'{where}:{step.line}: the budget check step is not named "{CHECK_STEP}"')
        if step.condition is not None:
            problems.append(f"{where}:{step.line}: the budget check step has a condition; it runs only "
                            "when the steps before it passed")
        if job.default_working_directory not in (None, ".") and step.working_directory != ".":
            problems.append(f'{where}:{step.line}: job "{job.id}" sets default working-directory '
                            f'"{job.default_working_directory}", so the budget check step needs '
                            "working-directory: .")
        if "steps.budget.outputs.started" not in match.group(1) or "--started" not in match.group(1):
            problems.append(f"{where}:{step.line}: the budget check doesn't pass "
                            '--started "${{ steps.budget.outputs.started }}"')
        if not timers:
            problems.append(f'{where}:{step.line}: job "{job.id}" has no step with id: budget')
        elif job.steps[timers[0]].name != TIMER_STEP or job.steps.index(step) < timers[0]:
            problems.append(f'{where}:{job.steps[timers[0]].line}: the step with id: budget is not named '
                            f'"{TIMER_STEP}" or doesn\'t come before the check')
        if argument is None:
            problems.append(f"{where}:{step.line}: the budget check has no --job")
            continue
        keys = expand(unquote(argument.group(1)), job.matrix)
        if keys is None:
            problems.append(f"{where}:{step.line}: --job {argument.group(1)} uses something other than "
                            f'the matrix values of job "{job.id}"')
            continue
        for key in keys:
            entry = entries.get(key)
            if entry is None:
                problems.append(f'{where}:{step.line}: --job "{key}" has no budget in {budgets_label}')
            elif entry_job(key, entry) != job.id:
                problems.append(f'{where}:{step.line}: --job "{key}" is the budget of job '
                                f'"{entry_job(key, entry)}", not "{job.id}"')
            elif not in_job(entry):
                problems.append(f'{where}:{step.line}: --job "{key}" is a budget with "inJob": false')
            else:
                checked[key] = True
    for key, entry in entries.items():
        if in_job(entry) and key not in checked and entry_job(key, entry) in by_id:
            problems.append(f'{budgets_label}: budget "{key}" is not checked by a step of job '
                            f'"{entry_job(key, entry)}"')
    return problems


# summarize

def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def is_setup_or_teardown(name):
    return name.startswith(SETUP_PREFIXES) or name.startswith(TEARDOWN_PREFIXES)


def running_seconds(job):
    """A job object's running time: between its budget steps when it has
    them, and otherwise the sum of its running steps."""
    steps = job.get("steps") or []
    by_name = {step.get("name"): step for step in steps}
    timer, checker = by_name.get(TIMER_STEP), by_name.get(CHECK_STEP)
    if timer and checker and timer.get("completed_at") and checker.get("started_at"):
        span = parse_time(checker["started_at"]) - parse_time(timer["completed_at"])
        return max(0.0, span.total_seconds())
    total = 0.0
    for step in steps:
        name = step.get("name") or ""
        if is_setup_or_teardown(name) or step.get("conclusion") == "skipped":
            continue
        if step.get("started_at") and step.get("completed_at"):
            total += (parse_time(step["completed_at"]) - parse_time(step["started_at"])).total_seconds()
    return total


def read_jobs(text):
    """The job objects in pages of the API's runs/RUN/jobs, one after another."""
    decoder, index, jobs = json.JSONDecoder(), 0, []
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return jobs
        try:
            value, index = decoder.raw_decode(text, index)
        except ValueError as error:
            raise Usage(f"the runs JSON is not valid: {error}")
        if not (isinstance(value, dict) and isinstance(value.get("jobs"), list)
                and all(isinstance(job, dict) and "name" in job for job in value["jobs"])):
            raise Usage("the runs JSON holds something other than pages of job objects")
        jobs += value["jobs"]


def name_pattern(name):
    parts = TEMPLATE.split(name)
    # split leaves the templates' contents at odd indices.
    return re.compile("".join(re.escape(part) if index % 2 == 0 else ".+?" for index, part in enumerate(parts)))


def budget_for(name, entries):
    for key, entry in entries.items():
        if name == entry["name"]:
            return key
    for key, entry in entries.items():
        if name_pattern(entry["name"]).fullmatch(name) or name.startswith(entry["name"] + " / "):
            return key
    return None


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * fraction
    low, high = int(position), min(int(position) + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def summarize_jobs(jobs, budgets):
    """Returns one row per budget, in the file's order."""
    entries = budgets["jobs"]
    records = {key: [] for key in entries}
    seen = set()
    # Pages fetched with filter=all and filter=latest overlap, and a rerun of
    # failed jobs lists the jobs that passed again, under the new attempt with
    # new IDs and the same times: count each once, at its first attempt.
    for job in sorted(jobs, key=lambda job: job.get("run_attempt") or 1):
        identities = {("id", job.get("id"))} if job.get("id") is not None else set()
        if job.get("started_at"):
            identities.add(("times", job.get("run_id"), job.get("name"), job["started_at"], job.get("completed_at")))
        if identities & seen:
            continue
        seen |= identities
        key = budget_for(job.get("name", ""), entries)
        if key is None or job.get("status") not in (None, "completed"):
            continue
        records[key].append({"run": job.get("run_id"), "attempt": job.get("run_attempt") or 1,
                             "conclusion": job.get("conclusion"), "started": job.get("started_at") or "",
                             "seconds": running_seconds(job)})
    rows = []
    for key, entry in entries.items():
        budget = entry["budgetSeconds"]
        passed = [record["seconds"] for record in records[key] if record["conclusion"] == "success"]
        runs = {}
        for record in records[key]:
            runs.setdefault(record["run"], []).append(record)
        verdicts = []
        for run, legs in runs.items():
            first = min(leg["attempt"] for leg in legs)
            legs = [leg for leg in legs if leg["attempt"] == first]
            conclusions = [leg["conclusion"] for leg in legs]
            if first > 1 or any(conclusion in FAILED for conclusion in conclusions):
                verdict = False
            elif all(conclusion == "success" for conclusion in conclusions):
                verdict = all(leg["seconds"] <= budget for leg in legs)
            else:
                continue  # cancelled or skipped: no credit, no break
            verdicts.append((min(leg["started"] for leg in legs), run or 0, verdict))
        streak = 0
        for _, _, verdict in sorted(verdicts, reverse=True):
            if not verdict:
                break
            streak += 1
        enforceable = entry["mode"] == "advisory" and in_job(entry)
        rows.append({"key": key, "mode": entry["mode"], "budgetSeconds": budget, "jobs": len(passed),
                     "p50": percentile(passed, 0.5), "p95": percentile(passed, 0.95),
                     "max": max(passed) if passed else None,
                     "over": sum(1 for seconds in passed if seconds > budget), "greenStreak": streak,
                     "promoteAfterGreenRuns": budgets["promoteAfterGreenRuns"],
                     "enforceable": enforceable,
                     "readyToEnforce": enforceable and streak >= budgets["promoteAfterGreenRuns"]})
    return rows


def markdown(rows):
    def seconds(value):
        return "-" if value is None else f"{value:.0f} s"
    lines = ["| Budget | Mode | Budget s | Jobs | p50 | p95 | Max | Over | Green streak | Promote |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in rows:
        promote = "ready" if row["readyToEnforce"] else ("not yet" if row["enforceable"] else "-")
        lines.append(f"| `{row['key']}` | {row['mode']} | {row['budgetSeconds']} | {row['jobs']} | "
                     f"{seconds(row['p50'])} | {seconds(row['p95'])} | {seconds(row['max'])} | {row['over']} | "
                     f"{row['greenStreak']}/{row['promoteAfterGreenRuns']} | {promote} |")
    return "\n".join(lines) + "\n"


def summarize(arguments, out):
    budgets = load_budgets(arguments.budgets)
    try:
        text = sys.stdin.read() if arguments.runs_json == "-" else Path(arguments.runs_json).read_text()
    except OSError as error:
        raise Usage(f"{arguments.runs_json}: {error}")
    out.write(markdown(summarize_jobs(read_jobs(text), budgets)))
    return 0


def main(argv=None, environment=None, out=None):
    environment = os.environ if environment is None else environment
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(description="Check CI jobs' running times against scripts/ci-budgets.json.")
    commands = parser.add_subparsers(dest="command", required=True)
    checker = commands.add_parser("check", help="check one job's running time")
    checker.add_argument("--job", required=True, help="the budget's key")
    checker.add_argument("--started", required=True, help="the Unix time when the running time began")
    checker.add_argument("--budgets", default=str(BUDGETS))
    validator = commands.add_parser("validate", help="check the budgets file against the workflow")
    validator.add_argument("--budgets", default=str(BUDGETS))
    validator.add_argument("--workflow", default=str(WORKFLOW))
    summarizer = commands.add_parser("summarize", help="summarize running times of fetched jobs")
    summarizer.add_argument("--runs-json", required=True, help="a file of job objects, or - for standard input")
    summarizer.add_argument("--budgets", default=str(BUDGETS))
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "check":
            return check(arguments, environment, out)
        if arguments.command == "summarize":
            return summarize(arguments, out)
        problems = validate(arguments.budgets, arguments.workflow)
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print(f"{shown(arguments.budgets)} matches {shown(arguments.workflow)}", file=out)
        return 1 if problems else 0
    except Usage as error:
        print(f"check-ci-budgets.py: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
