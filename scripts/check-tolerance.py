#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Qualify one full tolerance capture against the phase-3a release floors.

Capture and qualification are separate commands. `bench-tolerance.py` writes the
measurements; this script only reads them and never talks to the server. It
refuses a smoke, suite, or filtered capture, and checks the stable corpus
inventory before it believes any aggregate count.

The floors below are the accepted v1.0/M6 gate from issue #335. Changing one
must explain why, and must keep older captures comparable: raise a floor only
with a measurement that supports it, and never delete a historical result. The
inventory in `test/benchmarks/tolerance-inventory.json` is the corpus side of
the same rule. A capture that lacks a state, mode, session step, or oracle is a
failure, not a smaller run.

A zero floor is an uncovered class, not a recovery success. Phase 3a does not
recover truncated files or a lost method's own closing brace, so those rows are
printed as uncovered even when their count is zero.

Every verdict the scorecard counts is rescored from the recorded answers with
`bench-tolerance.py`'s own `score`, so a capture whose verdicts its answers
don't support fails. A capture whose answers and verdicts were both rewritten
consistently can't be detected without rerunning the benchmark.
"""
import argparse
import collections
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "test/benchmarks/tolerance-inventory.json"
MODES = ("warm", "cold")
FEATURES = ("definition", "hover", "references", "diagnostics")
SUITES = ("repair", "replay", "negative", "extra", "heldout")
EDIT_KINDS = ("body", "header", "whole_file")
VERDICTS = ("available", "correct", "partial", "missing", "wrong", "matches_oracle")
ANSWER_KEY = re.compile(r"^\d+:(definition|hover|references)$")
OPENERS = ("if-opener", "loop-opener", "match-opener", "method-opener")

# The gate, measured independently for warm and cold. Each entry is
# (required count, expected denominator). `primary` is the 45 repair and 140
# replay states. Held-out subgroups are counted from the inventory's stable
# state IDs, never from totals alone.
FLOORS = {
    "primary": {
        "all_features": (180, 185),
    },
    "repair": {
        "body": (34, 37),
        "header": (6, 6),
        "whole_file": (0, 2),
    },
    "replay": {
        "body": (78, 78),
        "header": (62, 62),
    },
    "heldout": {
        "all_features": (44, 94),
        "body_quarantine": (24, 24),
        "lost_openers": (15, 28),
        "seed_317_block_closers": (2, 7),
        "seed_320_block_closers": (3, 20),
        "method_closers": (0, 15),
    },
    "exact": {
        "definition": (616, 627),
        "hover": (616, 627),
        "references": (589, 627),
        "diagnostics": (180, 185),
    },
}

# Shown for context, not enforced as floors by this issue.
BASELINES = {
    "primary_all_exact": (153, 185),
    "heldout_all_exact": (40, 94),
}


def _load_driver():
    """The benchmark driver, whose scoring this script must not copy."""
    spec = importlib.util.spec_from_file_location("bench_tolerance", ROOT / "scripts/bench-tolerance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = _load_driver()


def load_inventory(path=INVENTORY_PATH):
    with Path(path).open() as source:
        return json.load(source)


def _fail(problems, message):
    problems.append(message)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_measure(value):
    """A latency, sample, count, or size: finite, nonnegative, and not a bool."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _check_fields(problems, where, row, fields):
    """Report every missing or wrong-typed field; say whether all are well typed."""
    complete = True
    for field, kind, label in fields:
        if field not in row:
            _fail(problems, where + " is missing " + field)
            complete = False
        elif not isinstance(row[field], kind) or (kind is int and isinstance(row[field], bool)):
            article = "an" if label[0] in "aeiou" else "a"
            _fail(problems, where + " " + field + " is not " + article + " " + label)
            complete = False
    return complete


# What bench-tolerance.py always writes for a state record. The first five
# identify it; a record that lacks them stops at this point.
STATE_FIELDS = (
    ("id", str, "string"), ("suite", str, "string"), ("mode", str, "string"),
    ("edit_kind", str, "string"), ("oracle_id", int, "integer"),
    ("answers", dict, "object"), ("scores", dict, "object"),
    ("samples", list, "array"), ("expectation_mismatches", list, "array"),
    ("configuration", dict, "object"), ("texts", dict, "object"),
    ("snapshot", dict, "object"), ("changed", list, "array"),
    ("server_pid", int, "integer"))

# What execute_session always writes for one step, in both the intended-text
# oracle and a measured mode.
SESSION_STEP_FIELDS = (
    ("id", str, "string"), ("suite", str, "string"), ("mode", str, "string"),
    ("edit_kind", str, "string"), ("actions", list, "array"),
    ("answers", dict, "object"), ("samples", list, "array"),
    ("queries", list, "array"), ("snapshot", dict, "object"),
    ("measured_probes", list, "array"))

# The measured steps add these; the intended-text oracle records no scores.
SESSION_MEASURED_FIELDS = (
    ("scores", dict, "object"), ("substitution_kinds", dict, "object"))


def _check_markers(problems, where, row):
    """What bench-tolerance.py leaves on a record whose run failed."""
    for marker in ("error", "failed_record", "transcript"):
        if marker in row:
            _fail(problems, where + " is a failed record (" + marker + ")")


def _verdict_problem(problems, where, verdict):
    if not isinstance(verdict, dict):
        _fail(problems, where + ": score is not an object")
        return False
    for field in VERDICTS:
        if not isinstance(verdict.get(field), bool):
            _fail(problems, where + ": score field " + field + " is not a boolean")
            return False
    return True


def _rescore(problems, where, key, answer, verdict, expected, allow_absent):
    """Score one recorded answer against its oracle answer as bench-tolerance.py
    does, report a recorded verdict that differs, and return the new verdict."""
    if not isinstance(expected, dict) or key not in expected:
        _fail(problems, where + ": the oracle has no " + key + " answer to rescore against")
        return None
    if not _answer_type_ok(key, answer) or not _answer_type_ok(key, expected[key]):
        _fail(problems, where + ": the answer or its oracle answer has the wrong type to rescore")
        return None
    rescored = driver.score(key.split(":")[-1], answer, expected[key], allow_absent)
    if verdict != rescored:
        _fail(problems, where + ": recorded verdict " + json.dumps(verdict, sort_keys=True)
              + " differs from rescoring its answer: " + json.dumps(rescored, sort_keys=True))
    return rescored


