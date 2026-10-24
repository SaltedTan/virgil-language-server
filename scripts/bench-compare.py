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
    return git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}")


def previous_week(new, now):
    cutoff = int((now - datetime.timedelta(days=7)).timestamp())
    # Do not let a merge's side branch or nonmonotonic dates pick the baseline.
    for line in git("log", "--first-parent", "--format=%H %ct", new).splitlines():
        revision, timestamp = line.split()
        if int(timestamp) <= cutoff:
            return revision
    return resolve(git("describe", "--tags", "--match", "v[0-9]*", "--abbrev=0", new))


def select_refs(event_name, event, base, new, now):
    if event_name == "pull_request":
        return (resolve(event["pull_request"]["base"]["sha"]),
                resolve(event["pull_request"]["head"]["sha"]))
    newer = resolve("origin/main" if event_name == "schedule" else new)
    older = previous_week(newer, now) if event_name == "schedule" or base == "last-week" else resolve(base)
    return older, newer


def run(*args, cwd=ROOT):
    print("+ " + shlex.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def compare(base, new, work, output):
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
        (output / "refs.json").write_text(json.dumps({"base": base, "new": new}, indent=2) + "\n")
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
    print(f"Comparing base {refs[0]} with new {refs[1]}", flush=True)
    compare(*refs, args.work_dir.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
