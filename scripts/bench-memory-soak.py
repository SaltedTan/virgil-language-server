#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in memory soak through default worker retirement and project-cache turnover.

One server lifetime, with the default worker limits, analyzes three physical
copies of the pinned Aeneas program in turn under a fixed action schedule,
and records per-process memory, worker retirements, and the snapshot and
parseable-text charges that a BENCH=1 build reports. See
docs/benchmarks.md#memory-soak.
"""
import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import statistics
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = load("bench-lsp")
states = load("tolerance-corpus")
sessions = load("tolerance-sessions")
ROOT = bench.ROOT

CONTEXTS = ("first", "second", "third")
WARMUPS, CYCLES = 2, 80
# The smoke run's two cycles; the second has every periodic action.
SMOKE_CYCLES = (39, 40)
PERIODS = (("reopen", 10), ("break", 20), ("descriptors", 40))
REQUEST_S, RUN_S = 15, 30 * 60
# The limits the soak checks, as the server sets them: AnalysisWorkerLimits,
# ParseableTextCache.MAX_BYTES, WorkspaceProjects.MAX_SNAPSHOT_BYTES, and the
# project input bounds of ProjectLimits and LspAnalysisCapture.
MAX_ANALYSES, MAX_UID = 100, 1 << 28
TEXT_CACHE_BYTES = 4 * 1024 * 1024
SNAPSHOT_BUDGET = 32 * 1024 * 1024
MAX_PROJECT_FILES, MAX_CAPTURE_BYTES = 1024, 4 * 1024 * 1024
REQUEST_CANCELLED = -32800
OBSERVER = "Observer.v3"
EARLY = "aeneas/src/arm64/Arm64AddrPatcher.v3"
DESCRIBED = "Described.v3"
# The descriptor fixture of LspProjectAnalysisTest, with names that Aeneas doesn't declare.
DESCRIBED_TEXT = ("// A descriptor clause parses only with -lang:descriptors.\n"
                  "class SoakKind describes SoakShape { }\nclass SoakShape descriptor SoakKind { }\n"
                  "component SoakShapes {\n\tdef none: SoakShape;\n}\n")
PROBE = re.compile(r"^BENCH (server|worker) (\d+) (\w+) (-?\d+)$")
RETIRED = re.compile(r"^virgil-lsp: replacing analysis worker (\d+) (?:after (\d+) analyses \(limit (\d+)\)"
                     r"|because its UID counter reached (\d+) \(limit (\d+)\)"
                     r"|because its live heap reached (\d+) bytes \(limit (\d+)\))$")
FAILED = re.compile(r"^virgil-lsp: analysis worker (\d+) (.*); it ended with (.*)$")
STATE = ("worker_analyses", "worker_uid_next", "worker_live_bytes", "worker_live_limit_bytes")


class Finding(Exception):
    """A protocol barrier that could not complete: the run stops here."""
    bound = "barrier"


class Deadline(Finding):
    """A request, a write, or the whole run passed its deadline."""
    bound = "deadline"


class WriteDeadline(Deadline):
    """A write to the server didn't finish by its deadline."""


class DeadlineSession(bench.Session):
    """A Session whose writes end at the request deadline too, so a server
    that stops reading can't block the run. The server leads its own process
    group, which its workers join, so that stop_group can end them all."""

    def __init__(self, exe, remaining, *options):
        super().__init__(exe, *options, start_new_session=True)
        self.remaining = remaining
        os.set_blocking(self.proc.stdin.fileno(), False)

    def raw(self, data):
        fd, view = self.proc.stdin.fileno(), memoryview(data)
        end = time.monotonic() + self.remaining()
        while view:
            left = end - time.monotonic()
            if left <= 0:
                raise WriteDeadline(("the server read no more of a write before its deadline",
                                     len(data) - len(view), len(data)))
            _, ready, _ = select.select([], [fd], [], left)
            if ready:
                try:
                    view = view[os.write(fd, view):]
                except BlockingIOError:
                    pass


def group_members(pgid):
    """The processes of process group {pgid} that haven't exited, from /proc."""
    members = []
    for entry in os.scandir("/proc"):
        stat = bench.optional_file(f"/proc/{entry.name}/stat") if entry.name.isdigit() else None
        fields = stat.rsplit(")", 1)[1].split() if stat else None
        if fields and int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
            members.append(int(entry.name))
    return members


def stop_group(proc, grace_s=5, deadline_s=5):
    """End a server that leads its own process group, and every process in it.

    Closes its input and gives it {grace_s} to exit. Then, while the leader
    is still unreaped, so that its group ID can't name another group, sends
    SIGKILL to the group, waits on each member's pidfd until {deadline_s},
    and lists the members still running before reaping the leader. A worker
    started in the grace period is in the group, so it can't escape. If the
    leader was already reaped, a process that now has its PID belongs to
    another group, and ours, which would have kept the ID, is empty."""
    pgid = proc.pid
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(grace_s)
    except subprocess.TimeoutExpired:
        pass
    if proc.returncode is not None and os.path.exists(f"/proc/{pgid}"):
        return {"pgid": pgid, "members": [], "survivors": [], "reused": True}
    pidfds = []
    for pid in group_members(pgid):
        try:
            pidfds.append((pid, os.pidfd_open(pid)))
        except OSError:
            pass  # Exited since the listing.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    pending, end = dict(pidfds), time.monotonic() + deadline_s
    try:
        while pending and time.monotonic() < end:
            ready, _, _ = select.select(list(pending.values()), [], [], end - time.monotonic())
            pending = {pid: fd for pid, fd in pending.items() if fd not in ready}
        survivors = group_members(pgid)
    finally:
        for _, fd in pidfds:
            os.close(fd)
        if proc.returncode is None:
            proc.wait()
    return {"pgid": pgid, "members": [pid for pid, _ in pidfds], "survivors": survivors, "reused": False}