def _check_measures(problems, where, row, required=()):
    """Every latency, sample, count, and size in a measurement record."""
    values = []
    for field in required:
        if field not in row:
            _fail(problems, where + " is missing " + field)
    for field in ("state_latency_ms", "worker_replacements", "prefix_bytes", "suffix_bytes"):
        if field in row:
            values.append((field, row[field]))
    if isinstance(row.get("samples"), list):
        for index, sample in enumerate(row["samples"]):
            name = "samples[" + str(index) + "].latency_ms"
            values.append((name, sample.get("latency_ms") if isinstance(sample, dict) else None))
    if isinstance(row.get("resources"), list):
        for index, entry in enumerate(row["resources"]):
            entry = entry if isinstance(entry, dict) else {}
            values.append(("resources[" + str(index) + "].rss_bytes", entry.get("rss_bytes")))
            # Only Linux reports a peak.
            if entry.get("peak_rss_bytes") is not None:
                values.append(("resources[" + str(index) + "].peak_rss_bytes", entry["peak_rss_bytes"]))
    if isinstance(row.get("texts"), dict):
        for role, files in row["texts"].items():
            for path, text in (files.items() if isinstance(files, dict) else ()):
                values.append(("texts." + role + "." + path + ".bytes",
                               text.get("bytes") if isinstance(text, dict) else None))
    if isinstance(row.get("excluded_bodies"), dict):
        values.extend(("excluded_bodies." + path, count) for path, count in row["excluded_bodies"].items())
    for name, value in values:
        if not _is_measure(value):
            _fail(problems, where + " " + name + " is not a finite nonnegative number: " + repr(value))


def _answer_feature(key):
    """The feature of an answer key, or None when it is not one."""
    if key == "diagnostics":
        return "diagnostics"
    match = ANSWER_KEY.match(key) if isinstance(key, str) else None
    return match.group(1) if match else None


def _answer_type_ok(key, value):
    kind = dict if _answer_feature(key) in ("definition", "hover") else list
    return value is None or isinstance(value, kind)


def _answer_type_problem(problems, where, key, value):
    feature = _answer_feature(key)
    if not _answer_type_ok(key, value):
        kind = "an object" if feature in ("definition", "hover") else "an array"
        _fail(problems, where + ": " + feature + " answer is not " + kind + " or null")


def _check_answer_values(problems, where, answers, expected_count=None):
    if not isinstance(answers, dict):
        _fail(problems, where + ": answers is not an object")
        return
    if expected_count is not None and len(answers) != expected_count:
        _fail(problems, where + ": " + str(len(answers)) + " answer keys, expected "
              + str(expected_count))
    for key, value in answers.items():
        if _answer_feature(key) is None:
            _fail(problems, where + ": unknown answer key " + repr(key))
            continue
        _answer_type_problem(problems, where + " " + key, key, value)


def _expected_answers(queries):
    """The answer keys bench-tolerance.py writes for a state with {queries} queries."""
    keys = {str(index) + ":" + feature for index in range(queries) for feature in FEATURES[:3]}
    keys.add("diagnostics")
    return keys


def _check_oracle_answers(problems, where, oracle, queries):
    """An oracle must answer exactly the queries of the state that owns it."""
    answers = oracle.get("answers")
    if isinstance(answers, dict):
        expected = _expected_answers(queries)
        if set(answers) != expected:
            _fail(problems, where + ": answer keys differ from a " + str(queries)
                  + "-query oracle: " + repr(sorted(answers)[:4]))
        for key, value in answers.items():
            if _answer_feature(key) is None:
                continue
            _answer_type_problem(problems, where + " " + key, key, value)
    source_queries = oracle.get("queries")
    if isinstance(source_queries, list) and len(source_queries) != queries:
        _fail(problems, where + ": " + str(len(source_queries)) + " queries, expected "
              + str(queries))
    paths = oracle.get("diagnostic_paths")
    if isinstance(paths, list) and not all(isinstance(path, str) for path in paths):
        _fail(problems, where + ": diagnostic paths are not strings")


def _check_answer_indices(problems, where, answers, queries):
    """Every query must have exactly its definition, hover, and references."""
    if not isinstance(answers, dict) or not _is_int(queries):
        return
    per_query = collections.defaultdict(set)
    for key in answers:
        if key == "diagnostics":
            continue
        match = ANSWER_KEY.match(key) if isinstance(key, str) else None
        if match is not None:
            per_query[int(key.split(":")[0])].add(match.group(1))
    if set(per_query) != set(range(queries)):
        _fail(problems, where + ": answer query indices are " + repr(sorted(per_query))
              + ", expected " + repr(list(range(queries))))
    for index, features in per_query.items():
        if features != set(FEATURES[:3]):
            _fail(problems, where + ": query " + str(index) + " has " + repr(sorted(features)))
    if "diagnostics" not in answers:
        _fail(problems, where + ": no diagnostics answer")


def _check_answers(problems, where, answers, scores, expected_count, oracle_answers,
                   allow_absent=False):
    """Check one record's answers and rescore each against {oracle_answers},
    or report it unscored when the oracle is unknown (already a failure)."""
    if not isinstance(answers, dict):
        _fail(problems, where + ": answers is not an object")
        return False
    if not isinstance(scores, dict):
        _fail(problems, where + ": scores is not an object")
        return False
    if not scores:
        _fail(problems, where + ": no scored answers")
    if set(answers) != set(scores):
        _fail(problems, where + ": answers and scores have different keys")
    if expected_count is not None and len(scores) != expected_count:
        _fail(problems, where + ": " + str(len(scores)) + " answer keys, expected " + str(expected_count))
    for key, value in answers.items():
        if _answer_feature(key) is None:
            _fail(problems, where + ": unknown answer key " + repr(key))
            continue
        if key in scores and _verdict_problem(problems, where + " " + key, scores[key]):
            _answer_type_problem(problems, where + " " + key, key, value)
            if oracle_answers is not None:
                _rescore(problems, where + " " + key, key, value, scores[key], oracle_answers,
                         allow_absent)
    return True


def _filter_flags(*parts):
    """The capture-filter flags among {parts}, as they appear on a command line."""
    return [flag for flag in ("--smoke", "--suite", "--only")
            if any(part == flag or part.startswith(flag + "=") for part in parts)]


