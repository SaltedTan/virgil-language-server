#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Record actual compile inputs/options, alongside the two executables."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

build, heap, bench, target, *sources = sys.argv[1:]
compiler = subprocess.check_output(["scripts/v3c.sh", "-print-compiler"], text=True).strip()
flags = ["-redef-field=BenchProbe.enabled=true,RiGc.stats=true"] if bench == "1" else []
commands = {}
for exe, entry, extra in (("virgil-lsp", "src/main.v3", []),
                           ("virgil-lsp-worker", "src/worker/WorkerMain.v3", ["-heap-size=" + heap])):
    commands[exe] = ["scripts/v3c.sh", *flags, *extra, "-output=" + build, "-program-name=" + exe, entry, *sources]
info = {"schema": 1,
        "server_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--short"], text=True).strip(), "benchmark_probes": bench == "1", "target": target, "worker_heap": heap,
        "server_heap": "runtime default (200 MiB at the pinned revision)",
        "compiler": {"path": str(Path(compiler).resolve()),
                     "sha256": hashlib.sha256(Path(compiler).read_bytes()).hexdigest()},
        "commands": commands,
        "source_sha256": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                          for p in ["src/main.v3", "src/worker/WorkerMain.v3", *sources]}}
(Path(build) / "benchmark-build.json").write_text(json.dumps(info, indent=2) + "\n")
