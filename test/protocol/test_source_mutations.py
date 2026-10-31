#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The source-mutation harness's helpers: limits, planning, oracles, and replay.

A scripted stand-in server drives the deadline, EOF, notification-reply, and
ID-type failures without a build. Replay, a contained worker failure, and a
known compiler trap use the built server and the test-only stall worker, and
are skipped if they are not built.
"""
from collections import Counter
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import shape_model  # noqa: E402
import source_mutations as sm  # noqa: E402
from reply_shapes import ReplyValidator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "build/virgil-lsp"
STALL = ROOT / "build/stall/virgil-lsp-worker"
SEED = 20261028
SEEDS, INPUTS = sm.load_seeds()
LIMITS = sm.resolve_limits({})
VALIDATOR = ReplyValidator()
# The capabilities the built server advertises to CLIENT_CAPABILITIES.
CAPABILITIES = {
    "callHierarchyProvider": True, "codeActionProvider": {"codeActionKinds": ["quickfix"]},
    "completionProvider": {"triggerCharacters": ["."]}, "definitionProvider": True,
    "documentHighlightProvider": True, "documentSymbolProvider": True, "foldingRangeProvider": True,
    "hoverProvider": True, "implementationProvider": True, "inlayHintProvider": True,
    "referencesProvider": True, "renameProvider": {"prepareProvider": True}, "selectionRangeProvider": True,
    "semanticTokensProvider": {"full": True, "legend": {"tokenModifiers": [], "tokenTypes": []}},
    "signatureHelpProvider": {"triggerCharacters": ["("]},
    "textDocumentSync": {"change": 1, "openClose": True, "save": {"includeText": False}},
    "typeDefinitionProvider": True, "typeHierarchyProvider": True,
    "workspace": {"workspaceFolders": {"changeNotifications": True, "supported": True}},
    "workspaceSymbolProvider": True,
}

# A stand-in server: it answers initialize and shutdown, answers every other
# request with null, and misbehaves as MODE says at the first feature request.
FAKE = textwrap.dedent("""\
    import json, os, signal, sys, time
    MODE = %r
    CAPABILITIES = %r
    OBSERVER_DECLARATION = %r
    if "--version" in sys.argv:
        print("stand-in " + MODE)
        sys.exit(0)
    def read():
        header = b""
        while not header.endswith(b"\\r\\n\\r\\n"):
            byte = sys.stdin.buffer.read(1)
            if not byte:
                sys.exit(0)
            header += byte
        size = int(header.split(b":")[1])
        return json.loads(sys.stdin.buffer.read(size))
    def write(msg):
        data = json.dumps(msg).encode()
        sys.stdout.buffer.write(b"Content-Length: %%d\\r\\n\\r\\n" %% len(data) + data)
        sys.stdout.buffer.flush()
    while True:
        msg = read()
        method, ident = msg.get("method"), msg.get("id")
        if MODE == "deaf":
            time.sleep(60)
        if method == "exit":
            sys.exit(0)
        if method == "textDocument/didOpen" and MODE == "notification-reply":
            write({"jsonrpc": "2.0", "id": None, "result": None})
        if ident is None:
            continue
        if method == "initialize":
            write({"jsonrpc": "2.0", "id": ident, "result": {"capabilities": CAPABILITIES}})
        elif method == "shutdown":
            write({"jsonrpc": "2.0", "id": ident, "result": None})
        elif MODE == "large-duplicate":
            result = {"contents": {"kind": "plaintext", "value": "\u00e9" * 100000}}
            reply = {"jsonrpc": "2.0", "id": ident, "result": result}
            write(reply)
            write(reply)
        elif MODE.startswith("restoration-") and str(ident).startswith("restored-"):
            if method == "textDocument/hover":
                result = {"contents": {"kind": "plaintext", "value": "stable(x: int) -> int"}}
            elif method == "textDocument/definition":
                result = {"uri": msg["params"]["textDocument"]["uri"], "range": OBSERVER_DECLARATION}
            elif method in ("textDocument/prepareCallHierarchy", "textDocument/prepareTypeHierarchy"):
                calls = method == "textDocument/prepareCallHierarchy"
                oversized = MODE == ("restoration-large-call" if calls else "restoration-large-type")
                result = [{"name": "stable" if calls else "MutationLeaf", "kind": 6 if calls else 5,
                           "uri": msg["params"]["textDocument"]["uri"], "range": OBSERVER_DECLARATION,
                           "selectionRange": OBSERVER_DECLARATION,
                           "data": {"run": MODE, "values": [0] * (9000 if oversized else 7000)}}]
            elif method == "callHierarchy/incomingCalls":
                result = [{"from": {"name": "check"}}]
            elif method == "typeHierarchy/supertypes":
                result = [{"name": "Wrong" if MODE == "restoration-failure" else "MutationBase"}]
            write({"jsonrpc": "2.0", "id": ident, "result": result})
        elif MODE == "hang":
            time.sleep(60)
        elif MODE == "stopped-group":
            import subprocess
            child = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            with open(sys.argv[0] + ".group", "w") as ready:
                json.dump({"server": os.getpid(), "worker": child.pid}, ready)
            os.kill(os.getpid(), signal.SIGSTOP)
            time.sleep(60)
        elif MODE == "eof":
            sys.exit(3)
        elif MODE == "orphan":
            import subprocess
            child = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
            open(sys.argv[0] + ".child", "w").write(str(child.pid))
            sys.exit(3)
        elif MODE == "nested-item" and method == "textDocument/prepareTypeHierarchy":
            data = [0] * 3000
            for depth in range(40):
                data = {"level": depth, "inner": data}
            item = {"name": "T", "kind": 5, "uri": msg["params"]["textDocument"]["uri"], "data": data,
                    "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
                    "selectionRange": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}}
            write({"jsonrpc": "2.0", "id": ident, "result": [item]})
        elif MODE == "id-type":
            write({"jsonrpc": "2.0", "id": str(ident), "result": None})
        elif MODE == "undocumented-error":
            write({"jsonrpc": "2.0", "id": ident, "error": {"code": -32603, "message": "internal"}})
        elif MODE == "bare-error" and method == "textDocument/rename":
            write({"jsonrpc": "2.0", "id": ident, "error": {"code": -32803}})
        else:
            write({"jsonrpc": "2.0", "id": ident, "result": None})