def _check_capture_kind(capture, problems):
    """Reject anything that isn't one unfiltered full capture."""
    meta = capture.get("capture")
    command = capture.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
        _fail(problems, "command is missing or is not a string array")
        command = []
    # The command is the only evidence of what actually ran, so a filter in it
    # is fatal whatever the metadata says.
    filters = _filter_flags(*command)
    if filters:
        _fail(problems, "not a full capture: command has " + ", ".join(filters))
    if meta is None:
        # Older captures record the filters only in the command. They remain
        # readable, but an unqualified command cannot show that it was full.
        return
    if not isinstance(meta, dict):
        _fail(problems, "capture metadata is not an object")
        return
    if meta.get("smoke") is not False:
        _fail(problems, "not a full capture: smoke is not false")
    if meta.get("suite") is not None:
        _fail(problems, "not a full capture: suite is " + repr(meta.get("suite")))
    if meta.get("only") is not None:
        _fail(problems, "not a full capture: only is " + repr(meta.get("only")))
    if meta.get("full") is not True:
        _fail(problems, "not a full capture: full is not true")
    recorded = set()
    if meta.get("smoke"):
        recorded.add("--smoke")
    if meta.get("suite") is not None:
        recorded.add("--suite")
    if meta.get("only") is not None:
        recorded.add("--only")
    if recorded != set(filters):
        _fail(problems, "not a full capture: metadata records filters " + repr(sorted(recorded))
              + " but the command has " + repr(filters))


def _inventory_index(inventory, problems):
    """Validate the inventory itself and return its state and session maps."""
    if not isinstance(inventory, dict):
        _fail(problems, "inventory is not an object")
        return {}, {}
    if inventory.get("schema") != 1:
        _fail(problems, "inventory schema is not 1")
    state_rows = inventory.get("states")
    session_rows = inventory.get("sessions")
    if not isinstance(state_rows, list) or not isinstance(session_rows, list):
        _fail(problems, "inventory states or sessions is not an array")
        return {}, {}
    states, sessions = {}, {}
    for index, row in enumerate(state_rows):
        where = "inventory.states[" + str(index) + "]"
        if not isinstance(row, dict):
            _fail(problems, where + " is not an object")
            continue
        state_id = row.get("id")
        if not isinstance(state_id, str) or not state_id:
            _fail(problems, where + " has no string id")
            continue
        if state_id in states:
            _fail(problems, where + " duplicates state ID " + state_id)
        if row.get("suite") not in SUITES:
            _fail(problems, where + " has an unknown suite")
        if row.get("edit_kind") not in EDIT_KINDS:
            _fail(problems, where + " has an unknown edit kind")
        if not _is_int(row.get("queries")) or row["queries"] < 1:
            _fail(problems, where + " has no positive query count")
        if not _is_int(row.get("oracle")) or row["oracle"] < 0:
            _fail(problems, where + " has no intended-text oracle group")
        if not isinstance(row.get("allow_absent", False), bool):
            _fail(problems, where + " allow_absent is not a boolean")
        if row.get("suite") == "heldout":
            if row.get("group") not in ("body-quarantine", "brace-317", "closer-320"):
                _fail(problems, where + " has no held-out group")
            if not isinstance(row.get("heldout_kind"), str):
                _fail(problems, where + " has no held-out kind")
        states[state_id] = row
    for index, row in enumerate(session_rows):
        where = "inventory.sessions[" + str(index) + "]"
        if not isinstance(row, dict):
            _fail(problems, where + " is not an object")
            continue
        session_id = row.get("id")
        if not isinstance(session_id, str) or not session_id:
            _fail(problems, where + " has no string id")
            continue
        if session_id in sessions:
            _fail(problems, where + " duplicates session ID " + session_id)
        steps = row.get("steps")
        if not isinstance(steps, list) or not steps:
            _fail(problems, where + " has no steps")
            continue
        seen = set()
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("id"), str) or not step["id"]:
                _fail(problems, where + " has a step without a string id")
                continue
            if step["id"] in seen:
                _fail(problems, where + " duplicates step ID " + step["id"])
            seen.add(step["id"])
            if not _is_int(step.get("queries")) or step["queries"] < 0:
                _fail(problems, where + " step " + step["id"] + " has no query count")
        sessions[session_id] = row
    counts = inventory.get("counts", {})
    if counts:
        expected = {
            "states": len(states),
            "state_mode_records": 2 * len(states),
            "negative_controls": sum(row.get("suite") == "negative" for row in states.values()),
            "sessions": len(sessions),
            "session_step_mode_records": 2 * sum(len(row["steps"]) for row in sessions.values()
                                                 if isinstance(row.get("steps"), list)),
            "oracles": len({row.get("oracle") for row in states.values()}),
            "session_oracles": len(sessions),
        }
        for name, value in expected.items():
            if counts.get(name) != value:
                _fail(problems, "inventory counts." + name + " says " + repr(counts.get(name))
                      + ", inventory has " + str(value))
    return states, sessions


def _check_oracles(capture, problems):
    oracles = capture.get("oracles")
    if not isinstance(oracles, list):
        _fail(problems, "oracles is not an array")
        return {}
    by_id = {}
    for index, oracle in enumerate(oracles):
        where = "oracles[" + str(index) + "]"
        if not isinstance(oracle, dict):
            _fail(problems, where + " is not an object")
            continue
        _check_markers(problems, where, oracle)
        oracle_id = oracle.get("id")
        if not _is_int(oracle_id):
            _fail(problems, where + " has no integer id")
            continue
        if oracle_id in by_id:
            _fail(problems, where + " duplicates oracle ID " + str(oracle_id))
        by_id[oracle_id] = oracle
        if not isinstance(oracle.get("answers"), dict) or not oracle["answers"]:
            _fail(problems, where + " has no answers")
        if not isinstance(oracle.get("queries"), list):
            _fail(problems, where + " has no queries")
        if not isinstance(oracle.get("configuration"), dict):
            _fail(problems, where + " has no configuration")
        if not isinstance(oracle.get("diagnostic_paths"), list):
            _fail(problems, where + " has no diagnostic paths")
    if set(by_id) != set(range(len(oracles))):
        _fail(problems, "oracle IDs are not unique and contiguous from zero")
    return by_id


