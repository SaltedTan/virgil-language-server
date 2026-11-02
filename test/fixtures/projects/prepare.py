#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Create host-filesystem cases (including sparse files and FIFOs) for both CI hosts.

The two directories that must be unreadable stay writable until --restrict;
--run applies it for the duration of a command and restores it afterwards, and
--cleanup restores it on its own.
"""
import json
import os
from pathlib import Path
import select
import shlex
import signal
import socket
import shutil
import struct
import subprocess
import sys
import time

build = Path(sys.argv[1])
root = build / "project-fixtures"
restricted = ["unreadable-directories/blocked", "ignored-entries/build"]


def restrict():
    for relative in restricted:
        directory = root / relative
        if directory.is_symlink() or not directory.is_dir():
            sys.exit(f"error: {directory} is missing; prepare the fixtures with prepare.py {build} first")
        directory.chmod(0)


def restore():
    for relative in restricted:
        directory = root / relative
        if not directory.is_symlink() and directory.is_dir():
            directory.chmod(0o700)


def _sleb(value):
    """Encodes {value} as signed LEB128."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        last = (value == 0 and not (byte & 0x40)) or (value == -1 and (byte & 0x40))
        if not last:
            byte |= 0x80
        out.append(byte)
        if last:
            return bytes(out)


def _read_sleb(data, i):
    result = 0
    shift = 0
    while True:
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            if byte & 0x40:
                result |= -1 << shift
            break
    return result, i


def _read_string(data, i):
    length, i = _read_sleb(data, i)
    if length < 0:
        return None, i
    return data[i:i + length].decode("utf-8"), i + length


def _parse_handshake(frame):
    """The (protocol, version, revision, heap) fields of a HELLO frame."""
    (length,) = struct.unpack("<I", frame[:4])
    payload = frame[4:4 + length]
    if payload[0] != 1:
        raise SystemExit("the captured frame is not a HELLO handshake")
    i = 1
    protocol, i = _read_sleb(payload, i)
    version, i = _read_string(payload, i)
    revision, i = _read_string(payload, i)
    heap, i = _read_sleb(payload, i)
    if i != len(payload):
        raise SystemExit("trailing bytes in the captured handshake")
    return protocol, version, revision, heap


def _encode_handshake(protocol, version, revision, heap):
    payload = bytearray([1])
    payload += _sleb(protocol)
    payload += _sleb(len(version)) + version.encode("utf-8")
    payload += _sleb(len(revision)) + revision.encode("utf-8")
    payload += _sleb(heap)
    return struct.pack("<I", len(payload)) + bytes(payload)


def _capture_handshake(worker, timeout):
    """Runs {worker} --serve, reads its HELLO frame, and kills it."""
    request_read, request_write = os.pipe()
    result_read, result_write = os.pipe()
    devnull = os.open(os.devnull, os.O_RDWR)
    pid = os.fork()
    if pid == 0:
        os.dup2(devnull, 0)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        os.dup2(request_read, 3)
        os.dup2(result_write, 4)
        # Close the source descriptors, but never the two just wired up: the
        # pipe ends can sit at fd 3 or 4, where we just pointed them.
        for fd in (devnull, request_read, request_write, result_read, result_write):
            if fd not in (3, 4):
                os.close(fd)
        os.execv(worker, [worker, "--serve"])
        os._exit(127)
    os.close(request_write)
    os.close(result_write)
    os.close(devnull)
    # One deadline for the whole handshake, so a worker that writes slowly
    # can't stretch the timeout by sending a byte at a time.
    deadline = time.monotonic() + timeout
    try:
        def _read_exact(n):
            data = b""
            while len(data) < n:
                ready, _, _ = select.select([result_read], [], [], max(0.0, deadline - time.monotonic()))
                if not ready:
                    raise SystemExit(f"the worker sent no handshake within {timeout:.0f}s")
                chunk = os.read(result_read, n - len(data))
                if not chunk:
                    raise SystemExit("the worker closed its result pipe before its handshake")
                data += chunk
            return data
        header = _read_exact(4)
        (length,) = struct.unpack("<I", header)
        return header + _read_exact(length)
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        os.close(request_read)
        os.close(result_read)


# The body of each fake worker. prepare.py substitutes __FRAME__ with the
# captured handshake, one field changed, as a bytes literal.
_FAKE_WORKER_PY = """\
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
# Generated by test/fixtures/projects/prepare.py into the build directory; do
# not edit. A fake analysis worker: it sends the real worker's handshake with
# exactly one field of its build identity changed, so the supervisor's
# matching check for that field is exercised on its own. The supervisor must
# refuse it. See test/unit/AnalysisSupervisorTest.v3.
import os
import time

FRAME = __FRAME__
RESULT_FD = 4  # AnalysisWire.RESULT_FD
WATCHDOG_SECONDS = 60.0


def main():
    parent = os.getppid()
    deadline = time.monotonic() + WATCHDOG_SECONDS
    os.write(RESULT_FD, FRAME)
    while os.getppid() == parent and time.monotonic() < deadline:
        time.sleep(0.1)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
"""


