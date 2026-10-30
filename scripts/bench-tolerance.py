#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Measure semantic availability and correctness during syntax errors over stdio."""
import argparse
import collections
import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import quote

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = load("bench-lsp")
states = load("tolerance-corpus")
sessions = load("tolerance-sessions")
ROOT = bench.ROOT
FEATURES = ("definition", "hover", "references", "diagnostics")


@contextlib.contextmanager
def cleanup(row):
    error = sys.exc_info()[1]
    try:
        yield
    except BaseException as cleanup_error:
        if error is None:
            cleanup_error.tolerance_record = row
            raise
        row.setdefault("cleanup_errors", []).append(repr(cleanup_error))


def canonical(feature, value):
    """Ignore set ordering, preserving every URI, range, text, and diagnostic field."""
    if feature in ("references", "diagnostics"):
        if value is None:
            return None
        return sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def score(feature, actual, expected, allow_absent=False):
    actual, expected = canonical(feature, actual), canonical(feature, expected)
    empty = actual is None or actual == []
    equal = actual == expected
    # An expected empty result is a legitimate, available answer. Missing a
    # nonempty oracle is unavailable; it must never count as a wrong binding.
    available = not empty or equal
    partial = False
    if feature in ("references", "diagnostics") and not empty and not equal and expected:
        # Compare whole items exactly, retaining duplicate counts as well as
        # every protocol field. An omitted item is safe; a foreign one is not.
        items = lambda values: collections.Counter(json.dumps(v, sort_keys=True) for v in values)
        partial = len(actual) < len(expected) and not (items(actual) - items(expected))
    return {"available": available, "correct": equal or (allow_absent and empty),
            "partial": partial, "missing": not available,
            "wrong": available and not equal and not partial, "matches_oracle": equal}


def substituted_files(snapshot):
    """The project-relative URIs of the files that {snapshot} substituted,
    relative to the directory of its project file, or none if it doesn't
    have that shape."""
    try:
        base = snapshot["projectConfiguration"]["uri"].rsplit("/", 1)[0] + "/"
        return {v["uri"].removeprefix(base) for v in snapshot["documentVersions"] if "substitution" in v}
    except (KeyError, TypeError, AttributeError):
        return set()


def without_documentation(hover):
    """{hover} without the documentation that follows its signature blocks
    after their first empty line."""
    value = hover.get("contents", {}).get("value") if isinstance(hover, dict) else None
    if not isinstance(value, str) or "\n\n" not in value:
        return hover
    return {**hover, "contents": {**hover["contents"], "value": value.split("\n\n", 1)[0]}}


def expected_answers(oracle, snapshot, queries):
    """The answers a record of {snapshot} must match: the oracle's, except
    that a hover whose target declaration is in a file that the snapshot
    substituted has no documentation, as the server omits it there (#357).
    The target's file is the oracle's definition's, or, where there is none,
    as at a declaration's own name, the queried file. Everywhere else the
    oracle's hover, with its documentation, is expected exactly."""
    substituted = substituted_files(snapshot)
    if not substituted:
        return oracle
    expected = dict(oracle)
    for key, hover in oracle.items():
        index, _, feature = key.rpartition(":")
        if feature != "hover" or not hover:
            continue
        found = oracle.get(index + ":definition")
        found = found if isinstance(found, list) else [found] if found else []
        files = {str(d.get("uri") or d.get("targetUri")).removeprefix("project:///")
                 for d in found if isinstance(d, dict)}
        query = queries[int(index)] if index.isdigit() and int(index) < len(queries) else None
        if not files and isinstance(query, dict) and isinstance(query.get("path"), str):
            files = {quote(query["path"])}
        if files & substituted:
            expected[key] = without_documentation(hover)
    return expected


def portable(value, base):
    """Use project-relative URIs for stable captures across disposable directories."""
    if isinstance(value, str):
        return value.replace(base.as_uri() + "/", "project:///")
    if isinstance(value, list):
        return [portable(v, base) for v in value]
    if isinstance(value, dict):
        return {k: portable(v, base) for k, v in value.items()}
    return value


