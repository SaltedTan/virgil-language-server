#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The result stream of run.sh --results: one nvim run's JSON Lines events, by
the contract in test/e2e/RESULTS.md. run.sh, the outer launcher, calls it, and
it alone writes the stream; check.lua's observations reach it through a file.

  start   writes the run-start, with the plan from coverage.json and the run's
          provenance, then nvim.infrastructure.preflight. After a failed
          preflight, it also skips every other planned check.
  run     runs headless Neovim with its deadline, as run.sh does without
          --results, and writes a check event for each observation that
          check.lua reports, as it arrives, then nvim.infrastructure.completed.
  end     removes the temporary directory, verifies the cleanup, and writes the
          run-end. Its exit status is run.sh's: 0 exactly when the run passed.

The stream is the state between the steps: each step reads the run-start and
counts the events written so far. A launcher that is interrupted or killed
leaves the events it wrote, without a run-end, so the stream is incomplete.
Requires Python 3.9 or later and only the standard library."""
import argparse
import datetime
import hashlib
import json
import math
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
MANIFEST = HERE.parent / "coverage.json"
CONFIGURATION = REPO / "editors" / "nvim" / "virgil_lsp.lua"
SUITE = "nvim"
PREFLIGHT = "nvim.infrastructure.preflight"
COMPLETED = "nvim.infrastructure.completed"
PURPOSES = ("release", "development", "compatibility", "canary", "demo")
# The launcher's own state, in the temporary directory: what run found, which
# end reads before it removes the directory.
STATE = "launcher.json"
OBSERVATIONS = "observations.jsonl"
# The signals that interrupt a run.
STOP_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
# How long processes that Neovim started may outlive it.
GRACE = 5.0
# The longest reason or observation that the stream keeps.
LIMIT = 4000


def now():
    return time.time()


def utc(seconds):
    moment = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(root):
    """RESULTS.md's tree digest: the SHA-256 of LF-terminated "<sha256>  <path>"
    lines, sorted by path relative to root, with "link <target>" for a symbolic
    link, as the Remote-SSH test computes it."""
    lines = []
    for directory, names, files in os.walk(root):
        for name in names + files:
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if os.path.islink(path):
                lines.append((relative, f"link {os.readlink(path)}  {relative}\n"))
            elif os.path.isfile(path):
                lines.append((relative, f"{sha256(path)}  {relative}\n"))
    return hashlib.sha256("".join(line for _, line in sorted(lines)).encode()).hexdigest()


def unavailable(reason):
    return {"value": None, "unavailable": reason}


def is_unavailable(value):
    return isinstance(value, dict) and "unavailable" in value


class Paths:
    """Replaces the machine's absolute paths in text from Neovim, longest
    first, so that the stream names files the way the fixture and the
    repository do."""

    def __init__(self, work=None):
        pairs = [(str(REPO), "<repo>"), (os.path.expanduser("~"), "~")]
        if work:
            pairs.append((os.path.realpath(work), "<work>"))
            pairs.append((str(work), "<work>"))
        self.pairs = sorted((pair for pair in pairs if len(pair[0]) > 1), key=lambda pair: -len(pair[0]))

    def __call__(self, text):
        # Lua's error positions shorten long paths to "...<end of the path>".
        text = re.sub(r"\S*check\.lua:(\d+):", r"test/e2e/nvim/check.lua:\1:", text)
        for path, name in self.pairs:
            text = text.replace(path, name)
        if len(text) > LIMIT:
            text = text[:LIMIT] + f" [{len(text) - LIMIT} more characters]"
        return text


def plan():
    """The planned observations of an nvim run, from the manifest: each check
    of the suite in each of its profiles, once."""
    manifest = json.loads(MANIFEST.read_bytes())
    suite = next(suite for suite in manifest["suites"] if suite["id"] == SUITE)
    profiles = {profile["id"]: profile for profile in suite["profiles"]}
    keys, levels = [], {}
    for check in manifest["checks"]:
        if SUITE not in check["suites"]:
            continue
        if check["when"] is not None:
            # A condition needs a fact that this writer doesn't observe.
            raise SystemExit(f"results.py: {check['id']} has the condition {check['when']}, "
                             "which the nvim writer doesn't decide")
        levels[check["id"]] = check["level"]
        for profile in check["profiles"]:
            if profiles[profile]["repeated"]:
                raise SystemExit(f"results.py: profile {profile} is repeated, which the nvim writer doesn't run")
            keys.append((check["id"], profile, 1))
    return sorted(keys), levels


def run_text(command, timeout=10):
    """A command's standard output, or None if it couldn't run or failed."""
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


def target(path):
    """The server's build target, from its executable's header."""
    with open(path, "rb") as file:
        header = file.read(20)
    if header[:4] == b"\x7fELF" and len(header) >= 20 and header[4] == 2 \
            and int.from_bytes(header[18:20], "little") == 0x3E:
        return "x86-64-linux"
    if header[:4] == b"\xcf\xfa\xed\xfe" and int.from_bytes(header[4:8], "little") == 0x01000007:
        return "x86-64-darwin"
    return unavailable("the server is neither an x86-64 ELF nor an x86-64 Mach-O executable")


def server_provenance(server):
    """The server that run.sh puts on PATH, virgil-lsp in the directory of its
    server argument, and the worker that it starts: virgil-lsp-worker beside
    its executable's physical path, with symlinks resolved."""
    lsp = os.path.join(os.path.dirname(server) or ".", "virgil-lsp")
    worker = os.path.join(os.path.dirname(os.path.realpath(lsp)), "virgil-lsp-worker")
    result = {"versionText": None, "version": None, "sourceRevision": None, "virgilRevision": None,
              "target": None, "sha256": None, "workerSha256": None}
    if not (os.path.isfile(lsp) and os.access(lsp, os.X_OK)):
        missing = unavailable("virgil-lsp is missing, or not executable, in the server's directory")
        result.update(versionText=missing, target=missing, sha256=missing)
    else:
        result["sha256"] = sha256(lsp)
        result["target"] = target(lsp)
        text = run_text([lsp, "--version"])
        if text is None or not text.strip():
            result["versionText"] = unavailable("virgil-lsp --version failed")
        else:
            text = text.rstrip("\n")
            result["versionText"] = text
            first = re.match(r"virgil-lsp (\S+)$", text.split("\n")[0])
            result["version"] = first.group(1) if first else None
            for field, label in (("sourceRevision", "source revision"), ("virgilRevision", "virgil revision")):
                match = re.search(rf"^\s*{label}: (\S+)\s*$", text, re.M)
                result[field] = match.group(1) if match else None
    if os.path.isfile(worker) and os.access(worker, os.X_OK):
        result["workerSha256"] = sha256(worker)
    else:
        result["workerSha256"] = unavailable("virgil-lsp-worker is missing, or not executable, beside the server")
    return result


