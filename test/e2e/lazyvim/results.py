#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The result stream of run.sh --results: one lazyvim run's JSON Lines events,
by the contract in test/e2e/RESULTS.md. run.sh, the outer launcher, calls it,
and it alone writes the stream. run.sh's own checks and driver.lua's
observations reach it through an observations file, one JSON object a line,
which run.sh and driver.lua append to as the run goes:

  check    run.sh records one of its own checks, which ends now.
  server   run.sh records the installed server's provenance, and the archive's.
  digest   run.sh records a tree digest: a profile's configuration, or the
           fixture as copied.
  end      writes the whole stream from the observations, then removes the
           observations file. Its exit status is run.sh's: 0 exactly when the
           run passed.

The stream is written only at the end, because its run-start plans one side
of each alternative, which facts that driver.lua observes during the main
profile decide. The LazyVim writer section of test/e2e/RESULTS.md describes
the rules. Requires Python 3.9 or later and only the standard library."""
import argparse
import datetime
import importlib.util
import json
import math
import os
import platform
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
MANIFEST = HERE.parent / "coverage.json"
LOCK = HERE / "lazy-lock.json"
SUITE = "lazyvim"
PREFLIGHT = "lazyvim.infrastructure.preflight"
CLEANUP = "lazyvim.infrastructure.cleanup"
COMPLETE = "lazyvim.infrastructure.profile-complete"
STARTER = "LazyVim/starter"
# The longest reason or observation that the stream keeps.
LIMIT = 4000
NAME = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")


def load_nvim_writer():
    """The headless Neovim writer, whose provenance helpers this one shares."""
    spec = importlib.util.spec_from_file_location("nvim_results", HERE.parent / "nvim" / "results.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nvim = load_nvim_writer()
utc, sha256, tree_sha256, unavailable, is_unavailable = (
    nvim.utc, nvim.sha256, nvim.tree_sha256, nvim.unavailable, nvim.is_unavailable)


def now():
    return time.time()


def append(path, entry):
    """Appends one observation, after an LF if a killed writer left a partial
    line, so that a line of its own stays whole."""
    with open(path, "ab") as file:
        if file.tell() > 0:
            with open(path, "rb") as source:
                source.seek(-1, os.SEEK_END)
                if source.read(1) != b"\n":
                    file.write(b"\n")
        file.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n")


class Paths:
    """Replaces the machine's absolute paths in text from the run, longest
    first: the checkout as <repo>, the work directory as <work>, the output
    directory as <out>, and the home directory as ~."""

    def __init__(self, work=None, out=None):
        pairs = [(str(REPO), "<repo>"), (os.path.expanduser("~"), "~")]
        for path, name in ((work, "<work>"), (out, "<out>")):
            if path:
                pairs += [(os.path.realpath(path), name), (str(path), name)]
        forms = []
        for path, name in pairs:
            if len(path) > 1:
                quoted = quote(path, safe="/")
                forms.extend((form, name) for form in {path, quoted, Path(path).as_uri(), "file://" + quoted})
        self.pairs = sorted(forms, key=lambda pair: -len(pair[0]))

    def __call__(self, text):
        for path, name in self.pairs:
            text = text.replace(path, name)
        if len(text) > LIMIT:
            text = text[:LIMIT] + f" [{len(text) - LIMIT} more characters]"
        return text


class Manifest:
    def __init__(self):
        self.document = json.loads(MANIFEST.read_bytes())
        suite = next(suite for suite in self.document["suites"] if suite["id"] == SUITE)
        self.profiles = {profile["id"]: profile for profile in suite["profiles"]}
        self.checks = {check["id"]: check for check in self.document["checks"] if SUITE in check["suites"]}
        self.conditions = {condition["id"]: condition for condition in self.document["conditions"]}
        self.xfails = [rule for rule in self.document["xfails"] if rule["check"] in self.checks]
        for profile in self.profiles.values():
            if profile["repeated"]:
                raise SystemExit(f"results.py: profile {profile['id']} is repeated, which the lazyvim writer "
                                 "doesn't run")
        for check in self.checks.values():
            condition = self.conditions.get(check["when"]) if check["when"] else None
            if check["when"] and not (condition and condition["alternative"] and condition.get("decidedBy")):
                raise SystemExit(f"results.py: {check['id']} has the condition {check['when']}, which the lazyvim "
                                 "writer doesn't decide: it decides only alternatives, by their decidedBy facts")

    def facts(self):
        """Each fact that decides a condition of the suite's checks."""
        return sorted({self.conditions[check["when"]]["decidedBy"]["fact"]
                       for check in self.checks.values() if check["when"]})

    def holds(self, condition_id, facts):
        """Whether the condition holds, by its fact. An unavailable fact
        selects the side that the value false selects."""
        decided = self.conditions[condition_id]["decidedBy"]
        value = facts[decided["fact"]]
        if is_unavailable(value):
            value = False
        return any(value is option for option in decided["values"])

    def plan(self, facts):
        """Each check of the suite in each of its profiles, once, if its
        condition holds."""
        keys = [(check["id"], profile, 1) for check in self.checks.values()
                if check["when"] is None or self.holds(check["when"], facts)
                for profile in check["profiles"]]
        return sorted(keys)