class Run:
    def __init__(self, exe, project, row):
        self.exe, self.p, self.row = exe, project, row
        self.s = bench.Session(exe)
        self.versions = {}
        self.diagnostics = {}
        self.cursor = 0

    def request(self, method, params=None):
        result = self.s.result(self.s.send(method, params), timeout=60)
        self.collect()
        return result

    def collect(self):
        for direction, msg in self.s.transcript[self.cursor:]:
            if direction == "recv" and msg.get("method") == "textDocument/publishDiagnostics":
                params = msg["params"]
                self.diagnostics[params["uri"]] = params
        self.cursor = len(self.s.transcript)

    def open(self, path, text, language="virgil"):
        bench.open_document(self.s, self.p["base"] / path, text, language)
        self.versions[path] = 1

    def change(self, path, text):
        self.versions[path] += 1
        bench.notify(self.s, "textDocument/didChange", {
            "textDocument": {"uri": (self.p["base"] / path).as_uri(), "version": self.versions[path]},
            "contentChanges": [{"text": text}]})

    def start(self, overlays):
        base = self.p["base"]
        self.request("initialize", {"rootUri": base.as_uri(),
                     "initializationOptions": {"virgilRoot": str(ROOT / "vendor/virgil")},
                     "capabilities": {"textDocument": {"hover": {"contentFormat": ["plaintext"]}}}})
        bench.notify(self.s, "initialized", {})
        for path, text in overlays.items():
            self.open(path, text)
        # One observer open schedules the project even when no edited file is
        # open (closed-file disk controls). Do it last, before the first query.
        observer = self.p.get("observer", "Observer.v3")
        if observer not in overlays:
            self.open(observer, self.p["sources"][observer])

    def apply(self, action, intended=False):
        """Apply exactly one editor/disk event; no timed waits are needed."""
        op, path = action["op"], action["path"]
        text = action.get("intended" if intended else "text")
        uri = (self.p["base"] / path).as_uri()
        if op == "open":
            bench.check(path not in self.versions, ("already open", path))
            self.open(path, text)
        elif op == "change":
            self.change(path, text)
        elif op in ("save", "write"):
            (self.p["base"] / path).write_bytes(text.encode())
            if op == "save":
                bench.check(path in self.versions, ("save requires an open document", path))
                bench.notify(self.s, "textDocument/didSave", {"textDocument": {"uri": uri}})
            else:
                bench.notify(self.s, "workspace/didChangeWatchedFiles", {"changes": [
                    {"uri": uri, "type": action["event_type"]}]})
        elif op == "close":
            bench.check(path in self.versions, ("close requires an open document", path))
            bench.notify(self.s, "textDocument/didClose", {"textDocument": {"uri": uri}})
            del self.versions[path]
        else:
            raise ValueError("unknown session action: " + op)

    def watched_diagnostics(self, paths):
        result = []
        for path in sorted(paths):
            uri = (self.p["base"] / path).as_uri()
            publication = self.diagnostics.get(uri, {})
            # didClose may leave an unchanged empty publication carrying the
            # old overlay version; only nonempty lists can be stale answers.
            if "version" in publication and publication.get("diagnostics"):
                bench.check(publication["version"] == self.versions.get(path), ("stale diagnostic version", publication))
            for diagnostic in publication.get("diagnostics", []):
                if diagnostic.get("code") != "ParseError":
                    result.append({"uri": uri, "diagnostic": diagnostic})
        return portable(result, self.p["base"])

    def answers(self, queries, diagnostic_paths, measure=False):
        answers, samples = {}, []
        for index, q in enumerate(queries):
            params = {"textDocument": {"uri": (self.p["base"] / q["path"]).as_uri()}, "position": q["position"]}
            for feature in FEATURES[:3]:
                start = time.perf_counter_ns()
                result = self.request("textDocument/" + feature, {
                    **params, **({"context": {"includeDeclaration": True}} if feature == "references" else {})})
                elapsed = (time.perf_counter_ns() - start) / 1e6
                key = str(index) + ":" + feature
                answers[key] = portable(result, self.p["base"])
                if measure:
                    samples.append({"feature": feature, "site": q, "latency_ms": elapsed})
        # Navigation waits for the current project analysis, whose publications
        # precede its reply. An inspection is a deterministic freshness barrier,
        # not a timed diagnostic publication and not a polling loop.
        start = time.perf_counter_ns()
        snapshot = self.request("virgil-lsp/snapshot")
        bench.check(snapshot is not None, "project analysis did not complete")
        bench.check(snapshot["projectConfiguration"]["current"], snapshot)
        bench.check(all(v["current"] for v in snapshot["documentVersions"]), snapshot)
        answers["diagnostics"] = self.watched_diagnostics(diagnostic_paths)
        if measure:
            samples.append({"feature": "diagnostics", "latency_ms": (time.perf_counter_ns() - start) / 1e6,
                            "boundary": "post-navigation snapshot and publication inspection"})
            self.row["snapshot"] = snapshot
        return answers, samples

    def close(self):
        try:
            self.s.shutdown()
            self.collect()
        finally:
            try:
                with cleanup(self.row):
                    log = bench.logs(self.s)
                    self.row.update({"server_pid": self.s.proc.pid, "command": [str(self.exe), "--stdio"],
                                     "stderr": log, "probes": bench.probes(log),
                                     "worker_replacements": len(re.findall(r"replacing analysis worker \d+", log))})
            finally:
                with cleanup(self.row):
                    self.s.close()


