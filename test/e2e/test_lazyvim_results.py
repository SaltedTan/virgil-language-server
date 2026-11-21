#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Tests the LazyVim test's result stream, run.sh --results
(test/e2e/RESULTS.md), without the network or tmux that a real run needs.
lazyvim/results.py writes streams from synthetic observations, as run.sh and
driver.lua record them, for passing, failing, stopped, and interrupted runs,
and scripts/editor-results.py validates each one. run.sh itself runs up to
the point where it would clone the starter, with stand-ins for nvim, tmux,
git, and the server, and with --cache, from a stand-in cache until it would
start the first profile. make test runs this file from its own SUITES
entry."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RUN = HERE / "lazyvim" / "run.sh"
MANIFEST = json.loads((HERE / "coverage.json").read_bytes())
CHECKS = {check["id"]: check for check in MANIFEST["checks"] if "lazyvim" in check["suites"]}
RULE = next(rule for rule in MANIFEST["xfails"] if rule["check"] in CHECKS)
STARTER_COMMIT = "803bc181d7c0d6d5eeba9274d9be49b287294d99"
WRAPPER_BEFORE = ["lazyvim.infrastructure.preflight", "lazyvim.infrastructure.artifact-start",
                  "lazyvim.infrastructure.starter", "lazyvim.infrastructure.plugins-install",
                  "lazyvim.infrastructure.pins-before", "lazyvim.infrastructure.parsers-ready"]
PROFILES = ["main", "noroot", "noroot-noice-workaround"]
SERVER = {"versionText": "virgil-lsp 0.1.0-test", "version": "0.1.0-test", "sourceRevision": None,
          "virgilRevision": None, "target": "x86-64-linux", "sha256": "1" * 64, "workerSha256": "2" * 64}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


results = load("lazyvim_results", HERE / "lazyvim" / "results.py")
validator = load("editor_results", REPO / "scripts" / "editor-results.py")
VALIDATOR_MANIFEST = validator.Manifest.load(validator.MANIFEST)
VALIDATOR_SCHEMA = validator.Schema.load(validator.SCHEMA)


def by(check_id):
    return "driver" if CHECKS[check_id]["source"].endswith("driver.lua") else "launcher"


def driver_checks(profile, watching=False, builtin=False):
    """The driver's steps in a profile, of the sides that the facts select."""
    sides = {"file-watching": watching, "no-file-watching": not watching,
             "builtin-lsp-command": builtin, "lspconfig-restart-command": not builtin}
    return [check_id for check_id, check in CHECKS.items()
            if by(check_id) == "driver" and profile in check["profiles"]
            and (check["when"] is None or sides[check["when"]])]


class Observations:
    """Builds an observations file as run.sh and driver.lua write it."""

    def __init__(self, path):
        self.path = path
        self.clock = time.time() - 120
        self.lines = []
        self.tail = ""    # a partial last line

    def add(self, **entry):
        self.lines.append(json.dumps(entry))

    def check(self, check_id, profile="wrapper", status="pass", code=None, stopped=False):
        self.clock += 0.5
        entry = {"kind": "check", "by": by(check_id), "id": check_id, "profile": profile, "status": status,
                 "startedAt": self.clock * 1000, "endedAt": (self.clock + 0.25) * 1000,
                 "observation": f"{check_id} observed", "reason": None, "failureCode": None}
        if status == "fail":
            entry.update(reason=f"{check_id} failed", failureCode=code or "error")
        if stopped:
            entry["stopped"] = True
        self.add(**entry)

    def launcher(self, until=None, stopped_code="error"):
        """run.sh's checks before the profiles, up to a check that stops it."""
        for check_id in WRAPPER_BEFORE:
            if check_id == until:
                self.check(check_id, status="fail", code=stopped_code, stopped=True)
                return
            self.check(check_id)
            if check_id == "lazyvim.infrastructure.artifact-start":
                self.add(kind="server", server=SERVER, archive=None)
        self.add(kind="digest", name="fixture", sha256="3" * 64)

    def profile(self, profile, watching=False, builtin=False, statuses=None, done=True, complete=True):
        statuses = statuses or {}
        self.add(kind="digest", name=f"configuration:{profile}",
                 sha256=("5" if profile == "noroot-noice-workaround" else "4") * 64)
        for check_id in driver_checks(profile, watching, builtin):
            if check_id == "lazyvim.configuration.disk-watch" or check_id == "lazyvim.configuration.disk-next-edit":
                self.add(kind="fact", by="driver", profile=profile, fact="didChangeWatchedFilesDynamicRegistration",
                         value=True if watching else None)
            if check_id in ("lazyvim.restart.builtin", "lazyvim.restart.legacy"):
                self.add(kind="fact", by="driver", profile=profile, fact="neovimBuiltinLspCommand", value=builtin)
            status = statuses.get(check_id, "pass")
            if status is None:
                continue
            if status == "stop":
                break
            if status == "warning-not-observed":
                self.check(check_id, profile, "fail", code=RULE["failureCode"])
            else:
                self.check(check_id, profile, status)
        if done:
            self.add(kind="done", by="driver", profile=profile)
        if complete is not None:
            self.check("lazyvim.infrastructure.profile-complete", profile, "pass" if complete else "fail",
                       code=None if complete else "check-failed")
            self.check("lazyvim.infrastructure.profile-processes", profile)

    def stock_noroot(self, **statuses):
        """The noroot profile, with the warning unobserved, as noice.nvim at the
        rule's commit leaves it."""
        self.profile("noroot", statuses={RULE["check"]: "warning-not-observed", **statuses})

    def finish(self, pins_after=True, cleanup=True):
        if pins_after:
            self.check("lazyvim.infrastructure.pins-after")
        self.check("lazyvim.infrastructure.cleanup", status="pass" if cleanup else "fail", code="processes")

    def write(self):
        Path(self.path).write_text("".join(line + "\n" for line in self.lines) + self.tail)