def harness():
    """The checkout's HEAD, its tree, and whether tracked files changed. Changes
    inside a submodule's checkout don't count: the first build replaces
    vendor/virgil's bin/v3c. A different submodule commit does."""
    revision = run_text(["git", "-C", str(REPO), "rev-parse", "--verify", "HEAD"])
    tree = run_text(["git", "-C", str(REPO), "rev-parse", "--verify", "HEAD^{tree}"])
    status = run_text(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no",
                       "--ignore-submodules=dirty"])

    def commit(text):
        text = (text or "").strip()
        return text if re.fullmatch("[0-9a-f]{40}", text) else None

    return {"revision": commit(revision), "tree": commit(tree), "dirty": None if status is None else bool(status.strip())}


def machine(server_target):
    system = platform.system()
    if system == "Linux":
        try:
            kernel = Path("/proc/sys/kernel/osrelease").read_text()
        except OSError:
            kernel = ""
        os_name = "wsl" if "microsoft" in kernel.lower() else "linux"
        release = platform.release()
        cpu = None
        try:
            match = re.search(r"^model name\s*:\s*(.*)$", Path("/proc/cpuinfo").read_text(), re.M)
            cpu = (match.group(1).strip() or None) if match else None
        except OSError:
            pass
        translation = None
    elif system == "Darwin":
        os_name = "macos"
        release = platform.mac_ver()[0] or platform.release()
        cpu = (run_text(["sysctl", "-n", "machdep.cpu.brand_string"]) or "").strip() or None
        arm64 = (run_text(["sysctl", "-n", "hw.optional.arm64"]) or "").strip() == "1"
        translation = "rosetta-2" if arm64 and server_target == "x86-64-darwin" else None
    else:
        raise SystemExit(f"results.py: no nvim setup runs on {system}")
    return {"os": os_name, "release": release or "unknown", "arch": platform.machine() or "unknown",
            "cpu": cpu, "translation": translation}


