#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Interactive transcripts: replies must arrive without another stdin write.

Golden files cannot express interleaved worker completion, so this client checks
framing, IDs, response order and bounded responsiveness over real process pipes.
Every receive has a deadline; failures include the complete decoded transcript.
"""

import glob
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time


class Session:
    def __init__(self, exe, *options):
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            [str(exe), "--stdio", *options],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
        )
        self.buffer = b""
        self.transcript = []
        self.answered = set()
        self.waiting = {}
        self.next_id = 1

    def send(self, method, params=None, request=True, id=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if request:
            if id is None:
                id = self.next_id
                self.next_id += 1
            msg["id"] = id
        self.transcript.append(("send", msg))
        payload = json.dumps(msg, separators=(",", ":")).encode()
        self.raw(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
        return id

    def raw(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def receive(self, deadline):
        while True:
            header, sep, body = self.buffer.partition(b"\r\n\r\n")
            if sep:
                assert header.startswith(b"Content-Length: "), header
                size = int(header.removeprefix(b"Content-Length: "))
                if len(body) >= size:
                    self.buffer = body[size:]
                    msg = json.loads(body[:size])
                    self.transcript.append(("recv", msg))
                    assert msg["jsonrpc"] == "2.0", msg
                    if "id" in msg:
                        key = (type(msg["id"]), msg["id"])
                        assert key not in self.answered, ("duplicate response", msg)
                        self.answered.add(key)
                    else:
                        assert msg["method"] == "textDocument/publishDiagnostics", msg
                    return msg
            remaining = deadline - time.monotonic()
            assert remaining > 0, "timed out waiting for a protocol reply"
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            assert ready, "server did not answer before the deadline"
            data = os.read(self.proc.stdout.fileno(), 65536)
            assert data, ("unexpected stdout EOF", self.proc.poll())
            self.buffer += data

    def reply(self, id, timeout=30):
        deadline = time.monotonic() + timeout
        while id not in self.waiting:
            msg = self.receive(deadline)
            if "id" in msg:
                self.waiting[msg["id"]] = msg
        return self.waiting.pop(id)

    def result(self, id, timeout=30):
        msg = self.reply(id, timeout)
        assert "result" in msg, msg
        return msg["result"]

    def initialize(self):
        result = self.result(self.send("initialize", {}))
        assert set(result["capabilities"]) == {"textDocumentSync", "documentSymbolProvider", "workspace"}

    def snapshot(self, expected, timeout=3):
        assert self.result(self.send("virgil-lsp/snapshot"), timeout) == expected

    def ping(self):
        # Standard unsupported request, independent of compiler data.
        msg = self.reply(self.send("test/ping"), 3)
        assert msg["error"]["code"] == -32601, msg

    def analyze(self, uris, id=None):
        return self.send("virgil-lsp/analyze", {"uris": uris}, id=id)

    def shutdown(self):
        assert self.result(self.send("shutdown"), 3) is None
        self.send("exit", request=False)
        self.proc.stdin.close()
        assert self.proc.wait(timeout=5) == 0
        assert not self.buffer and not self.proc.stdout.read(), "unsolicited extra response"
        assert not self.waiting, self.waiting

    def logs(self):
        self.err.seek(0)
        return self.err.read().decode(errors="replace")

    def close(self):
        if self.proc.poll() is None:
            # EOF lets the server reap its worker even after a test assertion.
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc.stdout.close()
        self.err.close()


def error(session, id, code, text=""):
    msg = session.reply(id)
    assert msg["error"]["code"] == code, msg
    assert text in msg["error"]["message"], msg


def aeneas_uris():
    root = Path("vendor/virgil")
    files = sorted(root.glob("aeneas/src/*/*.v3"))
    for pattern in (root / "aeneas/DEPS").read_text().split():
        if not pattern.startswith("lib/test/"):
            files.extend(Path(p) for p in sorted(glob.glob(str(root / pattern))))
    return [p.resolve().as_uri() for p in files]


def large_payload(method, id, params=None, values=49900, size=4 * 1024 * 1024):
    params = {**(params or {}), "ignored": [{}] * values, "padding": ""}
    msg = {"jsonrpc": "2.0", "id": id, "method": method, "params": params}
    params["padding"] = "x" * (size - len(json.dumps(msg, separators=(",", ":")).encode()))
    payload = json.dumps(msg, separators=(",", ":")).encode()
    assert len(payload) == size
    return payload


def send_payload(session, payload):
    session.raw(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)


def expected_snapshot(generation, uris, version=None, current=True):
    return {"generation": generation, "diagnostics": [], "documentVersions": [
        {"uri": uri, "version": version, "current": current} for uri in uris
    ]}


def memory_headroom(exe, work):
    # Aeneas analyses keep the default time limit; separate hang sessions use a
    # short limit so their watchdog checks finish promptly on slower CI runners.
    s = Session(exe)
    try:
        s.initialize()
        uris = aeneas_uris()
        first = s.result(s.analyze(uris))
        assert first == expected_snapshot(1, uris), first
        send_payload(s, large_payload("virgil-lsp/snapshot", "large-snapshot"))
        assert s.result("large-snapshot") == first

        pending = s.analyze(uris)
        payload = large_payload("virgil-lsp/snapshot", "buffered-snapshot")
        s.raw(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload[:-1])
        second = s.result(pending)
        assert second == expected_snapshot(2, uris), second
        s.raw(payload[-1:])
        assert s.result("buffered-snapshot") == second

        source = work / "large-source.v3"
        source.write_text("def a = 42; def b = a;\n//" + " " * (15 * 1024 * 1024))
        send_payload(s, large_payload("virgil-lsp/analyze", "original-overflow", {"uris": [source.as_uri()]},
                                      values=499900, size=16 * 1024 * 1024))
        s.ping()
        assert (str, "original-overflow") not in s.answered
        s.snapshot(second)
        send_payload(s, large_payload("virgil-lsp/analyze", "over-budget", {"uris": [source.as_uri()]}))
        error(s, "over-budget", -32602, "size budget")
        s.snapshot(second)
        send_payload(s, large_payload("virgil-lsp/analyze", "too-many-values", {"uris": [source.as_uri()]}, values=499900))
        error(s, None, -32700, "50000 values")
        s.ping()

        source.write_text("def a = 42; def b = a;\n//" + " " * (3 * 1024 * 1024))
        send_payload(s, large_payload("virgil-lsp/analyze", "large-analysis", {"uris": [source.as_uri()]}))
        good = s.result("large-analysis")
        assert good == expected_snapshot(3, [source.as_uri()]), good
        other = work / "other-source.v3"
        other.write_text("def other = 1;\n//" + " " * (3 * 1024 * 1024))
        send_payload(s, large_payload("virgil-lsp/analyze", "aggregate-budget",
                                      {"uris": [source.as_uri(), other.as_uri()]}))
        error(s, "aggregate-budget", -32602, "size budget")
        s.snapshot(good)

        final = s.result(s.analyze(uris))
        assert final == expected_snapshot(4, uris), final
        s.shutdown()
        assert "longer than the limit of 4194304 bytes" in s.logs()
        assert "HeapOverflow" not in s.logs(), s.logs()
    finally:
        s.close()


def large_hang(exe, work):
    s = Session(exe, "--worker-timeout-ms=5000")
    try:
        s.initialize()
        shapes = Path("test/fixtures/analysis/two-file/shapes.v3").resolve().as_uri()
        first = s.result(s.analyze([shapes]))
        assert first == expected_snapshot(1, [shapes]), first
        source = work / "large-hang.v3"
        source.write_text("class S extends S { def f() { me; } }\n//" + " " * (3 * 1024 * 1024))
        send_payload(s, large_payload("virgil-lsp/analyze", "large-hang", {"uris": [source.as_uri()]}))
        send_payload(s, large_payload("test/ping", "large-ping"))
        error(s, "large-ping", -32601)
        assert (str, "large-hang") not in s.answered
        send_payload(s, large_payload("virgil-lsp/snapshot", "snapshot-during-hang"))
        assert s.result("snapshot-during-hang") == first
        error(s, "large-hang", -32603, "timed out")
        s.snapshot(first)
        final = s.result(s.analyze([shapes]))
        assert final == expected_snapshot(2, [shapes]), final
        s.shutdown()
        assert "time limit of 5000 ms" in s.logs(), s.logs()
    finally:
        s.close()


def exercise(exe, work):
    s = Session(exe, "--worker-timeout-ms=5000")
    try:
        uri = (work / "overlay space.v3").as_uri()
        (work / "overlay space.v3").write_text("def disk: i32 = false;\n")
        crash = Path("test/fixtures/analysis/worker-crash/main.v3").resolve().as_uri()
        hang = Path("test/fixtures/analysis/worker-hang/main.v3").resolve().as_uri()
        error(s, s.analyze([uri]), -32002)
        s.initialize()
        s.snapshot(None)
        for params in ({}, {"uris": []}, {"uris": [42]}, {"uris": ["file://remote/no"]},
                       {"uris": [uri, uri]}, {"uris": ["untitled:missing"]},
                       {"uris": [uri], "unsupported": 0.5}):
            error(s, s.send("virgil-lsp/analyze", params), -32602)
        s.send("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "virgil", "version": 1,
            "text": "def overlay = 42;\n",
        }}, request=False)
        # Open overlay wins over the invalid disk text; string IDs are preserved.
        pending = s.analyze([uri], id="overlay")
        s.send("textDocument/didChange", {"textDocument": {"uri": uri, "version": 2},
                                        "contentChanges": [{"text": "def changed: i32 = false;\n"}]},
               request=False)
        good = s.result(pending)
        assert good in (expected_snapshot(1, [uri], version=1, current=True),
                        expected_snapshot(1, [uri], version=1, current=False)), good
        s.snapshot(expected_snapshot(1, [uri], version=1, current=False))
        s.send("textDocument/didClose", {"textDocument": {"uri": uri}}, request=False)
        diagnostic = s.result(s.analyze([uri]))
        assert diagnostic["generation"] == 2 and diagnostic["diagnostics"], diagnostic
        assert diagnostic["diagnostics"][0]["path"] == str(work / "overlay space.v3")
        assert diagnostic["documentVersions"] == [{"uri": uri, "version": None, "current": True}]

        # Oversized disk input must be rejected before unchecked file allocation.
        huge = work / "huge.v3"
        with huge.open("wb") as file:
            file.truncate(32 * 1024 * 1024)
        error(s, s.analyze([huge.as_uri()]), -32602, "size budget")
        s.snapshot(diagnostic)
        s.ping()

        # The compiler crash must be isolated, and nonempty previous diagnostic
        # data must remain queryable without contacting its now-dead worker.
        error(s, s.analyze([crash]), -32603, "crashed")
        s.ping()
        s.snapshot(diagnostic)
        good = s.result(s.analyze([Path("test/fixtures/analysis/two-file/shapes.v3").resolve().as_uri(),
                                   Path("test/fixtures/analysis/two-file/main.v3").resolve().as_uri()]))
        assert good == expected_snapshot(3, [Path("test/fixtures/analysis/two-file/shapes.v3").resolve().as_uri(),
                                             Path("test/fixtures/analysis/two-file/main.v3").resolve().as_uri()]), good

        # Warm worker: hang is active before either lightweight reply. Keep an
        # incomplete stdin frame buffered while the worker deadline expires.
        pending = s.analyze([hang], id="hanging")
        s.ping()
        s.snapshot(good)
        assert (str, pending) not in s.answered, "hang completed before responsiveness checks"
        error(s, s.analyze([uri]), -32602, "already running")
        s.raw(b"Content-Len")
        error(s, pending, -32603, "timed out")
        payload = b'{"jsonrpc":"2.0","id":"partial","method":"virgil-lsp/snapshot"}'
        s.raw(f"gth: {len(payload)}\r\n\r\n".encode() + payload)
        assert s.result("partial") == good
        s.snapshot(good)

        # No retained source buffer may turn later overlays into old inputs.
        s.send("textDocument/didOpen", {"textDocument": {
            "uri": "untitled:worker-test", "languageId": "virgil", "version": 1, "text": "",
        }}, request=False)
        good = s.result(s.analyze(["untitled:worker-test"]))
        assert good == expected_snapshot(4, ["untitled:worker-test"], version=1), good

        pending = s.analyze([hang])
        shutdown = s.send("shutdown")
        error(s, pending, -32800, "shutting down")
        assert s.result(shutdown, 3) is None
        error(s, s.analyze([uri]), -32600)
        s.send("exit", request=False)
        s.proc.stdin.close()
        assert s.proc.wait(timeout=5) == 0
        assert not s.buffer and not s.proc.stdout.read()
        assert not s.waiting
        log = s.logs()
        assert "NullCheckException" in log and "snapshot 2 is kept" in log, log
        assert "time limit of 5000 ms" in log and "snapshot 3 is kept" in log, log
    except BaseException:
        print(json.dumps(s.transcript, indent=2), file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def aeneas_repeats(exe):
    # Match the CLI's Aeneas file set and default time limit: 20 full
    # parse/verify/index analyses, crossing multiple forced replacements while
    # stdio keeps responding. Report each analysis's time against the limit.
    s = Session(exe, "--worker-max-analyses=8")
    try:
        s.initialize()
        uris = aeneas_uris()
        times = []
        for generation in range(1, 21):
            start = time.monotonic()
            pending = s.analyze(uris)
            s.ping()
            current = s.result(pending)
            times.append(time.monotonic() - start)
            assert current == expected_snapshot(generation, uris), current
            s.snapshot(current)
        s.shutdown()
        log = s.logs()
        assert log.count("after 8 analyses (limit 8)") == 2, log
        ms = sorted(round(t * 1000) for t in times)
        print(f"protocol worker: {len(ms)} Aeneas analyses took {ms[0]}-{ms[-1]} ms "
              f"(median {ms[len(ms) // 2]} ms) against the default limit of 10000 ms")
    except BaseException:
        print(json.dumps(s.transcript, indent=2), file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def startup_and_eof(exe, work):
    # A deliberately silent fake worker proves startup/handshake is nonblocking,
    # not just verification. Copy the server so defaultPath finds this fixture.
    local = work / "startup"
    local.mkdir()
    shutil.copy2(exe, local / "virgil-lsp")
    worker = local / "virgil-lsp-worker"
    worker.write_text("#!/bin/sh\nexec /bin/sleep 60\n")
    worker.chmod(0o755)
    uri = Path("test/fixtures/analysis/two-file/main.v3").resolve().as_uri()
    for ending in ("shutdown", "eof", "exit"):
        s = Session(local / "virgil-lsp")
        try:
            s.initialize()
            pending = s.analyze([uri])
            s.ping()
            s.snapshot(None)
            assert (int, pending) not in s.answered
            if ending == "shutdown":
                shutdown = s.send("shutdown")
                error(s, pending, -32800)
                assert s.result(shutdown, 3) is None
                s.send("exit", request=False)
            elif ending == "exit":
                s.send("exit", request=False)
            s.proc.stdin.close()
            if ending != "shutdown":
                error(s, pending, -32800)
            assert s.proc.wait(timeout=5) == (0 if ending == "shutdown" else 1)
            assert not s.buffer and not s.proc.stdout.read()
        finally:
            s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="virgil-worker-protocol-") as directory:
        work = Path(directory).resolve()
        exercise(exe, work)
        aeneas_repeats(exe)
        startup_and_eof(exe, work)
        memory_headroom(exe, work)
        large_hang(exe, work)
    print("protocol worker: crash, hang, retained snapshots, 20 Aeneas analyses, shutdown, startup and memory headroom passed")


if __name__ == "__main__":
    main()
