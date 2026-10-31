#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Tests the headless Neovim test's result stream, run.sh --results
(test/e2e/RESULTS.md). A real Neovim run checks the manifest's plan.
A stand-in nvim on PATH reports
observations as check.lua does, with the real server's executables, so that
each stream that run.sh writes, for passing, failing, incomplete, and
interrupted runs, can be checked with scripts/editor-results.py.
Usage: test_nvim_results.py [SERVER], where SERVER defaults to build/virgil-lsp
and has virgil-lsp-worker beside it."""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RUN = HERE / "nvim" / "run.sh"
RESULTS = HERE / "nvim" / "results.py"
MANIFEST = json.loads((HERE / "coverage.json").read_bytes())
SERVER = Path(sys.argv.pop(1) if len(sys.argv) > 1 else REPO / "build" / "virgil-lsp").resolve()
NVIM_CHECKS = {check["id"]: check for check in MANIFEST["checks"] if "nvim" in check["suites"]}


def load_validator():
    spec = importlib.util.spec_from_file_location("editor_results", REPO / "scripts" / "editor-results.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_validator()
VALIDATOR_MANIFEST = validator.Manifest.load(validator.MANIFEST)
VALIDATOR_SCHEMA = validator.Schema.load(validator.SCHEMA)


ORDER = [
    "nvim.startup.attach",
    "nvim.diagnostics.parser",
    "nvim.symbols.broken-empty",
    "nvim.diagnostics.semicolon-fix",
    "nvim.diagnostics.unsaved-clear",
    "nvim.symbols.hierarchy",
    "nvim.folding-ranges.response",
    "nvim.selection-ranges.response",
    "nvim.configuration.valid-root",
    "nvim.diagnostics.semantic",
    "nvim.definition.response",
    "nvim.inlay-hints.response",
    "nvim.semantic-tokens.response",
    "nvim.type-definition.response",
    "nvim.type-definition.jump",
    "nvim.hover.response",
    "nvim.signature.complete",
    "nvim.workspace-symbols.join",
    "nvim.references.library",
    "nvim.document-highlight.response",
    "nvim.implementation.response",
    "nvim.implementation.jump",
    "nvim.type-hierarchy.response",
    "nvim.type-hierarchy.quickfix",
    "nvim.call-hierarchy.response",
    "nvim.call-hierarchy.quickfix",
    "nvim.rename.apply-undo",
    "nvim.completion.keyword",
    "nvim.completion.local",
    "nvim.completion.member",
    "nvim.configuration.json-routing",
    "nvim.configuration.unsaved",
    "nvim.shutdown.clean",
]


def tree_sha256(root):
    lines = []
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            lines.append((relative, f"link {os.readlink(path)}  {relative}\n"))
        elif path.is_file():
            lines.append((relative, f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}\n"))
    return hashlib.sha256("".join(line for _, line in sorted(lines)).encode()).hexdigest()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def passing(check_id):
    return json.dumps({"id": check_id, "status": "pass", "startedAt": "<NOW>", "endedAt": "<NOW>",
                       "observation": f"{check_id} observed"})


def failing(check_id, message):
    return json.dumps({"id": check_id, "status": "fail", "startedAt": "<NOW>", "endedAt": "<NOW>",
                       "observation": message, "reason": message, "failureCode": "wait-timeout"})


# The stand-in nvim: --version prints FAKE_NVIM_VERSION, and otherwise it
# appends FAKE_NVIM's lines to the observations file, with <WORK> as the
# temporary directory, then its partial line without an LF, does what else it
# says, and exits with its status.
FAKE_NVIM = """#!{python}
import json, os, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote
if sys.argv[1:] == ["--version"]:
    print(os.environ.get("FAKE_NVIM_VERSION", "NVIM v0.11.6\\nBuild type: Release"))
    sys.exit(0)
scenario = json.loads(os.environ.get("FAKE_NVIM", "{{}}"))
path = os.environ.get("VIRGIL_NVIM_OBSERVATIONS")
work = os.path.dirname(path) if path else None
if scenario.get("pidfile"):
    with open(scenario["pidfile"], "a") as file:
        file.write(f"{{os.getpid()}}\\n")