def lock_pins(starter_commit):
    """The starter's pin and each plugin's in lazy-lock.json, which
    lazyvim.infrastructure.pins-before and pins-after check were installed."""
    pins = [{"name": STARTER, "version": None, "commit": starter_commit if starter_commit and re.fullmatch(
        "[0-9a-f]{40}", starter_commit) else None, "sha256": None}]
    try:
        lock = json.loads(LOCK.read_bytes())
    except (OSError, ValueError):
        return pins
    for name, entry in sorted(lock.items()):
        commit = entry.get("commit") if isinstance(entry, dict) else None
        pins.append({"name": name, "version": None,
                     "commit": commit if isinstance(commit, str) and re.fullmatch("[0-9a-f]{40}", commit) else None,
                     "sha256": None})
    return pins


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
        elif word in ("--archive", "--out"):
            words += [word, relative(next(rest, "<missing>"), "<archive>" if word == "--archive" else "<dir>")]
        elif word.startswith("--"):
            words.append(word)
        else:
            words.append(relative(word, "<server>"))
    return words


def seconds(text):
    """A time from bash's EPOCHREALTIME, whose decimal point follows the
    locale, or from date +%s."""
    return float(text.replace(",", "."))


def record_check(args):
    entry = {"kind": "check", "by": "launcher", "id": args.id, "profile": args.profile, "status": args.status,
             "startedAt": args.started * 1000, "endedAt": now() * 1000, "observation": args.observation,
             "reason": args.reason, "failureCode": args.failure_code, "stopped": args.stopped}
    append(args.observations, entry)
    return 0


def record_server(args):
    """The server that run.sh put on PATH, and the archive it came from."""
    archive = None
    if args.archive:
        manifest = Path(args.archive_root or "", "manifest.json")
        archive = {"name": os.path.basename(args.archive), "sha256": sha256(args.archive),
                   "manifestSha256": sha256(manifest) if args.archive_root and manifest.is_file() else None}
    append(args.observations, {"kind": "server", "server": nvim.server_provenance(args.server), "archive": archive})
    return 0


def record_digest(args):
    append(args.observations, {"kind": "digest", "name": args.name, "sha256": tree_sha256(args.tree)})
    return 0


