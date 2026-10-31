#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check the memory soak's schedule and classifiers with injected state.

Opt-in like the soak itself: run by hand, not by `make test`.
"""
import importlib.util
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("soak", ROOT / "scripts/bench-memory-soak.py")
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)

HEAP = 402653184


def started(pid, heap=HEAP):
    return f"BENCH server 1 worker_heap_bytes {heap}\nBENCH server 1 worker_pid {pid}\n"


def analysis(analyses, uid=20000, live=177088, limit=HEAP // 8):
    return (f"BENCH server 1 worker_analyses {analyses}\nBENCH server 1 worker_uid_next {uid}\n"
            f"BENCH server 1 worker_live_bytes {live}\nBENCH server 1 worker_live_limit_bytes {limit}\n")


def admission(retained, incoming, latest, pinned=0, protected=None, outcome=1, evicted=(), own=None, after=None):
    lines = [f"BENCH server 0 admit_replaces 1", f"BENCH server 0 admit_incoming_bytes {incoming}",
             f"BENCH server 0 admit_retained_bytes {retained}", f"BENCH server 0 admit_latest_bytes {latest}",
             f"BENCH server 0 admit_pinned_bytes {pinned}",
             f"BENCH server 0 admit_protected_bytes {latest + pinned if protected is None else protected}"]
    if own is not None:
        lines.append(f"BENCH server 0 admit_own_freed_bytes {own}")
    lines += [f"BENCH server 0 admit_evicted_bytes {e}" for e in evicted]
    lines.append(f"BENCH server 0 admit_outcome {outcome}")
    if after is not None:
        lines.append(f"BENCH server 0 admit_after_bytes {after}")
    return "\n".join(lines) + "\n"


class ScheduleTest(unittest.TestCase):
    def test_full_plan_counts(self):
        counts = soak.expected_counts(soak.plan(False))
        self.assertEqual(counts, {"measured.analyze": 240, "measured.temporary": 80, "measured.reopen": 8,
                                  "measured.break": 4, "measured.descriptors": 2,
                                  "warmup.analyze": 6, "warmup.temporary": 2})

    def test_cycle_order_and_rotation(self):
        self.assertEqual(soak.schedule(-1), [("analyze", "first"), ("analyze", "second"),
                                             ("analyze", "third"), ("temporary", "third")])
        self.assertEqual(soak.schedule(40)[4:], [("reopen", "first"), ("break", "second"),
                                                 ("descriptors", "first")])
        self.assertEqual([soak.schedule(n)[4] for n in (10, 20, 30)],
                         [("reopen", "first"), ("reopen", "second"), ("reopen", "third")])
        self.assertEqual(soak.schedule(80)[-1], ("descriptors", "second"))

    def test_smoke_exercises_every_action_without_warmups(self):
        cycles = soak.plan(True)
        self.assertEqual(len(cycles), 2)
        self.assertFalse(any(warmup for _, warmup in cycles))
        kinds = {kind for n, _ in cycles for kind, _ in soak.schedule(n)}
        self.assertEqual(kinds, {"analyze", "temporary", "reopen", "break", "descriptors"})


class RetirementTest(unittest.TestCase):
    def classify(self, text):
        workers = soak.Workers()
        # Feeding in arbitrary whole-line pieces must not change the result.
        lines = text.splitlines(keepends=True)
        for i in range(0, len(lines), 3):
            workers.feed("".join(lines[i:i + 3]))
        return workers

    def test_count_retirement(self):
        text = started(10) + "".join(analysis(n) for n in range(1, 101))
        text += "virgil-lsp: replacing analysis worker 10 after 100 analyses (limit 100)\n"
        text += started(11) + analysis(1)
        workers = self.classify(text)
        self.assertEqual(workers.findings, [])
        self.assertEqual(workers.summary()["reasons"], {"count": 1, "live at end": 1})
        self.assertEqual(workers.workers[0]["analyses"], 100)

    def test_uid_and_heap_eighth_retirements(self):
        text = started(10) + analysis(3, uid=1 << 28)
        text += f"virgil-lsp: replacing analysis worker 10 because its UID counter reached {1 << 28} (limit {1 << 28})\n"
        text += started(11) + analysis(1, live=HEAP // 8)
        text += f"virgil-lsp: replacing analysis worker 11 because its live heap reached {HEAP // 8} bytes (limit {HEAP // 8})\n"
        workers = self.classify(text)
        self.assertEqual(workers.findings, [])
        self.assertEqual([w["ended"]["reason"] for w in workers.workers], ["uid", "live"])

    def test_count_takes_precedence_over_live(self):
        text = started(10) + analysis(100, live=HEAP)
        text += f"virgil-lsp: replacing analysis worker 10 because its live heap reached {HEAP} bytes (limit {HEAP // 8})\n"
        workers = self.classify(text)
        self.assertEqual([f["detail"] for f in workers.findings], ["misclassified retirement"])
        self.assertEqual(workers.findings[0]["predicted"], "count")

    def test_missed_retirement_and_wrong_live_limit(self):
        workers = self.classify(started(10) + analysis(100) + analysis(101, limit=HEAP // 4))
        self.assertEqual([f["detail"] for f in workers.findings],
                         ["a limit was reached without retirement", "live limit is not an eighth of the heap"])
        # The last one may yet retire in the next piece of the trace, until it ends.
        workers.end()
        self.assertEqual(len(workers.findings), 3)
        workers = self.classify(started(10) + analysis(100) + started(11))
        self.assertIn("a limit was reached without retirement", [f["detail"] for f in workers.findings])

    def test_failure_and_unknown_worker(self):
        text = started(10) + "virgil-lsp: analysis worker 10 crashed during an analysis; it ended with signal 9\n"
        text += "virgil-lsp: replacing analysis worker 99 after 100 analyses (limit 100)\n"
        workers = self.classify(text)
        self.assertEqual(workers.workers[0]["ended"]["reason"], "failed")
        self.assertEqual([f["detail"] for f in workers.findings], ["retirement of an unknown worker"])


class AdmissionTest(unittest.TestCase):
    MIB = 1024 * 1024

    def test_eviction_within_budget(self):
        record, = soak.admissions(admission(26 * self.MIB, 16 * self.MIB, 13 * self.MIB, outcome=2,
                                            evicted=[13 * self.MIB], after=13 * self.MIB))
        self.assertEqual(soak.admission_findings(record), [])
        self.assertIsNone(soak.refusal_cause(record))

    def test_pinned_refusal_is_classified_separately(self):
        # The latest and a rename's pinned original leave no room; without the pin it would fit.
        record, = soak.admissions(admission(26 * self.MIB, 16 * self.MIB, 13 * self.MIB, pinned=13 * self.MIB,
                                            outcome=0))
        self.assertEqual(soak.admission_findings(record), [])
        self.assertEqual(soak.refusal_cause(record), "pinned")
        record, = soak.admissions(admission(20 * self.MIB, 17 * self.MIB, 17 * self.MIB, outcome=0))
        self.assertEqual(soak.refusal_cause(record), "protected")

    def test_contract_violations(self):
        over, = soak.admissions(admission(20 * self.MIB, 16 * self.MIB, 13 * self.MIB))
        self.assertEqual(soak.admission_findings(over), ["admitted without room"])
        freed_protected, = soak.admissions(admission(26 * self.MIB, 16 * self.MIB, 13 * self.MIB, outcome=2,
                                                     evicted=[14 * self.MIB], after=12 * self.MIB))
        self.assertIn("a protected snapshot was freed", soak.admission_findings(freed_protected))
        refused, = soak.admissions(admission(26 * self.MIB, 16 * self.MIB, 13 * self.MIB, outcome=0))
        self.assertEqual(soak.admission_findings(refused), ["refused although freeing snapshots would make room"])
        retained, = soak.admissions(admission(33 * self.MIB, 1, 13 * self.MIB, outcome=2,
                                              evicted=[20 * self.MIB], after=13 * self.MIB))
        self.assertEqual(soak.admission_findings(retained), ["retained snapshot charge exceeds the budget"])

    def test_text_cache(self):
        text = ("BENCH server 0 text_cache_offered_bytes 3200000\nBENCH server 0 text_cache_evicted_bytes 3100000\n"
                "BENCH server 0 text_cache_bytes 4150000\nBENCH worker 7 live_after_gc_bytes 1\n")
        record, = soak.text_cache(text)
        self.assertEqual(record, {"offered_bytes": 3200000, "evicted_bytes": 3100000, "bytes": 4150000})
        self.assertEqual(soak.text_cache_findings(record), [])
        self.assertEqual(soak.text_cache_findings({**record, "bytes": soak.TEXT_CACHE_BYTES + 1}),
                         ["parseable-text charge exceeds its cap"])


class ObservationTest(unittest.TestCase):
    def test_stale_and_future_versions(self):
        p = soak.Publications()
        p.opened("file:///a", 1)
        p.changed("file:///a", 2)
        p.receive({"uri": "file:///a", "version": 2, "diagnostics": [{"code": "X"}]})
        p.confirm("file:///a", 2)
        p.receive({"uri": "file:///a", "version": 1, "diagnostics": [{"code": "X"}]})
        p.receive({"uri": "file:///a", "version": 3, "diagnostics": [{"code": "X"}]})
        # Empty, unversioned, and closed-document publications are not answers.
        p.receive({"uri": "file:///a", "version": 1, "diagnostics": []})
        p.receive({"uri": "file:///b", "version": 9, "diagnostics": [{"code": "X"}]})
        self.assertEqual([(f["version"], f["floor"], f["sent"]) for f in p.findings], [(1, 2, 2), (3, 2, 2)])
        p.closed("file:///a")
        p.opened("file:///a", 1)
        p.receive({"uri": "file:///a", "version": 1, "diagnostics": [{"code": "X"}]})
        self.assertEqual(len(p.findings), 2, "a reopened document starts a new lifetime")

    def test_binding_detection(self):
        oracle = {"stable": {"uri": "file:///w/first/Observer.v3", "range": 1}}
        self.assertEqual(soak.binding_findings(dict(oracle), oracle), [])
        foreign = {"stable": {"uri": "file:///w/second/Observer.v3", "range": 1}}
        self.assertEqual(soak.binding_findings(foreign, oracle)[0]["answer"], foreign["stable"])
        self.assertEqual(soak.binding_findings({}, oracle)[0]["answer"], None)

    def test_missing_worker_peaks_are_unknown(self):
        reads = iter([{1: ("server", 10, 20), 2: ("worker", 30, 40)}, {1: ("server", 11, 21)},
                      {1: ("server", 12, 22), 3: ("worker", 31, 41)}])
        sampler = soak.bench.ResourceSampler(1, read=lambda: next(reads), clock=lambda: 0)
        for _ in range(3):
            sampler.sample()
        # Three workers started; the second was never sampled.
        peaks = soak.peaks(sampler.stop(worker_starts=3))
        self.assertEqual(peaks["unobserved_workers"], 1)
        self.assertEqual([(r["pid"], r["hwm_bytes"]) for r in peaks["processes"]], [(1, 22), (2, 40), (3, 41)])
        self.assertEqual(peaks["max_sampled_worker_hwm_bytes"], 41)
        self.assertIsNone(soak.peaks(sampler.stop())["unobserved_workers"], "unknown starts are not zero")

    def test_trend(self):
        result = soak.trend([(n, 100 + 2 * n) for n in range(1, 21)] + [(21, None)])
        self.assertEqual(result["n"], 20)
        self.assertAlmostEqual(result["slope_per_cycle"], 2)
        self.assertEqual((result["first10_median"], result["last10_median"]), (111, 131))


if __name__ == "__main__":
    unittest.main()
