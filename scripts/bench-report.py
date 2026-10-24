#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Render advisory comparisons or a single build's reference baseline as Markdown."""
import argparse
import collections
import json
from pathlib import Path
import sys

# The Linux reference has analysis spreads below 18%, but some fast
# requests have much wider tails. Also require disjoint p10-p90 intervals.
THRESHOLD = 0.25
SCENARIOS = ("fresh_analysis", "reused_analysis", "syntax_diagnostic", "semantic_diagnostic",
             "completion_miss", "completion_hit", "definition", "hover", "references", "prepareRename", "rename")
PROJECTS = ("small", "util", "aeneas")


def latency(row):
    return f"{row['median']:.3f} ({row['p10']:.3f}–{row['p90']:.3f})"


def rows(result):
    if result.get("schema") != 1 or result.get("error") or not result.get("summary"):
        raise ValueError("expected a successful schema-1 benchmark with summaries")
    return {(r["build"], r["project"], r["scenario"], r["metric"]): r for r in result["summary"]}


def change(before, after):
    if before["median"] <= 0:
        raise ValueError("latency median must be positive")
    relative = after["median"] / before["median"] - 1
    disjoint = before["p90"] < after["p10"] or after["p90"] < before["p10"]
    return relative, disjoint and abs(relative) > THRESHOLD


def context(result, builds):
    host = result["host"]
    lines = [f"Host: {host['cpu']}; {host['kernel']}. Affinity: `{host['affinity']}`.", ""]
    for index in builds:
        build = result["builds"][index]
        lines += [f"Build {index}: `{build['build']['server_revision']}`; `{build['version']}`.", ""]
    config = result["configuration"]
    lines += [f"{config['blocks']} blocks × {config['operations']} measured operations per case; "
              f"{config['warmups']} warmups per block.", ""]
    lines += [f"Corpus SHA-256: `{result['corpus_sha256']}`. Started: {result['started_utc']}.", ""]
    return lines


def gc_totals(result):
    totals = collections.defaultdict(int)
    for block in result["blocks"]:
        for sample in block["samples"]:
            if sample["warmup"]:
                continue
            for probe in sample["probes"]:
                if probe["metric"] == "gc_count":
                    totals[(block["build"], block["project"], sample["scenario"], probe["process"])] += probe["value"]
    return totals


def comparison(result):
    if len(result["builds"]) != 2:
        raise ValueError("comparison requires exactly two builds (base, new)")
    indexed = rows(result)
    cases = {(p, s) for b, p, s, m in indexed if b == 0 and m == "latency_ms"}
    if cases != {(p, s) for b, p, s, m in indexed if b == 1 and m == "latency_ms"}:
        raise ValueError("builds must have matching measured cases")
    lines = ["## Benchmark comparison", "", *context(result, (0, 1)),
             "Latency is median (p10–p90), ms. Change is new/base − 1; positive means slower.", "",
             "Flag only when intervals do not overlap and the absolute median change exceeds 25%. "
             "Flags are warnings, including improvements; they never fail the job.", "",
             "GC is total server/worker collections over measured transactions (warmups excluded); "
             "`—` means no transaction, not a measured zero. Explicit final collections are excluded.", "",
             "| Project / scenario | Base, ms | New, ms | Change | Base GC S/W | New GC S/W | Flag |",
             "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    totals = gc_totals(result)
    warnings = []
    for project, scenario in sorted(cases):
        before, after = (indexed[(b, project, scenario, "latency_ms")] for b in (0, 1))
        relative, flagged = change(before, after)
        counts = ["/".join(str(totals[(b, project, scenario, role)])
                           if (b, project, scenario, role) in totals else "—"
                           for role in ("server", "worker")) for b in (0, 1)]
        lines.append(f"| {project} / `{scenario}` | {latency(before)} | {latency(after)} | "
                     f"{relative:+.1%} | {counts[0]} | {counts[1]} | {'WARNING' if flagged else ''} |")
        if flagged:
            warnings.append(f"{project}/{scenario}: median {relative:+.1%}; disjoint p10–p90 intervals")
    lines += ["", f"{len(warnings)} flagged cases. Raw samples, GC probes, build hashes, and host details are in `comparison.json`.", ""]
    return "\n".join(lines), warnings


def baseline(result, build):
    indexed = rows(result)
    if not 0 <= build < len(result["builds"]):
        raise ValueError("baseline build index out of range")
    lines = ["## Reference baseline (new build)", "", *context(result, (build,)),
             "Median (p10–p90), in milliseconds; warmups excluded. This host is not comparable with other hosts.", "",
             "| Scenario | small | util | Aeneas |", "| --- | ---: | ---: | ---: |"]
    for scenario in SCENARIOS:
        cells = [latency(indexed[(build, p, scenario, "latency_ms")]) for p in PROJECTS]
        lines.append(f"| `{scenario}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--annotations", action="store_true", help="emit GitHub warning annotations to stderr")
    args = parser.parse_args()
    result = json.loads(args.input.read_text())
    if args.baseline:
        print(baseline(result, 1), end="")
    else:
        report, warnings = comparison(result)
        print(report, end="")
        if args.annotations:
            for warning in warnings:
                escaped = warning.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print("::warning title=Benchmark change::" + escaped, file=sys.stderr)


if __name__ == "__main__":
    main()
