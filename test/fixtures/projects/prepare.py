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
for name in ["ownership-reassignment", "ownership-removal", "ownership-project-transfer"]:
    base = root / name
    (base / "shared").mkdir(parents=True, exist_ok=True)
    projects = [{"name": "parent", "sources": ["main.v3", "shared/Greeting.v3"]}]
    if name == "ownership-project-transfer":
        projects.append({"name": "closed", "sources": ["shared/Other.v3"]})
    (base / ".virgil-lsp.json").write_text(json.dumps({"version": 1, "projects": projects}), encoding="utf-8")
    (base / "main.v3").write_text("component Main { def main() -> int { return 0; } }\n", encoding="utf-8")
    (base / "shared" / "Greeting.v3").write_text(
        "component Greeting { def text(who: string) -> string { return missing; } }\n", encoding="utf-8",
    )
    (base / "shared" / "Other.v3").write_text("def other = 1;\n", encoding="utf-8")
    if name == "ownership-reassignment":
        (base / "shared" / ".virgil-lsp.json").write_text(
            '{"version":1,"projects":[{"name":"nested","sources":["Greeting.v3","Other.v3"]}]}',
            encoding="utf-8",
        )
for name, config in [("ownership-hidden-empty", '{"version":1,"projects":[]}'), ("ownership-hidden-invalid", "{")]:
    base = root / name
    (base / ".hidden").mkdir(parents=True, exist_ok=True)
    (base / ".virgil-lsp.json").write_text(
        '{"version":1,"projects":[{"name":"parent","sources":["main.v3",".hidden/A.v3"]}]}',
        encoding="utf-8",
    )
    (base / "main.v3").write_text("component Main { def main() -> int { return 0; } }\n", encoding="utf-8")
    (base / ".hidden" / "A.v3").write_text("def hidden = missing;\n", encoding="utf-8")
    (base / ".hidden" / ".virgil-lsp.json").write_text(config, encoding="utf-8")
# LspAnalyzedTextTest writes, changes, and deletes the sources itself.
analyzed_text = root / "analyzed-text"
analyzed_text.mkdir(exist_ok=True)
(analyzed_text / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"p","sources":["*.v3"]}]}',
    encoding="utf-8",
)
# LspDefinitionTest writes and changes the sources itself.
definition = root / "definition"
definition.mkdir(exist_ok=True)
(definition / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"d","sources":["*.v3"]}]}',
    encoding="utf-8",
)
# LspHoverTest writes and changes the sources itself.
hover = root / "hover"
hover.mkdir(exist_ok=True)
(hover / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"h","sources":["*.v3"]}]}',
    encoding="utf-8",
)
# LspSignatureHelpTest writes and changes the sources itself.
signature_help = root / "signature-help"
signature_help.mkdir(exist_ok=True)
(signature_help / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"g","sources":["*.v3"]}]}',
    encoding="utf-8",
)
# LspDocumentSymbolsTest writes and changes the sources itself.
symbols = root / "document-symbols"
symbols.mkdir(exist_ok=True)
(symbols / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"s","sources":["*.v3"]}]}',
    encoding="utf-8",
)
# LspWorkspaceSymbolsTest writes the project file and the sources itself. It
# also loads configurations under a/ and z/ in that reverse order, and removes
# them afterwards; recreate them so a failed run cannot leave them behind.
workspace_symbols = root / "workspace-symbols"
for name in ["one", "two", "shared", "lib"]:
    (workspace_symbols / name).mkdir(parents=True, exist_ok=True)
for name in ["a", "z"]:
    shutil.rmtree(workspace_symbols / name, ignore_errors=True)
    (workspace_symbols / name).mkdir(parents=True, exist_ok=True)
