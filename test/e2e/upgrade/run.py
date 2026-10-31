#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Rehearse a Linux upgrade from one released version to the next and the
rollback to it, with the released archives and linux-x64 VSIX files.
See docs/releasing.md#upgrade-and-rollback-rehearsal for prerequisites.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ElementTree
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
HELPER = HERE / "vscode.cjs"
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"
ARCHIVE = re.compile(rf"virgil-lsp-({VERSION})-x86-64-linux\.tar\.gz")
VSIX = re.compile(r"virgil-lsp-vscode-([0-9]+\.[0-9]+\.[0-9]+)-linux-x64\.vsix")
SUMS = "SHA256SUMS"
REVISION = re.compile(r"[0-9a-f]{40}")
EXTENSION = "virgil-language-server.virgil-lsp-vscode"
VSIX_NAMESPACE = "{http://schemas.microsoft.com/developer/vsx-schema/2011}"
# The old release, the new one, then the old one again, in the same
# installation; the rollback VSIX needs --force.
PHASES = (("install", "from"), ("upgrade", "to"), ("rollback", "from"))
TRACKS = ("archive", "vscode")
# How long the server gets for each reply, and to exit after exit.
REPLY_TIMEOUT = 60
EXIT_TIMEOUT = 10
# The helper stops VS Code within its own bounds (see vscode.cjs); this is
# the outer bound for a phase, and the grace after SIGTERM before SIGKILL.
VSCODE_TIMEOUT = 600
STOP_GRACE = 60
DIAGNOSTIC = "expected int in var initialization, got Array<byte>"
FIXTURE = (
    "component Install {\n"
    "    def text() -> int {\n"
    "        var text: int = StringBuilder.new().toString();\n"
    "        return text;\n"
    "    }\n"
    "}\n")


class Failure(Exception):
    """A check that failed, with what was expected."""


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sums(path):
    """SHA256SUMS lines by file name; a malformed or repeated line is an error."""
    sums = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S.*)", line)
        if match is None:
            raise Failure(f"{path}:{number}: not a sha256sum line: {line!r}")
        if match[2] in sums:
            raise Failure(f"{path}:{number}: second line for {match[2]}")
        sums[match[2]] = match[1]
    return sums


def version_key(version):
    """Semantic-version precedence: a prerelease sorts before its release."""
    core, _, prerelease = version.partition("-")
    numbers = tuple(int(part) for part in core.split("."))
    if not prerelease:
        return numbers, (1,)
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in prerelease.split("."))
    return numbers, (0, parts)


def archive_manifest(path, version):
    """The archive's top directory, manifest.json, and executables' SHA-256s,
    after checking that every member is a regular file or directory under its
    top directory. Each executable is found by name wherever it is: the
    rehearsal itself checks that the server finds them in their places."""
    top = f"virgil-lsp-{version}-x86-64-linux"
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            parts = member.name.split("/")
            if parts[0] != top or ".." in parts or member.name.startswith("/"):
                raise Failure(f"{path.name}: member {member.name!r} is outside {top}/")
            if not (member.isfile() or member.isdir()):
                raise Failure(f"{path.name}: member {member.name!r} is not a regular file or directory")
        # The rehearsal itself checks where the executables and library are.
        if f"{top}/manifest.json" not in {member.name for member in members}:
            raise Failure(f"{path.name}: no {top}/manifest.json")
        data = archive.extractfile(f"{top}/manifest.json").read()
        executables = {}
        for name in ["virgil-lsp", "virgil-lsp-worker"]:
            found = [member for member in members if member.isfile() and member.name.split("/")[-1] == name]
            if len(found) != 1:
                raise Failure(f"{path.name}: expected one {name}, found {[member.name for member in found]}")
            executables[name] = hashlib.sha256(archive.extractfile(found[0]).read()).hexdigest()
    manifest = json.loads(data)
    expected = {"manifestVersion": 1, "name": "virgil-lsp", "version": version,
                "target": "x86-64-linux", "tag": f"v{version}", "dirty": False}
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise Failure(f"{path.name}: manifest {field} is {manifest.get(field)!r}, not {value!r}")
    for field in ["sourceRevision", "virgilRevision"]:
        if not REVISION.fullmatch(str(manifest.get(field))):
            raise Failure(f"{path.name}: manifest {field} is not a full commit SHA")
    if manifest.get("library", {}).get("revision") != manifest["virgilRevision"]:
        raise Failure(f"{path.name}: manifest library revision differs from virgilRevision")
    for name, digest in executables.items():
        if manifest.get("sha256", {}).get(name) != digest:
            raise Failure(f"{path.name}: its {name} is not its manifest's")
    return top, manifest, data, executables