def _check_states(capture, states_inventory, oracles, problems):
    records = capture.get("states")
    if not isinstance(records, list):
        _fail(problems, "states is not an array")
        return []
    pairs = collections.defaultdict(list)
    seen = set()
    used_oracles = set()
    state_oracles = collections.defaultdict(set)
    for index, row in enumerate(records):
        where = "states[" + str(index) + "]"
        if not isinstance(row, dict):
            _fail(problems, where + " is not an object")
            continue
        _check_markers(problems, where, row)
        if not _check_fields(problems, where, row, STATE_FIELDS):
            # A record whose identity is unreadable cannot be compared with the
            # inventory, grouped with a mode, or used as an oracle key.
            continue
        state_id = row["id"]
        mode = row["mode"]
        pairs[state_id].append(mode)
        if (state_id, mode) in seen:
            _fail(problems, where + " duplicates state/mode " + state_id + "/" + repr(mode))
        seen.add((state_id, mode))
        expected = states_inventory.get(state_id)
        if expected is None:
            _fail(problems, where + " has unknown state ID " + state_id)
        else:
            if row.get("suite") != expected.get("suite"):
                _fail(problems, where + " suite differs from the inventory")
            if row.get("edit_kind") != expected.get("edit_kind"):
                _fail(problems, where + " edit kind differs from the inventory")
        if mode not in MODES:
            _fail(problems, where + " has unknown mode " + repr(mode))
        if isinstance(row.get("expectation_mismatches"), list):
            for mismatch in row["expectation_mismatches"]:
                if not isinstance(mismatch, str):
                    _fail(problems, where + " has a non-string expectation mismatch")
        if isinstance(row.get("samples"), list) and not row["samples"]:
            _fail(problems, where + " has no samples")
        _check_measures(problems, where, row, ("state_latency_ms", "worker_replacements"))
        expected_answers = None
        if expected is not None and _is_int(expected.get("queries")):
            expected_answers = 3 * expected["queries"] + 1
            _check_answer_indices(problems, where, row.get("answers"), expected["queries"])
        oracle_id = row["oracle_id"]
        oracle = oracles.get(oracle_id)
        _check_answers(problems, where + " (" + state_id + " " + mode + ")", row.get("answers"),
                       row.get("scores"), expected_answers,
                       oracle.get("answers") if oracle is not None else None,
                       (expected or {}).get("allow_absent", False))
        if oracle is None:
            _fail(problems, where + " references missing oracle " + str(oracle_id))
            continue
        used_oracles.add(oracle_id)
        state_oracles[state_id].add(oracle_id)
    # Each state's two modes measure the same intended text. States whose text
    # is identical share one oracle, so the inventory names that intended-text
    # group: a group maps to exactly one oracle and one oracle never stands in
    # for two groups.
    for state_id, oracle_ids in state_oracles.items():
        if len(oracle_ids) != 1:
            _fail(problems, "state " + state_id + " references oracles "
                  + repr(sorted(oracle_ids)) + ", expected one shared by both modes")
    group_oracles = collections.defaultdict(set)
    for state_id, oracle_ids in state_oracles.items():
        expected = states_inventory.get(state_id) or {}
        if _is_int(expected.get("oracle")) and len(oracle_ids) == 1:
            group_oracles[expected["oracle"]].update(oracle_ids)
    for group, oracle_ids in group_oracles.items():
        if len(oracle_ids) != 1:
            _fail(problems, "states that share intended text group " + str(group)
                  + " reference oracles " + repr(sorted(oracle_ids)) + ", expected one")
    oracle_groups = collections.defaultdict(set)
    for group, oracle_ids in group_oracles.items():
        for oracle_id in oracle_ids:
            oracle_groups[oracle_id].add(group)
    for oracle_id, groups in oracle_groups.items():
        if len(groups) != 1:
            _fail(problems, "oracle " + str(oracle_id) + " stands in for intended text groups "
                  + repr(sorted(groups)))
    groups = {row.get("oracle") for row in states_inventory.values() if _is_int(row.get("oracle"))}
    if len(oracles) != len(groups):
        _fail(problems, "capture has " + str(len(oracles)) + " oracles, expected "
              + str(len(groups)))
    checked_oracles = set()
    for state_id in state_oracles:
        oracle_ids = state_oracles[state_id]
        if len(oracle_ids) != 1:
            continue
        oracle_id = next(iter(oracle_ids))
        expected = states_inventory.get(state_id) or {}
        if not _is_int(expected.get("queries")):
            continue
        checked = (oracle_id, expected["queries"])
        if checked in checked_oracles:
            continue
        checked_oracles.add(checked)
        _check_oracle_answers(problems, "oracle " + str(oracle_id) + " for state " + state_id,
                              oracles[oracle_id], expected["queries"])
    expected_ids = set(states_inventory)
    for state_id in expected_ids:
        modes = pairs.get(state_id, [])
        if sorted(modes, key=str) != ["cold", "warm"]:
            _fail(problems, "state " + state_id + " has modes " + repr(modes) + ", expected warm and cold")
    for state_id in pairs:
        if state_id not in expected_ids:
            _fail(problems, "capture has unexpected state ID " + state_id)
    if len(records) != 2 * len(states_inventory):
        _fail(problems, "capture has " + str(len(records)) + " state/mode records, expected "
              + str(2 * len(states_inventory)))
    if set(oracles) != used_oracles:
        _fail(problems, "capture has oracles that no state uses, or used oracles that are missing")
    _check_mode_evidence(problems, records)
    return records


def _document_versions(row):
    """{uri: version} of a state's snapshot, or None when it records none."""
    versions = row["snapshot"].get("documentVersions")
    if not isinstance(versions, list) or not all(
            isinstance(entry, dict) and isinstance(entry.get("uri"), str) for entry in versions):
        return None
    return {entry["uri"]: entry.get("version") for entry in versions}


def _check_snapshot_sources(problems, where, row):
    """bench-tolerance.py checks, before it scores a record, that the snapshot
    has one entry for each source of the current configuration, in order, in
    one project directory; each entry must also describe the current text."""
    try:
        sources = row["configuration"]["current"]["projects"][0]["sources"]
    except (KeyError, IndexError, TypeError):
        sources = None
    if not isinstance(sources, list) or not sources or not all(isinstance(s, str) for s in sources):
        _fail(problems, where + " has no current configured sources")
        return False
    versions = row["snapshot"].get("documentVersions")
    if not isinstance(versions, list) or len(versions) != len(sources):
        _fail(problems, where + " snapshot does not have one document for each configured source")
        return False
    bases = set()
    for entry, source in zip(versions, sources):
        uri = entry.get("uri") if isinstance(entry, dict) else None
        if not isinstance(uri, str) or not uri.endswith("/" + source):
            _fail(problems, where + " snapshot document " + repr(uri) + " is not the configured source "
                  + repr(source))
            return False
        if entry.get("current") is not True:
            _fail(problems, where + " snapshot document " + uri + " is not current")
            return False
        bases.add(uri[:-len(source)])
    if len(bases) != 1:
        _fail(problems, where + " snapshot documents are not in one project directory")
        return False
    return True