def ci():
    """GitHub Actions' run, or None outside it."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    server, repository, run = (os.environ.get(name) for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY",
                                                                  "GITHUB_RUN_ID"))
    if not (server and repository and run):
        return None
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    image = " ".join(os.environ[name] for name in ("ImageOS", "ImageVersion") if os.environ.get(name))
    return {"runUrl": f"{server}/{repository}/actions/runs/{run}", "jobUrl": None,
            "attempt": int(attempt) if attempt.isdigit() and int(attempt) > 0 else 1,
            "runnerImage": image or None}


def lane():
    job = os.environ.get("GITHUB_JOB")
    return job if os.environ.get("GITHUB_ACTIONS") == "true" and job else "local"


def command_of(argv):
    """run.sh's command, with the repository's paths relative to its root and
    other paths as placeholders, so that no machine's path is published."""
    def relative(path, placeholder):
        resolved = os.path.abspath(path)
        try:
            inside = os.path.relpath(resolved, REPO)
        except ValueError:
            return placeholder
        return placeholder if inside == ".." or inside.startswith(".." + os.sep) else inside.replace(os.sep, "/")

    words = [relative(argv[0], "<run.sh>")]
    rest = iter(argv[1:])
    for word in rest:
        if word in ("--release", "--purpose"):
            words += [word, next(rest, "<missing>")]
        elif word == "--results":
            next(rest, None)
            words += [word, "<results>"]
        else:
            words.append(relative(word, "<server>"))
    return words


class Stream:
    """The stream file: its run-start and the number of events in it."""

    def __init__(self, path):
        self.path = path
        try:
            lines = Path(path).read_bytes().split(b"\n")
        except FileNotFoundError:
            lines = [b""]
        self.events = [json.loads(line) for line in lines if line.strip()]
        self.start = self.events[0] if self.events and self.events[0].get("event") == "run-start" else None

    def write(self, event):
        kind = event.pop("event")
        run_id = event.pop("runId") if kind == "run-start" else self.start["runId"]
        event = {"schema": 1, "event": kind, "runId": run_id, "seq": len(self.events), **event}
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        with open(self.path, "a", encoding="utf-8", newline="\n") as file:
            file.write(line)
            file.flush()
        self.events.append(event)
        if kind == "run-start":
            self.start = event

    def check(self, check_id, profile, status, level, started, ended, observation, reason=None, failure_code=None):
        self.write({
            "event": "check", "time": utc(ended), "id": check_id, "profile": profile, "iteration": 1,
            "status": status, "level": level, "startedAt": utc(started),
            "durationMs": max(0, round((ended - started) * 1000)), "reason": reason, "expectation": None,
            "failureCode": failure_code, "observation": observation, "artifacts": [],
        })

    def reported(self):
        return {(event["id"], event["profile"], event["iteration"]): event
                for event in self.events if event["event"] == "check"}


def write_start(args, stream, fixture, preflight_failure=None, failure_code=None):
    """Writes the run-start and the preflight's check, and after a failed
    preflight, a skip for every other planned check."""
    keys, levels = plan()
    launched = args.launched_at if args.launched_at is not None else now()
    server = server_provenance(args.server)
    target_value = server["target"]
    host = machine(None if is_unavailable(target_value) else target_value)
    setup = {"linux": "nvim-linux", "macos": "nvim-macos", "wsl": "nvim-wsl"}[host["os"]]
    version = re.match(r"NVIM v(\S+)", args.nvim_version or "")
    runtimes = {"python3": platform.python_version()}
    for runtime in args.runtime:
        name, _, value = runtime.partition("=")
        if value:
            runtimes[name] = value
    configuration = {"main": sha256(CONFIGURATION)} if CONFIGURATION.is_file() else {}
    root = REPO / "vendor" / "virgil" / "lib"
    started = now()
    stream.write({
        "event": "run-start",
        "runId": f"nvim-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(started))}-{secrets.token_hex(4)}",
        "time": utc(started), "release": args.release, "purpose": args.purpose, "suite": SUITE, "setup": setup,
        "lane": lane(), "command": command_of(args.command), "coverageSha256": sha256(MANIFEST),
        "expected": [{"id": check_id, "profile": profile, "iteration": iteration}
                     for check_id, profile, iteration in keys],
        "facts": {},
        "provenance": {
            "server": server, "archive": None, "vsix": None, "clientMode": "none", "harness": harness(),
            "editor": {"name": "nvim", "version": version.group(1) if version else None, "commit": None, "arch": None},
            "client": host, "host": host, "transport": {"kind": "local", "topology": "local", "extensionHost": None},
            "pins": [], "configurationSha256": configuration,
            "fixtureSha256": tree_sha256(fixture) if fixture else unavailable(
                "the preflight failed before the fixture was copied" if preflight_failure
                else "the launcher stopped before it copied the fixture"),
            "virgilRootSha256": tree_sha256(root) if root.is_dir() else None,
            "runtimes": runtimes, "ci": ci(),
        },
        "attestation": None,
    })
    ended = now()
    if preflight_failure is None:
        stream.check(PREFLIGHT, "wrapper", "pass", levels[PREFLIGHT], launched, ended,
                     f"{args.nvim_version}; virgil-lsp and virgil-lsp-worker are executable")
        return
    stream.check(PREFLIGHT, "wrapper", "fail", levels[PREFLIGHT], launched, ended, preflight_failure,
                 preflight_failure, failure_code)
    for check_id, profile, _ in keys:
        if check_id != PREFLIGHT:
            stream.check(check_id, profile, "skip", levels[check_id], ended, ended, "not run",
                         f"not run: {PREFLIGHT} failed")