def vsix_metadata(path, extension_version, manifest, manifest_bytes, executables):
    """Check the VSIX's identity, target, and that it bundles the archive's
    server: its manifest, and executables with the archive's contents."""
    with zipfile.ZipFile(path) as package:
        def read(name):
            try:
                return package.read(name)
            except KeyError:
                raise Failure(f"{path.name}: no {name}") from None
        identity = ElementTree.fromstring(read("extension.vsixmanifest")).find(
            f"{VSIX_NAMESPACE}Metadata/{VSIX_NAMESPACE}Identity")
        if identity is None:
            raise Failure(f"{path.name}: extension.vsixmanifest has no Identity")
        expected = {"Publisher": "virgil-language-server", "Id": "virgil-lsp-vscode",
                    "Version": extension_version, "TargetPlatform": "linux-x64"}
        for field, value in expected.items():
            if identity.get(field) != value:
                raise Failure(f"{path.name}: VSIX {field} is {identity.get(field)!r}, not {value!r}")
        package_json = json.loads(read("extension/package.json"))
        for field, value in [("publisher", "virgil-language-server"), ("name", "virgil-lsp-vscode"),
                             ("version", extension_version)]:
            if package_json.get(field) != value:
                raise Failure(f"{path.name}: package.json {field} is {package_json.get(field)!r}, not {value!r}")
        target = json.loads(read("extension/server-target.json"))
        if target != {"target": "linux-x64"}:
            raise Failure(f"{path.name}: server-target.json is {target!r}")
        if read("extension/server/manifest.json") != manifest_bytes:
            raise Failure(f"{path.name}: its bundled server/manifest.json is not the archive's "
                          f"(server {manifest['version']})")
        for name in ["virgil-lsp", "virgil-lsp-worker"]:
            if hashlib.sha256(read(f"extension/server/{name}")).hexdigest() != executables[name]:
                raise Failure(f"{path.name}: its bundled {name} is not the archive's")


def verify_assets(directory):
    """Check one release's three files before anything runs: exact names, the
    SHA256SUMS lines, and the version and target metadata of both packages."""
    directory = directory.resolve()
    if not directory.is_dir():
        raise Failure(f"{directory} is not a directory")
    names = sorted(entry.name for entry in directory.iterdir())
    archives = [name for name in names if ARCHIVE.fullmatch(name)]
    packages = [name for name in names if VSIX.fullmatch(name)]
    if len(archives) != 1 or len(packages) != 1 or SUMS not in names or len(names) != 3:
        raise Failure(f"{directory} must hold exactly virgil-lsp-<version>-x86-64-linux.tar.gz, "
                      f"virgil-lsp-vscode-<version>-linux-x64.vsix, and {SUMS}; it holds {names}")
    archive, package = directory / archives[0], directory / packages[0]
    sums = read_sums(directory / SUMS)
    digests = {}
    for file in [archive, package]:
        if file.name not in sums:
            raise Failure(f"{directory / SUMS} has no line for {file.name}")
        digests[file.name] = sha256(file)
        if digests[file.name] != sums[file.name]:
            raise Failure(f"{file}: SHA-256 {digests[file.name]}, but {SUMS} lists {sums[file.name]}")
    version = ARCHIVE.fullmatch(archive.name)[1]
    extension_version = VSIX.fullmatch(package.name)[1]
    top, manifest, manifest_bytes, executables = archive_manifest(archive, version)
    vsix_metadata(package, extension_version, manifest, manifest_bytes, executables)
    return {
        "directory": str(directory),
        "archive": {"name": archive.name, "sha256": digests[archive.name], "path": str(archive)},
        "vsix": {"name": package.name, "sha256": digests[package.name], "path": str(package)},
        "sha256sums": sha256(directory / SUMS),
        "serverVersion": version, "extensionVersion": extension_version, "tag": manifest["tag"],
        "sourceRevision": manifest["sourceRevision"], "virgilRevision": manifest["virgilRevision"],
        "top": top, "manifest": manifest,
    }