def _check_mode_evidence(problems, records):
    """A state's warm and cold records come from two server processes, and
    their snapshots show how each opened the edited files.

    bench-tolerance.py starts one server per state and mode. Cold opens every
    document once, with the current text, so each open document is at version
    1. Warm opens the predecessor and changes each edited overlay once, so an
    edited file that is open is at version 2. A closed file on disk has no
    version in either mode. One record filed under both modes fails both.
    """
    by_state = collections.defaultdict(dict)
    for row in records:
        if (isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("mode") in MODES
                and isinstance(row.get("snapshot"), dict) and isinstance(row.get("changed"), list)):
            by_state[row["id"]].setdefault(row["mode"], row)
    for state_id, modes in by_state.items():
        for mode, row in modes.items():
            if not _check_snapshot_sources(problems, "state " + state_id + " " + mode, row):
                continue
            versions = _document_versions(row)
            if versions is None:
                _fail(problems, "state " + state_id + " " + mode + " snapshot has no document versions")
                continue
            if mode == "cold":
                stale = sorted(uri for uri, version in versions.items() if version not in (None, 1))
                if stale:
                    _fail(problems, "state " + state_id + " cold snapshot has documents past version 1: "
                          + ", ".join(stale))
            else:
                unchanged = sorted(uri for uri, version in versions.items() if version not in (None, 2)
                                   and any(isinstance(path, str) and uri.endswith("/" + path)
                                           for path in row["changed"]))
                if unchanged:
                    _fail(problems, "state " + state_id + " warm snapshot has edited documents not at "
                          "version 2: " + ", ".join(unchanged))
        if len(modes) == 2 and modes["warm"].get("server_pid") == modes["cold"].get("server_pid"):
            _fail(problems, "state " + state_id + " warm and cold records come from one server process "
                  + repr(modes["warm"].get("server_pid")))


def _check_injection(problems, where, stale, oracle_answers, allow_absent):
    """Validate one stale-predecessor injection and say whether it was rejected.

    bench-tolerance.py rejects an injection when scoring its stale answers
    against the oracle finds a wrong answer. The injected answers are rescored
    here the same way. A recorded verdict that differs from the rescored one,
    including a wrong-typed one, or a verdict on an unknown answer key, is a
    malformed control, not evidence that the server refused the injection.
    """
    if not isinstance(stale, dict):
        _fail(problems, where + " has no stale-predecessor injection")
        return False
    answers, scores = stale.get("answers"), stale.get("scores")
    if not isinstance(answers, dict) or not answers:
        _fail(problems, where + ": injection answers is not a non-empty object")
        return False
    if not isinstance(scores, dict) or not scores:
        _fail(problems, where + ": injection scores is not a non-empty object")
        return False
    if set(answers) != set(scores):
        _fail(problems, where + ": injection answers and scores have different keys")
        return False
    if isinstance(oracle_answers, dict) and set(answers) != set(oracle_answers):
        _fail(problems, where + ": injection answers " + repr(sorted(answers))
              + " are not the oracle's " + repr(sorted(oracle_answers)))
        return False
    rejected = False
    valid = True
    for key, verdict in scores.items():
        if _answer_feature(key) is None:
            _fail(problems, where + ": injection has unknown answer key " + repr(key))
            valid = False
            continue
        before = len(problems)
        # Rescoring compares whole verdicts, and 1 == True in Python.
        if not _verdict_problem(problems, where + " injection " + key, verdict):
            valid = False
            continue
        _answer_type_problem(problems, where + " injection " + key, key, answers[key])
        rescored = _rescore(problems, where + " injection " + key, key, answers[key], verdict,
                            oracle_answers, allow_absent)
        valid = valid and len(problems) == before
        rejected = rejected or bool(rescored and rescored["wrong"])
    return rejected and valid


def _check_negative_controls(states_inventory, oracles, records, problems):
    negative = [state_id for state_id, row in states_inventory.items() if row.get("suite") == "negative"]
    by_state = collections.defaultdict(list)
    for row in records:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            by_state[row["id"]].append(row)
    rejected = set()
    for state_id in negative:
        rows = by_state.get(state_id, [])
        modes = sorted((row.get("mode") for row in rows if isinstance(row.get("mode"), str)))
        if modes != ["cold", "warm"]:
            _fail(problems, "negative control " + state_id + " did not run in both modes")
        oracle_ids = {row.get("oracle_id") for row in rows if _is_int(row.get("oracle_id"))}
        if len(oracle_ids) != 1:
            _fail(problems, "negative control " + state_id + " does not have exactly one oracle")
            continue
        oracle_id = oracle_ids.pop()
        oracle = oracles.get(oracle_id)
        if oracle is None:
            continue
        # The driver scores a negative control's injection with absence allowed.
        if _check_injection(problems, "negative control " + state_id, oracle.get("stale_control"),
                            oracle.get("answers"), True):
            rejected.add(oracle_id)
        else:
            _fail(problems, "negative control " + state_id + " did not reject its injection")
    if len(rejected) != len(negative):
        _fail(problems, "only " + str(len(rejected)) + " of " + str(len(negative))
              + " negative controls rejected an injection")


def _session_step_ids(row):
    steps = row.get("steps")
    if not isinstance(steps, list):
        return None
    ids = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str):
            return None
        ids.append(step["id"])
    return ids


def _check_step(problems, where, step, fields, mode):
    """Check one session step record, which is one measured editor state."""
    if not isinstance(step, dict):
        _fail(problems, where + " is not an object")
        return False
    complete = _check_fields(problems, where, step, fields)
    before = len(problems)
    _check_measures(problems, where, step, ("state_latency_ms",))
    complete = complete and len(problems) == before
    for field in ("samples", "queries"):
        if isinstance(step.get(field), list) and not step[field]:
            _fail(problems, where + " has no " + field)
            complete = False
    if step.get("edit_kind") != step.get("id"):
        _fail(problems, where + " edit kind differs from its step ID")
    if mode is not None and step.get("mode") != mode:
        _fail(problems, where + " mode differs from its session")
    if step.get("suite") != "session":
        _fail(problems, where + " suite is not session")
    return complete


def _session_oracle_answers(oracle, index):
    steps = oracle.get("steps") if isinstance(oracle, dict) else None
    if not isinstance(steps, list) or index >= len(steps) or not isinstance(steps[index], dict):
        return None
    answers = steps[index].get("answers")
    return answers if isinstance(answers, dict) else None


