#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Bounded offline shapes and semantic oracles over worker.Session's pipes."""

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import random
import shlex
import subprocess
import sys
import tempfile
import time

from shape_model import (EXTENSIONS, MODEL_SHA256, Model, REQUESTS,
                         TEXT, URI, generated_cases, session_validity,
                         unsupported_number, verify_methods)
from worker import Session

ROOT = Path(__file__).resolve().parents[2]
# Known server failures: none. Record any future deviation in one narrow case
# here, retaining framing/typed-ID/liveness/shutdown checks and a write-up.
# Only errors actually relevant to these handlers, not the whole JSON-RPC range.
PARAM_ERRORS = {-32602}
METHOD_ERRORS = {
    "textDocument/rename": {-32602, -32803},
    "textDocument/prepareRename": {-32602, -32803},
}


def frame(message):
    payload = json.dumps(message, separators=(",", ":")).encode()
    return ("Content-Length: %d\r\n\r\n" % len(payload)).encode() + payload


class ShapeSession(Session):
    """Keep Session's framing, select deadlines, process ownership and cleanup.

    Match IDs by both type and value (Session.reply's dictionary alone cannot
    detect a server changing a numeric ID into a string). Sequential calls also
    let any unsolicited response fail immediately, including notification replies.
    """
    def __init__(self, exe):
        super().__init__(exe, "--worker-timeout-ms=5000")
        self.frames = []
        self.expected = {}
        self.case = None
        self.checkpoints = []

    def raw(self, data):
        self.frames.append(data)
        super().raw(data)

    def send(self, method, params=None, request=True, id=None):
        ident = super().send(method, params, request=request, id=id)
        if request:
            key = (type(ident), ident)
            assert key not in self.expected, ("test reused an ID", key)
            self.expected[key] = method
        return ident

    def receive(self, deadline):
        while True:
            msg = super().receive(deadline)
            if "method" in msg and "id" in msg:
                assert msg["method"] == "client/registerCapability", msg
                # Watcher-capability variants really negotiate registration.
                response = {"jsonrpc": "2.0", "id": msg["id"], "result": None}
                self.transcript.append(("send", response))
                self.raw(frame(response))
                continue
            if "id" in msg:
                key = (type(msg["id"]), msg["id"])
                assert key in self.expected, ("unexpected response/ID type, or notification reply", msg)
                assert ("result" in msg) != ("error" in msg), ("invalid reply envelope", msg)
                if "error" in msg:
                    assert type(msg["error"]["code"]) is int, msg
            return msg

    def reply(self, ident, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            msg = self.receive(deadline)
            if "id" in msg:
                assert type(msg["id"]) is type(ident) and msg["id"] == ident, (
                    "reply must preserve the outstanding request's typed ID", ident, msg)
                return msg

    def call(self, method, params=None, code=None, id=None):
        ident = self.send(method, params, id=id)
        msg = self.reply(ident)
        if code is not None:
            assert msg.get("error", {}).get("code") == code, (method, "expected error", code, msg)
        elif "error" in msg:
            assert msg["error"]["code"] in METHOD_ERRORS.get(method, PARAM_ERRORS), (
                method, "undocumented error", msg)
        return msg

    def success(self, method, params=None):
        msg = self.call(method, params)
        assert "result" in msg, (method, "expected success", msg)
        return msg["result"]

    def start(self, params=None, initialized=True):
        result = self.success("initialize", {"processId": None, "rootUri": None, "capabilities": {}}
                              if params is None else params)
        assert result["capabilities"]["textDocumentSync"]["change"] == 1, result
        if initialized:
            self.send("initialized", {}, request=False)
        return result

    def barrier(self):
        # A synchronous request proves all preceding notifications were read.
        assert isinstance(self.success("workspace/symbol", {"query": ""}), list)

    def finish(self):
        self.shutdown()
        assert set(self.expected) == self.answered, ("missing/extra replies", self.expected, self.answered)
        assert "HeapOverflow" not in self.logs(), self.logs()

    def mark(self, case):
        self.case = case
        self.checkpoints.append({"case": case["id"], "frame": len(self.frames)})


def special(ident, schema=True, negotiated=True, lifecycle=True, reason="semantic regression"):
    return {"id": ident, "method": "@oracle", "label": ident,
            "schema_valid": schema, "session_valid": negotiated,
            "lifecycle_valid": lifecycle, "reason": reason}


SPECIALS = [
    special("symbols-absent"), special("symbols-false"), special("symbols-true"),
    special("empty-change-version"), special("full-change-version-control"),
    special("ranged-change-rejected", negotiated=False, reason="full synchronization rejects ranged changes"),
    special("decimal-options-rejected", negotiated=False, reason="documented JsonValue number limitation"),
    special("oversized-integer-options-rejected", negotiated=False, reason="documented JsonValue number limitation"),
    special("typed-request-ids"),
    special("before-initialize", lifecycle=False, reason="requests before initialize get ServerNotInitialized"),
    special("duplicate-initialize", lifecycle=False, reason="second initialize gets InvalidRequest"),
    special("after-shutdown", lifecycle=False, reason="requests after shutdown get InvalidRequest"),
    special("shutdown-exit-null", reason="explicit null params in lifecycle epilogue"),
]


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        result = copy.deepcopy(a)
        for key, value in b.items():
            result[key] = merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(b)


def choose_cases(model, seed, budget):
    corpus = list(generated_cases(model))
    assert len({c["id"] for c in corpus}) == len(corpus), "case ID collision"
    mandatory = SPECIALS + [c for c in corpus if c["label"] == "minimal"]
    if budget < len(mandatory):
        raise ValueError("--cases must be at least %d (method minima and semantic oracles)" % len(mandatory))
    rng = random.Random(seed)
    rest = [c for c in corpus if c["label"] != "minimal"]
    rng.shuffle(rest)
    selected = mandatory + rest[:budget - len(mandatory)]
    # Wider runs combine independently generated initialize shapes. Full model
    # traversal stays fixed; only bounded pairwise extras depend on the seed.
    initializers = [c for c in corpus if c["method"] == "initialize" and c["schema_valid"]]
    while len(selected) < budget:
        left, right = rng.sample(initializers, 2)
        params = merge(left["params"], right["params"])
        valid = model.valid(model.params["initialize"], params)
        negotiated, reason = session_validity("initialize", params)
        selected.append({"id": "pair-%d-%d" % (seed, len(selected)), "method": "initialize",
                         "label": left["label"] + " + " + right["label"], "params": params,
                         "schema_valid": valid, "session_valid": valid and negotiated,
                         "lifecycle_valid": True, "reason": reason})
    return selected, len(corpus)


def open_document(s, uri=URI, text=TEXT):
    s.send("textDocument/didOpen", {"textDocument": {
        "uri": uri, "languageId": "virgil", "version": 1, "text": text}}, request=False)


def shape(s, case):
    method, params = case["method"], case["params"]
    if method == "initialize":
        if unsupported_number(params):
            s.call(method, params, code=-32602)
            s.start()  # failed initialize must leave recovery possible
        else:
            s.start(params)
        return
    if method.startswith("textDocument/") and method != "textDocument/didOpen":
        open_document(s)
    if method in REQUESTS + EXTENSIONS:
        reply = s.call(method, params, code=-32602 if unsupported_number(params) else None)
        if case["session_valid"] and method not in ("textDocument/prepareRename", "virgil-lsp/analyze"):
            assert "result" in reply, ("valid shape unexpectedly rejected", case, reply)
    else:
        s.send(method, params, request=False)
        s.barrier()
    if method.startswith("textDocument/"):
        s.send("textDocument/didClose", {"textDocument": {"uri": URI}}, request=False)
        s.barrier()


def symbols(s, mode):
    caps = {} if mode == "absent" else {"textDocument": {"documentSymbol": {
        "hierarchicalDocumentSymbolSupport": mode == "true"}}}
    s.start({"processId": None, "rootUri": None, "capabilities": caps})
    open_document(s)
    result = s.success("textDocument/documentSymbol", {"textDocument": {"uri": URI}})
    assert isinstance(result, list) and result, ("#196 expected nonempty document symbols", result)
    if mode == "true":
        assert result[0]["name"] == "C" and "range" in result[0] and "location" not in result[0], (
            "#196 hierarchy negotiated true: expected DocumentSymbol[]", result)
        assert [c["name"] for c in result[0]["children"]] == ["x"], result
    else:
        assert [sym["name"] for sym in result] == ["C", "x"], (
            "#196 absent/false hierarchy: expected flat SymbolInformation[] containing C and x", result)
        for sym in result:
            assert sym.get("location", {}).get("uri") == URI and "range" in sym["location"], (
                "#196 absent/false hierarchy: expected SymbolInformation.location", result)
            assert not ({"range", "selectionRange", "children"} & sym.keys()), result
        assert result[1]["containerName"] == "C", result


def version(s, mode, work):
    # A real project reaches the worker and rename preflight. A null result or
    # RequestFailed cannot accidentally satisfy the version regression oracle.
    fixture = work / "version-project"
    fixture.mkdir(exist_ok=True)
    text = "component C { def foo(a: int) -> int { return a; } def use() -> int { return foo(1); } }\n"
    source = fixture / "Main.v3"
    source.write_text(text)
    (fixture / ".virgil-lsp.json").write_text(json.dumps({"version": 1, "projects": [
        {"name": "shapes", "sources": ["*.v3"]}]}))
    uri = source.as_uri()
    s.start({"processId": None, "rootUri": fixture.as_uri(), "capabilities": {
        "workspace": {"workspaceEdit": {"documentChanges": True}}}})
    open_document(s, uri, text)
    at = {"textDocument": {"uri": uri}, "position": {"line": 0, "character": text.index("foo")},
          "newName": "bar"}
    before = s.success("textDocument/rename", at)
    assert before["documentChanges"][0]["textDocument"]["version"] == 1, before
    if mode == "ranged-change-rejected":
        changes = [{"range": {"start": {"line": 0, "character": 0},
                              "end": {"line": 0, "character": 0}}, "text": "broken"}]
    else:
        changes = [] if mode == "empty-change-version" else [{"text": text}]
    s.send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                      "contentChanges": changes}, request=False)
    # Do not send shutdown until the rename reply arrives: it could cancel the
    # preflight and hide the stale-version bug that this oracle is meant to find.
    after = s.success("textDocument/rename", at)
    expected = 1 if mode == "ranged-change-rejected" else 2
    edits = after["documentChanges"]
    assert edits and all(e["textDocument"]["version"] == expected for e in edits), (
        "#200 empty/full change: rename TextDocumentEdit must carry version %d" % expected, after)
    if mode == "ranged-change-rejected":
        s.barrier()
        assert "incremental changes are not supported" in s.logs(), s.logs()


