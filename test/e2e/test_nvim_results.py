#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Tests the headless Neovim test's result stream, run.sh --results
(test/e2e/RESULTS.md). check.lua's checks must be coverage.json's, and every
line of its check() must belong to one. A stand-in nvim on PATH reports
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RUN = HERE / "nvim" / "run.sh"
RESULTS = HERE / "nvim" / "results.py"
CHECK = HERE / "nvim" / "check.lua"
MANIFEST = json.loads((HERE / "coverage.json").read_bytes())
SERVER = Path(sys.argv.pop(1) if len(sys.argv) > 1 else REPO / "build" / "virgil-lsp").resolve()
NVIM_CHECKS = {check["id"]: check for check in MANIFEST["checks"] if "nvim" in check["suites"]}
WRAPPER = {"nvim.infrastructure.preflight", "nvim.infrastructure.completed"}


def load_validator():
    spec = importlib.util.spec_from_file_location("editor_results", REPO / "scripts" / "editor-results.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_validator()
VALIDATOR_MANIFEST = validator.Manifest.load(validator.MANIFEST)
VALIDATOR_SCHEMA = validator.Schema.load(validator.SCHEMA)


def lua_code(line):
    """A line of Lua without its string literals and comment."""
    line = re.sub(r'"(\\.|[^"\\])*"|\'(\\.|[^\'\\])*\'', '""', line)
    return line.split("--", 1)[0]


def check_body():
    """The lines of check.lua's check(), without its first and last."""
    lines = CHECK.read_text().split("\n")
    first = lines.index("local function check()")
    last = next(index for index in range(first, len(lines)) if lines[index] == "end")
    return lines[first + 1:last]


def begun():
    return re.findall(r'^\s*begin\("([^"]+)"\)', CHECK.read_text(), re.M)


# check.lua's checks, in the order that it runs them.
ORDER = begun()


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
    moment = int(time.time() * 1000)
    return json.dumps({"id": check_id, "status": "pass", "startedAt": moment, "endedAt": moment + 1,
                       "observation": f"{check_id} observed"})


def failing(check_id, message):
    moment = int(time.time() * 1000)
    return json.dumps({"id": check_id, "status": "fail", "startedAt": moment, "endedAt": moment + 1,
                       "observation": message, "reason": message, "failureCode": "wait-timeout"})


# The stand-in nvim: --version prints FAKE_NVIM_VERSION, and otherwise it
# appends FAKE_NVIM's lines to the observations file, with <WORK> as the
# temporary directory, then its partial line without an LF, does what else it
# says, and exits with its status.
FAKE_NVIM = """#!{python}
import json, os, subprocess, sys, time
if sys.argv[1:] == ["--version"]:
    print(os.environ.get("FAKE_NVIM_VERSION", "NVIM v0.11.6\\nBuild type: Release"))
    sys.exit(0)
scenario = json.loads(os.environ.get("FAKE_NVIM", "{{}}"))
path = os.environ.get("VIRGIL_NVIM_OBSERVATIONS")
work = os.path.dirname(path) if path else None
if scenario.get("pidfile"):
    with open(scenario["pidfile"], "a") as file:
        file.write(f"{{os.getpid()}}\\n")
if path:
    with open(path, "a") as out:
        for line in scenario.get("lines", []):
            out.write(line.replace("<WORK>", work) + "\\n")
        out.write(scenario.get("partial", ""))
        out.flush()
if scenario.get("linger"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(scenario["pidfile"], "a") as file:
        file.write(f"{{child.pid}}\\n")
if scenario.get("lock"):
    os.makedirs(os.path.join(work, "locked", "inside"))
    os.chmod(os.path.join(work, "locked"), 0o555)
time.sleep(scenario.get("sleep", 0))
sys.exit(scenario.get("exit", 0))
"""


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


class CheckLuaTest(unittest.TestCase):
    def test_checks_are_the_manifests(self):
        self.assertEqual(len(ORDER), len(set(ORDER)), "check.lua begins a check twice")
        main = {check_id for check_id, check in NVIM_CHECKS.items() if check["profiles"] == ["main"]}
        self.assertEqual(set(ORDER), main)
        self.assertEqual(set(NVIM_CHECKS) - main, WRAPPER)
        for check_id in WRAPPER:
            self.assertEqual(NVIM_CHECKS[check_id]["profiles"], ["wrapper"])

    def test_every_line_belongs_to_a_check(self):
        """Each begin() is followed by one passed(), and between a passed()
        and the next begin() are only comments and blank lines, so that a
        failure anywhere in check() is attributed to a check."""
        body = check_body()
        code = [(number, lua_code(line).strip()) for number, line in enumerate(body)]
        code = [(number, line) for number, line in code if line]
        self.assertTrue(code[0][1].startswith("begin("), body[code[0][0]])
        open_check = False
        expect_begin = False
        depth = 0
        for number, line in code:
            if depth == 0 and expect_begin:
                self.assertTrue(line.startswith("begin("), f"check.lua: {body[number]!r} is outside a check")
                expect_begin = False
            if depth == 0 and line.startswith("begin("):
                self.assertFalse(open_check, f"check.lua: {body[number]!r} begins before a passed()")
                open_check = True
            elif depth == 0 and line.startswith("passed("):
                self.assertTrue(open_check, f"check.lua: {body[number]!r} has no begin()")
                open_check = False
                depth = line.count("(") - line.count(")")
                expect_begin = depth == 0
                continue
            if depth:
                depth += line.count("(") - line.count(")")
                expect_begin = depth == 0
        self.assertFalse(open_check, "check.lua's last check has no passed()")


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
        self.assertTrue(any("outside a check" in issue for issue in run.end["issues"]), run.end["issues"])

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
        """A process that outlives Neovim fails the cleanup, and is stopped."""
        self.scenario(lines=[passing(check_id) for check_id in ORDER], linger=True)
        run = self.assertFailed(self.run_sh())
        self.assertEqual(run.end["cleanup"], "failed")
        self.assertTrue(any("still running" in issue for issue in run.end["issues"]), run.end["issues"])
        self.assertStopped()

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
        self.scenario(lines=[passing(ORDER[0])], sleep=60)
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