""")


def fake_server(directory, mode):
    path = Path(directory) / ("fake-" + mode)
    path.write_text("#!%s\n%s" % (sys.executable, FAKE % (mode, CAPABILITIES, sm.OBSERVER_DECLARATION)))
    path.chmod(0o755)
    return path


def single_case(text, requests=None, limits=LIMITS, ident="test"):
    """A case that changes Mutant.v3 to {text} and requests its hover."""
    if requests is None:
        requests = [{"id": 2, "method": "textDocument/hover", "params": {
            "textDocument": {"uri": sm.ROOT_TOKEN + "/Mutant.v3"}, "position": {"line": 0, "character": 0}}}]
    steps = [{"mutations": [], "texts": {"Mutant.v3": text}, "requests": requests}]
    return sm.case_record(ident, {"Mutant.v3": "component Mutant {}\n"}, steps, limits)


def floor(cases, seed=sm.DEFAULT_SEED):
    """The smallest --artifact-bytes that a default plan of {cases} accepts.
    Each record holds that value, so its digits change the floor a little."""
    budget, previous = sm.LIMITS["artifact_bytes"][0], None
    while budget != previous:
        limits = sm.resolve_limits({"cases": cases, "artifact_bytes": budget})
        plan = sm.plan_run(seed, limits, False, SEEDS)
        manifests = sum(len(sm.dump(m)) for m in sm.manifests(seed, limits, False, INPUTS, plan))
        previous, budget = budget, manifests + max(sm.failure_reserve(r) for r in plan) + sm.RUN_BYTES
    return budget


def quietly(argv):
    """sm.main's status, keeping its report out of this suite's output."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        return sm.main(argv)


def sent(transcript):
    return [msg for direction, msg in transcript if direction == "send"]


def feature_requests(transcript):
    return [m for m in sent(transcript) if "id" in m and m["method"] not in ("initialize", "shutdown")]


def tree_size(directory):
    return sum(p.stat().st_size for p in Path(directory).rglob("*") if p.is_file())