def check_pair(old, new):
    """The rehearsal needs a strictly newer server and extension to upgrade to."""
    for field, name in [("serverVersion", "server"), ("extensionVersion", "extension")]:
        if old[field] == new[field]:
            raise Failure(f"--from-assets and --to-assets have the same {name} version, {old[field]}")
        if version_key(old[field]) > version_key(new[field]):
            raise Failure(f"--from-assets {name} {old[field]} is newer than --to-assets {name} {new[field]}")


def version_output(assets):
    return (f"virgil-lsp {assets['serverVersion']}\n  source revision: {assets['tag']}\n"
            f"  virgil revision: {assets['virgilRevision']}\n")


def tree(directory):
    """Every entry under directory, with its type, mode, and contents' digest."""
    entries = {}
    for path in sorted(directory.rglob("*")):
        status = path.lstat()
        kind = "dir" if stat.S_ISDIR(status.st_mode) else "file" if stat.S_ISREG(status.st_mode) else "other"
        entries[path.relative_to(directory).as_posix()] = [
            kind, stat.S_IMODE(status.st_mode), sha256(path) if kind == "file" else None]
    return entries


def check_installation(directory, manifest):
    """The extracted files match the archive's manifest."""
    for name in ["virgil-lsp", "virgil-lsp-worker"]:
        file = directory / name
        if not os.access(file, os.X_OK) or sha256(file) != manifest["sha256"][name]:
            raise Failure(f"{file} is not the manifest's executable")
    library = directory / "virgil"
    files = {path.relative_to(library).as_posix(): sha256(path)
             for path in library.rglob("*") if path.is_file()}
    if files != manifest["library"]["files"]:
        differing = sorted(set(files.items()) ^ set(manifest["library"]["files"].items()))
        raise Failure(f"{library} differs from the manifest's library: {[name for name, _ in differing][:5]}")


def group_members(pgid):
    """The live processes in process group pgid, other than zombies, with
    their parents and executables."""
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) != pgid or fields[0] == "Z":
                continue
            try:
                exe = os.readlink(entry / "exe")
            except OSError:
                exe = None
            members.append({"pid": int(entry.name), "ppid": int(fields[1]), "exe": exe})
        except (OSError, IndexError, ValueError):
            continue
    return members