def _generate_worker_fakes(build):
    """Captures the real worker's handshake and generates the fake workers.

    Called from the --run and --restrict paths, which run after the worker is
    built. The plain project-fixtures path must not call this: it runs before
    the worker exists, so a clean build would capture nothing.
    """
    worker = build / "virgil-lsp-worker"
    if not worker.exists():
        # No worker means no fakes are needed (e.g. fixture-cleanup tests that
        # build a directory without a worker). The tests that do need the
        # fakes run after a full build, where the worker is present.
        return
    # The runner's scratch slots put a stand-in (a text file containing
    # "worker") at this path to test slot inode distinctness. Only capture
    # from a real executable; the marker check works on every platform
    # (ELF on Linux, Mach-O on macOS) without depending on file format.
    if worker.read_bytes() == b"worker":
        return
    real = _capture_handshake(str(worker), 30.0)
    protocol, version, revision, heap = _parse_handshake(real)
    worker_fakes = build / "worker-fakes"
    worker_fakes.mkdir(exist_ok=True)
    for field in ["protocol", "version", "revision"]:
        f_protocol, f_version, f_revision = protocol, version, revision
        if field == "protocol":
            f_protocol += 1
        elif field == "version":
            f_version = "0.0.0-test"
        elif field == "revision":
            f_revision = "test-revision"
        fake = _encode_handshake(f_protocol, f_version, f_revision, heap)
        # The fake must differ from the captured handshake in exactly this one
        # field, so every other byte (including the heap size) is untouched.
        if sum(a != b for a, b in zip((protocol, version, revision, heap), _parse_handshake(fake))) != 1:
            sys.exit(f"the {field} fake differs from the captured handshake in more than one field")
        py_path = worker_fakes / f"handshake-{field}.py"
        py_path.write_text(_FAKE_WORKER_PY.replace("__FRAME__", repr(fake)), encoding="utf-8")
        py_path.chmod(0o644)
        # A /bin/sh trampoline runs the Python body. The server spawns workers
        # with an empty environment, so the interpreter must be an absolute
        # path; both paths are quoted so a path with spaces works.
        trampoline = worker_fakes / f"handshake-{field}"
        trampoline.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(py_path.resolve())) + "\n",
            encoding="utf-8",
        )
        trampoline.chmod(0o755)


mode = sys.argv[2] if len(sys.argv) > 2 else None
if mode == "--cleanup":
    restore()
    sys.exit(0)
if mode == "--restrict":
    # Raise on a termination signal, so the capture's cleanup kills and reaps
    # the worker it started.
    def stop_restrict(signum, _frame):
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, stop_restrict)
    _generate_worker_fakes(build)
    restrict()
    sys.exit(0)
if mode == "--run":
    command = sys.argv[3:]
    if not command:
        sys.exit("usage: prepare.py BUILD --run COMMAND [ARGUMENT ...]")

    def stop(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    _generate_worker_fakes(build)
    try:
        restrict()
        status = subprocess.run(command).returncode
    except KeyboardInterrupt:
        status = 128 + signal.SIGINT
    finally:
        restore()
    sys.exit(128 - status if status < 0 else status)
if mode is not None:
    sys.exit(f"error: unknown option {mode}")
closed_shared = root / "closed-shared"
ownership_version = root / "ownership-version"
(ownership_version / "sub").mkdir(parents=True, exist_ok=True)
(ownership_version / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"parent","sources":["sub/main.v3"]}]}', encoding="utf-8",
)
(ownership_version / "sub" / "main.v3").write_text(
    "component Main { def main() -> int { return Shapes.answer(); } }\n", encoding="utf-8",
)
(ownership_version / "sub" / "shapes.v3").write_text(
    "component Shapes { def answer() -> bool { return true; } }\n", encoding="utf-8",
)
(ownership_version / "sub" / ".virgil-lsp.json").unlink(missing_ok=True)
for name in ["semantic-membership", "semantic-membership-running"]:
    base = root / name
    base.mkdir(parents=True, exist_ok=True)
    (base / ".virgil-lsp.json").write_text(
        '{"version":1,"projects":[{"name":"p","sources":["*.v3"]}]}', encoding="utf-8",
    )
    (base / "main.v3").write_text(
        "component Main { def main() -> int { return Shapes.answer(); } }\n", encoding="utf-8",
    )
    (base / "shapes.v3").unlink(missing_ok=True)
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
# Names whose URIs escape bytes. WorkspaceProjects:source_uris creates and
# deletes "new file.v3" itself.
source_uris = root / "source-uris"
source_uris.mkdir(exist_ok=True)
(source_uris / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"p","sources":["*.v3"]}]}',
    encoding="utf-8",
)
for name in ["plain.v3", "with space.v3", "caf\u00e9.v3", "100%.v3"]:
    (source_uris / name).write_text("def x = 1;\n", encoding="utf-8")