if scenario.get("linger") or scenario.get("detached"):
    child = subprocess.Popen([sys.executable, "-c",
                              "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
                             start_new_session=scenario.get("detached", False))
    with open(scenario["pidfile"], "a") as file:
        file.write(f"{{child.pid}}\\n")
if path:
    with open(path, "a") as out:
        if scenario.get("report_child"):
            out.write(json.dumps({{"process": child.pid, "name": "virgil-lsp-worker"}}) + "\\n")
        for line in scenario.get("lines", []):
            moment = int(time.time() * 1000)
            line = line.replace('"<NOW>"', str(moment)).replace('"<LATER>"', str(moment + 100))
            line = line.replace("<WORK_URI>", Path(work).as_uri()).replace("<WORK_ENCODED>", quote(work, safe="/"))
            out.write(line.replace("<WORK>", work) + "\\n")
        out.write(scenario.get("partial", ""))
        out.flush()
if scenario.get("lock"):
    os.makedirs(os.path.join(work, "locked", "inside"))
    os.chmod(os.path.join(work, "locked"), 0o555)
time.sleep(scenario.get("sleep", 0.2 if scenario.get("linger") or scenario.get("detached") else 0))
sys.exit(scenario.get("exit", 0))
"""


def wait_for_finalizer(pid):
    limit = time.monotonic() + 20
    while time.monotonic() < limit:
        listing = subprocess.run(["ps", "-A", "-o", "ppid=,args="], check=True,
                                 stdout=subprocess.PIPE, text=True).stdout
        for row in listing.splitlines():
            parent, command = row.split(None, 1)
            if int(parent) == pid and f"{RESULTS} end " in command:
                return
        time.sleep(0.02)
    raise AssertionError("run.sh did not start its finalizer")


class Case(unittest.TestCase):
    """A temporary directory with a server directory, the real server's
    executables linked into it, and a stand-in nvim first on PATH."""

    def setUp(self):
        self.assertTrue(SERVER.is_file() and (SERVER.parent / "virgil-lsp-worker").is_file(),
                        f"build {SERVER} and virgil-lsp-worker beside it first")
        self.dir = Path(tempfile.mkdtemp(prefix="nvim-results-")).resolve()
        self.addCleanup(self.remove)
        self.server_dir = self.dir / "server"
        self.server_dir.mkdir()
        for name in ("virgil-lsp", "virgil-lsp-worker"):
            (self.server_dir / name).symlink_to(SERVER.parent / name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        nvim = self.bin / "nvim"
        nvim.write_text(FAKE_NVIM.format(python=sys.executable))
        nvim.chmod(0o755)
        self.stream = self.dir / "out" / "nvim.jsonl"
        self.stream.parent.mkdir()
        self.pidfile = self.dir / "pids"
        self.env = {name: value for name, value in os.environ.items() if not name.startswith(("FAKE_NVIM", "REQUIRE_"))}
        self.env["PATH"] = f"{self.bin}{os.pathsep}{os.environ['PATH']}"

    def remove(self):
        for pid in self.pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for directory, names, _ in os.walk(self.dir):
            for name in names:
                os.chmod(os.path.join(directory, name), stat.S_IRWXU)
        shutil.rmtree(self.dir)

    def pids(self):
        return [int(line) for line in self.pidfile.read_text().split()] if self.pidfile.exists() else []

    def scenario(self, **scenario):
        scenario.setdefault("pidfile", str(self.pidfile))
        self.env["FAKE_NVIM"] = json.dumps(scenario)

    def run_sh(self, *args, results=True, env=None):
        command = ["bash", str(RUN), str(self.server_dir / "virgil-lsp"), *args]
        if results:
            command += ["--results", str(self.stream)]
        return subprocess.run(command, env=env or self.env, cwd=REPO, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=60)

    def events(self):
        return [json.loads(line) for line in self.stream.read_text().splitlines()]

    def checks(self):
        return {event["id"]: event for event in self.events() if event["event"] == "check"}

    def validate(self, data=None):
        """The validator's Run for the stream; fails the test if it's invalid."""
        try:
            return validator.validate_stream(self.stream.read_bytes() if data is None else data,
                                             VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)
        except validator.Invalid as error:
            self.fail(f"invalid stream: {error}\n{self.stream.read_text()}")

    def assertInvalid(self, data, pattern):
        with self.assertRaisesRegex(validator.Invalid, pattern):
            validator.validate_stream(data, VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)

    def assertNoWork(self):
        self.assertEqual(list(self.server_dir.glob("nvim-smoke.*")), [])

    def assertStopped(self):
        """Every process that the stand-in nvim recorded has exited."""
        for pid in self.pids():
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                self.assertLess(time.monotonic(), deadline, f"process {pid} is still running")
                time.sleep(0.05)

    def assertFailed(self, result, status="fail"):
        """A valid stream of a run that didn't pass, and a failing exit."""
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        run = self.validate()
        self.assertFalse(run.passed)
        self.assertEqual(run.end["status"], status)
        self.assertEqual(run.end["exitCode"], result.returncode)
        return run


class ReaderTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("nvim_results", RESULTS)
        self.writer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.writer)
        self.directory = tempfile.TemporaryDirectory(prefix="nvim-reader-", dir=REPO)
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        self.nvim = self.work / "nvim"
        self.nvim.write_text(FAKE_NVIM.format(python=sys.executable))
        self.nvim.chmod(0o755)
        self.stream = self.work / "r.jsonl"
        self.stream.write_text(json.dumps({"event": "run-start", "runId": "reader-test"}) + "\n")
        self.pidfile = self.work / "pids"
        self.addCleanup(self.stop_children)

    def stop_children(self):
        if self.pidfile.exists():
            for pid in map(int, self.pidfile.read_text().splitlines()):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def run_reader(self, lines, **changes):
        scenario = {"lines": lines, "detached": True, "sleep": 60, "pidfile": str(self.pidfile), **changes}
        with mock.patch.dict(os.environ, FAKE_NVIM=json.dumps(scenario)):
            return self.writer.main(["run", "--stream", str(self.stream), "--work", str(self.work),
                                     "--deadline", "1", "--", str(self.nvim)])

    def assert_cleaned(self):
        state = json.loads((self.work / "launcher.json").read_text())
        self.assertEqual(state["processes"], "verified", state)
        for pid in map(int, self.pidfile.read_text().splitlines()):
            with self.assertRaises(ProcessLookupError, msg=f"process {pid} survived cleanup"):
                os.kill(pid, 0)
        return state

    def test_successful_observations(self):
        self.assertEqual(self.run_reader(list(map(passing, ORDER)), detached=False, sleep=0), 0)
        state = self.assert_cleaned()
        self.assertEqual(state["issues"], [])
        checks = [json.loads(line) for line in self.stream.read_text().splitlines()[1:]]
        self.assertEqual([check["id"] for check in checks], [*ORDER, "nvim.infrastructure.completed"])
        self.assertTrue(all(check["status"] == "pass" for check in checks))

    def test_uri_paths_in_failure_are_redacted(self):
        self.work = self.work / "release build" / "nvim-smoke"
        self.work.mkdir(parents=True)
        message = " | ".join(f"{form}/project/bad.v3" for form in ("<WORK>", "<WORK_ENCODED>", "<WORK_URI>"))
        malformed = json.dumps({"observation": "<WORK_URI>/project/bad.v3"})
        self.assertEqual(self.run_reader([malformed, failing(ORDER[0], message)],
                                         detached=False, sleep=0, exit=1), 1)
        state = self.assert_cleaned()
        checks = [json.loads(line) for line in self.stream.read_text().splitlines()[1:]]
        expected = " | ".join(["<work>/project/bad.v3"] * 3)
        self.assertEqual(checks[0]["observation"], expected)
        self.assertEqual(checks[0]["reason"], expected)
        self.assertIn("<work>/project/bad.v3", state["issues"][0])
        text = self.stream.read_text() + json.dumps(state)
        for path in (str(self.work), quote(str(self.work), safe="/"), self.work.as_uri()):
            self.assertNotIn(path, text)

    def test_normal_exit_stops_detached_child(self):
        self.assertEqual(self.run_reader([passing(ORDER[0])], sleep=0.2), 0)
        state = self.assert_cleaned()
        self.assertTrue(any("still running" in issue for issue in state["issues"]), state)

    @unittest.skipUnless(sys.platform == "linux", "adoption of unsampled children requires Linux")
    def test_immediate_failure_stops_detached_child(self):
        self.assertEqual(self.run_reader([failing(ORDER[0], "immediate failure")], sleep=0, exit=1), 1)
        self.assert_cleaned()
        checks = [json.loads(line) for line in self.stream.read_text().splitlines()[1:]]
        self.assertEqual((checks[0]["id"], checks[0]["status"]), (ORDER[0], "fail"))
        self.assertTrue(all(check["status"] == "skip" for check in checks[1:-1]))

    def test_process_reports_are_cleanup_inputs(self):
        self.assertEqual(self.run_reader([passing(ORDER[0])], report_child=True, sleep=0), 0)
        state = self.assert_cleaned()
        self.assertTrue(any("still running" in issue for issue in state["issues"]), state)
        checks = [json.loads(line) for line in self.stream.read_text().splitlines()[1:]]
        self.assertEqual([check["id"] for check in checks], [ORDER[0], "nvim.infrastructure.completed"])

    def test_signal_during_unavailable_finalization(self):
        env = dict(os.environ, PATH=f"{self.work}{os.pathsep}{os.environ['PATH']}",
                   VIRGIL_NVIM_END_DELAY="2")
        with subprocess.Popen(["bash", str(RUN), str(self.work / "virgil-lsp"), "--results", str(self.stream)],
                              env=env, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as process:
            wait_for_finalizer(process.pid)
            os.kill(process.pid, signal.SIGTERM)
            output, error = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 1, output + error)
        run = validator.validate_stream(self.stream.read_bytes(), VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)
        self.assertEqual(run.end["exitCode"], process.returncode)
        self.assertEqual(run.end["status"], "unavailable")

    def test_malformed_records_continue_to_deadline_and_cleanup(self):
        lines = []
        for changes in ({"startedAt": True}, {"id": []}, {"startedAt": float("nan")},
                        {"endedAt": 10 ** 1000}, {"startedAt": "<LATER>"}):
            for status in ("pass", "fail"):
                record = json.loads(passing(ORDER[0]) if status == "pass" else failing(ORDER[0], "failed"))
                record.update(changes)
                lines.append(json.dumps(record))
        lines += [json.dumps(record) for record in (
            {"process": [], "name": "virgil-lsp"}, {"process": True, "name": "virgil-lsp"},
            {"process": os.getpid(), "name": "virgil-lsp"}, {"process": 1, "name": []},
        )]
        self.assertEqual(self.run_reader([*lines, passing(ORDER[0])]), 1)
        state = self.assert_cleaned()
        self.assertEqual(sum("malformed observation" in issue for issue in state["issues"]), len(lines))
        checks = [json.loads(line) for line in self.stream.read_text().splitlines()[1:]]
        self.assertEqual([check["id"] for check in checks], [ORDER[0], "nvim.infrastructure.completed"])
        self.assertEqual(checks[0]["status"], "pass")
        self.assertEqual(checks[1]["failureCode"], "deadline")

    def test_reader_exception_still_stops_descendants(self):
        original = self.writer.Stream.check

        def write(stream, check_id, *args):
            if check_id == ORDER[0]:
                raise OSError("reader regression")
            return original(stream, check_id, *args)

        with mock.patch.object(self.writer.Stream, "check", write):
            self.assertEqual(self.run_reader([passing(ORDER[0])]), 1)
        state = self.assert_cleaned()
        self.assertTrue(any("reader regression" in issue for issue in state["issues"]), state)


