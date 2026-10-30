#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Reproducible stdlib-only LSP benchmarks. See docs/benchmarks.md."""
import argparse
import collections
import datetime
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "test/protocol"))
from worker import Session
from inlay_hints import HintsSession

spec = importlib.util.spec_from_file_location("corpus", ROOT / "scripts/bench-corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def optional_file(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def percentile(values, fraction):
    """Linear interpolation between adjacent order statistics, including n=1."""
    at = (len(values) - 1) * fraction
    lo = int(at)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (at - lo)


def summarize(values):
    values = sorted(values)
    median = statistics.median(values)
    return {"n": len(values), "median": median, "p10": percentile(values, .1),
            "p50": percentile(values, .5), "p90": percentile(values, .9), "p95": percentile(values, .95),
            "min": values[0], "max": values[-1],
            "mad": statistics.median(abs(v - median) for v in values)}


def pin_cpu(cpu):
    before = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    method = None
    if cpu != "none":
        selected = (before[0] if before else None) if cpu == "auto" else int(cpu)
        if selected is not None and shutil.which("taskset"):
            subprocess.run(["taskset", "-pc", str(selected), str(os.getpid())],
                           stdout=subprocess.DEVNULL, check=True)
            method = "taskset"
        elif cpu != "auto":
            raise ValueError("explicit --cpu needs taskset; use --cpu none on macOS")
    return {"requested": cpu, "method": method, "before": before,
            "effective": sorted(os.sched_getaffinity(0)) if before else None}


def host_info(affinity):
    cpu = platform.processor()
    if sys.platform == "darwin":
        cpu = command("sysctl", "-n", "machdep.cpu.brand_string")
    elif Path("/proc/cpuinfo").exists():
        match = re.search(r"^model name\s*:\s*(.*)$", Path("/proc/cpuinfo").read_text(), re.M)
        if match:
            cpu = match[1]
    return {"cpu": cpu, "logical_cores": os.cpu_count(), "kernel": platform.platform(),
            "python": sys.version, "affinity": affinity,
            "governors": {str(c): optional_file(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor")
                          for c in affinity["effective"] or []},
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None}


def binary_info(exe, smoke=False):
    info = {"exe": str(exe), "version": command(str(exe), "--version"),
            "binaries": {p.name: {"sha256": corpus.sha256(p.read_bytes()), "bytes": p.stat().st_size}
                         for p in (exe, exe.with_name("virgil-lsp-worker"))},
            "build_info": optional_file(exe.parent / "gen/BuildInfo.v3")}
    manifest = exe.parent / "benchmark-build.json"
    check(smoke or manifest.exists(), f"missing {manifest}; build with make BUILD={exe.parent} bench-metadata")
    info["build"] = json.loads(manifest.read_text()) if manifest.exists() else None
    return info


def process_tree(pid):
    """{pid: (role, rss_bytes, hwm_bytes)} for a server and its direct children.

    Linux reads /proc without starting a process. Elsewhere `ps` supplies RSS and
    the OS high-water mark is None (unknown, never zero). A process that exits
    between listing and reading is omitted. None means the listing failed.
    """
    tasks = Path(f"/proc/{pid}/task")
    if tasks.is_dir():
        try:
            children = [int(c) for task in tasks.iterdir()
                        for c in (task / "children").read_text().split()]
        except (OSError, ValueError):
            children = None
        if children is not None:
            result = {}
            for process in [pid, *children]:
                status = optional_file(f"/proc/{process}/status")
                rss = status and re.search(r"^VmRSS:\s+(\d+) kB$", status, re.M)
                if rss:
                    peak = re.search(r"^VmHWM:\s+(\d+) kB$", status, re.M)
                    result[process] = ("server" if process == pid else "worker", int(rss[1]) * 1024,
                                       int(peak[1]) * 1024 if peak else None)
            return result
    try:
        rows = [list(map(int, row.split())) for row in command("ps", "-axo", "pid=,ppid=,rss=").splitlines()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    result = {}
    for process, parent, rss in rows:
        if process == pid or parent == pid:
            status = optional_file(f"/proc/{process}/status") or ""
            peak = re.search(r"^VmHWM:\s+(\d+) kB$", status, re.M)
            result[process] = ("server" if process == pid else "worker", rss * 1024,
                               int(peak[1]) * 1024 if peak else None)
    return result


def resources(pid):
    """Current RSS for the server and its worker; Linux adds each process's HWM."""
    tree = process_tree(pid)
    if tree is None:
        return None
    return [{"role": role, "pid": process, "rss_bytes": rss, "peak_rss_bytes": hwm}
            for process, (role, rss, hwm) in sorted(tree.items())]


class ResourceSampler:
    """Per-PID memory for one session: every 50 ms, at marks, and on PID-set changes.

    Every observed PID keeps its own sample maximum and last OS high-water mark,
    including after a replacement worker appears. Values are never summed across
    processes except within one sampling pass. A worker that exits between passes
    is never observed; `unobserved_workers` counts those when completed worker
    starts are known, and their peaks stay unknown.
    """
    INTERVAL = 0.05

    def __init__(self, pid, phase="session", read=None, clock=time.monotonic):
        self.read = read or (lambda: process_tree(pid))
        self.clock, self.started = clock, clock()
        self.phase, self.processes, self.passes, self.failed_passes = phase, {}, 0, 0
        self.max_total, self.lock, self.stopping, self.thread = None, threading.Lock(), threading.Event(), None

    def sample(self):
        """Take one pass; return whether the set of live PIDs changed."""
        with self.lock:
            rows = self.read()
            now = round(self.clock() - self.started, 6)
            if rows is None:
                self.failed_passes += 1
                return False
            self.passes += 1
            live = set(rows)
            changed = live != {p for p, r in self.processes.items() if r["live"]}
            for pid, record in self.processes.items():
                record["live"] = pid in live
            for pid, (role, rss, hwm) in rows.items():
                record = self.processes.setdefault(pid, {
                    "pid": pid, "role": role, "first_seen_s": now, "last_seen_s": now, "samples": 0,
                    "max_rss_bytes": rss, "hwm_bytes": None, "phases": {}, "live": True})
                record["last_seen_s"], record["samples"] = now, record["samples"] + 1
                record["max_rss_bytes"] = max(record["max_rss_bytes"], rss)
                if hwm is not None:
                    record["hwm_bytes"] = max(record["hwm_bytes"] or 0, hwm)
                phase = record["phases"].setdefault(self.phase, {"samples": 0, "max_rss_bytes": rss, "hwm_bytes": None})
                phase["samples"] += 1
                phase["max_rss_bytes"] = max(phase["max_rss_bytes"], rss)
                if hwm is not None:
                    phase["hwm_bytes"] = hwm
            total = sum(rss for _, rss, _ in rows.values())
            self.max_total = total if self.max_total is None else max(self.max_total, total)
            return changed

    def mark(self, phase):
        if self.phase != phase:
            self.phase = phase
            self.sample()

    def start(self):
        self.sample()
        self.thread = threading.Thread(target=self.loop, name="resource-sampler", daemon=True)
        self.thread.start()
        return self

    def loop(self):
        while not self.stopping.wait(self.INTERVAL):
            if self.sample() and not self.stopping.is_set():
                self.sample()  # Follow a PID transition at once.

    def stop(self, worker_starts=None):
        """Final pass, then join; call before the session shuts down."""
        if self.thread:
            self.stopping.set()
            self.thread.join()
            self.thread = None
            self.sample()
        observed = sum(r["role"] == "worker" for r in self.processes.values())
        # A PID gone before the final pass may have grown after its last sample.
        processes = [{**{key: value for key, value in r.items() if key != "live"}, "seen_at_stop": r["live"]}
                     for r in sorted(self.processes.values(), key=lambda r: (r["first_seen_s"], r["pid"]))]
        return {"interval_ms": self.INTERVAL * 1000, "passes": self.passes, "failed_passes": self.failed_passes,
                "hwm_available": bool(processes) and all(r["hwm_bytes"] is not None for r in processes),
                "max_sampled_total_rss_bytes": self.max_total, "worker_starts": worker_starts,
                "unobserved_workers": None if worker_starts is None else max(0, worker_starts - observed),
                "processes": processes}


def logs(session, offset=0):
    # pread does not change the file offset shared with the child writing stderr.
    return os.pread(session.err.fileno(), os.fstat(session.err.fileno()).st_size - offset, offset).decode(errors="replace")


def probes(text):
    return [{"process": role, "analysis_id": int(id), "metric": metric, "value": int(value)}
            for role, id, metric, value in re.findall(r"^BENCH (server|worker) (\d+) (\w+) (-?\d+)$", text, re.M)]


def notify(session, method, params):
    session.send(method, params, request=False)


def open_document(session, path, text, language="virgil"):
    notify(session, "textDocument/didOpen", {"textDocument": {
        "uri": path.as_uri(), "languageId": language, "version": 1, "text": text}})


def location(site):
    return {"uri": (ROOT / site["path"]).as_uri(), "range": site["range"]}


FIRST_SEMANTIC_ORDERS = ("config_first", "source_first")


class Block:
    def __init__(self, exe, project, args, row):
        self.exe, self.p, self.args, self.row = exe, project, args, row
        self.uri = (ROOT / project["query"]["path"]).as_uri()
        self.text = (ROOT / project["query"]["path"]).read_text()
        self.version = 1
        self.query = {"textDocument": {"uri": self.uri}, "position": project["query"]["range"]["start"]}
        self.uris = [(ROOT / f["path"]).as_uri() for f in project["files"]]
        # Keep all measured operations below retirement, including warmups and resets.
        self.options = ["--worker-max-analyses=" + str(50 + 20 * (args.warmups + args.operations))]
        self.s = None
        self.sampler = None
        self.analysis_generation = 0

    def analysis(self, result, diagnostics=None):
        self.snapshot(result, diagnostics)
        check(result["generation"] == self.analysis_generation + 1, result)
        self.analysis_generation = result["generation"]

    def request(self, method, params=None):
        return self.s.result(self.s.send(method, params), timeout=60)

    def snapshot(self, result=None, diagnostics=None):
        if result is None:
            result = self.request("virgil-lsp/snapshot")
        check(result is not None and result["diagnostics"] == (diagnostics or []), result)
        check(result["documentVersions"] == [
            {"uri": uri, "version": self.version if uri == self.uri else None, "current": True}
            for uri in self.uris], result)
        return result

    def definition(self, result):
        check(result == location(self.p["definition"]), ("definition", result))

    def mark(self, scenario, iteration):
        if self.sampler:
            self.sampler.mark("warmup" if iteration < self.args.warmups else scenario)

    def measure(self, scenario, iteration, action, validate):
        self.mark(scenario, iteration)
        offset = os.fstat(self.s.err.fileno()).st_size
        start = time.perf_counter_ns()
        result = action()
        elapsed = (time.perf_counter_ns() - start) / 1e6
        validate(result)  # Do not publish a fast sample for a failed/fallback answer.
        self.row["samples"].append({"scenario": scenario, "iteration": iteration,
                                    "warmup": iteration < self.args.warmups,
                                    "version": self.version, "server_pid": self.s.proc.pid, "latency_ms": elapsed,
                                    "probes": probes(logs(self.s, offset))})

    def change(self, text):
        self.version += 1
        notify(self.s, "textDocument/didChange", {"textDocument": {"uri": self.uri, "version": self.version},
                                                "contentChanges": [{"text": text}]})

    def restore(self):
        self.change(self.text)
        self.definition(self.request("textDocument/definition", self.query))
        self.snapshot()

    def diagnostic(self, kind, extra=""):
        self.change(self.text + self.p["edits"][kind]["append"] + extra)
        deadline = time.monotonic() + 60
        while True:
            msg = self.s.receive(deadline)
            check("id" not in msg, ("unexpected pending request", msg))
            params = msg["params"]
            if params["uri"] == self.uri and params.get("version") == self.version and params["diagnostics"]:
                return params

    def validate_diagnostic(self, kind, result):
        check(result == {"uri": self.uri, "version": self.version,
                         "diagnostics": [self.p["edits"][kind]["expected"]]}, (kind, result))

    def validate_completion(self, result):
        check(isinstance(result, dict) and any(item["label"] == self.p["completion_label"] and
              item.get("kind") != 14 and item.get("detail") == self.p["hover_prefix"]
              for item in result["items"]), ("semantic completion", result))

    def typing_burst(self, iteration):
        """Four edits 20 ms apart, cancelling the preceding completion each time.

        Type the last four letters of the pinned token. An appended unresolved
        name makes the final semantic publication observable (an empty parser
        publication cannot satisfy it). Only overlays change, never corpus files.
        """
        self.restore()
        self.mark("typing_burst", iteration)
        offset = os.fstat(self.s.err.fileno()).st_size
        site = self.p["query"]
        token, begin = site["token"], site["offset"]
        # Corpus offsets count bytes; all selected tokens are ASCII.
        before = self.text.encode()[:begin].decode()
        after = self.text.encode()[begin + len(token):].decode()
        ids, sent = [], []
        burst_start = time.perf_counter_ns()
        for step in range(4):
            target = burst_start + step * 20_000_000
            time.sleep(max(0, (target - time.perf_counter_ns()) / 1e9))
            if ids:
                notify(self.s, "$/cancelRequest", {"id": ids[-1]})
            length = len(token) - 3 + step
            self.change(before + token[:length] + after + self.p["edits"]["semantic"]["append"])
            position = {**site["range"]["start"],
                        "character": site["range"]["start"]["character"] + length}
            sent.append(time.perf_counter_ns())
            ids.append(self.s.send("textDocument/completion", {**self.query, "position": position}))
        replies, diagnostic_ms = {}, None
        deadline = time.monotonic() + 60
        while len(replies) < len(ids) or diagnostic_ms is None:
            msg = self.s.receive(deadline)
            elapsed = (time.perf_counter_ns() - sent[-1]) / 1e6
            if "id" in msg:
                check(msg["id"] in ids, ("unexpected burst reply", msg))
                replies[msg["id"]] = (msg, elapsed)
            elif (msg["params"]["uri"] == self.uri and
                  msg["params"].get("version") == self.version and msg["params"]["diagnostics"]):
                self.validate_diagnostic("semantic", msg["params"])
                diagnostic_ms = elapsed
        for id in ids[:-1]:
            reply = replies[id][0]
            if "error" in reply:
                check(reply["error"]["code"] == -32800, reply)
            else:
                # A small corpus may finish between keystrokes.
                self.validate_completion(reply["result"])
        self.validate_completion(replies[ids[-1]][0].get("result"))
        trace = logs(self.s, offset)
        metrics = probes(trace)
        self.row["samples"].append({"scenario": "typing_burst", "iteration": iteration,
            "warmup": iteration < self.args.warmups, "version": self.version,
            "server_pid": self.s.proc.pid, "latency_ms": replies[ids[-1]][1],
            "diagnostic_latency_ms": diagnostic_ms,
            "worker_replacements": len(re.findall(r"reclaiming analysis worker", trace)),
            "worker_starts": sum(p["metric"] == "worker_start_us" for p in metrics),
            "request_ids": ids, "probes": metrics})

    def validate_rename(self, result):
        check(isinstance(result, dict) and set(result) == {"documentChanges"}, result)
        actual = []
        for change in result["documentChanges"]:
            document = change["textDocument"]
            check(document["version"] == (self.version if document["uri"] == self.uri else None), change)
            for edit in change["edits"]:
                check(edit["newText"] == self.p["rename_to"], edit)
                actual.append({"uri": document["uri"], "range": edit["range"]})
        check(sorted(actual, key=lambda value: json.dumps(value, sort_keys=True)) == sorted(map(location, self.p["references"]), key=lambda value: json.dumps(value, sort_keys=True)), result)

    def initialize(self):
        initialized = self.request("initialize", {"initializationOptions": {"virgilRoot": str(ROOT / "vendor/virgil")},
                     "capabilities": {"workspace": {"workspaceEdit": {"documentChanges": True},
                                     "inlayHint": {"refreshSupport": self.args.inlay_refresh}},
                                      "textDocument": {"hover": {"contentFormat": ["plaintext"]}}}})
        self.has_inlay_hints = initialized['capabilities'].get('inlayHintProvider', False)
        notify(self.s, "initialized", {})

    def session(self):
        self.analysis_generation = 0
        self.command = [str(self.exe), "--stdio", *self.options]
        self.s = (HintsSession if self.args.inlay_refresh else Session)(self.exe, *self.options)
        if self.args.error_result:
            # Per-process memory for the opt-in error workload; the default run is unsampled.
            self.sampler = ResourceSampler(self.s.proc.pid).start()
        self.initialize()
        open_document(self.s, ROOT / self.p["query"]["path"], self.text)

    def stop_sampler(self):
        if not self.sampler:
            return None
        trace = logs(self.s)
        # Normal builds emit no probes, so their completed worker starts are unknown.
        starts = sum(p["process"] == "server" and p["metric"] == "worker_start_us" for p in probes(trace))
        summary, self.sampler = self.sampler.stop(starts if "BENCH " in trace else None), None
        return summary

    def finish_session(self):
        if self.s:
            summary = self.stop_sampler()  # Before shutdown, while the PIDs are still this session's.
            record = {"pid": self.s.proc.pid, "command": self.command,
                      "resources": resources(self.s.proc.pid), "stderr": logs(self.s)}
            if summary:
                record["resource_summary"] = summary
            self.row["processes"].append(record)
            self.s.shutdown()
            self.s.close()
            self.s = None

    def config(self):
        # Unsaved config: the original source files and their directories stay untouched.
        return {"version": 1, "projects": [{"name": self.row["project"], "sources": self.p["patterns"],
                                           "compilerArgs": self.p["compiler_args"]}]}

    def semantic_snapshot_diagnostics(self):
        expected = self.p["edits"]["semantic"]["expected"]
        begin, end = expected["range"]["start"], expected["range"]["end"]
        return [{"path": str(ROOT / self.p["query"]["path"]),
                 "beginLine": begin["line"] + 1, "beginColumn": begin["character"] + 1,
                 "endLine": end["line"] + 1, "endColumn": end["character"] + 1,
                 "kind": expected["code"], "message": expected["message"]}]

    def first_publication(self, launch, deadline):
        """Wait for the exact semantic publication at version 1, recording earlier ones.

        A parser publication, an empty clearing, another version or URI, or a
        different diagnostic never ends the interval; only the deadline does.
        """
        expected, seen = [self.p["edits"]["semantic"]["expected"]], []
        while True:
            msg = self.s.receive(deadline)
            now = time.perf_counter_ns()
            check("id" not in msg, ("unexpected pending request", msg))
            params = msg["params"]
            if params["uri"] == self.uri and params.get("version") == 1 and params["diagnostics"] == expected:
                return now, seen
            seen.append({"uri": params["uri"], "version": params.get("version"),
                         "codes": [d.get("code") for d in params["diagnostics"]], "ms": (now - launch) / 1e6})

    def first_semantic(self, order, iteration):
        """A fresh process pair from launch to the opened edit's exact semantic publication."""
        scenario, warmup = "first_semantic_" + order, iteration < self.args.warmups
        documents = [(ROOT / self.p["base"] / ".virgil-lsp.json", json.dumps(self.config()), "json"),
                     (ROOT / self.p["query"]["path"], self.text + self.p["edits"]["semantic"]["append"], "virgil")]
        if order == "source_first":
            documents.reverse()
        self.version, self.command = 1, [str(self.exe), "--stdio"]
        launch = time.perf_counter_ns()
        self.s = Session(self.exe)
        self.sampler = ResourceSampler(self.s.proc.pid, "warmup" if warmup else scenario).start()
        started = time.perf_counter_ns()
        self.initialize()
        initialized = time.perf_counter_ns()
        opened = {}
        for path, text, language in documents:
            opened[language] = time.perf_counter_ns()
            open_document(self.s, path, text, language)
        published, seen = self.first_publication(launch, time.monotonic() + 60)
        # Untimed semantic barrier: the project snapshot holds exactly that diagnostic.
        self.snapshot(diagnostics=self.semantic_snapshot_diagnostics())
        trace = logs(self.s)
        self.row["samples"].append({"scenario": scenario, "iteration": iteration, "warmup": warmup,
            "version": 1, "server_pid": self.s.proc.pid, "latency_ms": (published - launch) / 1e6,
            "open_latency_ms": (published - opened["virgil"]) / 1e6,
            "config_open_latency_ms": (published - opened["json"]) / 1e6,
            "initialize_ms": (initialized - started) / 1e6,
            "discarded_analyses": len(re.findall(r"^virgil-lsp: discarded analysis ", trace, re.M)),
            "earlier_publications": seen, "probes": probes(trace)})
        self.finish_session()

    def run_first_semantic(self):
        for i in range(self.args.warmups + self.args.operations):
            # Alternate which order runs first so neither always follows the other.
            for order in FIRST_SEMANTIC_ORDERS[::1 if i % 2 == 0 else -1]:
                self.first_semantic(order, i)

    def run(self):
        count = self.args.warmups + self.args.operations
        try:
            if self.args.first_semantic:
                self.run_first_semantic()
                return
            for i in range(count):
                self.session()
                self.measure("fresh_analysis", i, lambda: self.request("virgil-lsp/analyze", {"uris": self.uris}),
                             self.analysis)
                if i != count - 1:
                    self.finish_session()
            for i in range(count):
                self.measure("reused_analysis", i, lambda: self.request("virgil-lsp/analyze", {"uris": self.uris}),
                             self.analysis)
            open_document(self.s, ROOT / self.p["base"] / ".virgil-lsp.json", json.dumps(self.config()), "json")
            self.definition(self.request("textDocument/definition", self.query))
            self.snapshot()
            if self.args.inlay_refresh:
                # Explicit URI lists have no project context and cannot run
                # the refresh comparison. Measure project-scoped transactions
                # after the unsaved configuration has been discovered.
                self.analysis_generation = self.snapshot()["generation"]
                # Discovering the config can add hints to an already open
                # single-file document. Only subsequent unchanged analyses
                # are required to leave the refresh count unchanged.
                refreshes_before = len(self.s.refreshes)
                for i in range(count):
                    self.measure("refresh_analysis", i,
                                 lambda: self.request("virgil-lsp/analyze", {"uri": self.uri}), self.analysis)
                if self.has_inlay_hints:
                    check(len(self.s.refreshes) == refreshes_before, "unchanged project analyses refreshed hints")
            for i in range(count):
                for method, extra, validate in (
                    ("definition", {}, self.definition),
                    ("hover", {}, lambda r: check(r is not None and r["contents"]["kind"] == "plaintext" and
                                                 r["contents"]["value"].startswith(self.p["hover_prefix"]) and
                                                 r["range"] == self.p["query"]["range"], ("hover", r))),
                    ("references", {"context": {"includeDeclaration": True}},
                     lambda r: check(isinstance(r, list) and sorted(r, key=lambda value: json.dumps(value, sort_keys=True)) ==
                                     sorted(map(location, self.p["references"]), key=lambda value: json.dumps(value, sort_keys=True)), ("references", r))),
                    ("prepareRename", {}, lambda r: check(r == {"placeholder": self.p["query"]["token"],
                                                               "range": self.p["query"]["range"]}, r)),
                    ("rename", {"newName": self.p["rename_to"]}, self.validate_rename)):
                    self.snapshot()  # Unmeasured freshness precondition; never a polling loop.
                    self.measure(method, i, lambda: self.request("textDocument/" + method, {**self.query, **extra}), validate)
                comp = {**self.query, "position": self.p["completion_positions"][i % 2]}
                self.snapshot()
                self.measure("completion_miss", i, lambda: self.request("textDocument/completion", comp), self.validate_completion)
                self.measure("completion_hit", i, lambda: self.request("textDocument/completion", comp), self.validate_completion)
            for kind in ("syntax", "semantic"):
                for i in range(count):
                    self.restore()  # A definition reply drains the restore's analysis without sleeps.
                    self.measure(kind + "_diagnostic", i, lambda: self.diagnostic(kind),
                                 lambda r: self.validate_diagnostic(kind, r))
            self.restore()
            if self.args.error_result and self.row["project"] == "aeneas":
                # Reproduce #240's diagnostic-bearing RESULT growth cliff.
                # Generated overlays leave the reviewed corpus pin unchanged.
                extra = "".join(f"def reviewPad{i}(x: int) -> int {{ var y = x + 1; return y; }}\n"
                                for i in range(900))
                self.validate_diagnostic("semantic", self.diagnostic("semantic", extra))
                diagnostics = self.semantic_snapshot_diagnostics()
                self.analysis_generation = self.snapshot(diagnostics=diagnostics)["generation"]
                for i in range(count):
                    self.measure("error_analysis", i,
                                 lambda: self.request("virgil-lsp/analyze", {"uris": self.uris}),
                                 lambda r: self.analysis(r, diagnostics))
            if self.args.typing_burst:
                for i in range(count):
                    self.typing_burst(i)
            self.finish_session()
        except BaseException:
            if self.s:
                self.row["failure"] = {"transcript": self.s.transcript, "stderr": logs(self.s)}
            raise
        finally:
            if self.sampler:
                self.sampler.stop()
                self.sampler = None
            if self.s:
                self.s.close()


def summaries(blocks):
    groups = collections.defaultdict(list)
    for block in blocks:
        for sample in block["samples"]:
            if sample["warmup"]:
                continue
            key = (block["build"], block["project"], sample["scenario"])
            groups[key + ("latency_ms",)].append(sample["latency_ms"])
            for metric in ("diagnostic_latency_ms", "worker_replacements", "worker_starts", "open_latency_ms",
                           "config_open_latency_ms", "initialize_ms", "discarded_analyses"):
                if metric in sample:
                    groups[key + (metric,)].append(sample[metric])
            # Preserve each worker transaction, even if an operation runs several.
            for probe in sample["probes"]:
                groups[key + (probe["process"] + "." + probe["metric"],)].append(probe["value"])
        # One value per observed PID and measured phase; an unknown HWM is left out, not zero.
        for process in block.get("processes", ()):
            for record in process.get("resource_summary", {}).get("processes", []):
                for phase, values in record["phases"].items():
                    if phase in ("session", "warmup"):
                        continue
                    key = (block["build"], block["project"], phase)
                    groups[key + (record["role"] + ".max_rss_bytes",)].append(values["max_rss_bytes"])
                    if values["hwm_bytes"] is not None:
                        groups[key + (record["role"] + ".hwm_bytes",)].append(values["hwm_bytes"])
    return [{"build": b, "project": p, "scenario": s, "metric": m, **summarize(v)}
            for (b, p, s, m), v in sorted(groups.items())]


def write_json(path, result):
    text = json.dumps(result, indent=2) + "\n"
    if path == "-":
        sys.stdout.write(text)
    else:
        Path(path).write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", action="append", type=Path, help="repeat for interleaved A/B process blocks")
    parser.add_argument("--blocks", type=int, default=3)
    parser.add_argument("--operations", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--project", action="append", choices=["small", "util", "aeneas"])
    parser.add_argument("--cpu", default="auto", help="auto pins the first allowed CPU with taskset; none disables")
    parser.add_argument("--output", default="-", help="JSON file, or - for stdout")
    parser.add_argument("--smoke", action="store_true", help="small fixture, one block/operation, no warmups")
    parser.add_argument("--require-probes", action="store_true", help="fail unless all phase/GC metrics are present")
    parser.add_argument("--inlay-refresh", action="store_true", help="advertise and acknowledge inlay-hint refresh requests")
    parser.add_argument("--error-result", action="store_true",
                        help="also repeat Aeneas analyses with an unresolved name and 900 extra functions (#240)")
    parser.add_argument("--typing-burst", action="store_true",
                        help="also type four letters 20 ms apart, cancelling obsolete completions (#262)")
    parser.add_argument("--first-semantic", action="store_true",
                        help="instead time fresh launches to the first semantic diagnostic, in both open orders (#338)")
    args = parser.parse_args()
    if args.first_semantic and (args.error_result or args.typing_burst or args.inlay_refresh):
        parser.error("--first-semantic runs alone; measure the other workloads separately")
    if args.smoke:
        args.blocks, args.operations, args.warmups, args.project = 1, 1, 0, ["small"]
    check(args.blocks > 0 and args.operations > 0 and args.warmups >= 0, "invalid sample counts")
    os.chdir(ROOT)
    pinned, checked_projects = corpus.validate(strict=not args.smoke)
    affinity = pin_cpu(args.cpu)
    executables = [p.resolve() for p in args.exe or [ROOT / "build/virgil-lsp"]]
    result = {"schema": 1, "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "command": [sys.executable, *sys.argv], "server_revision": corpus.git_revision(ROOT, "rev-parse", "HEAD"),
              "git_status": corpus.git_revision(ROOT, "status", "--short"), "host": host_info(affinity),
              "corpus_sha256": corpus.sha256(corpus.MANIFEST.read_bytes()), "corpus": pinned,
              "configuration": {"blocks": args.blocks, "operations": args.operations, "warmups": args.warmups,
                                "error_result": args.error_result, "typing_burst": args.typing_burst,
                                "inlay_refresh": args.inlay_refresh, "first_semantic": args.first_semantic},
              "builds": [binary_info(p, args.smoke) for p in executables], "blocks": []}
    try:
        for block in range(args.blocks):
            order = list(range(len(executables)))
            if block % 2:
                order.reverse()
            for project in args.project or pinned["projects"]:
                for build in order:
                    row = {"block": block, "build": build, "project": project, "samples": [], "processes": []}
                    result["blocks"].append(row)
                    Block(executables[build], pinned["projects"][project], args, row).run()
                    print(f"block {block + 1}, build {build}, {project}: all semantic checks passed", file=sys.stderr)
        expected = {**pinned, "projects": {name: pinned["projects"][name] for name in checked_projects}}
        check(corpus.generate(checked_projects, pinned["virgil_source_revision"]) == expected,
              "benchmark mutated the corpus")
        result["summary"] = summaries(result["blocks"])
        if args.require_probes:
            worker_metrics = {"parse_us", "verify_us", "collect_us", "query_us", "request_decode_us",
                              "result_encode_us", "gc_count", "allocated_bytes", "heap_after_handle_bytes",
                              "live_after_gc_bytes", "force_gc_us", "result_bytes"}
            server_metrics = {"request_encode_us", "result_decode_us", "roundtrip_us", "gc_count", "allocated_bytes"}
            for block in result["blocks"]:
                for sample in block["samples"]:
                    measured = collections.defaultdict(list)
                    for probe in sample["probes"]:
                        measured[probe["process"]].append(probe["metric"])
                    scenario = sample["scenario"]
                    if scenario == "typing_burst":
                        # Cancelled transactions need not finish or emit a full probe set.
                        # The last useful completion and semantic publication must finish.
                        check(measured["server"].count("roundtrip_us") >= 2,
                              (scenario, "missing useful transactions"))
                        check(measured["worker"].count("result_encode_us") >= 2,
                              (scenario, "missing worker results"))
                    elif scenario.startswith("first_semantic_"):
                        # A discarded or single-file analysis may precede the project's.
                        for role, required in (("worker", worker_metrics), ("server", server_metrics)):
                            check(all(metric in measured[role] for metric in required),
                                  ("missing transaction probes", block["build"], block["project"], scenario, role, measured[role]))
                        check("capture_us" in measured["server"] and "worker_start_us" in measured["server"],
                              (scenario, "missing capture or worker start"))
                    elif scenario in ("fresh_analysis", "reused_analysis", "completion_miss", "rename", "semantic_diagnostic", "error_analysis", "refresh_analysis"):
                        for role, required in (("worker", worker_metrics), ("server", server_metrics)):
                            check(all(measured[role].count(metric) == 1 for metric in required),
                                  ("missing/duplicate transaction probes", block["build"], block["project"], scenario, role, measured[role]))
                        capture = "rename_capture_us" if scenario == "rename" else "capture_us"
                        check(measured["server"].count(capture) == 1, (scenario, "missing capture"))
                        started = "worker_start_us" in measured["server"]
                        check(started == (scenario == "fresh_analysis"), (scenario, "unexpected worker replacement/start"))
                    else:
                        check(not measured, (scenario, "unexpected analysis on cached/syntax path", dict(measured)))
        for row in result["summary"]:
            first = row["scenario"].startswith("first_semantic_")
            if row["metric"] == "latency_ms" or first and row["metric"] in ("open_latency_ms", "initialize_ms"):
                metric = f" {row['metric']}" if row["metric"] != "latency_ms" else ""
                tail = f", p50 {row['p50']:.3f}, p95 {row['p95']:.3f}" if first else ""
                print(f"build {row['build']} {row['project']} {row['scenario']}{metric}: "
                      f"median {row['median']:.3f} ms, p10–p90 {row['p10']:.3f}–{row['p90']:.3f}, "
                      f"MAD {row['mad']:.3f}{tail}", file=sys.stderr)
    except BaseException as error:
        result["error"] = repr(error)
        if args.output != "-":
            write_json(args.output + ".failure.json", result)
        raise
    write_json(args.output, result)


if __name__ == "__main__":
    main()
