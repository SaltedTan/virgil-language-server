#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Smoke test for the JVM target's server and worker, built by `make jvm`.

Starts the server's launcher, completes initialize, and asks for an analysis
of a file with a type error, which only the worker can report. While the
server runs, the worker must be a JVM process of its own, a child of the
server's. Then shutdown and exit must end the server with status 0, and the
worker with it. Standard output must hold only framed protocol messages, and
standard error no Java exception. Prints the times it took, as JSON, to
standard output. Every receive has a deadline.

Run from the repository root, after `make jvm`:

  python3 test/jvm/smoke.py build/jvm-target/virgil-lsp
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "protocol"))
from worker import Session  # noqa: E402


# A cold JVM worker is slower than a native one; see #463.
TIMEOUT = 60
SOURCE = "def main() -> int {\n\tvar x: int = true;\n\treturn x;\n}\n"


def processes():
    """Each process's parent and command line, by process ID."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,args="], capture_output=True,
                         text=True, check=True).stdout
    table = {}
    for line in out.splitlines():
        fields = line.split(None, 2)
        if len(fields) >= 2:
            table[int(fields[0])] = (int(fields[1]), fields[2] if len(fields) == 3 else "")
    return table


def workers(server):
    return [pid for pid, (parent, args) in processes().items()
            if parent == server and "virgil-lsp-worker.jar" in args]


def main():
    exe = Path(sys.argv[1]).resolve()
    times = {}
    with tempfile.TemporaryDirectory(prefix="virgil-jvm-smoke-") as directory:
        path = Path(directory) / "main.v3"
        path.write_text(SOURCE)
        uri = path.as_uri()
        start = time.monotonic()
        s = Session(exe)
        try:
            s.initialize()
            times["initialize_s"] = round(time.monotonic() - start, 3)
            server_args = processes()[s.proc.pid][1]
            assert "java" in server_args and "virgil-lsp.jar" in server_args, server_args

            s.send("initialized", {}, request=False)

            analyzed = time.monotonic()
            result = s.result(s.analyze([uri]), TIMEOUT)
            times["first_analysis_s"] = round(time.monotonic() - analyzed, 3)
            diagnostics = result["diagnostics"]
            assert result["generation"] == 1 and len(diagnostics) == 1, result
            assert diagnostics[0]["kind"] == "TypeError" and diagnostics[0]["beginLine"] == 2, result
            assert diagnostics[0]["path"] == str(path), result

            children = workers(s.proc.pid)
            assert len(children) == 1, ("expected one JVM worker", processes())
            worker = children[0]
            worker_args = processes()[worker][1]
            assert "java" in worker_args and "-Xmx" in worker_args, worker_args

            again = time.monotonic()
            assert s.result(s.analyze([uri]), TIMEOUT) == {**result, "generation": 2}
            times["second_analysis_s"] = round(time.monotonic() - again, 3)

            # Session.shutdown also checks for exit status 0 and that nothing
            # but framed messages reached standard output.
            s.shutdown()
            times["total_s"] = round(time.monotonic() - start, 3)
            deadline = time.monotonic() + 10
            while worker in processes() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert worker not in processes(), "the worker outlived the server"
            logs = s.logs()
            for text in ("Exception", "Error:", "at V3S_System"):
                assert text not in logs, logs
        except BaseException:
            print("transcript:", json.dumps(s.transcript, indent=1), file=sys.stderr)
            print("server stderr:", s.logs(), file=sys.stderr)
            raise
        finally:
            s.close()
    print(json.dumps(times))
    print("jvm smoke: initialize, worker analysis, shutdown, and exit passed")


if __name__ == "__main__":
    main()