class CheckLuaTest(unittest.TestCase):
    def test_real_result_stream(self):
        nvim = shutil.which("nvim")
        if nvim is None:
            self.skipTest("Neovim 0.11+ is not on PATH")
        version = subprocess.run([nvim, "--version"], capture_output=True, text=True, check=True)
        match = re.match(r"NVIM v(\d+)\.(\d+)", version.stdout)
        if not match or tuple(map(int, match.groups())) < (0, 11):
            self.skipTest("Neovim on PATH is older than 0.11 or has an unknown version")
        with tempfile.TemporaryDirectory(prefix="nvim-real-results-", dir=REPO) as directory:
            stream = Path(directory) / "r.jsonl"
            result = subprocess.run([str(RUN), str(SERVER), "--results", str(stream)], cwd=REPO,
                                    capture_output=True, text=True, timeout=150)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            validated = subprocess.run([sys.executable, str(REPO / "scripts" / "editor-results.py"),
                                        "validate", str(stream)], cwd=REPO, capture_output=True, text=True)
            self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
            events = [json.loads(line) for line in stream.read_text().splitlines()]
            checks = [event for event in events if event["event"] == "check"]
            self.assertEqual([check["id"] for check in checks],
                             ["nvim.infrastructure.preflight", *ORDER, "nvim.infrastructure.completed"])
            self.assertEqual(sorted(check["id"] for check in checks), sorted(NVIM_CHECKS))
            self.assertTrue(all(check["status"] == "pass" for check in checks))


