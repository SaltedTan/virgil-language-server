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

The server must run with the Java options adopted in #463, and the worker
with only -Xmx of the build's WORKER_HEAP: --worker-heap, 256m by default.
A packaged server's manifest.json must record that heap as its workerHeap.

Run from the repository root, after `make jvm`:

  python3 test/jvm/smoke.py build/jvm-target/virgil-lsp [--worker-heap=SIZE]

To build in a Unicode directory and check both launchers with an empty
environment:

  python3 test/jvm/smoke.py --unicode-build [--worker-heap=SIZE]
"""

import argparse
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
SERVER_OPTIONS = ["-Xmx128m", "-XX:+UseSerialGC"]


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


def java_options(args, name):
    """The Java options of a launcher's command line, as ps shows it: the
    options after the java executable and before the launcher's own
    -Dvirgil.lsp.executable=<path of name>, which comes just before -jar."""
    head, marker, tail = args.partition(" -Dvirgil.lsp.executable=")
    assert marker and tail.partition(" -jar ")[0].endswith("/" + name), args
    words = head.split(" ")
    options = []
    while words and words[-1].startswith("-"):
        options.insert(0, words.pop())
    return options


def main(exe, worker_heap, **popen):
    exe = Path(exe).resolve()
    manifest = exe.parent / "manifest.json"
    if manifest.exists():
        assert json.loads(manifest.read_text())["workerHeap"] == worker_heap, manifest
    times = {}
    with tempfile.TemporaryDirectory(prefix="virgil-jvm-smoke-") as directory:
        path = Path(directory) / "main.v3"
        path.write_text(SOURCE)
        uri = path.as_uri()
        start = time.monotonic()
        s = Session(exe, **popen)
        try:
            s.initialize()
            times["initialize_s"] = round(time.monotonic() - start, 3)
            server_args = processes()[s.proc.pid][1]
            assert "java" in server_args and "virgil-lsp.jar" in server_args, server_args
            assert java_options(server_args, "virgil-lsp") == SERVER_OPTIONS, server_args

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
            assert "java" in worker_args, worker_args
            assert java_options(worker_args, "virgil-lsp-worker") == ["-Xmx" + worker_heap], worker_args

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
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", nargs="?")
    parser.add_argument("--unicode-build", action="store_true")
    parser.add_argument("--worker-heap", default="256m")
    options = parser.parse_args()
    if options.unicode_build == (options.exe is not None):
        parser.error("give the server's launcher or --unicode-build")
    if options.unicode_build:
        Path("build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="jvm-π-", dir="build") as directory:
            subprocess.run(["make", "-j2", "V3C_TARGET=jvm", f"BUILD={directory}",
                            f"WORKER_HEAP={options.worker_heap}", "all"], check=True)
            main(Path(directory) / "virgil-lsp", options.worker_heap)
            main(Path(directory) / "virgil-lsp", options.worker_heap, env={})
    else:
        main(options.exe, options.worker_heap)
