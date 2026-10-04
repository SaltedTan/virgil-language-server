#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Create host-filesystem cases (including sparse files and FIFOs) for both CI hosts."""
import json
import os
from pathlib import Path
import socket
import shutil
import sys

root = Path(sys.argv[1]) / "project-fixtures"
if len(sys.argv) == 3 and sys.argv[2] == "--cleanup":
    for relative in ["unreadable-directories/blocked", "ignored-entries/build"]:
        directory = root / relative
        if not directory.is_symlink() and directory.is_dir():
            directory.chmod(0o700)
    sys.exit(0)
closed_shared = root / "closed-shared"
shutil.copytree(Path(__file__).parent / "two-programs", closed_shared, dirs_exist_ok=True)
shared = closed_shared / "shared" / "Greeting.v3"
shared.write_text(shared.read_text().replace("return who;", "return missing;"), encoding="utf-8")
(root / "safe").mkdir(parents=True, exist_ok=True)
(root / "repository-root").write_text(str(Path(__file__).absolute().parents[3]), encoding="utf-8")
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

special_fifo = root / "safe" / "fifo.v3"
if not special_fifo.exists():
    os.mkfifo(special_fifo)
special_fifo.chmod(0)
special_socket = root / "safe" / "socket.v3"
if special_socket.exists():
    special_socket.unlink()
with socket.socket(socket.AF_UNIX) as endpoint:
    original_directory = Path.cwd()
    try:
        os.chdir(special_socket.parent)
        endpoint.bind(special_socket.name)
    finally:
        os.chdir(original_directory)
special_socket.chmod(0)

for case, projects, files, path_length in [
    ("aggregate-references", 8, 512, None),
    ("aggregate-paths", 12, 256, 682),
]:
    base = root / case
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(exist_ok=True)
    (base / "Spare.txt").write_text("def spare = 1;\n", encoding="utf-8")
    for side in ["A", "B"]:
        directory = base / side
        directory.mkdir(exist_ok=True)
        (directory / ".virgil-lsp.json").write_text(
            json.dumps({"version": 1, "projects": [
                {"name": f"p{index}", "sources": ["**/*.v3"]}
                for index in range(projects)
            ]}),
            encoding="utf-8",
        )
        remaining = 0 if path_length is None else path_length - len(str(directory.absolute())) - len("/F000.v3")
        while remaining > 0:
            length = remaining - 1 if remaining <= 201 else min(200, remaining - 3)
            directory /= "d" * length
            remaining -= length + 1
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(files - (side == "B")):
            (directory / f"F{index:03d}.v3").write_text("def value = 1;\n", encoding="utf-8")
        if side == "B":
            (directory / "Move.v3").write_text("def move = 1;\n", encoding="utf-8")

unreadable_files = root / "unreadable-files"
unreadable_files.mkdir(exist_ok=True)
for name in ["A.v3", "B.v3"]:
    path = unreadable_files / name
    if path.exists():
        path.chmod(0o600)
    path.write_text("def value = 1;\n", encoding="utf-8")
(unreadable_files / "B.v3").chmod(0)
(unreadable_files / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"p","sources":["*.v3"]}]}',
    encoding="utf-8",
)
unreadable_directories = root / "unreadable-directories"
unreadable_directories.mkdir(exist_ok=True)
for name in ["readable", "blocked"]:
    directory = unreadable_directories / name
    directory.mkdir(exist_ok=True)
    directory.chmod(0o700)
    (directory / "A.v3").write_text("def value = 1;\n", encoding="utf-8")
(unreadable_directories / "blocked").chmod(0)

ignored_entries = root / "ignored-entries"
ignored_entries.mkdir(exist_ok=True)
for name in ["A.v3", "B.v3", "README.md"]:
    path = ignored_entries / name
    if path.exists():
        path.chmod(0o600)
    path.write_text("def value = 1;\n", encoding="utf-8")
for name in ["B.v3", "README.md"]:
    (ignored_entries / name).chmod(0)
excluded_directory = ignored_entries / "build"
excluded_directory.mkdir(exist_ok=True)
excluded_directory.chmod(0o700)
(excluded_directory / "Generated.v3").write_text("def generated = 1;\n", encoding="utf-8")
excluded_directory.chmod(0)

unrelated_files = root / "unrelated-files"
unrelated_files.mkdir(exist_ok=True)
for name in ["A.v3", "README.md"]:
    path = unrelated_files / name
    if path.exists():
        path.chmod(0o600)
    path.write_text("def value = 1;\n", encoding="utf-8")
(unrelated_files / "README.md").chmod(0)
