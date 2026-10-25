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
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "test/protocol"))
from worker import Session

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
            "p90": percentile(values, .9), "min": values[0], "max": values[-1],
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


def resources(pid):
    """Current RSS for the server and its worker; Linux adds each process's HWM."""
    try:
        rows = [list(map(int, row.split())) for row in command("ps", "-axo", "pid=,ppid=,rss=").splitlines()]
        result = []
        for process, parent, rss in rows:
            if process == pid or parent == pid:
                status = optional_file(f"/proc/{process}/status") or ""
                peak = re.search(r"^VmHWM:\s+(\d+) kB$", status, re.M)
                result.append({"role": "server" if process == pid else "worker", "pid": process,
                               "rss_bytes": rss * 1024,
                               "peak_rss_bytes": int(peak[1]) * 1024 if peak else None})
        return result
    except (OSError, subprocess.SubprocessError):
        return None


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

    def measure(self, scenario, iteration, action, validate):
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

    def session(self):
        self.analysis_generation = 0
        self.s = Session(self.exe, *self.options)
        self.request("initialize", {"initializationOptions": {"virgilRoot": str(ROOT / "vendor/virgil")},
                     "capabilities": {"workspace": {"workspaceEdit": {"documentChanges": True}},
                                      "textDocument": {"hover": {"contentFormat": ["plaintext"]}}}})
        notify(self.s, "initialized", {})
        open_document(self.s, ROOT / self.p["query"]["path"], self.text)

    def finish_session(self):
        if self.s:
            self.row["processes"].append({"pid": self.s.proc.pid, "command": [str(self.exe), "--stdio", *self.options],
                                         "resources": resources(self.s.proc.pid), "stderr": logs(self.s)})
            self.s.shutdown()
            self.s.close()
            self.s = None

    def run(self):
        count = self.args.warmups + self.args.operations
        try:
            for i in range(count):
                self.session()
                self.measure("fresh_analysis", i, lambda: self.request("virgil-lsp/analyze", {"uris": self.uris}),
                             self.analysis)
                if i != count - 1:
                    self.finish_session()
            for i in range(count):
                self.measure("reused_analysis", i, lambda: self.request("virgil-lsp/analyze", {"uris": self.uris}),
                             self.analysis)
            # Unsaved config: the original source files and their directories stay untouched.
            config = {"version": 1, "projects": [{"name": self.row["project"], "sources": self.p["patterns"],
                                                 "compilerArgs": self.p["compiler_args"]}]}
            open_document(self.s, ROOT / self.p["base"] / ".virgil-lsp.json", json.dumps(config), "json")
            self.definition(self.request("textDocument/definition", self.query))
            self.snapshot()
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
                expected = self.p["edits"]["semantic"]["expected"]
                begin, end = expected["range"]["start"], expected["range"]["end"]
                diagnostics = [{"path": str(ROOT / self.p["query"]["path"]),
                                "beginLine": begin["line"] + 1, "beginColumn": begin["character"] + 1,
                                "endLine": end["line"] + 1, "endColumn": end["character"] + 1,
                                "kind": expected["code"], "message": expected["message"]}]
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
            for metric in ("diagnostic_latency_ms", "worker_replacements", "worker_starts"):
                if metric in sample:
                    groups[key + (metric,)].append(sample[metric])
            # Preserve each worker transaction, even if an operation runs several.
            for probe in sample["probes"]:
                groups[key + (probe["process"] + "." + probe["metric"],)].append(probe["value"])
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
    parser.add_argument("--error-result", action="store_true",
                        help="also repeat Aeneas analyses with an unresolved name and 900 extra functions (#240)")
    parser.add_argument("--typing-burst", action="store_true",
                        help="also type four letters 20 ms apart, cancelling obsolete completions (#262)")
    args = parser.parse_args()
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
                                "error_result": args.error_result, "typing_burst": args.typing_burst},
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
                    elif scenario in ("fresh_analysis", "reused_analysis", "completion_miss", "rename", "semantic_diagnostic", "error_analysis"):
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
            if row["metric"] == "latency_ms":
                print(f"build {row['build']} {row['project']} {row['scenario']}: "
                      f"median {row['median']:.3f} ms, p10–p90 {row['p10']:.3f}–{row['p90']:.3f}, "
                      f"MAD {row['mad']:.3f}", file=sys.stderr)
    except BaseException as error:
        result["error"] = repr(error)
        if args.output != "-":
            write_json(args.output + ".failure.json", result)
        raise
    write_json(args.output, result)


if __name__ == "__main__":
    main()
