#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Render advisory comparisons, resource checks, or a single build's reference baseline as Markdown."""
import argparse
import collections
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import statistics
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


# Resource checks (#347): deterministic worker allocation, RESULT size, and
# in-handle collection checks under a reviewed policy file. See
# docs/benchmarks.md#resource-checks.
SEVERITIES = ("fail", "warn", "report")
WORKER_RESOURCE_METRICS = ("allocated_bytes", "result_bytes", "gc_count", "heap_after_handle_bytes")
# Metadata that must match between two builds, or between a build and an
# approved absolute baseline, for their resource numbers to be compared.
COMPATIBILITY = ("virgil_revision", "compiler_sha256", "corpus_sha256", "target", "benchmark_probes",
                 "worker_heap", "worker_heap_bytes", "server_heap", "root_bytes", "build_dir_bytes", "options")


def annotation(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def load_policy(policy):
    """Validates a resource-policy file; returns its scenario-to-group map."""
    if policy.get("schema") != 1 or policy.get("status") not in ("proposed", "approved"):
        raise ValueError("expected a schema-1 resource policy with status proposed or approved")
    if (policy["status"] == "approved") != bool(policy.get("approval")):
        raise ValueError("an approved resource policy must record its approval, and only an approved one may")
    groups = {}
    for group in policy["groups"]:
        threshold = group["allocation_increase"]
        if threshold is not None and not threshold >= 0 or group["new_worker_gc"] not in SEVERITIES:
            raise ValueError(f"invalid resource group {group['name']}")
        for scenario in group["scenarios"]:
            if scenario in groups or scenario in policy["excluded"]:
                raise ValueError(f"scenario {scenario} is in more than one group or exclusion")
            groups[scenario] = group
    if policy["pending_baseline"] not in SEVERITIES or not policy["headroom_warning_bytes"] >= 0:
        raise ValueError("invalid pending-baseline severity or headroom")
    return groups


def build_metadata(result, index, policy):
    """The compatibility metadata of one build, and the names of missing fields."""
    build = result["builds"][index]
    manifest = build.get("build") or {}
    match = re.search(r"^\s*virgil revision: (\S+)$", build.get("version") or "", re.M)
    heaps = {probe["value"] for block in result.get("blocks", ()) if block["build"] == index
             for sample in block["samples"] for probe in sample["probes"]
             if probe["process"] == "server" and probe["metric"] == "worker_heap_bytes"}
    configuration = result.get("configuration") or {}
    metadata = {"virgil_revision": match and match[1],
                "compiler_sha256": (manifest.get("compiler") or {}).get("sha256"),
                "corpus_sha256": result.get("corpus_sha256"), "target": manifest.get("target"),
                "benchmark_probes": manifest.get("benchmark_probes"),
                "worker_heap": manifest.get("worker_heap"),
                # The worker's actual reserved heap from its HELLO, both semispaces.
                "worker_heap_bytes": min(heaps) if len(heaps) == 1 else None,
                "server_heap": manifest.get("server_heap"),
                # Path bytes, not paths: analyzed sources sit under the tooling
                # root and compiled-in paths under the build directory.
                "root_bytes": len(result["root"].encode()) if result.get("root") else None,
                "build_dir_bytes": len(str(Path(build["exe"]).parent).encode()) if build.get("exe") else None,
                "options": {key: configuration.get(key) for key in policy["options"]}}
    missing = [key for key, value in metadata.items() if value is None]
    if len(heaps) > 1:
        missing[missing.index("worker_heap_bytes")] = "a single worker_heap_bytes"
    return metadata, missing


def resource_cases(result, policy, groups):
    """{(build, project, scenario): worker values} over measured samples, the
    scenarios with worker transactions that the policy doesn't review, and
    qualification problems."""
    problems, cases, unreviewed = [], {}, set()
    samples = collections.defaultdict(list)
    for block in result["blocks"]:
        for sample in block["samples"]:
            if not sample["warmup"]:
                samples[(block["build"], block["project"], sample["scenario"])].append(sample)
    for (build, project, scenario), measured in sorted(samples.items()):
        if scenario not in groups:
            if scenario not in policy["excluded"] and any(
                    p["process"] == "worker" for sample in measured for p in sample["probes"]):
                unreviewed.add(scenario)
            continue
        values = collections.defaultdict(list)
        for sample in measured:
            worker = collections.defaultdict(list)
            for probe in sample["probes"]:
                if probe["process"] == "worker":
                    worker[probe["metric"]].append(probe["value"])
            for metric in WORKER_RESOURCE_METRICS:
                if len(worker[metric]) != 1:
                    problems.append(f"build {build} {project}/{scenario} iteration {sample.get('iteration')}: "
                                    f"{len(worker[metric])} worker {metric} probes, expected 1")
                else:
                    values[metric].append(worker[metric][0])
        cases[(build, project, scenario)] = values
    for group in policy["groups"]:
        for scenario in group["scenarios"]:
            for project in policy["projects"]:
                present = [b for b in (0, 1) if cases.get((b, project, scenario), {}).get("allocated_bytes")]
                if group["required"] and len(present) < 2 or len(present) == 1:
                    problems.append(f"{project}/{scenario}: measured in builds {present or 'none'}, expected both")
    return cases, sorted(unreviewed), problems


def spread(values):
    values = sorted(values)
    return {"min": values[0], "median": statistics.median(values), "max": values[-1]}


def room(values, heap_bytes):
    """Room under the semispace after handling, or None if a sample collected."""
    if any(values["gc_count"]):
        return None
    return heap_bytes // 2 - max(values["heap_after_handle_bytes"])


def bytes_range(stats):
    if stats["min"] == stats["max"]:
        return f"{stats['min']:,}"
    return f"{stats['median']:,.0f} ({stats['min']:,}–{stats['max']:,})"


def compatible_baseline(policy, metadata):
    """The approved baseline whose metadata matches, or None."""
    for entry in policy["baselines"]:
        if entry.get("approval") and entry["compatibility"] == metadata:
            return entry
    return None


def over(before, after, limit):
    """Whether after exceeds before by more than the fraction limit, exactly."""
    return limit is not None and Fraction(after) > Fraction(before) * (1 + Fraction(str(limit)))


def describe(metadata):
    return ", ".join(f"{key} {value}" for key, value in metadata.items() if key != "options")


def resources(result, policy, enforce=False):
    """Markdown and verdicts [(severity, text)] for a two-build comparison."""
    groups = load_policy(policy)
    if result.get("schema") != 1 or result.get("error") or len(result.get("builds", ())) != 2:
        raise ValueError("expected a successful schema-1 two-build benchmark")
    if enforce and policy["status"] != "approved":
        raise ValueError("refusing to enforce a resource policy the maintainer has not approved")
    verdicts = []
    label = {"fail": "FAIL" if enforce else "WOULD FAIL", "warn": "WARNING"}
    lines = ["## Resource checks", "",
             f"Policy status: **{policy['status']}**" +
             ("." if policy["status"] == "approved" else "; the maintainer has not approved its thresholds.") +
             (" Mode: enforce; failed checks fail the job." if enforce else
              " Mode: report; verdicts are reported and never fail the job."), ""]
    metadata, missing = zip(*(build_metadata(result, b, policy) for b in (0, 1)))
    problems = [f"build {b}: missing {', '.join(missing[b])}" for b in (0, 1) if missing[b]]
    problems += [f"build {b}: not a BENCH=1 build" for b in (0, 1) if metadata[b]["benchmark_probes"] is False]
    for b in (0, 1):
        if metadata[b]["options"] != policy["options"]:
            problems.append(f"build {b}: scenario options {metadata[b]['options']} are not the reviewed {policy['options']}")
    cases, unreviewed, probe_problems = resource_cases(result, policy, groups)
    problems += probe_problems
    if problems:
        verdicts += [("fail", "qualification: " + problem) for problem in problems]
        lines += ["**Qualification failed.** No resource numbers are compared:", ""]
        lines += [f"- {problem}" for problem in problems]
        lines.append("")
        return finish(lines, verdicts, label)
    differs = [key for key in COMPATIBILITY if metadata[0][key] != metadata[1][key]]
    comparable = not differs
    heap_bytes = metadata[1]["worker_heap_bytes"]
    lines += [f"New build: {describe(metadata[1])}.", ""]
    if comparable:
        lines += ["Base and new builds have the same compatibility metadata.", ""]
    else:
        text = f"they differ in {', '.join(differs)}, so no relative change is derived"
        verdicts.append(("warn", "base and new builds are incomparable: " + text))
        lines += [f"**Incomparable.** Base and new builds: {text}.", ""]
    approved = compatible_baseline(policy, metadata[1])
    if approved:
        lines += [f"Absolute baseline: approved {approved['approval']}, from `{approved['source']['revision']}`.", ""]
    else:
        text = ("absolute baseline pending: no approved baseline matches the new build's metadata; "
                "adopt one with the baseline procedure in docs/benchmarks.md#resource-checks")
        if policy["pending_baseline"] != "report":
            verdicts.append((policy["pending_baseline"], text))
        lines += ["Absolute baseline: **pending**. No approved baseline matches the new build's metadata, "
                  "so the new build has no absolute check.", ""]
    lines += ["Worker allocation and RESULT bytes are the median (min–max) over measured transactions; "
              "change is new/base − 1. In-handle collections exclude the explicit collections before and "
              f"after sending. Room is what the new build's {heap_bytes // 2:,}-byte semispace (half of the "
              "worker's reported heap) has left after handling, before the explicit collection.", "",
              "| Project / scenario | Group | Base allocated | New allocated | Change | Base / new RESULT | "
              "In-handle GC base/new | New room | Verdict |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for group in policy["groups"]:
        for scenario in group["scenarios"]:
            for project in policy["projects"]:
                if (1, project, scenario) not in cases:
                    continue
                base, new = cases[(0, project, scenario)], cases[(1, project, scenario)]
                case = f"{project}/{scenario}"
                found = []
                allocated = [spread(base["allocated_bytes"]), spread(new["allocated_bytes"])]
                results = [spread(base["result_bytes"]), spread(new["result_bytes"])]
                gcs = [sum(base["gc_count"]), sum(new["gc_count"])]
                change = "incomparable"
                if comparable:
                    relative = allocated[1]["median"] / allocated[0]["median"] - 1
                    change = f"{relative:+.2%}"
                    threshold = group["allocation_increase"]
                    if over(allocated[0]["median"], allocated[1]["median"], threshold):
                        found.append(("fail", f"{case}: worker allocation {relative:+.2%}, over the {threshold:.0%} limit"))
                    limit = policy["result_bytes_increase"]
                    grown = results[1]["median"] / results[0]["median"] - 1
                    if over(results[0]["median"], results[1]["median"], limit):
                        found.append(("fail", f"{case}: RESULT bytes {grown:+.2%}, over the {limit:.0%} limit"))
                    if gcs[0] == 0 and gcs[1] > 0 and group["new_worker_gc"] != "report":
                        found.append((group["new_worker_gc"],
                                      f"{case}: {gcs[1]} new in-handle worker collections; the base had none"))
                if approved:
                    ceiling = approved["scenarios"].get(case, {}).get("ceiling")
                    if ceiling is None:
                        found.append(("warn", f"{case}: the approved baseline has no ceiling for this case"))
                    else:
                        if ceiling.get("allocated_bytes") is not None and allocated[1]["median"] > ceiling["allocated_bytes"]:
                            found.append(("fail", f"{case}: worker allocation {allocated[1]['median']:,.0f} bytes, "
                                                  f"over the approved ceiling of {ceiling['allocated_bytes']:,}"))
                        if ceiling.get("worker_gc") is not None and max(new["gc_count"]) > ceiling["worker_gc"]:
                            found.append(("fail", f"{case}: {max(new['gc_count'])} in-handle worker collections, "
                                                  f"over the approved ceiling of {ceiling['worker_gc']}"))
                left = room(new, heap_bytes)
                if left is not None and left < policy["headroom_warning_bytes"]:
                    found.append(("warn", f"{case}: {left:,} bytes of semispace room after handling, under "
                                          f"{policy['headroom_warning_bytes']:,}"))
                verdicts += found
                cell = ", ".join(label[s] for s in ("fail", "warn") if any(s == f for f, _ in found))
                lines.append(f"| {project} / `{scenario}` | {group['name']} | {bytes_range(allocated[0])} | "
                             f"{bytes_range(allocated[1])} | {change} | {results[0]['median']:,.0f} / "
                             f"{results[1]['median']:,.0f} | {gcs[0]}/{gcs[1]} | "
                             f"{'collected' if left is None else f'{left:,}'} | {cell} |")
    lines.append("")
    for scenario in unreviewed:
        verdicts.append(("warn", f"{scenario}: worker transactions in a scenario the policy doesn't review"))
    return finish(lines, verdicts, label)


def finish(lines, verdicts, label):
    failures = sum(severity == "fail" for severity, _ in verdicts)
    warnings = sum(severity == "warn" for severity, _ in verdicts)
    lines += [f"{failures} {label['fail'].lower()} and {warnings} warnings. "
              "Server collections are not checked; see the comparison table above.", ""]
    lines += [f"- {label[severity]}: {text}" for severity, text in verdicts]
    return "\n".join(lines) + ("\n\n" if verdicts else "\n"), verdicts


def resource_baseline(result, policy):
    """A candidate absolute baseline for the new build, for the maintainer to approve."""
    groups = load_policy(policy)
    metadata, missing = build_metadata(result, 1, policy)
    cases, _, problems = resource_cases(result, policy, groups)
    if missing or problems or metadata["options"] != policy["options"]:
        raise ValueError(f"cannot record a baseline: missing {missing}; {problems}; options {metadata['options']}")
    scenarios = {}
    for (build, project, scenario), values in sorted(cases.items()):
        if build != 1:
            continue
        group, allocated = groups[scenario], spread(values["allocated_bytes"])
        threshold, gcs = group["allocation_increase"], max(values["gc_count"])
        scenarios[f"{project}/{scenario}"] = {
            "allocated_bytes": allocated, "result_bytes": spread(values["result_bytes"]),
            "worker_gc_max": gcs, "room_bytes": room(values, metadata["worker_heap_bytes"]),
            "ceiling": {"allocated_bytes": None if threshold is None else math.ceil(allocated["median"] * (1 + threshold)),
                        "worker_gc": 0 if group["new_worker_gc"] == "fail" and gcs == 0 else None}}
    build = result["builds"][1]
    return {"approval": None,
            "source": {"revision": build["build"]["server_revision"], "started_utc": result["started_utc"],
                       "host": result["host"]["cpu"], "samples": result["configuration"]},
            "compatibility": metadata, "scenarios": scenarios}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--annotations", action="store_true", help="emit GitHub warning annotations to stderr")
    parser.add_argument("--resources", type=Path, metavar="POLICY",
                        help="instead check worker resources against a resource-policy file")
    parser.add_argument("--mode", choices=("report", "enforce"), default="report",
                        help="with --resources: enforce exits 1 on a failed check; report never does")
    parser.add_argument("--resource-baseline", type=Path, metavar="POLICY",
                        help="instead print a candidate absolute baseline for the new build")
    args = parser.parse_args()
    result = json.loads(args.input.read_text())
    if args.resource_baseline:
        policy = json.loads(args.resource_baseline.read_text())
        print(json.dumps(resource_baseline(result, policy), indent=2))
    elif args.resources:
        enforce = args.mode == "enforce"
        report, verdicts = resources(result, json.loads(args.resources.read_text()), enforce)
        print(report, end="")
        if args.annotations:
            for severity, text in verdicts:
                kind = "error" if enforce and severity == "fail" else "warning"
                title = "Resource check" + (" (would fail)" if severity == "fail" and not enforce else "")
                print(f"::{kind} title={title}::" + annotation(text), file=sys.stderr)
        if enforce and any(severity == "fail" for severity, _ in verdicts):
            sys.exit(1)
    elif args.baseline:
        print(baseline(result, 1), end="")
    else:
        report, warnings = comparison(result)
        print(report, end="")
        if args.annotations:
            for warning in warnings:
                print("::warning title=Benchmark change::" + annotation(warning), file=sys.stderr)


if __name__ == "__main__":
    main()
