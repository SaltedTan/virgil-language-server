# Proposal — not an adopted release gate

This performance-budget proposal records measured
baselines and candidate targets for the roadmap's requirement that published
performance budgets pass in CI or scheduled runs. The maintainer must
separately adopt the numbers, the reference environments, and the enforcement
policy. Nothing in this document is an adopted budget, a release gate, or a
guarantee.

| Item | Status |
| --- | --- |
| Adoption | Not adopted |
| Adoption date | None |
| Authority | The maintainer, after the evidence below is reviewed |

## Evidence

The captures below ran on 2026-11-01 (UTC) on the reference host of the
[reference baseline](benchmarks.md#reference-baseline): an Intel Core
i7-14700KF (28 logical CPUs), Linux `7.0.0-42-generic` x86-64/glibc 2.43,
Python 3.14.4, pinned with `taskset` to CPU 12 (governor `powersave`). The
executables came from `make bench-build` at source revision
`bf9e70a8ba3bd272330c48c7b55df40d6812c308`
(`v0.1.0-alpha.5-50-gbf9e70a8ba3b`), with pinned Virgil
`57318d37e9fcca8de6fda5f9cd82d669479f49ff`, the default 384 MiB worker heap
(402,653,184 bytes), and the server's default 200 MiB heap. Binary SHA-256
hashes: server `69c6fccce0b34f9793cb03bd23c72685126054d5d7b2c6ae85aa2b97b9f256b3`
(972,712 bytes), worker `6a69593f6ec39397050c39a80f0f556db97478319f3dad86f31ba511085cc561`
(916,792 bytes). The corpus manifest SHA-256 was
`79ff4b89d9b18251202dadefd53cef96f2089c7949c59d52400495113e001c15`, the same
pin the [first-semantic capture](benchmarks.md#first-semantic-diagnostics)
recorded.

| Capture | Command | Raw JSON |
| --- | --- | --- |
| Full scenarios | `python3 scripts/bench-lsp.py --exe build/bench/virgil-lsp --blocks 6 --operations 5 --warmups 2 --cpu 12 --require-probes --output budgets.json` | `budgets.json` |
| First semantic diagnostics | `python3 scripts/bench-lsp.py --exe build/bench/virgil-lsp --first-semantic --blocks 6 --operations 5 --warmups 2 --cpu 12 --require-probes --output first-semantic.json` | `first-semantic.json` |
| Error-result workload | `python3 scripts/bench-lsp.py --exe build/bench/virgil-lsp --project aeneas --error-result --require-probes --cpu 12 --blocks 6 --operations 5 --warmups 2 --output error-result.json` | `error-result.json` |
| Memory soak | `python3 scripts/bench-memory-soak.py --exe build/bench/virgil-lsp --cpu 12 --work-dir soak-work --output soak.json` | `soak.json` |

The three `bench-lsp.py` captures ran under the shared benchmark
lock, each with six process blocks, two warmups, and five measured
operations per scenario per block, so that every latency cell below
summarizes 30 measured operations. The soak ran 80 measured cycles after two
warmup cycles. The raw JSON files, the build log, and the binary and corpus
hashes are retained in the scratch directory
`/home/sean-tan/.t3/scratch-virgil/budgets-refresh/`. Every capture passed
its correctness checks; no sample was removed and no capture was rerun.

The [memory soak record in `docs/benchmarks.md`](benchmarks.md#memory-soak)
was measured at revision `500b9f53` with different server and worker binaries,
so it does not match this revision, build, and corpus. The soak was rerun
with the bounded tool above; its results follow.

## Changes since the 2026-10-31 capture

The first version of this proposal was measured on 2026-10-31 at
`85baf49de43acb77f372d0e477d204af5a4c4423`, with the same commands, host,
CPU, corpus, and pinned Virgil; its raw data is retained in
`/home/sean-tan/.t3/scratch-virgil/perf-budgets-345/`. Between the two
revisions, the server's source changed in #411 (completion admission beside
two large projects), #412 (parse-work probes, which only benchmark builds such as these compile in), #416 (semantic-token
replies), #417 (the position map), #418 (reading a closed project file),
#421 (declaration comment spans in the analysis result), and #425 (sibling
recovery in queries). Two conditions differed: other work kept the host's
one-minute load average between 1.3 and 2.3 when the three `bench-lsp.py`
captures started, against 0.5 to 1.1 before; and the soak's work directory
path is one character shorter, which changes its charges, since they include
paths.