# LspReferencesTest writes and changes the sources itself. They are analyzed in
# this order, which is not their paths' byte order.
references = root / "references"
references.mkdir(exist_ok=True)
(references / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"r","sources":["Refs.v3","Decl.v3"]}]}',
    encoding="utf-8",
)
# LspDefinitionTest writes the sources, and opens sub/.virgil-lsp.json only as
# an unsaved buffer, so that a nearer project takes sub/Use.v3 over.
ownership = root / "definition-ownership"
(ownership / "sub").mkdir(parents=True, exist_ok=True)
(ownership / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"outer","sources":["Old.v3","sub/Use.v3"]}]}',
    encoding="utf-8",
)
# LspPrepareRenameTest writes and changes the sources itself. Shared.v3 adds a
# same-named file-scope function in another file of the project.
rename = root / "rename"
rename.mkdir(exist_ok=True)
(rename / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"r","sources":["*.v3"]}]}',
    encoding="utf-8",
)
(rename / "Shared.v3").write_text("def fileShared(p: int) -> int { return p + 1; }\n", encoding="utf-8")
rename_multi = root / "rename-multi"
rename_multi.mkdir(exist_ok=True)
(rename_multi / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"m","sources":["Main.v3","Other.v3"]}]}',
    encoding="utf-8",
)
rename_deps = root / "rename-deps"
(rename_deps / "dep").mkdir(parents=True, exist_ok=True)
(rename_deps / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"d","sources":["Main.v3","Use.v3"],'
    '"dependencies":["dep/*.v3"]}]}',
    encoding="utf-8",
)
rename_shared = root / "rename-shared"
rename_shared.mkdir(exist_ok=True)
(rename_shared / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":['
    '{"name":"one","sources":["Decl.v3","Use.v3","SharedUse.v3","SharedDecl.v3"]},'
    '{"name":"two","sources":["SharedUse.v3","SharedDecl.v3"]}]}',
    encoding="utf-8",
)
# LspPrepareRenameTest creates and removes sources here without a file event
# first, then reports the event.
rename_scan = root / "rename-scan"
rename_scan.mkdir(exist_ok=True)
(rename_scan / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"s","sources":["*.v3"]}]}',
    encoding="utf-8",
)
for stale in [rename_scan / "Main.v3", rename_scan / "Other.v3"]:
    if stale.exists():
        stale.unlink()
# The scan variant of the shared-programs case: the root program selects
# one/SharedUse.v3, which the test creates after both configurations are
# loaded. Pattern segments cannot be "..", so the second program is the root.
rename_shared_scan = root / "rename-shared-scan"
(rename_shared_scan / "one").mkdir(parents=True, exist_ok=True)
(rename_shared_scan / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"two","sources":["one/SharedUse.v3"]}]}',
    encoding="utf-8",
)
(rename_shared_scan / "one" / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"one","sources":["Main.v3"]}]}',
    encoding="utf-8",
)
for stale in [rename_shared_scan / "one" / "SharedUse.v3", rename_shared_scan / "Overflow.v3"]:
    if stale.exists():
        stale.unlink()
# Twelve programs beside the renamed one, each selecting its own directory's
# 256 sources, and one more whose pattern names Main.v3 below its directory.
rename_many = root / "rename-many"
rename_many.mkdir(exist_ok=True)
(rename_many / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"main","sources":["*.v3"]}]}',
    encoding="utf-8",
)
for index in range(12):
    directory = rename_many / f"c{index:02d}"
    directory.mkdir(exist_ok=True)
    (directory / ".virgil-lsp.json").write_text(
        json.dumps({"version": 1, "projects": [{"name": f"c{index:02d}", "sources": ["**/*.v3"]}]}),
        encoding="utf-8",
    )
    for file in range(256):
        source = directory / f"F{file:03d}.v3"
        if not source.exists():
            source.write_text(f"def value{file} = {index};\n", encoding="utf-8")