(source_uris / "new file.v3").unlink(missing_ok=True)
# LspDefinitionTest writes and changes the sources itself.
definition = root / "definition"
definition.mkdir(exist_ok=True)
semantic_token_baselines = root / "semantic-token-baselines"
semantic_token_baselines.mkdir(exist_ok=True)
for name in ["Main.v3", "Library.v3"]:
    (semantic_token_baselines / name).write_text("", encoding="utf-8")
(definition / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"d","sources":["*.v3"]}]}',
    encoding="utf-8",
)
# ProjectFiles:load writes the files it reads itself.
(root / "file-loads").mkdir(exist_ok=True)
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
# LspCompletionScopeTest and LspCompletionResolveTest write each test's
# project file and sources themselves.
(root / "completion").mkdir(exist_ok=True)
(root / "completion-resolve").mkdir(exist_ok=True)
# LspSignatureRepairTest writes each test's project file and sources itself.
(root / "signature-repair").mkdir(exist_ok=True)
# LspRenameTest writes each test's project file and sources itself. The
# Virgil root of rename-root holds a library that its project depends on, with
# a class whose method a project class overrides.
(root / "rename-edits").mkdir(exist_ok=True)
for name in ["a", "b", "c"]:
    (root / "rename-budget" / name).mkdir(parents=True, exist_ok=True)
rename_root = root / "rename-root"
(rename_root / "vroot" / "lib").mkdir(parents=True, exist_ok=True)
(rename_root / ".virgil-lsp.json").write_text(
    '{"version":1,"projects":[{"name":"r","sources":["Main.v3"],"virgilDependencies":["lib/*.v3"]}]}',
    encoding="utf-8",
)
(rename_root / "vroot" / "lib" / "Lib.v3").write_text(
    "component Lib { def twice(n: int) -> int { return n + n; } }\n"
    "class Root {\n\tdef draw() -> int { return 0; }\n}\n",
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
# PathMapsTest analyzes programs of a main file and 256 or 512 sources whose
# names are strings of "Aa" and "BB" blocks of one length, which all hash
# alike, below a 400-byte prefix, and publishes their diagnostics. The last
# source in byte order has an error.
for count in [256, 512]:
    program = root / "colliding-diagnostics" / str(count) / ("d" * 200) / ("d" * 200)
    program.mkdir(parents=True, exist_ok=True)
    (program / ".virgil-lsp.json").write_text(
        '{"version":1,"projects":[{"name":"p","sources":["*.v3"]}]}', encoding="utf-8",
    )
    (program / "main.v3").write_text("component Main { def m() { var x: Missing; } }\n", encoding="utf-8")
    for index in range(count):
        body = " var y: Absent;" if index == count - 1 else ""
        (program / f"{blocks(9, index)}.v3").write_text(
            f"component C{index} {{{body} }}\n", encoding="utf-8",
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
# ProjectExpansionTest's path_budget walks 95 empty directories with 255-byte
# names below a 200-byte one, so that each path stays far below macOS's
# 1,024-byte limit, and its discovery_depth discovers a project file 64
# directories down but not the one 65 down.
path_budget = root / "path-budget"
if path_budget.exists():
    shutil.rmtree(path_budget)
for index in range(95):
    (path_budget / ("d" * 200) / (f"{index:02d}" + "d" * 253)).mkdir(parents=True)
discovery_depth = root / "discovery-depth"
for depth in range(1, 66):
    discovery_depth /= "d"
    if depth >= 64:
        discovery_depth.mkdir(parents=True, exist_ok=True)
        (discovery_depth / ".virgil-lsp.json").write_text('{"version":1,"projects":[]}', encoding="utf-8")

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

unrelated_files = root / "unrelated-files"
unrelated_files.mkdir(exist_ok=True)
for name in ["A.v3", "README.md"]:
    path = unrelated_files / name
    if path.exists():
        path.chmod(0o600)
    path.write_text("def value = 1;\n", encoding="utf-8")
(unrelated_files / "README.md").chmod(0)

# AnalysisSupervisorTest spawns fake workers whose handshake reports a build
# identity that differs from the server's in one field. The supervisor must
# refuse each one, and each refusal test must fail when its comparison is
# removed. We generate three fakes, each of which sends the real worker's
# handshake with exactly one field changed.
#
# The real worker's handshake is captured once here, at fixture-generation
# time, so each fake matches the build under test and no fake ever spawns a
# child of its own: a fake that captured its handshake at run time would leave
# its inner worker alive if the fake were killed mid-startup, and a finally
# block cannot run after SIGKILL. prepare.py is a normal short-lived process,
# so it can run the real worker, read its handshake, and kill it safely.
#
# The capture runs in --run and --restrict, which the unit-test runner calls
# after make has built the worker, never in plain generation, which make runs
# before the worker is built or rebuilt. See _generate_worker_fakes.