def start(args):
    stream = Stream(args.stream)
    write_start(args, stream, args.fixture, args.preflight_failure, args.failure_code)
    return 0


def save_state(work, state):
    Path(work, STATE).write_text(json.dumps(state) + "\n")


def run(args):
    """Runs Neovim as run.sh's deadline wrapper does, writing each observation
    that check.lua reports as a check event as soon as it arrives."""
    stream = Stream(args.stream)
    keys, levels = plan()
    main = {check_id for check_id, profile, _ in keys if profile == "main"}
    paths = Paths(args.work)
    observations = Path(args.work, OBSERVATIONS)
    observations.write_bytes(b"")
    state = {"issues": [], "processes": "unknown"}
    save_state(args.work, state)
    reported, failed = set(), []

    def take(line):
        try:
            entry = json.loads(line)
            check_id, status = entry["id"], entry["status"]
            started, ended = entry["startedAt"], entry["endedAt"]
            upper = (now() + 1) * 1000
            if not (isinstance(check_id, str) and check_id in main
                    and status in ("pass", "fail")
                    and all(type(value) in (int, float) and math.isfinite(value)
                            and (launched - 1) * 1000 <= value <= upper for value in (started, ended))
                    and started <= ended
                    and isinstance(entry["observation"], str) and entry["observation"]):
                raise ValueError("invalid observation fields")
            if status == "fail" and not (isinstance(entry["reason"], str) and entry["reason"]
                                         and entry["failureCode"] in ("wait-timeout", "error")):
                raise ValueError("invalid failure fields")
        except (ValueError, KeyError, TypeError, OverflowError):
            state["issues"].append("check.lua wrote a malformed observation: " + paths(line[:200]))
            return
        if check_id in reported:
            state["issues"].append(f"check.lua reported {check_id} again")
            return
        observation = paths(entry["observation"])
        reported.add(check_id)
        if status == "pass":
            stream.check(check_id, "main", "pass", levels[check_id], started / 1000, ended / 1000, observation)
        else:
            stream.check(check_id, "main", "fail", levels[check_id], started / 1000, ended / 1000,
                         observation, paths(entry["reason"]), entry["failureCode"])
            failed.append(check_id)

    env = dict(os.environ, VIRGIL_NVIM_OBSERVATIONS=str(observations))
    launched = now()
    process = None
    groups = set()
    protected = {os.getpgrp()}
    interrupted = None
    timed_out = False
    reader_failed = False
    buffered = b""

    def collect():
        listing = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid="], check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
        table = {pid: (parent, group) for pid, parent, group in
                 (map(int, row.split()) for row in listing.stdout.splitlines())}
        parent = os.getpid()
        while parent and parent in table:
            parent, group = table[parent]
            protected.add(group)
        if process is not None:
            descendants = {process.pid}
            while True:
                children = {pid for pid, (parent, _) in table.items() if parent in descendants}
                if children <= descendants:
                    break
                descendants.update(children)
            groups.update(table[pid][1] for pid in descendants if pid in table)
        groups.difference_update(protected)

    def alive():
        remaining = set()
        for group in groups:
            try:
                os.killpg(group, 0)
                remaining.add(group)
            except ProcessLookupError:
                pass
        return remaining

    def stop(signum, frame):
        nonlocal interrupted
        interrupted = signum

    previous = {signum: signal.signal(signum, stop) for signum in STOP_SIGNALS}
    try:
        collect()
        if interrupted is None:
            process = subprocess.Popen(args.nvim, start_new_session=True, env=env)
            groups.add(process.pid)
            with open(observations, "rb") as source:
                def read():
                    nonlocal buffered
                    buffered += source.read()
                    *lines, buffered = buffered.split(b"\n")
                    for line in lines:
                        if line.strip():
                            collect()
                            take(line.decode("utf-8", "replace"))

                limit = time.monotonic() + args.deadline
                sampled = time.monotonic()
                while process.poll() is None and interrupted is None:
                    read()
                    if time.monotonic() >= sampled:
                        collect()
                        sampled = time.monotonic() + 1
                    if time.monotonic() >= limit:
                        print(f"FAIL: Neovim smoke test exceeded {args.deadline} seconds", file=sys.stderr)
                        timed_out = True
                        break
                    time.sleep(0.05)
                if interrupted is None:
                    read()
            if buffered.strip():
                state["issues"].append("check.lua's last observation is incomplete")
    except Exception as error:
        reader_failed = True
        state["issues"].append("the Neovim observation reader failed: " + paths(str(error)))
    finally:
        ended = now()
        try:
            collect()
        except Exception as error:
            state["processes"] = "failed"
            state["issues"].append("could not record Neovim's process groups: " + paths(str(error)))
        try:
            if process is not None:
                if not (timed_out or interrupted or reader_failed):
                    left = time.monotonic() + GRACE
                    while alive() and time.monotonic() < left and interrupted is None:
                        time.sleep(0.05)
                    if alive() and interrupted is None:
                        state["issues"].append(f"processes that Neovim started were still running {GRACE:g} "
                                               "seconds after it exited, and the launcher killed them")
                collect()
        except Exception as error:
            state["processes"] = "failed"
            state["issues"].append("could not inspect Neovim's remaining processes: " + paths(str(error)))
        try:
            for group in groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None:
                process.wait()
            left = time.monotonic() + GRACE
            while alive() and time.monotonic() < left:
                time.sleep(0.05)
            if alive():
                state["processes"] = "failed"
                state["issues"].append("processes that Neovim started remain after SIGKILL")
            elif state["processes"] != "failed":
                state["processes"] = "verified"
        except Exception as error:
            state["processes"] = "failed"
            state["issues"].append("could not stop and verify Neovim's processes: " + paths(str(error)))
        save_state(args.work, state)
        if interrupted is not None:
            signal.signal(interrupted, signal.SIG_DFL)
            os.kill(os.getpid(), interrupted)
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    # check.lua stops at its first failure, so the checks after it didn't run.
    if failed:
        for check_id, profile, _ in keys:
            if profile == "main" and check_id not in reported:
                stream.check(check_id, profile, "skip", levels[check_id], ended, ended, "not run",
                             f"not run: check.lua stopped after {failed[0]} failed")

    code = process.returncode if process is not None else 1
    seconds = ended - launched
    if timed_out:
        state["issues"].append(f"Neovim exceeded its {args.deadline}-second deadline")
        stream.check(COMPLETED, "wrapper", "fail", levels[COMPLETED], launched, ended,
                     f"Neovim was still running at its {args.deadline}-second deadline, and was killed",
                     f"Neovim exceeded its {args.deadline}-second deadline", "deadline")
    elif code < 0:
        name = signal.Signals(-code).name
        stream.check(COMPLETED, "wrapper", "fail", levels[COMPLETED], launched, ended,
                     f"Neovim was killed by {name} after {seconds:.1f} seconds", f"Neovim was killed by {name}",
                     "signal")
    elif code != 0:
        stream.check(COMPLETED, "wrapper", "fail", levels[COMPLETED], launched, ended,
                     f"Neovim exited with status {code} after {seconds:.1f} seconds",
                     f"Neovim exited with status {code}", "exit-status")
    else:
        stream.check(COMPLETED, "wrapper", "pass", levels[COMPLETED], launched, ended,
                     f"Neovim exited with status 0 after {seconds:.1f} seconds, within its "
                     f"{args.deadline}-second deadline")
    save_state(args.work, state)
    return 1 if timed_out or reader_failed else code