def _check_sessions(capture, sessions_inventory, problems):
    oracle_rows = capture.get("session_oracles")
    records = capture.get("sessions")
    if not isinstance(oracle_rows, list):
        _fail(problems, "session_oracles is not an array")
        return []
    if not isinstance(records, list):
        _fail(problems, "sessions is not an array")
        return []
    oracles = {}
    for index, row in enumerate(oracle_rows):
        where = "session_oracles[" + str(index) + "]"
        if not isinstance(row, dict):
            _fail(problems, where + " is not an object")
            continue
        session_id = row.get("id")
        if not isinstance(session_id, str):
            _fail(problems, where + " has no string id")
            continue
        if session_id in oracles:
            _fail(problems, where + " duplicates session ID " + session_id)
        oracles[session_id] = row
        _check_markers(problems, where, row)
        _check_measures(problems, where, row)
        if row.get("mode") != "oracle":
            _fail(problems, where + " is not an intended-text oracle")
        expected = sessions_inventory.get(session_id)
        if expected is None:
            _fail(problems, where + " has unknown session ID " + session_id)
            continue
        ids = _session_step_ids(row)
        expected_ids = [step["id"] for step in expected["steps"]]
        if ids != expected_ids:
            _fail(problems, where + " step IDs " + repr(ids) + " differ from the inventory " + repr(expected_ids))
            continue
        for step, expected_step in zip(row["steps"], expected["steps"]):
            where_step = where + " step " + expected_step["id"]
            if isinstance(step, dict):
                _check_markers(problems, where_step, step)
            _check_step(problems, where_step, step, SESSION_STEP_FIELDS, None)
            _check_answer_values(problems, where_step, step.get("answers"),
                                 3 * expected_step["queries"] + 1)
            _check_answer_indices(problems, where_step, step.get("answers"),
                                  expected_step["queries"])
            if expected_step.get("stale"):
                # The stale predecessor's answers are scored against this
                # intended-text step's answers.
                if not _check_injection(problems, where_step, step.get("stale_control"),
                                        step.get("answers"), False):
                    _fail(problems, where_step + " did not reject its stale predecessor")
    if set(oracles) != set(sessions_inventory):
        _fail(problems, "session oracles differ from the inventory")

    pairs = collections.defaultdict(list)
    pids = collections.defaultdict(list)
    for session_id, row in oracles.items():
        pids[session_id].append(row.get("server_pid"))
    for index, row in enumerate(records):
        where = "sessions[" + str(index) + "]"
        if not isinstance(row, dict):
            _fail(problems, where + " is not an object")
            continue
        _check_markers(problems, where, row)
        session_id = row.get("id")
        if not isinstance(session_id, str):
            _fail(problems, where + " has no string id")
            continue
        pairs[session_id].append(row.get("mode"))
        pids[session_id].append(row.get("server_pid"))
        _check_measures(problems, where, row, ("worker_replacements",))
        if not _is_int(row.get("server_pid")):
            _fail(problems, where + " server_pid is not an integer")
        expected = sessions_inventory.get(session_id)
        if expected is None:
            _fail(problems, where + " has unknown session ID " + session_id)
            continue
        if row.get("suite") != "session":
            _fail(problems, where + " suite is not session")
        if row.get("mode") not in MODES:
            _fail(problems, where + " has unknown mode " + repr(row.get("mode")))
        ids = _session_step_ids(row)
        expected_ids = [step["id"] for step in expected["steps"]]
        if ids != expected_ids:
            _fail(problems, where + " step IDs " + repr(ids) + " differ from the inventory " + repr(expected_ids))
            continue
        for step_index, (step, expected_step) in enumerate(zip(row["steps"], expected["steps"])):
            where_step = where + " step " + expected_step["id"]
            if isinstance(step, dict):
                _check_markers(problems, where_step, step)
            _check_step(problems, where_step, step,
                        SESSION_STEP_FIELDS + SESSION_MEASURED_FIELDS, row.get("mode"))
            oracle_step = step.get("oracle_step")
            if not _is_int(oracle_step) or oracle_step != step_index:
                _fail(problems, where_step + " oracle step is " + repr(oracle_step))
            intended = _session_oracle_answers(oracles.get(session_id), step_index)
            _check_answers(problems, where_step, step.get("answers"), step.get("scores"),
                           3 * expected_step["queries"] + 1, intended)
            if intended is None:
                _fail(problems, where_step + " has no intended-text oracle step to rescore against")
            _check_answer_indices(problems, where_step, step.get("answers"),
                                  expected_step["queries"])
            mismatch = step.get("expectation_mismatches")
            if mismatch:
                _fail(problems, where_step + " has expectation mismatches")
    for session_id in sessions_inventory:
        modes = pairs.get(session_id, [])
        if sorted(modes, key=str) != ["cold", "warm"]:
            _fail(problems, "session " + session_id + " has modes " + repr(modes)
                  + ", expected warm and cold")
    for session_id in pairs:
        if session_id not in sessions_inventory:
            _fail(problems, "capture has unexpected session ID " + session_id)
    # The oracle and each mode replay a history in their own server process.
    for session_id, values in pids.items():
        if len(values) != len(set(map(repr, values))):
            _fail(problems, "session " + session_id + " records share a server process: " + repr(values))
    if len(records) != 2 * len(sessions_inventory):
        _fail(problems, "capture has " + str(len(records)) + " session records, expected "
              + str(2 * len(sessions_inventory)))
    return records


def validate(capture, inventory=None):
    """Return every reason a capture cannot qualify a release.

    An empty list means the capture is a complete full run whose stable IDs,
    modes, record structure, sessions, controls, and injections all match the
    corpus inventory.
    """
    problems = []
    if not isinstance(capture, dict):
        return ["capture is not a JSON object"]
    if inventory is None:
        inventory = load_inventory()
    if not _is_int(capture.get("schema")) or capture["schema"] != 1:
        _fail(problems, "capture schema is not 1")
    if capture.get("suite") != "tolerance":
        _fail(problems, "capture suite is not tolerance")
    for field, kind in (
            ("started_utc", str), ("command", list), ("server_revision", str),
            ("host", dict), ("corpus", dict), ("corpus_sha256", str),
            ("driver_sha256", dict), ("builds", list), ("input_sha256", dict),
            ("oracles", list), ("states", list), ("sessions", list),
            ("session_oracles", list), ("summary", dict), ("session_summary", dict)):
        value = capture.get(field)
        if field not in capture:
            _fail(problems, "capture is missing " + field)
        elif not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            _fail(problems, "capture " + field + " has the wrong type")
    if not _is_measure(capture.get("wall_seconds")):
        _fail(problems, "capture wall_seconds is not a finite nonnegative number")
    if "error" in capture or "failed_record" in capture:
        _fail(problems, "capture is a failure artifact")
    _check_capture_kind(capture, problems)
    if any("not a full capture" in problem for problem in problems):
        return problems
    states_inventory, sessions_inventory = _inventory_index(inventory, problems)
    oracles = _check_oracles(capture, problems)
    records = _check_states(capture, states_inventory, oracles, problems)
    _check_negative_controls(states_inventory, oracles, records, problems)
    _check_sessions(capture, sessions_inventory, problems)
    return problems