def capture(exe, state, overlays, queries, paths):
    row = {}
    run = Run(exe, state["project"], row)
    try:
        run.start(overlays)
        answers, _ = run.answers(queries, paths)
        return answers, row
    except BaseException as error:
        row["transcript"] = run.s.transcript
        error.tolerance_record = row
        raise
    finally:
        with cleanup(row):
            run.close()


def execute(exe, state, mode, oracle):
    project = state["project"]
    row = {k: v for k, v in state.items() if k in (
        "id", "suite", "edit_kind", "seed_policy", "prefix_bytes", "suffix_bytes", "changed", "parseable_current")}
    row["mode"] = mode
    row["configuration"] = {"predecessor": project["config"], "current": state.get("new_config", project["config"])}
    row["texts"] = {role: {p: {"sha256": bench.corpus.sha256(t.encode()), "bytes": len(t.encode())}
                           for p, t in state[role].items()} for role in ("predecessor", "current")}
    row["oracle_id"] = oracle["id"]
    run = Run(exe, project, row)
    try:
        initial = state["predecessor"] if mode == "warm" else state["current"]
        if state.get("disk"):
            for path, text in initial.items():
                (project["base"] / path).write_bytes(text.encode())
            initial = {}
        run.start(initial)
        if mode == "warm":
            # A real parsed predecessor is analyzed in this same server session.
            run.answers(state.get("stale_queries", state["queries"]), state["diagnostic_paths"])
        offset = os.fstat(run.s.err.fileno()).st_size
        start = time.perf_counter_ns()
        if mode == "warm":
            for path, text in state["current"].items():
                if state.get("disk"):
                    (project["base"] / path).write_bytes(text.encode())
                    bench.notify(run.s, "workspace/didChangeWatchedFiles", {"changes": [
                        {"uri": (project["base"] / path).as_uri(), "type": 2}]})
                else:
                    run.change(path, text)
        if state.get("project_change"):
            # Change source membership or language flags while A is broken.
            # The oracle is captured with exactly this configuration.
            run.open(".virgil-lsp.json", json.dumps(state["new_config"]), "json")
        actual, samples = run.answers(state["queries"], state["diagnostic_paths"], measure=True)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        samples[-1].update({"latency_ms": elapsed,
                            "boundary": "state start to post-navigation diagnostic inspection (publication upper bound)"})
        expected_paths = row["configuration"]["current"]["projects"][0]["sources"]
        bench.check([v["uri"] for v in row["snapshot"]["documentVersions"]] == [
            (project["base"] / path).as_uri() for path in expected_paths], row["snapshot"])
        row["excluded_bodies"] = excluded_bodies(row["snapshot"], project["base"])
        row["expectation_mismatches"] = mismatches(state, row["excluded_bodies"], row["snapshot"], actual)
        expected = expected_answers(oracle["answers"], row["snapshot"], state["queries"])
        row.update({"state_latency_ms": elapsed, "answers": actual,
                    "samples": samples, "scores": {
                        key: score(key.split(":")[-1], value, expected[key], state.get("allow_absent", False))
                        for key, value in actual.items()}, "measured_probes": bench.probes(bench.logs(run.s, offset))})
        # A live worker's RSS is sampled before shutting down the owned process.
        row["resources"] = bench.resources(run.s.proc.pid)
        return row
    except BaseException as error:
        row["transcript"] = run.s.transcript
        error.tolerance_record = row
        raise
    finally:
        try:
            with cleanup(row):
                run.close()
        finally:
            for path in state["current"]:
                with cleanup(row):
                    (project["base"] / path).write_bytes(project["sources"][path].encode())