def schedule(n):
    """The actions of cycle {n}: measured cycles count from 1, warmups are below.

    Each cycle analyzes every context in order, then submits and cancels one
    temporary query in the last; measured cycles add the periodic actions,
    each in the next context in turn."""
    actions = [("analyze", c) for c in CONTEXTS] + [("temporary", CONTEXTS[-1])]
    if n >= 1:
        for kind, period in PERIODS:
            if n % period == 0:
                actions.append((kind, CONTEXTS[(n // period - 1) % len(CONTEXTS)]))
    return actions


def plan(smoke):
    """(cycle, warmup) pairs in order."""
    if smoke:
        return [(n, False) for n in SMOKE_CYCLES]
    return [(n - WARMUPS, True) for n in range(WARMUPS)] + [(n, False) for n in range(1, CYCLES + 1)]


def expected_counts(cycles):
    counts = collections.Counter()
    for n, warmup in cycles:
        for kind, _ in schedule(n):
            counts[("warmup" if warmup else "measured") + "." + kind] += 1
    return dict(sorted(counts.items()))


def due(state, max_analyses=MAX_ANALYSES, max_uid=MAX_UID):
    """The reason AnalysisSupervisor.retireIfOverLimit gives for {state}, in its order, or None."""
    if state["worker_analyses"] >= max_analyses:
        return "count"
    if state["worker_uid_next"] >= max_uid:
        return "uid"
    if state["worker_live_bytes"] >= state["worker_live_limit_bytes"]:
        return "live"
    return None


class Workers:
    """Classifies worker starts, completed analyses, and retirements from a
    BENCH server trace fed in order. A retirement's logged reason must be the
    one the limits give for that worker's last completed analysis, and every
    analysis that reaches a limit must be followed by its retirement."""

    def __init__(self):
        self.workers, self.findings = [], []
        self.heap = None
        self.state = {}
        self.pending = None  # (worker, reason) after a state that reached a limit

    def current(self):
        return self.workers[-1] if self.workers and self.workers[-1]["ended"] is None else None

    def feed(self, text):
        for line in text.splitlines():
            probe = PROBE.match(line)
            if probe and probe[1] == "server":
                self.probe(probe[3], int(probe[4]))
                continue
            retired = RETIRED.match(line)
            if retired:
                self.retired(retired)
                continue
            failed = FAILED.match(line)
            if failed:
                self.miss()
                worker = self.current()
                if worker and worker["pid"] == int(failed[1]):
                    worker["ended"] = {"reason": "failed", "detail": failed[2], "status": failed[3]}
                else:
                    self.findings.append({"bound": "retirement", "detail": "failure of an unknown worker", "line": line})

    def probe(self, metric, value):
        if metric == "worker_heap_bytes":
            self.heap = value
        elif metric == "worker_pid":
            self.miss()
            if self.current():
                self.findings.append({"bound": "retirement", "detail": "a worker started before the last one ended",
                                      "previous": self.current()["pid"], "pid": value})
                self.current()["ended"] = {"reason": "unknown"}
            self.workers.append({"pid": value, "heap_bytes": self.heap, "analyses": 0, "last_state": None,
                                 "drift": 0, "ended": None})
            self.heap = None
        elif metric in STATE:
            self.state[metric] = value
            if all(name in self.state for name in STATE):
                state, self.state = self.state, {}
                self.miss()
                worker = self.current()
                if worker is None:
                    self.findings.append({"bound": "retirement", "detail": "an analysis without a started worker",
                                          "state": state})
                    return
                worker["analyses"] += 1
                self.check_counter(worker, state)
                worker["last_state"] = state
                if worker["heap_bytes"] is not None and state["worker_live_limit_bytes"] != worker["heap_bytes"] // 8:
                    self.findings.append({"bound": "retirement", "detail": "live limit is not an eighth of the heap",
                                          "pid": worker["pid"], "state": state, "heap_bytes": worker["heap_bytes"]})
                reason = due(state)
                if reason:
                    self.pending = (worker, reason)

    def check_counter(self, worker, state):
        """The worker's reported analysis count, which decides count retirement,
        must be the completions observed for it: a finding whenever the
        difference changes, by a reset, a missing increment, or a skip."""
        reported = state["worker_analyses"]
        last = (worker["last_state"] or {}).get("worker_analyses", 0)
        drift = reported - worker["analyses"]
        if drift == worker["drift"]:
            return
        worker["drift"] = drift
        detail = ("the worker's analysis count went back" if reported < last else
                  "the worker's analysis count didn't advance" if reported == last else
                  "the worker counted analyses that the trace doesn't show")
        self.findings.append({"bound": "retirement", "detail": detail, "pid": worker["pid"],
                              "reported": reported, "previous": last, "observed": worker["analyses"]})

    def retired(self, match):
        pid = int(match[1])
        if match[2]:
            reason, value, limit = "count", int(match[2]), int(match[3])
        elif match[4]:
            reason, value, limit = "uid", int(match[4]), int(match[5])
        else:
            reason, value, limit = "live", int(match[6]), int(match[7])
        worker = self.current()
        predicted = self.pending[1] if self.pending and self.pending[0] is worker else None
        self.pending = None
        record = {"reason": reason, "value": value, "limit": limit, "predicted": predicted}
        if worker is None or worker["pid"] != pid:
            self.findings.append({"bound": "retirement", "detail": "retirement of an unknown worker", "pid": pid})
            return
        worker["ended"] = record
        if predicted != reason:
            self.findings.append({"bound": "retirement", "detail": "misclassified retirement", "pid": pid,
                                  "logged": reason, "predicted": predicted, "state": worker["last_state"]})

    def miss(self):
        if self.pending:
            worker, reason = self.pending
            self.pending = None
            self.findings.append({"bound": "retirement", "detail": "a limit was reached without retirement",
                                  "pid": worker["pid"], "reason": reason, "state": worker["last_state"]})

    def end(self):
        """The trace is complete: a limit reached last must have retired its worker."""
        self.miss()

    def summary(self):
        reasons = collections.Counter((w["ended"] or {}).get("reason", "live at end") for w in self.workers)
        return {"started": len(self.workers), "reasons": dict(sorted(reasons.items())), "workers": self.workers}


def server_probes(text):
    """Server BENCH records as (metric, value), in trace order."""
    return [(m[3], int(m[4])) for m in map(PROBE.match, text.splitlines()) if m and m[1] == "server"]


def admissions(text):
    """Each WorkspaceProjects.admit call in a BENCH trace, in order."""
    records = []
    for metric, value in server_probes(text):
        if metric == "admit_replaces":
            records.append({"replaces": bool(value), "evicted_bytes": [], "own_freed_bytes": None})
        elif metric.startswith("admit_") and records:
            key = metric.removeprefix("admit_")
            if key == "evicted_bytes":
                records[-1][key].append(value)
            else:
                records[-1][key] = value
    return records


def refusal_cause(admission, budget=SNAPSHOT_BUDGET):
    """For a refused admission: "pinned" if the rename's pinned original alone
    prevents the reservation, otherwise "protected" (the latest snapshot, or
    the active context's own when it is kept)."""
    if admission.get("outcome") != 0:
        return None
    unpinned = admission["protected_bytes"] - admission["pinned_bytes"]
    return "pinned" if admission["pinned_bytes"] > 0 and unpinned <= budget - admission["incoming_bytes"] else "protected"


def admission_findings(admission, budget=SNAPSHOT_BUDGET):
    """How one admission breaks the snapshot budget's contract, if it does."""
    found = []
    if admission.get("outcome") not in (0, 1, 2):
        return ["incomplete admission record"]
    limit = budget - admission["incoming_bytes"]
    if admission["retained_bytes"] > budget:
        found.append("retained snapshot charge exceeds the budget")
    if admission["outcome"] == 1 and admission["retained_bytes"] > limit:
        found.append("admitted without room")
    if admission["outcome"] == 0 and admission["protected_bytes"] <= limit:
        found.append("refused although freeing snapshots would make room")
    if admission["outcome"] == 2:
        freed = sum(admission["evicted_bytes"]) + (admission["own_freed_bytes"] or 0)
        if admission["after_bytes"] > limit:
            found.append("admitted over the budget after eviction")
        if freed == 0 or admission["retained_bytes"] - freed != admission["after_bytes"]:
            found.append("eviction accounting does not balance")
        if admission["after_bytes"] < admission["protected_bytes"]:
            found.append("a protected snapshot was freed")
    return found


def text_cache(text):
    """Each completed analysis's parseable-text cache charges, in order."""
    records = []
    for metric, value in server_probes(text):
        if metric == "text_cache_offered_bytes":
            records.append({"offered_bytes": value})
        elif metric in ("text_cache_evicted_bytes", "text_cache_bytes") and records:
            records[-1][metric.removeprefix("text_cache_")] = value
    return records


def text_cache_findings(record, limit=TEXT_CACHE_BYTES):
    found = []
    if record.get("bytes", 0) > limit:
        found.append("parseable-text charge exceeds its cap")
    if record["offered_bytes"] > limit:
        found.append("one context's parseable inputs exceed the cap")
    return found


def binding_findings(answers, oracle):
    """Definition answers that differ from the context's oracle: a stale or foreign binding."""
    return [{"bound": "binding", "query": name, "answer": answers.get(name), "expected": expected}
            for name, expected in oracle.items() if answers.get(name) != expected]


class Publications:
    """Diagnostic publications for open documents, checked against the versions
    sent and confirmed. A nonempty publication for an open document must carry
    a version that was sent in its current open lifetime, no older than one a
    barrier has confirmed current or already published. (didClose may leave an
    empty publication with the old version; only a nonempty one can be a stale
    answer.)"""

    def __init__(self):
        self.sent, self.confirmed, self.published = {}, {}, {}
        self.findings, self.count = [], 0

    def opened(self, uri, version):
        self.sent[uri], self.confirmed[uri], self.published[uri] = version, 0, 0

    def changed(self, uri, version):
        self.sent[uri] = version

    def closed(self, uri):
        for table in (self.sent, self.confirmed, self.published):
            table.pop(uri, None)

    def confirm(self, uri, version):
        if uri in self.sent:
            self.confirmed[uri] = max(self.confirmed[uri], version)

    def receive(self, params):
        self.count += 1
        uri, version = params["uri"], params.get("version")
        if uri not in self.sent or version is None or not params["diagnostics"]:
            return
        floor = max(self.confirmed[uri], self.published[uri])
        if version > self.sent[uri] or version < floor:
            self.findings.append({"bound": "publication", "uri": uri, "version": version,
                                  "sent": self.sent[uri], "floor": floor,
                                  "codes": [d.get("code") for d in params["diagnostics"]]})
        self.published[uri] = max(self.published[uri], version)


def peaks(summary):
    """Each sampled PID's largest RSS and high-water mark, and the workers that
    started but were never sampled, whose peaks are unknown, not zero."""
    rows = [{"pid": r["pid"], "role": r["role"], "max_sampled_rss_bytes": r["max_rss_bytes"],
             "hwm_bytes": r["hwm_bytes"], "samples": r["samples"], "seen_at_stop": r["seen_at_stop"]}
            for r in summary["processes"]]
    return {"processes": rows, "unobserved_workers": summary.get("unobserved_workers"),
            "max_sampled_worker_hwm_bytes": max((r["hwm_bytes"] for r in rows if r["role"] == "worker"
                                                 and r["hwm_bytes"] is not None), default=None)}


def trend(points):
    """Least-squares slope per cycle, and the first and last ten values' medians."""
    points = [(x, y) for x, y in points if y is not None]
    if len(points) < 2:
        return {"n": len(points)}
    xs, ys = [x for x, _ in points], [y for _, y in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    slope = sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x in xs)
    return {"n": len(points), "slope_per_cycle": slope, "first10_median": statistics.median(ys[:10]),
            "last10_median": statistics.median(ys[-10:]), "min": min(ys), "max": max(ys)}


def start_time(pid):
    """A process's start time in clock ticks since boot, or None."""
    stat = bench.optional_file(f"/proc/{pid}/stat")
    return int(stat.rsplit(")", 1)[1].split()[19]) if stat else None


def fixture_hash(base):
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(base)).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def input_bounds(project):
    """Each context's project input, as source expansion and analysis capture count it."""
    result = {}
    for definition in project["config"]["projects"]:
        files = [project["base"] / p for p in definition["sources"]]
        source = sum(f.stat().st_size for f in files)
        capture = 32 + sum(len(str(f)) + 16 + f.stat().st_size for f in files)
        result[definition["name"]] = {"files": len(files), "source_bytes": source, "capture_bytes": capture,
                                      "fits": len(files) <= MAX_PROJECT_FILES and capture <= MAX_CAPTURE_BYTES}
    return result


class Soak:
    def __init__(self, exe, project, queries, result, deadline):
        self.exe, self.p, self.queries, self.result, self.deadline = exe, project, queries, result, deadline
        self.base = project["base"]
        self.config = project["config"]
        self.versions, self.texts = {}, {}
        self.publications = Publications()
        self.workers = Workers()
        self.oracle = {}
        self.cursor = 0
        self.findings = result["findings"]
        self.cycle_findings = 0
        self.observer_s = 0.0
        self.owned = {}
        self.s = DeadlineSession(exe, self.remaining)  # The default worker limits: no options.
        self.trace_offset = 0
        self.where = {"cycle": None, "action": "start"}
        self.sampler = bench.ResourceSampler(self.s.proc.pid, "initial", read=self.read).start()

    # Protocol

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise Deadline("the whole-run deadline passed")
        return min(REQUEST_S, left)

    def request(self, method, params=None):
        id = self.s.send(method, params)
        reply = self.s.reply(id, self.remaining())
        self.collect()
        if "result" not in reply:
            raise Finding(("request failed", method, reply))
        return reply["result"]

    def collect(self):
        for direction, msg in self.s.transcript[self.cursor:]:
            if direction == "recv" and msg.get("method") == "textDocument/publishDiagnostics":
                before = len(self.publications.findings)
                self.publications.receive(msg["params"])
                self.record(self.publications.findings[before:])
        self.cursor = len(self.s.transcript)

    def record(self, found, **context):
        for finding in found:
            self.findings.append({**finding, **context, "at": self.where})
            self.cycle_findings += 1

    def uri(self, path):
        return (self.base / path).as_uri()

    def open(self, path, text, language="virgil"):
        bench.open_document(self.s, self.base / path, text, language)
        self.versions[path], self.texts[path] = 1, text
        self.publications.opened(self.uri(path), 1)

    def change(self, path, text):
        self.versions[path] += 1
        self.texts[path] = text
        self.publications.changed(self.uri(path), self.versions[path])
        bench.notify(self.s, "textDocument/didChange", {
            "textDocument": {"uri": self.uri(path), "version": self.versions[path]},
            "contentChanges": [{"text": text}]})

    def close(self, path):
        bench.notify(self.s, "textDocument/didClose", {"textDocument": {"uri": self.uri(path)}})
        del self.versions[path], self.texts[path]
        self.publications.closed(self.uri(path))

    def definitions(self, ctx):
        answers = {}
        for q in self.queries[ctx]:
            answers[q["token"]] = self.request("textDocument/definition", {
                "textDocument": {"uri": self.uri(q["path"])}, "position": q["position"]})
        return answers

    def snapshot_findings(self, snap, ctx, expect=None, substituted=(), extra=()):
        """The latest snapshot must be {ctx}'s, current, over its own sources."""
        found = []
        if snap is None or snap.get("projectConfiguration", {}).get("name") != ctx:
            return [{"bound": "barrier", "detail": "the latest snapshot is not the context's",
                     "name": (snap or {}).get("projectConfiguration", {}).get("name")}]
        if not snap["projectConfiguration"]["current"] or not all(v["current"] for v in snap["documentVersions"]):
            found.append({"bound": "barrier", "detail": "the snapshot is not current"})
        uris = [v["uri"] for v in snap["documentVersions"]]
        own = [self.uri(p) for p in next(d for d in self.config["projects"] if d["name"] == ctx)["sources"]]
        if uris != own + [self.uri(f"{ctx}/{p}") for p in extra]:
            found.append({"bound": "barrier", "detail": "the snapshot's sources are not the context's"})
        versions = {v["uri"]: v for v in snap["documentVersions"]}
        for path, version in (expect or {}).items():
            got = versions.get(self.uri(path), {}).get("version")
            if got != version:
                found.append({"bound": "version", "path": path, "version": got, "expected": version})
            elif version is not None:
                self.publications.confirm(self.uri(path), version)
        subs = {u.removeprefix(self.base.as_uri() + "/") for u, v in versions.items() if "substitution" in v}
        if subs != {f"{ctx}/{p}" for p in substituted}:
            found.append({"bound": "barrier", "detail": "unexpected substitutions", "substituted": sorted(subs)})
        diagnostics = [{**d, "path": d["path"].removeprefix(str(self.base / ctx) + "/")} for d in snap["diagnostics"]]
        if diagnostics != self.observer_diagnostics:
            found.append({"bound": "barrier", "detail": "unexpected diagnostics", "diagnostics": snap["diagnostics"][:5]})
        return found

    def barrier(self, ctx, expect=None, substituted=(), extra=(), strict=True):
        """Navigation in {ctx} waits for its current analysis; then the bindings
        must match the oracle and, if {strict}, the latest snapshot must be
        {ctx}'s at the expected document versions."""
        answers = self.definitions(ctx)
        self.record(binding_findings(answers, self.oracle[ctx]), context=ctx)
        snap = self.request("virgil-lsp/snapshot")
        if strict:
            self.record(self.snapshot_findings(snap, ctx, expect, substituted, extra), context=ctx)
        return snap

    # Sampling

    def read(self):
        start = time.perf_counter()
        tree = bench.process_tree(self.s.proc.pid)
        self.observer_s += time.perf_counter() - start
        for pid, (role, _, _) in (tree or {}).items():
            if role == "worker" and pid not in self.owned:
                self.owned[pid] = start_time(pid)
        return tree

    def resources(self):
        with self.sampler.lock:
            tree = self.read()
        return None if tree is None else {str(pid): {"role": role, "rss_bytes": rss, "hwm_bytes": hwm}
                                          for pid, (role, rss, hwm) in sorted(tree.items())}

    def trace(self):
        """The server's stderr since the last call, in whole lines only."""
        fd = self.s.err.fileno()
        data = os.pread(fd, os.fstat(fd).st_size - self.trace_offset, self.trace_offset)
        data = data[:data.rfind(b"\n") + 1]
        self.trace_offset += len(data)
        return data.decode(errors="replace")

    def transition(self, cycle, warmup, kind, ctx, start, **extra):
        """Everything observed since the last transition."""
        text = self.trace()
        self.workers.feed(text)
        self.record(self.workers.findings, context=ctx)
        self.workers.findings = []
        record = {"cycle": cycle, "warmup": warmup, "action": kind, "context": ctx,
                  "findings": len(self.findings) - self.step_start,
                  "elapsed_ms": (time.perf_counter_ns() - start) / 1e6,
                  "worker": (self.workers.current() or {}).get("pid"), "resources": self.resources(), **extra}
        probes = server_probes(text)
        values = lambda name: [v for m, v in probes if m == name]
        record["analyses"] = len(values("roundtrip_us"))
        record["discarded"] = len(re.findall(r"^virgil-lsp: discarded analysis ", text, re.M))
        record["admissions"] = admissions(text)
        for a in record["admissions"]:
            a["refusal"] = refusal_cause(a)
            self.record([{"bound": "admission", "detail": d, "admission": a} for d in admission_findings(a)],
                        context=ctx)
        record["text_cache"] = text_cache(text)
        for t in record["text_cache"]:
            self.record([{"bound": "text_cache", "detail": d, "record": t} for d in text_cache_findings(t)],
                        context=ctx)
        record["snapshot_charge_bytes"] = values("snapshot_bytes")
        record["server_heap_after_decode_bytes"] = values("heap_after_decode_bytes")
        record["server_allocated_bytes"] = values("allocated_bytes")
        record["worker_reserved_heap_bytes"] = values("worker_heap_bytes")
        record["worker_live_after_gc_bytes"] = [int(m[4]) for m in map(PROBE.match, text.splitlines())
                                                if m and m[1] == "worker" and m[3] == "live_after_gc_bytes"]
        record["worker_uid_next"] = values("worker_uid_next")
        self.result["transitions"].append(record)
        return record

    # Actions

    def start(self):
        (self.base / ".virgil-lsp.json").write_text(json.dumps(self.config))
        self.request("initialize", {"rootUri": self.base.as_uri(),
                     "initializationOptions": {"virgilRoot": str(ROOT / "vendor/virgil")},
                     "capabilities": {"textDocument": {"hover": {"contentFormat": ["plaintext"]}}}})
        bench.notify(self.s, "initialized", {})
        start = time.perf_counter_ns()
        for ctx in CONTEXTS:
            self.open(f"{ctx}/{OBSERVER}", states.OBSERVER)
        # The oracle: each context's first bindings, which must be its own copies'.
        for ctx in CONTEXTS:
            self.oracle[ctx] = self.definitions(ctx)
            stable, builder = self.oracle[ctx]["stable"], self.oracle[ctx]["StringBuilder"]
            if not (stable and stable["uri"] == self.uri(f"{ctx}/{OBSERVER}") and builder
                    and builder["uri"] == self.uri(f"{ctx}/lib/util/StringBuilder.v3")):
                raise Finding(("unbound or foreign oracle", ctx, self.oracle[ctx]))
        snap = self.request("virgil-lsp/snapshot")
        ctx = snap["projectConfiguration"]["name"]
        self.observer_diagnostics = [{**d, "path": d["path"].removeprefix(str(self.base / ctx) + "/")}
                                     for d in snap["diagnostics"]]
        if [d["path"] for d in self.observer_diagnostics] != [OBSERVER]:
            raise Finding(("unexpected initial diagnostics", snap["diagnostics"]))
        self.where = {"cycle": None, "action": "initial"}
        self.step_start = len(self.findings)
        self.transition(None, True, "initial", None, start)

    def analyze(self, n, ctx):
        path = f"{ctx}/{OBSERVER}"
        self.change(path, states.OBSERVER + f"// soak cycle {n}, context {ctx}\n")
        snap = self.barrier(ctx, {path: self.versions[path]})
        return {"version": self.versions[path], "generation": snap["generation"]}

    def retained(self, ctx):
        """The previous latest snapshot answers navigation without an analysis."""
        offset = os.fstat(self.s.err.fileno()).st_size
        answers = self.definitions(ctx)
        self.record(binding_findings(answers, self.oracle[ctx]), context=ctx, check="retained")
        started = server_probes(bench.logs(self.s, offset))
        if any(m in ("admit_replaces", "roundtrip_us") for m, _ in started):
            self.record([{"bound": "protected", "detail": "the previous latest snapshot was evicted", "context": ctx}])
        return True

    def temporary(self, ctx):
        path = f"{ctx}/{OBSERVER}"
        q = self.queries[ctx][0]
        position = {**q["position"], "character": q["position"]["character"] + 3}
        id = self.s.send("textDocument/completion", {"textDocument": {"uri": self.uri(path)}, "position": position})
        self.s.send("$/cancelRequest", {"id": id}, request=False)
        reply = self.s.reply(id, self.remaining())
        self.collect()
        cancelled = reply.get("error", {}).get("code") == REQUEST_CANCELLED
        if "error" in reply and not cancelled:
            raise Finding(("temporary query failed", reply))
        if not cancelled and not any(i["label"] == "stable" for i in (reply["result"] or {}).get("items", [])):
            self.record([{"bound": "binding", "detail": "completed temporary query lacks the observer", "reply": reply}])
        return {"cancel_preceded_completion": cancelled}

    def reopen(self, n, ctx):
        path = f"{ctx}/{OBSERVER}"
        text = self.texts[path]
        self.close(path)
        self.barrier(ctx, {path: None})
        self.open(path, text)
        self.barrier(ctx, {path: 1})
        return {"text_sha256": bench.corpus.sha256(text.encode())}

    def broken(self, n, ctx):
        path = f"{ctx}/{EARLY}"
        original = (self.base / path).read_text()
        broken = original.replace("def pos = mw.pos;", "def pos = ;", 1)
        if broken == original:
            raise Finding(("pressure fixture edit not found", path))
        self.open(path, broken)
        self.barrier(ctx, {path: 1}, substituted=[EARLY])
        self.change(path, original)
        self.barrier(ctx, {path: 2})
        self.close(path)
        self.barrier(ctx, {path: None})
        return {}

    def descriptors(self, n, ctx):
        """Add a descriptor-only source with -lang:descriptors, then remove both.

        Replacing the configuration replaces every context, so the others are
        analyzed again and lose their snapshots and cached texts."""
        config = json.loads(json.dumps(self.config))
        definition = next(d for d in config["projects"] if d["name"] == ctx)
        definition["sources"].append(f"{ctx}/{DESCRIBED}")
        definition["compilerArgs"] = ["-lang:descriptors"]
        (self.base / ctx / DESCRIBED).write_text(DESCRIBED_TEXT)
        name = ".virgil-lsp.json"
        self.open(name, json.dumps(config), "json")
        for other in CONTEXTS:
            self.barrier(other, strict=False)
        self.latest(n, ctx, extra=[DESCRIBED])
        self.change(name, json.dumps(self.config))
        for other in CONTEXTS:
            self.barrier(other, strict=False)
        self.close(name)
        (self.base / ctx / DESCRIBED).unlink()
        bench.notify(self.s, "workspace/didChangeWatchedFiles", {"changes": [
            {"uri": self.uri(f"{ctx}/{DESCRIBED}"), "type": 3}]})
        for other in CONTEXTS:
            self.barrier(other, strict=False)
        self.latest(n, ctx)
        return {}

    def latest(self, n, ctx, extra=()):
        """Make {ctx}'s analysis the latest with one more edit, then check its snapshot.

        Not an explicit project analysis: that keeps the context's own
        snapshot beside the latest, so two Aeneas-size snapshots and its
        reservation exceed the budget, and admission refuses it whenever
        another context was analyzed last."""
        path = f"{ctx}/{OBSERVER}"
        self.change(path, states.OBSERVER + f"// soak cycle {n}, context {ctx}, configuration\n")
        self.barrier(ctx, {path: self.versions[path]}, extra=extra)

    def cycle(self, n, warmup):
        self.sampler.mark("warmup" if warmup else f"cycle-{n:02d}")
        self.cycle_findings = 0
        transcript_start = len(self.s.transcript)
        previous = None
        for kind, ctx in schedule(n):
            self.where = {"cycle": n, "action": kind, "context": ctx}
            self.step_start = len(self.findings)
            start = time.perf_counter_ns()
            if kind == "analyze":
                extra = self.analyze(n, ctx)
                if previous is not None:
                    extra["retained_checked"] = self.retained(previous)
                previous = ctx
            else:
                previous = None
                extra = {"temporary": self.temporary,
                         "reopen": lambda c: self.reopen(n, c),
                         "break": lambda c: self.broken(n, c),
                         "descriptors": lambda c: self.descriptors(n, c)}[kind](ctx)
            record = self.transition(n, warmup, kind, ctx, start, **extra)
            if kind == "analyze" and ctx == CONTEXTS[-1]:
                record["settled"] = True
        if self.cycle_findings:
            self.result["failing_transcripts"].append({"cycle": n, "transcript": self.s.transcript[transcript_start:]})
        # Bounded retention: keep only the transcript of cycles with findings.
        del self.s.transcript[:]
        self.cursor = 0

    def finish(self):
        """Stop sampling, shut down, and reap any owned worker that outlived the server."""
        trace = bench.logs(self.s)
        starts = sum(m == "worker_start_us" for m, _ in server_probes(trace))
        summary = self.sampler.stop(starts)
        self.result["resources"] = summary
        self.result["peaks"] = peaks(summary)
        self.result["observer_cost"] = {"passes": summary["passes"] + summary["failed_passes"],
                                        "seconds": self.observer_s,
                                        "mean_us": 1e6 * self.observer_s / max(1, summary["passes"])}
        try:
            self.s.shutdown()
        finally:
            self.cleanup()

    def cleanup(self):
        """End the server's whole process group, then any owned worker that left it."""
        if self.sampler.thread:
            self.sampler.stop()
        group = stop_group(self.s.proc)
        try:
            self.s.close()
        finally:
            reaped = []
            worker = self.exe.with_name("virgil-lsp-worker").resolve()
            for pid, started in self.owned.items():
                # Only a live process with the same start time and executable is ours.
                try:
                    exe = Path(f"/proc/{pid}/exe").resolve()
                except OSError:
                    continue
                if started is not None and start_time(pid) == started and exe == worker:
                    os.kill(pid, signal.SIGKILL)
                    reaped.append(pid)
            self.result["cleanup"] = {"owned_workers": len(self.owned), "killed": reaped, "group": group}
            if group["survivors"] or reaped:
                self.findings.append({"bound": "cleanup", "detail": "processes outlived the server's group",
                                      "survivors": group["survivors"], "killed": reaped})


def settled(transitions):
    """Each measured cycle's values after its last context's analysis."""
    rows = []
    for t in transitions:
        if not t.get("settled") or t["warmup"]:
            continue
        resources = t["resources"] or {}
        role = lambda r: [v for v in resources.values() if v["role"] == r]
        server, worker = role("server"), role("worker")
        rows.append({"cycle": t["cycle"],
                     "server_rss_bytes": server[0]["rss_bytes"] if server else None,
                     "worker_rss_bytes": worker[0]["rss_bytes"] if worker else None,
                     "sum_rss_bytes": sum(v["rss_bytes"] for v in resources.values()) if resources else None,
                     "server_hwm_bytes": server[0]["hwm_bytes"] if server else None,
                     "worker_pid": t["worker"],
                     "worker_live_after_gc_bytes": (t["worker_live_after_gc_bytes"] or [None])[-1],
                     "server_heap_after_decode_bytes": (t["server_heap_after_decode_bytes"] or [None])[-1],
                     "snapshot_charge_bytes": (t["snapshot_charge_bytes"] or [None])[-1],
                     "text_cache_bytes": (t["text_cache"] or [{}])[-1].get("bytes")})
    return rows


def bounds(result, smoke):
    """Every checked bound, how many observations it covered, and its findings."""
    transitions = result["transitions"]
    admitted = [a for t in transitions for a in t["admissions"]]
    cached = [c for t in transitions for c in t["text_cache"]]
    found = collections.Counter(f["bound"] for f in result["findings"])
    measured = [t for t in transitions if t["action"] == "analyze" and not t["warmup"]]
    offered = {}
    for t in transitions:
        if t["action"] == "analyze" and t["text_cache"]:
            offered[t["context"]] = t["text_cache"][-1]["offered_bytes"]
    evicting = [a for a in admitted if a["evicted_bytes"]]
    return {
        "text_cache_cap": {"checked": len(cached), "max_bytes": max((c.get("bytes", 0) for c in cached), default=None),
                           "limit_bytes": TEXT_CACHE_BYTES, "violations": found["text_cache"]},
        "text_cache_pressure": {"offered_bytes_per_context": offered, "sum_bytes": sum(offered.values()),
                                "completions_with_evictions": sum(c.get("evicted_bytes", 0) > 0 for c in cached),
                                "ok": sum(offered.values()) > TEXT_CACHE_BYTES and all(
                                    v < TEXT_CACHE_BYTES for v in offered.values()) and any(
                                    c.get("evicted_bytes", 0) > 0 for c in cached)},
        "snapshot_budget": {"checked": len(admitted), "budget_bytes": SNAPSHOT_BUDGET,
                            "max_retained_bytes": max((a["retained_bytes"] for a in admitted), default=None),
                            "max_admitted_bytes": max((a.get("after_bytes", a["retained_bytes"]) + a["incoming_bytes"]
                                                       for a in admitted if a.get("outcome") in (1, 2)), default=None),
                            "violations": found["admission"]},
        "inactive_eviction_required": {"admissions_evicting": len(evicting),
                                       "evicted_snapshots": sum(len(a["evicted_bytes"]) for a in evicting),
                                       "own_freed": sum(a["own_freed_bytes"] is not None for a in admitted),
                                       "ok": bool(evicting)},
        "admission_refusals": dict(collections.Counter(a["refusal"] for a in admitted if a["refusal"])),
        "pinned_bytes_max": max((a.get("pinned_bytes", 0) for a in admitted), default=None),
        "protected_retained": {"checked": sum(1 for t in transitions if t.get("retained_checked")),
                               "violations": found["protected"]},
        "retirement_classified": {"retirements": sum(1 for w in result["workers"]["workers"]
                                                     if (w["ended"] or {}).get("reason") in ("count", "uid", "live")),
                                  "violations": found["retirement"]},
        "bindings": {"violations": found["binding"]},
        "versions": {"violations": found["version"] + found["publication"],
                     "publications": result.get("publications")},
        "barriers": {"violations": found["barrier"]},
        "main_completions": {"measured": len(measured),
                             "expected": result["expected_counts"].get("measured.analyze", 0)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "build/bench/virgil-lsp")
    parser.add_argument("--cpu", default="auto", help="auto pins the first allowed CPU with taskset; none disables")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build/bench-results/soak-work")
    parser.add_argument("--output", default="-", help="JSON file, or - for stdout")
    parser.add_argument("--smoke", action="store_true",
                        help=f"cycles {SMOKE_CYCLES[0]} and {SMOKE_CYCLES[1]} only, without warmups or a build manifest")
    args = parser.parse_args()
    os.chdir(ROOT)
    args.exe = args.exe.resolve()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    affinity = bench.pin_cpu(args.cpu)
    cycles = plan(args.smoke)
    began = time.monotonic()
    result = {"schema": 1, "suite": "memory-soak",
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "command": [sys.executable, *sys.argv],
              "server_revision": bench.corpus.git_revision(ROOT, "rev-parse", "HEAD"),
              "git_status": bench.corpus.git_revision(ROOT, "status", "--short"), "host": bench.host_info(affinity),
              "driver_sha256": {p.name: bench.corpus.sha256(p.read_bytes()) for p in (
                  Path(__file__), Path(__file__).with_name("tolerance-sessions.py"),
                  Path(__file__).with_name("bench-lsp.py"))},
              "builds": [bench.binary_info(args.exe, args.smoke)],
              "configuration": {"smoke": args.smoke, "contexts": CONTEXTS, "cycles": [n for n, _ in cycles],
                                "warmups": sum(w for _, w in cycles), "periods": dict(PERIODS),
                                "server_options": [], "request_deadline_s": REQUEST_S, "run_deadline_s": RUN_S,
                                "limits": {"max_analyses": MAX_ANALYSES, "max_uid": MAX_UID,
                                           "max_live": "heap/8", "text_cache_bytes": TEXT_CACHE_BYTES,
                                           "snapshot_budget_bytes": SNAPSHOT_BUDGET}},
              "expected_counts": expected_counts(cycles), "transitions": [], "findings": [],
              "failing_transcripts": []}
    soak = None
    try:
        with tempfile.TemporaryDirectory(prefix="soak-", dir=args.work_dir.resolve()) as temp:
            corpus = states.Corpus(args.exe, Path(temp))
            project, _, queries, charge = sessions.pressure_project(corpus, states, CONTEXTS, "soak")
            (project["base"] / ".virgil-lsp.json").write_text(json.dumps(project["config"]))
            inputs = input_bounds(project)
            result["fixture"] = {"sha256": fixture_hash(project["base"]), "cache_charge_bytes": charge,
                                 "inputs": inputs, "queries": queries}
            if not all(v["fits"] for v in inputs.values()):
                raise Finding(("a context exceeds the project input bound", inputs))
            soak = Soak(args.exe, project, queries, result, began + RUN_S)
            soak.start()
            if not server_probes(bench.logs(soak.s)):
                raise Finding("no BENCH probes: build with make bench-build")
            for n, warmup in cycles:
                soak.cycle(n, warmup)
                done = sum(1 for t in result["transitions"] if t["action"] == "analyze")
                print(f"memory soak: cycle {n}{' (warmup)' if warmup else ''}, {done} analyses, "
                      f"{len(result['findings'])} findings", file=sys.stderr)
            result["publications"] = soak.publications.count
            soak.workers.feed(soak.trace())
            soak.workers.end()
            soak.record(soak.workers.findings)
            result["workers"] = soak.workers.summary()
            result["stderr"] = bench.logs(soak.s)
            soak.finish()
            corpus.verify_inputs()
            result["input_sha256"] = corpus.inputs
        result["settled"] = settled(result["transitions"])
        result["trend"] = {key: trend([(r["cycle"], r[key]) for r in result["settled"]]) for key in (
            "server_rss_bytes", "worker_rss_bytes", "sum_rss_bytes", "worker_live_after_gc_bytes",
            "server_heap_after_decode_bytes")}
        trace = result["stderr"]
        result["analyses"] = {
            "worker_completions": sum(m == "roundtrip_us" for m, _ in server_probes(trace)),
            "main_measured": sum(t["action"] == "analyze" and not t["warmup"] for t in result["transitions"]),
            "main_warmup": sum(t["action"] == "analyze" and t["warmup"] for t in result["transitions"]),
            "by_action": {kind: sum(t["analyses"] for t in result["transitions"] if t["action"] == kind)
                          for kind in ("initial", "analyze", "temporary", "reopen", "break", "descriptors")},
            "discarded": len(re.findall(r"^virgil-lsp: discarded analysis ", trace, re.M)),
            "temporary_cancel_preceded_completion": collections.Counter(
                t["cancel_preceded_completion"] for t in result["transitions"] if t["action"] == "temporary")}
        result["analyses"]["temporary_cancel_preceded_completion"] = {
            str(k).lower(): v for k, v in result["analyses"]["temporary_cancel_preceded_completion"].items()}
        result["bounds"] = bounds(result, args.smoke)
        result["wall_seconds"] = time.monotonic() - began
        b = result["bounds"]
        if not b["text_cache_pressure"]["ok"]:
            result["findings"].append({"bound": "text_cache_pressure", "detail": "the fixture did not exceed the cap",
                                       "evidence": b["text_cache_pressure"]})
        if not b["inactive_eviction_required"]["ok"]:
            result["findings"].append({"bound": "inactive_eviction_required",
                                       "detail": "no admission needed to evict an inactive snapshot"})
        if b["main_completions"]["measured"] != b["main_completions"]["expected"]:
            result["findings"].append({"bound": "main_completions", "evidence": b["main_completions"]})
        workers = result["workers"]
        print(f"memory soak: {result['analyses']['worker_completions']} worker analyses "
              f"({result['analyses']['main_measured']} measured main), {workers['started']} workers "
              f"{workers['reasons']}, {len(result['findings'])} findings, {result['wall_seconds']:.1f}s",
              file=sys.stderr)
    except BaseException as error:
        result["error"] = repr(error)
        if isinstance(error, Finding):
            result["findings"].append({"bound": error.bound, "detail": error.args, "at": soak.where if soak else None})
        if soak:
            result["failure"] = {"transcript": soak.s.transcript}
            if not soak.s.err.closed:
                result["failure"]["stderr"] = bench.logs(soak.s)
            try:
                soak.cleanup()
            except BaseException as cleanup_error:
                result["cleanup_error"] = repr(cleanup_error)
        if args.output != "-":
            bench.write_json(args.output + ".failure.json", result)
        raise
    bench.write_json(args.output, result)
    if result["findings"]:
        print(f"memory soak: {len(result['findings'])} findings; see {args.output}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