def aggregate(capture, inventory=None):
    """Count the scorecard's per-mode measurements from a validated capture."""
    if inventory is None:
        inventory = load_inventory()
    states_inventory = {row["id"]: row for row in inventory["states"]}
    pairs = collections.defaultdict(list)
    for row in capture["states"]:
        pairs[row["id"]].append(row.get("mode"))
    modes = {}
    for mode in MODES:
        data = {
            "primary": {"all_features": [0, 0]},
            "repair": {}, "replay": {}, "heldout": {},
            "exact": {feature: [0, 0] for feature in FEATURES},
            "feature": collections.defaultdict(lambda: {
                feature: {"total": 0, "available": 0, "exact": 0, "partial": 0,
                          "missing": 0, "wrong": 0} for feature in FEATURES}),
            "baselines": {}, "wrong": 0, "expectation_mismatches": 0,
            "session_wrong": 0, "session_steps": 0,
        }
        for suite in SUITES:
            data["feature"][suite]
        for row in capture["states"]:
            if row.get("mode") != mode:
                continue
            scores = row["scores"]
            available = all(verdict["available"] for verdict in scores.values())
            exact = all(verdict["correct"] for verdict in scores.values())
            suite, kind = row["suite"], row["edit_kind"]
            if suite in ("repair", "replay"):
                cell = data["primary"]["all_features"]
                cell[1] += 1
                cell[0] += int(available)
                cell = data.setdefault(suite, {}).setdefault(kind, [0, 0])
                cell[1] += 1
                cell[0] += int(available)
                for key, verdict in scores.items():
                    feature = "diagnostics" if key == "diagnostics" else key.split(":")[-1]
                    cell = data["exact"][feature]
                    cell[1] += 1
                    cell[0] += int(verdict["correct"])
            elif suite == "heldout":
                cell = data["heldout"].setdefault("all_features", [0, 0])
                cell[1] += 1
                cell[0] += int(available)
                expected = states_inventory.get(row["id"], {})
                group = expected.get("group")
                heldout_kind = expected.get("heldout_kind")
                buckets = []
                if group == "body-quarantine":
                    buckets.append("body_quarantine")
                elif group == "brace-317" and heldout_kind in OPENERS:
                    buckets.append("lost_openers")
                elif group == "brace-317" and heldout_kind == "block-closer":
                    buckets.append("seed_317_block_closers")
                elif group == "closer-320" and heldout_kind == "block-closer":
                    buckets.append("seed_320_block_closers")
                if heldout_kind == "method-closer":
                    buckets.append("method_closers")
                for bucket in buckets:
                    cell = data["heldout"].setdefault(bucket, [0, 0])
                    cell[1] += 1
                    cell[0] += int(available)
            for key, verdict in scores.items():
                feature = "diagnostics" if key == "diagnostics" else key.split(":")[-1]
                cell = data["feature"][suite][feature]
                cell["total"] += 1
                for label, field in (("available", "available"), ("exact", "correct"),
                                     ("partial", "partial"), ("missing", "missing"),
                                     ("wrong", "wrong")):
                    cell[label] += int(verdict[field])
                if suite in ("repair", "replay"):
                    cell = data["feature"]["primary"][feature]
                    cell["total"] += 1
                    for label, field in (("available", "available"), ("exact", "correct"),
                                         ("partial", "partial"), ("missing", "missing"),
                                         ("wrong", "wrong")):
                        cell[label] += int(verdict[field])
                data["wrong"] += int(verdict["wrong"])
            data["expectation_mismatches"] += len(row.get("expectation_mismatches") or [])
            if suite in ("repair", "replay"):
                cell = data["baselines"].setdefault("primary_all_exact", [0, 0])
                cell[1] += 1
                cell[0] += int(exact)
            elif suite == "heldout":
                cell = data["baselines"].setdefault("heldout_all_exact", [0, 0])
                cell[1] += 1
                cell[0] += int(exact)
        for row in capture["sessions"]:
            if row.get("mode") != mode:
                continue
            for step in row.get("steps", []):
                data["session_steps"] += 1
                for verdict in step.get("scores", {}).values():
                    data["wrong"] += int(verdict["wrong"])
                    data["session_wrong"] += int(verdict["wrong"])
                mismatch = step.get("expectation_mismatches")
                if mismatch:
                    data["expectation_mismatches"] += len(mismatch)
        modes[mode] = data
    negative = [state_id for state_id, row in states_inventory.items() if row.get("suite") == "negative"]
    negative_runs = sum(1 for state_id in negative
                        if sorted(pairs.get(state_id, [])) == ["cold", "warm"])
    injections = 0
    for oracle in capture["oracles"]:
        stale = oracle.get("stale_control")
        if isinstance(stale, dict) and isinstance(stale.get("scores"), dict):
            if any(isinstance(verdict, dict) and verdict.get("wrong") is True
                   for verdict in stale["scores"].values()):
                injections += 1
    return {
        "modes": modes,
        "counts": {
            "state_ids": len(pairs),
            "state_mode_records": len(capture["states"]),
            "negative_controls": len(negative),
            "negative_runs": negative_runs,
            "negative_injections": injections,
            "sessions": len(capture["sessions"]) // 2,
            "session_steps": sum(len(row.get("steps", [])) for row in capture["sessions"]),
            "oracles": len(capture["oracles"]),
            "session_oracles": len(capture["session_oracles"]),
        },
    }


def check_floors(metrics, floors=None):
    """Return the unmet per-mode floor and denominator checks."""
    floors = FLOORS if floors is None else floors
    failures = []
    for mode in MODES:
        data = metrics.get("modes", {}).get(mode)
        if data is None:
            failures.append(mode + ": no mode measurements")
            continue
        for section, requirements in floors.items():
            measured = data.get(section, {})
            for name, (minimum, expected) in requirements.items():
                actual, total = measured.get(name, [0, 0])
                if total != expected:
                    failures.append(mode + ": " + section + "." + name + " covers "
                                    + str(total) + "/" + str(expected) + " records")
                if actual < minimum:
                    failures.append(mode + ": " + section + "." + name + " is "
                                    + str(actual) + "/" + str(total) + ", floor "
                                    + str(minimum) + "/" + str(expected))
    return failures


