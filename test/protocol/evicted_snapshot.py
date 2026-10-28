#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""An evicted project snapshot is analyzed again when a request needs it (#330).

Opt-in: `make test` does not run it, since its fixture is a copy of the pinned
Aeneas tree whose two analyses take about 13 MB each and a few seconds. Run it
by hand from the repository root after `make all`:

    TMPDIR=<scratch> python3 test/protocol/evicted_snapshot.py [build/virgil-lsp]

It copies `aeneas/src/` and `apps/Demangle/` from `vendor/virgil` into a new
directory under TMPDIR, configures them as the projects `compiler` and
`demangle` (whose dependencies include the compiler's sources), and opens
`aeneas/src/vst/Verifier.v3` and `apps/Demangle/Demangle.v3`. Under the 32 MiB
snapshot budget, the automatic analyses after an edit to Verifier.v3 leave the
compiler's snapshot evicted, so hover, definition, and semantic tokens there
used to answer null, null, and ContentModified until the next edit. Now the
first request analyzes the compiler again and is answered from that analysis.
The client supports both refresh requests and answers each; the script checks
that the analysis, which changes no open document's tokens or hints, sends
none, and that no analysis runs afterwards.

Automatic analyses have no completion signal, so the script waits until the
latest snapshot's generation (virgil-lsp/snapshot) has not changed for
QUIET seconds (default 4, or the EVICTED_SNAPSHOT_QUIET environment variable).
Every receive has a deadline. It prints each step's observations as JSON lines
and exits nonzero, with the transcript tail and the server's stderr, if the
compiler's requests are not answered after the edit.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from worker import Session

ROOT = Path(__file__).resolve().parents[2]
VIRGIL = ROOT / "vendor" / "virgil"
QUIET = float(os.environ.get("EVICTED_SNAPSHOT_QUIET", "4"))
DEPENDENCIES = ["lib/util/*.v3", "lib/asm/arm/*.v3", "lib/asm/x86/*.v3", "lib/asm/x86-64/*.v3",
                "lib/asm/arm64/*.v3", "lib/asm/riscv64/*.v3", "lib/file/elf/*.v3"]
CONFIG = {"version": 1, "projects": [
    {"name": "compiler", "sources": ["aeneas/src/*/*.v3"], "virgilDependencies": DEPENDENCIES},
    {"name": "demangle", "sources": ["apps/Demangle/*.v3"], "dependencies": ["aeneas/src/*/*.v3"],
     "virgilDependencies": DEPENDENCIES}]}
REFRESHES = ("workspace/semanticTokens/refresh", "workspace/inlayHint/refresh")
CONTENT_MODIFIED = -32801


class Client(Session):
    """Answers the server's refresh requests, as a client that supports them does."""
    NOTIFICATIONS = Session.NOTIFICATIONS + ("window/showMessage", "window/logMessage")

    def __init__(self, exe):
        super().__init__(exe)
        self.refreshes = []

    def receive(self, deadline):
        while True:
            msg = super().receive(deadline)
            if "id" in msg and "method" in msg:
                assert msg["method"] in REFRESHES, msg
                self.refreshes.append(msg["method"])
                self.answer(msg["id"])
                continue
            return msg

    def answer(self, id):
        msg = {"jsonrpc": "2.0", "id": id, "result": None}
        self.transcript.append(("send", msg))
        payload = json.dumps(msg, separators=(",", ":")).encode()
        self.raw(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)

    def drain(self, seconds):
        # Reads what arrives within {seconds}, answering refresh requests.
        deadline = time.monotonic() + seconds
        try:
            while True:
                msg = self.receive(deadline)
                if "id" in msg:
                    self.waiting[msg["id"]] = msg
        except AssertionError as e:
            if "timed out" not in str(e) and "deadline" not in str(e):
                raise


def generation(s):
    snapshot = s.result(s.send("virgil-lsp/snapshot"), 10)
    return None if snapshot is None else snapshot["generation"]


def observe(s):
    snapshot = s.result(s.send("virgil-lsp/snapshot"), 10)
    if snapshot is None:
        return None
    project = snapshot.get("projectConfiguration") or {}
    return {"generation": snapshot["generation"], "project": project.get("name"),
            "current": all(v["current"] for v in snapshot["documentVersions"])}


def settle(s, limit=180):
    # Waits until no analysis has completed for QUIET seconds.
    deadline = time.monotonic() + limit
    last, since = generation(s), time.monotonic()
    while time.monotonic() - since < QUIET:
        assert time.monotonic() < deadline, "automatic analyses did not settle"
        s.drain(0.25)
        now = generation(s)
        if now != last:
            last, since = now, time.monotonic()
    return last


def probe(s, uri, position):
    replies = {}
    for method in ("textDocument/hover", "textDocument/definition", "textDocument/semanticTokens/full"):
        params = {"textDocument": {"uri": uri}}
        if not method.endswith("/full"):
            params["position"] = position
        start = time.monotonic()
        reply = s.reply(s.send(method, params), 60)
        elapsed = round((time.monotonic() - start) * 1000, 2)
        if "result" in reply and method.endswith("/full"):
            reply = {"result": {"tokenIntegers": len((reply["result"] or {}).get("data", []))}}
        replies[method.rsplit("/", 1)[1]] = {"ms": elapsed, **{k: reply[k] for k in ("result", "error") if k in reply}}
    return replies


def answered(replies):
    hover, definition, tokens = replies["hover"], replies["definition"], replies["full"]
    return (hover.get("result") is not None and "def prog: Program" in json.dumps(hover["result"])
            and definition.get("result") not in (None, [])
            and tokens.get("result", {}).get("tokenIntegers", 0) > 0)


def main():
    exe = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build" / "virgil-lsp").resolve()
    work = Path(tempfile.mkdtemp(prefix="evicted-snapshot-"))
    s = None
    try:
        for relative in ("aeneas/src", "apps/Demangle"):
            shutil.copytree(VIRGIL / relative, work / relative)
        (work / ".virgil-lsp.json").write_text(json.dumps(CONFIG))
        compiler, app = work / "aeneas/src/vst/Verifier.v3", work / "apps/Demangle/Demangle.v3"
        s = Client(exe)
        s.result(s.send("initialize", {
            "rootUri": work.as_uri(), "workspaceFolders": [{"uri": work.as_uri(), "name": "evicted"}],
            "initializationOptions": {"virgilRoot": str(VIRGIL.resolve())},
            "capabilities": {"workspace": {"semanticTokens": {"refreshSupport": True},
                                           "inlayHint": {"refreshSupport": True}}}}), 10)
        s.send("initialized", {}, request=False)
        for path in (compiler, app):
            s.send("textDocument/didOpen", {"textDocument": {"uri": path.as_uri(), "languageId": "virgil",
                                                             "version": 1, "text": path.read_text()}}, request=False)
        prog, mangle = {"line": 24, "character": 12}, {"line": 5, "character": 2}

        def step(label, check=None):
            row = {"step": label, "snapshot": observe(s),
                   "compiler": probe(s, compiler.as_uri(), prog), "app": probe(s, app.as_uri(), mangle)}
            print(json.dumps(row), flush=True)
            return row

        settle(s)
        initial = step("initial")
        assert answered(initial["compiler"]), "the compiler's first analysis answers"
        s.send("textDocument/didChange", {"textDocument": {"uri": compiler.as_uri(), "version": 2},
                                          "contentChanges": [{"text": compiler.read_text() + "\n// whitespace-only dogfood change 2\n"}]},
               request=False)
        settle(s)
        before = len(s.refreshes)
        edited = step("Verifier.v3 v2")
        demanded = len(s.refreshes) - before
        quiet = settle(s)
        after = observe(s)
        print(json.dumps({"step": "after", "snapshot": after, "refreshesDuringRequests": demanded,
                          "refreshesTotal": len(s.refreshes)}), flush=True)
        assert answered(edited["compiler"]), "the compiler's requests are answered after its snapshot was evicted"
        assert demanded == 0, "an unchanged rebuild sends no refresh"
        assert after["generation"] == quiet, "no analysis runs without a request"
        s.shutdown()
        print("evicted_snapshot: ok", flush=True)
    except BaseException:
        if s is not None:
            for direction, msg in s.transcript[-12:]:
                text = json.dumps(msg)
                print(direction, text[:400] + ("..." if len(text) > 400 else ""), file=sys.stderr)
            print(s.logs(), file=sys.stderr)
        raise
    finally:
        if s is not None:
            s.close()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
