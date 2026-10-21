#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Runs the completion repair corpus through `virgil-lsp repair` and
summarizes it (issue #128, docs/decisions/0006-incomplete-code-repair.md).

Usage: scripts/repair-corpus.py [--runs=N] [--only=SUBSTRING] [--verbose] <virgil-lsp>

The fixture cases are test/fixtures/repair/cases/*.v3, and directories of
several files; "<|>" in one file marks the cursor and is removed. The Aeneas
cases (test/fixtures/repair/aeneas-sites.txt) cut real call and member sites
of the Aeneas sources and analyze them with the whole Aeneas program; with
--runs=N, each also times N repaired analyses against N analyses of the
unmodified sources, in one process with one worker.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "test", "fixtures", "repair")
MARKER = "<|>"


def aeneas_files():
    virgil = os.path.join(ROOT, "vendor", "virgil")
    files = sorted(glob.glob(os.path.join(virgil, "aeneas", "src", "*", "*.v3")))
    with open(os.path.join(virgil, "aeneas", "DEPS")) as deps:
        for line in deps.read().split():
            if not line.startswith("lib/test/"):
                files += sorted(glob.glob(os.path.join(virgil, line)))
    return files


def fixture_cases():
    cases = []
    for path in sorted(glob.glob(os.path.join(CORPUS, "cases", "*"))):
        name = os.path.basename(path)
        files = sorted(glob.glob(os.path.join(path, "*.v3"))) if os.path.isdir(path) else [path]
        cases.append((name[:-3] if name.endswith(".v3") else name, files))
    return cases


def run_fixture(exe, name, files, extra):
    tmp = tempfile.mkdtemp(prefix="repair-")
    try:
        args, cursor = [], None
        for f in files:
            text = open(f, encoding="utf-8").read()
            out = os.path.join(tmp, os.path.basename(f))
            if MARKER in text:
                cursor = (out, len(text[:text.index(MARKER)].encode("utf-8")))
                text = text.replace(MARKER, "", 1)
            with open(out, "w", encoding="utf-8") as w:
                w.write(text)
            args.append(out)
        assert cursor, f"{name}: no {MARKER} marker"
        return run(exe, [f"--cursor={cursor[0]}:{cursor[1]}"] + extra + args, tmp)
    finally:
        shutil.rmtree(tmp)


def aeneas_cases():
    cases = []
    with open(os.path.join(CORPUS, "aeneas-sites.txt")) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            name, rel, cut, needle = line.rstrip("\n").split("\t")
            cases.append((name, rel, cut, needle))
    return cases


def run_aeneas(exe, case, files, extra):
    name, rel, cut, needle = case
    path = os.path.join(ROOT, "vendor", "virgil", "aeneas", "src", rel)
    data = open(path, "rb").read()
    plain = needle.replace("|", "", 1).encode()
    at = data.find(plain)
    assert at >= 0, f"{name}: needle not found in {rel}"
    offset = at + len(needle[:needle.index("|")].encode())
    return run(exe, [f"--cursor={path}:{offset}", f"--cut={cut}"] + extra + files, None)


def run(exe, args, tmp):
    p = subprocess.run([exe, "repair"] + args, capture_output=True, text=True)
    if p.returncode not in (0, 1):
        sys.exit(f"virgil-lsp repair {' '.join(args[:3])} ... exited with {p.returncode}:\n{p.stderr}")
    out = p.stdout.replace(ROOT + os.sep, "")
    if tmp:
        out = out.replace(tmp + os.sep, "")
    return out


def parse(out):
    fields = {}
    attempts = []
    for line in out.splitlines():
        key, _, rest = line.partition(": ")
        if key == "attempt":
            attempts.append(rest)
        elif key in ("case", "result", "time"):
            fields[key] = rest
    result = dict(re.findall(r"(\S+)=(\S+)", fields["result"]))
    m = re.search(r" context=(.*?) all-usable=", fields["result"])
    result["context"] = m.group(1) if m else "-"
    result["member"] = "member=yes" in fields["case"]
    result["time"] = fields.get("time")
    result["attempts"] = attempts
    return result


def main():
    args = sys.argv[1:]
    runs, only, verbose = 0, None, False
    while args and args[0].startswith("--"):
        a = args.pop(0)
        if a.startswith("--runs="):
            runs = int(a[len("--runs="):])
        elif a.startswith("--only="):
            only = a[len("--only="):]
        elif a == "--verbose":
            verbose = True
        else:
            sys.exit(__doc__)
    if len(args) != 1:
        sys.exit(__doc__)
    exe = args[0]
    rows = []
    for name, files in fixture_cases():
        if only and only not in name:
            continue
        out = run_fixture(exe, name, files, [])
        rows.append((name, parse(out), out))
    files = aeneas_files()
    for case in aeneas_cases():
        if only and only not in case[0]:
            continue
        out = run_aeneas(exe, case, files, [f"--runs={runs}"] if runs else [])
        rows.append((case[0], parse(out), out))

    width = max(len(r[0]) for r in rows)
    print(f"{'case':<{width}}  {'after .':<7}  {'first usable':<32}  {'first verified':<24}  context")
    for name, r, out in rows:
        print(f"{name:<{width}}  {'yes' if r['member'] else 'no':<7}  {r['usable']:<32}  {r['verified']:<24}  {r['context']}")
        if verbose:
            print("    " + out.strip().replace("\n", "\n    "))
    total = len(rows)
    usable = [r for _, r, _ in rows if r["usable"] != "-"]
    print()
    print(f"usable: {len(usable)} of {total} ({100 * len(usable) / total:.0f}%)")
    print(f"verified without other errors: {sum(1 for _, r, _ in rows if r['verified'] != '-')} of {total}")
    for label, subset in (("after .", [r for _, r, _ in rows if r["member"]]), ("elsewhere", [r for _, r, _ in rows if not r["member"]])):
        n = sum(1 for r in subset if r["usable"] != "-")
        print(f"  {label}: {n} of {len(subset)}")
    kinds = {}
    for _, r, _ in rows:
        kinds[r["usable"]] = kinds.get(r["usable"], 0) + 1
    print("first usable repair (- for none):")
    for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {k}: {n}")
    usable_when_tried = {}
    for _, r, _ in rows:
        for kind in r.get("all-usable", "-").split(","):
            if kind != "-":
                usable_when_tried[kind] = usable_when_tried.get(kind, 0) + 1
    print("usable when tried (cases where the repair works, first or not):")
    for k, n in sorted(usable_when_tried.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {k}: {n}")
    timed = [(name, r["time"]) for name, r, _ in rows if r["time"]]
    if timed:
        print("latency (wall clock per analysis, one worker reused):")
        for name, t in timed:
            print(f"  {name}: {t}")


if __name__ == "__main__":
    main()
