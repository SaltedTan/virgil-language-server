#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Regenerate or check the stable tolerance corpus inventory.

`--check`, the default, re-derives every row of
`test/benchmarks/tolerance-inventory.json` from the corpus and sessions
generators and compares them with the committed file. It writes nothing under
the repository. The held-out states are selected by the compiler's parse
verdicts, which `--write` records in
`test/benchmarks/tolerance-parse-facts.json`, so the check replays those
verdicts and needs no built server.

`--write` is the only way to change either file. It needs a built server
(`make bench-build`) to parse the corpus texts again, and it changes the gate,
so review its diff and explain it in the commit.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "test/benchmarks/tolerance-inventory.json"
FACTS = ROOT / "test/benchmarks/tolerance-parse-facts.json"
DEFAULT_EXE = ROOT / "build/bench/virgil-lsp"
COMMENT = ("Stable tolerance corpus inventory for scripts/check-tolerance.py. "
           "Regenerate it with `python3 scripts/tolerance-inventory.py --write` after a "
           "corpus change, and explain why in the commit.")
FACTS_COMMENT = ("Parse verdicts for the tolerance inventory check: the sha256 of each corpus "
                 "text that the held-out selection parses, mapped to the line of its first syntax "
                 "error, or null when it parses. Regenerate it with "
                 "`python3 scripts/tolerance-inventory.py --write` from a built server "
                 "(`make bench-build`).")


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def heldout_group(state_id):
    if state_id.startswith("brace-"):
        return "brace-317"
    if state_id.startswith("closer-"):
        return "closer-320"
    return "body-quarantine"


def state_rows(states, corpus):
    cases = list(corpus.fixtures()) + list(corpus.large()) + list(corpus.controls()) + list(corpus.heldout())
    groups, rows = {}, []
    for state in cases:
        # A capture records one intended-text oracle per distinct key, in
        # first-use order, so the inventory names the group instead.
        key = states.oracle_key(state)
        if key not in groups:
            groups[key] = len(groups)
        row = {"id": state["id"], "suite": state["suite"],
               "edit_kind": state["edit_kind"], "queries": len(state["queries"]),
               "oracle": groups[key]}
        if state["suite"] == "heldout":
            row["group"] = heldout_group(state["id"])
            row["heldout_kind"] = state["heldout_kind"]
            if "indentation" in state:
                row["indentation"] = state["indentation"]
        rows.append(row)
    return rows, len(groups)


def session_rows(corpus, states, sessions):
    histories = list(sessions.fixtures(corpus, states)) + [sessions.pressure(corpus, states)]
    return [{"id": case["id"], "queries": len(case["queries"]),
             "steps": [{"id": step["id"], "queries": len(step.get("queries", case["queries"])),
                        "stale": bool(step.get("stale_control"))} for step in case["steps"]]}
            for case in histories]


def generate(parse=None, exe=None):
    """Return the inventory and the parse verdicts that generating it needed."""
    states, sessions = load("tolerance-corpus"), load("tolerance-sessions")
    work = Path(tempfile.mkdtemp(prefix="tolerance-inventory-"))
    try:
        corpus = states.Corpus(exe or DEFAULT_EXE, work, parse=parse)
        rows, oracles = state_rows(states, corpus)
        history_rows = session_rows(corpus, states, sessions)
    finally:
        shutil.rmtree(work)
    return {"schema": 1, "comment": COMMENT,
            "counts": {
                "states": len(rows),
                "state_mode_records": 2 * len(rows),
                "negative_controls": sum(row["suite"] == "negative" for row in rows),
                "sessions": len(history_rows),
                "session_step_mode_records": 2 * sum(len(row["steps"]) for row in history_rows),
                "oracles": oracles,
                "session_oracles": len(history_rows),
            },
            "states": rows, "sessions": history_rows}, corpus.parsed


def read(path):
    with path.open() as source:
        return json.load(source)


def check():
    """Return every way the committed inventory or parse facts differ from the corpus."""
    inventory, recorded = read(INVENTORY), read(FACTS).get("facts", {})
    missing = []

    def parse(text):
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest not in recorded:
            missing.append(digest)
            raise LookupError(digest)
        return recorded[digest]

    try:
        actual, used = generate(parse=parse)
    except LookupError:
        return ["the corpus parses " + str(len(missing)) + " text(s) that " + FACTS.name
                + " does not record, starting with " + ", ".join(missing[:3])
                + "; regenerate the inventory with --write and review the diff"]
    problems = []
    if actual != inventory:
        problems.append(INVENTORY.name + " differs from the generators: " + difference(inventory, actual)
                        + "; regenerate it with --write and review the diff")
    unused = sorted(set(recorded) - set(used))
    if unused:
        problems.append(FACTS.name + " records " + str(len(unused))
                        + " parse verdict(s) that the corpus no longer asks about, starting with "
                        + ", ".join(unused[:3]) + "; regenerate it with --write")
    return problems


def difference(committed, actual):
    """Describe the first row that differs, for a readable failure."""
    for field in ("comment", "counts"):
        if committed.get(field) != actual.get(field):
            return field + " differs"
    for field in ("states", "sessions"):
        old, new = committed.get(field), actual.get(field)
        if old == new:
            continue
        if not isinstance(old, list) or not isinstance(new, list):
            return field + " is not an array"
        if len(old) != len(new):
            return field + " has " + str(len(old)) + " rows, the generators produce " + str(len(new))
        for index, (before, after) in enumerate(zip(old, new)):
            if before != after:
                return field + "[" + str(index) + "] is " + json.dumps(before)
        return field + " differs"
    return "the file differs"


def write(exe):
    if not exe.exists():
        print("tolerance inventory: " + str(exe) + " is missing; run make bench-build", file=sys.stderr)
        return 2
    inventory, facts = generate(exe=exe)
    INVENTORY.write_text(json.dumps(inventory, indent=1) + "\n")
    FACTS.write_text(json.dumps({"schema": 1, "comment": FACTS_COMMENT,
                                 "facts": {digest: facts[digest] for digest in sorted(facts)}},
                                indent=1) + "\n")
    print("tolerance inventory: wrote " + str(len(inventory["states"])) + " states, "
          + str(len(inventory["sessions"])) + " sessions, " + str(len(facts)) + " parse facts")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check the committed files (the default)")
    parser.add_argument("--write", action="store_true", help="replace both files; needs a built server")
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE, help="server used by --write to parse")
    args = parser.parse_args(argv)
    if args.write:
        return write(args.exe.resolve())
    problems = check()
    for problem in problems:
        print("tolerance inventory: FAIL " + problem, file=sys.stderr)
    if problems:
        return 1
    inventory = read(INVENTORY)
    print("tolerance inventory: OK, " + str(len(inventory["states"])) + " states, "
          + str(len(inventory["sessions"])) + " sessions, "
          + str(len(read(FACTS)["facts"])) + " parse facts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