class StreamTest(Case):
    def test_passing_run(self):
        self.scenario(lines=[passing(check_id) for check_id in ORDER])
        result = self.run_sh()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Neovim smoke test: NVIM v0.11.6", result.stdout)
        self.assertIn("run passed (35 of 35 observations)", result.stdout)
        run = self.validate()
        self.assertTrue(run.passed)
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertNoWork()
        start = run.start
        self.assertEqual(start["suite"], "nvim")
        self.assertEqual(start["purpose"], "development")
        self.assertIsNone(start["release"])
        self.assertEqual(start["command"], ["test/e2e/nvim/run.sh", "<server>", "--results", "<results>"])
        provenance = start["provenance"]
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], stdout=subprocess.PIPE, text=True)
        self.assertEqual(provenance["harness"]["revision"], head.stdout.strip())
        self.assertEqual(provenance["server"]["sha256"], sha256(SERVER))
        self.assertEqual(provenance["server"]["workerSha256"], sha256(SERVER.parent / "virgil-lsp-worker"))
        version = subprocess.run([str(SERVER), "--version"], stdout=subprocess.PIPE, text=True).stdout
        self.assertEqual(provenance["server"]["versionText"], version.rstrip("\n"))
        self.assertEqual(provenance["editor"]["version"], "0.11.6")
        self.assertEqual(provenance["fixtureSha256"], tree_sha256(HERE / "nvim" / "fixture"))
        self.assertEqual(provenance["virgilRootSha256"], tree_sha256(REPO / "vendor" / "virgil" / "lib"))
        self.assertEqual(provenance["configurationSha256"], {"main": sha256(REPO / "editors" / "nvim" / "virgil_lsp.lua")})
        self.assertEqual(start["coverageSha256"], sha256(HERE / "coverage.json"))
        text = self.stream.read_text()
        for path in (str(self.dir), str(REPO), os.path.expanduser("~")):
            self.assertNotIn(path, text)
        # The events are in check.lua's order, between the launcher's own.
        self.assertEqual([event.get("id") for event in self.events()[1:-1]],
                         ["nvim.infrastructure.preflight", *ORDER, "nvim.infrastructure.completed"])

    def test_uri_paths_in_failure_are_redacted(self):
        server_dir = self.dir / "release build"
        self.server_dir.rename(server_dir)
        self.server_dir = server_dir
        message = " | ".join(f"{form}/project/bad.v3" for form in ("<WORK>", "<WORK_ENCODED>", "<WORK_URI>"))
        self.scenario(lines=[json.dumps({"observation": "<WORK_URI>/project/bad.v3"}),
                             failing(ORDER[0], message)], exit=1)
        self.assertFailed(self.run_sh())
        failed = self.checks()[ORDER[0]]
        expected = " | ".join(["<work>/project/bad.v3"] * 3)
        self.assertEqual(failed["observation"], expected)
        self.assertEqual(failed["reason"], expected)
        text = self.stream.read_text()
        self.assertIn("<work>/project/bad.v3", self.events()[-1]["issues"][0])
        for path in (str(server_dir), quote(str(server_dir), safe="/"), server_dir.as_uri()):
            self.assertNotIn(path, text)

    def test_release_options(self):
        self.scenario(lines=[passing(check_id) for check_id in ORDER])
        result = self.run_sh("--release", "0.1.0-alpha.6", "--purpose", "release")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        start = self.validate().start
        self.assertEqual((start["release"], start["purpose"]), ("0.1.0-alpha.6", "release"))
        self.assertEqual(start["command"][2:], ["--release", "0.1.0-alpha.6", "--purpose", "release",
                                                "--results", "<results>"])

    def test_usage(self):
        for args in (["--release", "1"], ["--purpose", "release"], ["--bogus"]):
            self.assertEqual(self.run_sh(*args, results=False).returncode, 2, args)
        self.assertEqual(self.run_sh("--purpose", "later").returncode, 2)
        missing = self.run_sh("--results", str(self.dir / "missing" / "nvim.jsonl"), results=False)
        self.assertEqual(missing.returncode, 2)

    def test_check_failure(self):
        message = ("...t3/repo/test/e2e/nvim/check.lua:65: timed out waiting for semantic diagnostics in "
                   "<WORK>/project/words/Words.v3")
        self.scenario(lines=[*map(passing, ORDER[:3]), failing(ORDER[3], message)], exit=1)
        result = self.run_sh()
        self.assertEqual(result.returncode, 1)
        run = self.assertFailed(result)
        checks = self.checks()
        failed = checks[ORDER[3]]
        self.assertEqual((failed["status"], failed["failureCode"]), ("fail", "wait-timeout"))
        self.assertEqual(failed["reason"], "test/e2e/nvim/check.lua:65: timed out waiting for semantic diagnostics "
                                           "in <work>/project/words/Words.v3")
        for check_id in ORDER[4:]:
            self.assertEqual(checks[check_id]["status"], "skip")
            self.assertIn(ORDER[3], checks[check_id]["reason"])
        completed = checks["nvim.infrastructure.completed"]
        self.assertEqual((completed["status"], completed["failureCode"]), ("fail", "exit-status"))
        self.assertTrue(run.end["complete"])
        self.assertEqual(run.end["counts"], {"pass": 4, "fail": 2, "xfail": 0, "xpass": 0, "skip": 29})
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertNoWork()
        self.assertNotIn(str(self.dir), self.stream.read_text())

    def test_missing_observations(self):
        """A driver that stops reporting, and exits 0, leaves the run incomplete,
        never passing; its missing checks aren't skipped, since nothing failed."""
        self.scenario(lines=[passing(check_id) for check_id in ORDER[:-2]])
        run = self.assertFailed(self.run_sh())
        self.assertFalse(run.end["complete"])
        self.assertEqual(run.end["checkCount"], 33)
        self.assertEqual(run.end["counts"]["skip"], 0)
        self.assertTrue(any(ORDER[-2] in issue for issue in run.end["issues"]), run.end["issues"])
        self.assertEqual(self.checks()["nvim.infrastructure.completed"]["status"], "pass")

    def test_failure_outside_a_check(self):
        record = json.dumps({"id": None, "status": "fail", "startedAt": 0, "endedAt": 0,
                             "observation": "attempt to index a nil value", "reason": "attempt to index a nil value"})
        self.scenario(lines=[*map(passing, ORDER[:2]), record], exit=1)
        run = self.assertFailed(self.run_sh())
        self.assertFalse(run.end["complete"])
        self.assertEqual(run.end["counts"]["skip"], 0)
        self.assertTrue(any("malformed observation" in issue for issue in run.end["issues"]), run.end["issues"])

    def test_reports_that_arent_observations(self):
        """Every check passes, but the driver also reported a line that isn't
        JSON, an unplanned check, a repeat, a wrapper check, and a cut-off
        line: each is an issue, so the run fails."""
        lines = [*map(passing, ORDER), "not json", passing("nvim.hover.popup"), passing(ORDER[0]),
                 passing("nvim.infrastructure.completed"), '{"id": "nvim.hover.response"']
        self.scenario(lines=lines[:-1])
        run = self.assertFailed(self.run_sh())
        self.assertTrue(run.end["complete"])
        self.assertEqual(len(run.end["issues"]), 4, run.end["issues"])
        self.scenario(lines=list(map(passing, ORDER)), partial=lines[-1])
        run = self.assertFailed(self.run_sh())
        self.assertEqual(run.end["issues"], ["check.lua's last observation is incomplete"])

    def test_without_results_unchanged(self):
        """Without --results, a pass, a failure, and a missing Neovim behave as
        they always have, and nothing is written."""
        self.scenario()
        result = self.run_sh(results=False)
        self.assertEqual((result.returncode, result.stdout), (0, "Neovim smoke test: NVIM v0.11.6\n"))
        self.scenario(exit=1)
        self.assertEqual(self.run_sh(results=False).returncode, 1)
        self.assertNoWork()
        env = dict(self.env, PATH=str(self.tools()))
        result = self.run_sh(results=False, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "SKIP: Neovim smoke test requires Neovim 0.11+: nvim not found on PATH\n")
        self.assertEqual(self.run_sh(results=False, env=dict(env, REQUIRE_NVIM="1")).returncode, 1)
        self.assertFalse(self.stream.exists())

    def tools(self):
        """A PATH directory with what run.sh needs before it runs Neovim, but
        no nvim."""
        tools = self.dir / "tools"
        tools.mkdir(exist_ok=True)
        for name in ("bash", "dirname", "basename", "date", "python3", "git", "sysctl"):
            found = shutil.which(name)
            if found and not (tools / name).exists():
                (tools / name).symlink_to(found)
        return tools

    def test_without_neovim(self):
        """A missing Neovim fails a run with --results, even without
        REQUIRE_NVIM, with a valid stream that says why."""
        result = self.run_sh(env=dict(self.env, PATH=str(self.tools())))
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: Neovim smoke test requires Neovim 0.11+: nvim not found on PATH", result.stderr)
        run = self.assertFailed(result, "unavailable")
        self.assertTrue(run.end["complete"])
        checks = self.checks()
        preflight = checks["nvim.infrastructure.preflight"]
        self.assertEqual((preflight["status"], preflight["failureCode"]), ("fail", "neovim-missing"))
        self.assertEqual({event["status"] for check_id, event in checks.items() if check_id != preflight["id"]},
                         {"skip"})
        provenance = run.start["provenance"]
        self.assertIsNone(provenance["editor"]["version"])
        self.assertEqual(provenance["fixtureSha256"]["value"], None)
        self.assertIn("preflight", provenance["fixtureSha256"]["unavailable"])
        self.assertEqual(provenance["server"]["sha256"], sha256(SERVER))
        self.assertNoWork()

    def test_old_neovim(self):
        self.env["FAKE_NVIM_VERSION"] = "NVIM v0.10.4"
        run = self.assertFailed(self.run_sh(), "unavailable")
        self.assertEqual(run.start["provenance"]["editor"]["version"], "0.10.4")
        self.assertEqual(self.checks()["nvim.infrastructure.preflight"]["failureCode"], "neovim-too-old")

    def test_missing_worker(self):
        # The server starts the worker beside its physical path.
        (self.server_dir / "virgil-lsp").unlink()
        shutil.copy2(SERVER, self.server_dir / "virgil-lsp")
        (self.server_dir / "virgil-lsp-worker").unlink()
        run = self.assertFailed(self.run_sh(), "unavailable")
        server = run.start["provenance"]["server"]
        self.assertEqual(server["sha256"], sha256(SERVER))
        self.assertIsNone(server["workerSha256"]["value"])
        self.assertEqual(self.checks()["nvim.infrastructure.preflight"]["failureCode"], "server-missing")
        self.assertIn("unavailable", self.run_sh().stdout)

    def test_lingering_process(self):
        """A process that outlives Neovim fails the run, and is stopped."""
        self.scenario(lines=[passing(check_id) for check_id in ORDER], linger=True)
        run = self.assertFailed(self.run_sh())
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertTrue(any("still running" in issue for issue in run.end["issues"]), run.end["issues"])
        self.assertStopped()

    def test_detached_process_after_normal_exit(self):
        self.scenario(lines=[passing(check_id) for check_id in ORDER], detached=True)
        run = self.assertFailed(self.run_sh())
        self.assertTrue(any("still running" in issue for issue in run.end["issues"]), run.end["issues"])
        self.assertStopped()
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertNoWork()

    @unittest.skipUnless(sys.platform == "linux", "adoption of unsampled children requires Linux")
    def test_immediate_failure_stops_detached_child(self):
        self.scenario(lines=[failing(ORDER[0], "immediate failure")], detached=True, sleep=0, exit=1)
        run = self.assertFailed(self.run_sh())
        self.assertStopped()
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertNoWork()

    def test_signal_during_finalization(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                self.scenario(lines=[failing(ORDER[0], "failed")] if failed else list(map(passing, ORDER)),
                              exit=1 if failed else 0)
                self.env["VIRGIL_NVIM_END_DELAY"] = "2"
                with self.start_run_sh() as process:
                    wait_for_finalizer(process.pid)
                    os.kill(process.pid, signal.SIGTERM)
                    process.wait(timeout=20)
                run = self.validate()
                self.assertEqual(process.returncode, 1 if failed else 0)
                self.assertEqual(run.end["exitCode"], process.returncode)
                self.assertEqual(run.end["status"], "fail" if failed else "pass")
                self.assertStopped()
                self.assertNoWork()

    def test_detached_process_after_launcher_signal(self):
        self.scenario(lines=[passing(ORDER[0])], sleep=60, detached=True)
        process = self.start_run_sh()
        self.wait_for_events(3)
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=20)
        self.assertNotEqual(process.returncode, 0)
        self.assertInvalid(self.stream.read_bytes(), "incomplete: no run-end")
        self.assertStopped()
        self.assertNoWork()

    def test_malformed_observation_fields(self):
        for changes in ({"startedAt": True}, {"endedAt": False}, {"id": []}, {"id": {}},
                        {"startedAt": float("nan")}, {"endedAt": float("inf")},
                        {"endedAt": 10 ** 1000}, {"startedAt": 0},
                        {"startedAt": "<LATER>", "endedAt": "<NOW>"},
                        {"observation": []}, {"status": []}, {"reason": []}, {"failureCode": []}):
            for status in ("pass", "fail"):
                with self.subTest(changes=changes, status=status):
                    record = json.loads(passing(ORDER[0]) if status == "pass" else failing(ORDER[0], "failed"))
                    record.update(changes)
                    if status == "pass" and ("reason" in changes or "failureCode" in changes):
                        continue
                    self.scenario(lines=[json.dumps(record), *map(passing, ORDER)])
                    run = self.assertFailed(self.run_sh())
                    self.assertTrue(run.end["complete"])
                    self.assertTrue(any("malformed observation" in issue for issue in run.end["issues"]),
                                    run.end["issues"])
                    self.assertEqual(run.end["cleanup"], "verified")
                    self.assertStopped()
                    self.assertNoWork()

    def test_temporary_directory_left(self):
        self.scenario(lines=[passing(check_id) for check_id in ORDER], lock=True)
        run = self.assertFailed(self.run_sh())
        self.assertEqual(run.end["cleanup"], "failed")
        self.assertIn("the temporary directory remains after its removal", run.end["issues"])

    def start_run_sh(self):
        return subprocess.Popen(["bash", str(RUN), str(self.server_dir / "virgil-lsp"), "--results", str(self.stream)],
                                env=self.env, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)

    def wait_for_events(self, count):
        deadline = time.monotonic() + 20
        while not (self.stream.exists() and len(self.stream.read_bytes().splitlines()) >= count):
            self.assertLess(time.monotonic(), deadline, "the stream didn't grow")
            time.sleep(0.05)

    def test_interrupted(self):
        """An interrupt keeps the events written so far, stops Neovim, removes
        the temporary directory, and writes no run-end."""
        self.scenario(lines=[passing(check_id) for check_id in ORDER[:2]], sleep=60)
        process = self.start_run_sh()
        self.wait_for_events(4)
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=20)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual([event["event"] for event in self.events()], ["run-start", "check", "check", "check"])
        self.assertInvalid(self.stream.read_bytes(), "incomplete: no run-end")
        self.assertStopped()
        self.assertNoWork()

    def test_killed(self):
        """A killed launcher leaves its events, and an incomplete stream."""
        self.scenario(lines=[passing(check_id) for check_id in ORDER[:2]], sleep=60)
        process = self.start_run_sh()
        self.wait_for_events(4)
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=20)
        self.assertEqual(len(self.events()), 4)
        self.assertInvalid(self.stream.read_bytes(), "incomplete: no run-end")

    def test_early_interruption(self):
        """A run stopped before it resolves its paths leaves no earlier run's
        stream behind: a passing stream is emptied before the first command."""
        self.scenario(lines=[passing(check_id) for check_id in ORDER])
        self.assertEqual(self.run_sh().returncode, 0)
        self.assertTrue(self.validate().passed)
        marker = self.dir / "dirname-started"
        dirname = self.bin / "dirname"
        dirname.write_text(f"""#!{sys.executable}
import os, sys, time
with open({str(self.pidfile)!r}, "a") as file:
    file.write(f"{{os.getpid()}}\\n")
open({str(marker)!r}, "w").close()
time.sleep(30)
os.execv({shutil.which("dirname")!r}, ["dirname", *sys.argv[1:]])
""")
        dirname.chmod(0o755)
        process = self.start_run_sh()
        deadline = time.monotonic() + 20
        while not marker.exists():
            self.assertLess(time.monotonic(), deadline, "run.sh didn't run dirname")
            time.sleep(0.05)
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=20)
        self.assertNotEqual(process.returncode, 0)
        self.assertInvalid(self.stream.read_bytes(), "the stream is empty")

    def test_signal_to_launcher_only(self):
        """A signal to run.sh alone, not its process group, stops Neovim at
        once, through the wrapper, and leaves an incomplete stream."""
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=signum.name):
                self.scenario(lines=[passing(check_id) for check_id in ORDER[:2]], sleep=60)
                process = self.start_run_sh()
                self.wait_for_events(4)
                signalled = time.monotonic()
                os.kill(process.pid, signum)
                process.wait(timeout=20)
                self.assertLess(time.monotonic() - signalled, 5)
                # Bash exits with 128 + the signal, or by the signal itself.
                self.assertIn(process.returncode, (128 + signum, -signum))
                self.assertEqual([event["event"] for event in self.events()],
                                 ["run-start", "check", "check", "check"])
                self.assertInvalid(self.stream.read_bytes(), "incomplete: no run-end")
                self.assertStopped()
                self.assertNoWork()

    def test_deadline(self):
        """results.py run stops Neovim at its deadline, which fails the run.
        run.sh's deadline is 120 seconds, so this runs the steps directly."""
        self.scenario(lines=[passing(ORDER[0])], sleep=60, detached=True)
        work = Path(tempfile.mkdtemp(dir=self.server_dir, prefix="nvim-smoke."))
        shutil.copytree(HERE / "nvim" / "fixture", work / "project")
        common = ["--stream", str(self.stream), "--server", str(self.server_dir / "virgil-lsp"),
                  "--nvim-version", "NVIM v0.11.6"]
        command = ["--", "test/e2e/nvim/run.sh", "--results", str(self.stream)]

        def step(*args, check=True):
            result = subprocess.run([sys.executable, str(RESULTS), *args], env=self.env, cwd=REPO,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            if check:
                self.assertEqual(result.returncode, 0, result.stderr)
            return result

        step("start", *common, "--fixture", str(work / "project"), *command)
        ran = step("run", "--stream", str(self.stream), "--work", str(work), "--deadline", "1", "--",
                   str(self.bin / "nvim"), "--headless", check=False)
        self.assertEqual(ran.returncode, 1)
        self.assertIn("FAIL: Neovim smoke test exceeded 1 seconds", ran.stderr)
        ended = step("end", *common, "--work", str(work), "--exit-code", "1", "--preflight-passed", *command,
                     check=False)
        self.assertEqual(ended.returncode, 1)
        run = self.validate()
        self.assertEqual(run.end["status"], "fail")
        completed = self.checks()["nvim.infrastructure.completed"]
        self.assertEqual((completed["status"], completed["failureCode"]), ("fail", "deadline"))
        self.assertIn("Neovim exceeded its 1-second deadline", run.end["issues"])
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertStopped()
        self.assertFalse(work.exists())

    def test_mismatches_are_invalid(self):
        """The validator rejects a writer's stream that misreports its run."""
        self.scenario(lines=[passing(check_id) for check_id in ORDER])
        self.assertEqual(self.run_sh().returncode, 0)
        lines = self.stream.read_bytes().splitlines(keepends=True)

        def edited(index, **changes):
            event = json.loads(lines[index])
            event.update(changes)
            return b"".join(lines[:index] + [json.dumps(event).encode() + b"\n"] + lines[index + 1:])

        self.validate()
        # A failed check that the run-end still counts as a pass.
        self.assertInvalid(edited(5, status="fail", reason="failed", failureCode="error"), "counts.pass is 35")
        # A pass without the check's evidence level.
        self.assertInvalid(edited(5, level="ui-state"), "level is ui-state")
        # A complete run that lost a check.
        dropped = [json.loads(line) for line in lines[:5] + lines[6:]]
        for seq, event in enumerate(dropped):
            event["seq"] = seq
        dropped[-1]["checkCount"] = 34
        dropped[-1]["counts"]["pass"] = 34
        self.assertInvalid(b"".join(json.dumps(event).encode() + b"\n" for event in dropped),
                           "complete is true, but")
        # A pass whose launcher exited with a failure.
        self.assertInvalid(edited(len(lines) - 1, exitCode=1), "exitCode")
        # Another manifest's plan.
        self.assertInvalid(edited(0, coverageSha256="0" * 64), "another coverage.json")


if __name__ == "__main__":
    unittest.main()
