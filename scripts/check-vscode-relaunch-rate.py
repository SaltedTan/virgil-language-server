#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Counts #205 pre-activation Extension Host relaunches across completed CI
# and nightly runs and fails if the relaunch rate exceeds a ceiling, as the
# counted-exception policy for the VS Code Extension Host retry says. See
# docs/development.md#the-205-counted-exception and
# clients/vscode/README.md#extension-host-test.
#
#   check-vscode-relaunch-rate.py --input FILE [--max-rate 0.02] [--json]
#
# Reads FILE, or - for standard input, as a list (or concatenated JSON
# objects / arrays, in the shape of check-ci-budgets.py's read_jobs) of
# records of GitHub Actions job objects with their Check Run annotations:
#
#   {"job": <job object>, "annotations": [<annotation object>, ...]}
#
# or a plain job object with an optional "annotations" key. Completed jobs
# (conclusion success, failure, or timed_out; cancelled and skipped are
# excluded) whose name contains "Extension Host" or "JVM archive and VSIX"
# are counted. A #205 relaunch is any annotation whose annotation_level is
# "warning" and whose title is exactly "Extension Host relaunch (#205)".
# Jobs with the same id, or the same (run_id, name, started_at,
# completed_at), are counted once; when a job appears more than once (the
# GitHub Actions API returns reruns with new attempt numbers), the record
# with the lowest run_attempt wins, matching check-ci-budgets.py.
#
# Exit codes:
#   0 within threshold (rate <= --max-rate, including zero jobs)
#   1 over threshold; also prints a ::error workflow command in GitHub Actions
#   2 usage, malformed input, or invalid --max-rate
#
# Only the Python 3 standard library is used; compatible with Python 3.9+.
import argparse
import json
import os
import sys
from pathlib import Path

RELAUNCH_TITLE = "Extension Host relaunch (#205)"
COUNTED_CONCLUSIONS = {"success", "failure", "timed_out"}
JOB_NAME_MARKERS = ("Extension Host", "JVM archive and VSIX")


class Usage(Exception):
    """A problem that makes the command exit with status 2."""


def extension_host_job(job):
    """Whether job is a completed Extension Host (or JVM archive) job."""
    if not isinstance(job, dict):
        return False
    status = job.get("status")
    if status not in (None, "completed"):
        return False
    if job.get("conclusion") not in COUNTED_CONCLUSIONS:
        return False
    name = job.get("name") or ""
    return any(marker in name for marker in JOB_NAME_MARKERS)


def is_relaunch(annotation):
    return (isinstance(annotation, dict)
            and annotation.get("annotation_level") == "warning"
            and annotation.get("title") == RELAUNCH_TITLE)


def read_records(text):
    """Reads the input as a list or as concatenated JSON values, each a list
    or a single record. Returns an iterable of raw records."""
    decoder, index, records = json.JSONDecoder(), 0, []
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return records
        try:
            value, index = decoder.raw_decode(text, index)
        except ValueError as error:
            raise Usage(f"the input is not valid JSON: {error}")
        if isinstance(value, list):
            records.extend(value)
        elif isinstance(value, dict):
            records.append(value)
        else:
            raise Usage("the input holds something other than records of job objects")


def normalize(record):
    """Returns (job, annotations) for a record. A record may be either
    {"job": ..., "annotations": [...]} or a plain job object with an
    optional "annotations" key."""
    if not isinstance(record, dict):
        raise Usage("record is not a JSON object")
    if "job" in record:
        job = record.get("job")
        if not isinstance(job, dict):
            raise Usage('record "job" is not a JSON object')
        annotations = record.get("annotations", [])
    else:
        job = record
        annotations = record.get("annotations", [])
    if not isinstance(annotations, list):
        raise Usage('record "annotations" is not a JSON array')
    return job, annotations


def deduplicate(records):
    """Pairs of (job, annotations), each job at most once: by id if given,
    otherwise by (run_id, name, started_at, completed_at). Earlier attempts
    win."""
    seen, result = set(), []
    paired = []
    for record in records:
        job, annotations = normalize(record)
        paired.append((job, annotations))
    paired.sort(key=lambda pair: pair[0].get("run_attempt") or 1)
    for job, annotations in paired:
        identities = set()
        if job.get("id") is not None:
            identities.add(("id", job.get("id")))
        if job.get("started_at"):
            identities.add(("times", job.get("run_id"), job.get("name"),
                            job["started_at"], job.get("completed_at")))
        if identities & seen:
            continue
        seen |= identities
        result.append((job, annotations))
    return result