class WriterTest(unittest.TestCase):
    """results.py end, on synthetic observations."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="lazyvim-results-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.stream = self.dir / "lazyvim.jsonl"
        self.obs = Observations(self.dir / "observations")

    def end(self, code=0, *extra, work=True):
        """Runs results.py end, as run.sh does once it removed its work
        directory, or before it created one."""
        self.obs.write()
        args = ["end", str(self.obs.path), "--stream", str(self.stream), "--exit-code", str(code),
                "--starter-commit", STARTER_COMMIT, "--nvim-version", "NVIM v0.11.6",
                "--launched-at", str(time.time() - 130), *(["--work", str(self.dir / "work-removed")] if work else []),
                *extra, "--", "test/e2e/lazyvim/run.sh", "build/virgil-lsp"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = results.main(args)
        self.assertFalse(Path(self.obs.path).exists(), "the observations file remains")
        return status

    def validate(self):
        try:
            return validator.validate_stream(self.stream.read_bytes(), VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)
        except validator.Invalid as error:
            self.fail(f"invalid stream: {error}\n{self.stream.read_text()}")

    def checks(self):
        return {(event["id"], event["profile"]): event for event in map(json.loads, self.stream.read_text().splitlines())
                if event["event"] == "check"}

    def passing(self, watching=False, builtin=False):
        self.obs.launcher()
        self.obs.profile("main", watching, builtin)
        self.obs.stock_noroot()
        self.obs.profile("noroot-noice-workaround")
        self.obs.finish()

    def test_passing_run(self):
        """Every check passes, and the stock warning is the expected failure."""
        self.passing()
        self.assertEqual(self.end(), 0)
        run = self.validate()
        self.assertTrue(run.passed, run.end)
        self.assertEqual(run.end["counts"]["xfail"], 1)
        self.assertEqual(run.end["counts"]["pass"], run.end["expectedCount"] - 1)
        start = run.start
        self.assertEqual(start["facts"], {"didChangeWatchedFilesDynamicRegistration": None,
                                          "neovimBuiltinLspCommand": False})
        planned = {key["id"] for key in start["expected"]}
        self.assertIn("lazyvim.configuration.disk-next-edit", planned)
        self.assertIn("lazyvim.restart.legacy", planned)
        self.assertNotIn("lazyvim.configuration.disk-watch", planned)
        self.assertNotIn("lazyvim.restart.builtin", planned)
        self.assertEqual(set(start["provenance"]["configurationSha256"]), set(PROFILES))
        pins = {pin["name"]: pin["commit"] for pin in start["provenance"]["pins"]}
        self.assertEqual(pins[RULE["plugin"]], RULE["commit"])
        self.assertEqual(pins["LazyVim/starter"], STARTER_COMMIT)
        warning = self.checks()[(RULE["check"], RULE["profile"])]
        self.assertEqual((warning["status"], warning["expectation"], warning["failureCode"]),
                         ("xfail", RULE["id"], RULE["failureCode"]))
        self.assertEqual(self.checks()[(RULE["check"], RULE["workaroundProfile"])]["status"], "pass")
        self.assertEqual(start["command"], ["test/e2e/lazyvim/run.sh", "build/virgil-lsp"])

    def test_other_sides_of_the_alternatives(self):
        """A client that declares file watching, in Neovim with :lsp, plans the
        other side of each pair."""
        self.passing(watching=True, builtin=True)
        self.assertEqual(self.end(), 0)
        run = self.validate()
        self.assertTrue(run.passed, run.end)
        planned = {key["id"] for key in run.start["expected"]}
        self.assertTrue({"lazyvim.configuration.disk-watch", "lazyvim.restart.builtin"} <= planned)
        self.assertFalse({"lazyvim.configuration.disk-next-edit", "lazyvim.restart.legacy"} & planned)

    def test_unexpected_pass(self):
        """The stock warning observed is an xpass, which fails the run."""
        self.obs.launcher()
        self.obs.profile("main")
        self.obs.profile("noroot", complete=False)
        self.obs.profile("noroot-noice-workaround")
        self.obs.finish()
        self.assertEqual(self.end(1), 1)
        run = self.validate()
        self.assertEqual((run.end["status"], run.end["counts"]["xpass"]), ("fail", 1))
        self.assertEqual(self.checks()[(RULE["check"], "noroot")]["expectation"], RULE["id"])

    def test_expected_failure_needs_its_prerequisites(self):
        """Without its prerequisites, the unobserved warning is a fail."""
        self.obs.launcher()
        self.obs.profile("main")
        self.obs.profile("noroot", complete=False, statuses={RULE["check"]: "warning-not-observed",
                                                             RULE["requires"][-1]: "fail"})
        self.obs.profile("noroot-noice-workaround")
        self.obs.finish()
        self.end(1)
        run = self.validate()
        self.assertEqual(self.checks()[(RULE["check"], "noroot")]["status"], "fail")
        self.assertEqual(run.end["counts"]["xfail"], 0)

    def test_expected_failure_needs_its_failure_code(self):
        self.obs.launcher()
        self.obs.profile("main")
        self.obs.profile("noroot", complete=False, statuses={RULE["check"]: "fail"})
        self.obs.profile("noroot-noice-workaround")
        self.obs.finish()
        self.end(1)
        self.validate()
        self.assertEqual(self.checks()[(RULE["check"], "noroot")]["status"], "fail")

    def test_workaround_profile_fails_normally(self):
        """In the workaround profile, an unobserved warning is a fail."""
        self.obs.launcher()
        self.obs.profile("main")
        self.obs.stock_noroot()
        self.obs.profile("noroot-noice-workaround", complete=False,
                         statuses={RULE["check"]: "warning-not-observed"})
        self.obs.finish()
        self.end(1)
        self.validate()
        self.assertEqual(self.checks()[(RULE["check"], "noroot-noice-workaround")]["status"], "fail")

    def test_failed_attach_skips_the_profile(self):
        """driver.lua finishes main early when its client doesn't attach, so
        the steps it didn't run are skipped, after the attach's failure."""
        self.obs.launcher()
        self.obs.profile("main", complete=False, statuses={"lazyvim.startup.attach": "fail",
                                                           "lazyvim.diagnostics.parser": "stop"})
        self.obs.stock_noroot()
        self.obs.profile("noroot-noice-workaround")
        self.obs.finish()
        self.assertEqual(self.end(1), 1)
        run = self.validate()
        self.assertTrue(run.end["complete"])
        self.assertEqual(run.end["status"], "fail")
        skipped = [check for check in self.checks().values() if check["status"] == "skip"]
        self.assertTrue(skipped)
        self.assertTrue(all(check["profile"] == "main" for check in skipped))
        self.assertIn("lazyvim.startup.attach failed", skipped[0]["reason"])
        # The driver stopped before it decided the alternatives.
        self.assertIn("unavailable", run.start["facts"]["neovimBuiltinLspCommand"])

    def test_driver_error_leaves_the_profile_incomplete(self):
        """A failure outside a step leaves the steps after it unobserved."""
        self.obs.launcher()
        self.obs.profile("main", complete=False, done=False, statuses={"lazyvim.completion.keyword": "stop"})
        self.obs.add(kind="error", by="driver", profile="main", message="timed out waiting for Normal mode")
        self.obs.stock_noroot()
        self.obs.profile("noroot-noice-workaround")
        self.obs.finish()
        self.end(1)
        run = self.validate()
        self.assertFalse(run.end["complete"])
        self.assertEqual(run.end["counts"]["skip"], 0)
        self.assertTrue(any("driver.lua failed outside a check in profile main" in issue
                            for issue in run.end["issues"]), run.end["issues"])
        self.assertTrue(any("have no check event" in issue for issue in run.end["issues"]))

    def test_launcher_stop_skips_the_rest(self):
        """run.sh stops at a failed check of its own: every later check is
        skipped, and what it would have observed is unavailable."""
        self.obs.launcher(until="lazyvim.infrastructure.plugins-install", stopped_code="deadline")
        self.obs.check("lazyvim.infrastructure.cleanup")
        self.assertEqual(self.end(1), 1)
        run = self.validate()
        self.assertTrue(run.end["complete"])
        self.assertEqual(run.end["status"], "fail")
        self.assertEqual(run.end["counts"]["pass"], 4)
        self.assertEqual(run.end["counts"]["fail"], 1)
        self.assertEqual(run.end["counts"]["skip"], run.end["expectedCount"] - 5)
        self.assertIn("unavailable", run.start["provenance"]["fixtureSha256"])
        self.assertEqual(run.start["provenance"]["configurationSha256"], {})
        install = self.checks()[("lazyvim.infrastructure.plugins-install", "wrapper")]
        self.assertEqual(install["failureCode"], "deadline")

    def test_preflight_failure_is_unavailable(self):
        self.obs.launcher(until="lazyvim.infrastructure.preflight")
        self.end(1, work=False)
        run = self.validate()
        self.assertEqual(run.end["status"], "unavailable")
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertIn("unavailable", run.start["provenance"]["server"]["sha256"])

    def test_malformed_and_unexpected_observations(self):
        """Each observation that isn't one, or isn't planned, is an issue."""
        self.passing()
        self.obs.add(kind="check", by="driver", id="lazyvim.hover.response", profile="main", status="maybe")
        self.obs.check("lazyvim.hover.response", "main")
        self.obs.check("lazyvim.configuration.disk-watch", "main")
        self.obs.check("lazyvim.infrastructure.preflight", "main")
        self.obs.lines.append("not json")
        self.obs.tail = '{"kind": "check"'
        self.assertEqual(self.end(), 1)
        run = self.validate()
        issues = "\n".join(run.end["issues"])
        self.assertEqual(run.end["status"], "fail")
        self.assertIn("lazyvim.hover.response (main) was reported again", issues)
        self.assertIn("lazyvim.configuration.disk-watch (main) was reported, but the plan doesn't have it", issues)
        self.assertEqual(issues.count("a malformed observation"), 3, issues)
        self.assertIn("the last observation is incomplete", issues)

    def test_work_directory_left(self):
        self.passing()
        work = self.dir / "work-removed"
        work.mkdir()
        self.end()
        run = self.validate()
        self.assertEqual(run.end["cleanup"], "failed")
        self.assertIn("the work directory remains after its removal", run.end["issues"])
        self.assertFalse(run.passed)

    def test_kept_work_directory(self):
        """--keep-work keeps the work directory, which doesn't fail the run."""
        self.passing()
        (self.dir / "work-removed").mkdir()
        self.assertEqual(self.end(0, "--keep-work"), 0)
        self.assertTrue(self.validate().passed)

    def test_failed_cleanup(self):
        self.obs.launcher()
        for profile in PROFILES:
            if profile == "noroot":
                self.obs.stock_noroot()
            else:
                self.obs.profile(profile)
        self.obs.finish(cleanup=False)
        self.end(1)
        run = self.validate()
        self.assertEqual(run.end["cleanup"], "failed")

    def test_interrupted(self):
        """An interrupted run's stream has no run-end, so it is invalid."""
        self.passing()
        self.assertEqual(self.end(130, "--interrupted"), 130)
        with self.assertRaisesRegex(validator.Invalid, "incomplete: no run-end"):
            validator.validate_stream(self.stream.read_bytes(), VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)

    def test_paths_are_redacted(self):
        work = self.dir / "work"
        self.obs.launcher()
        self.obs.profile("main")
        self.obs.lines[-4] = self.obs.lines[-4].replace(
            "observed", f"observed in {work}/project and {REPO}/test and {Path('~').expanduser()}/x")
        self.obs.stock_noroot()
        self.obs.profile("noroot-noice-workaround")
        self.obs.finish()
        self.end(0, "--work", str(work))
        text = self.stream.read_text()
        self.assertIn("<work>/project and <repo>/test and ~/x", text)
        self.assertNotIn(str(self.dir), text)

    def test_mismatches_are_invalid(self):
        """The validator rejects a writer's stream that misreports its run."""
        self.passing()
        self.end()
        lines = self.stream.read_bytes().splitlines(keepends=True)
        events = [json.loads(line) for line in lines]

        def edited(index, **changes):
            event = dict(events[index], **changes)
            return b"".join(lines[:index] + [json.dumps(event).encode() + b"\n"] + lines[index + 1:])

        warning = next(index for index, event in enumerate(events) if event.get("status") == "xfail")
        with self.assertRaisesRegex(validator.Invalid, "makes a pass an xpass"):
            validator.validate_stream(edited(warning, status="pass", expectation=None, failureCode=None,
                                             reason=None), VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)
        with self.assertRaisesRegex(validator.Invalid, "which selects"):
            validator.validate_stream(edited(0, facts={"didChangeWatchedFilesDynamicRegistration": True,
                                                       "neovimBuiltinLspCommand": False}),
                                      VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)


