#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Explicitly regenerate the reviewed corpus pin; ordinary runs only check it."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "test/benchmarks/corpus.json"
SMALL_REVISION = "9a9e384"  # Revision introducing the benchmark's source baseline.


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


def generate():
    virgil = ROOT / "vendor/virgil"
    dependencies = [p for p in (virgil / "aeneas/DEPS").read_text().split()
                    if not p.startswith("lib/test/")]
    paths = {"small": sorted(ROOT.glob("test/fixtures/analysis/two-file/*.v3")),
             "util": sorted(virgil.glob("lib/util/*.v3")),
             "aeneas": sorted(virgil.glob("aeneas/src/*/*.v3"))}
    for pattern in dependencies:
        paths["aeneas"].extend(sorted(virgil.glob(pattern)))
    small = "test/fixtures/analysis/two-file/main.v3"
    vector = "vendor/virgil/lib/util/Vector.v3"
    program = "vendor/virgil/aeneas/src/core/Program.v3"
    aeneas = "vendor/virgil/aeneas/src/main/Aeneas.v3"
    queries = {
        "small": (site(small, "return total + r.width;", "total"),
                  site(small, "var total = r.area();", "total"), "var total: int"),
        "util": (site(vector, "return if(index >= 0 && index < length, array[index]);", "index"),
                 site(vector, "def get(index: int) -> T", "index"), "index: int"),
        "aeneas": (site(program, "if (explicitName != null) return explicitName;", "explicitName"),
                   site(program, "var explicitName: string;", "explicitName"), "var explicitName: Array<byte>")}
    references = {
        "small": [queries["small"][1], queries["small"][0]],
        "util": [queries["util"][1]] + [site(vector, queries["util"][0]["anchor"], "index", n)
                                       for n in range(3)],
        "aeneas": [queries["aeneas"][1]] + [site(program, queries["aeneas"][0]["anchor"], "explicitName", n)
                                            for n in range(2)] + [
            site(aeneas, "prog.explicitName = CLOptions.PROGRAM_NAME.val;", "explicitName")]}
    result = {"schema": 1, "server_source_revision": subprocess.check_output(
        ["git", "rev-parse", SMALL_REVISION], cwd=ROOT, text=True).strip(),
        "virgil_source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=virgil, text=True).strip(), "projects": {}}
    for name, files in paths.items():
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
    parser.add_argument("--write", action="store_true", help="replace the pin (review its diff)")
    args = parser.parse_args()
    actual = generate()
    if args.write:
        MANIFEST.write_text(json.dumps(actual, indent=2) + "\n")
    else:
        assert actual == json.loads(MANIFEST.read_text()), "corpus changed; review before --write"
    for name, p in actual["projects"].items():
        print(f"{name}: {len(p['files'])} files, {p['source_bytes']} bytes, {p['lines']} lines")
