#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Build two resolved refs at equal-length paths and benchmark them together."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def resolve(ref):
    # checkout fetches remote branches, but creates only its selected local
    # branch. Accept a dispatch input such as main as well as origin/main.
    for candidate in (ref, "refs/remotes/origin/" + ref):
        command = ["git", "rev-parse", "--verify", "--end-of-options", candidate + "^{commit}"]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        if result.returncode == 0:
            return result.stdout.strip()
    raise ValueError(f"unknown commit, tag, or branch: {ref}")


def previous_week(new, now):
    cutoff = int((now - datetime.timedelta(days=7)).timestamp())
    # Do not let a merge's side branch or nonmonotonic dates pick the baseline.
    for line in git("log", "--first-parent", "--format=%H %ct", new).splitlines():
        revision, timestamp = line.split()
        if int(timestamp) <= cutoff:
            return revision
    return resolve(git("describe", "--tags", "--match", "v[0-9]*", "--abbrev=0", new))


def comparable(revision, pin):
    """Whether revision has the benchmark tooling (#237) and the pinned Virgil
    of the tooling checkout's corpus."""
    def entry(path):
        # The tree entry's object name. A submodule's commit isn't in this
        # repository, so don't require the object itself.
        command = ["git", "rev-parse", "--verify", "--quiet", f"{revision}:{path}"]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else None
    return entry("scripts/bench-build-info.py") is not None and entry("vendor/virgil") == pin


def automatic_base(requested, newer):
    """The week-old baseline, or, if it predates the benchmark tooling or the
    pinned Virgil, the first later revision on newer's first-parent history
    that has both, with a note saying so. A comparison that would only fail
    because history is too short moves forward instead."""
    pin = git("rev-parse", "HEAD:vendor/virgil")
    if comparable(requested, pin):
        return requested, None
    for revision in git("rev-list", "--first-parent", "--reverse", f"{requested}..{newer}").splitlines():
        if comparable(revision, pin):
            return revision, (f"The week-old baseline {requested} predates the benchmark tooling or "
                              f"the pinned Virgil; the baseline moved forward to {revision}.")
    raise ValueError(f"no first-parent revision from {requested} to {newer} has the benchmark tooling and the pinned Virgil")


def select_refs(event_name, event, base, new, now):
    """The base and new revisions, and a note if an automatic base moved.
    Explicit refs are used as given, so one without benchmark support fails."""
    if event_name == "pull_request":
        return (resolve(event["pull_request"]["base"]["sha"]),
                resolve(event["pull_request"]["head"]["sha"]), None)
    newer = resolve("origin/main" if event_name == "schedule" else new)
    if event_name == "schedule" or base == "last-week":
        older, note = automatic_base(previous_week(newer, now), newer)
        return older, newer, note
    return resolve(base), newer, None


def run(*args, cwd=ROOT):
    print("+ " + shlex.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def compare(base, new, work, output, note=None):
    work.mkdir(parents=True)  # Refuse to reuse a previous run's builds.
    output.mkdir(parents=True, exist_ok=True)
    paths = [work / "base", work / "next"]
    added = []
    try:
        pin = git("rev-parse", "HEAD:vendor/virgil")
        for revision, path in zip((base, new), paths):
            if git("rev-parse", revision + ":vendor/virgil") != pin:
                raise ValueError("both refs and the benchmark corpus must use the same pinned Virgil")
            run("git", "worktree", "add", "--detach", path, revision)
            added.append(path)
            run("git", "submodule", "update", "--init", "--recursive", "--reference", ROOT / "vendor/virgil", cwd=path)
            if not (path / "scripts/bench-build-info.py").exists():
                raise ValueError(f"{revision} predates benchmark probes/metadata; choose a ref with benchmark support")
            run("make", "BUILD=build/bench", "BENCH=1", "buildinfo", cwd=path)
            info = path / "build/bench/gen/BuildInfo.v3"
            # git describe lengths vary across tags and distances. Use full SHAs
            # in both binaries, without modifying any tracked source file.
            text, count = re.subn(r'def REVISION = "[^"]*";', f'def REVISION = "{revision}";', info.read_text())
            if count != 1:
                raise ValueError("expected exactly one BuildInfo revision")
            info.write_text(text)
            run("make", "-o", "buildinfo", "BUILD=build/bench", "BENCH=1", "all", "bench-metadata", cwd=path)
        refs = {"base": base, "new": new, "note": note}
        (output / "refs.json").write_text(json.dumps(refs, indent=2) + "\n")
        run(sys.executable, ROOT / "scripts/bench-lsp.py",
            "--exe", paths[0] / "build/bench/virgil-lsp",
            "--exe", paths[1] / "build/bench/virgil-lsp",
            "--blocks", "3", "--operations", "5", "--warmups", "2", "--require-probes",
            "--cpu", "none" if sys.platform == "darwin" else "auto",
            "--output", output / "comparison.json")
    finally:
        for path in reversed(added):
            run("git", "worktree", "remove", "--force", path)
        shutil.rmtree(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="last-week")
    parser.add_argument("--new-ref", default="origin/main")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text()) if event_path else {}
    refs = select_refs(os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"), event,
                       args.base_ref, args.new_ref, datetime.datetime.now(datetime.timezone.utc))
    base, new, note = refs
    print(f"Comparing base {base} with new {new}", flush=True)
    if note:
        print(f"::notice::{note}", flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write(note + "\n\n")
    compare(base, new, args.work_dir.resolve(), args.output_dir.resolve(), note)


if __name__ == "__main__":
    main()