def compute(pairs, max_rate):
    """Returns a summary dict for the deduplicated pairs."""
    events = []
    jobs = 0
    jobs_with_relaunch = 0
    for job, annotations in pairs:
        if not extension_host_job(job):
            continue
        jobs += 1
        relaunches = [a for a in annotations if is_relaunch(a)]
        if relaunches:
            jobs_with_relaunch += 1
        for annotation in relaunches:
            events.append({
                "runId": job.get("run_id"),
                "jobId": job.get("id"),
                "name": job.get("name"),
                "message": annotation.get("message", ""),
            })
    relaunches = len(events)
    rate = (relaunches / jobs) if jobs > 0 else 0.0
    exceeded = jobs > 0 and rate > max_rate
    return {
        "jobs": jobs,
        "relaunches": relaunches,
        "jobsWithRelaunch": jobs_with_relaunch,
        "rate": rate,
        "maxRate": max_rate,
        "exceeded": exceeded,
        "relaunchEvents": events,
    }


def markdown(summary):
    """Human-readable Markdown report."""
    status = "FAIL" if summary["exceeded"] else "PASS"
    rate_percent = summary["rate"] * 100
    max_percent = summary["maxRate"] * 100
    lines = [
        f"**Status:** {status}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Extension Host jobs | {summary['jobs']} |",
        f"| Relaunch annotations | {summary['relaunches']} |",
        f"| Jobs with a relaunch | {summary['jobsWithRelaunch']} |",
        f"| Relaunch rate | {rate_percent:.2f}% |",
        f"| Threshold | {max_percent:.2f}% |",
    ]
    if summary["relaunchEvents"]:
        lines += ["", "### Relaunch events", "",
                  "| Run | Job | Name | Message |",
                  "| ---: | ---: | --- | --- |"]
        for event in summary["relaunchEvents"]:
            message = (event["message"] or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
            lines.append(f"| {event['runId']} | {event['jobId']} | {event['name']} | {message} |")
    return "\n".join(lines) + "\n"


def parse_rate(text):
    try:
        value = float(text)
    except ValueError:
        raise Usage(f"--max-rate {text!r} is not a number")
    if not (0.0 <= value <= 1.0):
        raise Usage(f"--max-rate {text!r} is not between 0 and 1")
    return value


def read_input(path):
    try:
        return sys.stdin.read() if path == "-" else Path(path).read_text()
    except OSError as error:
        raise Usage(f"{path}: {error}")


def main(argv=None, environment=None, out=None):
    environment = os.environ if environment is None else environment
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(
        description="Count VS Code Extension Host #205 relaunches and gate on the rate.")
    parser.add_argument("--input", required=True,
                        help="a file of job records, or - for standard input")
    parser.add_argument("--max-rate", default="0.02",
                        help="relaunch rate ceiling, between 0 and 1 (default: 0.02, i.e. 2%%)")
    parser.add_argument("--json", action="store_true",
                        help="write the summary as JSON instead of Markdown")
    arguments = parser.parse_args(argv)
    try:
        max_rate = parse_rate(arguments.max_rate)
        text = read_input(arguments.input)
        records = read_records(text)
        pairs = deduplicate(records)
        summary = compute(pairs, max_rate)
    except Usage as error:
        print(f"check-vscode-relaunch-rate.py: {error}", file=sys.stderr)
        return 2
    if arguments.json:
        json.dump(summary, out, indent=2, sort_keys=True)
        out.write("\n")
    else:
        out.write(markdown(summary))
    if summary["exceeded"]:
        if environment.get("GITHUB_ACTIONS") == "true":
            message = (f"Extension Host relaunch rate {summary['rate'] * 100:.2f}% exceeded "
                       f"the {max_rate * 100:.2f}% ceiling "
                       f"({summary['relaunches']} relaunches in {summary['jobs']} jobs)")
            print(f"::error title=Extension Host relaunch rate exceeded (#205)::{message}", file=out)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
