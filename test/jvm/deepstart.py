#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Regression test for the JVM parser stack guard's deep-start case (#461).

A parse that starts deep in the thread's stack leaves little headroom below
the 1,024-frame cap of Thread.getStackTrace(), so the guard's growth reading
is short and the guard must fire early (failing closed) rather than let the
parse overflow the thread's stack. This test starts the server from 600
recursive frames deep, through a small Java wrapper that calls the server's
main, then opens a document nested far beyond the thread's default 1 MB
stack. The server must answer with the established `nesting too deep; parsing
stopped here` diagnostic and stay alive, with no StackOverflowError, on the
JVM's default stack (no -Xss).

Run from the repository root, after `make jvm`:

  python3 test/jvm/deepstart.py build/jvm-target/virgil-lsp.jar

The wrapper is compiled with the JDK's javac, from the JAVA the Makefile
exports; set JAVA to a JDK if `java` is a JRE.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

# The wrapper calls the server's main from this many recursive frames deep,
# past the guard's 512-frame threshold, so the guard's growth reading is
# short and it must fail closed.
BASE_FRAMES = 600
# Nested brackets far beyond the thread's default 1 MB stack: the unguarded
# parse overflows at about 960 brackets, so this is well past it.
BRACKETS = 10000

WRAPPER = """\
public class DeepStart {
    static void at(int n, String[] a) throws Exception {
        if (n == 0) V3K_VirgilLsp.main(a); else at(n - 1, a);
    }
    public static void main(String[] a) throws Exception { at(%d, a); }
}
""" % BASE_FRAMES


def frame(msg):
    b = json.dumps(msg, separators=(",", ":")).encode()
    return b"Content-Length: %d\r\n\r\n" % len(b) + b


def main(jar):
    jar = Path(jar).resolve()
    java = subprocess.run(["java", "-version"], capture_output=True).returncode == 0 and "java" or None
    # The Makefile exports JAVA; use it to find a javac.
    env_java = subprocess.run(["sh", "-c", "echo $JAVA"], capture_output=True, text=True).stdout.strip()
    javac = [env_java, "-m", "jdk.compiler/com.sun.tools.javac.Main"] if env_java else ["javac"]
    with tempfile.TemporaryDirectory(prefix="virgil-jvm-deepstart-") as directory:
        d = Path(directory)
        (d / "DeepStart.java").write_text(WRAPPER)
        subprocess.run(javac + ["-cp", str(jar), "-d", str(d), str(d / "DeepStart.java")], check=True)
        text = "def x = " + "(" * BRACKETS + "1" + ")" * BRACKETS + ";"
        inp = (frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"capabilities": {}}})
               + frame({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                        "params": {"textDocument": {"uri": "u", "languageId": "virgil", "version": 1, "text": text}}})
               + frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
               + frame({"jsonrpc": "2.0", "method": "exit"}))
        java_bin = env_java.split()[-1] if env_java else "java"
        p = subprocess.run([java_bin, "-cp", str(d) + ":" + str(jar), "DeepStart", "--stdio"],
                           input=inp, capture_output=True, timeout=120)
        stderr = p.stderr.decode("utf-8", "replace")
        stdout = p.stdout.decode("utf-8", "replace")
        assert p.returncode == 0, ("server exited %d" % p.returncode, stderr)
        assert "StackOverflowError" not in stderr, ("the deep-start parse overflowed", stderr)
        assert "nesting too deep" in stdout, ("no nesting diagnostic", stdout)
    print("jvm deepstart: a parse from %d frames deep stopped with the nesting" % BASE_FRAMES)
    print("diagnostic and no StackOverflowError on the default stack")


if __name__ == "__main__":
    main(sys.argv[1])