def check_zero(metrics):
    """Return wrong answers or control-expectation mismatches in either mode."""
    failures = []
    for mode in MODES:
        data = metrics["modes"][mode]
        if data["wrong"]:
            failures.append(mode + ": " + str(data["wrong"]) + " wrong answers")
        if data["expectation_mismatches"]:
            failures.append(mode + ": " + str(data["expectation_mismatches"])
                            + " expectation mismatches")
    return failures


def evaluate(capture, inventory=None, floors=None):
    """Validate, aggregate, and gate one capture. Returns a report dictionary."""
    if inventory is None:
        inventory = load_inventory()
    problems = validate(capture, inventory)
    if problems:
        return {"ok": False, "failures": problems, "metrics": None}
    metrics = aggregate(capture, inventory)
    floors = FLOORS if floors is None else floors
    failures = check_floors(metrics, floors) + check_zero(metrics)
    return {"ok": not failures, "failures": failures, "metrics": metrics, "floors": floors}


def _status(actual, minimum, total, expected):
    if total != expected:
        return "INCOMPLETE"
    if minimum == 0:
        return "uncovered"
    return "pass" if actual >= minimum else "FAIL"


def _floor_line(label, pair, minimum, expected, width=31):
    actual, total = pair
    return ("  " + label.ljust(width) + str(actual).rjust(3) + "/" + str(total).ljust(4)
            + "  floor " + str(minimum).rjust(3) + "/" + str(expected).ljust(4)
            + "  " + _status(actual, minimum, total, expected))


def _feature_lines(label, counts):
    lines = ["  " + label]
    for feature in FEATURES:
        cell = counts[feature]
        lines.append("    " + feature.ljust(14)
                     + "exact " + str(cell["exact"]).rjust(3)
                     + "  partial " + str(cell["partial"]).rjust(2)
                     + "  missing " + str(cell["missing"]).rjust(3)
                     + "  wrong " + str(cell["wrong"]).rjust(2)
                     + "  available " + str(cell["available"]).rjust(3))
    return lines


def format_scorecard(report, capture=None):
    """Render a report from evaluate() as a readable scorecard."""
    lines = ["Tolerance release scorecard: " + ("PASS" if report["ok"] else "FAIL")]
    if capture is not None:
        revision = capture.get("server_revision", "unknown")
        lines.append("revision " + str(revision)
                     + "  corpus " + str(capture.get("corpus_sha256", "unknown"))[:16])
    if report["metrics"] is None:
        for failure in report["failures"]:
            lines.append("  FAIL " + failure)
        return "\n".join(lines)
    metrics = report["metrics"]
    counts = metrics["counts"]
    floors = report.get("floors", FLOORS)
    lines.append("full capture: " + str(counts["state_ids"]) + " states, "
                 + str(counts["state_mode_records"]) + " state/mode records, "
                 + str(counts["negative_runs"]) + "/" + str(counts["negative_controls"])
                 + " negative controls in both modes, "
                 + str(counts["negative_injections"]) + " injections rejected, "
                 + str(counts["sessions"]) + " sessions, "
                 + str(counts["session_steps"]) + " session step/mode records")
    for mode in MODES:
        data = metrics["modes"][mode]
        lines.append("")
        lines.append(mode.upper())
        lines.append("  availability (states retaining all four features)")
        lines.append(_floor_line("primary", data["primary"]["all_features"],
                                 *floors["primary"]["all_features"]))
        for suite in ("repair", "replay"):
            for kind in EDIT_KINDS:
                if kind in data[suite] and kind in floors.get(suite, {}):
                    lines.append(_floor_line(suite + " " + kind, data[suite][kind],
                                             *floors[suite][kind]))
        lines.append(_floor_line("held-out", data["heldout"].get("all_features", [0, 0]),
                                 *floors.get("heldout", {}).get("all_features", (0, 0))))
        for name, label in (
                ("body_quarantine", "held-out body quarantine"),
                ("lost_openers", "held-out lost openers"),
                ("seed_317_block_closers", "held-out seed-317 block closers"),
                ("seed_320_block_closers", "held-out seed-320 block closers"),
                ("method_closers", "held-out method closers")):
            if name in floors.get("heldout", {}):
                lines.append(_floor_line(label, data["heldout"].get(name, [0, 0]),
                                         *floors["heldout"][name]))
        lines.append("  exact answers (primary)")
        for feature in FEATURES:
            if feature in floors.get("exact", {}):
                lines.append(_floor_line(feature, data["exact"][feature],
                                         *floors["exact"][feature]))
        lines.append("  fully exact states (baseline)")
        for name, label in (("primary_all_exact", "primary"), ("heldout_all_exact", "held-out")):
            actual, total = data["baselines"].get(name, [0, 0])
            baseline, baseline_total = BASELINES[name]
            lines.append("  " + label.ljust(31) + str(actual).rjust(3) + "/"
                         + str(total).ljust(4) + "  baseline " + str(baseline) + "/"
                         + str(baseline_total))
        lines.append("  wrong answers " + str(data["wrong"])
                     + "  expectation mismatches " + str(data["expectation_mismatches"])
                     + "  session wrong answers " + str(data["session_wrong"]))
        lines.append("  feature counts (primary)")
        lines.extend(_feature_lines("primary", data["feature"]["primary"]))
        lines.append("  feature counts (held-out)")
        lines.extend(_feature_lines("held-out", data["feature"]["heldout"]))
    uncovered = []
    for mode in MODES:
        data = metrics["modes"][mode]
        if data["repair"].get("whole_file", [0, 0])[0] == 0:
            uncovered.append(mode + ": repair whole-file availability is 0/2 (truncated files are phase 3b)")
        if data["heldout"].get("method_closers", [0, 0])[0] == 0:
            uncovered.append(mode + ": held-out method closers are 0/15 (a lost method close is refused)")
    if uncovered:
        lines.append("")
        lines.append("UNCOVERED CLASSES (zero floors, not recovery successes)")
        lines.extend("  " + item for item in uncovered)
    if report["failures"]:
        lines.append("")
        for failure in report["failures"]:
            lines.append("  FAIL " + failure)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="full bench-tolerance.py JSON capture")
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    args = parser.parse_args()
    with args.capture.open() as source:
        capture = json.load(source)
    inventory = load_inventory(args.inventory)
    report = evaluate(capture, inventory)
    print(format_scorecard(report, capture))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
