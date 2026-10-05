#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Golden semantic-diagnostic transcripts.

Automatic analysis completes after the input that scheduled it, so these
transcripts interleave input with expected output instead of feeding all input
at once as test/protocol/run.sh does. Each *.jsonl file next to this script is
one session of "virgil-lsp --stdio". Every line is a JSON object with one key:

  {"send": message}       Writes one message. An array of messages is written
                          in a single write, so the server reads them together.
  {"recv": message}       The next message from the server must equal this.
  {"stderr": "regex"}     Standard error must match this, after the session.
  {"status": n}           The expected exit status (default 0).
  {"write": [path, text]} Writes a file in the transcript's temporary
                          directory, creating its parent directories.
  {"remove": path}        Removes a file from the temporary directory.

Lines starting with # are comments. In strings, @ROOT@ stands for the file
URI of test/fixtures/projects, @TMP@ for the file URI of the transcript's own
temporary directory, @TMPDIR@ for its path, and @VERSION@ for the server
version. The temporary directory starts empty. After the
last line, standard input is closed; the server must exit with the expected
status and write nothing more. Every receive has a deadline, but no expected
message depends on timing: a result can only follow the input before it.

Usage: test/diagnostics/run.py <path-to-virgil-lsp> [case ...]
"""

import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import time

DIR = Path(__file__).resolve().parent
ROOT = (DIR.parent / "fixtures" / "projects").resolve().as_uri()
DEADLINE = 60


def substitute(value, names):
    if isinstance(value, str):
        for name, replacement in names.items():
            value = value.replace(name, replacement)
        return value
    if isinstance(value, list):
        return [substitute(v, names) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, names) for k, v in value.items()}
    return value


def encode(message):
    payload = json.dumps(message, separators=(",", ":")).encode()
    return f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload


class Session:
    def __init__(self, exe):
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([str(exe), "--stdio"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.err)
        self.buffer = b""
        self.transcript = []

    def send(self, messages):
        self.transcript.append(("send", messages))
        data = b"".join(encode(m) for m in (messages if isinstance(messages, list) else [messages]))
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def receive(self):
        deadline = time.monotonic() + DEADLINE
        while True:
            header, sep, body = self.buffer.partition(b"\r\n\r\n")
            if sep:
                assert header.startswith(b"Content-Length: "), header
                size = int(header.removeprefix(b"Content-Length: "))
                if len(body) >= size:
                    self.buffer = body[size:]
                    message = json.loads(body[:size])
                    self.transcript.append(("recv", message))
                    return message
            remaining = deadline - time.monotonic()
            assert remaining > 0, "timed out waiting for a message"
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            assert ready, "timed out waiting for a message"
            data = os.read(self.proc.stdout.fileno(), 65536)
            assert data, ("unexpected end of output", self.proc.poll())
            self.buffer += data

    def finish(self):
        self.proc.stdin.close()
        status = self.proc.wait(timeout=DEADLINE)
        rest = self.buffer + self.proc.stdout.read()
        assert not rest, ("unexpected output after the transcript", rest)
        return status

    def logs(self):
        self.err.seek(0)
        return self.err.read().decode(errors="replace")

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        self.err.close()


def run(exe, path, version):
    lines = path.read_text().splitlines()
    # The server never follows symbolic links, so use the physical path (on
    # macOS, /var is a link to /private/var).
    tmp = tempfile.TemporaryDirectory()
    tmpdir = Path(os.path.realpath(tmp.name))
    names = {"@ROOT@": ROOT, "@TMPDIR@": str(tmpdir), "@TMP@": tmpdir.as_uri(), "@VERSION@": version}
    session = Session(exe)
    try:
        status, patterns = 0, []
        for line in lines:
            if not line.strip() or line.startswith("#"):
                continue
            step = json.loads(line)
            (kind, value), = step.items()
            if kind == "send":
                session.send(substitute(value, names))
            elif kind == "recv":
                got = session.receive()
                assert got == substitute(value, names), ("unexpected message", got)
            elif kind == "write":
                file = tmpdir / value[0]
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(value[1], encoding="utf-8")
                session.transcript.append(("write", value[0]))
            elif kind == "remove":
                (tmpdir / value).unlink()
                session.transcript.append(("remove", value))
            elif kind == "stderr":
                patterns.append(value)
            elif kind == "status":
                status = value
            else:
                raise AssertionError(f"unknown step {kind}")
        assert session.finish() == status, ("exit status", session.proc.returncode)
        logs = session.logs()
        for pattern in patterns:
            assert re.search(pattern, logs), ("stderr does not match", pattern)
    except BaseException:
        print(f"FAIL: {path.name}", file=sys.stderr)
        for kind, message in session.transcript:
            print(f"  {kind} {json.dumps(message)}", file=sys.stderr)
        print("  stderr:", session.logs(), sep="\n", file=sys.stderr)
        raise
    finally:
        session.close()
        tmp.cleanup()


def main():
    args = sys.argv[1:]
    exe = Path(args.pop(0)).resolve()
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True).stdout.split()[1]
    cases = [DIR / f"{n}.jsonl" for n in args] or sorted(DIR.glob("*.jsonl"))
    for case in cases:
        run(exe, case, version)
    print(f"diagnostics: {len(cases)} transcripts passed")


if __name__ == "__main__":
    main()
