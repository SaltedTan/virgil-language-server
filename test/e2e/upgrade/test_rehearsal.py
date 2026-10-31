#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Asset checks, failure propagation, and cleanup of the upgrade rehearsal,
with synthetic assets and fake servers; run.py itself is the integration test."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
from unittest import mock
import zipfile

import run

OLD, NEW = ("0.1.0-alpha.4", "0.1.3"), ("0.1.0-alpha.5", "0.1.4")
REVISIONS = {OLD[0]: ("1" * 40, "a" * 40), NEW[0]: ("2" * 40, "b" * 40)}


def make_assets(root, server=OLD[0], extension=OLD[1], manifest=None, identity=None, target=None,
                bundled=None, archive_name=None, vsix_name=None, sums=None, member=None,
                archived=None, packaged=None, places=None):
    """One release's archive, linux-x64 VSIX, and SHA256SUMS in a new
    directory under root, each changed as the arguments say: archived and
    packaged replace executables' contents in the archive and in the VSIX
    only, and places moves them within the archive."""
    directory = Path(tempfile.mkdtemp(dir=root))
    source, virgil = REVISIONS.get(server, ("3" * 40, "c" * 40))
    files = {"virgil-lsp": b"server " + server.encode(), "virgil-lsp-worker": b"worker " + server.encode()}
    library = {"REVISION": virgil.encode() + b"\n", "lib/util/StringBuilder.v3": b"class StringBuilder {}\n"}
    data = {"manifestVersion": 1, "name": "virgil-lsp", "version": server, "tag": f"v{server}",
            "target": "x86-64-linux", "sourceRevision": source, "dirty": False, "virgilRevision": virgil,
            "library": {"revision": virgil, "files": {k: hashlib.sha256(v).hexdigest() for k, v in library.items()}},
            "sha256": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
    data.update(manifest or {})
    manifest_bytes = json.dumps(data).encode()
    top = f"virgil-lsp-{server}-x86-64-linux"
    archive = directory / (archive_name or f"{top}.tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        def add(name, content, mode=0o644):
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size, info.mode = len(content), mode
            tar.addfile(info, io.BytesIO(content))
        for name, content in {**files, **(archived or {})}.items():
            add((places or {}).get(name, name), content, 0o755)
        add("manifest.json", manifest_bytes)
        for name, content in library.items():
            add(f"virgil/{name}", content)
        if member is not None:
            tar.addfile(member)
    attributes = {"Publisher": "virgil-language-server", "Id": "virgil-lsp-vscode", "Version": extension,
                  "TargetPlatform": "linux-x64", **(identity or {})}
    vsix = directory / (vsix_name or f"virgil-lsp-vscode-{extension}-linux-x64.vsix")
    with zipfile.ZipFile(vsix, "w") as package:
        package.writestr("extension.vsixmanifest", (
            '<PackageManifest xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"><Metadata><Identity '
            + " ".join(f'{k}="{v}"' for k, v in attributes.items()) + "/></Metadata></PackageManifest>"))
        package.writestr("extension/package.json", json.dumps({
            "publisher": "virgil-language-server", "name": "virgil-lsp-vscode", "version": extension}))
        package.writestr("extension/server-target.json", json.dumps(target or {"target": "linux-x64"}))
        package.writestr("extension/server/manifest.json", manifest_bytes if bundled is None else bundled)
        for name, content in {**files, **(packaged or {})}.items():
            package.writestr(f"extension/server/{name}", content)
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}" for f in [archive, vsix]]
    (directory / "SHA256SUMS").write_text("\n".join(sums(lines) if sums else lines) + "\n")
    return directory


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        # The driver's progress lines.
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        temporary = tempfile.TemporaryDirectory(prefix="upgrade-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


class AssetTests(TemporaryTest):
    def rejected(self, pattern, **changes):
        with self.assertRaisesRegex(run.Failure, pattern):
            run.verify_assets(make_assets(self.root, **changes))

    def test_accepts_a_release(self):
        assets = run.verify_assets(make_assets(self.root))
        self.assertEqual((assets["serverVersion"], assets["extensionVersion"]), OLD)
        self.assertEqual(assets["tag"], "v" + OLD[0])
        self.assertEqual(assets["virgilRevision"], "a" * 40)

    def test_rejects_wrong_targets(self):
        self.rejected("must hold exactly", archive_name=f"virgil-lsp-{OLD[0]}-x86-64-darwin.tar.gz")
        self.rejected("must hold exactly", vsix_name=f"virgil-lsp-vscode-{OLD[1]}-darwin-arm64.vsix")
        self.rejected("must hold exactly", vsix_name=f"virgil-lsp-vscode-{OLD[1]}.vsix")
        self.rejected("manifest target is 'x86-64-darwin'", manifest={"target": "x86-64-darwin"})
        self.rejected("VSIX TargetPlatform is 'darwin-arm64'", identity={"TargetPlatform": "darwin-arm64"})
        self.rejected("server-target.json", target={"target": "darwin-x64"})

    def test_rejects_wrong_versions(self):
        self.rejected("manifest version is '0.1.0-alpha.5'", manifest={"version": NEW[0]})
        self.rejected("manifest tag", manifest={"tag": None})
        self.rejected("manifest dirty", manifest={"dirty": True})
        self.rejected("VSIX Version is '0.1.4'", identity={"Version": NEW[1]})
        # A VSIX that bundles another release's server.
        other = make_assets(self.root, *NEW)
        _, _, bundled, _ = run.archive_manifest(next(other.glob("*.tar.gz")), NEW[0])
        self.rejected("bundled server/manifest.json is not the archive's", bundled=bundled)

    def test_rejects_missing_or_mismatched_checksums(self):
        self.rejected("has no line for virgil-lsp-vscode", sums=lambda lines: lines[:1])
        self.rejected("has no line for virgil-lsp-0", sums=lambda lines: lines[1:])
        self.rejected("but SHA256SUMS lists", sums=lambda lines: ["0" * 64 + lines[0][64:], lines[1]])
        self.rejected("second line", sums=lambda lines: lines + lines[:1])
        self.rejected("not a sha256sum line", sums=lambda lines: [lines[0].replace("  ", " ", 1), lines[1]])
        directory = make_assets(self.root)
        (directory / "SHA256SUMS").unlink()
        with self.assertRaisesRegex(run.Failure, "must hold exactly"):
            run.verify_assets(directory)
        directory = make_assets(self.root)
        (directory / "virgil-lsp-0.1.0-alpha.4-x86-64-linux.manifest.json").write_text("{}")
        with self.assertRaisesRegex(run.Failure, "must hold exactly"):
            run.verify_assets(directory)

    def test_compares_both_packages_executables(self):
        # A changed archive executable, with SHA256SUMS updated to match.
        self.rejected("its virgil-lsp is not its manifest's", archived={"virgil-lsp": b"changed server"})
        self.rejected("its virgil-lsp-worker is not its manifest's", archived={"virgil-lsp-worker": b"changed"})
        # An archive consistent with its manifest, and a VSIX bundling that
        # manifest with other executables.
        self.rejected("its bundled virgil-lsp-worker is not the archive's", packaged={"virgil-lsp-worker": b"other"})
        self.rejected("expected one virgil-lsp-worker", member=tarfile.TarInfo(
            f"virgil-lsp-{OLD[0]}-x86-64-linux/libexec/virgil-lsp-worker"))
        # A misplaced worker with the right contents passes; the rehearsal
        # finds that the server can't start it.
        assets = run.verify_assets(make_assets(self.root, places={"virgil-lsp-worker": "libexec/virgil-lsp-worker"}))
        self.assertEqual(assets["serverVersion"], OLD[0])

    def test_rejects_unsafe_members(self):
        link = tarfile.TarInfo(f"virgil-lsp-{OLD[0]}-x86-64-linux/virgil/lib/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc"
        self.rejected("is not a regular file or directory", member=link)
        self.rejected("is outside", member=tarfile.TarInfo(f"virgil-lsp-{OLD[0]}-x86-64-linux/../escape"))

    def test_pairs(self):
        old = run.verify_assets(make_assets(self.root))
        new = run.verify_assets(make_assets(self.root, *NEW))
        run.check_pair(old, new)
        with self.assertRaisesRegex(run.Failure, "same server version"):
            run.check_pair(old, old)
        with self.assertRaisesRegex(run.Failure, "is newer than"):
            run.check_pair(new, old)
        same_extension = run.verify_assets(make_assets(self.root, NEW[0], OLD[1]))
        with self.assertRaisesRegex(run.Failure, "same extension version"):
            run.check_pair(old, same_extension)

    def test_version_order(self):
        versions = ["0.1.0-alpha.4", "0.1.0-alpha.5", "0.1.0-alpha.10", "0.1.0-beta", "0.1.0", "0.1.1", "0.10.0"]
        self.assertEqual(sorted(reversed(versions), key=run.version_key), versions)


def receipt_of(directory):
    return json.loads((directory / "receipt.json").read_text())


class PropagationTests(TemporaryTest):
    def sets(self):
        return {"from": {"serverVersion": OLD[0], "extensionVersion": OLD[1]},
                "to": {"serverVersion": NEW[0], "extensionVersion": NEW[1]}}

    def test_failure_stops_later_phases(self):
        calls = []

        def track(name):
            def run_track(phase, assets, logs):
                calls.append((phase, name))
                if (phase, name) == ("upgrade", "vscode"):
                    raise run.Failure("VS Code kept 0.1.3")
                return {"checks": [name]}
            return run_track
        logs = self.root / "logs"
        logs.mkdir()
        receipt = {}
        self.assertFalse(run.rehearse(self.sets(), logs, {t: track(t) for t in run.TRACKS}, receipt))
        self.assertEqual(calls, [("install", "archive"), ("install", "vscode"), ("upgrade", "archive"),
                                 ("upgrade", "vscode")])
        written = receipt_of(logs)
        self.assertEqual(written["result"], "fail")
        results = [(p["name"], p["archive"]["result"], p["vscode"]["result"]) for p in written["phases"]]
        self.assertEqual(results, [("install", "pass", "pass"), ("upgrade", "pass", "fail"),
                                   ("rollback", "not run", "not run")])
        self.assertIn("VS Code kept 0.1.3", written["phases"][1]["vscode"]["error"])
        self.assertEqual([p["serverVersion"] for p in written["phases"]], [OLD[0], NEW[0], OLD[0]])

    def test_interruption_is_recorded(self):
        def track(phase, assets, logs):
            if phase == "upgrade":
                raise KeyboardInterrupt
            return {}
        logs = self.root / "logs"
        logs.mkdir()
        with self.assertRaises(KeyboardInterrupt):
            run.rehearse(self.sets(), logs, {t: track for t in run.TRACKS}, {})
        written = receipt_of(logs)
        self.assertEqual(written["result"], "interrupted")
        results = [(p["name"], p["archive"]["result"], p["vscode"]["result"]) for p in written["phases"]]
        self.assertEqual(results, [("install", "pass", "pass"), ("upgrade", "interrupted", "not run"),
                                   ("rollback", "not run", "not run")])

    def test_sigterm_is_handled_as_an_interrupt(self):
        # A main() that cleans up in a finally, as the real one does, under
        # the real entry point; SIGTERM must run that cleanup and exit 143.
        cleaned = self.root / "cleaned"
        script = textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(Path(run.__file__).parent)!r})
            import run
            def main():
                try:
                    print("ready", flush=True)
                    time.sleep(30)
                finally:
                    open({str(cleaned)!r}, "w").close()
            run.main = main
            run.entry()
        """)
        child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline(), "ready\n")
        child.send_signal(signal.SIGTERM)
        out, err = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 128 + signal.SIGTERM, err)
        self.assertIn("INTERRUPTED upgrade rehearsal", err)
        self.assertTrue(cleaned.exists())

    def test_all_pass(self):
        logs = self.root / "logs"
        logs.mkdir()
        self.assertTrue(run.rehearse(self.sets(), logs, {t: lambda *_: {} for t in run.TRACKS}, {}))
        self.assertEqual(receipt_of(logs)["result"], "pass")

    @unittest.skipUnless(sys.platform.startswith("linux") and platform.machine() == "x86_64", "Linux x86-64 only")
    def test_main_removes_its_installation_on_failure(self):
        executable = self.root / "code" / "code"
        (executable.parent / "resources/app").mkdir(parents=True)
        (executable.parent / "resources/app/product.json").write_text('{"version": "1.0.0", "commit": "c"}')
        tmp = self.root / "tmp"
        tmp.mkdir()
        works = []

        class Broken(run.Rehearsal):
            def archive(self, phase, assets, logs):
                works.append(self.home.parent)
                raise run.Failure("worker missing")
        logs = self.root / "logs"
        args = ["--from-assets", str(make_assets(self.root)), "--to-assets", str(make_assets(self.root, *NEW)),
                "--logs", str(logs), "--vscode", str(executable)]
        with mock.patch.object(run, "Rehearsal", Broken), mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(tempfile, "tempdir", str(tmp)):
            self.assertEqual(run.main(args), 1)
        self.assertEqual(len(works), 1)
        self.assertFalse(works[0].exists())
        self.assertEqual(list(tmp.iterdir()), [])
        written = receipt_of(logs)
        self.assertEqual(written["phases"][0]["archive"]["result"], "fail")
        self.assertEqual(written["assets"]["to"]["serverVersion"], NEW[0])
        with self.assertRaisesRegex(run.Failure, "new or empty directory"):
            run.main(args)

    def test_assets_are_checked_before_anything_runs(self):
        bad = make_assets(self.root, *NEW, sums=lambda lines: lines[:1])
        with mock.patch.object(run, "Rehearsal") as rehearsal, mock.patch.object(run, "locate_vscode") as locate:
            with self.assertRaisesRegex(run.Failure, "has no line"):
                run.main(["--from-assets", str(make_assets(self.root)), "--to-assets", str(bad),
                          "--logs", str(self.root / "logs")])
        rehearsal.assert_not_called()
        locate.assert_not_called()
        self.assertFalse((self.root / "logs").exists())


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
class CleanupTests(TemporaryTest):
    def script(self, body):
        path = self.root / "server"
        path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
        path.chmod(0o755)
        return path

    def child(self):
        deadline = time.monotonic() + 10
        while not (self.root / "child").exists() or not (self.root / "child").read_text().strip():
            self.assertLess(time.monotonic(), deadline, "the fake server did not start its child")
            time.sleep(0.05)
        return int((self.root / "child").read_text())

    def test_unresponsive_server_is_stopped_with_its_children(self):
        # Starts a child, as the server starts its worker, and never answers.
        server = self.script(f"""\
            sleep 300 &
            echo $! > {self.root}/child
            exec sleep 300
            """)
        project = self.root / "project"
        with mock.patch.object(run, "REPLY_TIMEOUT", 1):
            with self.assertRaisesRegex(run.Failure, "no reply to initialize within 1 seconds"):
                run.exercise(server, self.root, {"virgilRevision": "a" * 40}, project,
                             {"PATH": "/usr/bin:/bin"}, self.root / "server.log")
        self.assertFalse(alive(self.child()))

    def test_children_left_after_exit_are_reported_and_stopped(self):
        server = self.script(f"""\
            sleep 300 &
            echo $! > {self.root}/child
            """)
        group = run.Group([str(server)], {"PATH": "/usr/bin:/bin"}, self.root)
        self.assertEqual(group.wait(10), 0)
        child = self.child()
        left = group.stop()
        self.assertEqual([p["pid"] for p in left], [child])
        self.assertFalse(alive(child))

    def vscode_phase(self):
        rehearsal = run.Rehearsal(self.root / "work", self.root / "code")
        assets = {"extensionVersion": OLD[1], "serverVersion": OLD[0], "tag": "v" + OLD[0],
                  "virgilRevision": "a" * 40, "vsix": {"path": str(self.root / "x.vsix")}}
        logs = self.root / "phase"
        logs.mkdir(exist_ok=True)
        return rehearsal, assets, logs

    def hanging_helper(self):
        """A helper that ignores SIGTERM and has a child, as VS Code's are."""
        hanging = self.root / "hanging.cjs"
        hanging.write_text(textwrap.dedent(f"""\
            const {{ spawn }} = require('node:child_process');
            process.on('SIGTERM', () => {{}});
            const child = spawn('sleep', ['300'], {{ stdio: 'ignore' }});
            require('node:fs').writeFileSync({json.dumps(str(self.root / 'child'))}, String(child.pid));
            setInterval(() => {{}}, 1000);
            """))
        return hanging

    def test_vscode_helper_failure_and_timeout(self):
        rehearsal, assets, logs = self.vscode_phase()
        failing = self.root / "failing.cjs"
        failing.write_text("process.exitCode = 1;\n")
        with mock.patch.object(run, "HELPER", failing):
            with self.assertRaisesRegex(run.Failure, "VS Code phase failed \\(status 1\\)"):
                rehearsal.code("install", assets, logs)
        with mock.patch.object(run, "HELPER", self.hanging_helper()), mock.patch.object(run, "VSCODE_TIMEOUT", 2), \
                mock.patch.object(run, "STOP_GRACE", 1):
            with self.assertRaisesRegex(run.Failure, "timed out"):
                rehearsal.code("rollback", assets, logs)
        self.assertFalse(alive(self.child()))

    def test_interrupt_stops_and_reaps_the_vscode_helper(self):
        rehearsal, assets, logs = self.vscode_phase()
        leaders = []
        wait = run.Group.wait

        def interrupted(group, timeout):
            # Ctrl-C during the phase's wait, once the helper has started.
            if not leaders:
                leaders.append(group.pid)
                self.child()
                raise KeyboardInterrupt
            return wait(group, timeout)
        with mock.patch.object(run, "HELPER", self.hanging_helper()), mock.patch.object(run, "STOP_GRACE", 1), \
                mock.patch.object(run.Group, "wait", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                rehearsal.code("upgrade", assets, logs)
        self.assertFalse(alive(self.child()))
        self.assertEqual(run.group_members(leaders[0]), [])
        # Reaped, not just stopped.
        with self.assertRaises(ChildProcessError):
            os.waitid(os.P_PID, leaders[0], os.WEXITED | os.WNOHANG)


if __name__ == "__main__":
    unittest.main()