def end(args):
    """Tears down, then writes the run-end, unless the launcher was
    interrupted. Returns run.sh's exit status."""
    code = args.exit_code
    stream = Stream(args.stream)
    state = None
    work_left = False
    if args.work:
        try:
            state = json.loads(Path(args.work, STATE).read_text())
        except (OSError, ValueError):
            state = None
        shutil.rmtree(args.work, ignore_errors=True)
        work_left = os.path.lexists(args.work)
    if args.interrupted:
        print(f"results.py: the run was interrupted, so {args.stream} has no run-end", file=sys.stderr)
        return code or 1

    issues = []
    if stream.start is None:
        # run.sh stopped before it wrote the run-start, such as when copying
        # the fixture failed. The preflight's own failures write it.
        write_start(args, stream, None, None if args.preflight_passed else
                    f"the launcher stopped during its preflight, with exit status {code}",
                    None if args.preflight_passed else "launcher-error")
    keys = [(key["id"], key["profile"], key["iteration"]) for key in stream.start["expected"]]
    reported = stream.reported()
    preflight = reported.get((PREFLIGHT, "wrapper", 1))
    if state is not None:
        issues += state["issues"]
    if (COMPLETED, "wrapper", 1) not in reported and preflight and preflight["status"] == "pass":
        issues.append(f"the launcher stopped, with exit status {code}, before Neovim completed")
    missing = [key for key in keys if key not in reported]
    if missing:
        more = f" and {len(missing) - 1} more" if len(missing) > 1 else ""
        issues.append(f"{missing[0][0]} ({missing[0][1]}){more} have no check event")
    if work_left:
        issues.append("the temporary directory remains after its removal")
    # Without the state, run never started Neovim.
    processes = state["processes"] if state is not None else "verified"
    cleanup = "failed" if work_left or processes == "failed" else "unknown" if processes == "unknown" else "verified"

    counts = dict.fromkeys(("pass", "fail", "xfail", "xpass", "skip"), 0)
    for event in reported.values():
        counts[event["status"]] += 1
    provenance = stream.start["provenance"]
    unavailable_values = [value for value in [*provenance["server"].values(), provenance["fixtureSha256"]]
                          if is_unavailable(value)]
    passed = (not missing and counts["pass"] == len(keys) and code == 0 and cleanup == "verified"
              and not issues and not unavailable_values)
    status = "pass" if passed else "unavailable" if unavailable_values or (
        preflight and preflight["status"] == "fail") else "fail"
    final = 0 if passed else code or 1
    began = datetime.datetime.strptime(stream.start["time"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=datetime.timezone.utc).timestamp()
    if args.launched_at is not None:
        began = min(began, args.launched_at)
    finished = now()
    stream.write({
        "event": "run-end", "time": utc(finished), "complete": not missing, "status": status,
        "durationMs": max(0, round((finished - began) * 1000)), "expectedCount": len(keys),
        "checkCount": len(reported), "counts": counts, "exitCode": final, "signal": None, "cleanup": cleanup,
        "issues": issues,
    })
    outcome = {"pass": "passed", "fail": "failed", "unavailable": "unavailable"}[status]
    print(f"\nResults: {args.stream}: run {outcome} ({len(reported)} of {len(keys)} observations): "
          + ", ".join(f"{count} {name}" for name, count in counts.items()))
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    steps = parser.add_subparsers(dest="step", required=True)
    for name in ("start", "end"):
        step = steps.add_parser(name)
        step.add_argument("--stream", required=True)
        step.add_argument("--server", required=True)
        step.add_argument("--release")
        step.add_argument("--purpose", choices=PURPOSES, default="development")
        step.add_argument("--nvim-version", default="")
        step.add_argument("--launched-at", type=float)
        step.add_argument("--runtime", action="append", default=[])
        step.add_argument("command", nargs="+")
        if name == "start":
            step.add_argument("--fixture")
            step.add_argument("--preflight-failure")
            step.add_argument("--failure-code")
        else:
            step.add_argument("--work")
            step.add_argument("--exit-code", type=int, required=True)
            step.add_argument("--preflight-passed", action="store_true")
            step.add_argument("--interrupted", action="store_true")
    step = steps.add_parser("run")
    step.add_argument("--stream", required=True)
    step.add_argument("--work", required=True)
    step.add_argument("--deadline", type=int, required=True)
    step.add_argument("nvim", nargs="+")
    args = parser.parse_args(argv)
    return {"start": start, "run": run, "end": end}[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