class Observations:
    """What the observations file says, checked as it is read. Each problem
    is an issue of the run."""

    def __init__(self, path, manifest, paths, launched):
        self.manifest = manifest
        self.paths = paths
        self.issues = []
        self.checks = []           # in the order observed
        self.keys = set()
        self.facts = {}
        self.server = None
        self.archive = None
        self.digests = {}
        self.errors = {}           # profile -> driver.lua's first error outside a check
        self.done = set()          # profiles whose driver.lua reported its DONE line
        try:
            data = Path(path).read_bytes()
        except OSError:
            data = b""
        lines = data.split(b"\n")
        if lines[-1].strip():
            self.issues.append("the last observation is incomplete: " + paths(lines[-1][:200].decode("utf-8", "replace")))
        for line in lines[:-1]:
            if line.strip():
                self.take(line.decode("utf-8", "replace"), launched)

    def malformed(self, line):
        self.issues.append("a malformed observation: " + self.paths(line[:200]))

    def take(self, line, launched):
        try:
            entry = json.loads(line)
            kind = entry["kind"]
            if kind == "check":
                self.take_check(entry, launched)
            elif kind == "fact":
                name, value = entry["fact"], entry["value"]
                if name not in self.manifest.facts() or not (value is None or isinstance(value, bool)):
                    raise ValueError("invalid fact")
                if name in self.facts:
                    self.issues.append(f"driver.lua reported the fact {name} again")
                else:
                    self.facts[name] = value
            elif kind == "error":
                profile, message = entry["profile"], entry["message"]
                if profile not in self.manifest.profiles or not isinstance(message, str) or not message:
                    raise ValueError("invalid error")
                self.errors.setdefault(profile, self.paths(message))
            elif kind == "done":
                if entry["profile"] not in self.manifest.profiles:
                    raise ValueError("invalid profile")
                self.done.add(entry["profile"])
            elif kind == "server":
                self.server, self.archive = entry["server"], entry["archive"]
            elif kind == "digest":
                name, digest = entry["name"], entry["sha256"]
                if not (isinstance(name, str) and isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest)):
                    raise ValueError("invalid digest")
                self.digests[name] = digest
            else:
                raise ValueError("unknown kind")
        except (ValueError, KeyError, TypeError, OverflowError):
            self.malformed(line)

    def take_check(self, entry, launched):
        check_id, profile, status, by = entry["id"], entry["profile"], entry["status"], entry["by"]
        started, ended = entry["startedAt"], entry["endedAt"]
        check = self.manifest.checks.get(check_id) if isinstance(check_id, str) else None
        source = {"driver": "test/e2e/lazyvim/driver.lua", "launcher": "test/e2e/lazyvim/run.sh"}.get(by)
        upper = (now() + 1) * 1000
        if not (check and source and check["source"] == source and profile in check["profiles"]
                and status in ("pass", "fail")
                and all(type(value) in (int, float) and math.isfinite(value)
                        and (launched - 1) * 1000 <= value <= upper for value in (started, ended))
                and started <= ended
                and isinstance(entry["observation"], str) and entry["observation"]):
            raise ValueError("invalid observation fields")
        if status == "fail" and not (isinstance(entry["reason"], str) and entry["reason"]
                                     and isinstance(entry["failureCode"], str)
                                     and NAME.fullmatch(entry["failureCode"])):
            raise ValueError("invalid failure fields")
        if entry.get("stopped", False) not in (True, False) or entry.get("stopped") and (
                by != "launcher" or status != "fail"):
            raise ValueError("invalid stopped field")
        key = (check_id, profile, 1)
        if key in self.keys:
            self.issues.append(f"{check_id} ({profile}) was reported again")
            return
        self.keys.add(key)
        self.checks.append({
            "id": check_id, "profile": profile, "status": status, "started": started / 1000, "ended": ended / 1000,
            "observation": self.paths(entry["observation"]),
            "reason": self.paths(entry["reason"]) if status == "fail" else None,
            "failureCode": entry["failureCode"] if status == "fail" else None,
            "driver": by == "driver", "stopped": entry.get("stopped") is True, "expectation": None,
        })


def apply_rules(checks, manifest, pins):
    """RESULTS.md's expected-failure rule: in its profile, with its plugin at
    its commit, the rule's check is an xfail if the warning wasn't observed
    and its prerequisites passed, an xpass if it passed, and a fail otherwise."""
    commits = {pin["name"]: pin["commit"] for pin in pins}
    statuses = {(check["id"], check["profile"]): check["status"] for check in checks}
    for rule in manifest.xfails:
        if commits.get(rule["plugin"]) != rule["commit"]:
            continue
        for check in checks:
            if check["id"] != rule["check"] or check["profile"] != rule["profile"]:
                continue
            if check["status"] == "pass":
                check.update(status="xpass", expectation=rule["id"], reason=(
                    f"rule {rule['id']} expects this check to fail, but it passed: review the {rule['plugin']} "
                    "update and the rule"))
            elif check["failureCode"] == rule["failureCode"] and all(
                    statuses.get((required, check["profile"])) == "pass" for required in rule["requires"]):
                check.update(status="xfail", expectation=rule["id"])