def oracle(s, case, work):
    ident = case["id"]
    if ident.startswith("symbols-"):
        symbols(s, ident.removeprefix("symbols-"))
    elif ident in ("empty-change-version", "full-change-version-control", "ranged-change-rejected"):
        version(s, ident, work)
    elif ident in ("decimal-options-rejected", "oversized-integer-options-rejected"):
        value = 0.5 if ident.startswith("decimal") else 2147483648
        s.call("initialize", {"processId": None, "rootUri": None, "capabilities": {},
                              "initializationOptions": {"unused": value}}, code=-32602)
        s.start()
    elif ident == "before-initialize":
        s.send("initialized", {}, request=False)
        s.call("workspace/symbol", {"query": ""}, code=-32002)
        s.start()
    elif ident == "duplicate-initialize":
        s.start()
        s.call("initialize", {}, code=-32600)
    elif ident == "after-shutdown":
        s.start()
        assert s.success("shutdown") is None
        s.call("workspace/symbol", {"query": ""}, code=-32600)
        s.send("exit", request=False)
        s.proc.stdin.close()
        assert s.proc.wait(timeout=5) == 0
        assert not s.buffer and not s.proc.stdout.read(), "extra response after exit"
        assert set(s.expected) == s.answered
        return False
    elif ident == "typed-request-ids":
        s.start()
        for value in (0, "0", -1, "-1", 2147483647, "2147483647", "", -2147483648):
            assert "result" in s.call("workspace/symbol", {"query": ""}, id=value)
    elif ident == "shutdown-exit-null":
        s.start()
        # Session.send(None) omits params; exercise explicit null independently.
        msg = {"jsonrpc": "2.0", "id": "null-shutdown", "method": "shutdown", "params": None}
        s.expected[(str, "null-shutdown")] = "shutdown"
        s.transcript.append(("send", msg))
        s.raw(frame(msg))
        assert s.reply("null-shutdown")["result"] is None
        msg = {"jsonrpc": "2.0", "method": "exit", "params": None}
        s.transcript.append(("send", msg))
        s.raw(frame(msg))
        s.proc.stdin.close()
        assert s.proc.wait(timeout=5) == 0
        assert not s.buffer and not s.proc.stdout.read(), "extra response after exit"
        assert set(s.expected) == s.answered
        return False
    else:
        raise AssertionError("unknown oracle " + ident)
    return True