def excluded_bodies(snapshot, base):
    """The number of excluded bodies of each substituted file. Builds before
    #312 report one body as bodyBegin and bodyEnd."""
    return {v["uri"].removeprefix(base.as_uri() + "/"): len(v["substitution"].get("bodies", [None]))
            for v in snapshot["documentVersions"] if "substitution" in v}


def mismatches(state, bodies, snapshot, answers):
    """How a control's substitution differs from what it expects: the number of
    excluded bodies of each file, and, where an excluded error is the
    fifteenth, an analysis that reaches the error limit without a notice."""
    found = [f"{path}: {bodies.get(path, 0)} excluded bodies, expected {count}"
             for path, count in state.get("expected_bodies", {}).items() if bodies.get(path, 0) != count]
    if state.get("expected_limit"):
        notices = [d for d in answers["diagnostics"] if d["diagnostic"]["code"] == "ErrorLimit"]
        if len(snapshot["diagnostics"]) < 15 or notices:
            found.append(f"{len(snapshot['diagnostics'])} errors and {len(notices)} limit notices, "
                         "expected the limit without a notice")
    return found


def substitution_kinds(snapshot, base):
    """These sessions remove ordinary initializers, which fallback restores;
    repair blanks the body, which moves no offset.

    Fallback restores the original expression or the longer error-filled body,
    so its interval always changes length, and inspecting the interval
    distinguishes the two independently of answer quality.
    """
    return {v["uri"].removeprefix(base.as_uri() + "/"):
            ("repair" if v["substitution"]["effectiveEnd"] == v["substitution"]["currentEnd"] else "fallback")
            for v in snapshot["documentVersions"] if "substitution" in v}


def check_session_step(step, row, base, mode):
    kinds = substitution_kinds(row["snapshot"], base)
    row["substitution_kinds"] = kinds
    for path in step.get("absent_substitutions", []):
        bench.check(path not in kinds, (step["id"], "closed file substituted", kinds))
    for path, expected in step.get("substitution", {}).items():
        if isinstance(expected, dict):
            expected = expected[mode]
        bench.check(kinds.get(path) == expected, (step["id"], expected, kinds))
    if step.get("full_semantics"):
        bench.check(all(v["correct"] for v in row["scores"].values()), ("full semantics did not return", row))
    if step.get("no_substitutions"):
        bench.check(not kinds, ("parseable program substituted", row))
    if step.get("member"):
        bench.check((base / step["member"]).as_uri() in [v["uri"] for v in row["snapshot"]["documentVersions"]], row)
    if step.get("limit_reached"):
        bench.check(len(row["snapshot"]["diagnostics"]) >= 15, ("error limit not reached", row))
    if "limit_notice" in step:
        notices = [d for d in row["answers"]["diagnostics"] if d["diagnostic"]["code"] == "ErrorLimit"]
        # A cold fallback-excluded step repairs instead, and still excludes errors.
        expected_notice = step["limit_notice"]
        if isinstance(expected_notice, dict):
            expected_notice = expected_notice[mode]
        bench.check(bool(notices) == expected_notice, (step["id"], "ErrorLimit policy", notices))