def end(args):
    """Writes the stream from the observations, and returns run.sh's exit
    status."""
    code = args.exit_code
    launched = args.launched_at if args.launched_at is not None else now()
    manifest = Manifest()
    paths = Paths(args.work, args.out)
    seen = Observations(args.observations, manifest, paths, launched)
    try:
        os.remove(args.observations)
    except OSError:
        pass
    issues = list(seen.issues)

    facts = {}
    for name in manifest.facts():
        if name in seen.facts:
            facts[name] = seen.facts[name]
        else:
            facts[name] = unavailable("driver.lua didn't observe it: the run stopped before the main profile's "
                                      "driver reached the step that it decides")
    keys = manifest.plan(facts)
    planned = set(keys)
    checks = []
    for check in seen.checks:
        if (check["id"], check["profile"], 1) in planned:
            checks.append(check)
        else:
            issues.append(f"{check['id']} ({check['profile']}) was reported, but the plan doesn't have it")
    pins = lock_pins(args.starter_commit)
    apply_rules(checks, manifest, pins)

    # Which planned checks have no observation, and why.
    reported = {(check["id"], check["profile"], 1) for check in checks}
    # The failed check of run.sh's own at which it stopped, if any.
    stopped_at = next((check for check in checks if check["stopped"]), None)
    first_failure = {}
    for check in checks:
        if check["status"] == "fail" and check["driver"]:
            first_failure.setdefault(check["profile"], check)
    skips, missing = [], []
    stopped = now()
    for key in keys:
        if key in reported:
            continue
        check_id, profile, _ = key
        driver = manifest.checks[check_id]["source"].endswith("driver.lua")
        if stopped_at is not None:
            reason = f"not run: run.sh stopped after {stopped_at['id']} failed"
        elif driver and profile in seen.done and profile not in seen.errors and profile in first_failure:
            # driver.lua finished without running it, because a check that
            # it needs failed, as main_checks does after its attach fails.
            reason = f"not run: {first_failure[profile]['id']} failed in profile {profile}"
        else:
            missing.append(key)
            continue
        skips.append({"id": check_id, "profile": profile, "status": "skip", "started": stopped, "ended": stopped,
                      "observation": "not run", "reason": reason, "failureCode": None, "expectation": None,
                      "driver": driver, "stopped": False})

    for profile, message in sorted(seen.errors.items()):
        issues.append(f"driver.lua failed outside a check in profile {profile}: {message}")
    for check in checks:
        if check["id"] == COMPLETE and check["failureCode"] == "deadline":
            issues.append(f"Neovim exceeded its deadline in profile {check['profile']}")
    if missing:
        more = f" and {len(missing) - 1} more" if len(missing) > 1 else ""
        issues.append(f"{missing[0][0]} ({missing[0][1]}){more} have no check event")
    work_left = bool(args.work) and os.path.lexists(args.work) and not args.keep_work
    if work_left:
        issues.append("the work directory remains after its removal")
    cleanup_check = next((check for check in checks if check["id"] == CLEANUP), None)
    if cleanup_check is None:
        # run.sh stops before it creates the work directory or starts any
        # process, or it records the cleanup.
        cleanup = "unknown" if args.work else "verified"
    else:
        cleanup = "verified" if cleanup_check["status"] == "pass" and not work_left else "failed"

    server = seen.server
    if server is None:
        reason = "the launcher stopped before it installed the server"
        missing_value = unavailable(reason)
        server = {"versionText": missing_value, "version": None, "sourceRevision": None, "virgilRevision": None,
                  "target": missing_value, "sha256": missing_value, "workerSha256": missing_value}
    host = nvim.machine(None if is_unavailable(server["target"]) else server["target"])
    setup = {"linux": "lazyvim-linux", "macos": "lazyvim-macos", "wsl": "lazyvim-wsl"}[host["os"]]
    version = re.match(r"NVIM v(\S+)", args.nvim_version or "")
    runtimes = {"python3": platform.python_version()}
    for runtime in args.runtime:
        name, _, value = runtime.partition("=")
        if value:
            runtimes[name] = value
    for name, command in (("tmux", ["tmux", "-V"]), ("git", ["git", "--version"])):
        text = (nvim.run_text(command) or "").strip()
        if text:
            runtimes[name] = text
    profiles = {profile for _, profile, _ in keys}
    configuration = {name.partition(":")[2]: digest for name, digest in seen.digests.items()
                     if name.startswith("configuration:") and name.partition(":")[2] in profiles - {"wrapper"}}
    fixture = seen.digests.get("fixture") or unavailable(
        f"run.sh stopped after {stopped_at['id']} failed, before it copied the fixture" if stopped_at
        else "run.sh stopped before it copied the fixture")
    root = REPO / "vendor" / "virgil" / "lib"

    written = now()
    events = []

    def event(kind, **fields):
        events.append({"schema": 1, "event": kind, "runId": run_id, "seq": len(events), **fields})

    def check_event(check):
        event("check", time=utc(check["ended"]), id=check["id"], profile=check["profile"], iteration=1,
              status=check["status"], level=manifest.checks[check["id"]]["level"], startedAt=utc(check["started"]),
              durationMs=max(0, round((check["ended"] - check["started"]) * 1000)), reason=check["reason"],
              expectation=check["expectation"], failureCode=check["failureCode"],
              observation=check["observation"], artifacts=[])

    run_id = f"lazyvim-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(launched))}-{secrets.token_hex(4)}"
    event("run-start", time=utc(written), release=args.release, purpose=args.purpose, suite=SUITE, setup=setup,
          lane=nvim.lane(), command=command_of(args.command), coverageSha256=sha256(MANIFEST),
          expected=[{"id": check_id, "profile": profile, "iteration": iteration}
                    for check_id, profile, iteration in keys],
          facts=facts,
          provenance={
              "server": server, "archive": seen.archive, "vsix": None, "clientMode": "none",
              "harness": nvim.harness(),
              "editor": {"name": "nvim", "version": version.group(1) if version else None, "commit": None,
                         "arch": None},
              "client": host, "host": host, "transport": {"kind": "local", "topology": "local", "extensionHost": None},
              "pins": pins, "configurationSha256": configuration, "fixtureSha256": fixture,
              "virgilRootSha256": tree_sha256(root) if root.is_dir() else None,
              "runtimes": runtimes, "ci": nvim.ci(),
          },
          attestation=None)
    for check in checks + skips:
        check_event(check)

    counts = dict.fromkeys(("pass", "fail", "xfail", "xpass", "skip"), 0)
    for check in checks + skips:
        counts[check["status"]] += 1
    provenance = events[0]["provenance"]
    unavailable_values = [value for value in [*provenance["server"].values(), provenance["fixtureSha256"],
                                              *facts.values()] if is_unavailable(value)]
    preflight = next((check for check in checks if check["id"] == PREFLIGHT), None)
    passed = (not missing and counts["pass"] + counts["xfail"] == len(keys) and code == 0 and cleanup == "verified"
              and not issues and not unavailable_values)
    preflight_failed = preflight is not None and preflight["status"] == "fail"
    status = "pass" if passed else "unavailable" if preflight_failed or any(
        is_unavailable(value) for value in provenance["server"].values()) else "fail"
    final = 0 if passed else code or 1
    if not args.interrupted:
        finished = now()
        event("run-end", time=utc(finished), complete=not missing, status=status,
              durationMs=max(0, round((finished - launched) * 1000)), expectedCount=len(keys),
              checkCount=len(checks) + len(skips), counts=counts, exitCode=final, signal=None, cleanup=cleanup,
              issues=issues)
    with open(args.stream, "w", encoding="utf-8", newline="\n") as file:
        for entry in events:
            file.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    if args.interrupted:
        print(f"results.py: the run was interrupted, so {args.stream} has no run-end", file=sys.stderr)
        return code or 1
    outcome = {"pass": "passed", "fail": "failed", "unavailable": "unavailable"}[status]
    print(f"Results: {args.stream}: run {outcome} ({len(checks) + len(skips)} of {len(keys)} observations): "
          + ", ".join(f"{count} {name}" for name, count in counts.items()))
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    steps = parser.add_subparsers(dest="step", required=True)
    step = steps.add_parser("check")
    step.add_argument("observations")
    step.add_argument("--id", required=True)
    step.add_argument("--profile", required=True)
    step.add_argument("--status", choices=("pass", "fail"), required=True)
    step.add_argument("--started", type=seconds, required=True)
    step.add_argument("--observation", required=True)
    step.add_argument("--reason")
    step.add_argument("--failure-code")
    step.add_argument("--stopped", action="store_true", help="run.sh stops at this failed check")
    step = steps.add_parser("server")
    step.add_argument("observations")
    step.add_argument("--server", required=True)
    step.add_argument("--archive")
    step.add_argument("--archive-root")
    step = steps.add_parser("digest")
    step.add_argument("observations")
    step.add_argument("--name", required=True)
    step.add_argument("--tree", required=True)
    step = steps.add_parser("end")
    step.add_argument("observations")
    step.add_argument("--stream", required=True)
    step.add_argument("--release")
    step.add_argument("--purpose", choices=nvim.PURPOSES, default="development")
    step.add_argument("--nvim-version", default="")
    step.add_argument("--starter-commit")
    step.add_argument("--launched-at", type=seconds)
    step.add_argument("--runtime", action="append", default=[])
    step.add_argument("--work")
    step.add_argument("--out")
    step.add_argument("--keep-work", action="store_true")
    step.add_argument("--exit-code", type=int, required=True)
    step.add_argument("--interrupted", action="store_true")
    step.add_argument("command", nargs="+")
    args = parser.parse_args(argv)
    return {"check": record_check, "server": record_server, "digest": record_digest, "end": end}[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