def failure(s, args, exc):
    case = s.case
    dest = args.artifacts.resolve() / ("seed-%d" % args.seed) / case["id"]
    dest.mkdir(parents=True, exist_ok=True)
    data = b"".join(s.frames)
    (dest / "input.bin").write_bytes(data)
    (dest / "transcript.json").write_text(json.dumps(s.transcript, indent=2) + "\n")
    replies = [msg for direction, msg in s.transcript if direction == "recv"]
    (dest / "replies.json").write_text(json.dumps(replies, indent=2) + "\n")
    logs = s.logs()
    (dest / "stderr.txt").write_text(logs)
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), str(args.exe),
                          "--seed", str(args.seed), "--cases", str(args.cases),
                          "--case", case["id"], "--artifacts", str(args.artifacts)])
    metadata = {"seed": args.seed, "case": case, "error": str(exc), "rerun": command,
                "server_sha256": hashlib.sha256(args.exe.read_bytes()).hexdigest(),
                "worker_sha256": hashlib.sha256(args.exe.with_name("virgil-lsp-worker").read_bytes()).hexdigest(),
                "model_sha256": MODEL_SHA256, "checkpoints": s.checkpoints}
    (dest / "failure.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print("FAIL seed=%d case=%s: %s" % (args.seed, case["id"], exc), file=sys.stderr)
    print(json.dumps(metadata, indent=2), file=sys.stderr)
    print("exact framed messages: " + repr(data), file=sys.stderr)
    print("replies: " + json.dumps(replies), file=sys.stderr)
    print("stderr:\n" + logs, file=sys.stderr)
    print("artifacts: " + str(dest) + "\nrerun: " + command, file=sys.stderr)


def run_group(group, args, work):
    s = ShapeSession(args.exe)
    try:
        s.mark(group[0])
        standalone = group[0]["method"] in ("initialize", "@oracle")
        if not standalone:
            s.start(initialized=group[0]["method"] != "initialized")
        finish = True
        for case in group:
            s.mark(case)
            if case["method"] == "@oracle":
                finish = oracle(s, case, work)
            else:
                shape(s, case)
        if finish:
            s.finish()
        return True
    except Exception as exc:
        # EOF reaps this server's worker; Session.close escalates only through
        # this Popen object if its own child does not exit. Never kill by name.
        if not s.proc.stdin.closed:
            s.proc.stdin.close()
        try:
            s.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            s.proc.kill()
            s.proc.wait(timeout=5)
        failure(s, args, exc)
        return False
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cases", type=int, default=1500, help="total budget, including mandatory semantic cases")
    parser.add_argument("--case", help="run one stable case ID, including its setup and shutdown")
    parser.add_argument("--list", action="store_true", help="print selected cases and their three validity layers")
    parser.add_argument("--artifacts", type=Path, default=Path("build/protocol-shapes"))
    args = parser.parse_args()
    args.exe = args.exe.resolve()
    verify_methods(ROOT)
    model = Model()
    try:
        cases, corpus_size = choose_cases(model, args.seed, args.cases)
    except ValueError as exc:
        parser.error(str(exc))
    if args.case:
        # Rerun any model case even if a smaller budget would not select it.
        pool = cases + list(generated_cases(model))
        cases = [c for c in pool if c["id"] == args.case][:1]
        if not cases:
            parser.error("unknown case ID " + args.case)
    if args.list:
        for case in cases:
            print(json.dumps(case, ensure_ascii=False))
        return 0
    started = time.monotonic()
    groups = []
    shared = {}
    for case in cases:
        if case["method"] in ("initialize", "@oracle"):
            groups.append([case])
        else:
            shared.setdefault(case["method"], []).append(case)
    for group in shared.values():
        # Bound trace size and isolate document state; dozens share each server.
        groups.extend(group[i:i + 32] for i in range(0, len(group), 32))
    args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fixtures-", dir=args.artifacts.resolve()) as temp:
        for group in groups:
            if not run_group(group, args, Path(temp)):
                return 1
    layers = Counter((c["schema_valid"], c["session_valid"], c["lifecycle_valid"]) for c in cases)
    print("protocol shapes: %d passed; seed=%d; budget=%d; model corpus=%d; sessions=%d; %.2fs" % (
        len(cases), args.seed, args.cases, corpus_size, len(groups), time.monotonic() - started))
    print("validity (schema/session/lifecycle): " + "; ".join(
        "%s=%d" % (key, value) for key, value in sorted(layers.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
