#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Create host-filesystem cases (including sparse files and FIFOs) for both CI hosts."""
import os
from pathlib import Path
import sys

root = Path(sys.argv[1]) / "project-fixtures"
(root / "safe").mkdir(parents=True, exist_ok=True)
(root / "safe" / "A.v3").write_text("def a = 1;\n", encoding="utf-8")
with (root / "large.v3").open("wb") as output:
    output.truncate(4 * 1024 * 1024 + 1)
for name, target in [
    ("safe/link.v3", "A.v3"),
    ("safe/cycle", "."),
    ("safe/.virgil-lsp.json", "A.v3"),
    ("linked", "safe"),
]:
    path = root / name
    if path.is_symlink():
        path.unlink()
    path.symlink_to(target)
fifo = root / "fifo.v3"
if not fifo.exists():
    os.mkfifo(fifo)
deep = root / "deep"
for _ in range(65):
    deep /= "d"
deep.mkdir(parents=True, exist_ok=True)
(deep / "Deep.v3").write_text("def deep = 1;\n", encoding="utf-8")

wide = root / "scan-a"
wide.mkdir(exist_ok=True)
for index in range(10001):
    (wide / f"entry-{index}").touch()
other = root / "scan-b"
other.mkdir(exist_ok=True)
(other / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"p","sources":["*.v3"]}]}',
    encoding="utf-8",
)
(other / "B.v3").write_text("def b = 1;\n", encoding="utf-8")
