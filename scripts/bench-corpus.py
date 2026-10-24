#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Explicitly regenerate the reviewed corpus pin; ordinary runs only check it."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "test/benchmarks/corpus.json"
SMALL_REVISION = "9a9e384dc371a88268762a66115c1bec8c283f0d"  # Revision introducing the benchmark's source baseline.
# The reviewed corpus covers these projects. The list is fixed here rather than
# derived from the manifest so that deleting a project cannot disable its checks.
PROJECTS = ("small", "util", "aeneas")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def position(text, offset):
    before = text[:offset]
    return {"line": before.count("\n"),
            "character": len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2}


def site(path, anchor, token, occurrence=0):
    text = (ROOT / path).read_text()
    assert text.count(anchor) == 1, (path, anchor, "anchor must be unique")
    matches = list(re.finditer(r"\b" + re.escape(token) + r"\b", anchor))
    offset = text.index(anchor) + matches[occurrence].start()
    return {"path": path, "anchor": anchor, "token": token, "occurrence": occurrence,
            "offset": offset, "range": {"start": position(text, offset),
                                         "end": position(text, offset + len(token))}}


def git_revision(path, *args):
    # Do not accidentally discover a containing repository in an exported tree.
    if not (path / ".git").exists():
        return "unknown"
    try:
        return subprocess.check_output(["git", *args], cwd=path, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def revisions():
    # Read the gitlink from the index, not HEAD: CI and fresh clones have the
    # same value there, and a staged pin bump can be validated before it is
    # committed. Only inspecting another commit inside vendor/virgil (the
    # advisory workflow) leaves the index alone.
    tree = git_revision(ROOT, "ls-files", "-s", "vendor/virgil")
    match = re.fullmatch(r"160000 ([0-9a-f]{40}) 0\tvendor/virgil", tree)
    return (match[1] if match else "unknown",
            git_revision(ROOT / "vendor/virgil", "rev-parse", "HEAD"))


def check_projects(pinned):
    """Require the manifest to list exactly the reviewed projects, in any mode."""
    projects = pinned.get("projects") or {}
    missing = [name for name in PROJECTS if name not in projects]
    extra = sorted(set(projects) - set(PROJECTS))
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise AssertionError(f"corpus projects must be exactly {', '.join(PROJECTS)}; "
                             f"the manifest has {' and '.join(details)}; "
                             "run python3 scripts/bench-corpus.py --write and review the diff")


def validate(strict=True):
    """Validate all pins for benchmarks; tests may skip an unpinned vendor tree."""
    pinned = json.loads(MANIFEST.read_text())
    check_projects(pinned)
    gitlink, checkout = revisions()
    revision = pinned["virgil_source_revision"]
    regenerate = ("run python3 scripts/bench-corpus.py --write and review the hashes and anchors; "
                  "see CONTRIBUTING.md#updating-the-pinned-virgil-revision")
    if gitlink != "unknown" and revision != gitlink:
        raise AssertionError(f"corpus Virgil revision {revision} differs from superproject gitlink {gitlink}; "
                             + regenerate)
    vendor_matches = gitlink == revision and checkout == revision
    if strict and not vendor_matches:
        raise AssertionError(f"benchmark requires pinned Virgil {revision}; gitlink {gitlink}, checkout {checkout}; "
                             + regenerate)
    projects = list(PROJECTS) if vendor_matches else ["small"]
    if not vendor_matches:
        print(f"benchmark corpus: skipping vendor projects (util, aeneas): pinned {revision}, "
              f"gitlink {gitlink}, checkout {checkout}; checking in-repo fixtures", file=sys.stderr)
    expected = {**pinned, "projects": {name: pinned["projects"][name] for name in projects}}
    assert generate(projects, revision) == expected, "corpus sources or anchors changed; " + regenerate
    return pinned, projects


def generate(projects=PROJECTS, virgil_revision=None):
    virgil = ROOT / "vendor/virgil"
    vendor = any(name != "small" for name in projects)
    dependencies = ([p for p in (virgil / "aeneas/DEPS").read_text().split()
                     if not p.startswith("lib/test/")] if vendor else [])
    paths = {"small": sorted(ROOT.glob("test/fixtures/analysis/two-file/*.v3")),
             "util": sorted(virgil.glob("lib/util/*.v3")),
             "aeneas": sorted(virgil.glob("aeneas/src/*/*.v3"))}
    for pattern in dependencies:
        paths["aeneas"].extend(sorted(virgil.glob(pattern)))
    small = "test/fixtures/analysis/two-file/main.v3"
    vector = "vendor/virgil/lib/util/Vector.v3"
    program = "vendor/virgil/aeneas/src/core/Program.v3"
    aeneas = "vendor/virgil/aeneas/src/main/Aeneas.v3"
    anchors = {
        "small": (small, "return total + r.width;", "var total = r.area();", "total", "var total: int"),
        "util": (vector, "return if(index >= 0 && index < length, array[index]);",
                 "def get(index: int) -> T", "index", "index: int"),
        "aeneas": (program, "if (explicitName != null) return explicitName;",
                   "var explicitName: string;", "explicitName", "var explicitName: Array<byte>")}
    queries = {name: (site(path, query, token), site(path, target, token), hover)
               for name, (path, query, target, token, hover) in anchors.items() if name in projects}
    references = {
        "small": [queries["small"][1], queries["small"][0]],
        "util": [queries["util"][1]] + [site(vector, queries["util"][0]["anchor"], "index", n)
                                       for n in range(3)] if "util" in projects else [],
        "aeneas": [queries["aeneas"][1]] + [site(program, queries["aeneas"][0]["anchor"], "explicitName", n)
                                            for n in range(2)] + [
            site(aeneas, "prog.explicitName = CLOptions.PROGRAM_NAME.val;", "explicitName")] if "aeneas" in projects else []}
    result = {"schema": 1, "server_source_revision": SMALL_REVISION,
              "virgil_source_revision": virgil_revision or git_revision(virgil, "rev-parse", "HEAD"),
              "projects": {}}
    for name, files in paths.items():
        if name not in projects:
            continue
        query, target, hover = queries[name]
        text = (ROOT / query["path"]).read_text()
        base = str(Path(query["path"]).parent) if name != "aeneas" else "vendor/virgil"
        edits = {}
        for kind, suffix in (("syntax", "\ndef benchBroken = ;\n"),
                             ("semantic", "\ndef benchMissing = benchUnresolved;\n")):
            changed = text + suffix
            start = changed.rindex(";" if kind == "syntax" else "benchUnresolved")
            end = start if kind == "syntax" else start + len("benchUnresolved")
            edits[kind] = {"anchor": "EOF", "position": position(text, len(text)), "append": suffix,
                           "expected": {"code": "ParseError" if kind == "syntax" else "UnresolvedIdentifier",
                                        "message": "invalid start of expression" if kind == "syntax" else
                                                   'identifier "benchUnresolved" cannot be found',
                                        "range": {"start": position(changed, start), "end": position(changed, end)},
                                        "severity": 1, "source": "aeneas"}}
        result["projects"][name] = {
            "base": base, "patterns": ([str(d.relative_to(virgil)) + "/*.v3" for d in sorted((virgil / "aeneas/src").iterdir())
                                      if d.is_dir()] + dependencies if name == "aeneas" else ["*.v3"]),
            "compiler_args": [], "target": None,
            "files": [{"path": str(p.relative_to(ROOT)), "bytes": len(p.read_bytes()),
                       "sha256": sha256(p.read_bytes())} for p in files],
            "source_bytes": sum(p.stat().st_size for p in files),
            "lines": sum(p.read_bytes().count(b"\n") for p in files),
            "query": query, "definition": target, "hover_prefix": hover,
            "references": references[name], "completion_label": query["token"],
            "completion_positions": [position(text, query["offset"] + n) for n in (1, 2)],
            "rename_to": "benchRenamed", "edits": edits}
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check the pin (the default)")
    parser.add_argument("--write", action="store_true", help="replace the pin (review its diff)")
    args = parser.parse_args()
    if args.write:
        actual = generate()
        MANIFEST.write_text(json.dumps(actual, indent=2) + "\n")
    else:
        pinned, projects = validate(strict=False)
        actual = {**pinned, "projects": {name: pinned["projects"][name] for name in projects}}
    for name, p in actual["projects"].items():
        print(f"{name}: {len(p['files'])} files, {p['source_bytes']} bytes, {p['lines']} lines")