(rename_many / "named").mkdir(exist_ok=True)
(rename_many / "named" / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"named","sources":["Main.v3","**/Other.v3"]}]}',
    encoding="utf-8",
)
(rename_many / "named" / "Main.v3").write_text("def named = 1;\n", encoding="utf-8")
# The ownership budget. ordinary/ is shaped like the Aeneas measurement in
# docs/architecture.md: a program under the Virgil root beside 127 others with
# five ordinary patterns each. pathological/ has one program whose 2,000
# patterns select nothing, above a program in active/. The tests match
# synthetic paths, so active programs need no files.
ownership = root / "rename-ownership"
ordinary_aeneas = ownership / "ordinary" / "virgil" / "aeneas"
ordinary_aeneas.mkdir(parents=True, exist_ok=True)
(ordinary_aeneas / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"aeneas","sources":["src/*/*.v3"]}]}',
    encoding="utf-8",
)
for index in range(127):
    directory = ownership / "ordinary" / "others" / f"c{index}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".virgil-lsp.json").write_text(
        json.dumps({"version": 1, "projects": [{
            "name": f"c{index}", "sources": ["**/*.v3", "src/*/*.v3"], "dependencies": ["lib/*.v3"],
            "exclude": ["build/**"], "virgilDependencies": ["lib/none/*.v3"],
        }]}),
        encoding="utf-8",
    )
    (directory / "A.v3").write_text(f"def value = {index};\n", encoding="utf-8")
(ownership / "pathological" / "active").mkdir(parents=True, exist_ok=True)
(ownership / "pathological" / ".virgil-lsp.json").write_text(
    json.dumps({"version": 1, "projects": [{"name": "patterns", "sources": [f"nope/N{i:04d}.v3" for i in range(2000)]}]}),
    encoding="utf-8",
)
(ownership / "pathological" / "active" / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"active","sources":["*.v3"]}]}',
    encoding="utf-8",
)
# colliding/ has 32 programs of 128 sources each below a long shared prefix,
# beside a program in active/. Every directory and file name is a string of
# "Aa" and "BB" blocks of one length, which hash alike under a polynomial
# string hash, so a map keyed by the 4,096 paths would chain them all in one
# bucket. Expansion uses OrderedStringSet; see src/workspace/ProjectExpansion.v3.
def blocks(count, index):
    return "".join("BB" if index >> bit & 1 else "Aa" for bit in range(count))


colliding = ownership / "colliding" / ("d" * 200) / ("d" * 200) / ("d" * 200)
(colliding / "active").mkdir(parents=True, exist_ok=True)
(colliding / "active" / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"active","sources":["*.v3"]}]}',
    encoding="utf-8",
)
(colliding / ".virgil-lsp.json").write_text(
    json.dumps({"version": 1, "projects": [
        {"name": f"o{index}", "sources": [f"{blocks(5, index)}/*.v3"]} for index in range(32)
    ]}),
    encoding="utf-8",
)
for index in range(32):
    directory = colliding / blocks(5, index)
    directory.mkdir(exist_ok=True)
    for file in range(128):
        source = directory / f"{blocks(7, file)}.v3"
        if not source.exists():
            source.write_text(f"def value{file} = {index};\n", encoding="utf-8")
# ProjectExpansionTest expands 1,024 files whose 20-byte names are strings of
# "Aa" and "BB" blocks, which all hash alike, and 1,024 ordinary names of the
# same length, below a 600-byte prefix.
expansion_names = root / "expansion-names" / ("d" * 200) / ("d" * 200) / ("d" * 200)
for kind in ["colliding", "ordinary"]:
    directory = expansion_names / kind
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(1024):
        name = blocks(10, index) if kind == "colliding" else f"N{10000 + index}{'x' * 14}"
        (directory / f"{name}.v3").touch()
# LspPrepareRenameTest checks the membership of a declaration's 1,000 use files
# in a program of 1,002 sources, one of which is a dependency.
rename_members = root / "rename-members"
(rename_members / "use").mkdir(parents=True, exist_ok=True)
(rename_members / "dep").mkdir(exist_ok=True)
(rename_members / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"m","sources":["Main.v3","use/*.v3"],"dependencies":["dep/*.v3"]}]}',
    encoding="utf-8",
)
(rename_members / "Main.v3").write_text("class Widget { }\nclass Gadget { }\n", encoding="utf-8")
(rename_members / "dep" / "Lib.v3").write_text(
    "component Lib { def get(g: Gadget) -> int { return 0; } }\n", encoding="utf-8",
)
for index in range(1000):
    source = rename_members / "use" / f"U{index:04d}.v3"
    if not source.exists():
        source.write_text(
            f"component U{index:04d} {{ def get(g: Gadget) -> Widget {{ return Widget.new(); }} }}\n",
            encoding="utf-8",
        )
(root / "rename-no-project").mkdir(exist_ok=True)
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