def execute_session(exe, case, mode, oracle=None):
    """Replay a whole history in one process, or capture its intended-text oracle."""
    project, intended = case["project"], oracle is None
    row = {"id": case["id"], "description": case["description"], "suite": "session",
           "mode": "oracle" if intended else mode, "steps": [], "configuration": project["config"],
           "initial": case["initial"], "cache_charge": case.get("cache_charge")}
    run = Run(exe, project, row)
    paths = set(project["sources"]) | {a["path"] for st in case["steps"] for a in st["actions"]}
    try:
        for path, text in project["sources"].items():
            (project["base"] / path).write_bytes(text.encode())
        for path in paths - project["sources"].keys():
            (project["base"] / path).unlink(missing_ok=True)
        (project["base"] / ".virgil-lsp.json").write_text(json.dumps(project["config"]))
        initial = dict(case["initial"])
        if mode == "cold" and not intended:
            for a in case["steps"][0]["actions"]:
                bench.check(a["op"] == "change", "first cold step must only change initial overlays")
                initial[a["path"]] = a["text"]
        run.start(initial)
        if mode == "warm" or intended:
            row["predecessor_answers"], _ = run.answers(case["queries"], paths)
        for index, st in enumerate(case["steps"]):
            record = {"id": st["id"], "suite": "session", "mode": mode,
                      "edit_kind": st["id"], "actions": st["actions"]}
            row["steps"].append(record)
            run.row = record
            offset = os.fstat(run.s.err.fileno()).st_size
            start = time.perf_counter_ns()
            for a in st["actions"]:
                if a["op"] == "write":
                    a = {**a, "event_type": 2 if (project["base"] / a["path"]).exists() else 1}
                run.apply(a, intended)
            queries = st.get("queries", case["queries"])
            actual, samples = run.answers(queries, paths, measure=True)
            bench.check({q["path"] for q in queries} <= {
                v["uri"].removeprefix(project["base"].as_uri() + "/")
                for v in record["snapshot"]["documentVersions"]}, ("wrong project snapshot", record))
            samples[-1].update(latency_ms=(time.perf_counter_ns() - start) / 1e6,
                               boundary="step start to post-navigation diagnostic inspection (publication upper bound)")
            record.update(answers=actual, samples=samples, queries=queries,
                          state_latency_ms=(time.perf_counter_ns() - start) / 1e6,
                          measured_probes=bench.probes(bench.logs(run.s, offset)))
            if intended:
                bench.check(all(actual["0:" + f] for f in FEATURES[:3]), (case["id"], st["id"], "unbound observer oracle"))
                if st.get("stale_control"):
                    stale = row["predecessor_answers"]
                    scores = {k: score(k.split(":")[-1], v, actual[k]) for k, v in stale.items()}
                    bench.check(any(s["wrong"] for s in scores.values()), "session stale control was not rejected")
                    record["stale_control"] = {"answers": stale, "scores": scores}
            else:
                expected = expected_answers(oracle["steps"][index]["answers"], record["snapshot"], queries)
                record["scores"] = {k: score(k.split(":")[-1], v, expected[k]) for k, v in actual.items()}
                record["oracle_step"] = index
                bench.check(not any(v["wrong"] for v in record["scores"].values()), (case["id"], st["id"], "wrong session answer"))
                check_session_step(st, record, project["base"], mode)
        row["resources"] = bench.resources(run.s.proc.pid)
        return row
    except BaseException as error:
        row["transcript"] = run.s.transcript
        error.tolerance_record = row
        raise
    finally:
        run.row = row
        with cleanup(row):
            run.close()


def session_summary(rows):
    steps = [{**st, "worker_replacements": 0} for row in rows for st in row["steps"]]
    result = summary(steps)
    result["worker_replacements"] = sum(row["worker_replacements"] for row in rows)
    return result