def alive(pid):
    """Whether {pid} runs: it exists and, where /proc says, is not a zombie."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path("/proc/%d/stat" % pid).read_text().split(")")[-1].split()[0] != "Z"
    except FileNotFoundError:
        return not Path("/proc/self").exists()


def gone(pids, wait=2):
    """{pids} still running after {wait} seconds."""
    deadline = time.monotonic() + wait
    while any(map(alive, pids)) and time.monotonic() < deadline:
        time.sleep(0.02)
    return [pid for pid in pids if alive(pid)]


@contextlib.contextmanager
def without_waitid():
    """os as it is on macOS before Python 3.13."""
    saved = getattr(os, "waitid", None)
    if saved is not None:
        del os.waitid
    try:
        yield
    finally:
        if saved is not None:
            os.waitid = saved


def children(pid):
    return [int(c) for task in Path("/proc/%d/task" % pid).iterdir()
            for c in (task / "children").read_text().split()]


class Limits(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(LIMITS, {"cases": 100, "source_bytes": 32 * 1024, "requests": 24, "request_timeout": 10,
                                  "case_timeout": 30, "run_timeout": 180, "artifact_bytes": 64 * 1024 * 1024})
        stress = sm.resolve_limits({}, stress=True)
        self.assertEqual((stress["cases"], stress["source_bytes"]), (10, 1024 * 1024))
        self.assertEqual({k: stress[k] for k in sm.DEADLINES}, {k: LIMITS[k] for k in sm.DEADLINES})

    def test_hard_caps(self):
        for name, value in (("cases", 1001), ("cases", 0), ("source_bytes", 64 * 1024 + 1), ("requests", 65),
                            ("request_timeout", 31), ("request_timeout", 0), ("case_timeout", 121),
                            ("run_timeout", 901), ("artifact_bytes", 256 * 1024 * 1024 + 1), ("source_bytes", 1024),
                            ("requests", sm.MIN_REQUESTS - 1)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                sm.resolve_limits({name: value})
        for name, value in (("cases", 11), ("source_bytes", 1024 * 1024 + 1)):
            with self.subTest(stress=name), self.assertRaises(ValueError):
                sm.resolve_limits({name: value}, stress=True)
        with self.assertRaises(ValueError):
            sm.resolve_limits({"request_timeout": 20, "case_timeout": 15})
        self.assertEqual(sm.resolve_limits({"source_bytes": 64 * 1024, "cases": 1000})["cases"], 1000)

    def test_command_line_rejects(self):
        with tempfile.TemporaryDirectory() as directory:
            for flags in (["--cases", "1001"], ["--stress", "--cases", "11"], ["--run-timeout", "1000"]):
                result = subprocess.run([sys.executable, str(Path(sm.__file__)), str(EXE), "--artifacts", directory,
                                         "--plan-only", *flags], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 2, (flags, result.stderr))
                self.assertIn("at most", result.stderr)


class Planning(unittest.TestCase):
    def manifest_bytes(self, seed, limits=LIMITS, stress=False):
        plan = sm.plan_run(seed, limits, stress, SEEDS)
        return [json.dumps(m, sort_keys=True) for m in sm.manifests(seed, limits, stress, INPUTS, plan)], plan

    def test_manifests_are_deterministic(self):
        first, plan = self.manifest_bytes(SEED)
        self.assertEqual(first, self.manifest_bytes(SEED)[0])
        self.assertNotEqual(first, self.manifest_bytes(SEED + 1)[0])
        # String hashing must not reach the plan.
        script = ("import json, sys; sys.path.insert(0, %r); import source_mutations as sm; "
                  "s, i = sm.load_seeds(); l = sm.resolve_limits({}); p = sm.plan_run(%d, l, False, s); "
                  "print(json.dumps(sm.manifests(%d, l, False, i, p), sort_keys=True))") % (
                      str(Path(sm.__file__).parent), SEED, SEED)
        outputs = {subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True,
                                  env={**os.environ, "PYTHONHASHSEED": h}).stdout for h in ("1", "2")}
        self.assertEqual(len(outputs), 1)
        # A shorter run plans the same first cases.
        short = sm.plan_run(SEED, sm.resolve_limits({"cases": 10}), False, SEEDS)
        self.assertEqual([c["steps"] for c in short], [c["steps"] for c in plan[:10]])

    def test_plan_bounds_and_coverage(self):
        plan = sm.plan_run(SEED, sm.resolve_limits({"cases": 300}), False, SEEDS)
        families = {m["family"] for c in plan for s in c["steps"] for m in s["mutations"]}
        self.assertEqual(families, set(sm.FAMILIES + sm.STRESS_FAMILIES))
        counts = {}
        for case in plan:
            requests = [r for s in case["steps"] for r in s["requests"]]
            # Planned requests, barriers, and restoration leave room for follow-ups.
            self.assertEqual(sm.fixed_requests(case), len(requests) + len(case["steps"]) + 1 + 6)
            self.assertLessEqual(sm.fixed_requests(case) + sm.FOLLOW_UP_RESERVE, LIMITS["requests"])
            self.assertEqual(len({(type(r["id"]), r["id"]) for r in requests}), len(requests))
            self.assertLessEqual({type(r["id"]) for r in requests}, {int, str})
            for r in requests:
                counts[r["method"]] = counts.get(r["method"], 0) + 1
            texts = dict(case["files"])
            for step in case["steps"]:
                texts.update(step["texts"])
                self.assertLessEqual(sm.measure(list(texts.values()), False), LIMITS["source_bytes"])
                self.assertEqual(step["bytes"], sm.measure(list(texts.values()), False))
        self.assertEqual(set(counts), set(sm.MAIN_METHODS))
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1, counts)

    def test_stress_plan(self):
        plan = sm.plan_run(SEED, sm.resolve_limits({}, stress=True), True, SEEDS)
        self.assertEqual(len(plan), 10)
        for case in plan:
            self.assertIn(case["steps"][0]["mutations"][0]["family"], sm.STRESS_FAMILIES)
            self.assertTrue(all(s["bytes"] <= 1024 * 1024 for s in case["steps"]))

    def test_positions(self):
        self.assertEqual(sm.position("a\r\nb\rc\nd", 7), {"line": 3, "character": 0})
        self.assertEqual(sm.position("a\r\nb", 2), {"line": 0, "character": 1})
        self.assertEqual(sm.position("\U0001F600x", 1), {"line": 0, "character": 2})
        self.assertEqual(sm.templated({"uri": "file:///w/A.v3", "x": ["file:///w"]}, "file:///w"),
                         {"uri": "${ROOT}/A.v3", "x": ["${ROOT}"]})


class Records(unittest.TestCase):
    def test_replay_bounds(self):
        record = single_case("component Mutant {}\n")
        sm.check_record(record)
        large = json.loads(json.dumps(record))
        large["steps"][0]["texts"]["Mutant.v3"] = "x" * (2 * 1024 * 1024)
        many = json.loads(json.dumps(record))
        many["steps"][0]["requests"] = [dict(record["steps"][0]["requests"][0], id=n) for n in range(2, 402)]
        stress = json.loads(json.dumps(large), object_hook=None)
        stress["limits"]["source_bytes"] = 4 * 1024 * 1024
        unknown = json.loads(json.dumps(record))
        unknown["steps"][0]["texts"]["Other.v3"] = ""
        extra = json.loads(json.dumps(record))
        extra["restore_followups"] = [{"id": "extra", "method": "callHierarchy/outgoingCalls", "params": {}}]
        for name, bad in (("large", large), ("many", many), ("stress cap", stress), ("unknown file", unknown),
                          ("extra restoration request", extra)):
            with self.subTest(name), self.assertRaises(ValueError):
                sm.check_record(bad)
        # A stress record may use the stress bound.
        stress["stress"], stress["limits"]["source_bytes"] = True, 1024 * 1024
        stress["limits"]["cases"] = 10
        stress["steps"][0]["texts"]["Mutant.v3"] = "x" * (512 * 1024)
        sm.check_record(stress)
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / "case.json"
            for bad in (large, many):
                saved.write_text(json.dumps(bad))
                self.assertEqual(quietly([str(EXE), "--replay", str(saved), "--artifacts", directory]), 2)


class Coverage(unittest.TestCase):
    def test_manifest_matches(self):
        self.assertEqual(sm.coverage_drift(CAPABILITIES), [])
        self.assertLessEqual(set(sm.MANIFEST), set(shape_model.REQUESTS))

    def test_drift(self):
        cases = {
            "monikerProvider": True,
            "completionProvider": {"resolveProvider": True},
            "renameProvider": True,
            "semanticTokensProvider": {"full": {"delta": True}},
            "workspace": {"fileOperations": {}},
            "typeHierarchyProvider": None,
        }
        for name, value in cases.items():
            with self.subTest(name):
                self.assertTrue(sm.coverage_drift({**CAPABILITIES, name: value}))


class WorkerDeaths(unittest.TestCase):
    TRACE = ("!NullCheckException\n"
             "\tin MethodEnv.lookupInfix() [vendor/virgil/aeneas/src/vst/MethodEnv.v3 @ 719:45]\n"
             "\tin TypeChecker.visitBinOp() [vendor/virgil/aeneas/src/vst/Verifier.v3 @ 2271:47]\n\n"
             "virgil-lsp: warning: analysis worker 7 crashed during an analysis; it ended with exit status 255; "
             "snapshot 3 is kept\n")

    OVERFLOW = ("!StackOverflow\n\nvirgil-lsp: warning: analysis worker 8 crashed during an analysis; "
                "it ended with exit status 255\n")

    def known(self, log, *texts):
        return [d["known"] for d in sm.worker_deaths(log.encode(), [(None, list(texts))])]

    def test_known_signature_needs_its_source(self):
        deaths = sm.worker_deaths(self.TRACE.encode(), [(None, ["def f() -> int { return 0i + 1; }"])])
        self.assertEqual([(d["pid"], d["trap"], d["known"]) for d in deaths], [(7, "!NullCheckException", "crash-path row 1")])
        self.assertEqual(self.known(self.TRACE, "def f() -> int { return 0i32 + 1; }"), [None])
        self.assertEqual(self.known(self.OVERFLOW, "def f() { g(); }"), [None])
        self.assertEqual(self.known(self.OVERFLOW, "def f() { a" + ".b" * 1000 + "; }"), ["crash-path row 25"])
        timeout = ("virgil-lsp: warning: analysis worker 9 exceeded the analysis time limit of 5000 ms and was "
                   "killed; it ended with signal 9\n")
        self.assertEqual(self.known(timeout, "0i"), [None])

    def test_triggers_count_only_code(self):
        deep = "(" * 1000 + "1" + ")" * 1000
        self.assertEqual(self.known(self.OVERFLOW, "def f() { var x = %s; }" % deep), ["crash-path row 13"])
        self.assertEqual(self.known(self.OVERFLOW, "/* %s */ def f() {}" % ("(" * 1000)), [None])
        self.assertEqual(self.known(self.OVERFLOW, "// %s\ndef f() {}" % ("(" * 1000)), [None])
        self.assertEqual(self.known(self.OVERFLOW, 'def s = "%s";' % ("(" * 1000)), [None])
        self.assertEqual(self.known(self.TRACE, 'def s = "0i"; // 0i'), [None])

    def test_each_death_matches_its_own_source_state(self):
        # A death logged after the first boundary had the second state's text only.
        log = ("virgil-lsp: info\n" + self.TRACE).encode()
        windows = [(len(b"virgil-lsp: info\n"), ["def f() -> int { return 0i + 1; }"]),
                   (None, ["def f() -> int { return 1 + 1; }"])]
        self.assertEqual([d["known"] for d in sm.worker_deaths(log, windows)], [None])
        self.assertEqual([d["known"] for d in sm.worker_deaths(log, windows[::-1][:1] + [(None, [])])], [None])
        self.assertEqual([d["known"] for d in sm.worker_deaths(log, [(0, ["1 + 1"]), (None, ["0i + b"])])],
                         ["crash-path row 1"])

    def test_error_objects(self):
        self.assertIsNone(sm.error_problem({"code": -32803, "message": "no"}))
        self.assertIsNone(sm.error_problem({"code": -32803, "message": "no", "data": [None]}))
        for error in ({"code": -32803}, {"code": -32803, "message": 1}, {"code": "x", "message": "no"},
                      {"code": True, "message": "no"}, {"code": 1.5, "message": "no"}, [],
                      {"code": -32803, "message": "no", "extra": 1}):
            with self.subTest(error=error):
                self.assertTrue(sm.error_problem(error))

    def test_documented_message(self):
        message = ("Semantic diagnostics for project mutations could not be updated: the analysis worker crashed "
                   "(exit status 255). The last published diagnostics are kept; see the server log.")
        self.assertTrue(sm.WORKER_FAILURE_MESSAGE.fullmatch(message))
        self.assertFalse(sm.WORKER_FAILURE_MESSAGE.fullmatch(message.replace("crashed", "exploded")))


class StandIn(unittest.TestCase):
    """Oracle failures from a scripted server; no build needed."""

    def run_fake(self, mode, limits=None, record=None):
        limits = limits or sm.resolve_limits({"request_timeout": 1, "case_timeout": 5, "run_timeout": 20})
        record = record or single_case("component Mutant { def f() {} }\n", limits=limits)
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            result, transcript, _, _ = sm.run_case(record, fake_server(directory, mode),
                                                   started + limits["run_timeout"], Path(directory), VALIDATOR)
            return result, transcript, time.monotonic() - started

    @unittest.skipUnless(shutil.which("ps"), "needs ps to check owned process groups")
    def test_cancellation_cleans_a_stopped_server_group(self):
        def members(group):
            listing = subprocess.run(["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True, check=True)
            return {int(pid): state for pid, pgid, state in (line.split() for line in listing.stdout.splitlines())
                    if int(pgid) == group and not state.startswith("Z")}

        for entry in ("harness", "helpers", "direct", "closing"):
            for signum in (signal.SIGTERM, signal.SIGHUP):
                with self.subTest(entry=entry, signum=signum), tempfile.TemporaryDirectory() as directory:
                    exe = fake_server(directory, "stopped-group")
                    ready = Path(str(exe) + ".group")
                    script = textwrap.dedent("""\
                        import runpy, sys, time, unittest
                        sys.path.insert(0, %r)
                        import source_mutations as sm
                        from reply_shapes import ReplyValidator
                        mode = %r
                        class Probe(unittest.TestCase):
                            def runTest(self):
                                limits = sm.resolve_limits({"request_timeout": 30, "case_timeout": 60, "run_timeout": 60})
                                now = time.monotonic()
                                session = sm.MutationSession(%r, sm.Deadlines(now + 60, now + 60, limits), ReplyValidator())
                                try:
                                    session.call("initialize", {}, 1)
                                    key, deadline = session.request("textDocument/hover", {}, 2)
                                    if mode != "closing":
                                        session.collect({key: deadline})
                                finally:
                                    session.close()
                        if mode in ("direct", "closing"):
                            with sm.cancellation():
                                Probe().runTest()
                        else:
                            unittest.main = lambda: unittest.TextTestRunner().run(unittest.TestSuite([Probe()]))
                            runpy.run_path(%r, run_name="__main__")
                        """) % (str(Path(sm.__file__).parent), entry, str(exe), str(Path(__file__)))
                    command = ([sys.executable, str(Path(sm.__file__)), str(exe), "--cases", "1", "--artifacts", directory]
                               if entry == "harness" else [sys.executable, "-c", script])
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    group = None
                    try:
                        deadline = time.monotonic() + 8
                        owned = {}
                        while time.monotonic() < deadline and process.poll() is None:
                            try:
                                owned = json.loads(ready.read_text())
                            except (FileNotFoundError, json.JSONDecodeError):
                                time.sleep(0.02)
                                continue
                            group = owned["server"]
                            live = members(group)
                            if "T" in live.get(group, "") and len(live) >= 3:
                                break
                            time.sleep(0.02)
                        else:
                            self.fail("the stand-in server group did not stop before cancellation")
                        self.assertIn(owned["worker"], live)
                        process.send_signal(signum)
                        time.sleep(0.1)
                        if process.poll() is None:
                            process.send_signal(signal.SIGHUP if signum == signal.SIGTERM else signal.SIGTERM)
                        stdout, stderr = process.communicate(timeout=8)
                        self.assertEqual(process.returncode, 128 + signum, (stdout, stderr))
                        deadline = time.monotonic() + 2
                        while members(group) and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertEqual(members(group), {})
                    finally:
                        if process.poll() is None:
                            process.send_signal(signal.SIGTERM)
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.communicate()
                        if group is not None:
                            try:
                                os.killpg(group, signal.SIGKILL)
                            except ProcessLookupError:
                                pass

    def test_cancellation_restores_handlers_and_preserves_sigint(self):
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
        with self.assertRaisesRegex(RuntimeError, "probe"):
            with sm.cancellation():
                self.assertEqual(signal.getsignal(signal.SIGINT), before[signal.SIGINT])
                raise RuntimeError("probe")
        self.assertEqual({sig: signal.getsignal(sig) for sig in before}, before)

    def test_request_deadline(self):
        result, transcript, elapsed = self.run_fake("hang")
        self.assertEqual(result["outcome"], "deadline", result)
        self.assertIn("request deadline", result["error"])
        self.assertLess(elapsed, 4)
        self.assertEqual(result["requests"], dict(Counter(m["method"] for m in feature_requests(transcript))))

    def test_run_deadline_during_a_read(self):
        limits = sm.resolve_limits({"request_timeout": 1, "case_timeout": 1, "run_timeout": 1})
        result, _, elapsed = self.run_fake("hang", limits, single_case("component M {}\n", limits=limits))
        self.assertEqual(result["outcome"], "run-deadline", result)
        self.assertLess(elapsed, 3)

    def test_end_of_output(self):
        result, _, _ = self.run_fake("eof")
        self.assertEqual(result["outcome"], "server-failure", result)
        self.assertIn("closed its output", result["error"])

    def test_reply_to_a_notification(self):
        result, _, _ = self.run_fake("notification-reply")
        self.assertEqual(result["outcome"], "protocol-failure", result)
        self.assertIn("reply to a notification", result["error"])

    def test_changed_id_type(self):
        result, _, _ = self.run_fake("id-type")
        self.assertEqual(result["outcome"], "protocol-failure", result)
        self.assertIn('"id": "2"', result["error"])

    def test_end_of_output_leaves_no_child(self):
        # The server reaped on end of output still has its group ended.
        with tempfile.TemporaryDirectory() as directory, without_waitid():
            exe = fake_server(directory, "orphan")
            limits = sm.resolve_limits({"request_timeout": 1, "case_timeout": 5, "run_timeout": 20})
            result, _, _, _ = sm.run_case(single_case("component Mutant {}\n", limits=limits), exe,
                                          time.monotonic() + 20, Path(directory), VALIDATOR)
            self.assertEqual(result["outcome"], "server-failure", result)
            child = int(Path(str(exe) + ".child").read_text())
            self.assertEqual(gone([child]), [])

    def test_failure_with_a_nested_item_keeps_its_record(self):
        # Indented, this item's record outgrew the reserve, and saving the
        # failure removed the record saved before the case.
        prepare = [{"id": 2, "method": "textDocument/prepareTypeHierarchy", "params": {
            "textDocument": {"uri": sm.ROOT_TOKEN + "/Mutant.v3"}, "position": {"line": 0, "character": 0}}}]
        limits = sm.resolve_limits({"request_timeout": 1, "case_timeout": 5, "run_timeout": 20, "requests": 15})
        record = single_case("component Mutant {}\n", prepare, limits=limits)
        reserve = sm.failure_reserve(record)
        budget = reserve + sm.RUN_BYTES
        record["limits"]["artifact_bytes"] = budget
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            artifacts = sm.Artifacts(out, budget, reserve)
            self.assertTrue(artifacts.whole("cases/test/case.json", sm.dump(record), "failure"))
            result, transcript, stamps, log = sm.run_case(record, fake_server(directory, "nested-item"),
                                                          time.monotonic() + 20, Path(directory), VALIDATOR)
            self.assertEqual(result["outcome"], "recovery-failure", result)
            self.assertEqual(len(record["steps"][0]["followups"]), 2)
            sm.save_case(artifacts, record, result, transcript, stamps, log)
            sm.write_run(artifacts, {"result": result})
            saved = json.loads((out / "cases/test/case.json").read_text())
            self.assertEqual(saved["steps"][0]["followups"], record["steps"][0]["followups"])
            self.assertTrue((out / "run.json").exists())
            self.assertLessEqual(tree_size(out), budget)

    def test_error_without_a_message(self):
        rename = [{"id": 2, "method": "textDocument/rename", "params": {
            "textDocument": {"uri": sm.ROOT_TOKEN + "/Mutant.v3"}, "position": {"line": 0, "character": 0},
            "newName": "x"}}]
        result, _, _ = self.run_fake("bare-error", record=single_case("component Mutant {}\n", rename))
        self.assertEqual(result["outcome"], "protocol-failure", result)
        self.assertIn("malformed error for textDocument/rename: its message is not a string", result["error"])

    def test_writes_honor_request_deadlines(self):
        limits = sm.resolve_limits({"request_timeout": 0.2, "case_timeout": 5, "run_timeout": 10})
        write = os.write

        def bounded_write(fd, data):
            self.assertLessEqual(len(data), 512)
            return write(fd, data)

        for request in (False, True):
            with self.subTest(request=request), tempfile.TemporaryDirectory() as directory:
                now = time.monotonic()
                s = sm.MutationSession(fake_server(directory, "deaf"), sm.Deadlines(now + 10, now + 5, limits),
                                       VALIDATOR)
                try:
                    with mock.patch.object(sm.select, "PIPE_BUF", 512), mock.patch.object(sm.os, "write", bounded_write):
                        s.request("textDocument/hover", {}, 2)
                        started = time.monotonic()
                        with self.assertRaises(sm.CaseFailure) as caught:
                            params = {"text": "x" * (1024 * 1024)}
                            if request:
                                s.request("textDocument/hover", params, 3)
                            else:
                                s.send("textDocument/didChange", params, request=False)
                        self.assertIn("request deadline", str(caught.exception))
                        self.assertLess(time.monotonic() - started, 1)
                finally:
                    s.close()

    def test_restoration_followups_are_saved_and_replayed(self):
        limits = sm.resolve_limits({"requests": 15})
        requests = [dict(single_case("")["steps"][0]["requests"][0], id=n) for n in range(2, 9)]
        record = single_case("component Mutant {}\n", requests, limits=limits)
        self.assertEqual(sm.fixed_requests(record), limits["requests"])
        reserve = sm.failure_reserve(record)
        budget = reserve + sm.RUN_BYTES
        record["limits"]["artifact_bytes"] = budget
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            artifacts = sm.Artifacts(work / "out", budget, reserve)
            self.assertTrue(artifacts.whole("cases/test/case.json", sm.dump(record), "failure"))
            result, live, stamps, log = sm.run_case(record, fake_server(directory, "restoration-failure"),
                                                   time.monotonic() + 30, work, VALIDATOR)
            self.assertEqual(result["outcome"], "recovery-failure", result)
            saved = sm.save_case(artifacts, record, result, live, stamps, log)
            sm.write_run(artifacts, {"result": result})
            persisted = json.loads(saved.read_text())
            followups = persisted["restore_followups"]
            self.assertEqual([r["method"] for r in followups],
                             ["callHierarchy/incomingCalls", "typeHierarchy/supertypes"])
            self.assertTrue(json.loads(saved.with_name("result.json").read_text())["record_saved"])
            self.assertLessEqual(tree_size(work / "out"), budget)
            sm.check_record(persisted)
            exe = fake_server(directory, "restoration-changed")
            replay_result, replay_transcript, _, _ = sm.run_case(persisted, exe, time.monotonic() + 30, work,
                                                                VALIDATOR, replay=True)
            self.assertEqual(replay_result["outcome"], "pass", replay_result)
            self.assertEqual(quietly([str(exe), "--replay", str(saved), "--artifacts", directory]), 0)
            for messages in (sent(live), sent(replay_transcript)):
                root = messages[0]["params"]["rootUri"]
                actual = [sm.templated(m, root) for m in messages if m.get("id") in
                          ("restored-incoming", "restored-supertypes")]
                self.assertEqual([sm.dump(m["params"]) for m in actual], [sm.dump(r["params"]) for r in followups])
            self.assertLessEqual(tree_size(work / "replay-test"), budget)

    def test_restoration_items_are_bounded(self):
        for mode, count in (("restoration-large-call", 0), ("restoration-large-type", 1)):
            with self.subTest(mode=mode):
                record = single_case("component Mutant {}\n")
                result, _, _ = self.run_fake(mode, record=record)
                self.assertEqual(result["outcome"], "recovery-failure", result)
                self.assertIn("restoration item larger than", result["error"])
                self.assertEqual(len(record["restore_followups"]), count)

    def test_undocumented_error(self):
        result, _, _ = self.run_fake("undocumented-error")
        self.assertEqual(result["outcome"], "protocol-failure", result)
        self.assertIn("undocumented error for textDocument/hover", result["error"])

    def test_unsent_restoration_followups_are_never_exercised(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = fake_server(directory, "null")
            self.assertEqual(quietly([str(exe), "--cases", "1", "--artifacts", directory]), 1)
            out = Path(directory) / ("seed-%d" % sm.DEFAULT_SEED)
            run = json.loads((out / "run.json").read_text())
            case = out / "cases" / run["failures"][0]["case"]
            result = json.loads((case / "result.json").read_text())
            self.assertEqual(result["outcome"], "recovery-failure")
            self.assertIn("no clean-source hover after restoration", result["error"])
            transcript = [(e["dir"], e["msg"]) for e in map(json.loads,
                          (case / "transcript.jsonl").read_text().splitlines())]
            actual = dict(Counter(m["method"] for m in feature_requests(transcript)))
            self.assertEqual(result["requests"], actual)
            self.assertEqual(run["requests"], actual)
            self.assertLessEqual(sum(actual.values()), LIMITS["requests"])
            for method in ("callHierarchy/incomingCalls", "typeHierarchy/supertypes"):
                self.assertNotIn(method, actual)
                self.assertIn(method, run["never_exercised"])
            ids = {m["id"] for m in feature_requests(transcript)}
            self.assertTrue({"restored-hover", "restored-definition", "restored-calls", "restored-types"} <= ids)

    def test_run_deadline_saves_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = fake_server(directory, "hang")
            budget = floor(3) + 1024
            status = quietly([str(exe), "--cases", "3", "--request-timeout", "1", "--case-timeout", "1",
                              "--run-timeout", "1", "--artifacts", directory, "--artifact-bytes", str(budget)])
            self.assertEqual(status, 1)
            out = Path(directory) / ("seed-%d" % sm.DEFAULT_SEED)
            self.assertLessEqual(tree_size(out), budget)
            run = json.loads((out / "run.json").read_text())
            self.assertTrue(run["incomplete"])
            self.assertEqual(run["failures"][0]["outcome"], "run-deadline")
            case = out / "cases" / run["failures"][0]["case"]
            self.assertTrue(sm.check_record(json.loads((case / "case.json").read_text())) is None)
            self.assertTrue((case / "transcript.jsonl").exists())
            self.assertEqual(json.loads((out / "requests.json").read_text())["seed"], sm.DEFAULT_SEED)

    def test_artifact_budget_marks_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = sm.RUN_BYTES + 20000 + 10000
            artifacts = sm.Artifacts(Path(directory), budget, 20000)
            self.assertTrue(artifacts.cut("small.txt", b"x" * 100, "passing"))
            self.assertFalse(artifacts.cut("large.txt", b"y" * 20000, "passing"))
            self.assertIn(b"[truncated: ", (Path(directory) / "large.txt").read_bytes())
            # A passing record that doesn't fit is left out; a failing one uses the reserve.
            self.assertFalse(artifacts.whole("passing.json", b"z" * 5000, "passing"))
            self.assertFalse((Path(directory) / "passing.json").exists())
            self.assertTrue(artifacts.whole("failing.json", b"z" * 15000, "failure"))
            self.assertEqual((artifacts.truncated, artifacts.omitted), (["large.txt"], ["passing.json"]))
            self.assertLessEqual(tree_size(directory), budget)

    def test_summary_diagnostics_respect_utf8_boundaries(self):
        for text in ("short", "\U0001F600" * 512, "\U0001F600" * 513, "\"\n\u00e9" * 10000):
            with self.subTest(size=len(text.encode())):
                value = sm.bounded({"error": text, "failures": [{"error": text}],
                                    "result": {"messages": [text], "worker_deaths": [{"what": text}]}})
                diagnostics = [value["error"], value["failures"][0]["error"],
                               value["result"]["messages"][0], value["result"]["worker_deaths"][0]["what"]]
                for diagnostic in diagnostics:
                    self.assertLessEqual(len(diagnostic.encode()), 2048)
                    if len(text.encode()) <= 2048:
                        self.assertEqual(diagnostic, text)
                    else:
                        prefix, marker = diagnostic.rsplit(" ... ", 1)
                        self.assertEqual(marker, "[truncated, %d bytes total]" % len(text.encode()))
                        self.assertTrue(text.startswith(prefix))
                self.assertEqual(sm.bounded(value), value)

    def test_large_duplicate_reply_keeps_failure_summaries(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = fake_server(directory, "large-duplicate")
            budget = floor(1)
            self.assertEqual(quietly([str(exe), "--cases", "1", "--artifacts", directory,
                                      "--artifact-bytes", str(budget)]), 1)
            out = Path(directory) / ("seed-%d" % sm.DEFAULT_SEED)
            run = json.loads((out / "run.json").read_text())
            self.assertEqual(len(run["failures"]), 1)
            case = out / "cases" / run["failures"][0]["case"]
            result_path = case / "result.json"
            result = json.loads(result_path.read_text())
            self.assertEqual(result["outcome"], "protocol-failure", result)
            self.assertIn("duplicate response", result["error"])
            self.assertIn("[truncated, ", result["error"])
            self.assertLessEqual(len(result["error"].encode()), 2048)
            self.assertEqual(run["failures"][0]["error"], result["error"])
            self.assertLessEqual(result_path.stat().st_size, sm.RESULT_BYTES)
            self.assertLessEqual((out / "run.json").stat().st_size, sm.RUN_BYTES)
            self.assertLessEqual(tree_size(out), budget)
            saved = case / "case.json"
            self.assertEqual(quietly([str(exe), "--replay", str(saved), "--artifacts", directory]), 1)
            replay = Path(directory) / ("replay-" + run["failures"][0]["case"])
            replay_run = json.loads((replay / "run.json").read_text())
            self.assertEqual(replay_run["result"]["error"], result["error"])
            self.assertLessEqual((replay / "run.json").stat().st_size, sm.RUN_BYTES)
            self.assertLessEqual(tree_size(replay), budget)

    def test_harness_traceback_diagnostic_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(sm, "drive", side_effect=RuntimeError("\U0001F600" * 100000)):
                result, _, _, _ = sm.run_case(single_case("component Mutant {}\n"), fake_server(directory, "null"),
                                               time.monotonic() + 30, Path(directory), VALIDATOR)
            self.assertEqual(result["outcome"], "harness-error", result)
            self.assertIn("Traceback", result["error"])
            self.assertIn("[truncated, ", result["error"])
            self.assertLessEqual(len(result["error"].encode()), 2048)

    def test_transcript_marker_respects_available_bytes(self):
        transcript = [("send", {"message": "\u00e9" * 20}), ("recv", {"result": None})]
        stamps = [0, 1]
        marker = sm.dump({"truncated": True, "omitted_messages": 2, "reason": "artifact budget"})
        complete = b"".join(sm.dump({"t": t, "dir": d, "msg": m}) for (d, m), t in zip(transcript, stamps))
        for room in (0, len(marker) - 1, len(marker), len(complete)):
            with self.subTest(room=room), tempfile.TemporaryDirectory() as directory:
                artifacts = sm.Artifacts(Path(directory), sm.RUN_BYTES + 1000 + room, 1000)
                written = artifacts.transcript("transcript.jsonl", transcript, stamps, "passing")
                path = Path(directory) / "transcript.jsonl"
                if room < len(marker):
                    self.assertFalse(written)
                    self.assertFalse(path.exists())
                    self.assertIn("transcript.jsonl", artifacts.omitted)
                elif room == len(marker):
                    self.assertFalse(written)
                    self.assertEqual(path.read_bytes(), marker)
                else:
                    self.assertTrue(written)
                    self.assertEqual(path.read_bytes(), complete)
                self.assertLessEqual(tree_size(directory), room)

    def test_passing_cases_preserve_the_next_failure_reserve(self):
        limits = sm.resolve_limits({"requests": 15})
        requests = [dict(single_case("")["steps"][0]["requests"][0], id=n) for n in range(2, 9)]
        record = single_case("component Mutant {}\n", requests, limits=limits, ident="failure")
        reserve = sm.failure_reserve(record)
        budget = sm.RUN_BYTES + reserve + 1024
        transcript, stamps = [("send", {"payload": "x" * 1024})], [0]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            out = work / "out"
            artifacts = sm.Artifacts(out, budget, reserve)
            self.assertTrue(artifacts.whole("occupied.bin", b"x" * 1024, "passing"))
            for n in range(999):
                passing = dict(record, id="passing-%d" % n)
                artifacts.whole("cases/%s/case.json" % passing["id"], sm.dump(passing), "failure")
                sm.save_case(artifacts, passing, {"outcome": "pass"}, transcript, stamps, "")
            passing_bytes = artifacts.used
            self.assertTrue(artifacts.whole("cases/failure/case.json", sm.dump(record), "failure"))
            result, transcript, stamps, log = sm.run_case(record, fake_server(directory, "restoration-failure"),
                                                          time.monotonic() + 30, work, VALIDATOR)
            self.assertEqual(result["outcome"], "recovery-failure", result)
            self.assertEqual(len(record["restore_followups"]), 2)
            path = sm.save_case(artifacts, record, result, transcript, stamps, log)
            self.assertEqual(json.loads(path.read_text()), record)
            self.assertTrue(json.loads(path.with_name("result.json").read_text())["record_saved"])
            sm.write_run(artifacts, {"outcomes": {"pass": 999, "recovery-failure": 1}})
            run = json.loads((out / "run.json").read_text())
            self.assertIn("cases/passing-0/transcript.jsonl", run["artifacts_omitted"])
            self.assertLessEqual(passing_bytes, budget - reserve - sm.RUN_BYTES)
            self.assertLessEqual(tree_size(out), budget)

    def test_budget_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            for flags in (["--artifact-bytes", "4096"], ["--artifact-bytes", str(floor(100, seed=5) - 1)]):
                result = subprocess.run([sys.executable, str(Path(sm.__file__)), str(EXE), "--seed", "5", "--cases", "100",
                                         "--plan-only", "--artifacts", directory, *flags],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("--artifact-bytes must be at least", result.stderr)
            budget = floor(100, seed=5)
            self.assertEqual(quietly([str(EXE), "--seed", "5", "--cases", "100", "--plan-only", "--artifacts", directory,
                                      "--artifact-bytes", str(budget)]), 0)
            self.assertLessEqual(tree_size(Path(directory) / "seed-5"), budget)


@unittest.skipUnless(EXE.exists() and EXE.with_name("virgil-lsp-worker").exists(), "build/virgil-lsp is not built")
class RealServer(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="source-mutations-test-"))

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def run_record(self, record, exe=EXE, **options):
        return sm.run_case(record, exe, time.monotonic() + 60, self.directory, VALIDATOR, **options)

    def test_replay_from_a_saved_case(self):
        plan = sm.plan_run(SEED, sm.resolve_limits({"cases": 100}), False, SEEDS)
        # A case whose prepare requests returned items replays recorded follow-ups.
        for record in plan:
            result, live, _, _ = self.run_record(record)
            self.assertIn(result["outcome"], sm.PASSING, result)
            # Every feature request sent, restoration and barriers included, is counted and capped.
            self.assertEqual(len(feature_requests(live)), sum(result["requests"].values()))
            self.assertLessEqual(len(feature_requests(live)), record["limits"]["requests"])
            if any(s["followups"] for s in record["steps"]):
                break
        else:
            self.fail("no planned case got hierarchy items")
        saved = self.directory / "case.json"
        saved.write_text(json.dumps(record))
        status = quietly([str(EXE), "--replay", str(saved), "--artifacts", str(self.directory)])
        self.assertEqual(status, 0)
        path = self.directory / ("replay-" + record["id"]) / "cases" / record["id"] / "transcript.jsonl"
        replayed = [e["msg"] for e in map(json.loads, path.read_text().splitlines()) if e["dir"] == "send"]

        def normalized(messages):
            root = messages[0]["params"]["rootUri"]
            return [sm.templated(m, root) for m in messages]
        self.assertEqual(normalized(sent(live)), normalized(replayed))
        for messages in (normalized(sent(live)), normalized(replayed)):
            actual = [m for m in messages if m.get("id") in ("restored-incoming", "restored-supertypes")]
            self.assertEqual([sm.dump(m["params"]) for m in actual],
                             [sm.dump(r["params"]) for r in record["restore_followups"]])
        # A record must be the format this runner writes.
        saved.write_text(json.dumps(dict(record, format=sm.FORMAT + 1)))
        self.assertEqual(quietly([str(EXE), "--replay", str(saved), "--artifacts", str(self.directory)]), 2)

    def test_known_compiler_trap_is_recorded(self):
        text = "component Mutant {\n\tdef add(a: int, b: int) -> int { return 0i + b; }\n}\n"
        result, _, _, log = self.run_record(single_case(text))
        self.assertEqual(result["outcome"], "known-worker-trap", (result, log))
        self.assertEqual([d["known"] for d in result["worker_deaths"]], ["crash-path row 1"])
        self.assertEqual(result["worker_deaths"][0]["replacement"], "verified")
        self.assertEqual(len(result["messages"]), 1)

    @unittest.skipUnless(STALL.exists() and sys.platform.startswith("linux"), "needs the stall worker and /proc")
    def test_cleanup_ends_a_stopped_servers_worker(self):
        self.stopped_server_case()
        with without_waitid():
            self.stopped_server_case()

    def stopped_server_case(self):
        stalled = self.directory / "stalled"
        shutil.rmtree(stalled, ignore_errors=True)
        stalled.mkdir()
        shutil.copy2(EXE, stalled / "virgil-lsp")
        shutil.copy2(STALL, stalled / "virgil-lsp-worker")
        limits = sm.resolve_limits({"request_timeout": 1, "case_timeout": 5, "run_timeout": 20})
        workers, original = [], sm.MutationSession.collect

        def collect(session, keys):
            # Once the stalling analysis has a worker, stop the server.
            if (int, 2) in keys and not workers:
                deadline = time.monotonic() + 5
                while not workers and time.monotonic() < deadline:
                    workers.extend(children(session.proc.pid))
                    time.sleep(0.02)
                time.sleep(0.2)
                os.kill(session.proc.pid, signal.SIGSTOP)
            return original(session, keys)
        with mock.patch.object(sm.MutationSession, "collect", collect):
            result, _, _, _ = self.run_record(single_case("// workerStallsHere\ncomponent Mutant {}\n", limits=limits),
                                              stalled / "virgil-lsp", worker_timeout_ms=30000)
        self.assertEqual(result["outcome"], "deadline", result)
        self.assertTrue(workers)
        self.assertEqual(gone(workers), [])

    @unittest.skipUnless(STALL.exists(), "build/stall/virgil-lsp-worker is not built")
    def test_contained_worker_failure(self):
        # The stall worker never finishes an analysis of marked source; the
        # supervisor kills it, and a replacement analyzes the restored source.
        stalled = self.directory / "stalled"
        stalled.mkdir()
        shutil.copy2(EXE, stalled / "virgil-lsp")
        shutil.copy2(STALL, stalled / "virgil-lsp-worker")
        result, _, _, log = self.run_record(single_case("// workerStallsHere\ncomponent Mutant {}\n"),
                                            stalled / "virgil-lsp", worker_timeout_ms=1000)
        self.assertEqual(result["outcome"], "worker-failure", (result, log))
        death, = result["worker_deaths"]
        self.assertIn("exceeded the analysis time limit of 1000 ms", death["what"])
        self.assertEqual((death["known"], death["replacement"]), (None, "verified"))
        self.assertIn("timed out", result["messages"][0])
        self.assertTrue(result["restored"])


if __name__ == "__main__":
    with sm.cancellation():
        unittest.main()