class StandIns(unittest.TestCase):
    """Stand-ins that let run.sh run until it clones the starter."""

    def setUp(self):
        # tmux's socket path limits the work directory's, so it is short.
        self.dir = Path(tempfile.mkdtemp(prefix="lzr-", dir="/tmp")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.server = self.dir / "server"
        self.server.mkdir()
        git = shutil.which("git")
        # A stand-in cache's plugins are directories whose .head file holds
        # the commit that git reports for them. tmux starts no session, so a
        # run from a cache stops at its first profile.
        stand_ins = {
            self.bin / "nvim": 'if [ "$1" = --version ]; then echo "NVIM v0.11.6"; fi',
            self.bin / "tmux": 'case "$*" in *new-session*) exit 3 ;; esac; echo "tmux 0.0-test"',
            self.bin / "git": f'''if [ "$1" = clone ]; then echo "clone refused" >&2; exit 128; fi
if [ "$1" = -C ] && [ -f "$2/.head" ]; then
  case $3 in rev-parse) cat "$2/.head"; exit 0 ;; describe) echo v0-test; exit 0 ;; esac
fi
exec {git} "$@"''',
            self.server / "virgil-lsp": 'echo "virgil-lsp 0.1.0-test"',
            self.server / "virgil-lsp-worker": "exit 0",
        }
        for path, body in stand_ins.items():
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        self.tmp = self.dir / "tmp"
        self.tmp.mkdir()
        self.stream = self.dir / "lazyvim.jsonl"
        self.env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", TMPDIR=str(self.tmp))
        for name in ("GITHUB_ACTIONS", "VIRGIL_LAZYVIM_RUN_TIMEOUT", "VIRGIL_LAZYVIM_INSTALL_TIMEOUT"):
            self.env.pop(name, None)

    def run_sh(self, *args):
        """run.sh by its path from the checkout's root, as make test-lazyvim
        runs it, so that relative paths are resolved after its work directory
        is gone."""
        return subprocess.run(["bash", str(RUN.relative_to(REPO)), *args], env=self.env, cwd=REPO,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)

    def validate(self):
        try:
            return validator.validate_stream(self.stream.read_bytes(), VALIDATOR_MANIFEST, VALIDATOR_SCHEMA)
        except validator.Invalid as error:
            self.fail(f"invalid stream: {error}\n{self.stream.read_text()}")


class RunTest(StandIns):
    """run.sh --results, with the stand-ins."""

    def test_usage_error_empties_an_earlier_stream(self):
        # A stale stream left in FILE must not outlive a run that stops at its
        # arguments, wherever the bad argument is.
        for args in (["--results", str(self.stream), "--bogus"],
                     ["--results", str(self.stream), "--purpose", "bogus", str(self.server / "virgil-lsp")],
                     ["--bogus", "--results", str(self.stream)]):
            with self.subTest(args=args):
                self.stream.write_text('{"stale": true}\n')
                result = self.run_sh(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.stream.read_bytes(), b"")

    def test_stops_at_the_starter(self):
        result = self.run_sh("--out", str(self.dir / "out"), "--results", str(self.stream), "--release",
                             "0.1.0-test", str(self.server / "virgil-lsp"))
        # git clone's status, as without --results.
        self.assertEqual(result.returncode, 128, result.stdout + result.stderr)
        run = self.validate()
        checks = {event["id"]: event for event in run.checks if event["profile"] == "wrapper"}
        self.assertEqual(checks["lazyvim.infrastructure.preflight"]["status"], "pass")
        self.assertEqual(checks["lazyvim.infrastructure.artifact-start"]["status"], "pass")
        self.assertEqual(checks["lazyvim.infrastructure.starter"]["status"], "fail")
        self.assertEqual(checks["lazyvim.infrastructure.starter"]["failureCode"], "exit-status")
        self.assertEqual(checks["lazyvim.infrastructure.cleanup"]["status"], "pass")
        self.assertEqual(checks["lazyvim.infrastructure.plugins-install"]["status"], "skip")
        self.assertEqual(run.end["cleanup"], "verified")
        self.assertEqual(run.end["exitCode"], 128)
        self.assertEqual(run.start["release"], "0.1.0-test")
        self.assertEqual(run.start["command"], ["test/e2e/lazyvim/run.sh", "--out", "<dir>", "--results",
                                                "<results>", "--release", "0.1.0-test", "<server>"])
        # The stand-in server is a script, whose build target is unknown.
        self.assertIn("unavailable", run.start["provenance"]["server"]["target"])
        self.assertEqual(list(self.tmp.iterdir()), [], "run.sh left its work directory or observations")

    def test_preflight_failure(self):
        self.stream.write_text("an earlier run's stream\n")
        result = self.run_sh("--results", str(self.stream), str(self.dir / "missing"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("server not found", result.stderr)
        run = self.validate()
        self.assertEqual(run.end["status"], "unavailable")
        preflight = next(event for event in run.checks if event["id"] == "lazyvim.infrastructure.preflight")
        self.assertIn("server not found", preflight["reason"])
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_without_results_unchanged(self):
        result = self.run_sh(str(self.dir / "missing"))
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr, f"run.sh: server not found: {self.dir / 'missing'}\n")
        self.assertFalse(self.stream.exists())

    def test_usage(self):
        for args in (["--release", "1"], ["--results", str(self.stream), "--purpose", "other", "x"]):
            result = self.run_sh(*args, str(self.server / "virgil-lsp"))
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("usage:", result.stderr)


class CacheTest(StandIns):
    """run.sh --cache and --seed-cache, with the stand-ins."""

    def setUp(self):
        super().setUp()
        self.cache = self.dir / "cache"

    def identity(self, **changes):
        """The ready file's lines that run.sh compares, as it writes them."""
        def sha256(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()
        lines = {"starter": STARTER_COMMIT, "neovim": "NVIM v0.11.6",
                 "system": f"{platform.system()} {platform.machine()}",
                 "lazy-lock.json": sha256(HERE / "lazyvim" / "lazy-lock.json"),
                 "parsers.lua": sha256(HERE / "lazyvim" / "parsers.lua"),
                 "virgil.lua": sha256(REPO / "editors" / "nvim" / "lazyvim" / "virgil.lua")}
        lines.update(changes)
        return "".join(f"{key} {value}\n" for key, value in lines.items())

    def seed(self, **changes):
        """A stand-in for what --seed-cache writes: the plugins at the locked
        commits, a parser and its query link, the tree-sitter CLI, and
        mason.nvim's registry update file."""
        config = self.cache / "config" / "nvim"
        (config / "lua" / "plugins").mkdir(parents=True)
        (config / "init.lua").write_text("-- the starter\n")
        data = self.cache / "data" / "nvim"
        lock = json.loads((HERE / "lazyvim" / "lazy-lock.json").read_text())
        for name, entry in lock.items():
            (data / "lazy" / name).mkdir(parents=True)
            (data / "lazy" / name / ".head").write_text(entry["commit"] + "\n")
        (data / "lazy" / "nvim-treesitter" / "runtime" / "queries" / "lua").mkdir(parents=True)
        (data / "site" / "parser").mkdir(parents=True)
        (data / "site" / "parser" / "lua.so").write_bytes(b"")
        (data / "site" / "parser-info").mkdir()
        (data / "site" / "parser-info" / "lua.revision").write_text("abc123")
        (data / "site" / "queries").mkdir()
        (data / "site" / "queries" / "lua").symlink_to("@DATA@/nvim/lazy/nvim-treesitter/runtime/queries/lua")
        (data / "mason" / "bin").mkdir(parents=True)
        (data / "mason" / "bin" / "tree-sitter").write_text("#!/bin/sh\n")
        (data / "mason" / "bin" / "tree-sitter").chmod(0o755)
        (self.cache / "cache" / "nvim").mkdir(parents=True)
        (self.cache / "cache" / "nvim" / "mason-registry-update").write_text("c0ffee\n1")
        (self.cache / "parsers.txt").write_text(
            "tree-sitter CLI: tree-sitter 0.0-test (@DATA@/nvim/mason/bin/tree-sitter)\nparser lua abc123\n")
        (self.cache / "ready").write_text(self.identity(**changes) + "seeded 2026-11-22T00:00:00Z\n")

    def snapshot(self):
        """The cache's tree, with each file's bytes and each link's target."""
        tree = {}
        for path in sorted(self.cache.rglob("*")):
            key = str(path.relative_to(self.cache))
            if path.is_symlink():
                tree[key] = ("link", os.readlink(path))
            elif path.is_file():
                tree[key] = ("file", path.read_bytes())
            else:
                tree[key] = ("dir",)
        return tree

    def test_usage(self):
        server = str(self.server / "virgil-lsp")
        seed = ["--seed-cache", str(self.cache)]
        for args in ([*seed, server], [*seed, "--archive", server], [*seed, "--results", str(self.stream)],
                     [*seed, "--cache", str(self.cache)], [*seed, "--update-lock"],
                     ["--cache", str(self.cache), "--update-lock", server], ["--cache", "", server],
                     ["--cache"], ["--seed-cache"], ["--seed-cache", ""]):
            with self.subTest(args=args):
                result = self.run_sh(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("--seed-cache CACHE", result.stderr)
        self.assertFalse(self.cache.exists())

    def test_seed_needs_an_empty_directory(self):
        self.cache.mkdir()
        (self.cache / "other").write_text("kept\n")
        result = self.run_sh("--seed-cache", str(self.cache))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("the cache directory is not empty", result.stderr)
        self.assertEqual([path.name for path in self.cache.iterdir()], ["other"])

    def test_seed_that_stops_is_not_ready(self):
        # A seed needs no server or Virgil sources, and leaves no ready file
        # when it stops, here at the starter's clone.
        result = self.run_sh("--out", str(self.dir / "out"), "--seed-cache", str(self.cache))
        self.assertEqual(result.returncode, 128, result.stdout + result.stderr)
        self.assertIn(f"seeding the cache: {self.cache}", result.stdout)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(list(self.tmp.iterdir()), [], "run.sh left its work directory")

    def test_miss_installs_and_leaves_the_cache_alone(self):
        for exists in (False, True):
            with self.subTest(exists=exists):
                if exists:
                    self.cache.mkdir()
                    (self.cache / "data").mkdir()
                    (self.cache / "parsers.txt").write_text("partial\n")
                before = self.snapshot() if exists else None
                result = self.run_sh("--cache", str(self.cache), "--results", str(self.stream),
                                     str(self.server / "virgil-lsp"))
                # It clones the starter, as without --cache.
                self.assertEqual(result.returncode, 128, result.stdout + result.stderr)
                self.assertIn(f"cache: none ready in {self.cache}; installing from GitHub", result.stdout)
                self.assertIn("clone refused", result.stderr)
                if exists:
                    self.assertEqual(self.snapshot(), before)
                else:
                    self.assertFalse(self.cache.exists())
                run = self.validate()
                starter = next(event for event in run.checks if event["id"] == "lazyvim.infrastructure.starter")
                self.assertEqual(starter["failureCode"], "exit-status")

    def test_mismatched_cache_is_refused(self):
        for key, value in (("starter", "0" * 40), ("neovim", "NVIM v0.11.5"), ("lazy-lock.json", "0" * 64),
                           ("parsers.lua", "0" * 64), ("virgil.lua", "0" * 64), ("system", "Plan9 mips")):
            with self.subTest(key=key):
                shutil.rmtree(self.cache, True)
                self.seed(**{key: value})
                before = self.snapshot()
                # One stream is enough to show a preflight failure.
                results = ["--results", str(self.stream)] if key == "starter" else []
                result = self.run_sh("--cache", str(self.cache), *results, str(self.server / "virgil-lsp"))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("was seeded for another installation", result.stderr)
                self.assertIn(f"{key} {value}", result.stderr)
                self.assertEqual(self.snapshot(), before)
                if results:
                    self.assertEqual(self.validate().end["status"], "unavailable")
                self.assertEqual(list(self.tmp.iterdir()), [])

    def test_incomplete_ready_cache_is_refused(self):
        self.seed()
        shutil.rmtree(self.cache / "data")
        result = self.run_sh("--cache", str(self.cache), str(self.server / "virgil-lsp"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("lacks config/nvim, data/nvim, or parsers.txt", result.stderr)

    def test_missing_parser_fails_parsers_ready(self):
        self.seed()
        (self.cache / "data" / "nvim" / "site" / "parser-info" / "lua.revision").write_text("other")
        result = self.run_sh("--cache", str(self.cache), "--results", str(self.stream),
                             str(self.server / "virgil-lsp"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL: parser lua is not at revision abc123", result.stdout)
        run = self.validate()
        checks = {event["id"]: event for event in run.checks if event["profile"] == "wrapper"}
        self.assertEqual(checks["lazyvim.infrastructure.pins-before"]["status"], "pass")
        self.assertEqual(checks["lazyvim.infrastructure.parsers-ready"]["status"], "fail")

    def test_hit_restores_and_checks(self):
        self.seed()
        before = self.snapshot()
        out = self.dir / "out"
        result = self.run_sh("--keep-work", "--out", str(out), "--cache", str(self.cache), "--results",
                             str(self.stream), str(self.server / "virgil-lsp"))
        # The stand-in tmux starts no session, so it stops at main.
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before, "run.sh changed the cache")
        self.assertIn(f"cache: {self.cache}, seeded 2026-11-22T00:00:00Z", result.stdout)
        run = self.validate()
        checks = {event["id"]: event for event in run.checks if event["profile"] == "wrapper"}
        for check_id in WRAPPER_BEFORE:
            self.assertEqual(checks[check_id]["status"], "pass", check_id)
        self.assertIn("restored from the cache", checks["lazyvim.infrastructure.starter"]["observation"])
        self.assertIn("restored from the cache", checks["lazyvim.infrastructure.plugins-install"]["observation"])
        # The plugins' commits and the parsers, in the provenance as without a cache.
        provenance = (out / "provenance.txt").read_text()
        self.assertIn("seeded 2026-11-22T00:00:00Z; the starter, plugins, and parsers restored from it", provenance)
        self.assertIn("plugin LazyVim ", provenance)
        self.assertIn("parser lua abc123", provenance)
        work = Path(next(line for line in result.stdout.splitlines()
                         if line.startswith("work directory kept: "))[len("work directory kept: "):])
        self.addCleanup(shutil.rmtree, work, True)
        data = work / "data" / "nvim"
        # The query link points into this run's data directory, the CLI's path
        # in the provenance too, and mason.nvim's registry is as fresh as a
        # cold install leaves it.
        self.assertEqual(os.readlink(data / "site" / "queries" / "lua"),
                         f"{data.parent}/nvim/lazy/nvim-treesitter/runtime/queries/lua")
        self.assertTrue((data / "site" / "queries" / "lua").is_dir())
        self.assertIn(f"({data}/mason/bin/tree-sitter)", provenance)
        checksum, stamp = (work / "cache" / "nvim" / "mason-registry-update").read_text().split("\n")
        self.assertEqual(checksum, "c0ffee")
        self.assertLess(abs(int(stamp) - time.time()), 600)
        # The checkout's plugin spec and lock, not the cache's.
        config = work / "config" / "nvim"
        self.assertEqual((config / "lua" / "plugins" / "virgil.lua").read_bytes(),
                         (REPO / "editors" / "nvim" / "lazyvim" / "virgil.lua").read_bytes())
        self.assertEqual((config / "lazy-lock.json").read_bytes(), (HERE / "lazyvim" / "lazy-lock.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