class Group:
    """A command in a process group of its own. The leader is never reaped
    before the group has been checked and, if need be, killed, so the group's
    number stays this run's while the driver signals it."""

    def __init__(self, command, env, cwd, **streams):
        self.proc = subprocess.Popen(command, env=env, cwd=cwd, start_new_session=True, **streams)
        self.pid = self.proc.pid
        self.code = None

    def exited(self):
        """The leader's exit status once it has exited, without reaping it."""
        if self.code is None:
            info = os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if info is not None and info.si_pid == self.pid:
                self.code = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
        return self.code

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self.exited() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return self.exited()

    def signal(self, number):
        try:
            os.killpg(self.pid, number)
        except ProcessLookupError:
            pass

    def stop(self, grace=5):
        """Stop the whole group, SIGTERM then SIGKILL, and reap the leader.
        Returns the processes that were still running after the leader exited."""
        if self.exited() is None:
            self.signal(signal.SIGTERM)
            if self.wait(grace) is None:
                self.signal(signal.SIGKILL)
                self.wait(30)
        left = [p for p in group_members(self.pid) if p["pid"] != self.pid]
        if left:
            self.signal(signal.SIGTERM)
            deadline = time.monotonic() + grace
            while group_members(self.pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.signal(signal.SIGKILL)
            deadline = time.monotonic() + 30
            while group_members(self.pid) and time.monotonic() < deadline:
                time.sleep(0.05)
        self.proc.wait()
        return left


class Session:
    """The server through stdio, in a process group of its own."""

    def __init__(self, server, directory, env, log):
        self.group = Group([str(server), "--stdio"], env, directory, stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=log)
        self.proc = self.group.proc
        self.buffer = b""
        self.messages = []
        self.number = 0

    def send(self, method, params=None, request=True):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request:
            self.number += 1
            message["id"] = self.number
        data = json.dumps(message).encode()
        self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.proc.stdin.flush()
        return message.get("id")

    def until(self, what, predicate):
        timeout = REPLY_TIMEOUT
        deadline = time.monotonic() + timeout
        while True:
            for message in self.messages:
                if predicate(message):
                    return message
            header, separator, body = self.buffer.partition(b"\r\n\r\n")
            if separator:
                size = int(header.removeprefix(b"Content-Length: "))
                if len(body) >= size:
                    self.messages.append(json.loads(body[:size]))
                    self.buffer = body[size:]
                    continue
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise Failure(f"no {what} within {timeout} seconds")
            data = os.read(self.proc.stdout.fileno(), 65536)
            if not data:
                raise Failure(f"the server closed its output before {what}")
            self.buffer += data

    def request(self, method, params=None):
        number = self.send(method, params)
        message = self.until(f"reply to {method}", lambda m: m.get("id") == number)
        if "result" not in message:
            raise Failure(f"{method} failed: {message}")
        return message["result"]


def fixture(project):
    project.mkdir(parents=True, exist_ok=True)
    (project / ".virgil-lsp.json").write_text(json.dumps({"version": 1, "projects": [{
        "name": "upgrade", "sources": ["*.v3"], "virgilDependencies": ["lib/util/*.v3"]}]}) + "\n")
    (project / "Install.v3").write_text(FIXTURE)
    return project / "Install.v3"


def worker_of(session, timeout=10):
    """The executable of the server's worker, a child in its process group."""
    deadline = time.monotonic() + timeout
    while True:
        workers = [p for p in group_members(session.group.pid) if p["ppid"] == session.group.pid]
        if workers or time.monotonic() > deadline:
            return [p["exe"] for p in workers]
        time.sleep(0.05)


def exercise(link, real, assets, project, env, log):
    """Start the server through link with no root, check a diagnostic, a
    library definition, and hover, and that the worker and library are the ones
    beside real, the link's target; then shut it down."""
    source = fixture(project)
    uri = source.as_uri()
    library = real / "virgil" / "lib/util/StringBuilder.v3"
    with log.open("wb") as stderr:
        session = Session(link, project, env, stderr)
        try:
            session.request("initialize", {"processId": os.getpid(), "rootUri": project.as_uri(),
                                           "capabilities": {}})
            session.send("initialized", {}, request=False)
            session.send("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": "virgil", "version": 1, "text": FIXTURE}}, request=False)
            session.until(f"diagnostic {DIAGNOSTIC!r}", lambda m: (
                m.get("method") == "textDocument/publishDiagnostics" and m["params"]["uri"] == uri
                and any(d.get("message") == DIAGNOSTIC and d.get("severity") == 1 for d in m["params"]["diagnostics"])))
            position = {"textDocument": {"uri": uri},
                        "position": {"line": 2, "character": FIXTURE.splitlines()[2].index("StringBuilder")}}
            definition = session.request("textDocument/definition", position)
            if isinstance(definition, list) and len(definition) == 1:
                definition = definition[0]
            if not isinstance(definition, dict) or definition.get("uri") != library.as_uri():
                raise Failure(f"definition of StringBuilder is {definition}, not in {library}")
            line = library.read_text().splitlines()[definition["range"]["start"]["line"]]
            if "class StringBuilder" not in line:
                raise Failure(f"definition of StringBuilder is at {line!r}")
            hover = session.request("textDocument/hover", position)
            if "class StringBuilder" not in json.dumps(hover):
                raise Failure(f"hover on StringBuilder is {hover}")
            workers = worker_of(session)
            if workers != [str(real / "virgil-lsp-worker")]:
                raise Failure(f"the server's worker is {workers}, not {real / 'virgil-lsp-worker'}")
            session.request("shutdown")
            session.send("exit", request=False)
            session.proc.stdin.close()
            code = session.group.wait(EXIT_TIMEOUT)
            if code != 0:
                raise Failure(f"the server exited with {code} after exit, not 0")
        finally:
            left = session.group.stop()
            for stream in [session.proc.stdin, session.proc.stdout]:
                stream.close()
        if left:
            raise Failure(f"processes left running after the server exited: {left}")
    roots = [line for line in log.read_text().splitlines() if "Virgil root:" in line]
    expected = f"Virgil root: {real / 'virgil'} (bundle, revision {assets['virgilRevision']})"
    if len(roots) != 1 or not roots[0].endswith(expected):
        raise Failure(f"the server logged {roots}, not {expected!r}")
    return {"definition": str(library), "worker": workers[0], "root": str(real / "virgil")}