def summary(rows):
    groups, latencies = {}, collections.defaultdict(list)
    for row in rows:
        for key, verdict in row["scores"].items():
            feature = key.split(":")[-1]
            group = (row["suite"], row["mode"], row["edit_kind"], feature)
            counts = groups.setdefault(group, collections.Counter())
            counts["total"] += 1
            for label in ("available", "correct", "partial", "missing", "wrong", "matches_oracle"):
                counts[label] += int(verdict[label])
        for sample in row["samples"]:
            latencies[(row["suite"], row["mode"], sample["feature"])].append(sample["latency_ms"])
    return {"features": [{"suite": s, "mode": m, "edit_kind": k, "feature": f, **counts}
                         for (s, m, k, f), counts in sorted(groups.items())],
            "latency_ms": [{"suite": s, "mode": m, "feature": f, "n": len(values),
                            "p50": bench.percentile(sorted(values), .5),
                            "p95": bench.percentile(sorted(values), .95)}
                           for (s, m, f), values in sorted(latencies.items())],
            "worker_replacements": sum(row["worker_replacements"] for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "build/virgil-lsp")
    parser.add_argument("--output", default="-")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build/bench-results")
    parser.add_argument("--cpu", default="auto")
    parser.add_argument("--smoke", action="store_true", help="small repair cases, negative controls, and one editing session; no build manifest required")
    parser.add_argument("--only", help="state ID substring for focused investigation")
    parser.add_argument("--suite", choices=("repair", "replay", "negative", "extra", "heldout", "session"))
    args = parser.parse_args()
    os.chdir(ROOT)
    args.exe = args.exe.resolve()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    pinned, _ = bench.corpus.validate(strict=not args.smoke)
    affinity = bench.pin_cpu(args.cpu)
    result = {"schema": 1, "suite": "tolerance", "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "command": [sys.executable, *sys.argv],
              "capture": {"smoke": args.smoke, "suite": args.suite, "only": args.only,
                          "full": not (args.smoke or args.suite or args.only)},
              "server_revision": bench.corpus.git_revision(ROOT, "rev-parse", "HEAD"),
              "git_status": bench.corpus.git_revision(ROOT, "status", "--short"), "host": bench.host_info(affinity),
              "corpus": pinned, "corpus_sha256": bench.corpus.sha256(bench.corpus.MANIFEST.read_bytes()),
              "driver_sha256": {p.name: bench.corpus.sha256(p.read_bytes()) for p in (
                  Path(__file__), Path(__file__).with_name("tolerance-corpus.py"),
                  Path(__file__).with_name("tolerance-sessions.py"))},
              "builds": [bench.binary_info(args.exe, args.smoke)], "oracles": [], "states": [], "sessions": [], "session_oracles": []}
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="tolerance-", dir=args.work_dir.resolve()) as temp:
            corpus = states.Corpus(args.exe, Path(temp))
            cases = []
            if args.suite != "session":
                cases = list(corpus.fixtures())
                if args.smoke:
                    cases = [s for s in cases if s["id"] in ("member-with-prefix", "syntax-error-in-other-file")]
                    cases += [s for s in corpus.controls() if s["suite"] == "negative"]
                else:
                    cases += list(corpus.large()) + list(corpus.controls()) + list(corpus.heldout())
                    bench.check(collections.Counter(s["suite"] for s in cases) == {
                        "repair": 45, "replay": 140, "negative": 56, "extra": 15, "heldout": 94}, "state inventory changed")
                    bench.check(sum(len(s["queries"]) - 1 for s in cases if s["suite"] == "replay") == 400,
                                "unchanged real-file replay query inventory changed")
            if args.only and args.suite != "session":
                cases = [s for s in cases if args.only in s["id"]]
                bench.check(cases, "no states selected")
            if args.suite and args.suite != "session":
                cases = [s for s in cases if s["suite"] == args.suite]
                bench.check(cases, "no states selected")
            cache = {}
            for state in cases:
                # The same set as the oracle key's, so states share an oracle
                # only when their diagnostics cover the same files.
                paths = states.diagnostic_paths(state)
                state["diagnostic_paths"] = paths
                project = state["project"]
                if state.get("project_change"):
                    config = json.loads(json.dumps(project["config"]))
                    if state["id"].endswith("0"):
                        config["projects"][0]["sources"].remove("C.v3")
                    elif state["id"].endswith("1"):
                        config["projects"][0]["compilerArgs"] = ["-lang:fun-exprs=false"]
                    else:
                        # Add a newly discovered intact file with a type error.
                        path = "Added.v3"
                        (project["base"] / path).write_text('component Added { def bad() -> int { return "bad"; } }\n')
                        config["projects"][0]["sources"].append(path)
                        paths.add(path)
                    state["new_config"] = config
                    (project["base"] / ".virgil-lsp.json").write_text(json.dumps(config))
                overlays = {**state["predecessor"], **state.get("oracle", {})}
                key = states.oracle_key(state)
                if key not in cache:
                    answers, process = capture(args.exe, state, overlays, state["queries"], paths)
                    # Nonnegative corpus sites must really bind; a broken oracle
                    # must not make an empty server answer look successful.
                    if state["suite"] != "negative":
                        bench.check(all(value for key, value in answers.items()), (state["id"], "empty oracle", answers))
                    cache[key] = {"id": len(result["oracles"]), "answers": answers, "process": process,
                                  "source_overlays": overlays, "queries": state["queries"],
                                  "configuration": state.get("new_config", project["config"]),
                                  "diagnostic_paths": sorted(paths)}
                    result["oracles"].append(cache[key])
                oracle = cache[key]
                if state["suite"] == "negative":
                    stale, process = capture(args.exe, state, state["predecessor"], state["stale_queries"], paths)
                    injected = {k: score(k.split(":")[-1], v, oracle["answers"][k], True) for k, v in stale.items()}
                    bench.check(any(s["wrong"] for s in injected.values()), (state["id"], "stale control was not rejected"))
                    oracle["stale_control"] = {"answers": stale, "scores": injected, "process": process}
                if state.get("project_change"):
                    (project["base"] / ".virgil-lsp.json").write_text(json.dumps(project["config"]))
                for mode in ("warm", "cold"):
                    row = execute(args.exe, state, mode, oracle)
                    result["states"].append(row)
                if len(result["states"]) % 40 == 0:
                    print(f"tolerance: {len(result['states'])} state/mode records", file=sys.stderr)
            if args.suite in (None, "session"):
                histories = list(sessions.fixtures(corpus, states))
                if args.smoke:
                    histories = [s for s in histories if s.get("smoke")]
                else:
                    histories.append(sessions.pressure(corpus, states))
                    bench.check(len(histories) == 10, "session inventory changed")
                if args.only:
                    histories = [s for s in histories if args.only in s["id"]]
                bench.check(histories or (args.only and cases), "no sessions selected")
                for case in histories:
                    oracle = execute_session(args.exe, case, "warm")
                    result["session_oracles"].append(oracle)
                    for mode in ("warm", "cold"):
                        result["sessions"].append(execute_session(args.exe, case, mode, oracle))
            corpus.verify_inputs()
            result["input_sha256"] = corpus.inputs
        result["wall_seconds"] = time.monotonic() - start
        result["summary"] = summary(result["states"])
        result["session_summary"] = session_summary(result["sessions"])
        session_steps = [st for row in result["sessions"] for st in row["steps"]]
        wrong = sum(s["wrong"] for row in result["states"] + session_steps for s in row["scores"].values())
        partial = sum(s["partial"] for row in result["states"] + session_steps for s in row["scores"].values())
        unmet = [(row["id"], row["mode"], m) for row in result["states"] for m in row["expectation_mismatches"]]
        result["summary"]["expectation_mismatches"] = unmet
        if args.smoke:
            bench.check(wrong == 0, "wrong smoke answer")
            bench.check(not unmet, ("unmet control expectations", unmet))
            for st in session_steps:
                bench.check(all(v["correct"] for v in st["scores"].values()), st)
            for row in result["states"]:
                if row["id"] == "member-with-prefix":
                    bench.check(all(s["correct"] for s in row["scores"].values()), row)
        print(f"tolerance: {len(result['states'])} state/mode records, {partial} partial, {wrong} wrong answers, "
              f"{len(unmet)} unmet control expectations, {len(session_steps)} session steps, "
              f"{result['wall_seconds']:.2f}s", file=sys.stderr)
    except BaseException as error:
        result["error"] = repr(error)
        result["failed_record"] = getattr(error, "tolerance_record", None)
        if args.output != "-":
            with cleanup(result["failed_record"] if result["failed_record"] is not None else result):
                bench.write_json(args.output + ".failure.json", result)
        raise
    bench.write_json(args.output, result)


if __name__ == "__main__":
    main()
