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
`/home/sean-tan/.t3/scratch-virgil/budgets-refresh2/`. Every capture passed
its correctness checks; no sample was removed and no capture was rerun.
Other work on the host slowed the middle blocks of the full-scenario
capture, and those blocks set several of its Aeneas values below; the
[changes section](#changes-since-the-2026-11-02-capture) describes them.

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

The host's one-minute load average was 1.2, 3.5, and 2.8 when the three
`bench-lsp.py` captures started, against 2.3, 1.5, and 1.3 before. Other work
slowed the full-scenario capture's second to fifth blocks: in them, Aeneas
`fresh_analysis` had block medians of 391.9 to 464.2 ms, against 349.7 and
354.7 ms in the first and last blocks, although the worker allocated the same
bytes in every block. In the second to fourth blocks, the worker's
collection after sending, a fixed amount of work, took 10.4 to 11.2 ms of CPU
at the block median for `reused_analysis`, against 10.0 to 10.2 ms in the
others, so the host gave less CPU throughput, not just less time. util and
small analyses slowed in the same blocks. The error-result capture, which ran
after it, recorded an Aeneas `fresh_analysis` p50 of 354.304 ms, against
358.579 ms before.

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
| Aeneas `fresh_analysis` p95 | 361.650 ms | 473.447 ms | The host's load in the second to fifth blocks. Its p50 rose from 353.998 to 407.372 ms, but the p10–p90 intervals overlap. |
| Aeneas `semantic_diagnostic` p95 | 521.011 ms | 603.070 ms | The host's load, as above. |
| Aeneas `completion_miss` p95 | 119.143 ms | 131.601 ms | The host's load, as above. |
| small `rename` p95 | 0.499 ms | 0.552 ms | The host's load: the slowest samples are in the third to fifth blocks. |
| Aeneas `completion_hit` p95 | 33.564 ms | 20.128 ms | #444. The fourth measured hit of every block still meets a server collection, as #433 found, but took 16.6 to 21.4 ms instead of 29.6 to 35.3 ms; the other hits took at most 8.1 ms. The server's collector is compiled with the global register allocator too. The collection itself wasn't timed. |
| Aeneas `prepareRename` p95 | 23.534 ms | 13.984 ms | #444. On one CPU this request waits for the worker's collection after sending ([prepareRename on one CPU](benchmarks.md#preparerename-on-one-cpu-2026-10-31)), which is shorter, below. Its p50 fell from 20.492 to 11.935 ms; the p10–p90 intervals overlap only because six of the old 30 samples took 11.1 to 11.5 ms, which put the old p10 at 11.159 ms. |
| Aeneas `rename`, error-result capture, p95 | 364.269 ms | 325.935 ms | #444, through the same collection: its p50 fell from 340.252 to 316.492 ms. |
| Aeneas `references` p95 | 4.242 ms | 0.963 ms | Not identified: the old capture's slowest few samples. Its p50 didn't move. |
| Aeneas `syntax_diagnostic` p95 | 2.801 ms | 0.425 ms | Not identified: the old capture's slowest three samples. Its p50 didn't move. |
| util first semantic, config first, p50 | 30.010 ms | 28.654 ms | #444: this session discards one analysis, and the second waits for the worker's collection after sending the first, which fell from 1.431 to 0.288 ms at p50. #445's smaller util corpus may contribute. |
| Aeneas first semantic, config first, p50 | 698.004 ms | 656.635 ms | #444, as above: that collection fell from 57.967 to 20.761 ms at p50. The server's result decoding also fell, from 32.062 to 24.509 ms, and the probes record no server collection in the round trip and none during the worker's analysis; that part is not identified. |
| Aeneas first semantic, source first, p50 | 373.625 ms | 355.493 ms | Not identified. This session runs one analysis, and the probes record no server collection in its round trip and none during the worker's analysis. The worker's verification p50 fell from 54.074 to 46.510 ms and the server's result decoding from 32.199 to 24.360 ms. |
| small first semantic, worker HWM / largest simultaneous total | 933,888 / 2,027,520 bytes | 950,272 / 2,060,288 bytes | #443 and #445: the worker executable is 11,144 bytes larger. |
| util corpus | 31 files, 166,628 bytes, 5,360 lines | 33 files, 161,421 bytes, 5,033 lines | #445. |
| Aeneas corpus | 206 files, 3,072,972 bytes, 84,585 lines | 208 files, 3,068,198 bytes, 84,272 lines | #445. |
| Aeneas RESULT frame, fresh, reused, and rename analyses | 4,384,791 bytes | 4,379,351 bytes | #445's smaller corpus. |
| Aeneas RESULT frame, semantic diagnostic | 4,385,026 bytes | 4,379,601 bytes | #445, as above. |
| Aeneas RESULT frame, `error_analysis` | 4,516,395 bytes | 4,510,920 bytes | #445, as above. |
| Aeneas RESULT frame, `completion_miss` | 62,858 bytes | 62,959 bytes | #445, as above. |
| Aeneas snapshot charge | 13,123,309 bytes | 13,113,155 bytes | #445, as above. |
| Soak fixture | 207 files | 209 files | #445. |
| Soak worker HWM, four workers | 194,048,000 to 194,064,384 bytes | 193,957,888 to 193,970,176 bytes | #445's smaller fixture. |
| Soak worker live bytes after collection | 180,960 bytes | 180,816 bytes | #445, as above. |
| Soak largest sum of RSS in one pass | 404,656,128 bytes | 404,574,208 bytes | The worker's HWM above. |
| Soak largest parseable-text charge | 4,155,623 bytes | 4,148,406 bytes | #445's smaller fixture. |
| Soak largest retained snapshot charge, and with the reservation | 26,268,564 / 30,017,837 bytes | 26,249,314 / 30,009,605 bytes | #445, as above. |

The worker's collection after sending an Aeneas analysis, which #434
explained, took 19.2 to 20.3 ms of wall time and 10.1 to 10.2 ms of CPU at
p50 in every scenario that reports it except `rename`, against 43.9 to
55.2 ms of wall time before; `rename` took 14.9 to 16.0 ms and 7.8 ms of CPU.
That agrees with the 20.1 ms of wall time and about 10 ms of CPU that the
[ADR-0015](decisions/0015-gc-global-register-allocation.md) comparison
recorded and with the [pin
comparison](benchmarks.md#pinned-virgil-revision-comparison-2026-11-03),
which found clearing the same at both pins, so #444, not #445, likely made it
shorter. An analysis that follows another one in a session likely gains part
of that: the error-result capture's `error_analysis`, `reused_analysis`,
`semantic_diagnostic`, and `rename` p50 values fell by 14.0 to 23.8 ms, but
their p10–p90 intervals overlap the old ones, so they don't count as moved.
In the full-scenario capture, the host's load hid the same gain. Every other
cell moved by less than its noise. The soak's server HWM was
210,604,032 bytes, 12,288 more than before, and equal to the error-result
capture's, whose server HWM varies by about 179 MB across sessions.

Sixteen proposed latency targets changed, each by the unchanged rule:

- Raised by the slow blocks of the full-scenario capture: Aeneas
  `fresh_analysis` from 450/550 to 550/800 ms, Aeneas `reused_analysis` p95
  from 500 to 550 ms, Aeneas `rename` p95 from 550 to 600 ms, and Aeneas
  `semantic_diagnostic` p95 from 800 to 1,100 ms.
- Raised by a slower measurement: util `fresh_analysis` from 20/25 to
  25/30 ms. Its p50 rose from 15.847 to 17.117 ms, with overlapping p10–p90
  intervals, and the [pin
  comparison](benchmarks.md#pinned-virgil-revision-comparison-2026-11-03)
  measured 15.102 ms at the same pin, so the host's load is the likeliest
  cause; it wasn't ablated.
- Raised by a small change that crossed a rounding step: util
  `completion_hit` p50 from 1 to 2 ms (0.832 × 1.25 is just over 1), and util
  first semantic source first p95 from 25 to 30 ms (16.814 × 1.5 is just over
  25).
- Lowered by a faster or tighter measurement: Aeneas `completion_hit` p95
  from 75 to 40 ms, Aeneas `prepareRename` from 30/40 to 15/25 ms, Aeneas
  `references` p95 from 10 to 2 ms, Aeneas `syntax_diagnostic` p95 from 5 to
  1 ms, Aeneas `error_analysis` from 450/550 to 375/500 ms, Aeneas first
  semantic source first from 500/600 to 450/550 ms, small `completion_miss`
  p95 from 3 to 2 ms, util `prepareRename` p95 from 5 to 3 ms, and util
  `rename` p50 from 25 to 20 ms (15.961 × 1.25 is just under 20).

The previous capture set the Aeneas `completion_hit` p95 target to 75 ms
because one server collection fell in every block's fourth hit (#433,
explained in #437). That collection still falls there, but it is shorter, so
the target is 40 ms, below the issue's starting 50 ms. The four Aeneas rows
the slow blocks raised rest on a capture whose second to fifth blocks ran slower
than its first and last for a reason outside the server; a capture on a
quiet host would likely bring them back down. The rule was not changed to
correct for that, and the decision to adopt targets from this capture is
the maintainer's. The memory targets did not change: the largest measured
values moved by less than 1 MiB.

## Measured baselines

Every latency cell is in milliseconds over 30 measured operations, with p50,
p95, p10, p90, and the median absolute deviation (MAD). The definitions of
the clocks, percentiles, sample counts, and correctness barriers are
[below](#definitions).

### Full scenarios, `budgets.json`

From `budgets.json`, the `summary` rows with `metric: latency_ms`.

| Scenario | small p50 | small p95 | small p10 | small p90 | small MAD | util p50 | util p95 | util p10 | util p90 | util MAD | Aeneas p50 | Aeneas p95 | Aeneas p10 | Aeneas p90 | Aeneas MAD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 0.479 | 0.499 | 0.454 | 0.494 | 0.011 | 17.117 | 17.749 | 15.177 | 17.681 | 0.557 | 407.372 | 473.447 | 352.413 | 469.143 | 50.918 |
| `reused_analysis` | 0.172 | 0.184 | 0.163 | 0.184 | 0.005 | 13.663 | 15.346 | 12.611 | 15.150 | 1.011 | 313.683 | 359.399 | 284.150 | 348.781 | 19.030 |
| `definition` | 0.058 | 0.066 | 0.055 | 0.062 | 0.002 | 0.089 | 0.094 | 0.080 | 0.093 | 0.003 | 0.194 | 0.241 | 0.161 | 0.224 | 0.024 |
| `hover` | 0.054 | 0.059 | 0.051 | 0.057 | 0.001 | 0.080 | 0.088 | 0.075 | 0.087 | 0.003 | 0.179 | 0.629 | 0.143 | 0.199 | 0.021 |
| `references` | 0.059 | 0.066 | 0.056 | 0.063 | 0.002 | 0.131 | 0.145 | 0.113 | 0.141 | 0.010 | 0.827 | 0.963 | 0.777 | 0.944 | 0.028 |
| `prepareRename` | 0.161 | 0.187 | 0.153 | 0.182 | 0.007 | 1.461 | 1.978 | 1.342 | 1.611 | 0.087 | 11.935 | 13.984 | 11.240 | 13.273 | 0.560 |
| `rename` | 0.477 | 0.552 | 0.469 | 0.543 | 0.012 | 15.961 | 17.748 | 14.985 | 17.565 | 0.989 | 340.418 | 373.496 | 306.466 | 369.977 | 21.844 |
| `completion_miss` | 0.729 | 0.827 | 0.694 | 0.779 | 0.022 | 7.896 | 8.422 | 7.061 | 8.393 | 0.488 | 117.520 | 131.601 | 109.889 | 124.977 | 6.243 |
| `completion_hit` | 0.470 | 0.558 | 0.443 | 0.533 | 0.021 | 0.832 | 1.326 | 0.756 | 1.038 | 0.042 | 6.554 | 20.128 | 5.935 | 18.158 | 0.396 |
| `syntax_diagnostic` | 0.074 | 0.080 | 0.069 | 0.077 | 0.002 | 0.284 | 0.297 | 0.257 | 0.296 | 0.011 | 0.389 | 0.425 | 0.374 | 0.413 | 0.014 |
| `semantic_diagnostic` | 200.750 | 203.204 | 200.626 | 201.258 | 0.065 | 215.151 | 220.051 | 213.334 | 217.994 | 1.518 | 519.701 | 603.070 | 494.173 | 584.707 | 23.513 |

The Aeneas `completion_hit` and `hover` p95 values (20.128 ms and
0.629 ms) sit far above their p50 values (6.554 ms and 0.179 ms); the p10
and p90 values (5.935 to 18.158 ms and 0.143 to 0.199 ms) show the tail is a
small number of slow samples, not a shift of the whole distribution. The
Aeneas `fresh_analysis`, `reused_analysis`, `rename`, and
`semantic_diagnostic` rows have wide p10–p90 intervals and MADs of 50.918,
19.030, 21.844, and 23.513 ms because the host slowed this capture's second
to fifth blocks, as the [changes
section](#changes-since-the-2026-11-02-capture) describes.

### First semantic diagnostics, `first-semantic.json`

From `first-semantic.json`, the `summary` rows with `metric: latency_ms` for
the launch interval (process launch to the first exact semantic publication).
Each of the 252 sessions, warmups included, had one worker, every PID was
observed, and no worker was unobserved.

| Project | Order | p50 | p95 | p10 | p90 | MAD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| small | config first | 1.108 | 1.176 | 1.079 | 1.155 | 0.017 |
| small | source first | 0.995 | 1.068 | 0.961 | 1.053 | 0.026 |
| util | config first | 28.654 | 29.467 | 28.443 | 29.274 | 0.135 |
| util | source first | 16.195 | 16.814 | 16.038 | 16.654 | 0.123 |
| Aeneas | config first | 656.635 | 663.932 | 642.644 | 663.356 | 3.591 |
| Aeneas | source first | 355.493 | 360.467 | 344.554 | 359.466 | 1.594 |

### Error-result workload, `error-result.json`

From `error-result.json`, the `summary` rows with `metric: latency_ms`, for
the Aeneas project. Only `error_analysis` runs with the pinned
unresolved-name edit and 900 generated ordinary functions (54,790 extra
source bytes); the other rows use the ordinary workloads.

| Scenario | p50 | p95 | p10 | p90 | MAD |
| --- | ---: | ---: | ---: | ---: | ---: |
| `error_analysis` | 297.753 | 323.198 | 286.285 | 315.017 | 7.074 |
| `fresh_analysis` | 354.304 | 366.364 | 350.784 | 364.477 | 2.541 |
| `reused_analysis` | 299.348 | 322.618 | 284.202 | 317.606 | 9.547 |
| `semantic_diagnostic` | 498.285 | 511.915 | 490.245 | 510.838 | 4.049 |
| `completion_miss` | 111.642 | 118.272 | 109.013 | 114.143 | 0.835 |
| `rename` | 316.492 | 325.935 | 306.748 | 325.727 | 7.166 |

### Memory, per-PID and simultaneous

From the `resource_summary` of each session's process records in
`first-semantic.json` and `error-result.json`: the largest sampled RSS and
the OS high-water mark (HWM) over every observed server and worker PID, and
the largest sum of RSS within one 50 ms sampling pass.

| Capture, project | Server HWM | Worker HWM | Largest simultaneous total |
| --- | ---: | ---: | ---: |
| `first-semantic.json`, small | 1,114,112 bytes | 950,272 bytes | 2,060,288 bytes |
| `first-semantic.json`, util | 4,284,416 bytes | 11,075,584 bytes | 15,360,000 bytes |
| `first-semantic.json`, Aeneas | 53,460,992 bytes | 196,853,760 bytes | 250,310,656 bytes |
| `error-result.json`, Aeneas | 210,604,032 bytes | 197,775,360 bytes | 408,379,392 bytes |

The independent server and worker maxima are not a simultaneous peak; the
simultaneous column is the only sum of two processes' RSS in one pass.

### Memory soak, `soak.json`

The rerun of the [memory soak](benchmarks.md#memory-soak) ran one server
lifetime through two warmup cycles and 80 measured cycles over three physical
copies of the Aeneas sources (fixture tree hash
`de59c0546ea1be7520eb56278e1ad0ed47031b5938c9b8e7d2f1d0d6f122724d`, which
differs from the recorded soak's tree because the Virgil pin changed), in
101.4 seconds with 0 findings. The worker
completed 381 analyses (240 measured main), four workers ran, three were
retired after 100 analyses each, and every PID was observed over 2,095
sampler passes with 0 unobserved workers.

| Bound | Observed | Limit |
| --- | ---: | ---: |
| Parseable-text charge, 299 completions | at most 4,148,406 bytes | 4,194,304 bytes |
| Contexts' parseable texts together | 9,601,449 bytes, evictions in 271 completions | more than 4,194,304 bytes |
| Retained snapshot charge, 381 admissions | at most 26,249,314 bytes | 33,554,432 bytes |
| Retained charge and reservation after admission | at most 30,009,605 bytes | 33,554,432 bytes |
| Server RSS and HWM | 210,604,032 bytes, reached by cycle 21 and flat to cycle 80 | |
| Worker HWM, four workers | 193,957,888 to 193,970,176 bytes | |
| Largest sum of RSS in one pass | 404,574,208 bytes | |
| Worker live bytes after collection | 180,816 bytes, flat | 50,331,648 bytes (an eighth of the heap) |

The server's settled RSS was 210,538,496 bytes after cycle 1 and rose by
65,536 bytes in three steps, at cycles 4, 12, and 21, then stayed flat for the
remaining 59 cycles; that gives a slope of 549.3 bytes per cycle, which
describes the early steps, not continuing growth. The worker's slope was
4.7 bytes per cycle: each worker's settled RSS rose by 8,192 to 40,960 bytes
over its own cycles, and every worker ended between 193,957,888 and
193,961,984 bytes, so it describes small rises within each worker, not
growth across workers. These are this schedule's values, not a proof that nothing leaks.

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
| `fresh_analysis` | 0.479 ms | 0.499 ms | 1 ms | 1 ms | 0.521 ms | 0.501 ms |
| `reused_analysis` | 0.172 ms | 0.184 ms | 1 ms | 1 ms | 0.828 ms | 0.816 ms |
| `definition` | 0.058 ms | 0.066 ms | 1 ms | 1 ms | 0.942 ms | 0.934 ms |
| `hover` | 0.054 ms | 0.059 ms | 1 ms | 1 ms | 0.946 ms | 0.941 ms |
| `references` | 0.059 ms | 0.066 ms | 1 ms | 1 ms | 0.941 ms | 0.934 ms |
| `prepareRename` | 0.161 ms | 0.187 ms | 1 ms | 1 ms | 0.839 ms | 0.813 ms |
| `rename` | 0.477 ms | 0.552 ms | 1 ms | 1 ms | 0.523 ms | 0.448 ms |
| `completion_miss` | 0.729 ms | 0.827 ms | 1 ms | 2 ms | 0.271 ms | 1.173 ms |
| `completion_hit` | 0.470 ms | 0.558 ms | 1 ms | 1 ms | 0.530 ms | 0.442 ms |
| `syntax_diagnostic` | 0.074 ms | 0.080 ms | 1 ms | 1 ms | 0.926 ms | 0.920 ms |
| `semantic_diagnostic` | 200.750 ms | 203.204 ms | 300 ms | 350 ms | 99.250 ms | 146.796 ms |
| first semantic, config first | 1.108 ms | 1.176 ms | 2 ms | 2 ms | 0.892 ms | 0.824 ms |
| first semantic, source first | 0.995 ms | 1.068 ms | 2 ms | 2 ms | 1.005 ms | 0.932 ms |

### util

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 17.117 ms | 17.749 ms | 25 ms | 30 ms | 7.883 ms | 12.251 ms |
| `reused_analysis` | 13.663 ms | 15.346 ms | 20 ms | 25 ms | 6.337 ms | 9.654 ms |
| `definition` | 0.089 ms | 0.094 ms | 1 ms | 1 ms | 0.911 ms | 0.906 ms |
| `hover` | 0.080 ms | 0.088 ms | 1 ms | 1 ms | 0.920 ms | 0.912 ms |
| `references` | 0.131 ms | 0.145 ms | 1 ms | 1 ms | 0.869 ms | 0.855 ms |
| `prepareRename` | 1.461 ms | 1.978 ms | 2 ms | 3 ms | 0.539 ms | 1.022 ms |
| `rename` | 15.961 ms | 17.748 ms | 20 ms | 30 ms | 4.039 ms | 12.252 ms |
| `completion_miss` | 7.896 ms | 8.422 ms | 10 ms | 15 ms | 2.104 ms | 6.578 ms |
| `completion_hit` | 0.832 ms | 1.326 ms | 2 ms | 2 ms | 1.168 ms | 0.674 ms |
| `syntax_diagnostic` | 0.284 ms | 0.297 ms | 1 ms | 1 ms | 0.716 ms | 0.703 ms |
| `semantic_diagnostic` | 215.151 ms | 220.051 ms | 300 ms | 350 ms | 84.849 ms | 129.949 ms |
| first semantic, config first | 28.654 ms | 29.467 ms | 40 ms | 50 ms | 11.346 ms | 20.533 ms |
| first semantic, source first | 16.195 ms | 16.814 ms | 25 ms | 30 ms | 8.805 ms | 13.186 ms |

### Aeneas

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 407.372 ms | 473.447 ms | 550 ms | 800 ms | 142.628 ms | 326.553 ms |
| `reused_analysis` | 313.683 ms | 359.399 ms | 400 ms | 550 ms | 86.317 ms | 190.601 ms |
| `definition` | 0.194 ms | 0.241 ms | 1 ms | 1 ms | 0.806 ms | 0.759 ms |
| `hover` | 0.179 ms | 0.629 ms | 1 ms | 1 ms | 0.821 ms | 0.371 ms |
| `references` | 0.827 ms | 0.963 ms | 2 ms | 2 ms | 1.173 ms | 1.037 ms |
| `prepareRename` | 11.935 ms | 13.984 ms | 15 ms | 25 ms | 3.065 ms | 11.016 ms |
| `rename` | 340.418 ms | 373.496 ms | 450 ms | 600 ms | 109.582 ms | 226.504 ms |
| `completion_miss` | 117.520 ms | 131.601 ms | 175 ms | 250 ms | 57.480 ms | 118.399 ms |
| `completion_hit` | 6.554 ms | 20.128 ms | 10 ms | 40 ms | 3.446 ms | 19.872 ms |
| `syntax_diagnostic` | 0.389 ms | 0.425 ms | 1 ms | 1 ms | 0.611 ms | 0.575 ms |
| `semantic_diagnostic` | 519.701 ms | 603.070 ms | 650 ms | 1,100 ms | 130.299 ms | 496.930 ms |
| `error_analysis` | 297.753 ms | 323.198 ms | 375 ms | 500 ms | 77.247 ms | 176.802 ms |
| first semantic, config first | 656.635 ms | 663.932 ms | 900 ms | 1,100 ms | 243.365 ms | 436.068 ms |
| first semantic, source first | 355.493 ms | 360.467 ms | 450 ms | 550 ms | 94.507 ms | 189.533 ms |

The issue's starting numbers for Aeneas, measured at an older revision,
compare as follows: hover/definition 5/25 ms covers the measured 0.179/0.629
ms and 0.194/0.241 ms; completion miss 150/250 ms covers 117.520/131.601 ms
with 32.480 ms and 118.399 ms of margin; completion hit 10/50 ms covers
6.554/20.128 ms with 3.446 ms and 29.872 ms of margin; reused analysis
350/500 ms covers 313.683/359.399 ms with 36.317 ms and 140.601 ms of margin;
publication 600/800 ms covers 519.701/603.070 ms with 80.299 ms and
196.930 ms of margin; first-semantic config-first 750/1,000 ms covers
656.635/663.932 ms with 93.365 ms and 336.068 ms of margin. The policy's
Aeneas rows match the issue's starting rows where they coincide: completion
hit p50 at 10 ms and completion miss p95 at 250 ms. The policy's completion
hit p95, 40 ms, is below the issue's 50 ms. Its reused analysis p95, 550 ms,
and publication p95, 1,100 ms, are above the issue's 500 and 800 ms, because
this capture's slow blocks set those p95 values.

### Memory targets

The measured maxima over this revision's captures are: server HWM
210,604,032 bytes (200.8 MiB) in both the error-result capture and the soak;
worker HWM 197,775,360 bytes (188.6 MiB) in the error-result capture and
193,970,176 bytes (185.0 MiB) in the soak; largest simultaneous total
408,379,392 bytes (389.5 MiB) in the error-result capture and 404,574,208
bytes (385.8 MiB) in the soak. The proposed targets are:

| Quantity | Measured maximum | Target | Margin |
| --- | ---: | ---: | ---: |
| Server HWM | 210,604,032 bytes | 256 MiB (268,435,456 bytes) | 57,831,424 bytes (27.5%) |
| Worker HWM | 197,775,360 bytes | 256 MiB (268,435,456 bytes) | 70,660,096 bytes (35.7%) |
| Largest simultaneous total | 408,379,392 bytes | 512 MiB (536,870,912 bytes) | 128,491,520 bytes (31.5%) |

The issue's starting proposal of 224/400 MiB for server/worker peak RSS
covers the measured server HWM with 24,276,992 bytes (11.5%) of margin and the
worker HWM with 221,655,040 bytes (112.1%) of margin. This proposal sets both
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
  rename records 665 and 666 bytes), so a constant
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
| Exact numeric rows | The target tables above, at revision `139db84582d7f230bd28e3609d2699a200878fe1` on the i7-14700KF reference host, CPU 12 | Not adopted |
| Platforms | Linux x86-64 only; macOS/Rosetta unmeasured | Not adopted |
| Noise and sample policy | 30 measured operations per cell, p50/p95, no trimming; a second run before any failure is declared | Not adopted |
| Memory envelope | Server 256 MiB HWM, worker 256 MiB HWM, 512 MiB largest simultaneous total | Not adopted |
| Enforcement mode | Advisory; no CI gate enabled | Not adopted |
| Exception and retest authority | The maintainer | Not adopted |
