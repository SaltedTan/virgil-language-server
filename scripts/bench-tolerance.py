#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Measure semantic availability and correctness during syntax errors over stdio."""
import argparse
import collections
import datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

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
ROOT = bench.ROOT
FEATURES = ("definition", "hover", "references", "diagnostics")


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
    return {"available": available, "correct": equal or (allow_absent and empty),
            "wrong": available and not equal, "matches_oracle": equal}


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
        self.open("Observer.v3", self.p["sources"]["Observer.v3"])

    def watched_diagnostics(self, paths):
        result = []
        for path in sorted(paths):
            uri = (self.p["base"] / path).as_uri()
            publication = self.diagnostics.get(uri, {})
            if "version" in publication:
                bench.check(publication["version"] == self.versions[path], ("stale diagnostic version", publication))
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
            log = bench.logs(self.s)
            self.row.update({"server_pid": self.s.proc.pid, "command": [str(self.exe), "--stdio"],
                             "stderr": log, "probes": bench.probes(log),
                             "worker_replacements": len(re.findall(r"replacing analysis worker \d+", log))})
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
            # Remove C from membership, or change language flags, while A is
            # broken. The oracle is captured with exactly this configuration.
            run.open(".virgil-lsp.json", json.dumps(state["new_config"]), "json")
        actual, samples = run.answers(state["queries"], state["diagnostic_paths"], measure=True)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        samples[-1].update({"latency_ms": elapsed,
                            "boundary": "state start to post-navigation diagnostic inspection (publication upper bound)"})
        expected_paths = row["configuration"]["current"]["projects"][0]["sources"]
        bench.check([v["uri"] for v in row["snapshot"]["documentVersions"]] == [
            (project["base"] / path).as_uri() for path in expected_paths], row["snapshot"])
        row.update({"state_latency_ms": elapsed, "answers": actual,
                    "samples": samples, "scores": {
                        key: score(key.split(":")[-1], value, oracle["answers"][key], state.get("allow_absent", False))
                        for key, value in actual.items()}, "measured_probes": bench.probes(bench.logs(run.s, offset))})
        # A live worker's RSS is sampled before shutting down the owned process.
        row["resources"] = bench.resources(run.s.proc.pid)
        return row
    except BaseException as error:
        row["transcript"] = run.s.transcript
        error.tolerance_record = row
        raise
    finally:
        run.close()
        for path in state["current"]:
            (project["base"] / path).write_bytes(project["sources"][path].encode())


def summary(rows):
    groups, latencies = {}, collections.defaultdict(list)
    for row in rows:
        for key, verdict in row["scores"].items():
            feature = key.split(":")[-1]
            group = (row["suite"], row["mode"], row["edit_kind"], feature)
            counts = groups.setdefault(group, collections.Counter())
            counts["total"] += 1
            for label in ("available", "correct", "wrong", "matches_oracle"):
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
    parser.add_argument("--smoke", action="store_true", help="two small repair cases in both modes; no build manifest required")
    parser.add_argument("--only", help="state ID substring for focused investigation")
    parser.add_argument("--suite", choices=("repair", "replay", "negative", "extra"))
    args = parser.parse_args()
    os.chdir(ROOT)
    args.exe = args.exe.resolve()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    pinned, _ = bench.corpus.validate(strict=not args.smoke)
    affinity = bench.pin_cpu(args.cpu)
    result = {"schema": 1, "suite": "tolerance", "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "command": [sys.executable, *sys.argv], "server_revision": bench.corpus.git_revision(ROOT, "rev-parse", "HEAD"),
              "git_status": bench.corpus.git_revision(ROOT, "status", "--short"), "host": bench.host_info(affinity),
              "corpus": pinned, "corpus_sha256": bench.corpus.sha256(bench.corpus.MANIFEST.read_bytes()),
              "driver_sha256": {p.name: bench.corpus.sha256(p.read_bytes()) for p in (
                  Path(__file__), Path(__file__).with_name("tolerance-corpus.py"))},
              "builds": [bench.binary_info(args.exe, args.smoke)], "oracles": [], "states": []}
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="tolerance-", dir=args.work_dir.resolve()) as temp:
            corpus = states.Corpus(args.exe, Path(temp))
            cases = list(corpus.fixtures())
            if args.smoke:
                cases = [s for s in cases if s["id"] in ("member-with-prefix", "syntax-error-in-other-file")]
                cases += [s for s in corpus.controls() if s["suite"] == "negative"]
            else:
                cases += list(corpus.large()) + list(corpus.controls())
                bench.check(collections.Counter(s["suite"] for s in cases) == {
                    "repair": 45, "replay": 140, "negative": 3, "extra": 15}, "state inventory changed")
                bench.check(sum(len(s["queries"]) - 1 for s in cases if s["suite"] == "replay") == 400,
                            "unchanged real-file replay query inventory changed")
            if args.only:
                cases = [s for s in cases if args.only in s["id"]]
                bench.check(cases, "no states selected")
            if args.suite:
                cases = [s for s in cases if s["suite"] == args.suite]
                bench.check(cases, "no states selected")
            cache = {}
            for state in cases:
                paths = {q["path"] for q in state["queries"]} | {"Observer.v3"}
                if state["id"].startswith("fresh-type-error"):
                    paths.add("C.v3")
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
                key = (str(project["base"]), json.dumps(overlays, sort_keys=True),
                       json.dumps(state["queries"], sort_keys=True), tuple(sorted(paths)))
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
            corpus.verify_inputs()
            result["input_sha256"] = corpus.inputs
        result["wall_seconds"] = time.monotonic() - start
        result["summary"] = summary(result["states"])
        wrong = sum(s["wrong"] for row in result["states"] for s in row["scores"].values())
        if args.smoke:
            bench.check(wrong == 0, "wrong smoke answer")
            for row in result["states"]:
                if row["id"] == "member-with-prefix":
                    bench.check(all(s["correct"] for s in row["scores"].values()), row)
        print(f"tolerance: {len(result['states'])} state/mode records, {wrong} wrong answers, "
              f"{result['wall_seconds']:.2f}s", file=sys.stderr)
    except BaseException as error:
        result["error"] = repr(error)
        result["failed_record"] = getattr(error, "tolerance_record", None)
        if args.output != "-":
            bench.write_json(args.output + ".failure.json", result)
        raise
    bench.write_json(args.output, result)


if __name__ == "__main__":
    main()
