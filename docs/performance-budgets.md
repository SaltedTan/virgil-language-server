# Performance budgets

**Proposal — not an adopted release gate.** This document records measured
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

The captures below ran on 2026-10-31 (UTC) on the reference host of the
[reference baseline](benchmarks.md#reference-baseline): an Intel Core
i7-14700KF (28 logical CPUs), Linux `7.0.0-42-generic` x86-64/glibc 2.43,
Python 3.14.4, pinned with `taskset` to CPU 12 (governor `powersave`). The
executables came from `make bench-build` at source revision
`85baf49de43acb77f372d0e477d204af5a4c4423`
(`v0.1.0-alpha.5-35-g85baf49de43a`), with pinned Virgil
`57318d37e9fcca8de6fda5f9cd82d669479f49ff`, the default 384 MiB worker heap
(402,653,184 bytes), and the server's default 200 MiB heap. Binary SHA-256
hashes: server `f4f73214b0c9985c9db5d80040c608a40acc1b7b6df09daca0c77e6a613d7264`
(981,720 bytes), worker `b358ec3259126a769f857e8c2f96db467b0acf3d665151afb1e22ffc296baa82`
(892,952 bytes). The corpus manifest SHA-256 was
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
`/home/sean-tan/.t3/scratch-virgil/perf-budgets-345/`. Every capture passed
its correctness checks; no sample was removed and no capture was rerun.

The [memory soak record in `docs/benchmarks.md`](benchmarks.md#memory-soak)
was measured at revision `500b9f53` with different server and worker binaries,
so it does not match this revision, build, and corpus. The soak was rerun
with the bounded tool above; its results follow.

## Measured baselines

Every latency cell is in milliseconds over 30 measured operations, with p50,
p95, p10, p90, and the median absolute deviation (MAD). The definitions of
the clocks, percentiles, sample counts, and correctness barriers are
[below](#definitions).

### Full scenarios, `budgets.json`

From `budgets.json`, the `summary` rows with `metric: latency_ms`.

| Scenario | small p50 | small p95 | small p10 | small p90 | small MAD | util p50 | util p95 | util p10 | util p90 | util MAD | Aeneas p50 | Aeneas p95 | Aeneas p10 | Aeneas p90 | Aeneas MAD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 0.430 | 0.459 | 0.424 | 0.453 | 0.004 | 14.349 | 14.582 | 14.224 | 14.566 | 0.083 | 347.137 | 358.416 | 335.890 | 355.839 | 8.249 |
| `reused_analysis` | 0.161 | 0.178 | 0.139 | 0.170 | 0.008 | 13.063 | 14.358 | 11.876 | 13.846 | 0.484 | 292.074 | 310.841 | 282.909 | 309.912 | 9.460 |
| `definition` | 0.055 | 0.058 | 0.054 | 0.058 | 0.001 | 0.093 | 0.098 | 0.090 | 0.098 | 0.002 | 0.205 | 3.768 | 0.171 | 3.588 | 0.027 |
| `hover` | 0.053 | 0.055 | 0.051 | 0.055 | 0.001 | 0.089 | 0.096 | 0.087 | 0.093 | 0.001 | 0.386 | 0.406 | 0.351 | 0.400 | 0.019 |
| `references` | 0.057 | 0.062 | 0.056 | 0.059 | 0.001 | 0.125 | 0.133 | 0.123 | 0.131 | 0.002 | 0.835 | 4.363 | 0.803 | 1.207 | 0.014 |
| `prepareRename` | 0.152 | 0.161 | 0.150 | 0.158 | 0.001 | 1.627 | 2.816 | 1.610 | 2.807 | 0.012 | 23.064 | 40.087 | 17.277 | 39.456 | 3.278 |
| `rename` | 0.447 | 0.502 | 0.443 | 0.482 | 0.004 | 16.405 | 16.771 | 15.400 | 16.672 | 0.232 | 339.860 | 350.577 | 324.956 | 345.338 | 5.201 |
| `completion_miss` | 0.678 | 0.736 | 0.662 | 0.709 | 0.016 | 7.469 | 8.174 | 6.790 | 7.934 | 0.347 | 134.286 | 151.377 | 122.096 | 146.777 | 8.535 |
| `completion_hit` | 0.441 | 0.668 | 0.433 | 0.613 | 0.006 | 0.817 | 1.679 | 0.720 | 1.496 | 0.104 | 5.876 | 6.602 | 2.981 | 6.349 | 0.861 |
| `syntax_diagnostic` | 0.078 | 0.085 | 0.068 | 0.083 | 0.005 | 0.291 | 0.305 | 0.268 | 0.299 | 0.017 | 0.420 | 1.555 | 0.380 | 1.537 | 0.042 |
| `semantic_diagnostic` | 202.101 | 202.223 | 200.662 | 202.200 | 0.088 | 219.450 | 220.176 | 214.455 | 220.000 | 0.254 | 511.261 | 520.038 | 498.883 | 516.816 | 3.392 |

The Aeneas `definition` and `references` p95 values (3.768 ms and 4.363 ms)
sit far above their p50 values (0.205 ms and 0.835 ms); the p90 values
(3.588 ms and 1.207 ms) show the tail is a small number of slow samples, not
a shift of the whole distribution.

### First semantic diagnostics, `first-semantic.json`

From `first-semantic.json`, the `summary` rows with `metric: latency_ms` for
the launch interval (process launch to the first exact semantic publication).
Each of the 252 sessions, warmups included, had one worker, every PID was
observed, and no worker was unobserved.

| Project | Order | p50 | p95 | p10 | p90 | MAD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| small | config first | 1.057 | 1.089 | 1.026 | 1.081 | 0.016 |
| small | source first | 0.949 | 0.976 | 0.925 | 0.967 | 0.011 |
| util | config first | 29.310 | 29.859 | 29.081 | 29.669 | 0.095 |
| util | source first | 15.820 | 15.913 | 15.744 | 15.899 | 0.038 |
| Aeneas | config first | 691.693 | 699.226 | 660.330 | 697.242 | 7.460 |
| Aeneas | source first | 364.289 | 372.768 | 349.305 | 371.444 | 4.908 |

### Error-result workload, `error-result.json`

From `error-result.json`, the `summary` rows with `metric: latency_ms`, for
the Aeneas project. Only `error_analysis` runs with the pinned
unresolved-name edit and 900 generated ordinary functions (54,790 extra
source bytes); the other rows use the ordinary workloads.

| Scenario | p50 | p95 | p10 | p90 | MAD |
| --- | ---: | ---: | ---: | ---: | ---: |
| `error_analysis` | 300.622 | 308.623 | 287.648 | 307.729 | 7.193 |
| `fresh_analysis` | 356.967 | 360.738 | 341.816 | 360.365 | 2.641 |
| `reused_analysis` | 308.018 | 334.411 | 285.328 | 331.375 | 14.316 |
| `semantic_diagnostic` | 512.898 | 524.751 | 494.215 | 520.736 | 4.589 |
| `completion_miss` | 137.286 | 152.013 | 123.205 | 151.327 | 12.689 |
| `rename` | 340.898 | 350.676 | 326.449 | 349.689 | 8.429 |

### Memory, per-PID and simultaneous

From the `resource_summary` of each session's process records in
`first-semantic.json` and `error-result.json`: the largest sampled RSS and
the OS high-water mark (HWM) over every observed server and worker PID, and
the largest sum of RSS within one 50 ms sampling pass.

| Capture, project | Server HWM | Worker HWM | Largest simultaneous total |
| --- | ---: | ---: | ---: |
| `first-semantic.json`, small | 32,960,512 bytes | 929,792 bytes | 32,960,512 bytes |
| `first-semantic.json`, util | 5,173,248 bytes | 11,157,504 bytes | 16,326,656 bytes |
| `first-semantic.json`, Aeneas | 67,854,336 bytes | 196,005,888 bytes | 263,852,032 bytes |
| `error-result.json`, Aeneas | 210,608,128 bytes | 196,911,104 bytes | 407,519,232 bytes |

The independent server and worker maxima are not a simultaneous peak; the
simultaneous column is the only sum of two processes' RSS in one pass.

### Memory soak, `soak.json`

The rerun of the [memory soak](benchmarks.md#memory-soak) ran one server
lifetime through two warmup cycles and 80 measured cycles over three physical
copies of the Aeneas sources (fixture tree hash
`30913a056ae8ab025a596973654a7eadf6825f8e7a975dd410cbef4e044b79f9`, the same
tree as the recorded soak), in 103.6 seconds with 0 findings. The worker
completed 381 analyses (240 measured main), four workers ran, three were
retired after 100 analyses each, and every PID was observed over 2,138
sampler passes with 0 unobserved workers.

| Bound | Observed | Limit |
| --- | ---: | ---: |
| Parseable-text charge, 299 completions | at most 4,155,891 bytes | 4,194,304 bytes |
| Contexts' parseable texts together | 9,612,028 bytes, evictions in 271 completions | more than 4,194,304 bytes |
| Retained snapshot charge, 381 admissions | at most 26,269,458 bytes | 33,554,432 bytes |
| Retained charge and reservation after admission | at most 30,018,699 bytes | 33,554,432 bytes |
| Server RSS and HWM | 210,604,032 bytes, flat across all 80 cycles | |
| Worker HWM, four workers | 193,126,400 to 193,134,592 bytes | |
| Largest sum of RSS in one pass | 403,738,624 bytes | |
| Worker live bytes after collection | 177,088 bytes, flat | 50,331,648 bytes (an eighth of the heap) |

The server's RSS slope was 0 bytes per cycle and the worker's 24.6 bytes per
cycle, the difference between workers, not growth within one. These are this
schedule's values, not a proof that nothing leaks.

## Supported envelope

The corpus pins the source density each cell measures: small is 2 files,
441 bytes, and 20 lines; util is 31 files, 166,628 bytes, and 5,360 lines;
Aeneas is 206 files, 3,072,972 bytes, and 84,585 lines. A project's program
may select at most 1,024 files and 4 MiB of paths and source. The Aeneas
corpus analysis encodes a RESULT frame of 4,379,197 bytes for fresh,
reused, and rename analyses and 4,379,432 bytes for the semantic
diagnostic, and charges a snapshot of 13,123,309 bytes against the 32 MiB
retained-snapshot budget, from the `result_bytes` and `snapshot_bytes`
probes in `budgets.json` and `error-result.json`. The soak's 207-file
fixture is a different program: it charged snapshots of 13,133,432 to
13,133,878 bytes and held the parseable-text cache at most 4,155,891 bytes
against its 4 MiB limit. The worker's heap is 402,653,184 bytes
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
the 25% change threshold at which that workflow flags a comparison, so a
target failure is a change beyond what the advisory comparison flags. Thirty measured operations give a rough p95, not
an SLA; the p10, p90, and MAD columns in [Measured baselines](#measured-baselines)
stay the record of the spread each target covers.

Targets are Linux reference-host values. The macOS/Rosetta cells are
[unmeasured](#unmeasured-cells), so there is no macOS target.

### small

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 0.430 ms | 0.459 ms | 1 ms | 1 ms | 0.570 ms | 0.541 ms |
| `reused_analysis` | 0.161 ms | 0.178 ms | 1 ms | 1 ms | 0.839 ms | 0.822 ms |
| `definition` | 0.055 ms | 0.058 ms | 1 ms | 1 ms | 0.945 ms | 0.942 ms |
| `hover` | 0.053 ms | 0.055 ms | 1 ms | 1 ms | 0.947 ms | 0.945 ms |
| `references` | 0.057 ms | 0.062 ms | 1 ms | 1 ms | 0.943 ms | 0.938 ms |
| `prepareRename` | 0.152 ms | 0.161 ms | 1 ms | 1 ms | 0.848 ms | 0.839 ms |
| `rename` | 0.447 ms | 0.502 ms | 1 ms | 1 ms | 0.553 ms | 0.498 ms |
| `completion_miss` | 0.678 ms | 0.736 ms | 1 ms | 2 ms | 0.322 ms | 1.264 ms |
| `completion_hit` | 0.441 ms | 0.668 ms | 1 ms | 2 ms | 0.559 ms | 1.332 ms |
| `syntax_diagnostic` | 0.078 ms | 0.085 ms | 1 ms | 1 ms | 0.922 ms | 0.915 ms |
| `semantic_diagnostic` | 202.101 ms | 202.223 ms | 300 ms | 350 ms | 97.899 ms | 147.777 ms |
| first semantic, config first | 1.057 ms | 1.089 ms | 2 ms | 2 ms | 0.943 ms | 0.911 ms |
| first semantic, source first | 0.949 ms | 0.976 ms | 2 ms | 2 ms | 1.051 ms | 1.024 ms |

### util

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 14.349 ms | 14.582 ms | 20 ms | 25 ms | 5.651 ms | 10.418 ms |
| `reused_analysis` | 13.063 ms | 14.358 ms | 20 ms | 25 ms | 6.937 ms | 10.642 ms |
| `definition` | 0.093 ms | 0.098 ms | 1 ms | 1 ms | 0.907 ms | 0.902 ms |
| `hover` | 0.089 ms | 0.096 ms | 1 ms | 1 ms | 0.911 ms | 0.904 ms |
| `references` | 0.125 ms | 0.133 ms | 1 ms | 1 ms | 0.875 ms | 0.867 ms |
| `prepareRename` | 1.627 ms | 2.816 ms | 3 ms | 5 ms | 1.373 ms | 2.184 ms |
| `rename` | 16.405 ms | 16.771 ms | 25 ms | 30 ms | 8.595 ms | 13.229 ms |
| `completion_miss` | 7.469 ms | 8.174 ms | 10 ms | 15 ms | 2.531 ms | 6.826 ms |
| `completion_hit` | 0.817 ms | 1.679 ms | 2 ms | 3 ms | 1.183 ms | 1.321 ms |
| `syntax_diagnostic` | 0.291 ms | 0.305 ms | 1 ms | 1 ms | 0.709 ms | 0.695 ms |
| `semantic_diagnostic` | 219.450 ms | 220.176 ms | 300 ms | 350 ms | 80.550 ms | 129.824 ms |
| first semantic, config first | 29.310 ms | 29.859 ms | 40 ms | 50 ms | 10.690 ms | 20.141 ms |
| first semantic, source first | 15.820 ms | 15.913 ms | 20 ms | 25 ms | 4.180 ms | 9.087 ms |

### Aeneas

| Scenario | Measured p50 | Measured p95 | Target p50 | Target p95 | p50 margin | p95 margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 347.137 ms | 358.416 ms | 450 ms | 550 ms | 102.863 ms | 191.584 ms |
| `reused_analysis` | 292.074 ms | 310.841 ms | 375 ms | 500 ms | 82.926 ms | 189.159 ms |
| `definition` | 0.205 ms | 3.768 ms | 1 ms | 10 ms | 0.795 ms | 6.232 ms |
| `hover` | 0.386 ms | 0.406 ms | 1 ms | 1 ms | 0.614 ms | 0.594 ms |
| `references` | 0.835 ms | 4.363 ms | 2 ms | 10 ms | 1.165 ms | 5.637 ms |
| `prepareRename` | 23.064 ms | 40.087 ms | 30 ms | 75 ms | 6.936 ms | 34.913 ms |
| `rename` | 339.860 ms | 350.577 ms | 450 ms | 550 ms | 110.140 ms | 199.423 ms |
| `completion_miss` | 134.286 ms | 151.377 ms | 175 ms | 250 ms | 40.714 ms | 98.623 ms |
| `completion_hit` | 5.876 ms | 6.602 ms | 10 ms | 10 ms | 4.124 ms | 3.398 ms |
| `syntax_diagnostic` | 0.420 ms | 1.555 ms | 1 ms | 3 ms | 0.580 ms | 1.445 ms |
| `semantic_diagnostic` | 511.261 ms | 520.038 ms | 650 ms | 800 ms | 138.739 ms | 279.962 ms |
| `error_analysis` | 300.622 ms | 308.623 ms | 400 ms | 500 ms | 99.378 ms | 191.377 ms |
| first semantic, config first | 691.693 ms | 699.226 ms | 900 ms | 1,100 ms | 208.307 ms | 400.774 ms |
| first semantic, source first | 364.289 ms | 372.768 ms | 500 ms | 600 ms | 135.711 ms | 227.232 ms |

The issue's starting numbers for Aeneas, measured at an older revision,
compare as follows: hover/definition 5/25 ms covers the measured 0.386/0.406
ms and 0.205/3.768 ms; completion miss 150/250 ms covers 134.286/151.377 ms
with 15.714 ms and 98.623 ms of margin; completion hit 10/50 ms covers
5.876/6.602 ms; reused analysis 350/500 ms covers 292.074/310.841 ms with
57.926 ms and 189.159 ms of margin; publication 600/800 ms covers
511.261/520.038 ms with 88.739 ms and 279.962 ms of margin; first-semantic
config-first 750/1,000 ms covers 691.693/699.226 ms with 58.307 ms and
300.774 ms of margin. The policy's Aeneas rows match the issue's starting
rows where they coincide: completion hit p50 at 10 ms, completion miss p95
at 250 ms, reused analysis p95 at 500 ms, and publication p95 at 800 ms.

### Memory targets

The measured maxima over this revision's captures are: server HWM
210,608,128 bytes (200.8 MiB) in the error-result capture and 210,604,032
bytes in the soak; worker HWM 196,911,104 bytes (187.8 MiB) in the
error-result capture and 193,134,592 bytes (184.3 MiB) in the soak; largest
simultaneous total 407,519,232 bytes (388.6 MiB) in the error-result capture
and 403,738,624 bytes (384.9 MiB) in the soak. The proposed targets are:

| Quantity | Measured maximum | Target | Margin |
| --- | ---: | ---: | ---: |
| Server HWM | 210,608,128 bytes | 256 MiB (268,435,456 bytes) | 57,827,328 bytes (27.4%) |
| Worker HWM | 196,911,104 bytes | 256 MiB (268,435,456 bytes) | 71,524,352 bytes (36.3%) |
| Largest simultaneous total | 407,519,232 bytes | 512 MiB (536,870,912 bytes) | 129,351,680 bytes (31.7%) |

The issue's starting proposal of 224/400 MiB for server/worker peak RSS
covers the measured server HWM with 24,272,896 bytes (11.5%) of margin and the
worker HWM with 222,519,296 bytes (113.0%) of margin. This proposal sets both
processes at 256 MiB, which leaves 27.4% and 36.3% of headroom over the
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
  semantic diagnostic records 860, 861, and 863 bytes), so a constant
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
| Exact numeric rows | The target tables above, at revision `85baf49de43acb77f372d0e477d204af5a4c4423` on the i7-14700KF reference host, CPU 12 | Not adopted |
| Platforms | Linux x86-64 only; macOS/Rosetta unmeasured | Not adopted |
| Noise and sample policy | 30 measured operations per cell, p50/p95, no trimming; a second run before any failure is declared | Not adopted |
| Memory envelope | Server 256 MiB HWM, worker 256 MiB HWM, 512 MiB largest simultaneous total | Not adopted |
| Enforcement mode | Advisory; no CI gate enabled | Not adopted |
| Exception and retest authority | The maintainer | Not adopted |