class Rehearsal:
    """One temporary installation: a home with the documented archive layout,
    and one VS Code profile, used by every phase."""

    def __init__(self, work, executable, rollback_force=True):
        self.home = work / "home"
        self.opt = self.home / ".local/opt"
        self.bin = self.home / ".local/bin"
        self.link = self.bin / "virgil-lsp"
        self.project = work / "project"
        self.vscode = work / "vscode"
        self.tmp = work / "tmp"
        for directory in [self.opt, self.bin, self.vscode, self.tmp]:
            directory.mkdir(parents=True)
        self.executable = executable
        self.rollback_force = rollback_force
        self.installed = {}

    def env(self, path):
        env = {"PATH": path, "HOME": str(self.home), "LANG": "C.UTF-8", "TMPDIR": str(self.tmp)}
        for name in ["DISPLAY", "XAUTHORITY"]:
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def archive(self, phase, assets, logs):
        """Install or reselect the archive through the symlink, as
        docs/releasing.md#installing does, and use it."""
        real = self.opt / assets["top"]
        checks = []
        if not real.exists():
            subprocess.run(["tar", "-xzf", assets["archive"]["path"], "-C", str(self.opt)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            self.installed[real] = tree(real)
            checks.append("extracted beside the other installations")
        subprocess.run(["ln", "-sf", str(real / "virgil-lsp"), str(self.link)], check=True, timeout=10)
        if os.readlink(self.link) != str(real / "virgil-lsp"):
            raise Failure(f"{self.link} points to {os.readlink(self.link)}, not {real / 'virgil-lsp'}")
        env = self.env(f"{self.bin}:/usr/bin:/bin")
        found = shutil.which("virgil-lsp", path=env["PATH"])
        if found != str(self.link):
            raise Failure(f"virgil-lsp on PATH is {found}, not {self.link}")
        version = subprocess.run(["virgil-lsp", "--version"], env=env, cwd=self.home, text=True,
                                 capture_output=True, timeout=30)
        (logs / "version.txt").write_text(version.stdout + version.stderr)
        if version.returncode != 0 or version.stdout != version_output(assets):
            raise Failure(f"virgil-lsp --version through {self.link} printed {version.stdout!r} "
                          f"(status {version.returncode}), not {version_output(assets)!r}")
        checks.append("--version through the symlink on PATH")
        try:
            evidence = exercise(self.link, real, assets, self.project, env, logs / "server.log")
        except Failure as error:
            raise Failure(f"{error}; see {logs / 'server.log'}") from None
        checks += ["diagnostic", "library definition", "hover", "worker and library beside the target",
                   "shutdown and exit"]
        check_installation(real, assets["manifest"])
        checks.append("installation matches its manifest")
        for directory, entries in self.installed.items():
            if tree(directory) != entries:
                raise Failure(f"{directory} changed after it was installed")
        checks.append(f"all {len(self.installed)} installations intact")
        return {"link": str(self.link), "target": str(real / "virgil-lsp"),
                "version": version.stdout, "checks": checks, **evidence}

    def code(self, phase, assets, logs):
        """Install the phase's VSIX into the shared profile, with --force for
        the rollback, and observe it in a new VS Code (see vscode.cjs)."""
        expected = {"extensionVersion": assets["extensionVersion"], "serverVersion": assets["serverVersion"],
                    "versionOutput": version_output(assets), "virgilRevision": assets["virgilRevision"]}
        evidence = logs / "vscode-evidence.json"
        request = {"executable": str(self.executable), "userData": str(self.vscode / "user-data"),
                   "extensions": str(self.vscode / "extensions"), "vsix": assets["vsix"]["path"],
                   "force": phase == "rollback" and self.rollback_force, "project": str(self.project),
                   "expected": expected, "evidence": str(evidence), "logs": str(logs / "vscode-logs")}
        fixture(self.project)
        # PATH without the installation's bin directory: no external server.
        env = self.env(os.pathsep.join(entry for entry in os.environ["PATH"].split(os.pathsep)
                                       if entry and not (Path(entry) / "virgil-lsp").exists()))
        with (logs / "vscode.log").open("wb") as log:
            group = Group(["node", str(HELPER), "phase", json.dumps(request)], env, self.vscode,
                          stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = group.wait(VSCODE_TIMEOUT)
                timed_out = code is None
                if timed_out:
                    # The helper stops VS Code's process groups on SIGTERM.
                    group.signal(signal.SIGTERM)
                    group.wait(STOP_GRACE)
            finally:
                # Also on an interrupt, which doesn't reach the helper's own
                # session: stop and reap it before main() removes the
                # installation that it uses.
                left = group.stop(grace=STOP_GRACE)
        if code != 0:
            # The suite's or the helper's first complaint.
            reasons = re.findall(r"^(?:AssertionError.*|FAIL: .*)$",
                                 (logs / "vscode.log").read_text(errors="replace"), re.M)
            how = f"timed out after {VSCODE_TIMEOUT} seconds" if timed_out else f"status {code}"
            raise Failure(f"VS Code phase failed ({how})"
                          f"{': ' + reasons[0] if reasons else ''}; see {logs / 'vscode.log'}")
        if left:
            raise Failure(f"processes left running after the VS Code helper exited: {left}")
        observed = json.loads(evidence.read_text())
        for field, value in [("extensionVersion", expected["extensionVersion"]),
                             ("bundledVersion", expected["versionOutput"]),
                             ("serverStarted", expected["serverVersion"])]:
            if observed.get(field) != value:
                raise Failure(f"VS Code {field} is {observed.get(field)!r}, not {value!r}")
        return observed


def editor(executable):
    product = json.loads((executable.parent / "resources/app/product.json").read_text())
    return {"name": "VS Code", "version": product["version"], "commit": product["commit"],
            "executable": str(executable)}


def driver():
    """This checkout's commit, and whether tracked files differ from it."""
    def git(*args):
        result = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.returncode == 0 else None
    revision = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no", "--ignore-submodules=dirty")
    return {"script": "test/e2e/upgrade/run.py", "revision": revision,
            "modified": None if status is None else status != ""}


def locate_vscode():
    """The cached VS Code that the Extension Host test uses; never downloads."""
    result = subprocess.run(["node", str(HELPER), "locate"], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise Failure(result.stderr.strip() or "could not find the cached VS Code")
    return Path(result.stdout.strip())


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def public(assets):
    return {key: value for key, value in assets.items() if key not in ["top", "manifest"]}


def rehearse(sets, logs, tracks, receipt):
    """Run every phase's tracks in order, stopping at the first failure, and
    keep the receipt current after each. Returns whether every one passed."""
    receipt["phases"] = [{"name": name, "assets": side, "serverVersion": sets[side]["serverVersion"],
                          "extensionVersion": sets[side]["extensionVersion"],
                          **{track: {"result": "not run"} for track in TRACKS}} for name, side in PHASES]
    write_receipt(logs, receipt)
    for entry in receipt["phases"]:
        for track in TRACKS:
            directory = logs / entry["name"] / track
            directory.mkdir(parents=True)
            print(f"{entry['name']} ({entry['serverVersion']}, extension {entry['extensionVersion']}): {track}",
                  flush=True)
            try:
                details = tracks[track](entry["name"], sets[entry["assets"]], directory)
            except (Failure, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                entry[track] = {"result": "fail", "error": f"{type(error).__name__}: {error}",
                                "logs": str(directory)}
                receipt["result"] = "fail"
                write_receipt(logs, receipt)
                print(f"FAIL {entry['name']} {track}: {error}", flush=True)
                return False
            except KeyboardInterrupt:
                entry[track] = {"result": "interrupted", "logs": str(directory)}
                receipt["result"] = "interrupted"
                write_receipt(logs, receipt)
                print(f"INTERRUPTED {entry['name']} {track}", flush=True)
                raise
            entry[track] = {"result": "pass", "logs": str(directory), **details}
            write_receipt(logs, receipt)
            print(f"PASS {entry['name']} {track}", flush=True)
    receipt["result"] = "pass"
    write_receipt(logs, receipt)
    return True


def write_receipt(logs, receipt):
    receipt["updated"] = now()
    temporary = logs / "receipt.json.part"
    temporary.write_text(json.dumps(receipt, indent=2) + "\n")
    temporary.replace(logs / "receipt.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-assets", type=Path, required=True,
                        help="the older release's archive, linux-x64 VSIX, and SHA256SUMS")
    parser.add_argument("--to-assets", type=Path, required=True,
                        help="the newer release's archive, linux-x64 VSIX, and SHA256SUMS")
    parser.add_argument("--logs", type=Path, required=True, help="a new or empty directory for logs and the receipt")
    parser.add_argument("--vscode", type=Path,
                        help="the VS Code executable; by default the Extension Host test's cached one")
    parser.add_argument("--rollback-without-force", action="store_true",
                        help="install the rollback VSIX without --force, to show that the rehearsal fails "
                             "when VS Code keeps the newer extension")
    args = parser.parse_args(argv)
    logs = args.logs.resolve()
    if logs.exists() and not (logs.is_dir() and not any(logs.iterdir())):
        raise Failure(f"--logs takes a new or empty directory, and {logs} is not one")
    if not (sys.platform.startswith("linux") and platform.machine() == "x86_64"):
        raise Failure("the rehearsal runs on Linux x86-64 only")
    # Every check of the assets comes before anything runs.
    sets = {"from": verify_assets(args.from_assets), "to": verify_assets(args.to_assets)}
    check_pair(sets["from"], sets["to"])
    if "DISPLAY" not in os.environ:
        raise Failure("no DISPLAY for VS Code; run the rehearsal under xvfb-run -a")
    executable = args.vscode.resolve() if args.vscode else locate_vscode()
    logs.mkdir(parents=True, exist_ok=True)
    receipt = {
        "receiptVersion": 1,
        "kind": "virgil-lsp upgrade and rollback rehearsal",
        "observation": "automated by test/e2e/upgrade/run.py; no manual observation",
        "started": now(), "result": "running",
        "driver": driver(),
        "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "editor": editor(executable),
        "assets": {side: public(assets) for side, assets in sets.items()},
        "coverage": "Linux x86-64 archive symlink layout and the linux-x64 VSIX installed from a file into one "
                    "isolated VS Code profile; not macOS, Remote-SSH, WSL, or a Marketplace update",
    }
    work = Path(tempfile.mkdtemp(prefix="virgil-upgrade-"))
    # Paths in the phases are under this directory, which is removed at the end.
    receipt["work"] = str(work)
    try:
        rehearsal = Rehearsal(work, executable, rollback_force=not args.rollback_without_force)
        passed = rehearse(sets, logs, {"archive": rehearsal.archive, "vscode": rehearsal.code}, receipt)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"{'PASS' if passed else 'FAIL'} upgrade rehearsal: receipt {logs / 'receipt.json'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"FAIL upgrade rehearsal: {error}")
    except KeyboardInterrupt:
        print("INTERRUPTED upgrade rehearsal", file=sys.stderr)
        sys.exit(130)