A latency p50 counts as moved when the two captures' p10–p90 intervals are
disjoint, the test the [scheduled
comparison](benchmarks.md#scheduled-comparison) uses. A p95 counts as moved
when the two captures' p90-to-maximum ranges are disjoint and it changed by
more than 10%, because the p95 of 30 samples rests on its second- and
third-slowest samples. A
memory value counts as moved when it changed by more than its spread across
sessions or workers in either capture, and a frame length or charge when it
changed at all. The cells that moved, with the likely cause; no cause was
ablated:

| Cell | 2026-10-31 | 2026-11-01 | Likely cause |
| --- | ---: | ---: | --- |
| Aeneas `completion_miss` p50 / p95 | 134.286 / 151.377 ms | 113.298 / 119.143 ms | #418: capture's p50 fell from 11.766 to 5.491 ms. #421: the worker allocated 145,642,312 bytes instead of 163,177,480 for this query, and its verification p50 fell from 47.861 to 37.779 ms. |
| Aeneas `completion_miss`, error-result capture, p50 / p95 | 137.286 / 152.013 ms | 113.547 / 120.819 ms | As above. |
| Aeneas `completion_hit` p95 | 6.602 ms | 33.564 ms | Not identified. In both Aeneas captures, the fourth measured operation of every block took 29.6 to 35.3 ms, and the others at most 11.9 ms. A cached reply doesn't reach the worker, so a server collection at a fixed point in each process's allocation is the likeliest cause; it wasn't measured. |
| Aeneas `hover` p50 | 0.386 ms | 0.173 ms | #421: hover no longer parses the declaration's file for its comment. |
| Aeneas `prepareRename` p95 | 40.087 ms | 23.534 ms | Not identified. |
| Aeneas `definition` p95 | 3.768 ms | 0.220 ms | Not identified. The same build's error-result capture recorded 3.546 ms, so this tail comes and goes between runs. |
| Aeneas `error_analysis` p50 | 300.622 ms | 320.947 ms | #421, and #412's probe: worker parse p50 rose from 42.685 to 49.531 ms, and the probe's classification, outside it, took 1.183 ms. Capture rose from 24.923 to 31.186 ms, and the worker's collection after sending from 38.216 to 55.217 ms. |
| util `fresh_analysis` p50 / p95 | 14.349 / 14.582 ms | 15.847 / 16.255 ms | #421, and #412's probe: worker parse p50 rose from 3.414 to 4.155 ms. |
| util `definition` p50 | 0.093 ms | 0.083 ms | #417, which builds a smaller position map for every request. |
| util `hover` p50 / p95 | 0.089 / 0.096 ms | 0.079 / 0.083 ms | #417, as above. |
| util `prepareRename` p50 / p95 | 1.627 / 2.816 ms | 1.307 / 2.297 ms | Not identified; #417 is the likeliest. |
| small `completion_miss` p95 | 0.736 ms | 1.890 ms | Two samples, 2.652 and 4.387 ms, both in the fourth block, with no collection recorded; the other 28 took at most 0.958 ms. Likely the host's load. |
| util first semantic, config first, p50 | 29.310 ms | 30.010 ms | #421, and #412's probe, through the worker's parse. |
| util first semantic, source first, p50 | 15.820 ms | 16.221 ms | As above. |
| Aeneas RESULT frame, fresh, reused, and rename analyses | 4,379,197 bytes | 4,384,791 bytes | #421: 5,594 bytes of comment spans. |
| Aeneas RESULT frame, semantic diagnostic | 4,379,432 bytes | 4,385,026 bytes | #421, as above. |
| Aeneas RESULT frame, `error_analysis` | 4,510,801 bytes | 4,516,395 bytes | #421, as above. |
| Aeneas RESULT frame, `completion_miss` | 61,408 bytes | 62,858 bytes | #421. |
| Soak worker HWM, four workers | 193,126,400 to 193,134,592 bytes | 194,048,000 to 194,064,384 bytes | #421: a fresh Aeneas analysis leaves 913,176 more bytes in the worker's heap after handling, by the full capture's p50. |
| Soak worker live bytes after collection | 177,088 bytes | 180,960 bytes | #412's probe: its record of the files each parse reached. |
| Soak largest sum of RSS in one pass | 403,738,624 bytes | 404,656,128 bytes | The worker's HWM above. |

The Aeneas worker's HWM in the first-semantic and error-result captures rose
by 921,600 and 946,176 bytes, which agrees with the soak but is within those
captures' spread across sessions of about 4 MB. The largest server HWM in
the first-semantic capture fell for small from 32,960,512 to 1,097,728
bytes; the old value came from one session, and every other session of both
captures stayed under 1.2 MB. The soak's snapshot and text-cache charges fell
by 268 to 894 bytes, which the shorter work directory explains: the corpus
captures, whose paths didn't change, charged the same 13,123,309-byte
snapshot as before. The soak's server RSS no longer stayed at its first
value: it rose by 163,840 bytes by cycle 12 and then stayed flat to cycle 80,
which gives the nonzero slope below. Every other cell moved by less than its
noise, including Aeneas `fresh_analysis`, `reused_analysis`, `rename`, and
first-semantic rows. The worker's collection after sending an Aeneas
analysis took 50 to 55 ms at p50, against 38 to 40 ms before; that is longer
than the 3.3 to 7.4 ms that #421's measurement attributed to it, and these
captures don't explain the difference.

Eleven proposed latency targets changed, each by the unchanged rule:

- Raised by a slower measurement: Aeneas `reused_analysis` p50 from 375 to
  400 ms (p50 309.889 ms against 292.074), `error_analysis` from 400/500 to
  450/550 ms, util first semantic source first p50 from 20 to 25 ms (16.221
  × 1.25 is just over 20).
- Raised by a new tail: Aeneas `completion_hit` p95 from 10 to 75 ms, small
  `completion_miss` p95 from 2 to 3 ms, and Aeneas `syntax_diagnostic` p95
  from 3 to 5 ms, whose p95 rose from 1.555 to 2.801 ms while its p50 and
  p90 didn't move.
- Lowered by a faster or tighter measurement: Aeneas `definition` p95 from 10
  to 1 ms, Aeneas `prepareRename` p95 from 75 to 40 ms, util `prepareRename`
  p50 from 3 to 2 ms, util `completion_hit` p50 from 2 to 1 ms, and small
  `completion_hit` p95 from 2 to 1 ms.

One lowered target rests on a tail that comes and goes: the Aeneas
`definition` p95 target of 1 ms is below the 3.546 ms the same build recorded
in its error-result capture, which has no target for that row. Two raised
targets rest on tails of a few samples: small `completion_miss` on one block's
two samples, and Aeneas `syntax_diagnostic` on its slowest three. The Aeneas
`completion_hit` p95 target of 75 ms now exceeds the issue's starting 50 ms.
The memory targets did not change: the largest measured values moved by less
than 1 MiB.

## Measured baselines

Every latency cell is in milliseconds over 30 measured operations, with p50,
p95, p10, p90, and the median absolute deviation (MAD). The definitions of
the clocks, percentiles, sample counts, and correctness barriers are
[below](#definitions).

### Full scenarios, `budgets.json`

From `budgets.json`, the `summary` rows with `metric: latency_ms`.

| Scenario | small p50 | small p95 | small p10 | small p90 | small MAD | util p50 | util p95 | util p10 | util p90 | util MAD | Aeneas p50 | Aeneas p95 | Aeneas p10 | Aeneas p90 | Aeneas MAD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 0.453 | 0.480 | 0.446 | 0.474 | 0.007 | 15.847 | 16.255 | 15.324 | 16.151 | 0.142 | 353.998 | 361.650 | 349.797 | 359.792 | 2.628 |
| `reused_analysis` | 0.171 | 0.194 | 0.157 | 0.187 | 0.010 | 13.792 | 15.148 | 12.583 | 14.993 | 0.562 | 309.889 | 332.225 | 299.125 | 331.935 | 10.729 |
| `definition` | 0.057 | 0.061 | 0.055 | 0.060 | 0.001 | 0.083 | 0.089 | 0.080 | 0.087 | 0.002 | 0.180 | 0.220 | 0.157 | 0.211 | 0.018 |
| `hover` | 0.053 | 0.056 | 0.051 | 0.055 | 0.002 | 0.079 | 0.083 | 0.075 | 0.082 | 0.002 | 0.173 | 0.196 | 0.139 | 0.191 | 0.023 |
| `references` | 0.058 | 0.060 | 0.055 | 0.059 | 0.001 | 0.117 | 0.126 | 0.113 | 0.124 | 0.002 | 0.808 | 4.242 | 0.770 | 4.219 | 0.028 |
| `prepareRename` | 0.158 | 0.175 | 0.154 | 0.170 | 0.003 | 1.307 | 2.297 | 1.279 | 1.463 | 0.023 | 20.492 | 23.534 | 11.159 | 23.399 | 1.343 |
| `rename` | 0.470 | 0.499 | 0.461 | 0.495 | 0.008 | 16.393 | 17.159 | 16.007 | 17.021 | 0.224 | 340.288 | 347.888 | 320.960 | 346.622 | 6.534 |
| `completion_miss` | 0.726 | 1.890 | 0.687 | 0.853 | 0.026 | 7.940 | 8.508 | 7.334 | 8.300 | 0.207 | 113.298 | 119.143 | 109.021 | 117.190 | 2.324 |
| `completion_hit` | 0.456 | 0.506 | 0.437 | 0.491 | 0.017 | 0.736 | 1.560 | 0.711 | 1.271 | 0.023 | 6.192 | 33.564 | 5.040 | 32.099 | 0.751 |
| `syntax_diagnostic` | 0.084 | 0.091 | 0.071 | 0.089 | 0.004 | 0.284 | 0.306 | 0.264 | 0.304 | 0.015 | 0.386 | 2.801 | 0.367 | 1.620 | 0.018 |
| `semantic_diagnostic` | 200.729 | 202.141 | 200.623 | 202.089 | 0.081 | 215.618 | 221.030 | 214.982 | 220.540 | 0.585 | 505.453 | 521.011 | 496.545 | 516.850 | 6.401 |

The Aeneas `references`, `completion_hit`, and `syntax_diagnostic` p95
values (4.242 ms, 33.564 ms, and 2.801 ms) sit far above their p50 values
(0.808 ms, 6.192 ms, and 0.386 ms); the p10 and p90 values (0.770 to
4.219 ms, 5.040 to 32.099 ms, and 0.367 to 1.620 ms) show the tail is a small
number of slow samples, not a shift of the whole distribution.

### First semantic diagnostics, `first-semantic.json`

From `first-semantic.json`, the `summary` rows with `metric: latency_ms` for
the launch interval (process launch to the first exact semantic publication).
Each of the 252 sessions, warmups included, had one worker, every PID was
observed, and no worker was unobserved.

| Project | Order | p50 | p95 | p10 | p90 | MAD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| small | config first | 1.091 | 1.132 | 1.064 | 1.128 | 0.016 |
| small | source first | 0.976 | 0.995 | 0.948 | 0.993 | 0.012 |
| util | config first | 30.010 | 31.828 | 29.782 | 30.460 | 0.133 |
| util | source first | 16.221 | 16.551 | 16.104 | 16.538 | 0.065 |
| Aeneas | config first | 698.004 | 709.197 | 663.382 | 708.627 | 8.798 |
| Aeneas | source first | 373.625 | 385.664 | 359.613 | 379.931 | 4.051 |

### Error-result workload, `error-result.json`

From `error-result.json`, the `summary` rows with `metric: latency_ms`, for
the Aeneas project. Only `error_analysis` runs with the pinned
unresolved-name edit and 900 generated ordinary functions (54,790 extra
source bytes); the other rows use the ordinary workloads.

| Scenario | p50 | p95 | p10 | p90 | MAD |
| --- | ---: | ---: | ---: | ---: | ---: |
| `error_analysis` | 320.947 | 339.061 | 308.392 | 332.960 | 5.263 |
| `fresh_analysis` | 358.579 | 371.950 | 352.852 | 369.057 | 5.733 |
| `reused_analysis` | 313.336 | 330.490 | 301.703 | 329.689 | 8.613 |
| `semantic_diagnostic` | 514.005 | 535.177 | 501.188 | 528.146 | 6.068 |
| `completion_miss` | 113.547 | 120.819 | 109.539 | 117.915 | 2.521 |
| `rename` | 340.252 | 364.269 | 324.253 | 349.638 | 8.101 |

### Memory, per-PID and simultaneous

From the `resource_summary` of each session's process records in
`first-semantic.json` and `error-result.json`: the largest sampled RSS and
the OS high-water mark (HWM) over every observed server and worker PID, and
the largest sum of RSS within one 50 ms sampling pass.

| Capture, project | Server HWM | Worker HWM | Largest simultaneous total |
| --- | ---: | ---: | ---: |
| `first-semantic.json`, small | 1,097,728 bytes | 933,888 bytes | 2,027,520 bytes |
| `first-semantic.json`, util | 4,268,032 bytes | 11,284,480 bytes | 15,552,512 bytes |
| `first-semantic.json`, Aeneas | 53,456,896 bytes | 196,927,488 bytes | 250,380,288 bytes |
| `error-result.json`, Aeneas | 210,591,744 bytes | 197,857,280 bytes | 408,449,024 bytes |

The independent server and worker maxima are not a simultaneous peak; the
simultaneous column is the only sum of two processes' RSS in one pass.

### Memory soak, `soak.json`

The rerun of the [memory soak](benchmarks.md#memory-soak) ran one server
lifetime through two warmup cycles and 80 measured cycles over three physical
copies of the Aeneas sources (fixture tree hash
`30913a056ae8ab025a596973654a7eadf6825f8e7a975dd410cbef4e044b79f9`, the same
tree as the recorded soak), in 107.1 seconds with 0 findings. The worker
completed 381 analyses (240 measured main), four workers ran, three were
retired after 100 analyses each, and every PID was observed over 2,205
sampler passes with 0 unobserved workers.

| Bound | Observed | Limit |
| --- | ---: | ---: |
| Parseable-text charge, 299 completions | at most 4,155,623 bytes | 4,194,304 bytes |
| Contexts' parseable texts together | 9,611,407 bytes, evictions in 271 completions | more than 4,194,304 bytes |
| Retained snapshot charge, 381 admissions | at most 26,268,564 bytes | 33,554,432 bytes |
| Retained charge and reservation after admission | at most 30,017,837 bytes | 33,554,432 bytes |
| Server RSS and HWM | 210,591,744 bytes, reached by cycle 12 and flat to cycle 80 | |
| Worker HWM, four workers | 194,048,000 to 194,064,384 bytes | |
| Largest sum of RSS in one pass | 404,656,128 bytes | |
| Worker live bytes after collection | 180,960 bytes, flat | 50,331,648 bytes (an eighth of the heap) |

The server's settled RSS was 210,427,904 bytes after cycle 1 and rose by
163,840 bytes in two steps, at cycles 4 and 12, then stayed flat for the
remaining 68 cycles; that gives a slope of 1,432.2 bytes per cycle, which
describes the early steps, not continuing growth. The worker's slope was
-58.5 bytes per cycle, the difference between workers, not growth within
one. These are this schedule's values, not a proof that nothing leaks.

## Supported envelope

The corpus pins the source density each cell measures: small is 2 files,
441 bytes, and 20 lines; util is 31 files, 166,628 bytes, and 5,360 lines;
Aeneas is 206 files, 3,072,972 bytes, and 84,585 lines. A project's program
may select at most 1,024 files and 4 MiB of paths and source. The Aeneas
corpus analysis encodes a RESULT frame of 4,384,791 bytes for fresh,
reused, and rename analyses and 4,385,026 bytes for the semantic
diagnostic, and charges a snapshot of 13,123,309 bytes against the 32 MiB
retained-snapshot budget, from the `result_bytes` and `snapshot_bytes`
probes in `budgets.json` and `error-result.json`. The soak's 207-file
fixture is a different program: its ordinary analyses charged snapshots of
13,132,986 to 13,133,432 bytes, its broken-file and descriptor steps up to
13,135,578 bytes, and it held the parseable-text cache at most 4,155,623
bytes against its 4 MiB limit. The worker's heap is 402,653,184 bytes
(384 MiB) and the server's is 200 MiB. These budgets bound what the server
supports; they are not a universal file or line count guarantee, and no
target below applies to a program outside this envelope.

## Proposed targets

Every target is a p50/p95 pair in milliseconds on the reference host above,
for the corpus and build recorded in [Evidence](#evidence). The margin
policy is: the p50 target is the measured p50 multiplied by 1.25, and the p95
target is the measured p95 multiplied by 1.5, each rounded up to the value
shown. The 25% p50 margin is larger than the ordinary analysis spread the
[scheduled comparison](benchmarks.md#scheduled-comparison) records (6–18% of
the median for fresh/reused analysis), and the 50% p95 margin is larger than
the 25% median-change threshold used by that workflow. Its warning also
requires disjoint p10–p90 intervals; an absolute p50 or p95 target failure
does not imply that it would flag a comparison. Thirty measured operations give a rough p95, not
an SLA; the p10, p90, and MAD columns in [Measured baselines](#measured-baselines)
stay the record of the spread each target covers.

Targets are Linux reference-host values. The macOS/Rosetta cells are
[unmeasured](#unmeasured-cells), so there is no macOS target.

### small

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 0.453 ms | 0.480 ms | 1 ms | 1 ms | 0.547 ms | 0.520 ms |
| `reused_analysis` | 0.171 ms | 0.194 ms | 1 ms | 1 ms | 0.829 ms | 0.806 ms |
| `definition` | 0.057 ms | 0.061 ms | 1 ms | 1 ms | 0.943 ms | 0.939 ms |
| `hover` | 0.053 ms | 0.056 ms | 1 ms | 1 ms | 0.947 ms | 0.944 ms |
| `references` | 0.058 ms | 0.060 ms | 1 ms | 1 ms | 0.942 ms | 0.940 ms |
| `prepareRename` | 0.158 ms | 0.175 ms | 1 ms | 1 ms | 0.842 ms | 0.825 ms |
| `rename` | 0.470 ms | 0.499 ms | 1 ms | 1 ms | 0.530 ms | 0.501 ms |
| `completion_miss` | 0.726 ms | 1.890 ms | 1 ms | 3 ms | 0.274 ms | 1.110 ms |
| `completion_hit` | 0.456 ms | 0.506 ms | 1 ms | 1 ms | 0.544 ms | 0.494 ms |
| `syntax_diagnostic` | 0.084 ms | 0.091 ms | 1 ms | 1 ms | 0.916 ms | 0.909 ms |
| `semantic_diagnostic` | 200.729 ms | 202.141 ms | 300 ms | 350 ms | 99.271 ms | 147.859 ms |
| first semantic, config first | 1.091 ms | 1.132 ms | 2 ms | 2 ms | 0.909 ms | 0.868 ms |
| first semantic, source first | 0.976 ms | 0.995 ms | 2 ms | 2 ms | 1.024 ms | 1.005 ms |

### util

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 15.847 ms | 16.255 ms | 20 ms | 25 ms | 4.153 ms | 8.745 ms |
| `reused_analysis` | 13.792 ms | 15.148 ms | 20 ms | 25 ms | 6.208 ms | 9.852 ms |
| `definition` | 0.083 ms | 0.089 ms | 1 ms | 1 ms | 0.917 ms | 0.911 ms |
| `hover` | 0.079 ms | 0.083 ms | 1 ms | 1 ms | 0.921 ms | 0.917 ms |
| `references` | 0.117 ms | 0.126 ms | 1 ms | 1 ms | 0.883 ms | 0.874 ms |
| `prepareRename` | 1.307 ms | 2.297 ms | 2 ms | 5 ms | 0.693 ms | 2.703 ms |
| `rename` | 16.393 ms | 17.159 ms | 25 ms | 30 ms | 8.607 ms | 12.841 ms |
| `completion_miss` | 7.940 ms | 8.508 ms | 10 ms | 15 ms | 2.060 ms | 6.492 ms |
| `completion_hit` | 0.736 ms | 1.560 ms | 1 ms | 3 ms | 0.264 ms | 1.440 ms |
| `syntax_diagnostic` | 0.284 ms | 0.306 ms | 1 ms | 1 ms | 0.716 ms | 0.694 ms |
| `semantic_diagnostic` | 215.618 ms | 221.030 ms | 300 ms | 350 ms | 84.382 ms | 128.970 ms |
| first semantic, config first | 30.010 ms | 31.828 ms | 40 ms | 50 ms | 9.990 ms | 18.172 ms |
| first semantic, source first | 16.221 ms | 16.551 ms | 25 ms | 25 ms | 8.779 ms | 8.449 ms |

### Aeneas

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 353.998 ms | 361.650 ms | 450 ms | 550 ms | 96.002 ms | 188.350 ms |
| `reused_analysis` | 309.889 ms | 332.225 ms | 400 ms | 500 ms | 90.111 ms | 167.775 ms |
| `definition` | 0.180 ms | 0.220 ms | 1 ms | 1 ms | 0.820 ms | 0.780 ms |
| `hover` | 0.173 ms | 0.196 ms | 1 ms | 1 ms | 0.827 ms | 0.804 ms |
| `references` | 0.808 ms | 4.242 ms | 2 ms | 10 ms | 1.192 ms | 5.758 ms |
| `prepareRename` | 20.492 ms | 23.534 ms | 30 ms | 40 ms | 9.508 ms | 16.466 ms |
| `rename` | 340.288 ms | 347.888 ms | 450 ms | 550 ms | 109.712 ms | 202.112 ms |
| `completion_miss` | 113.298 ms | 119.143 ms | 175 ms | 250 ms | 61.702 ms | 130.857 ms |
| `completion_hit` | 6.192 ms | 33.564 ms | 10 ms | 75 ms | 3.808 ms | 41.436 ms |
| `syntax_diagnostic` | 0.386 ms | 2.801 ms | 1 ms | 5 ms | 0.614 ms | 2.199 ms |
| `semantic_diagnostic` | 505.453 ms | 521.011 ms | 650 ms | 800 ms | 144.547 ms | 278.989 ms |
| `error_analysis` | 320.947 ms | 339.061 ms | 450 ms | 550 ms | 129.053 ms | 210.939 ms |
| first semantic, config first | 698.004 ms | 709.197 ms | 900 ms | 1,100 ms | 201.996 ms | 390.803 ms |
| first semantic, source first | 373.625 ms | 385.664 ms | 500 ms | 600 ms | 126.375 ms | 214.336 ms |

The issue's starting numbers for Aeneas, measured at an older revision,
compare as follows: hover/definition 5/25 ms covers the measured 0.173/0.196
ms and 0.180/0.220 ms; completion miss 150/250 ms covers 113.298/119.143 ms
with 36.702 ms and 130.857 ms of margin; completion hit 10/50 ms covers
6.192/33.564 ms with 3.808 ms and 16.436 ms of margin; reused analysis
350/500 ms covers 309.889/332.225 ms with 40.111 ms and 167.775 ms of margin;
publication 600/800 ms covers 505.453/521.011 ms with 94.547 ms and
278.989 ms of margin; first-semantic config-first 750/1,000 ms covers
698.004/709.197 ms with 51.996 ms and 290.803 ms of margin. The policy's
Aeneas rows match the issue's starting rows where they coincide: completion
hit p50 at 10 ms, completion miss p95 at 250 ms, reused analysis p95 at
500 ms, and publication p95 at 800 ms. The policy's completion hit p95,
75 ms, is above the issue's 50 ms.

### Memory targets

The measured maxima over this revision's captures are: server HWM
210,591,744 bytes (200.8 MiB) in both the error-result capture and the soak;
worker HWM 197,857,280 bytes (188.7 MiB) in the error-result capture and
194,064,384 bytes (185.1 MiB) in the soak; largest simultaneous total
408,449,024 bytes (389.5 MiB) in the error-result capture and 404,656,128
bytes (385.9 MiB) in the soak. The proposed targets are:

| Quantity | Measured maximum | Target | Margin |
| --- | ---: | ---: | ---: |
| Server HWM | 210,591,744 bytes | 256 MiB (268,435,456 bytes) | 57,843,712 bytes (27.5%) |
| Worker HWM | 197,857,280 bytes | 256 MiB (268,435,456 bytes) | 70,578,176 bytes (35.7%) |
| Largest simultaneous total | 408,449,024 bytes | 512 MiB (536,870,912 bytes) | 128,421,888 bytes (31.4%) |

The issue's starting proposal of 224/400 MiB for server/worker peak RSS
covers the measured server HWM with 24,289,280 bytes (11.5%) of margin and the
worker HWM with 221,573,120 bytes (112.0%) of margin. This proposal sets both
processes at 256 MiB, which leaves 27.5% and 35.7% of headroom over the
measured maxima, and adds a simultaneous-total target of 512 MiB, because the
independent maxima are not a simultaneous peak.

## Unmeasured cells

- Every macOS/Rosetta cell. No Mac is available for this capture, so no
  macOS measurement exists at this revision, and the cross-platform
  qualification is incomplete. The scheduled
  [macOS comparison](benchmarks.md#scheduled-comparison) continues to record
  runner variability, but it produces no baseline for these targets.
- Full editor launch, from starting the editor to the client's first
  request. The first-semantic capture starts at process launch, and the
  editor's own startup is a separate product boundary the existing tools do
  not measure.
- Continuous typing and general cancellation latency. The
  [typing-burst scenario](benchmarks.md#cancelled-completion-typing-burst)
  measures a fixed burst followed by a pause, not continuous typing, and no
  tool here measures an open-ended typing session.
- Any target for a program outside the [supported envelope](#supported-envelope).
  The corpus pins three programs, and no measurement generalizes beyond
  their file, byte, and line counts.

## Definitions

- **Clocks.** Protocol latencies (`latency_ms`, `open_latency_ms`,
  `config_open_latency_ms`, `initialize_ms`) run on Python's monotonic
  `perf_counter_ns` clock from the client. Internal phase clocks use the
  `System.ticksUs` wall clock. The memory sampler reads `/proc` every
  50 ms. No timed interval contains a sleep or a polling request.
- **Timing boundaries.** Each scenario's timed interval starts and ends as
  the [scenarios and boundaries
  table](benchmarks.md#scenarios-and-boundaries) in `docs/benchmarks.md`
  defines, and the [first-semantic
  section](benchmarks.md#first-semantic-diagnostics) defines the
  first-semantic cells (process launch to the first exact semantic
  publication). The post-edit semantic publication
  (`semantic_diagnostic`) includes the 200 ms debounce, `DEBOUNCE_MS` in
  `src/protocol/LspSemanticDiagnostics.v3`, plus the full project
  analysis.
- **Units.** Latencies are milliseconds; memory is bytes, with MiB
  (1,048,576 bytes) given where used.
- **Percentiles.** Each percentile interpolates linearly between the order
  statistics around position (*n* − 1) × *q* of the sorted samples, so p50
  equals the median. No samples are trimmed.
- **Sample counts.** Every latency cell summarizes 30 measured operations
  (six process blocks × five operations), with two warmups per block
  retained as `warmup: true` and excluded. The soak summarizes 80 measured
  cycles after two warmup cycles.
- **Correctness barriers.** Every measured operation checks its answer
  before the sample is accepted: analyses require the expected diagnostics,
  the exact source list, current document versions, and an advancing
  generation; navigation checks the exact definition location, hover
  signature and range, and every reference; completion requires the named
  semantic item and its detail, so a keyword-only fallback fails; and
  `--require-probes` requires the complete probe transactions. An empty
  parser clearing, a publication for another version, or a fallback answer
  never finishes a timed semantic publication, so no fast reply is counted
  as a successful semantic latency sample.

## Enforcement proposal

The checks split into two kinds.

**Deterministic resource checks** pass or fail without a timing budget and
can run in CI or scheduled runs as-is:

- The corpus pin check, `python3 scripts/bench-corpus.py --check`.
- The per-sample semantic correctness checks the client already enforces,
  with `--require-probes` requiring the complete probe transactions.
- The soak's budget checks: the 4 MiB parseable-text charge, the 32 MiB
  retained-snapshot charge, the live-heap limit of an eighth of the worker
  heap, the retirement classifications, and the binding and version checks.
- The worker collection counts, and the RESULT frame lengths checked
  against a documented bound: the lengths vary within a scenario (small's
  semantic diagnostic records 865 and 868 bytes), so a constant
  length is not a valid check.

**Calibrated absolute reference-run checks** compare p50/p95 latency and RSS
against the targets above. They are meaningful only on the reference host
(i7-14700KF, CPU 12, pinned, warm file caches, the recorded build and
corpus), because source and path layout, the governor, and the file cache
move the numbers. They need a recorded baseline and a re-baseline when the
build, corpus, or host changes.

The hosted timing A/B of the [weekly scheduled
comparison](benchmarks.md#scheduled-comparison) stays advisory: its 25%
change and disjoint-interval heuristic produces warning annotations, never a
failed timing check, and no evidence here supports making it a gate.

No check is enabled by this document, and no heap, workflow, or test setting
changes with it.

### Failure and retest procedure

A target failure on the reference host is not a regression until a second
run with the same settings reproduces it. A large spread or a moving median
calls for another run, not a claimed regression or win. A retest reruns the
failing capture under the shared benchmark lock with the same command,
build, and corpus, and compares the new p50, p95, and MAD against the
recorded ones before any conclusion.

### Baseline-change procedure

An approved baseline change requires a recorded decision by the maintainer,
a new capture with the same settings at the new revision, and an update to
this document's evidence and baseline tables. A follow-up adoption change
requires that recorded decision, and changes to workflows, dependencies, or
suite-wide test settings require a human merge.

## Adoption decision table

These questions are for the maintainer after the evidence above is reviewed.
Their absence does not block this proposal.

| Question | Proposed value | Status |
| --- | --- | --- |
| Exact numeric rows | The target tables above, at revision `bf9e70a8ba3bd272330c48c7b55df40d6812c308` on the i7-14700KF reference host, CPU 12 | Not adopted |
| Platforms | Linux x86-64 only; macOS/Rosetta unmeasured | Not adopted |
| Noise and sample policy | 30 measured operations per cell, p50/p95, no trimming; a second run before any failure is declared | Not adopted |
| Memory envelope | Server 256 MiB HWM, worker 256 MiB HWM, 512 MiB largest simultaneous total | Not adopted |
| Enforcement mode | Advisory; no CI gate enabled | Not adopted |
| Exception and retest authority | The maintainer | Not adopted |
