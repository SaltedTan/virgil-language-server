# Adopted performance budgets

These performance budgets record measured
baselines and adopted targets for the roadmap's requirement that published
performance budgets pass in CI or scheduled runs. The maintainer adopted the
numbers, the reference environments, and the enforcement policy on
2026-11-03 ([ADR-0016](decisions/0016-performance-budgets.md)). Enforcement
is advisory: no CI gate is enabled, and no workflow, test setting, heap, or
code changes with the adoption.

| Item | Status |
| --- | --- |
| Adoption | Adopted |
| Adoption date | 2026-11-03 |
| Authority | The maintainer, per [ADR-0016](decisions/0016-performance-budgets.md) |

## Evidence

The captures below ran on 2026-11-03 (UTC) on the reference host of the
[reference baseline](benchmarks.md#reference-baseline): an Intel Core
i7-14700KF (28 logical CPUs), Linux `7.0.0-42-generic` x86-64/glibc 2.43,
Python 3.14.4, pinned with `taskset` to CPU 12 (governor `powersave`). The
executables came from `make bench-build` at source revision
`139db84582d7f230bd28e3609d2699a200878fe1`
(`v0.1.0-alpha.5-64-g139db84582d7`), with pinned Virgil
`b86f43f102406e94dc2ac4a85909c38ef414e214`, the default 384 MiB worker heap
(402,653,184 bytes), the server's default 200 MiB heap, and the runtime's
collector compiled with the global register allocator
([ADR-0015](decisions/0015-gc-global-register-allocation.md)). Binary SHA-256
hashes: server `e929102493d18bfe01fc26021ece346dbcb2e386149963fca172aa3dc22a08c6`
(983,656 bytes), worker `01ce31782fc21944bafc91f18448bd3737a2a80e2ba8b9ffdbb837781b6979f6`
(927,936 bytes). The corpus manifest SHA-256 was
`b2204f108da69144d4d2769e562082244e78533e8f3c578fdc6cf210cd951145`, the
corpus pinned for Virgil `b86f43f1`.

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
`/home/sean-tan/.t3/scratch-virgil/budgets-refresh2/`. Every retained capture
passed its correctness checks; no sample was removed and the replacement set
was not rerun.
Other work on the host slowed the last four blocks of the error-result
capture, and those blocks set its p95 values below; the [changes
section](#changes-since-the-2026-11-02-capture) describes them.

The [memory soak record in `docs/benchmarks.md`](benchmarks.md#memory-soak)
was measured at revision `500b9f53` with different server and worker binaries,
so it does not match this revision, build, and corpus. The soak was rerun
with the bounded tool above; its results follow.

## Changes since the 2026-11-02 capture

The previous version of this proposal was measured at
`bf9e70a8ba3bd272330c48c7b55df40d6812c308` on 2026-11-02 in the
maintainer's time zone (2026-11-01 in UTC), with the same commands, host, and
CPU; its raw data is retained in
`/home/sean-tan/.t3/scratch-virgil/budgets-refresh/`. Between the two
revisions, the server's source changed in #427 and #438 (bounded parses of
dense documents), #443 (CPU-time and context-switch probes for the worker's
collections, which only benchmark builds such as these compile in), #444 (the
runtime's collector compiled with the global register allocator), and #445
(the Virgil pin moved from `57318d37` to `b86f43f1`, with upstream's unrolled
collector loops and new float parsing in the bundled `lib/util`). #445 also
changed the corpus and the soak's fixture, which copies the Aeneas sources.

The host's one-minute load average was 0.5, 1.0, and 1.0 when the three
`bench-lsp.py` captures started, against 2.3, 1.5, and 1.3 before. The
full-scenario and first-semantic captures were even across their blocks:
Aeneas `fresh_analysis` had block medians of 343.6 to 357.0 ms. The
error-result capture was not. Other work started on the host during it, and
the load average was 10.0 when it ended. Its `error_analysis` block medians
were 296.8 and 297.3 ms in the first two blocks and 332.6 to 452.0 ms in
the last four, and its other analyses slowed in the same blocks, so that
capture's p95 values and the `error_analysis` p95 target rest on a loaded
host. The data is kept, as the procedure requires. An earlier capture of
this revision on the same day, whose full-scenario capture met the same kind
of load, was replaced wholesale by this one, as decided before this one ran;
it is retained in the scratch directory's `contaminated-2026-11-03/`. The
soak also ran after the load rose; its memory values matched the earlier
capture's to within 8,192 bytes.

A latency p50 counts as moved when the two captures' p10–p90 intervals are
disjoint, the test the [scheduled
comparison](benchmarks.md#scheduled-comparison) uses. A p95 counts as moved
when the two captures' p90-to-maximum ranges are disjoint and it changed by
more than 10%, because the p95 of 30 samples rests on its second- and
third-slowest samples. A
memory value counts as moved when it changed by more than its spread across
sessions or workers in either capture, and a frame length, corpus count, or
charge when it changed at all. The cells that moved, with the likely cause;
no cause was ablated:

| Cell | 2026-11-02 | 2026-11-03 | Likely cause |
| --- | ---: | ---: | --- |
| Aeneas `completion_hit` p95 | 33.564 ms | 19.107 ms | #444. The fourth measured hit of every block still meets a server collection, as #433 found, but took 17.0 to 19.4 ms instead of 29.6 to 35.3 ms; the other hits took at most 8.0 ms. The server's collector is compiled with the global register allocator too. The collection itself wasn't timed. |
| Aeneas `prepareRename` p95 | 23.534 ms | 11.819 ms | #444. On one CPU this request waits for the worker's collection after sending ([prepareRename on one CPU](benchmarks.md#preparerename-on-one-cpu-2026-10-31)), which is shorter, below. Its p50 fell from 20.492 to 11.141 ms; the p10–p90 intervals overlap only because six of the old 30 samples took 11.1 to 11.5 ms, which put the old p10 at 11.159 ms. |
| Aeneas `references` p95 | 4.242 ms | 1.167 ms | Not identified: the old capture's slowest few samples. Its p50 didn't move. |
| Aeneas `syntax_diagnostic` p95 | 2.801 ms | 0.388 ms | Not identified: the old capture's slowest three samples. Its p50 didn't move. |
| util `reused_analysis` p95 | 15.148 ms | 13.087 ms | #444 and #445, not separated: the worker's collection after sending fell from 1.311 to 0.256 ms at p50, and the smaller util corpus allocates 9,751,160 bytes instead of 9,983,928. |
| util `rename` p50 | 16.393 ms | 15.041 ms | #444 and #445, as above. |
| util `completion_hit` p95 | 1.560 ms | 0.982 ms | Not identified: the old capture's slowest few samples. Its p50 didn't move. |
| small `fresh_analysis` p95 | 0.480 ms | 0.538 ms | Not identified. Its p50 didn't move. |
| util first semantic, config first, p50 | 30.010 ms | 28.627 ms | #444: this session discards one analysis, and the second waits for the worker's collection after sending the first, which fell from 1.431 to 0.286 ms at p50. |
| Aeneas `error_analysis`, `fresh_analysis`, `reused_analysis`, and `semantic_diagnostic` p95, error-result capture | 339.061, 371.950, 330.490, and 535.177 ms | 457.413, 521.058, 496.516, and 673.821 ms | The host's load in that capture's last four blocks, above. |
| small first semantic, worker HWM / largest simultaneous total | 933,888 / 2,027,520 bytes | 950,272 / 2,064,384 bytes | #443 and #445: the worker executable is 11,144 bytes larger. |
| util corpus | 31 files, 166,628 bytes, 5,360 lines | 33 files, 161,421 bytes, 5,033 lines | #445. |
| Aeneas corpus | 206 files, 3,072,972 bytes, 84,585 lines | 208 files, 3,068,198 bytes, 84,272 lines | #445. |
| Aeneas RESULT frame, fresh, reused, and rename analyses | 4,384,791 bytes | 4,379,351 bytes | #445's smaller corpus. |
| Aeneas RESULT frame, semantic diagnostic | 4,385,026 bytes | 4,379,601 bytes | #445, as above. |
| Aeneas RESULT frame, `error_analysis` | 4,516,395 bytes | 4,510,920 bytes | #445, as above. |
| Aeneas RESULT frame, `completion_miss` | 62,858 bytes | 62,959 bytes | #445, as above. |
| Aeneas snapshot charge | 13,123,309 bytes | 13,113,155 bytes | #445, as above. |
| Soak fixture | 207 files | 209 files | #445. |
| Soak worker HWM, four workers | 194,048,000 to 194,064,384 bytes | 193,957,888 to 193,966,080 bytes | #445's smaller fixture. |
| Soak worker live bytes after collection | 180,960 bytes | 180,816 bytes | #445, as above. |
| Soak largest sum of RSS in one pass | 404,656,128 bytes | 404,566,016 bytes | The worker's HWM above. |
| Soak largest parseable-text charge | 4,155,623 bytes | 4,148,406 bytes | #445's smaller fixture. |
| Soak largest retained snapshot charge, and with the reservation | 26,268,564 / 30,017,837 bytes | 26,249,314 / 30,009,605 bytes | #445, as above. |

The worker's collection after sending an Aeneas analysis, which #434
explained, took 19.6 to 20.2 ms of wall time and 10.2 to 10.3 ms of CPU at
p50 in every scenario that reports it except `rename`, against 43.9 to
55.2 ms of wall time before; `rename` took 14.7 to 14.9 ms and 7.8 to
7.9 ms of CPU. That agrees with the 20.1 ms of wall time and about 10 ms of
CPU that the [ADR-0015](decisions/0015-gc-global-register-allocation.md)
comparison recorded and with the [pin
comparison](benchmarks.md#pinned-virgil-revision-comparison-2026-11-03),
which found clearing the same at both pins, so #444, not #445, likely made it
shorter. An analysis that follows another one in a session likely gains part
of that: in the full-scenario capture, Aeneas `reused_analysis` p50 fell from
309.889 to 289.981 ms and `rename` from 340.288 to 316.391 ms, and the
first-semantic config-first p50 from 698.004 to 645.277 ms, but their
p10–p90 intervals overlap the old ones, so they don't count as moved. Every
other cell moved by less than its noise. The soak's server HWM was
210,599,936 bytes, 8,192 more than before, and the error-result capture's
largest was 210,608,128 bytes; that capture's server HWM varies by about
179 MB across sessions.

Thirteen proposed latency targets changed, each by the unchanged rule:

- Lowered by a faster or tighter measurement: Aeneas `completion_hit` p95
  from 75 to 30 ms, Aeneas `prepareRename` from 30/40 to 15/20 ms, Aeneas
  `references` from 2/10 to 1/2 ms, Aeneas `rename` from 450/550 to
  400/500 ms, Aeneas `reused_analysis` p50 from 400 to 375 ms, Aeneas
  `syntax_diagnostic` p95 from 5 to 1 ms, Aeneas first semantic source first
  p50 from 500 to 450 ms, small `completion_miss` p95 from 3 to 2 ms, util
  `completion_hit` p95 from 3 to 2 ms, util `prepareRename` p95 from 5 to
  3 ms, util `rename` from 25/30 to 20/25 ms, and util `reused_analysis` p95
  from 25 to 20 ms.
- Raised by the error-result capture's loaded blocks: Aeneas
  `error_analysis` p95 from 550 to 800 ms. Its p50 target stayed at 450 ms.

The previous capture set the Aeneas `completion_hit` p95 target to 75 ms
because one server collection fell in every block's fourth hit (#433,
explained in #437). That collection still falls there, but it is shorter, so
the target is 30 ms, below the issue's starting 50 ms. The `error_analysis`
p95 target of 800 ms rests on the loaded blocks; a capture on a quiet host
would likely bring it back down. The rule was not changed to correct for
that, and the decision to adopt targets from this capture is the
maintainer's. The memory targets did not change: the largest measured values
moved by less than 1 MiB.

## Measured baselines

Every latency cell is in milliseconds over 30 measured operations, with p50,
p95, p10, p90, and the median absolute deviation (MAD). The definitions of
the clocks, percentiles, sample counts, and correctness barriers are
[below](#definitions).

### Full scenarios, `budgets.json`

From `budgets.json`, the `summary` rows with `metric: latency_ms`.

| Scenario | small p50 | small p95 | small p10 | small p90 | small MAD | util p50 | util p95 | util p10 | util p90 | util MAD | Aeneas p50 | Aeneas p95 | Aeneas p10 | Aeneas p90 | Aeneas MAD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 0.485 | 0.538 | 0.464 | 0.527 | 0.010 | 14.976 | 15.968 | 14.846 | 15.906 | 0.099 | 347.288 | 363.882 | 342.505 | 361.407 | 4.863 |
| `reused_analysis` | 0.172 | 0.198 | 0.164 | 0.185 | 0.006 | 12.661 | 13.087 | 12.448 | 12.996 | 0.098 | 289.981 | 306.919 | 284.568 | 305.940 | 5.084 |
| `definition` | 0.055 | 0.059 | 0.054 | 0.058 | 0.001 | 0.083 | 0.094 | 0.079 | 0.089 | 0.003 | 0.199 | 0.214 | 0.159 | 0.211 | 0.013 |
| `hover` | 0.053 | 0.064 | 0.051 | 0.055 | 0.001 | 0.080 | 0.083 | 0.075 | 0.082 | 0.002 | 0.156 | 0.189 | 0.138 | 0.188 | 0.018 |
| `references` | 0.058 | 0.064 | 0.056 | 0.060 | 0.001 | 0.114 | 0.130 | 0.109 | 0.125 | 0.004 | 0.790 | 1.167 | 0.764 | 0.862 | 0.025 |
| `prepareRename` | 0.156 | 0.172 | 0.152 | 0.164 | 0.003 | 1.318 | 1.553 | 1.305 | 1.539 | 0.012 | 11.141 | 11.819 | 11.044 | 11.532 | 0.084 |
| `rename` | 0.470 | 0.490 | 0.457 | 0.488 | 0.007 | 15.041 | 15.701 | 14.875 | 15.644 | 0.129 | 316.391 | 323.924 | 304.957 | 322.583 | 5.661 |
| `completion_miss` | 0.708 | 0.757 | 0.683 | 0.739 | 0.020 | 7.136 | 7.894 | 6.935 | 7.369 | 0.123 | 115.136 | 118.729 | 110.921 | 117.906 | 2.323 |
| `completion_hit` | 0.451 | 0.505 | 0.440 | 0.480 | 0.009 | 0.772 | 0.982 | 0.745 | 0.826 | 0.022 | 6.226 | 19.107 | 3.400 | 17.829 | 0.650 |
| `syntax_diagnostic` | 0.073 | 0.091 | 0.071 | 0.088 | 0.002 | 0.269 | 0.303 | 0.256 | 0.292 | 0.010 | 0.374 | 0.388 | 0.366 | 0.384 | 0.006 |
| `semantic_diagnostic` | 202.192 | 202.307 | 200.982 | 202.296 | 0.109 | 218.684 | 226.171 | 217.775 | 219.816 | 0.708 | 507.046 | 522.047 | 495.675 | 521.401 | 7.635 |

The Aeneas `completion_hit` p95 value (19.107 ms) sits far above its p50
value (6.226 ms); the p10 and p90 values (3.400 to 17.829 ms) show the tail
is a small number of slow samples, one hit per block that meets a server
collection, not a shift of the whole distribution.

### First semantic diagnostics, `first-semantic.json`

From `first-semantic.json`, the `summary` rows with `metric: latency_ms` for
the launch interval (process launch to the first exact semantic publication).
Each of the 252 sessions, warmups included, had one worker, every PID was
observed, and no worker was unobserved.

| Project | Order | p50 | p95 | p10 | p90 | MAD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| small | config first | 1.116 | 1.157 | 1.091 | 1.155 | 0.020 |
| small | source first | 1.004 | 1.059 | 0.962 | 1.039 | 0.019 |
| util | config first | 28.627 | 29.949 | 28.431 | 29.341 | 0.167 |
| util | source first | 16.077 | 16.380 | 15.972 | 16.342 | 0.070 |
| Aeneas | config first | 645.277 | 675.403 | 638.299 | 669.987 | 6.376 |
| Aeneas | source first | 356.124 | 367.895 | 345.883 | 365.231 | 8.650 |

### Error-result workload, `error-result.json`

From `error-result.json`, the `summary` rows with `metric: latency_ms`, for
the Aeneas project. Only `error_analysis` runs with the pinned
unresolved-name edit and 900 generated ordinary functions (54,790 extra
source bytes); the other rows use the ordinary workloads.

| Scenario | p50 | p95 | p10 | p90 | MAD |
| --- | ---: | ---: | ---: | ---: | ---: |
| `error_analysis` | 328.439 | 457.413 | 294.153 | 450.675 | 27.768 |
| `fresh_analysis` | 374.734 | 521.058 | 348.114 | 506.469 | 15.757 |
| `reused_analysis` | 312.162 | 496.516 | 291.591 | 452.549 | 20.250 |
| `semantic_diagnostic` | 549.370 | 673.821 | 502.444 | 659.330 | 37.330 |
| `completion_miss` | 119.689 | 123.193 | 111.848 | 123.009 | 2.809 |
| `rename` | 326.380 | 365.977 | 312.450 | 363.753 | 13.540 |

The wide p10–p90 intervals and high p95 values of the analysis rows come
from other work on the host during this capture's last four blocks, as the
[changes section](#changes-since-the-2026-11-02-capture) describes; the
full-scenario capture's Aeneas rows measure the same ordinary workloads on
an even host.

### Memory, per-PID and simultaneous

From the `resource_summary` of each session's process records in
`first-semantic.json` and `error-result.json`: the largest sampled RSS and
the OS high-water mark (HWM) over every observed server and worker PID, and
the largest sum of RSS within one 50 ms sampling pass.

| Capture, project | Server HWM | Worker HWM | Largest simultaneous total |
| --- | ---: | ---: | ---: |
| `first-semantic.json`, small | 1,114,112 bytes | 950,272 bytes | 2,064,384 bytes |
| `first-semantic.json`, util | 4,284,416 bytes | 11,075,584 bytes | 15,355,904 bytes |
| `first-semantic.json`, Aeneas | 53,465,088 bytes | 196,853,760 bytes | 250,310,656 bytes |
| `error-result.json`, Aeneas | 210,608,128 bytes | 197,775,360 bytes | 408,379,392 bytes |

The independent server and worker maxima are not a simultaneous peak; the
simultaneous column is the only sum of two processes' RSS in one pass.

### Memory soak, `soak.json`

The rerun of the [memory soak](benchmarks.md#memory-soak) ran one server
lifetime through two warmup cycles and 80 measured cycles over three physical
copies of the Aeneas sources (fixture tree hash
`de59c0546ea1be7520eb56278e1ad0ed47031b5938c9b8e7d2f1d0d6f122724d`, which
differs from the recorded soak's tree because the Virgil pin changed), in
101.9 seconds with 0 findings. The worker
completed 381 analyses (240 measured main), four workers ran, three were
retired after 100 analyses each, and every PID was observed over 2,105
sampler passes with 0 unobserved workers.

| Bound | Observed | Limit |
| --- | ---: | ---: |
| Parseable-text charge, 299 completions | at most 4,148,406 bytes | 4,194,304 bytes |
| Contexts' parseable texts together | 9,601,449 bytes, evictions in 271 completions | more than 4,194,304 bytes |
| Retained snapshot charge, 381 admissions | at most 26,249,314 bytes | 33,554,432 bytes |
| Retained charge and reservation after admission | at most 30,009,605 bytes | 33,554,432 bytes |
| Server RSS and HWM | 210,599,936 bytes, reached by cycle 21 and flat to cycle 80 | |
| Worker HWM, four workers | 193,957,888 to 193,966,080 bytes | |
| Largest sum of RSS in one pass | 404,566,016 bytes | |
| Worker live bytes after collection | 180,816 bytes, flat | 50,331,648 bytes (an eighth of the heap) |

The server's settled RSS was 210,534,400 bytes after cycle 1 and rose by
65,536 bytes in three steps, at cycles 4, 12, and 21, then stayed flat for the
remaining 59 cycles; that gives a slope of 549.3 bytes per cycle, which
describes the early steps, not continuing growth. The worker's slope was
-44.5 bytes per cycle: each worker's settled RSS rose by 8,192 to 40,960
bytes over its own cycles, and every worker ended between 193,957,888 and
193,961,984 bytes, so it describes the differences within and between
workers, not growth across the session. These are this schedule's values,
not a proof that nothing leaks.

## Supported envelope

The corpus pins the source density each cell measures: small is 2 files,
441 bytes, and 20 lines; util is 33 files, 161,421 bytes, and 5,033 lines;
Aeneas is 208 files, 3,068,198 bytes, and 84,272 lines. A project's program
may select at most 1,024 files and 4 MiB of paths and source. The Aeneas
corpus analysis encodes a RESULT frame of 4,379,351 bytes for fresh,
reused, and rename analyses and 4,379,601 bytes for the semantic
diagnostic, and charges a snapshot of 13,113,155 bytes against the 32 MiB
retained-snapshot budget, from the `result_bytes` and `snapshot_bytes`
probes in `budgets.json` and `error-result.json`. The soak's 209-file
fixture is a different program: its ordinary analyses charged snapshots of
13,123,358 to 13,123,808 bytes, its broken-file and descriptor steps up to
13,125,956 bytes, and it held the parseable-text cache at most 4,148,406
bytes against its 4 MiB limit. The worker's heap is 402,653,184 bytes
(384 MiB) and the server's is 200 MiB. These budgets bound what the server
supports; they are not a universal file or line count guarantee, and no
target below applies to a program outside this envelope.

## Targets

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
| `fresh_analysis` | 0.485 ms | 0.538 ms | 1 ms | 1 ms | 0.515 ms | 0.462 ms |
| `reused_analysis` | 0.172 ms | 0.198 ms | 1 ms | 1 ms | 0.828 ms | 0.802 ms |
| `definition` | 0.055 ms | 0.059 ms | 1 ms | 1 ms | 0.945 ms | 0.941 ms |
| `hover` | 0.053 ms | 0.064 ms | 1 ms | 1 ms | 0.947 ms | 0.936 ms |
| `references` | 0.058 ms | 0.064 ms | 1 ms | 1 ms | 0.942 ms | 0.936 ms |
| `prepareRename` | 0.156 ms | 0.172 ms | 1 ms | 1 ms | 0.844 ms | 0.828 ms |
| `rename` | 0.470 ms | 0.490 ms | 1 ms | 1 ms | 0.530 ms | 0.510 ms |
| `completion_miss` | 0.708 ms | 0.757 ms | 1 ms | 2 ms | 0.292 ms | 1.243 ms |
| `completion_hit` | 0.451 ms | 0.505 ms | 1 ms | 1 ms | 0.549 ms | 0.495 ms |
| `syntax_diagnostic` | 0.073 ms | 0.091 ms | 1 ms | 1 ms | 0.927 ms | 0.909 ms |
| `semantic_diagnostic` | 202.192 ms | 202.307 ms | 300 ms | 350 ms | 97.808 ms | 147.693 ms |
| first semantic, config first | 1.116 ms | 1.157 ms | 2 ms | 2 ms | 0.884 ms | 0.843 ms |
| first semantic, source first | 1.004 ms | 1.059 ms | 2 ms | 2 ms | 0.996 ms | 0.941 ms |

### util

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 14.976 ms | 15.968 ms | 20 ms | 25 ms | 5.024 ms | 9.032 ms |
| `reused_analysis` | 12.661 ms | 13.087 ms | 20 ms | 20 ms | 7.339 ms | 6.913 ms |
| `definition` | 0.083 ms | 0.094 ms | 1 ms | 1 ms | 0.917 ms | 0.906 ms |
| `hover` | 0.080 ms | 0.083 ms | 1 ms | 1 ms | 0.920 ms | 0.917 ms |
| `references` | 0.114 ms | 0.130 ms | 1 ms | 1 ms | 0.886 ms | 0.870 ms |
| `prepareRename` | 1.318 ms | 1.553 ms | 2 ms | 3 ms | 0.682 ms | 1.447 ms |
| `rename` | 15.041 ms | 15.701 ms | 20 ms | 25 ms | 4.959 ms | 9.299 ms |
| `completion_miss` | 7.136 ms | 7.894 ms | 10 ms | 15 ms | 2.864 ms | 7.106 ms |
| `completion_hit` | 0.772 ms | 0.982 ms | 1 ms | 2 ms | 0.228 ms | 1.018 ms |
| `syntax_diagnostic` | 0.269 ms | 0.303 ms | 1 ms | 1 ms | 0.731 ms | 0.697 ms |
| `semantic_diagnostic` | 218.684 ms | 226.171 ms | 300 ms | 350 ms | 81.316 ms | 123.829 ms |
| first semantic, config first | 28.627 ms | 29.949 ms | 40 ms | 50 ms | 11.373 ms | 20.051 ms |
| first semantic, source first | 16.077 ms | 16.380 ms | 25 ms | 25 ms | 8.923 ms | 8.620 ms |

### Aeneas

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 347.288 ms | 363.882 ms | 450 ms | 550 ms | 102.712 ms | 186.118 ms |
| `reused_analysis` | 289.981 ms | 306.919 ms | 375 ms | 500 ms | 85.019 ms | 193.081 ms |
| `definition` | 0.199 ms | 0.214 ms | 1 ms | 1 ms | 0.801 ms | 0.786 ms |
| `hover` | 0.156 ms | 0.189 ms | 1 ms | 1 ms | 0.844 ms | 0.811 ms |
| `references` | 0.790 ms | 1.167 ms | 1 ms | 2 ms | 0.210 ms | 0.833 ms |
| `prepareRename` | 11.141 ms | 11.819 ms | 15 ms | 20 ms | 3.859 ms | 8.181 ms |
| `rename` | 316.391 ms | 323.924 ms | 400 ms | 500 ms | 83.609 ms | 176.076 ms |
| `completion_miss` | 115.136 ms | 118.729 ms | 175 ms | 250 ms | 59.864 ms | 131.271 ms |
| `completion_hit` | 6.226 ms | 19.107 ms | 10 ms | 30 ms | 3.774 ms | 10.893 ms |
| `syntax_diagnostic` | 0.374 ms | 0.388 ms | 1 ms | 1 ms | 0.626 ms | 0.612 ms |
| `semantic_diagnostic` | 507.046 ms | 522.047 ms | 650 ms | 800 ms | 142.954 ms | 277.953 ms |
| `error_analysis` | 328.439 ms | 457.413 ms | 450 ms | 800 ms | 121.561 ms | 342.587 ms |
| first semantic, config first | 645.277 ms | 675.403 ms | 900 ms | 1,100 ms | 254.723 ms | 424.597 ms |
| first semantic, source first | 356.124 ms | 367.895 ms | 450 ms | 600 ms | 93.876 ms | 232.105 ms |

The issue's starting numbers for Aeneas, measured at an older revision,
compare as follows: hover/definition 5/25 ms covers the measured 0.156/0.189
ms and 0.199/0.214 ms; completion miss 150/250 ms covers 115.136/118.729 ms
with 34.864 ms and 131.271 ms of margin; completion hit 10/50 ms covers
6.226/19.107 ms with 3.774 ms and 30.893 ms of margin; reused analysis
350/500 ms covers 289.981/306.919 ms with 60.019 ms and 193.081 ms of margin;
publication 600/800 ms covers 507.046/522.047 ms with 92.954 ms and
277.953 ms of margin; first-semantic config-first 750/1,000 ms covers
645.277/675.403 ms with 104.723 ms and 324.597 ms of margin. The policy's
Aeneas rows match the issue's starting rows where they coincide: completion
hit p50 at 10 ms, completion miss p95 at 250 ms, reused analysis p95 at
500 ms, and publication p95 at 800 ms. The policy's completion hit p95,
30 ms, is below the issue's 50 ms.

### Memory targets

The measured maxima over this revision's captures are: server HWM
210,608,128 bytes (200.9 MiB) in the error-result capture and 210,599,936
bytes (200.8 MiB) in the soak; worker HWM 197,775,360 bytes (188.6 MiB) in
the error-result capture and 193,966,080 bytes (185.0 MiB) in the soak;
largest simultaneous total 408,379,392 bytes (389.5 MiB) in the error-result
capture and 404,566,016 bytes (385.8 MiB) in the soak. The adopted targets are:

| Quantity | Measured maximum | Target | Margin |
| --- | ---: | ---: | ---: |
| Server HWM | 210,608,128 bytes | 256 MiB (268,435,456 bytes) | 57,827,328 bytes (27.5%) |
| Worker HWM | 197,775,360 bytes | 256 MiB (268,435,456 bytes) | 70,660,096 bytes (35.7%) |
| Largest simultaneous total | 408,379,392 bytes | 512 MiB (536,870,912 bytes) | 128,491,520 bytes (31.5%) |

The issue's starting proposal of 224/400 MiB for server/worker peak RSS
covers the measured server HWM with 24,272,896 bytes (11.5%) of margin and the
worker HWM with 221,655,040 bytes (112.1%) of margin. This document sets both
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

## Enforcement

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
  semantic diagnostic records 865, 867, and 868 bytes), so a constant
  length is not a valid check.

The [resource checks](benchmarks.md#resource-checks) of the scheduled
comparison check worker allocation, RESULT bytes, and worker collections
under the policy of
[#347](https://github.com/SaltedTan/virgil-language-server/issues/347), which
the maintainer approved as proposed on 2026-11-06. They are enforced: a failed
check fails the scheduled job.

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

The maintainer adopted every row on 2026-11-03, with the values exactly as
proposed ([ADR-0016](decisions/0016-performance-budgets.md)).

| Question | Adopted value | Status |
| --- | --- | --- |
| Exact numeric rows | The target tables above, at revision `139db84582d7f230bd28e3609d2699a200878fe1` on the i7-14700KF reference host, CPU 12 | Adopted 2026-11-03 |
| Platforms | Linux x86-64 only; macOS/Rosetta unmeasured | Adopted 2026-11-03 |
| Noise and sample policy | 30 measured operations per cell, p50/p95, no trimming; a second run before any failure is declared | Adopted 2026-11-03 |
| Memory envelope | Server 256 MiB HWM, worker 256 MiB HWM, 512 MiB largest simultaneous total | Adopted 2026-11-03 |
| Enforcement mode | Advisory; no CI gate enabled | Adopted 2026-11-03 |
| Exception and retest authority | The maintainer | Adopted 2026-11-03 |
