# Performance benchmarks

The performance benchmark measures whole-program analyses and editor
requests over real stdio pipes. The [syntax-error tolerance benchmark](#semantics-during-syntax-errors)
measures semantic availability and correctness while editing. Both use Python
3.9+ and its standard library, including the
[`Session` protocol client](../test/protocol/worker.py). The [weekly comparison](#scheduled-comparison)
reports changes without timing budgets; these measurements do not gate releases.

## Run

From a physical checkout with the pinned submodule initialized:

```sh
mkdir -p build/bench-results
make bench BENCH_ARGS='--cpu 4 --output build/bench-results/run-1.json'
```

This builds separate `build/bench/virgil-lsp` and `virgil-lsp-worker` executables
with compile-time probes, then runs all three projects. Choose an allowed CPU for
your machine; `4` is a P-core hardware thread on the reference host below. Without
`--cpu`, the client uses `taskset` to pin itself and its children to its first
allowed CPU when available. On macOS, automatic pinning is unavailable; the JSON
records this, and `--cpu none` explicitly requests an unpinned run. Explicit CPU
selection requires `taskset`. No governor or system-wide setting is changed.

Defaults are three process blocks, two warmups, and five measured operations per
scenario per block. Adjust them with `--blocks`, `--warmups`, and `--operations` in
`BENCH_ARGS`. Select projects with repeated `--project small`, `--project util`, or
`--project aeneas`. Every warmup is checked and retained with `warmup: true`, but
excluded from summaries. A fresh-worker warmup uses its own process pair so it
cannot warm a measured fresh worker. File caches may already be warm.

The client is also directly runnable against a normal build. Generate benchmark
metadata explicitly; ordinary `make all` builds need no Python:

```sh
make all bench-metadata
python3 scripts/bench-lsp.py --exe build/virgil-lsp --output build/bench-results/normal.json
python3 scripts/bench-lsp.py --smoke --exe build/virgil-lsp --output /dev/null
```

`make test` runs the small smoke case and the client's rejection checks
(`python3 test/benchmarks/test_client.py`). The smoke case takes under a second
on the reference host; the full benchmark is not part
of `make test`. Smoke runs need no benchmark build metadata. The tests compare
the corpus revision to the superproject's `vendor/virgil` gitlink in the index
(which matches `HEAD` unless a pin bump is staged). A changed gitlink requires
corpus regeneration. The manifest must list exactly the three reviewed projects
(`small`, `util`, and `aeneas`) in every mode; deleting one cannot skip it. If
the checkout differs from a matching gitlink (the advisory Virgil-master
workflow), or there is no Git metadata, they print one notice and skip vendor
corpus projects while checking the small fixture. Strict benchmarks always
require matching pins and hashes.
Outside the [typing-burst](#cancelled-completion-typing-burst) and
[first-semantic](#first-semantic-diagnostics) scenarios,
`--require-probes` requires one complete phase transaction for each
analysis, completion miss, rename preflight, and semantic publication, and no
worker transaction for a cache hit, current navigation, or syntax publication.
`make bench` supplies it automatically. This catches a mislabeled completion hit
or worker replacement as well as missing probe output.

JSON goes to `--output` (stdout by default); a human summary goes to stderr. Failed
runs exit nonzero, produce no success summary, and, with a file output, preserve
partial samples and the failing session's transcript in `<output>.failure.json`.
`make` prints its own build commands, so use a file output with `make bench`.

## Corpus and correctness

[`test/benchmarks/corpus.json`](../test/benchmarks/corpus.json) pins the small
two-file fixture, Virgil's util library, and the Makefile's `AENEAS_SRC` compiler
source set. The generated manifest owns their ordered file lists, file counts,
byte sizes, and line totals; `python3 scripts/bench-corpus.py --check` checks the
pin and prints the totals for the checked projects.

The manifest includes source revisions, ordered paths, each file's SHA-256 and
length, analysis flags (pinned language defaults, no target), and query and edit
coordinates in UTF-16. Query sites are `total` in `Main.main`, parameter `index` in
`Vector.get`, and field `explicitName` in `Program.name`. Unique text anchors
recompute every position; the pin must match before and after a run. A pin bump,
moved anchor, changed byte, or changed selection fails before measurement. Review
any intentional change with `python3 scripts/bench-corpus.py --write`; do not use
that command merely to make a failed check pass. When changing the small fixture,
also advance the full literal `SMALL_REVISION` in the generator to a commit
containing those bytes. For a compiler pin bump, follow the
[pin update checklist](../CONTRIBUTING.md#updating-the-pinned-virgil-revision).
Run old and new corpora separately when assessing a compiler pin change.

The file order matches shell expansion of the Makefile's source set. Aeneas's
unsaved project config uses one pattern per compiler source directory, in that
order, then its dependency patterns. A single `src/*/*.v3` project pattern sorts
full paths differently around `x86/` and `x86-64/`; fixing the pattern order keeps
explicit and project analyses identical. The client checks the returned document
list in order. Configuration files and text changes exist only as open overlays;
the harness never writes source files or project files to fixture directories.

Every measured operation checks its answer before accepting the sample. Analyses
require the scenario's expected diagnostics, the exact source list, current
document versions, and an advancing generation. Navigation checks the definition's
exact location, hover's signature prefix and range, and every reference location.
Completion requires the named semantic item and its detail, so a keyword-only
fallback fails. Rename requires every expected edit, the new name, and each
document's expected version; the returned edits are never applied. Diagnostics
must have the exact code, message, range, source, severity, URI, and edited version.

## Scenarios and boundaries

| JSON scenario | Timed interval and preparation |
| --- | --- |
| `fresh_analysis` | Explicit `virgil-lsp/analyze` send to its decoded reply, including the first worker's startup and full occurrence snapshot. Initialize and open happen before timing. Each iteration starts a fresh server/worker pair. |
| `reused_analysis` | The same explicit analysis in the last fresh iteration's worker, starting with its second analysis. Warmups are additional analyses. |
| `definition`, `hover`, `references`, `prepareRename` | A request on a current project snapshot to its decoded reply. An untimed inspection checks freshness before each operation. |
| `rename` | The rename request through its returned versioned edits, including edited-source capture, worker preflight, and binding validation. |
| `completion_miss` | A names completion at alternating positions inside the query identifier. Alternation bypasses the one-entry cursor/version cache. |
| `completion_hit` | Immediately repeat the identical completion at the same position and version. |
| `syntax_diagnostic` | Full-text `didChange` appending `def benchBroken = ;` to a nonempty `publishDiagnostics` for that version. Only the edited file's parser runs on this path. |
| `semantic_diagnostic` | Full-text `didChange` appending an unresolved global to its nonempty diagnostic publication, including the 200 ms debounce and full project analysis. |

Before each error edit, an untimed restore sends the original text, awaits a
successful definition reply, and checks its version. That reply drains the
restore's analysis. There is no sleep or snapshot-polling timing barrier. Empty
clearing notifications and diagnostics from other versions never finish a timed
publication. The count retirement limit is raised for each block to keep reused
analyses in one worker; process command lines record the override. Retirement,
members, and broader cursor distributions need separate benchmarks. The
[typing-burst scenario](#cancelled-completion-typing-burst) covers a fixed burst
of cancelled completions.

### Error-result regression

Add `--error-result --project aeneas` to repeat full analyses with the pinned
unresolved-name edit and 900 generated ordinary functions (54,790 extra source
bytes). This opt-in `error_analysis` scenario reproduces the diagnostic-bearing
RESULT allocation regression from [#240](https://github.com/SaltedTan/virgil-language-server/issues/240):

```sh
python3 scripts/bench-lsp.py \
  --exe build/base-0/virgil-lsp --exe build/r2-n00/virgil-lsp \
  --project aeneas --error-result --require-probes --cpu 12 \
  --output build/bench-results/error-result.json
```

The extra functions are deterministic unsaved text appended after the unresolved
name; the reviewed corpus and its diagnostic coordinates stay unchanged. An
untimed semantic publication prepares the error state, then each timed explicit
analysis must return exactly that diagnostic, current source versions, and an
advancing generation. Warmups and operations follow the ordinary block settings.
This scenario times repeated error analyses without a restore between them;
`semantic_diagnostic` continues to time the original publication workload.

### Cancelled completion typing burst

With `--typing-burst`, each block also types the last four letters of the
project's pinned query token, one edit and completion every 20 ms, cancelling
the preceding completion before each edit. The final cursor is after the full
token. The run first restores a current project, then uses unsaved overlays only.
An appended unresolved name makes the project's semantic publication observable:
an empty parser publication cannot satisfy the diagnostic check. Every request
must reply once; earlier replies may finish before cancellation, and the last
reply must contain the expected semantic item and detail.

The `typing_burst` sample's `latency_ms` runs from the final completion's send to
its reply. `diagnostic_latency_ms` runs from that same send to publication of the
exact semantic diagnostic at the final version. `worker_replacements` counts
informational reclaims, and `worker_starts` counts completed spawn-to-handshake
probes. The sample retains every emitted probe, including incomplete cancelled
transactions; `--require-probes` requires the final useful query and automatic
analysis to finish. These metrics describe a fixed burst followed by a pause,
not continuous typing or a general cancellation latency budget.

### First semantic diagnostics

With `--first-semantic`, each block runs fresh launches instead of the scenarios
above; the option can't be combined with `--error-result`, `--typing-burst`, or
`--inlay-refresh`. Every sample, warmups included, starts a new server with no
extra options, and that server starts its own worker. After `initialize` and
`initialized`, the client opens the project's unsaved configuration and the
query file with the pinned unresolved-name edit already appended, at version 1.
`first_semantic_config_first` opens the configuration first, and
`first_semantic_source_first` opens the source first, so the server starts it in
single-file mode until the configuration arrives. Iterations alternate which
order runs first, and warmups are retained with `warmup: true`.

Timing ends at the first publication for the query file at version 1 whose
diagnostics are exactly the expected one (code, message, range, severity, and
source). The configuration's publication, the parser's empty publication, other
versions, and other diagnostics are kept in `earlier_publications` and never end
the interval. A 60-second deadline fails the sample if the expected publication
never arrives. An untimed `virgil-lsp/snapshot` must then hold exactly that
diagnostic, with current sources. No interval contains a sleep or a polling
request. With `--require-probes`, each sample must include the worker and server
transaction metrics, source capture, and worker startup; a discarded or
single-file analysis may contribute additional transactions.

| Sample metric | Timed interval |
| --- | --- |
| `latency_ms` | Process launch, just before the server starts, to that publication: process start, `initialize`, configuration discovery, worker startup, and the full analysis. |
| `open_latency_ms` | The source's `didOpen` send to the publication. It includes configuration discovery and worker startup in both orders; in config-first order, the configuration was opened just before. |
| `config_open_latency_ms` | The configuration's `didOpen` send to the publication. |
| `initialize_ms` | The `initialize` request to its reply. |
| `discarded_analyses` | Analyses the server discarded because an open document changed after their sources were captured. |

In config-first order, the configuration's open starts a project analysis before
the edited source arrives, so the server discards it and analyzes again. Full
editor launch, from starting the editor to the client's first request, isn't
measured here; it is a separate product boundary. File caches may already be
warm.

Measured on 2026-10-31 at `b5d28d3c` (server `v0.1.0-alpha.5-10-gbf50f3ea027c`,
Virgil `57318d37`), on the i7-14700KF host of the [reference baseline](#reference-baseline),
pinned to CPU 12 (governor `powersave`), with the `make bench-build` executables
and a 384 MiB worker heap. SHA-256 hashes: server
`1707d1747bbe5a8529f197fdae8b762af96da009fc30b9c38d09fb9a815cc9a6`, worker
`5eab9c087dce8472a1e0ce0fde2d1f8e8eaeef0ee6c017b7ff6b3869fe0d67d2`, and corpus
manifest `79ff4b89d9b18251202dadefd53cef96f2089c7949c59d52400495113e001c15`:

```sh
python3 scripts/bench-lsp.py --exe build/bench/virgil-lsp --first-semantic \
  --blocks 6 --operations 5 --warmups 2 --cpu 12 --require-probes \
  --output build/bench-results/first-semantic.json
```

Each cell is **p50 / p95, in milliseconds**, over 30 measured launches, using the
percentile definition [below](#phases-allocation-and-output). Every config-first
sample discarded one analysis, and no source-first sample discarded any.
`initialize_ms` was about 0.1 ms in every case.

| Project | Config first, launch | Config first, `didOpen` | Source first, launch | Source first, `didOpen` |
| --- | ---: | ---: | ---: | ---: |
| small | 1.084 / 1.168 | 0.684 / 0.754 | 0.980 / 1.032 | 0.633 / 0.669 |
| util | 29.986 / 32.260 | 29.537 / 31.839 | 16.174 / 16.986 | 15.813 / 16.604 |
| Aeneas | 689.942 / 712.432 | 689.025 / 711.939 | 365.066 / 381.941 | 364.606 / 381.479 |

Each of the 252 sessions, warmups included, had one worker, every PID was
observed, and every one had a high-water mark. The Aeneas worker's high-water
mark was 206,307,328 bytes (p50) config first and 200,235,008 source first; the
server's was 67,807,232 and 35,872,768.

## Phases, allocation, and output

`BENCH=1` sets `BenchProbe.enabled` and `RiGc.stats` with one `-redef-field` option.
Ordinary builds compile out the probe calls and marker strings. The fixed-buffer
formatter writes only to stderr, performs no heap allocation, and does not turn
on verbose GC. It changes neither LSP messages nor the worker wire format.
Keep benchmark builds in their own directory.

Each sample retains individual `BENCH` records with process role and analysis
number; each process also retains its whole stderr trace. Time metrics end in
`_us`; protocol `latency_ms` uses Python's monotonic `perf_counter_ns`. Internal
phase clocks use the existing `System.ticksUs` wall clock. Interpret phases as:

| Metric | Meaning |
| --- | --- |
| worker `parse_us`, `verify_us` | Existing adapter statistics, excluding cleanup and diagnostic mapping. |
| worker `adapter_us` | Request-decoded to adapter return, including parse, verify, cleanup, and result setup; overlaps the preceding two metrics. |
| worker `collect_us` | Occurrence collection; zero or near zero for a scope-only completion. |
| worker `query_us` | Scope/member/call query plus the final alarm/global-stat bookkeeping before encoding. |
| server `capture_us` | Capture source bytes and their provenance for analysis/query submission. |
| server `rename_capture_us` | Capture and edit the analyzed source bytes for a rename's preflight; excludes the earlier reference/edit planning. |
| server `request_encode_us`, worker `request_decode_us` | Binary worker-request encoding and decoding. |
| worker `result_encode_us`, server `result_decode_us` | Binary worker-result encoding and decoding. These are not JSON-only serialization timings. |
| server `worker_start_us`, `roundtrip_us` | Process start to HELLO, and supervisor admission through result decoding. Startup overlaps request encoding, and roundtrip excludes source capture. |
| worker `gc_count`, `allocated_bytes` | Collections and allocation from request decoding through result encoding; exclude the explicit collections before and after sending. Allocation is the pinned runtime's cumulative allocated bytes plus current-cycle allocation delta. |
| server `gc_count`, `allocated_bytes` | Corresponding deltas from supervisor admission through result decoding; exclude capture and later LSP response rendering. |
| worker `force_gc_after_send_us` | Collection after dropping the sent result; emitted with the next request so the probe falls within that request's sample window. Absent on a fresh worker's first request; the last collection is unreported if there is no next request. |
| worker `force_gc_us`, `live_after_gc_bytes` | Explicit pre-send collection duration and the existing retirement-policy live-byte count, excluding the result byte-array length. It runs once per transaction, before the collection after sending. |
| worker `heap_after_handle_bytes`, server `heap_after_decode_bytes` | Space used at those boundaries, not peak live memory. |
| worker `result_bytes`, server `worker_heap_bytes` | Encoded RESULT frame bytes and the worker's actual reserved heap from HELLO. |

Phase times include GC that occurs within the phase; do not add `adapter_us` to
parse/verify, or `roundtrip_us` to its constituent phases. Probe syscalls add some
end-to-end overhead despite allocating nothing. Prefer normal builds for the
most precise external latency comparison and instrumented builds for attribution.

JSON includes raw samples grouped by build, project, and process block; summaries
report sample count, median, p10, p50, p90, and p95, min/max, and median absolute
deviation (MAD), with no trimming. Each percentile interpolates linearly between
the order statistics around position (*n* − 1) × *q* of the sorted samples, so
p50 equals the median. The additive fields keep the JSON schema at 1;
`scripts/bench-report.py` accepts results with or without them. Summaries pool
measured operations; use raw block IDs when comparing correlated samples.
Process records include current RSS, and Linux adds process peak RSS when
readable; macOS uses `ps` and leaves peak RSS null.

[First-semantic](#first-semantic-diagnostics) sessions and every session of an
`--error-result` run also keep a `resource_summary`; the default run isn't
sampled. A sampler thread reads the server and its child processes every 50 ms,
when the session starts and stops, when the scenario changes, and again at once
when it sees the set of processes change. On Linux it reads `/proc` without
starting a process, about 33 µs per pass on the reference host, and that time
falls inside any timed interval that a pass overlaps. Each observed PID keeps its
own largest sampled RSS (`max_rss_bytes`) and OS high-water mark (`hwm_bytes`,
Linux `VmHWM`), overall and for each phase, also after a worker is replaced.
Phases are scenario names, and warmups share the `warmup` phase. The high-water
mark covers the process's life up to the phase's last sample. The summary rows
`server.max_rss_bytes`, `worker.max_rss_bytes`, `server.hwm_bytes`, and
`worker.hwm_bytes` hold one value per PID and measured phase, so the
error-result workload has its own `error_analysis` rows.

`max_sampled_total_rss_bytes` is the largest sum of RSS within one sampling pass.
Adding the server's and worker's separate maxima doesn't give a simultaneous
peak, and no summary does. A worker that starts and exits between passes is
never observed, including one that has exited but hasn't been reaped when `ps`
is the source. When probes count completed worker starts, `unobserved_workers`
counts such workers, and their peaks are unknown. `seen_at_stop: false` marks a
PID that was gone before the last pass, whose peak may exceed its last sample.
On macOS, `ps` supplies RSS, `hwm_bytes` is null, and `hwm_available` is false:
a missing high-water mark is never recorded as zero. The sampler stops and its
thread exits before the session shuts down.

Metadata includes the corpus, both revisions, generated
BuildInfo, compiler and binary hashes, compilation commands/source hashes, heap
settings, host CPU, logical cores, kernel, Python, load, affinity, governors when
readable, and all server command lines. Missing build metadata fails with a request
to rebuild using `make bench-metadata` with the executable's `BUILD` directory.

## Compare builds

Use the same host, CPU affinity, corpus, compiler, flags, and heap sizes. Use
same-length physical checkout and build-directory names: both analyzed source
paths and compiled-in paths affect sizes and allocation. A different checkout
path alone can move the Aeneas worker across a GC boundary. Keep both executables
and their build metadata together, and avoid concurrent work where possible.

Build each candidate with `make BUILD=build/name all bench-metadata` (add `BENCH=1` to both for
phase comparisons). From the checkout owning the pinned corpus, pass both paths:

```sh
python3 scripts/bench-lsp.py \
  --exe /path/to/base/build/name/virgil-lsp \
  --exe /path/to/next/build/name/virgil-lsp \
  --blocks 6 --operations 5 --warmups 2 --cpu 4 \
  --output build/bench-results/comparison.json
```

The client alternates build order across whole process blocks (A/B, then B/A) for
each project. Compare per-block medians, distributions, and GC counts, rerun, and
retain outliers. Do not treat milliseconds from unrelated machines, a 1 GB heap,
or different source path lengths as an interchangeable baseline. A large spread
or changing medians calls for another run, not a claimed regression or win.

[`scripts/bench-analysis.sh`](../scripts/bench-analysis.sh) remains available for
fresh CLI parse/verify statistics and retains its regression tests. Its CLI output
path and statistics differ from this full-snapshot protocol workload; it cannot
replace end-to-end measurements.

### Document highlight kinds

Measured on 2026-10-25 with the [Read/Write highlight change](architecture.md#document-highlights) at source revision `efced29`, against `741a7d3`, on the same i7-14700KF host and pinned Virgil compiler as the reference baseline below. Both candidates were built together in this physical checkout, in equal-length `build/base01` and `build/next01` directories, with `BENCH=1 all bench-metadata`. Their generated BuildInfo text was identical, including its `-dirty` suffix; the metadata's source hashes were checked against each revision. Both used the default 384 MiB worker heap. The comparison used all three pinned projects, CPU 12, six alternating process blocks, two warmups, and five measured operations per block, with no outliers removed:

```sh
python3 scripts/bench-lsp.py \
  --exe build/base01/virgil-lsp --exe build/next01/virgil-lsp \
  --require-probes --blocks 6 --operations 5 --warmups 2 --cpu 12 \
  --output build/bench-results/highlight-kinds.json
```

Each timing is **median (p10–p90), in milliseconds**, over 30 measured operations per candidate:

| Metric | Baseline | Highlight kinds |
| --- | ---: | ---: |
| Small reused occurrence collection | 0.012 (0.011–0.014) | 0.012 (0.011–0.014) |
| Util reused occurrence collection | 3.761 (3.649–3.941) | 3.746 (3.667–3.867) |
| Aeneas fresh occurrence collection | 110.649 (108.066–115.753) | 111.319 (109.749–114.871) |
| Aeneas reused occurrence collection | 93.155 (92.399–93.962) | 94.398 (92.253–104.992) |
| Aeneas reused analysis, end to end | 289.328 (281.400–313.503) | 303.973 (281.750–325.329) |

The first run had one slower Aeneas collection block in the changed build, widening its reused-collection spread. A second six-block comparison, run after the editor tests finished with the same command plus `--project aeneas`, gave fresh collection 110.823 (106.475–113.909) ms before and 110.161 (106.861–111.179) ms after; reused collection was 93.440 (92.180–98.105) ms before and 92.615 (91.867–96.954) ms after. Reused end-to-end analysis was 309.673 (284.387–335.440) ms before and 305.324 (282.250–332.505) ms after. The slower first-run median did not repeat; the overlapping spreads and varying block medians do not establish a collection or end-to-end latency regression. All samples remain in the results.

Every measured worker transaction collected zero times in both candidates, excluding the explicit final collection. Successful full-snapshot result lengths were identical: 596 bytes for small, 168,738 for util, and 3,936,046 for Aeneas. CLI analyses of the same relative source paths charged identical decoded result allocations: 5,212, 751,779, and 13,250,205 bytes respectively. Temporary worker allocation increased only by the collector's one target pointer per file: 16, 248, and 1,640 bytes respectively, under 0.001% for Aeneas. A fieldless occurrence subtype carries the write flag without enlarging each temporary occurrence, and the snapshot packs it in its existing target integer.

## Reclaiming obsolete jobs

The [#262](https://github.com/SaltedTan/virgil-language-server/issues/262)
experiment did **not** justify changing the scheduler. The measured policy
reclaimed a withdrawn temporary completion, repaired signature-help query, or
rename preflight as soon as another job or a due context could start. Automatic
analyses were never reclaimed; retained snapshots and the job/context turn order
were preserved. Stale queries kept their existing once-only rerun or fallback.

The initial Aeneas baseline, measured before scheduler edits, used CPU 4, one
process block, two warmups, and five measured operations. Its last burst reply
took 200.370 ms median, semantic diagnostics took 502.718 ms after the final
request, idle completion took 123.317 ms, and spawn-to-handshake took 5.416 ms.
Those numbers motivated testing immediate reclaim without an age threshold.

The paired comparison used main `0d33c02` and the uncommitted experimental policy
on 2026-10-25, with pinned Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5`,
the Intel Core i7-14700KF reference host, and the default 384 MiB worker heap.
Both were built in this checkout with `BENCH=1 all bench-metadata`, in
equal-length `build/base` and `build/next` directories. Generated BuildInfo
strings had equal lengths, including their `-dirty` suffixes. Metadata hashes
were checked for all 275 inputs in each build; only `AnalysisSupervisor`,
`LspSemanticDiagnostics`, `LspQueryRequests`, and `LspRename` differed among
production sources. The run held the shared benchmark lock, waited for the other
agent's benchmark to finish, and pinned the client and both child processes to
P-core hardware thread 4 (use the same lock path in every worktree):

```sh
flock /path/to/shared/bench.lock taskset -c 4 \
  python3 scripts/bench-lsp.py \
  --exe build/base/virgil-lsp --exe build/next/virgil-lsp \
  --project aeneas --typing-burst --require-probes \
  --blocks 3 --operations 5 --warmups 2 --cpu 4 \
  --output build/bench-results/reclaim-jobs.json
```

Build order alternated by block. Every semantic answer and final versioned
semantic publication passed. No outliers were removed. Times below are
**median (p10–p90), milliseconds**, over 15 measured operations per build:

| Metric | Main | Immediate reclaim |
| --- | ---: | ---: |
| Burst last useful reply | 216.046 (202.106–218.075) | 201.761 (198.648–215.046) |
| Burst semantic diagnostics | 503.129 (496.935–541.989) | 610.867 (574.525–617.389) |
| Idle completion miss | 132.332 (130.900–147.818) | 132.056 (131.266–144.566) |
| Fresh analysis | 340.400 (327.521–345.502) | 338.866 (326.565–342.668) |
| Reused analysis | 295.483 (282.707–307.953) | 298.314 (281.502–323.855) |
| Rename preflight | 333.828 (318.265–341.627) | 337.261 (320.595–339.092) |
| Ordinary semantic diagnostic | 516.582 (503.038–537.576) | 522.325 (505.113–535.230) |

The last reply's median improved by 6.6%, but its p10–p90 intervals overlap.
Diagnostics after the burst regressed by **21.4%** (107.738 ms), with disjoint
intervals. The measured bursts reclaimed 45 workers, three per burst, and
completed 36 replacement handshakes; some workers were reclaimed during startup.
Replacement spawn-to-handshake took 8.029 (7.137–9.616) ms, over those 36 starts,
versus fresh-start 5.321 (5.262–5.487) ms on main. Full restart cost includes
cold worker state and scheduling delay beyond the handshake. Measuring that
handshake alone understated the cost to useful completion and publication.

The issue requires faster last replies **and diagnostics no later**. This policy
failed that requirement, so the implementation and its race tests were discarded;
only the scenario and measurements are committed. Cancellation still answers at
once and discards a running temporary result when it finishes. There is no new
restart trigger or elapsed-time threshold. A future policy needs new evidence
covering full replacement cost and diagnostic publication before it can ship.

The rejected source/test patch has SHA-256
`935d94154798d32f0a7f78b15fc0c67efe7a51e4dca841cd1605d1424aa790c7`.
The paired JSON retains binary hashes, source hashes, individual samples, all
probe records, complete process stderr, host state, and the exact command.

## Type-definition navigation

Measured on 2026-10-25 against main `a2a522c`, on the Intel Core i7-14700KF reference host with pinned Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5` and the default 384 MiB worker heap. Both candidates were built in this physical checkout, in equal-length `build/base-r1` and `build/next-r1` directories, with `BENCH=1 all bench-metadata` and identical generated BuildInfo text. The baseline sources were extracted for its build, then the optimized sources were restored byte for byte. Source hashes in both build manifests were checked against their inputs.

The comparison held the shared benchmark lock after a read-only process check found no other benchmark. The client and its child processes ran on P-core hardware thread 12. All three pinned projects used six alternating process blocks, two warmups, and five measured operations per block, with every semantic check passing and no samples removed:

```sh
flock /path/to/shared/bench.lock taskset -c 12 \
  python3 scripts/bench-lsp.py \
  --exe build/base-r1/virgil-lsp --exe build/next-r1/virgil-lsp \
  --require-probes --blocks 6 --operations 5 --warmups 2 --cpu 12 \
  --output comparison-final.json
```

Times are **median (p10–p90), milliseconds**, over 30 measured operations per build and scenario.

| Measurement | Main | With type-definition targets |
| --- | ---: | ---: |
| Small fresh analysis | 0.462 (0.442–0.501) | 0.462 (0.445–0.495) |
| Small reused analysis | 0.144 (0.135–0.158) | 0.144 (0.135–0.154) |
| Util fresh analysis | 15.760 (15.231–16.502) | 15.758 (15.103–16.118) |
| Util reused analysis | 13.622 (13.314–14.201) | 13.430 (13.216–13.838) |
| Aeneas fresh analysis, end to end | 343.992 (338.285–357.976) | 334.337 (323.617–346.791) |
| Aeneas fresh occurrence collection | 112.397 (111.054–120.147) | 115.498 (111.709–119.710) |
| Aeneas fresh result encoding | 70.811 (70.002–76.785) | 59.775 (57.283–63.471) |
| Aeneas fresh result decoding | 16.234 (15.740–17.248) | 17.105 (16.540–17.899) |
| Aeneas reused analysis, end to end | 308.001 (293.099–326.309) | 296.570 (285.764–321.383) |
| Aeneas reused occurrence collection | 98.831 (93.954–102.124) | 100.615 (97.296–102.747) |
| Aeneas reused result encoding | 69.574 (66.114–73.392) | 57.111 (54.758–59.180) |
| Aeneas reused result decoding | 14.349 (13.923–16.134) | 15.012 (14.519–16.995) |

Aeneas fresh and reused analysis medians are lower by 9.655 ms (2.81%) and 11.431 ms (3.71%), respectively, with overlapping end-to-end intervals. Small and util medians are unchanged within noise or lower. This comparison establishes no measurable end-to-end latency cost. Collection still adds 3.101 ms fresh and 1.784 ms reused at the median, and decoding adds 0.871 and 0.663 ms; their intervals overlap. Result encoding saves 11.036 and 12.463 ms, with disjoint intervals. Nominal targets are memoized across files, distinct targets are registered during collection, defaults share declaration indexing, and ordered stream merging removes override sorting. Numeric declaration, string, and path IDs and prepared sparse bytes are reused between sizing and writing. Further collection savings could share resolved-type lookup with display interning; a smaller target encoding could reduce the remaining decoding work.

Every measured worker transaction collected zero times in both candidates, excluding the explicit final collection. Aeneas fresh worker allocation rises from 189,226,344 to 193,162,328 bytes (2.08%); reused allocation medians rise from 189,072,920 to 193,008,904. The worker-only target registry and constructor cache, numeric translation arrays, and prepared byte buffers account for the temporary growth. These buffers do not enter the retained snapshot.

Successful full-snapshot wire sizes are identical to the reviewed type-definition implementation. The following charges come from six independent size-probe runs per project and build, using the same relative source paths and no editor document provenance. Every size and charge is constant across those runs, so its median, p10, and p90 are the listed value. Wire lengths below are from the benchmark's worker probes and are also constant across all measured samples.

| Project | Wire bytes, main → targets | Decoded allocation, main → targets | Retained charge, main → targets |
| --- | ---: | ---: | ---: |
| Small | 596 → 614 | 5,212 → 5,353 | 6,572 → 6,713 |
| Util | 168,738 → 172,674 | 751,779 → 756,219 | 757,784 → 762,224 |
| Aeneas | 3,936,046 → 4,054,897 | 13,250,205 → 13,311,167 | 13,285,835 → 13,346,797 |

The wire sends a default source-type target per declaration and sparse exceptions, including explicit nulls. Aeneas wire growth remains 118,851 bytes (3.02%), and retained charge grows by 60,962 bytes (0.46%) for the compressed overrides and their checkpoints. The default integer remains within the existing conservative 128-byte declaration/range charge: the x86-64 declaration and range each occupy 40 bytes, plus an 8-byte array slot. Both Aeneas charges stay below the unchanged 80% budget guard. The frame and decoded-allocation limits are unchanged; no compiler types or rendered type strings are added to the snapshot.

## Inlay hints

Measured on 2026-10-25 against main `240eed7`, including method-body fallback, after the third review round. The implementation sources are commit `8785f21`. The host is an Intel Core i7-14700KF with pinned Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5` and the default 384 MiB worker heap. Both candidates were built in this physical checkout, in equal-length `build/base-265-r3` and `build/next-265-r3` directories, with `BENCH=1 all bench-metadata` and identical generated BuildInfo text. Baseline sources were extracted for their build, then feature sources restored byte for byte. The metadata’s actual source hashes match the two commits.

Read-only process checks preceded the comparisons. The final comparison held the shared benchmark lock and pinned the client and its children to P-core hardware thread 12. It used all three pinned projects, six alternating process blocks, two warmups, and five measured operations per block. Every semantic check passed; no samples were removed.

```sh
flock /path/to/shared/bench.lock taskset -c 12 \
  python3 scripts/bench-lsp.py \
  --exe build/base-265-r3/virgil-lsp --exe build/next-265-r3/virgil-lsp \
  --require-probes --blocks 6 --operations 5 --warmups 2 --cpu 12 \
  --inlay-refresh --output comparison-r3-final.json
```

The refresh option advertises and acknowledges `workspace.inlayHint.refreshSupport` and adds repeated project-scoped analyses after discovery of the unsaved project configuration. Explicit URI-list analyses have no project context and cannot measure refresh comparison. Every benchmark session has **one open Virgil source document**; the refresh scenario additionally has the JSON configuration open, for **two open documents in total**. Only the source document contributes hints. The scenario runs with warm per-file coordinate caches and asserts that unchanged analyses send no additional refresh. Installing the configuration can legitimately refresh the source that was already open without a project; that transition occurs before timing. New files establish a baseline without refreshing, because clients request hints when opening them. Cross-file edits, configuration replacement, active-context switches, and leaving every project are covered by protocol tests. Baselines belong to open documents across configuration revisions, and only their active contexts contribute results. The initial cold scan is outside the timed samples.

Times are **median (p10–p90), milliseconds**, over 30 operations per build and scenario. All rows use the final refresh-capable comparison. Its explicit URI-list fresh and reused analyses do not run refresh comparison; the additional project-scoped scenario does.

| Measurement | Main | With inlay hints |
| --- | ---: | ---: |
| Small fresh analysis, end to end | 0.449 (0.437–0.484) | 0.448 (0.429–0.488) |
| Small reused analysis, end to end | 0.141 (0.134–0.148) | 0.138 (0.132–0.146) |
| Util fresh analysis, end to end | 15.181 (14.948–15.305) | 15.165 (15.053–15.762) |
| Util reused analysis, end to end | 13.278 (13.116–14.265) | 13.244 (13.132–13.335) |
| Aeneas fresh end to end | 330.093 (319.283–336.532) | 331.246 (328.259–335.932) |
| Aeneas fresh occurrence collection | 114.554 (110.810–116.733) | 118.590 (117.528–120.655) |
| Aeneas fresh result encoding | 60.360 (56.735–62.620) | 57.505 (56.696–60.294) |
| Aeneas fresh result decoding | 16.789 (16.616–17.159) | 15.558 (15.393–15.938) |
| Aeneas reused end to end | 298.038 (277.419–323.077) | 298.707 (283.345–323.316) |
| Aeneas reused occurrence collection | 97.603 (96.060–103.835) | 102.990 (98.734–104.843) |
| Aeneas reused result encoding | 54.913 (54.265–59.311) | 55.124 (51.356–57.445) |
| Aeneas reused result decoding | 14.782 (14.052–16.613) | 13.757 (13.367–15.419) |
| Aeneas refresh end to end | 291.215 (285.914–305.233) | 292.578 (286.467–306.713) |
| Aeneas refresh occurrence collection | 96.580 (95.695–98.990) | 99.191 (98.409–102.471) |
| Aeneas refresh result encoding | 54.547 (54.069–56.322) | 51.711 (51.177–54.048) |
| Aeneas refresh result decoding | 14.460 (13.976–14.918) | 13.661 (13.310–13.957) |
| Small analysis with refresh comparison | 0.232 (0.225–0.242) | 0.260 (0.251–0.275) |
| Util analysis with refresh comparison | 14.363 (14.158–15.471) | 14.485 (13.921–14.963) |

Fresh Aeneas latency changes by +1.153 ms (+0.35%), with main and feature median absolute deviations of 2.434 and 2.047 ms. Reused Aeneas latency changes by +0.669 ms (+0.22%), with main and feature median absolute deviations of 14.383 and 10.887 ms. Refresh-scoped Aeneas latency changes by +1.363 ms (+0.47%), with main and feature median absolute deviations of 5.963 and 6.629 ms. The comparison enumerates only open documents’ hints.

Fresh Aeneas phase changes are collection +4.035 ms, encoding -2.856 ms, decoding -1.231 ms. Reused Aeneas phase changes are collection +5.387 ms, encoding +0.211 ms, decoding -1.026 ms. Refresh-scoped Aeneas phase changes are collection +2.611 ms, encoding -2.836 ms, decoding -0.798 ms. Phase medians are separate measurements, not additive savings guarantees.

The final Aeneas end-to-end changes are smaller than both builds’ median absolute deviations, with overlapping p10–p90 intervals. Decode medians are lower in all three scenarios; the fresh and refresh decode intervals are entirely below main’s. The small project’s fresh analysis is unchanged after reusing the single-file selection already made during document opening. Its development refresh scenario still adds 0.028 ms (0.232 → 0.260), beyond median absolute deviations of 0.005 and 0.006 ms. That scenario includes active-owner resolution and hint comparison, while automatic analyses reuse diagnostic owners. Further reduction could share or cache development owner selection with invalidation on configuration and membership changes. Collection still adds 4.035 ms fresh and 5.387 ms reused; these measurements do not claim zero added work in every phase or project.

A separate locked CPU-12 probe opened 50 Aeneas source documents containing 15,044 hints and 11,726 calls in 911,353 bytes, plus an unsaved JSON configuration. Automatic analysis reuses the owners already selected for diagnostics. Its comparison took 3.311 ms warm (p10–p90: 3.195–3.472; 30 measurements) and 8.788 ms initially (six measurements). The reviewer’s preceding-round measurements were 3.154 ms warm (2.996–3.339) and 8.596 ms initially; the warm intervals overlap. This is a separate comparison, not a paired end-to-end result. Development analyses resolve open-document owners together because they do not publish diagnostics; the paired refresh scenario includes that work.

The pull request's final review fixes, made after these measurements, checked that a link target's source is still current once per linked hint, normalizing its URI and, for a closed file, asking for its disk status each time. On main `5e8ede9`, the same probe took 11.676 ms warm and 19.063 ms initially. [#280](https://github.com/SaltedTan/virgil-language-server/issues/280) checks each target file once per comparison, still never across comparisons, and compares the canonical URIs that the snapshot already records. Measured on 2026-10-25 in six rotations of five builds, on CPU 12 under the benchmark lock, as medians with p10–p90:

| Build | Initial (ms) | Warm (ms) |
| --- | --- | --- |
| `90db0b8`, before the final review fixes | 9.133 (9.082–9.219) | 3.349 (3.305–3.389) |
| main `5e8ede9` | 19.063 | 11.676 |
| The check once per target file | 11.148 (11.111–11.215) | 3.770 (3.725–3.829) |
| The same, with the check removed entirely | 10.787 (10.756–10.941) | 3.420 (3.393–3.459) |
| The check once per target file, with canonical URIs (#280) | 11.002 (10.928–11.081) | 3.533 (3.474–3.605) |

The check now costs about 0.1 ms warm and 0.2 ms initially. The rest of the initial difference from `90db0b8` is another of the final review fixes: so that positions are correct after CR line breaks, each target file's analyzed text gets a line-start table, built once per content and reused by later comparisons.

Comparing canonical URIs also shortens every request answered from an analysis, since each checks that every source of the analysis is still current. In a paired Aeneas comparison (`scripts/bench-lsp.py --project aeneas --inlay-refresh --blocks 3 --operations 5 --warmups 2 --cpu 12`), definition went from 1.693 to 0.217 ms, hover from 1.639 to 0.195 ms, and references from 2.264 to 0.788 ms; fresh, reused, and refresh-scoped analyses stayed within noise.

The second round’s locked, pinned collection ablation used six alternating blocks and five measured operations after two warmups in a standalone 1 GiB probe. Before the final shortcuts, collection medians were 89.218 ms with both hint kinds, 88.488 without local hints, 85.733 without parameter hints, and 85.017 without either. Most added work is parameter eligibility, redundant-name checks, and writing argument label IDs; rendering and complete resolved-type validation add less. Skipping zero-argument eligibility and reusing the already computed argument count were cheap reductions. A second ablation measured 89.289, 88.124, 84.689, and 84.047 ms respectively, so those shortcuts did not establish a separate timing gain. The independent reviewer measured only about 0.24 ms saved by omitting the extra eligibility pass, within noise. Redundant-name checks and label writes already share the argument encoding loop, so merging eligibility into it alone cannot promise to remove the remaining collection cost. Further reduction would need cheaper eligibility or suppression checks, or less label-writing work, while preserving the same hints.

The second round’s separate locked decoder ablation, also six alternating blocks with two warmups and five measured decodes, initially measured 14.339 ms for main and 16.276 ms for the feature. Omitting hint records and argument labels reduced the feature to 15.335 ms; retaining full-width occurrence coordinates instead measured 16.154 ms. Instrumented phase medians put 1.023 ms of the difference in call validation, 0.413 ms in occurrence decoding/checking, and 0.375 ms in local hints. Hoisting label checks out of unlabeled calls alone was within noise and was not retained.

The final decoder walks the already validated occurrence and builtin spans as callees advance. A backward callee uses the original binary lookup, and every target still must match the same start or end. That removes repeated binary searches without weakening validation. The standalone decoder then measured 13.422 ms against main’s 14.404 ms; the table above is the final real-worker comparison. Duplicate parameter spans remain eliminated, and local hints, UTF-8 labels, declaration references, and allocation limits retain their strict checks.

Fresh Aeneas worker allocation is 193,161,456 → 193,830,360 bytes, and reused allocation is 193,008,032 → 193,676,872 bytes at the median. Every measured worker transaction in the final comparison collected zero times in both builds, excluding the explicit final collection; its median and p10–p90 are all zero. Worker caches and prepared buffers are not retained by snapshots.

The decoded-allocation and retained-charge figures below come from the second round’s six size-probe runs per project and build using identical relative paths without editor provenance. Round three changes refresh bookkeeping only; the decoder, wire records, and snapshot charges are unchanged. Both charges are constant, so their medians and p10–p90 are the listed value. Wire sizes are the fresh-analysis worker probes’ medians, with identical p10 and p90.

| Project | Wire bytes, main → hints | Decoded allocation, main → hints | Retained charge, main → hints |
| --- | ---: | ---: | ---: |
| Small | 614 → 634 | 5,353 → 5,450 | 6,713 → 6,810 |
| Util | 172,674 → 179,848 | 756,219 → 738,632 | 762,224 → 744,637 |
| Aeneas | 4,054,897 → 4,258,483 | 13,311,167 → 12,782,964 | 13,346,797 → 12,818,594 |

Wire version 13 carries parameter labels with call arguments and stores separate local hint records. No parameter span is repeated or validated twice. A requested file’s cursor merges its local and argument hints lazily. Strict UTF-8 validation remains cached by string and hint kind, and the existing call-array charge includes every label byte. Packing compiler line/column coordinates when both fit 16 bits saves four actual bytes per occurrence, with full-width fallback. Aeneas allocation and retained charge remain below the unchanged 80% guard for the 16 MiB result budget. Frame and admission limits are unchanged. Retained charge is 90,007 bytes below round 1’s 12,908,601 bytes.

The second round’s link-cost probe ran under the same lock and CPU affinity, with one warmup and three measured unchanged-snapshot requests per case. Each request caches validated target text/maps and declaration locations; later requests still check freshness. Target maps now also use definition’s current-text mapping for substituted files. “Before” is the first review’s uncached implementation; “after” is the second round’s implementation. Round three leaves request and target mapping unchanged.

| Hints | Target padding | Before, median ms | After, median ms |
| ---: | ---: | ---: | ---: |
| 100 | 0 bytes | 1.556 | 0.570 |
| 100 | 262,144 bytes | 211.541 | 2.969 |
| 200 | 262,144 bytes | 433.157 | 3.316 |
| 400 | 262,144 bytes | 866.187 | 4.329 |

## Implementation navigation

Measured on 2026-10-25 against main `5e8ede9`, this change's base, on the Intel Core i7-14700KF reference host with pinned Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5` and the default 384 MiB worker heap. Both candidates were built as for [type-definition navigation](#type-definition-navigation): in this physical checkout, in `build/base-r2` and `build/next-r2`, with `BENCH=1 all bench-metadata` and identical generated BuildInfo text, from main's extracted sources and then from the restored ones, whose hashes both build manifests matched. The comparison held the shared benchmark lock after a read-only process check found no other benchmark, ran on P-core hardware thread 12, and used the same command: six alternating process blocks, two warmups, and five measured operations per block for all three pinned projects, with every semantic check passing and no samples removed.

Times are **median (p10–p90), milliseconds**, over 30 measured operations per build and scenario.

| Measurement | Main | With implementation parents |
| --- | ---: | ---: |
| Small fresh analysis | 0.439 (0.428–0.521) | 0.431 (0.423–0.453) |
| Small reused analysis | 0.141 (0.137–0.154) | 0.142 (0.132–0.149) |
| Util fresh analysis | 15.115 (14.951–15.457) | 15.294 (15.149–15.888) |
| Util reused analysis | 13.185 (13.037–13.380) | 13.417 (13.288–14.058) |
| Aeneas fresh analysis, end to end | 329.047 (327.128–333.609) | 334.568 (331.495–340.380) |
| Aeneas fresh occurrence collection | 118.317 (117.480–121.031) | 119.981 (119.040–124.452) |
| Aeneas fresh result encoding | 57.441 (56.675–58.999) | 57.775 (57.127–60.045) |
| Aeneas fresh result decoding | 15.432 (15.327–15.575) | 16.013 (15.910–16.431) |
| Aeneas reused analysis, end to end | 296.342 (272.940–319.611) | 297.745 (276.789–306.647) |
| Aeneas reused occurrence collection | 99.868 (98.588–103.762) | 101.675 (100.882–105.864) |
| Aeneas reused result encoding | 51.672 (50.960–55.552) | 51.673 (51.099–55.579) |
| Aeneas reused result decoding | 13.525 (13.185–15.265) | 13.955 (13.562–15.985) |

The Aeneas fresh analysis median is 5.521 ms (1.68%) higher and the reused one 1.403 ms (0.47%) higher, with overlapping intervals; small and util medians differ by at most 1.8%. This comparison establishes no measurable end-to-end latency cost. Result encoding, which also drops the parents on any inheritance cycle, is 0.335 ms higher fresh and 0.001 ms higher reused at the median, with overlapping intervals, and decoding, which reads one more integer per declaration and checks the parents' kinds and that they form no cycle, is 0.581 ms higher and 0.430 ms higher, with disjoint intervals fresh and overlapping ones reused. A pinned decode of the same Aeneas result inside one process, 40 times in each of six interleaved runs per build, takes 15,579 µs on main and 15,828 µs with the parents (+1.6%), the median of the runs' medians, for reading, checking, and keeping one more integer per declaration; without the cycle check it takes 15,852 µs, so the check costs nothing measurable.

Every measured worker transaction collected zero times in both candidates, excluding the explicit final collection. The server collected once in each Aeneas semantic-diagnostic operation and at most once in an Aeneas rename in both candidates, and main's also at most once in an Aeneas completion miss and at most once in an Aeneas reused analysis. Aeneas fresh worker allocation changes from 193,831,072 to 193,930,120 bytes (+0.05%), for the collector's parent pairs, the encoder's links and its cycle check's byte for each, and the larger result, and the server's from 21,499,920 to 21,754,336 (+1.18%), mostly the decoded parents, one integer per declaration, and the cycle check's byte per declaration. A reused Aeneas analysis ends 22,152 bytes short of a worker collection, at 201,326,592 bytes, half the worker heap, where main's ends 165,512 short; of the 143,360-byte difference, 99,048 is allocated during the analysis and 44,312 is held from the one before, about the result's 44,316 more bytes. An earlier encoder, which also kept a parent and a cycle-check byte for every declaration, 210 KB more, made each reused Aeneas analysis collect, adding 148 ms to its encoding.

The following sizes come from six size-probe runs per project and build, as for type-definition navigation, and a seventh run of the change that also queries every class and method. Every size and charge is constant across those runs. Wire lengths from the benchmark's worker probes, which also encode collection time, are 4,258,483 → 4,302,799 for Aeneas and constant across all its measured samples.

| Project | Wire bytes, main → parents | Decoded allocation | Retained charge | Reverse index |
| --- | ---: | ---: | ---: | ---: |
| Small | 634 → 645 | 5,450, unchanged | 6,810, unchanged | none: no parents |
| Util | 179,847 → 182,481 | 738,632, unchanged | 744,637, unchanged | 276 |
| Aeneas | 4,258,481 → 4,302,797 | 12,782,964, unchanged | 12,818,594, unchanged | 5,372 |

The wire sends one parent per declaration: one byte for each of Aeneas's 42,007 declarations, and up to two more for each of the 1,311 that have a parent, 44,316 bytes (1.04%) in all. The integer fits within the conservative 128-byte declaration charge, so decoded allocation and retained charge are unchanged. The first implementation query adds the reverse index, 128 bytes and 4 for each declaration that has a parent, so a queried Aeneas snapshot retains 12,823,966 bytes, within the 80% guard (13,421,772) that `OccurrenceCoverage` checks after decoding. A first version kept an offset for every declaration, 173,408 bytes for Aeneas, which exceeded that guard before main's compact occurrence coordinates and would now leave 429,770 bytes; the sparse index leaves 597,806. In one unpinned probe run, the first Aeneas query, which builds the index, took 140 µs, and each later class or method query at most 78 µs, including the 162 implementations of `Opcode` in 52 µs; 336 of the 7,675 classes and methods have implementations.

## Override-family rename

Measured on 2026-10-26 against main `5e28701`, this change's base, on the Intel Core i7-14700KF reference host with pinned Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5` and the default 384 MiB worker heap. Both candidates were built in this physical checkout, in `build/base-rf` and `build/next-rf`, with `BENCH=1 all bench-metadata`: main's from its own commit, and the change's with the same commands and main's generated BuildInfo text, so that the revision string doesn't move the layout. The comparison held the shared benchmark lock, ran on P-core hardware thread 12, and used six alternating process blocks, two warmups, and five measured operations per block for the Aeneas project, with every semantic check passing and no samples removed. The benchmark's rename renames a field, so it measures what the change adds to every rename: the family search, which a field skips, the merged references, and the comparison of every method's overridden method in the preflight.

Times are **median (p10–p90), milliseconds**, over 30 measured operations per build and scenario.

| Measurement | Main | With override families |
| --- | ---: | ---: |
| Aeneas fresh analysis | 322.881 (319.342–329.351) | 323.366 (319.267–330.549) |
| Aeneas reused analysis | 282.422 (276.227–300.292) | 282.154 (275.657–301.309) |
| Aeneas references | 0.783 (0.752–0.821) | 0.797 (0.755–0.817) |
| Aeneas prepareRename | 16.715 (16.569–16.875) | 16.712 (16.576–17.070) |
| Aeneas rename | 327.546 (314.733–329.479) | 328.140 (314.052–331.217) |

The rename median is 0.594 ms (0.18%) higher, and every interval overlaps, so the change has no measurable cost. A first version, which matched a single declaration's name token through the same loop as a family's and searched its one or two indexes by bisection, made Aeneas references take 1.109 ms (1.081–1.143) against main's 0.793 (0.749–0.816), with disjoint intervals; matching one declaration as before restored them. Every worker transaction collected zero times in both candidates, and the server collected once in 6 of the 30 renames and in 12 of the 30 reused analyses in each, so the change keeps the reused Aeneas analysis within its worker heap margin: the worker sends nothing new, and its executable is the same size as main's.

Main refuses every method that shares its name with another, which on Aeneas includes the 65 methods named `render` and the 62 named `emit`. With the change, each family renames whole. The largest family among Aeneas's 6,806 methods, found by asking for the implementations of each, is `range` from `vst/Vst.v3`, line 768, and the 17 methods that override it: renaming it takes 339.5 ms, from 337.9 to 345.5, with 48 edits in 4 files, the median of 10 requests after the first, from a throwaway stdio client pinned to the same thread, and prepareRename 19.70 ms. The largest `emit` family, from `jvm/JvmClassfile.v3`, line 54, with 15 overrides, takes 332.5 ms for 19 edits in one file, and the largest `render` family, from `ssa/Ssa.v3`, line 189, with 6 overrides, 329.3 ms for 8 edits in 5 files. As for any rename, the preflight's analysis is most of that time.

#287 makes the two scans of a family's references linear or n log n in the family's size. On a generated program, one root `class A { def m() -> int { return 0; } }` and *n* classes `class Ci extends A { def m() -> int { return m(); } }` in one file, built from main `27c57e9` in `build/base-287` and from the change in `build/next-287` with the same generated BuildInfo, and pinned to hardware thread 12 under the shared benchmark lock, times are medians in milliseconds of five prepareRename requests and three renames.

| Family | prepareRename, main | prepareRename, with #287 | rename, main | rename, with #287 |
| ---: | ---: | ---: | ---: | ---: |
| 2,001 | 25.1 | 6.7 | 98.3 | 42.2 |
| 4,001 | 96.9 | 12.9 | 350.0 | 103.8 |
| 8,001 | 177.4 | 27.0 | 641.0 | 183.4 |

The renames edit 4,001, 8,001, and 10,000 tokens. At 8,001 methods the program gives a call to only every fourth class's method, and skips the last of those, because 8,001 names leave room for at most 1,999 uses under the 10,000-edit bound; a call in every other class's method would be 4,000 uses and 12,001 edits, which rename refuses. With the change, prepareRename takes 1.9 and 2.1 times as long and rename 2.5 and 1.8 times as long as the family doubles, against main's prepareRename taking 3.9 times as long from 2,001 to 4,001 methods. Rename also includes the preflight's analysis of the edited program, whose cost grows with the program; prepareRename has no preflight.

A paired Aeneas run against main `27c57e9`, with six alternating process blocks, two warmups, and five operations per block, held the shared lock and ran on hardware thread 12 with identical BuildInfo. `references` takes 0.804 (0.786–0.837) ms against 0.795 (0.758–0.826), `prepareRename` 16.893 (16.835–17.400) against 16.942 (16.803–17.347), and `rename` 329.368 (312.687–332.152) against 328.962 (313.642–333.996), all with overlapping intervals: single-declaration requests are unchanged.

## Type hierarchy

Measured on 2026-10-26 against main `06672f2`, this change's base, on the Intel Core i7-14700KF reference host with pinned Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5` and the default 384 MiB worker heap. Both candidates were built in this physical checkout, in `build/base-th` and `build/next-th`, with `BENCH=1 all bench-metadata`: main's from its `src/` checked out over the change, and the change's from its own, both with the same uncommitted documentation edit, so that their generated BuildInfo text is identical. The comparison held the shared benchmark lock, ran on P-core hardware thread 12, and used six alternating process blocks, two warmups, and five measured operations per block for the Aeneas project, with every semantic check passing and no samples removed. The change adds only new requests, which the benchmark doesn't send, so nothing it measures should change.

Times are **median (p10–p90), milliseconds**, over 30 measured operations per build and scenario.

| Measurement | Main | With type hierarchy |
| --- | ---: | ---: |
| Aeneas fresh analysis | 342.056 (333.185–359.263) | 344.228 (335.304–358.922) |
| Aeneas reused analysis | 290.349 (281.569–307.013) | 297.439 (279.558–314.865) |
| Aeneas definition | 0.199 (0.169–0.238) | 0.190 (0.170–0.229) |
| Aeneas hover | 0.187 (0.152–3.507) | 0.187 (0.154–0.207) |
| Aeneas references | 0.821 (0.790–0.861) | 0.812 (0.793–1.041) |
| Aeneas prepareRename | 24.137 (16.828–25.869) | 24.153 (16.783–25.691) |
| Aeneas rename | 338.251 (324.264–343.694) | 337.422 (325.652–345.780) |
| Aeneas completion hit | 5.682 (2.664–6.250) | 5.654 (2.679–6.301) |
| Aeneas completion miss | 119.582 (112.410–132.488) | 119.431 (115.852–132.405) |
| Aeneas syntax diagnostic | 1.465 (0.380–1.548) | 0.949 (0.379–1.862) |
| Aeneas semantic diagnostic | 528.799 (512.913–544.191) | 526.560 (508.127–544.301) |

The fresh analysis median is 2.172 ms (0.64%) higher and the reused one 7.090 ms (2.44%) higher, and every interval overlaps, so the change has no measurable cost. The worker executables are the same size, 888,000 bytes, since the worker sends nothing new; the server's grows from 808,128 to 819,352 bytes. Every measured worker transaction collected zero times in both candidates, and the server collected in the same operations in each: once in every semantic diagnostic, in 6 of the 30 renames, and in 12 of the 30 reused analyses.

With the Aeneas project, from the name of class `Type` in the open `types/Type.v3`, prepareTypeHierarchy takes 0.49 ms and subtypes 1.51 ms, the medians of 20 requests after a first one, once the project's analysis had completed, from a throwaway stdio client of the normal build, pinned to the same thread under the same lock. Subtypes lists `Type`'s 15 direct subclasses, in 11 files, each of which it reads again and checks against its captured length and hash, or finds open, and maps and parses for the declarations' extents once. prepareTypeHierarchy parses only `Type.v3`.

## Call hierarchy

Measured on 2026-10-26 against main `71c6ec4`, this change's base, on the Intel Core i7-14700KF reference host with pinned Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5` and the default 384 MiB worker heap. Both candidates were built in this physical checkout, in `build/base-ch` and `build/next-ch`, with `BENCH=1 all bench-metadata`: main's from its `src/` checked out over the change, and the change's from its own, both with the same uncommitted documentation edit, so that their generated BuildInfo text is identical. The comparison held the shared benchmark lock, ran on P-core hardware thread 12, and used six alternating process blocks, two warmups, and five measured operations per block for the Aeneas project, with every semantic check passing and no samples removed. The change adds only new requests, which the benchmark doesn't send, and nothing new crosses the worker boundary, so nothing it measures should change.

Times are **median (p10–p90), milliseconds**, over 30 measured operations per build and scenario.

| Measurement | Main | With call hierarchy |
| --- | ---: | ---: |
| Aeneas fresh analysis | 354.101 (342.273–370.561) | 351.883 (346.074–366.668) |
| Aeneas reused analysis | 303.550 (287.827–323.907) | 304.261 (289.090–324.875) |
| Aeneas definition | 0.195 (0.175–0.226) | 0.197 (0.176–0.250) |
| Aeneas hover | 0.171 (0.156–0.205) | 0.179 (0.160–0.220) |
| Aeneas references | 0.853 (0.811–4.203) | 0.870 (0.818–3.820) |
| Aeneas prepareRename | 24.526 (17.451–27.191) | 24.349 (17.499–27.417) |
| Aeneas rename | 344.122 (330.591–359.198) | 344.423 (339.867–393.378) |
| Aeneas completion hit | 5.861 (2.835–6.402) | 5.845 (2.893–6.809) |
| Aeneas completion miss | 114.043 (111.828–130.196) | 116.077 (111.245–133.584) |
| Aeneas syntax diagnostic | 0.400 (0.386–1.535) | 0.409 (0.387–1.597) |
| Aeneas semantic diagnostic | 524.015 (513.778–542.719) | 521.068 (513.685–536.605) |

The fresh analysis median is 2.218 ms (0.63%) lower and the reused one 0.711 ms (0.23%) higher, and every interval overlaps, so the change has no measurable cost. The worker executables are the same size, 888,200 bytes, and differ in 64 bytes, since the worker sends nothing new; the server's grows from 821,872 to 837,712 bytes. Every measured worker transaction collected zero times in both candidates, and the server collected in the same operations in each: once in every semantic diagnostic, in 6 of the 30 renames, and in 12 of the 30 reused analyses.

With the Aeneas project, from the name of `puts` in the open `lib/util/StringBuilder.v3`, `def puts(s: Array<byte>) -> this`, prepareCallHierarchy takes 0.42 ms, incomingCalls 76.7 ms, and outgoingCalls 0.58 ms, the medians of 20 requests of each after a first one, once the project's analysis had completed, from a throwaway stdio client of the normal build, pinned to the same thread under the same lock. The incoming calls are 655 calls from 257 callers in 76 files, about 197 KiB of JSON; outgoingCalls lists `puts`'s one callee, `acquire`. A references request for `puts` from the same position, which reads and maps the same 76 files again for its 661 uses, takes 17.1 ms, so most of incomingCalls' time is parsing those files for their callers' extents, once each. prepareCallHierarchy and outgoingCalls parse only `StringBuilder.v3`. Items later gained a `container` member naming their enclosing declarations, which each request checks. Measured again in six alternating blocks of 20 requests, with every build made the same way, incomingCalls takes 80.1 ms against 76.5 ms before, and its reply grew from 201,652 to 208,744 bytes; with the check turned off it takes 79.9 ms, so the time is the larger reply's, not the check's. prepareCallHierarchy and outgoingCalls are unchanged.

## Semantic tokens

Measured on 2026-10-27 and 2026-10-28 on the Intel Core i7-14700KF reference host, with pinned Virgil `57318d37e9fcca8de6fda5f9cd82d669479f49ff` and the default 384 MiB worker heap, holding the shared benchmark lock, on P-core hardware thread 12.

**Requests.** A throwaway stdio client of the normal build opened `vendor/virgil/aeneas/src/vst/Verifier.v3`, the largest of the Aeneas sources at 106,419 bytes, and the benchmark corpus's Aeneas project as an unsaved project file. In each of six processes, it ran six rounds of each of two kinds, the first of each a warmup. A round asks for a project analysis of the file through `virgil-lsp/analyze`, which installs a new snapshot, then sends six requests of its kind on that snapshot. A request's time runs from writing it to reading the last byte of its reply. Times are **median (p10–p90, maximum), milliseconds**:

| Request | First on a snapshot (30) | Later (150) |
| --- | ---: | ---: |
| `textDocument/semanticTokens/full` | 4.455 (2.257–5.418, 5.863) | 2.178 (2.114–2.868, 22.397) |
| `textDocument/hover` | 0.711 (0.676–3.956, 4.016) | 0.639 (0.609–2.312, 4.331) |

Each reply holds the file's 8,164 tokens, 40,820 integers, in 86,845 bytes of JSON. A token request walks every declaration site and occurrence in the file, checks that each spells its declaration's name, converts each position to UTF-16, and encodes the reply, allocating about 1.9 MB in the server; hover looks up one name. The first token request on a snapshot took 2.3 to 2.7 ms about half the time and 3.8 to 5.9 ms otherwise, and hover's first requests had a tail too; this measurement doesn't show why. Twelve of the 150 later token requests took 19 to 23 ms. A copy of the server that recorded its collections at each request found a collection in two of the four such requests that it saw, so collections explain part of that tail, not all of it. An earlier run of the same client on the change's first commit measured the same medians for later requests, 2.184 ms for tokens and 0.635 ms for hover.

**Analyses.** The worker also reads three facts from each declaration's syntax ([Semantic tokens](architecture.md#semantic-tokens)). The change's base, main `620f89d2`, and the change were built in `build/base-313` and `build/fix1-313` with `BENCH=1 all bench-metadata`: main's from its `src/` checked out over the change, and both with the same uncommitted documentation edit, so that their generated BuildInfo text is identical. The comparison used six alternating process blocks, two warmups, and five measured operations per block for the Aeneas project, with every semantic check passing and no samples removed:

```sh
flock /path/to/shared/bench.lock taskset -c 12 \
  python3 scripts/bench-lsp.py \
  --exe build/base-313/virgil-lsp --exe build/fix1-313/virgil-lsp \
  --project aeneas --require-probes --blocks 6 --operations 5 --warmups 2 --cpu 12
```

Times are **median (p10–p90), milliseconds**, over 30 measured operations per build and scenario.

| Measurement | Main | With semantic tokens |
| --- | ---: | ---: |
| Aeneas fresh analysis | 341.433 (340.579–347.433) | 344.937 (342.478–347.915) |
| Aeneas fresh result encoding | 55.205 (54.825–56.603) | 55.768 (55.312–59.025) |
| Aeneas fresh result decoding | 30.924 (27.867–31.139) | 31.536 (28.526–31.811) |
| Aeneas reused analysis | 303.261 (293.409–322.214) | 305.178 (295.516–322.078) |
| Aeneas reused result encoding | 52.844 (52.487–54.157) | 53.422 (52.870–54.394) |
| Aeneas definition | 0.201 (0.168–0.213) | 0.181 (0.164–0.210) |
| Aeneas hover | 0.158 (0.148–0.201) | 0.175 (0.149–3.435) |
| Aeneas semantic diagnostic | 514.326 (495.177–519.574) | 513.415 (494.812–516.637) |

The fresh analysis median is 3.504 ms (1.0%) higher and the reused one 1.917 ms (0.6%) higher, and the intervals overlap. Every measured worker transaction collected zero times in both builds, and the server collected in the same operations in each. The `RESULT` payload, 4,379,198 bytes, and the worker's allocation, 197,739,184 bytes, are the same in both ([ADR-0004](decisions/0004-analysis-worker-process.md#supported-program-size)). The first version of the change read the flag from the syntax as it encoded each declaration, which the encoder otherwise doesn't touch: encoding took 59.745 ms against main's 55.458 ms, medians of 10 `analyze --stats --bindings` runs of each, and a copy of it that never read the flag took 55.479 ms. A field for the flag restored the time but made every worker declaration 8 bytes larger, 344,568 bytes in all. The worker now chooses a subclass with no fields when it makes a flagged declaration, while its syntax is in the cache, as the snapshot does when it decodes one: 55.576 ms against main's 56.150 ms, with the same allocation.

## Scheduled comparison

The [Benchmarks workflow](../.github/workflows/benchmarks.yml) runs on Mondays at
04:23 UTC on `ubuntu-24.04` and `macos-15`, in separate jobs. Each compares current
`main` with the first commit in its first-parent history whose committer date is
at least seven days before the run. If no such commit exists, it uses the nearest
reachable release tag (`v[0-9]*`). Manual dispatch accepts `base_ref` and `new_ref`;
the defaults are `last-week` and `origin/main`. `last-week` selects from
`new_ref`'s first-parent history with the same date and release-tag fallback.
A pull request that changes the workflow, its benchmark scripts, their build
inputs, or benchmark tests compares its head SHA with its base SHA, using the
pull request's benchmark tooling.
When a PR changes the Virgil pin, its refs cannot share the tooling corpus:
the comparison is skipped with a notice in the job summary and `refs.json`.
Pin updates need separate measurements using each pin's reviewed corpus, as
in the [pinned revision comparison](#pinned-virgil-revision-comparison-2026-10-27).

[`scripts/bench-compare.py`](../scripts/bench-compare.py) builds both refs with
`BENCH=1` and `bench-metadata`, in fresh equal-length `base` and `next` checkouts
with identical `build/bench` directories. Generated BuildInfo uses full commit
SHAs for equal-length revision strings; tracked sources are unchanged. Both
refs must have benchmark probes and metadata support (introduced in
[#237](https://github.com/SaltedTan/virgil-language-server/pull/237)), and must use
the same pinned Virgil as the tooling checkout's corpus. An explicit ref without
them fails the comparison rather than produce misleading results. A week-old
baseline without them, such as the first scheduled runs' (benchmark support
landed on 2026-10-24) or one from before a Virgil pin bump, moves forward along
the newer ref's first-parent history to the first commit that has both; a notice
and the job summary say which commit was requested and which was used, and
`refs.json` records the note. If no commit up to the newer build has both, the
comparison fails.

One client invocation measures all three projects, alternating build order by
process block, with three blocks, two warmups, and five measured operations per
scenario per block. Linux uses `taskset` on the first allowed CPU, inherited by
the servers and workers. macOS has no `taskset`; it explicitly uses `--cpu none`.
Its Apple Silicon host runs the x86-64-darwin executables under Rosetta 2. Host
details and actual affinity are recorded in the JSON. Each comparison uses one
runner; measurements from different runs or operating systems are not paired.

The job summary shows each case's median and p10–p90 in milliseconds, the relative
median change (`new/base - 1`, positive means slower), and total server/worker GC
counts across measured transactions. Warmups and explicit final collections are
excluded. `—` means no instrumented transaction, as for a cache hit, rather than
a measured zero. Raw samples retain the GC counts for each transaction.

[`scripts/bench-report.py`](../scripts/bench-report.py) flags either direction
only when p10–p90 intervals do not overlap **and** the absolute median change is
greater than **25%**. The Linux reference's fresh/reused analysis intervals span
roughly 6–18% of their medians, while fast requests can have much wider tails
(small completion-hit spans about 45%, and Aeneas hover has an outlier tail).
The 25% threshold is deliberately larger than the ordinary analysis spread;
requiring disjoint intervals also avoids flagging those noisy tails. It is an
advisory heuristic, not a statistical significance test. Flags produce warning
annotations, never a failed timing check. Build or benchmark correctness failures
still fail the job. Rerun a flagged comparison and inspect its blocks and GC
behavior before calling it a regression or improvement.

The Linux job's `--cpu auto` pins the server and the worker to one CPU, so work
that the worker does while the server answers competes with that answer. Since
[#290](https://github.com/SaltedTan/virgil-language-server/pull/290), the worker
collects after sending each result, and `completion_hit` is measured immediately
after `completion_miss`, whose result the worker has just sent. On one CPU,
Aeneas `completion_hit` therefore takes about 5.8 ms rather than 2.7 ms; with the
server and the worker on separate cores, as editors run them, it is unchanged.
The first scheduled run flagged this against a baseline from before #290
([#294](https://github.com/SaltedTan/virgil-language-server/issues/294)); ADR 0004
records the measurements and why the collection stays.

Download `benchmarks-ubuntu-24.04` or `benchmarks-macos-15` from the workflow run's
artifacts (retained for 90 days). Each contains `comparison.json`, resolved
`refs.json`, and `summary.md`; macOS also contains `baseline.md`, the newer
build's reference table. Failed benchmarks retain `comparison.json.failure.json`
when available. Per-case budgets should be proposed separately only after
several weeks of scheduled results establish runner variability.

To reproduce the build and measurement steps locally, from a physical checkout
with full history, tags, and the pinned submodule initialized:

```sh
python3 scripts/bench-compare.py --base-ref BASE_SHA --new-ref NEW_SHA \
  --work-dir "$PWD/build/bench-checkouts" --output-dir "$PWD/build/bench-results"
python3 scripts/bench-report.py build/bench-results/comparison.json --annotations > build/bench-results/summary.md
python3 scripts/bench-report.py build/bench-results/comparison.json --baseline > build/bench-results/baseline.md
```

The work directory must not exist; it is removed after the run. The output
directory remains. `test/benchmarks/test_report.py`, included in `make test`,
checks flag boundaries, warmup exclusion, server/worker GC accounting, ref
selection, and baseline rendering. Its `linux-reference.json` fixture retains
the latency summaries and host/build identifiers from the recorded Linux
`compare-default.json` below (raw samples and other metrics omitted); the
expected table matches the documented current default-heap baseline exactly.

## Reference baseline

Measured on 2026-10-25 at implementation revision
`ac815fab2c0396accb42b902841ec5e6b82aa0d1`, against baseline `6406edc`, with
Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5`. The host was an Intel Core
i7-14700KF (28 logical CPUs), Linux `7.0.0-38-generic` x86-64/glibc 2.43, Python
3.14.4, pinned with `taskset` to CPU 12; its readable governor was `powersave`.
CPU 12 and sibling 13 were idle when selected. Other agents ran on the host;
local verification used CPUs 0–3. The default worker reserved 402,653,184 bytes
(384 MiB); the server used its default 200 MiB heap. These are reference
measurements, not budgets.

Both builds used this physical checkout and equal-length build directories:
`build/base-0` and `build/r2-n00` for 384 MiB, and `build/base-1` and
`build/r2-n01` for the 1 GiB control. Each was built at its own clean revision
with `BENCH=1 all bench-metadata`; BuildInfo revision strings have equal lengths.
All three pinned projects ran in three process blocks with two warmups and five
measured operations per scenario per block, reversing build order each block:

```sh
python3 scripts/bench-lsp.py \
  --exe build/base-0/virgil-lsp --exe build/r2-n00/virgil-lsp \
  --require-probes --error-result --blocks 3 --operations 5 --warmups 2 --cpu 12 \
  --output build/bench-results/compare-default.json
```

Repeat with the `-1` and `r2-n01` executables for the 1 GiB control. Retained
`compare-default.json` and `compare-1g.json` contain build and corpus hashes,
individual samples, metadata, and stderr traces. Every operation passed the
semantic/version and complete-probe checks; no outliers were removed. Earlier
dirty-build runs also passed but are excluded from these tables.

The current default-heap baseline follows. Each cell is **median (p10–p90),
in milliseconds**, from 15 measured operations.

| Scenario | small | util | Aeneas |
| --- | ---: | ---: | ---: |
| `fresh_analysis` | 0.453 (0.431–0.510) | 15.952 (15.216–16.337) | 337.747 (321.608–345.821) |
| `reused_analysis` | 0.139 (0.132–0.150) | 14.234 (13.890–14.758) | 304.965 (294.083–323.792) |
| `syntax_diagnostic` | 0.071 (0.068–0.079) | 0.293 (0.274–0.316) | 0.411 (0.398–0.442) |
| `semantic_diagnostic` | 200.658 (200.556–200.676) | 214.887 (214.470–215.844) | 527.585 (507.645–535.522) |
| `completion_miss` | 0.679 (0.653–0.794) | 8.670 (8.124–9.304) | 142.837 (134.916–152.189) |
| `completion_hit` | 0.473 (0.440–0.655) | 0.740 (0.716–0.759) | 2.747 (2.684–2.861) |
| `definition` | 0.073 (0.069–0.079) | 0.383 (0.368–0.391) | 1.794 (1.596–1.949) |
| `hover` | 0.067 (0.065–0.071) | 0.375 (0.362–0.386) | 2.037 (1.750–15.870) |
| `references` | 0.074 (0.070–0.078) | 0.414 (0.396–0.428) | 2.325 (2.204–2.497) |
| `prepareRename` | 0.173 (0.165–0.180) | 1.999 (1.957–2.050) | 18.746 (18.565–19.045) |
| `rename` | 0.455 (0.446–0.482) | 18.278 (17.711–18.829) | 343.376 (330.770–357.744) |

The same-run Aeneas comparison follows, in **median (p10–p90), ms**. The
1 GiB control separates encoding work from the default heap’s collection boundary.

| Scenario | 384 MiB before | 384 MiB after | 1 GiB before | 1 GiB after |
| --- | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 351.650 (347.513–360.174) | 337.747 (321.608–345.821) | 350.670 (348.326–354.274) | 342.844 (330.431–369.509) |
| `reused_analysis` | 464.939 (451.791–481.094) | 304.965 (294.083–323.792) | 310.543 (304.851–325.272) | 303.712 (291.260–319.083) |
| `semantic_diagnostic` | 519.237 (513.444–542.921) | 527.585 (507.645–535.522) | 537.907 (523.517–547.202) | 524.096 (505.121–537.963) |
| `completion_miss` | 135.861 (130.090–145.626) | 142.837 (134.916–152.189) | 134.143 (128.375–146.213) | 144.693 (133.464–169.188) |
| `rename` | 356.950 (346.100–376.154) | 343.376 (330.770–357.744) | 350.945 (339.763–358.909) | 341.321 (332.599–433.383) |
| `error_analysis` | 324.702 (306.671–333.325) | 307.976 (298.812–324.766) | 318.268 (306.475–328.178) | 307.550 (294.346–320.230) |

Worker collections during handling were constant across all 15 measured samples
of each Aeneas scenario. The explicit final collection is excluded:

| Scenario | 384 MiB before → after | 1 GiB before → after |
| --- | ---: | ---: |
| `fresh_analysis` | 0 → 0 | 0 → 0 |
| `reused_analysis` | 1 → 0 | 0 → 0 |
| `semantic_diagnostic` | 0 → 0 | 0 → 0 |
| `completion_miss` | 0 → 0 | 0 → 0 |
| `rename` | 0 → 0 | 0 → 0 |
| `error_analysis` | 0 → 0 | 0 → 0 |

Every measured small/util worker transaction collected zero times in both builds.
No case increased its collection count. Aeneas worker phase medians at 384 MiB follow;
`adapter_us` overlaps parse/verify and is omitted. Time rows are **milliseconds**:

| Metric | Fresh before | Fresh after | Reused before | Reused after | Error before | Error after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Parse, ms | 63.976 | 64.340 | 41.453 | 42.186 | 42.407 | 43.800 |
| Verify, ms | 46.064 | 47.440 | 35.922 | 35.836 | 34.307 | 35.310 |
| Collect, ms | 110.537 | 112.530 | 92.817 | 95.166 | 96.160 | 98.636 |
| Query, ms | 0.001 | 0.001 | 0.001 | 0.001 | 0.001 | 0.001 |
| Result encode, ms | 87.432 | 70.536 | 229.007 | 65.898 | 85.889 | 69.761 |
| Final GC, ms | 3.536 | 1.597 | 23.403 | 23.020 | 21.720 | 24.199 |
| Allocated bytes during handle | 197,599,392 | 189,224,704 | 197,445,968 | 189,071,280 | 192,860,856 | 189,805,184 |
| Heap bytes after handle | 200,643,952 | 192,269,264 | 129,150,584 | 196,228,248 | 200,893,512 | 197,137,312 |
| Live bytes after final GC | 176,769 | 176,768 | 176,769 | 176,768 | 176,772 | 176,768 |
| RESULT frame bytes | 3,936,046 | 3,936,046 | 3,936,046 | 3,936,046 | 4,056,370 | 4,056,370 |

Worker declarations cache their existing keyed path/position hash, which avoids
rehashing paths in call keys and the encoder’s declaration index. RESULT encoding
now prepares its tables once, counts the same writes without a byte buffer, then
allocates one output buffer and writes into it without growth or copies. Capacity
is the exact count rounded up to eight bytes (at most seven unused bytes); frame
lengths still use only bytes written. The temporary counting writer, closures, and
encoding tables do not survive the transaction.

Power-of-two growth was rejected after it increased allocation and triggered an
extra collection in a diagnostic-bearing result. Presizing reduces transaction
allocation compared with the original library writer for both result shapes:
8,374,688 fewer bytes for a successful Aeneas snapshot, and 3,055,672 fewer
for the unresolved-name snapshot with 900 functions. Reused successful analyses
avoid the baseline’s encoding collection in every measured sample. Successful
reused post-handle heap use is higher because the baseline already collected;
fresh post-handle heap use is lower on all three corpus projects.

A separate exact reproduction of the reviewer’s worker-only error case used
three alternating blocks, two warmups, and five measurements, on CPU 12. Base,
the rejected power-of-two writer, and the presized writer encoded in
84.0, 200.5, and 68.3 ms respectively; their worker round trips were
295.3, 406.1, and 286.2 ms. All 15 samples collected zero, one, and zero times
respectively. Normalized RESULT bytes matched across the three builds.

Live-after-GC bytes never increased in any measured scenario or project. Fresh
Aeneas retained bytes went from 176,769 to 176,768; the difference is buffer
alignment, not a removed object.
Full equality checks, keyed hashing, decoder validation/allocation charges,
framing/decode budgets, heap size, and retirement policy remain unchanged.

Normalized RESULT bytes match baseline for small, util, Aeneas, type-error,
syntax-error, unresolved-name, and the 900-function error case, fresh and reused
at both heaps. Only the three elapsed-time fields and reported live bytes were
normalized. Another 21 query/occurrence cases per heap also match, fresh and
reused. The full unit/protocol suite covers scope, member, call, display,
malformed-result, and allocation-budget paths. Boundary tests check buffer
capacity for 255/256/257-byte results, frame payloads immediately below/at/above
16 MiB, and counting beyond a signed 32-bit total.

Latency intervals overlap for several fresh-analysis, semantic-diagnostic,
completion, and fast-request comparisons. Those shared-host timings do not
establish a universal latency gain. The reused successful-analysis GC reduction
and lower allocation for both success and error results are consistent. No macOS
or normal-build timing comparison was collected for these changes.

### Earlier probe observer control

These 2026-10-24 controls predate the allocation changes above and describe the
benchmark instrumentation at its introduction.

A separate three-run heap control used the original worker at `9a9e384`, the new
worker with probes disabled, and the enabled worker, with identical source paths
and matching BuildInfo in every build. All nine fresh Aeneas analyses used exactly
**200,643,960 bytes after handling, with zero collections**: instrumentation added
**0 transient bytes** at that boundary. The normal server and worker contained
neither the `BENCH ` marker nor the probe metric names checked. The earlier
baseline at `2c1ebaf` used 8 fewer heap bytes because its clean revision string is shorter than the
control's dirty revision string in HELLO. The controls used temporary worker
source copies with a single heap read before formatting the measurement; repository
and compiler sources were not modified by those controls.

The following observer check used the same source revision, with equal-length
`build/plain` (normal) and `build/bench` (probes) directories:

```sh
make BUILD=build/plain all bench-metadata
python3 scripts/bench-lsp.py --exe build/plain/virgil-lsp --exe build/bench/virgil-lsp \
  --blocks 2 --operations 3 --warmups 1 --cpu 4 --output build/bench-results/observer.json
```

Aeneas medians from six measured operations per build:

| Scenario | Normal, ms | Probes, ms | Change |
| --- | ---: | ---: | ---: |
| Fresh analysis | 350.954 | 353.146 | +0.6% |
| Reused analysis | 469.604 | 464.838 | −1.0% |
| Completion miss | 129.257 | 133.078 | +3.0% |
| Rename | 349.086 | 355.810 | +1.9% |
| Semantic publication | 529.771 | 527.332 | −0.5% |

This short control bounds the observed effect on these samples; noise and probe
syscalls remain. It establishes no sub-millisecond equivalence. See the
[macOS reference baseline](#macos-reference-baseline) for macOS measurements.

## macOS reference baseline

Measured on 2026-10-24 (22:34 UTC) by the `Compare builds (macos-15)` job of
[run 40139072206](https://github.com/SaltedTan/virgil-language-server/actions/runs/40139072206),
the pull request that added the weekly comparison, at revision
`027c8d62190f7c0a650542059410a2d2e8a1d2f3`, whose server sources are those of
`677be00`, with Virgil `7cf038fc13a5474836bec8b14c7a3e796fd07fa5`. The host was
the hosted `macos-15` runner: an Apple M1 (Virtual) with 3 logical CPUs, macOS
15.7.9, Python 3.14.7, running the x86-64-darwin server and worker under
Rosetta 2, with no CPU pinning (macOS has no `taskset`). Its load average was
5.5 when the run started (16.7 over five minutes), so other work shared the
machine. It is a different host and execution environment from the Linux
reference above; its numbers are not comparable with that baseline, and they
are a reference, not budgets.

The table is the job's `baseline.md`, which the workflow generates from the
newer build's samples in `comparison.json`:

```sh
python3 scripts/bench-report.py results/comparison.json --baseline > results/baseline.md
```

All three pinned projects ran in three process blocks with two warmups and five
measured operations per scenario per block, so each cell summarizes 15 measured
operations; warmups are excluded. The default heaps were used, and the corpus
SHA-256 was
`625735d5760c6bbe9e3ee16aadd01367e5a674cef15567773822c11d37043350`. Median
(p10–p90), in milliseconds:

| Scenario | small | util | Aeneas |
| --- | ---: | ---: | ---: |
| `fresh_analysis` | 46.438 (43.906–52.099) | 117.437 (107.836–129.663) | 1340.596 (1255.567–1699.763) |
| `reused_analysis` | 0.937 (0.832–1.034) | 42.825 (35.153–55.366) | 989.911 (924.980–1293.568) |
| `syntax_diagnostic` | 0.287 (0.259–0.364) | 1.002 (0.910–1.515) | 1.691 (1.565–2.578) |
| `semantic_diagnostic` | 203.917 (203.123–204.735) | 250.413 (245.318–259.054) | 1178.017 (1124.797–1309.753) |
| `completion_miss` | 3.209 (2.963–4.334) | 32.865 (28.763–35.843) | 480.467 (414.266–640.432) |
| `completion_hit` | 1.729 (1.535–3.037) | 3.024 (2.348–4.485) | 9.402 (7.998–18.820) |
| `definition` | 0.269 (0.245–0.332) | 1.279 (1.231–1.350) | 6.022 (4.962–69.074) |
| `hover` | 0.296 (0.235–0.537) | 1.355 (1.234–1.727) | 5.525 (4.875–10.619) |
| `references` | 0.292 (0.279–0.360) | 1.432 (1.305–1.613) | 6.936 (6.309–12.444) |
| `prepareRename` | 1.770 (1.645–2.455) | 26.103 (23.860–30.834) | 243.000 (214.236–328.126) |
| `rename` | 4.894 (4.149–6.270) | 106.033 (92.241–116.272) | 1514.359 (1391.423–1773.595) |

The wide upper ranges on Aeneas, such as `definition`'s 69 ms p90, reflect the
shared virtual host. Weekly runs on the same runner type will show how much it
varies before any budget is proposed.

## Semantics during syntax errors

[`scripts/bench-tolerance.py`](../scripts/bench-tolerance.py) measures the semantic
answers an editor receives while another file fails to parse. It imports the
existing benchmark helpers and `Session`; a separate command keeps the successful
operation timings and output of `bench-lsp.py` unchanged. The baseline below
predates [method-body fallback](architecture.md#method-body-fallback); its
comparison follows the original results. The [current-text repair comparison](#current-text-repair-comparison)
follows that. Run both modes and all states with:

```sh
mkdir -p build/bench-results
make BUILD=build/bench BENCH=1 all bench-metadata
python3 scripts/bench-tolerance.py --exe build/bench/virgil-lsp --cpu 4 \
  --output build/bench-results/tolerance.json
```

A normal executable works too, after `make all bench-metadata`; its probe arrays
are empty. `--suite repair|replay|negative|extra|heldout|session` selects a group, and
`--only TEXT` filters state IDs (session IDs with `--suite session`). Editing
sessions have a separate inventory and are included in an unfiltered run. These
filters appear in the recorded command. `--work-dir`
chooses the parent of disposable source copies (default `build/bench-results`);
use scratch storage on machines with a small shared `/tmp`. Output directories
must already exist. No fixture, vendor source, or real project file is edited.
The driver rechecks input hashes and removes its copies. With a file output,
failed runs preserve partial results and a failing session's transcript in
`<output>.failure.json`.
If session teardown also fails, `failed_record.cleanup_errors` records those
errors while preserving the original exception and session evidence.

The main corpus has 185 states, each measured in a fresh server session in both
warm and cold modes:

- The [repair corpus](decisions/0006-incomplete-code-repair.md#cases): 31 fixture
  cases and 14 Aeneas sites. Broken fixtures retain their exact text with only the
  cursor marker removed. Their predecessors are explicitly labeled synthetic.
  The reviewed [seed manifest](../test/benchmarks/tolerance-seeds.json) pins fixture
  hashes and uses hand-checked insertions where straightforward. Other seeds use
  the first parseable repair ladder candidate, completing a remote missing
  initializer when necessary. Parseable seeds may still have type errors. Aeneas
  predecessors are the original pinned upstream files.
- 140 byte insertion states at six sites: the Aeneas component member, nested
  call, condition, and for header, plus `Arrays.` accesses in `StringBuilder.v3`
  and `Vector.v3`. Delete the line suffix and reinsert its ASCII bytes one at a
  time. Each state's predecessor is the most recent parseable version *before*
  that state, starting with the upstream original. The CLI parser validates this
  history; measured semantic answers always use the protocol.

Warm sessions open and analyze the predecessor, then send the current text as an
unsaved full-text `didChange`. Cold sessions open the current text immediately;
they have no explicitly warmed predecessor, although the disposable disk copy
ordinarily still contains the original program. Sequential opens can capture
that disk predecessor for a later-opened file during an earlier analysis.
Closed-file controls instead put the broken text on disk. Each replay state gets its own session, seeded from the sequence's
history; timing does not model user think time.

Definition, plaintext hover, and references with declarations are queried at a
resolved observer call and fixed bound sites in untouched `Paths.v3`, `Char.v3`,
and `StringBuilder.v3`. The edited file is excluded even at a replay's final
restored byte: there are exactly 400 real-file query sites across the 140 states,
plus 140 observer sites. The observer also contains a deliberate type error.
Semantic diagnostic publications in observed intact files are compared as sets,
preserving URI, range, code, message, severity, and source. Parser diagnostics are
excluded. Queries wait for the current analysis, then one snapshot inspection
checks configuration, membership, and document freshness. There are no sleeps or
snapshot polling loops.

Oracles use the same protocol on the parseable predecessor or the original
program with the intended semantic edit retained and only the syntax error
removed. JSON retains these answers, source overlays, configuration, and each
state's answers and scores. Disposable URI prefixes become `project:///`; target
paths and every range remain significant. Hover text and ranges compare exactly;
reference and diagnostic items compare exactly, ignoring set ordering. `available` counts a nonempty
answer or an oracle-matching legitimate empty answer. `correct` counts an exact
match. Missing a nonempty oracle is unavailable and incorrect, but is not a
`wrong` binding. For references and diagnostics, a nonempty proper subset of the
oracle is `partial`; a result containing any foreign item is `wrong`. Duplicate
counts are preserved. Definition and hover remain single exact answers. The
negative controls also count permitted empty suppression as `correct`, while
`matches_oracle` always records exact equality.

Five negative controls delete a declaration, change its signature, introduce
a shadowing local, insert braces inside a body that move an unchanged later
method to another component, or replace a body whose type errors contributed
to the error limit, alongside a syntax error. Their policy accepts suppression or
the current program's oracle. The driver separately feeds each predecessor's
captured answer to the scorer and requires a wrong verdict. The
[scoring tests](../test/benchmarks/test_tolerance.py) repeat this with
[protocol captures](../test/benchmarks/tolerance-negative-oracles.json), including
the signature change whose identifier ranges still match. These controls guard
against unrestricted substitution and foreign error-limit notices. The error-limit
control uses 13 old body errors because the benchmark observer contributes
another error before the intact caller reaches 15. Five more controls cover
[current-text repair](architecture.md#current-text-repair): a body that only a
`}` would complete, a site in a declaration header, a site in a field
initializer, sites in two bodies, and a repaired call whose arity and argument
types differ from both the intended call's and the caller's. Each of their
oracles also changes the called method's signature, so the predecessor's answers
are wrong. Three more cover [body blanking](architecture.md#current-text-repair)
([#312](https://github.com/SaltedTan/virgil-language-server/issues/312)): a body
with an unterminated string, a method whose header line was deleted, which
leaves an extra `}`, and a body that, once emptied, has a missing return that is
the fifteenth error, after the observer's and 13 others, so verification never
reaches the intact caller, whose answers are missing, and the excluded error
suppresses the limit notice. Their oracles change the called method's signature
too. Thirty-eight more cover the [brace rules](architecture.md#current-text-repair)
([#317](https://github.com/SaltedTan/virgil-language-server/issues/317)), each a
small tab-indented program whose oracle changes the called method's signature.
Rule 2 must refuse 34. Seven are layouts it doesn't read: ADR-0010's
`swallow-field`, two closers at one indentation, a method header split over two
lines, a body line no deeper than its header, a method header that lost its `{`
below a method indented with four spaces and closed with a tab, an unterminated
string, and a method that lost its `}` before a field indented like one of its
statements. Eight are places where a member could follow the method's end,
which the caller uses: a method that lost its `}` before a field whose first
line is deeper than its statements and whose continuation is as deep as them,
beside a file-level field of the same name, before a deeper value, method,
constructor, or variant case, before a field on its last statement's line, and
before a field whose `def` is alone on a deeper line, and a method whose `}` is
deeper than its header followed by a method that lost its `{`. Six have a local
function or a local written as deep as the method's header, where a wrong
reading would make it a member: a method that lost its `}` before a local
function, one that returns a value, one that spans lines, or a local value
whose initializer is a function; a block's `}` as deep as the method's header
before such a local function; and a method that lost its `{` after a method that
lost its `{` right before a `;`. Five repeat that last shape with the earlier
method's header holding a comment between its name and `(` or between `def`
and its name, split after `def` or after its name, or with a comment after
`private`. The last eight lost a `}`, which rule 3 refuses: an inner block and
a method that lost their `}`, a method's lost `}` beside an error in a later
body, and a missing return as the fifteenth error, each before a plain method
and before a private method. Each control with a local function has a second
query on a name that a wrong reading binds elsewhere. Rule 2 must accept four:
a legal `if` without braces followed by a `while` that lost its `{`, tabs and
spaces of equal width, a lost `{` beside an error in another body, and an
emptied body's missing return as the fifteenth error. Five more cover rule 3
([#320](https://github.com/SaltedTan/virgil-language-server/issues/320)), which
must accept three: a match case's block that lost its `}` before the next case,
an `if`'s block in a lambda that lost its `}`, and the match case again with
the emptied body's missing return as the fifteenth error. It must refuse two:
the match case with an error in a later body, and review round 3's
counterexample, a method that lost its own `}` after an `if` whose `}` is as
deep as the method's header, before a private method, which has the second
query on `count`. The repair controls also
state how many bodies each excludes, in both modes: two in the two-body control
and the brace control with another error, one in the arity and missing-return
controls and the other accepted brace controls, and
none in the others. Each record lists the bodies that its snapshot
excluded and any that differ from this (`expectation_mismatches`, summarized in
`summary.expectation_mismatches`); the smoke run fails on any. Fifteen extra states cover
three variants each of two broken files, fresh intact-file type errors, text coordinates (two UTF-16 cases with
non-ASCII before the query and one CRLF case), closed-file disk changes with
watched-file notifications, and project-file changes (remove/add membership and
change a language flag). The pinned compiler rejects CRLF, so that case uses an
LF oracle and expects semantic suppression today.

Twenty-four held-out states (`heldout`) test body blanking on edits that its
rule wasn't built from: eight of each kind that it covers, chosen with a fixed
seed (312) from the Aeneas files that no other state edits or queries, one file
each. A `next-line` state cuts a statement just after one of its member
accesses' `.`, and the next statement line continues it, so that the parser's
first error is on a later line; a `body-end` state cuts a body's last
statement the same way, before its closing brace; and a `two-bodies` state cuts
a statement in each of two bodies. Each cut alone must fail to parse in its own
body, by the CLI parser, and the generator reads method bodies from the
sources' tab layout. The intended program is the upstream file, and the
queries are the Aeneas replays' fixed sites; edit kind is `body`, and
`heldout_kind` and `edited_lines` record the rest.

Forty-two more held-out states test the brace rules
([#317](https://github.com/SaltedTan/virgil-language-server/issues/317)) on edits
that they weren't built from, chosen with a fixed seed (317) from the Aeneas
files that no other state, the 24 above included, edits or queries, one file
each. Each removes the last character of one line of a method: the `{` of an
`if`, a loop, or a `match` header (`if-opener`, `loop-opener`,
`match-opener`), the method's own header (`method-opener`), or the lone `}` of
an inner block or of the method (`block-closer`, `method-closer`). Each kind
appears twice in the upstream tab layout (`tabs`), twice re-indented with four
spaces per tab (`spaces`), twice with about half the lines' tabs as eight spaces
(`mixed`), and once with about half as four spaces (`mixed-width`), whose
widths disagree with the tabs'. Each removal alone must fail to parse, by the
CLI parser. The intended program is the file in that style, and the queries are
the same fixed sites; openers have edit kind `header` and closers `body`, and
`heldout_kind`, `indentation`, and `edited_lines` record the rest.

Twenty-eight more held-out states test rule 3
([#320](https://github.com/SaltedTan/virgil-language-server/issues/320)) on edits
that it wasn't built from, chosen with a fixed seed (320) from the Aeneas files
that no other state, the 66 above included, edits or queries, one file each.
Each removes the lone `}` of a line in a method, an inner block's
(`block-closer`) or the method's own (`method-closer`): five inner blocks and
two methods in each of the four styles above. Their IDs begin with `closer-`.
Each removal alone must fail to parse, by the CLI parser. The intended program
is the file in that style, the queries are the same fixed sites, the edit kind
is `body`, and `heldout_kind`, `indentation`, and `edited_lines` record the
rest.

JSON has the performance harness's revision, binary SHA-256, build, host, affinity,
command, and input-hash metadata. `states` retains mode, seed policy, edit kind,
text hashes, current parse outcomes, snapshots, answers, scores, and raw samples.
Edit kinds are `body` (an expression or statement inside a routine or member
body), `header` (a condition or for header, or a declaration header such as a
parameter list, return type, or variable type), and `whole_file` (truncation).
Each fixture's kind is pinned in the script's classification table.
`summary.features` splits availability, correctness, partial and missing answers, exact matches, and wrong
answers by suite, mode, kind, and feature;
`summary.latency_ms` reports p50/p95 by suite, mode, and query kind. Definition is
queried first and includes analysis waiting; later requests generally use the
completed snapshot. Diagnostic latency is an **upper bound** from state start to
the post-navigation publication inspection, not the exact publication instant.
Process records retain worker replacements and stderr; probe-enabled builds add
the existing phase, allocation, and heap counters. RSS is sampled before shutdown.
These measurements include unavailable answers and must not be compared directly
with the successful-operation benchmark above.

`make test` runs scoring, cleanup, and session sequencing regression checks, a
fifty-eight-state smoke replay (two small repair states and all 56 negative
controls, in both modes), and the four-step `in-turn` editing session in both modes. The
short session adds overlapping broken files and recovery in one server process.
A separate before/after run took 0.39 → 0.59 seconds on the reference host. As
the brace controls were added, it took about 0.88 seconds with the first 15,
1.08 seconds with 23, and 1.34 seconds with 33; with all 38, the smoke replay's
median of five runs on CPU 12 is 1.48 seconds, and with the five rule-3
controls, 43 in all, 1.60 seconds. The full suite remains opt-in.
Run these checks separately with:

```sh
python3 test/benchmarks/test_tolerance.py
python3 scripts/bench-tolerance.py --smoke --exe build/virgil-lsp --output /dev/null
```

### Tolerance baseline

Measured on 2026-10-25 at `220733d` (server sources unchanged from `6406edc`) and pinned Virgil
`7cf038fc13a5474836bec8b14c7a3e796fd07fa5`, with probes enabled, the default
384 MiB worker heap, CPU 4, Python 3.9.25, and the Intel Core i7-14700KF host
described above. The build's `-dirty` marker reflects pending documentation only.
Other work ran on this machine. Warm and cold counts were identical:

| Corpus (per mode) | States with all four features | Correct definition / hover / reference answers, each | Partial references | Correct diagnostic sets | Partial diagnostics | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Repair | 6/45 | 15/87 | 0 | 6/45 | 0 | 0 |
| Replay | 9/140 | 35/540 | 0 | 9/140 | 0 | 0 |
| Combined | 15/185 | 50/627 | 0 | 15/185 | 0 | 0 |

The same all-feature state counts split by edit kind follow, for either mode:

| Corpus | Body | Header | Whole file |
| --- | ---: | ---: | ---: |
| Repair | 6/37 | 0/6 | 0/2 |
| Replay | 7/78 | 2/62 | — |

Available answers were all correct. All three negative controls passed the safe
suppression policy in both modes and rejected the injected predecessor answer.
The fifteen extra states produced no wrong answers. This establishes the current
low-availability baseline; these deterministic cases do not estimate availability
across arbitrary editing sessions.

The full base rerun for #243 used `e3dbd69`, CPU 4, and the amended scorer
and fourth negative control. All 408 state/mode records completed in 200.26
seconds with zero partial and zero wrong answers. Every answer and legacy
score matched the original 406 records plus the separately captured boundary
control; the primary counts above are unchanged.

The full runs took 212.93 and 210.61 seconds for 406 state/mode records each,
including the three negative controls and fifteen extra cases. Every per-state
answer and score, aggregate feature count, input hash, driver hash, and binary
hash matched between runs. Both recorded zero worker replacements and complete
worker heap probes. First-run latency below pools only
the 185 primary states, including unavailable answers, in **milliseconds (p50 /
p95)**. Navigation has 627 samples per feature per mode; diagnostic inspection has
185 per mode.

| Query kind | Warm | Cold |
| --- | ---: | ---: |
| Definition, including analysis waiting | 2.256 / 104.981 | 2.325 / 217.395 |
| Hover | 2.224 / 2.360 | 2.240 / 2.395 |
| References | 2.225 / 2.621 | 2.248 / 2.727 |
| Diagnostic inspection upper bound | 127.045 / 399.817 | 235.614 / 812.473 |

These runs used server SHA-256
`171cde7f9cf9885bfd12d6f3fc7ce7baadf8267b3d4c0cfccd17aae3416d4111`
and worker SHA-256
`f658d6d1f707e9a693fc09718f04af896b240d3f200c30cfe3b72fefef1f75be`.

### Method-body fallback comparison

The #243 implementation at `aaa9b3d` is compared with the base executable at
`e3dbd69`, both using the pinned compiler, enabled probes, and the default
384 MiB worker heap. Pending documentation accounts for the after build's
`-dirty` marker. The tolerance runs use CPU 4; successful-operation comparisons
use CPU 5. Other agents were active on this shared host.

Eligibility was classified independently of returned answers by a native probe
calling the production `AeneasAdapter.methodBodies` and `LspBodyEligibility`
functions on every predecessor/current pair. A parseable current state needs no
fallback and is counted ineligible for substitution. The original corpus's
`header` label includes control headers and local types inside method bodies:
four repair states in that category satisfy containment, while all declaration
headers remain ineligible. Unbalanced intervals, including the Aeneas condition
and for-header replays, remain ineligible.

The table counts states retaining **all four features**, allowing correct plus
partial reference and diagnostic sets. Each cell gives before → after counts;
the denominator is the number of states in that eligibility group.

| Corpus | Edit kind | Eligibility | Warm before → after | Cold before → after |
| --- | --- | --- | ---: | ---: |
| Repair | Body | Eligible | 0 → 26 / 26 | 0 → 0 / 26 |
| Repair | Body | Ineligible | 6 → 6 / 11 | 6 → 6 / 11 |
| Repair | Header | Eligible | 0 → 4 / 4 | 0 → 0 / 4 |
| Repair | Header | Ineligible | 0 → 0 / 2 | 0 → 0 / 2 |
| Repair | Whole file | Ineligible | 0 → 0 / 2 | 0 → 0 / 2 |
| Replay | Body | Eligible | 0 → 71 / 71 | 0 → 0 / 71 |
| Replay | Body | Ineligible | 7 → 7 / 7 | 7 → 7 / 7 |
| Replay | Header | Ineligible | 2 → 2 / 62 | 2 → 2 / 62 |
| Combined | All | All | 15 → 116 / 185 | 15 → 15 / 185 |

All 97 eligible warm **body** states retain definition, hover, references, and
independent semantic diagnostics (100% for each feature). Their 309 definition
answers and 309 hover answers are exact. References have 283 exact and 26 partial answers;
the partial lists omit only uses inside excluded bodies. Including the four
eligible control-header states gives 101/101 retained states. No ineligible
primary state gains a substituted answer.

Per-feature primary counts after fallback follow. Before counts are those in
[Tolerance baseline](#tolerance-baseline); all before partial counts are zero.

| Corpus / mode | Exact definition | Exact hover | Exact references | Partial references | Exact diagnostic sets | Partial diagnostics | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Repair / warm | 66/87 | 66/87 | 65/87 | 1 | 36/45 | 0 | 0 |
| Repair / cold | 15/87 | 15/87 | 15/87 | 0 | 6/45 | 0 | 0 |
| Replay / warm | 300/540 | 300/540 | 274/540 | 26 | 80/140 | 0 | 0 |
| Replay / cold | 35/540 | 35/540 | 35/540 | 0 | 9/140 | 0 | 0 |

All four negative controls pass in both modes and reject the injected unsafe
predecessor answer. The shadowing-local control has one partial warm diagnostic
set: the independent observer error remains, while errors inside the excluded
body are omitted. There are **zero wrong answers** across all 408 mode records,
including the fifteen extra states, and zero worker replacements. Every cold
answer equals its base answer. The warm run has 27 partial reference answers
in 27 primary states and the one partial diagnostic set above. These are safe
subsets under the amended scoring rule.

Four cold records have a substitution for a later-opened file: the first open
started an analysis that captured that file's parseable disk text before its
broken overlay arrived. This is a valid per-file cache seed even though another
input failed to parse. That other broken file still prevents semantics, and all
cold answers remain unchanged. A fresh file with no such analyzed predecessor
cannot use fallback.

The protocol regression also verifies exactly one completed analysis for a
contained changed generation, no further analysis for its read-only requests
or rename refusal, original-text syntax diagnostics, mapped same-file and
cross-file results, and suppression of an error-limit notice whose last error
falls inside the excluded body.

Successful-operation runs alternate the two executables over three process
blocks, two warmups, and five measured operations per block (15 samples per
build and project). Every semantic check passes. All 33 latency comparisons
have overlapping p10–p90 intervals; this shared-host run shows no change beyond
its noise. Worker allocation, collection counts, and live-after-GC bytes match
exactly for fresh and reused successful analyses in all three projects. Aeneas results are **median (p10–p90), milliseconds**:

| Successful operation | Before | After |
| --- | ---: | ---: |
| Fresh analysis | 493.586 (479.631–500.877) | 480.834 (338.727–485.906) |
| Reused analysis | 460.280 (445.490–488.023) | 452.897 (317.190–495.707) |
| Definition | 2.725 (1.674–3.009) | 2.734 (1.811–2.973) |
| Hover | 2.826 (1.793–22.675) | 2.755 (1.746–2.995) |
| References | 3.609 (2.245–3.905) | 3.699 (2.128–3.917) |
| Semantic publication | 659.010 (508.385–675.097) | 687.098 (674.583–703.194) |

The base tolerance run took 200.26 seconds and the final after run 253.41
seconds, including all warm/cold oracle captures and controls. Broken programs
in the base stop at parsing, while eligible warm states now verify and collect
semantics, so those whole-suite times compare different work. No macOS timing
run or additional editor-specific check was added.

The base executable was built at `e3dbd69` and preserved separately before
implementation. The after build and measurements used these commands, with
`evidence` set to the task's scratch directory (any writable directory works):

```sh
make BUILD=build/body-fallback-final BENCH=1 all bench-metadata
python3 scripts/bench-tolerance.py --exe build/body-fallback-base/virgil-lsp --cpu 4 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-base-partial.json"
python3 scripts/bench-tolerance.py --exe build/body-fallback-final/virgil-lsp --cpu 4 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-final.json"
python3 scripts/bench-lsp.py --exe build/body-fallback-base/virgil-lsp \
  --exe build/body-fallback-final/virgil-lsp --cpu 5 \
  --output "$evidence/lsp-final-comparison.json"
```

Server and worker SHA-256 hashes, respectively:

- Before: `5d5a14677d9f84e446e259bbf182710f1dd60cbf7867bb681c1f8608f599ab5d`,
  `99bf5afed7be95785904c8a5cb7ec71fa5725e0c64f16153e90b297e040ac80f`.
- After: `40effe7c55866787eba8670498188abbf4e67df98e153ce1fefaf493d89f8fe0`,
  `4f94b5d4b1cb68b589e7176457dadac81cbf184b7a58a525a6f90805dd6c5dc5`.

The round-2 cache and error-limit fixes were rerun with probes on CPU 6, using
`make BUILD=build/body-fallback-r2 BENCH=1 all bench-metadata` and the same
tolerance command with that executable. The fifth negative control adds two
mode records: all 410 complete in 224.97 seconds, with zero wrong answers,
27 partial reference answers, one partial diagnostic set, and zero worker
replacements. All 408 answers shared with the preceding review run are
identical, so the primary counts above remain unchanged. The new control's
warm answers exactly match its current-program oracle, including diagnostics;
its cold answers are permitted suppression. All five controls reject their
injected unsafe predecessor answers.

#### Rebase and integration check (2026-10-25)

After rebasing onto main `eec5004` (wire version 12), the implementation at
`6152ea61` rejects unterminated lexical regions even when the cursor scanner
recovers before the changed interval ends. The new unit and stdio regressions
cover that rule, mapped type definitions with growing and shrinking edits,
excluded queries and type targets, and retained Read, Write, and Text highlight
kinds. ADR-0008 is accepted.

The full paired tolerance runs compare main's sources with `6152ea61`, built
in this checkout as `build/r3base` and `build/r3next` with `BENCH=1` and the
384 MiB worker heap. Source hashes were checked against main and against the
restored implementation. Both builds have identical generated BuildInfo text;
its revision names the implementation checkout with pending documentation,
so the source hashes identify the baseline. The pinned compiler remains
`7cf038fc`. The run held the shared benchmark lock after `pgrep -af bench-`
found no running benchmark, and `taskset` pinned the driver and children to
CPU 12 on the same i7-14700KF Linux host. Editor tests had finished.

Both runs complete all 410 records with **zero wrong answers and zero worker
replacements**. All 205 cold answers equal main's, and every after answer equals
round 2's. Independent eligibility classification still finds 97 eligible warm
body states and four eligible control-header states; all retain the four
measured features. The 27 partial reference lists, one partial diagnostic set,
and five unsafe controls are unchanged. The primary counts above therefore
still hold. The baseline takes 209.00 seconds and the implementation 229.99
seconds; these whole-suite times still compare different work because eligible
broken inputs now undergo verification.

The successful-operation comparison uses six alternating process blocks,
two warmups, and five measured operations per block: 30 samples per build,
project, and operation. All semantic checks pass, all 33 latency p10–p90
intervals overlap, and no samples are removed. Worker allocation, GC counts,
and live-after-GC distributions match exactly for fresh and reused analyses in
all three projects. These measurements establish no latency regression.
Aeneas results are **median (p10–p90), milliseconds**:

| Successful operation | Main | Rebased fallback |
| --- | ---: | ---: |
| Fresh analysis | 338.629 (333.466–342.804) | 335.481 (326.972–339.442) |
| Reused analysis | 305.593 (279.518–329.595) | 302.592 (284.682–327.940) |
| Definition | 1.831 (1.658–19.066) | 1.674 (1.547–1.827) |
| Hover | 1.746 (1.599–1.857) | 1.725 (1.549–1.896) |
| References | 2.388 (2.185–2.501) | 2.309 (2.183–2.446) |
| Completion cache hit | 2.676 (2.606–3.340) | 2.637 (2.597–2.858) |
| Completion cache miss | 132.413 (130.849–149.593) | 132.818 (131.687–146.218) |
| Semantic publication | 513.689 (490.185–533.547) | 534.480 (508.376–539.468) |

The measured commands, using the paired builds above and a writable `evidence`
directory, are:

```sh
flock /path/to/shared/bench.lock taskset -c 12 \
  python3 scripts/bench-tolerance.py --exe build/r3base/virgil-lsp --cpu 12 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-base.json"
flock /path/to/shared/bench.lock taskset -c 12 \
  python3 scripts/bench-tolerance.py --exe build/r3next/virgil-lsp --cpu 12 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-next.json"
flock /path/to/shared/bench.lock taskset -c 12 \
  python3 scripts/bench-lsp.py --exe build/r3base/virgil-lsp \
  --exe build/r3next/virgil-lsp --cpu 12 --blocks 6 --warmups 2 --operations 5 \
  --require-probes --output "$evidence/lsp-comparison.json"
```

Server and worker SHA-256 hashes, respectively:

- Main sources: `c532424aeb5a4c1c4b6f3f40419ae709553eed963110dbab65cf1a6a2cd13e9e`,
  `0892797acc6551aaed67992a209875c1dc38f05985b2daf3a3393ae362d0ba83`.
- Rebased fallback: `1fe1db517385d2af97e5c20809f6a76c220ffa05f3880afdf088d5a2954cd21a`,
  `f4be6e6bc739289eed7a89c8078b2edd26a0ee7f6261543e4e7501e9f234fab5`.

### Current-text repair comparison

The [current-text repair](architecture.md#current-text-repair) branch for #293,
at `edcf04d`, is compared with main at `803012c`, which already has method-body
fallback. Both used the pinned compiler, enabled probes, the default 384 MiB
worker heap, and the same `BuildInfo.v3`, and were built from clean clones with
equal-length paths. Each ran the branch's driver, so both measured the same
corpus, including the five new negative controls, pinned to CPU 12 under a
shared lock. Other agents were active on this shared host.

Eligibility for fallback is the classification of
[Method-body fallback comparison](#method-body-fallback-comparison). Whether a
state's current text is repaired was classified independently of the returned
answers by a native probe calling the production `AeneasAdapter.parseFile`,
`LspCurrentRepair.repair`, and `LspBodyEligibility.compare` functions on every
broken file. The table counts states retaining **all four features**, allowing
correct plus partial reference and diagnostic sets, as before → after counts
over the group's states.

| Corpus | Edit kind | Fallback eligibility | Repaired | Warm before → after | Cold before → after |
| --- | --- | --- | --- | ---: | ---: |
| Repair | Body | Eligible | Yes | 24 → 24 / 24 | 0 → 24 / 24 |
| Repair | Body | Eligible | No | 2 → 2 / 2 | 0 → 0 / 2 |
| Repair | Body | Ineligible | No | 6 → 6 / 11 | 6 → 6 / 11 |
| Repair | Header | Eligible | Yes | 4 → 4 / 4 | 0 → 4 / 4 |
| Repair | Header | Ineligible | No | 0 → 0 / 2 | 0 → 0 / 2 |
| Repair | Whole file | Ineligible | No | 0 → 0 / 2 | 0 → 0 / 2 |
| Replay | Body | Eligible | Yes | 67 → 67 / 67 | 0 → 67 / 67 |
| Replay | Body | Eligible | No | 4 → 4 / 4 | 0 → 0 / 4 |
| Replay | Body | Ineligible | No | 7 → 7 / 7 | 7 → 7 / 7 |
| Replay | Header | Ineligible | No | 2 → 2 / 62 | 2 → 2 / 62 |
| Combined | All | All | All | 116 → 116 / 185 | 15 → 110 / 185 |

Of the 97 body states that fallback recovers in warm sessions, 91 now also
recover cold, with definition, hover, references, and independent semantic
diagnostics. The four eligible control-header states, local types and
conditions inside method bodies, recover cold too. Every warm answer of the 185
primary states is identical to main's, so fallback's results are unchanged. The
six body states that stay unrecovered cold are blocked because the parser's
first error is not where the code is incomplete, and repair doesn't guess
another site:

- `util-Vector.v3/18`, `/19`, `/23`, and `/24` end a line in `a.length +` or
  `a.length + 10 +`, inside a call's arguments. The parser reads the next line,
  `a[s] = e;`, as more of the argument and reports its error at that line's
  `=`, where no repair applies.
- `aeneas-member-of-parameter` ends a line in `buffer.`. The parser reads the
  next line's `if (typeParams != null)` as a member and a call, and reports a
  missing `;` before its `{`, where nothing is missing.
- `member-in-match-arm` ends a match case in `p.`. The parser reads the next
  case's `Square(w) =` as the member, its call, and an assignment, and fails
  at the `>` of `=>`; neither site's repair parses.

Repairing at the document's last edit would recover all six, but only in warm
sessions, where fallback already recovers them; a cold open has no known edit.
No fallback-ineligible primary state is repaired. Two of the 11 ineligible
repair-fixture body states are field initializers, two have a second syntax
error in another body or outside one, and one is a missing `}`; all five are
refused. The other six already parse. The Aeneas condition and for-header
replays have deleted the rest of a line that holds a `{`, so their braces are
unbalanced and no repair parses; truncated files are refused the same way. The
partial and wrong counts include the controls and extra states.

Per-feature primary counts after repair follow. The warm rows are identical to
the [fallback comparison](#method-body-fallback-comparison)'s.

| Corpus / mode | Exact definition | Exact hover | Exact references | Partial references | Exact diagnostic sets | Partial diagnostics | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Repair / warm | 66/87 | 66/87 | 65/87 | 1 | 36/45 | 0 | 0 |
| Repair / cold | 61/87 | 61/87 | 60/87 | 1 | 34/45 | 0 | 0 |
| Replay / warm | 300/540 | 300/540 | 274/540 | 26 | 80/140 | 0 | 0 |
| Replay / cold | 284/540 | 284/540 | 262/540 | 22 | 76/140 | 0 | 0 |

There are **zero wrong answers** across all 420 mode records of both runs, and
zero worker replacements. Main has 28 partial answers and the branch 54: the 23
new ones are cold reference lists that omit uses inside a repaired body, the
partial diagnostic set of the shadowing-local control now also appears cold,
and the arity control has one partial reference list in each mode. All ten
negative controls pass in both modes and reject their injected predecessor
answers. Of the five new ones, a body that only a `}` would complete, a header
site, a field-initializer site, and sites in two bodies are refused, so their
answers are permitted suppression; the arity control is repaired in both modes,
and its definition, hover, and diagnostics match the current program's oracle
exactly, with no diagnostic or hover type from the repaired call outside its
body. Among the earlier controls, the boundary-escape control is now repaired in
both modes and matches its oracle, whose later method belongs to the component
that the current braces give it. Twelve extra-state records gain a repair, cold
or warm after a project-file change, and all their answers are correct.

Cold definition, which includes waiting for the analysis, now waits for an
analysis that verifies: its p95 rose from 196.3 to 678.9 ms on replays and from
197.1 to 702.3 ms on repair states, as warm fallback analyses already did. The
whole run took 240.80 seconds against main's 204.04, because cold broken states
now verify and collect semantics. Warm latencies are unchanged.

Successful operations were compared with `bench-lsp.py`, alternating the two
executables over six process blocks, with two warmups and five measured
operations per block (30 samples per build, project, and operation), on the
same CPU and lock. Every semantic check passes, and none of the 33 comparisons
is flagged: all have overlapping p10–p90 intervals. The largest median change,
util `completion_hit`, is 0.727 → 1.059 ms with overlapping intervals; completion
doesn't use the changed capture. Worker collection counts and live-after-GC
bytes match exactly for fresh and reused analyses in all three projects; each
analysis allocates 8 more bytes, for the parser's error recorder. Aeneas results
are **median (p10–p90), milliseconds**:

| Successful operation | Before | After |
| --- | ---: | ---: |
| Fresh analysis | 356.331 (344.090–361.572) | 354.023 (341.160–359.517) |
| Reused analysis | 290.503 (281.833–310.625) | 292.463 (281.910–307.803) |
| Definition | 0.205 (0.171–0.507) | 0.205 (0.169–0.225) |
| Hover | 0.175 (0.159–0.227) | 0.187 (0.154–0.219) |
| References | 0.842 (0.797–22.778) | 0.819 (0.793–24.432) |
| Syntax diagnostics | 1.521 (0.380–1.554) | 1.491 (0.378–1.540) |
| Semantic publication | 535.121 (514.561–549.691) | 527.048 (510.374–542.569) |

The builds and measurements used these commands from each clean clone and from
the branch, with `evidence` set to the task's scratch directory. The branch's
clone was rebuilt with main's `BuildInfo.v3` copied in and `make -o buildinfo`:

```sh
make BUILD=build BENCH=1 all bench-metadata
flock bench.lock taskset -c 12 python3 scripts/bench-tolerance.py \
  --exe "$evidence/base/build/virgil-lsp" --cpu 12 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-base.json"
flock bench.lock taskset -c 12 python3 scripts/bench-tolerance.py \
  --exe "$evidence/head/build/virgil-lsp" --cpu 12 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-head.json"
flock bench.lock taskset -c 12 python3 scripts/bench-lsp.py \
  --exe "$evidence/base/build/virgil-lsp" --exe "$evidence/head/build/virgil-lsp" \
  --blocks 6 --cpu 12 --require-probes --output "$evidence/lsp-comparison.json"
```

Server and worker SHA-256 hashes, respectively:

- Before: `725a5391fc357789d035471f6e30cd3d4855d086fde16b96085623d693ccfb88`,
  `aac0ad16d2108058d508a716c7c492332866cffd9d242e285b6e34611c64be9a`.
- After: `650c641ebaa538a09cce5f2b43a865d1c6e91f43cac8e8f918b4623d89e44d22`,
  `af89cf6e3247fd8db78990cb81fddce9b59deaa4c3d5f74419a56c4f7f5928b5`.

### Editing sessions

The `session` suite adds ten editing histories, separate from the 185 primary
states and their inventory checks. Each history uses one server process per
mode. Warm mode analyzes the initial parseable program first; cold mode starts
with the first step's text without that warmup. Both retain the history of
later steps. A separate oracle process replays the same opens, full-text
changes, saves, closes, and watched disk writes with only the syntax errors
removed. Navigation replies wait for the current analysis; snapshot inspection
then checks freshness and the published semantic diagnostic sets. There are no
sleeps or polling loops. Run both modes with:

```sh
make BUILD=build/bench BENCH=1 all bench-metadata
flock /path/to/shared/bench.lock python3 scripts/bench-tolerance.py \
  --suite session --exe build/bench/virgil-lsp --cpu 12 \
  --work-dir /path/to/scratch --output /path/to/scratch/sessions.json
```

The histories in [`tolerance-sessions.py`](../scripts/tolerance-sessions.py) are:

- `in-turn` breaks A, breaks B, fixes A, then fixes B.
- `overlapping` breaks both files and fixes B before A.
- `save-close-reopen` saves broken A to disk, closes it, reopens it, then fixes it.
- `new-file` creates and opens a broken source matched by the project's glob,
  fixes it, then fixes A; queries in the new file start when it exists.
- `newest-parseable` restores A with a changed target signature, then breaks a
  different body and checks that fallback uses that newest parseable version.
- `watched-disk` closes B, writes a broken body with a watched-file notification,
  restores it on disk, then fixes A.
- `limit-fallback-visible` reaches 15 semantic errors outside the substituted
  body, retaining the `ErrorLimit` notice when no error is excluded.
- `limit-fallback-excluded` replaces an error-filled cached body and checks that
  its hidden errors suppress the limit notice, even with visible errors left.
- `limit-repair` changes a declaration as well as breaking a body, forcing
  current-text repair. Since [#312](https://github.com/SaltedTan/virgil-language-server/issues/312)
  repair empties the body, which returns nothing, so it excludes no error and
  the limit notice stays, as it does in the other two histories' cold mode,
  where repair replaces fallback.
- `cache-pressure` analyzes two copies of Aeneas in separate project contexts,
  breaks an evicted file, then breaks a file still retained in the cache.

Measured on 2026-10-27 with benchmark sources at `653e4051`, pinned Virgil
`7cf038fc`, enabled probes, the default 384 MiB worker heap, and CPU 12 under
the shared benchmark lock on the Intel Core i7-14700KF host. The session-only
run took 6.61 seconds. All 72 step/mode records completed with zero wrong
answers and zero worker replacements. The table counts individual query/feature
answers, including legitimate empty oracle answers, rather than states retaining
all four features. Partial sets contain only exact oracle items.

| Step types | Step/mode records | Exact answers | Partial sets | Missing answers | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: |
| In-turn, overlapping, newest parseable | 24 | 240 | 0 | 0 | 0 |
| Save, close, reopen, restore | 10 | 80 | 0 | 20 | 0 |
| New-file creation and fixes | 8 | 98 | 0 | 0 | 0 |
| Watched disk changes and fixes | 10 | 80 | 0 | 20 | 0 |
| Error-limit stress and fixes | 12 | 115 | 5 | 0 | 0 |
| Cache pressure and broken bodies | 8 | 56 | 0 | 0 | 0 |

The four closed-broken step/mode records account for all 40 missing answers:
closed syntax errors suppress project semantics and never receive a body
substitution. Reopening or fixing the file restores the expected answers.
All five partial sets are diagnostics: the warm visible-error fallback keeps
its notice, while the excluded-error fallback and every repair suppress it.
The fixed steps again publish the oracle's limit notice. The scorer also rejects
an injected predecessor hover in `newest-parseable/other-body-broken`: its old
`int` result disagrees with the intended program's `bool` result.

Each Aeneas context has 206 source files, including its observer. Completed
analyses cache their own parseable inputs, including closed files; substituted
inputs never refresh those entries. Applying the documented charge of source
bytes plus URI bytes plus 512 bytes per entry gives 3,156,975 and 3,157,181 bytes,
respectively, exceeding the global 4,194,304-byte cap together while each project
fits the analysis budget. Eviction is also checked in the snapshot:
`first/aeneas/src/arm64/Arm64AddrPatcher.v3`, analyzed earlier with a parseable
initializer, needs the 15-byte sentinel insertion when broken after pressure.
Its six-byte original expression would have been eligible for fallback if still
cached. The following step still repairs that file but substitutes the one-byte
cached initializer in `first/lib/util/StringBuilder.v3`. These insertion lengths
in the snapshot's effective/current interval map distinguish repair from
fallback independently of the returned answers. Both modes assert those paths.

JSON keeps `sessions`, `session_oracles`, and `session_summary` separate from
`states`, `oracles`, and the original summary. Each step records its actions,
intended texts, query sites, answers, scores, snapshot, and probes; its parent
records the process, stderr, resources, and worker replacements. A wrong answer
stops the run and preserves the failing step and transcript in the failure JSON.

An unfiltered run on the same probe build took 250.02 seconds for all 420
single-edit state/mode records and the 72 session step/mode records. The 185
primary states still retain all four features in 116 warm and 110 cold states;
every per-feature count matches the current-text repair comparison above.
There are zero wrong answers and zero worker replacements across the full run.

### Body blanking comparison

[Body blanking](architecture.md#current-text-repair) for
[#312](https://github.com/SaltedTan/virgil-language-server/issues/312), rule 1 of
[ADR-0010](decisions/0010-analyze-unrepairable-files.md), at `aaf73ec`, is
compared with main at `e146354`, whose current-text repair still inserts
completion's repairs. Both used the pinned compiler, enabled probes, the default
384 MiB worker heap, and the same `BuildInfo.v3`, and were built from clean
clones with equal-length paths, pinned to CPU 12 under the shared lock. The
states ran under the branch's driver, so both builds measured the same corpus,
including the three new controls and the held-out states; main ran it one
`--suite` at a time and only reports the control expectations, which are the
branch's. Main's editing sessions ran under main's own driver, since each
history's checks expect its build's own kind of repair and limit notice. Other
agents were active on this shared host.

Whether a state's file was repaired cold is read from the cold record's
snapshot, which lists each substituted file and its excluded bodies,
independently of the returned answers; a cold run has no cached text, so every
cold substitution is a repair. Before the runs, a native probe calling the
production `AeneasAdapter.parseFile` and `LspCurrentRepair.repair` on the 200
broken files of ADR-0010's prototype chose the same bodies as the prototype's
one-candidate-per-body rule on 199. The other, the CRLF extra state, is refused:
the pinned parser rejects the CR that ends its first line, outside every body. The table counts states retaining **all four features**,
allowing correct plus partial reference and diagnostic sets, as before → after
counts over the group's states.

| Corpus | Edit kind | Fallback eligibility | Repaired cold before | Blanked cold after | Warm before → after | Cold before → after |
| --- | --- | --- | --- | --- | ---: | ---: |
| Repair | Body | Eligible | Yes | Yes | 24 → 24 / 24 | 24 → 24 / 24 |
| Repair | Body | Eligible | No | Yes | 2 → 2 / 2 | 0 → 2 / 2 |
| Repair | Body | Ineligible | No | Yes | 0 → 2 / 2 | 0 → 2 / 2 |
| Repair | Body | Ineligible | No | No | 6 → 6 / 9 | 6 → 6 / 9 |
| Repair | Header | Eligible | Yes | Yes | 4 → 4 / 4 | 4 → 4 / 4 |
| Repair | Header | Ineligible | No | No | 0 → 0 / 2 | 0 → 0 / 2 |
| Repair | Whole file | Ineligible | No | No | 0 → 0 / 2 | 0 → 0 / 2 |
| Replay | Body | Eligible | Yes | Yes | 67 → 67 / 67 | 67 → 67 / 67 |
| Replay | Body | Eligible | No | Yes | 4 → 4 / 4 | 0 → 4 / 4 |
| Replay | Body | Ineligible | No | No | 7 → 7 / 7 | 7 → 7 / 7 |
| Replay | Header | Ineligible | No | No | 2 → 2 / 62 | 2 → 2 / 62 |
| Combined | All | All | All | All | 116 → 118 / 185 | 110 → 118 / 185 |

Blanking keeps every state that the insertions kept: each of the 95 states
repaired cold before is blanked cold after and retains all four features, and
no primary state loses a feature in either mode. The only primary records whose
answers changed are the ten that gain all four, which include the eight states
that ADR-0010 names, all now recovered cold:

- Six next-line continuations: `util-Vector.v3/18`, `/19`, `/23`, and `/24`,
  `aeneas-member-of-parameter`, and `member-in-match-arm`. The parser reports
  its first error on the next line, where no insertion applied, but in the same
  body, so the same body is emptied.
- Two states with errors in two bodies, `syntax-error-after-cursor` and
  `syntax-error-elsewhere-in-file`, each of which now excludes two bodies.
  Fallback can't substitute them, so they also gain all four features warm.

Cold and warm now agree in every row: 118 of the 185 primary states retain all
four features in each mode. The 67 others are refused: the 62 Aeneas
condition and for-header states, the two truncated files, and the missing `}`
have unbalanced braces, and the two field initializers have their first error
outside every body. ADR-0010's brace rules, a later step, address the
unbalanced ones. With blanking keeping every state, it replaced the insertions,
as the maintainer decided in ADR-0010.

Per-feature primary counts after blanking follow. Warm and cold are now
identical; main's rows are the [current-text repair comparison](#current-text-repair-comparison)'s.

| Corpus / mode | Exact definition | Exact hover | Exact references | Partial references | Exact diagnostic sets | Partial diagnostics | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Repair / warm | 68/87 | 68/87 | 67/87 | 1 | 38/45 | 0 | 0 |
| Repair / cold | 68/87 | 68/87 | 67/87 | 1 | 38/45 | 0 | 0 |
| Replay / warm | 300/540 | 300/540 | 274/540 | 26 | 80/140 | 0 | 0 |
| Replay / cold | 300/540 | 300/540 | 274/540 | 26 | 80/140 | 0 | 0 |

There are **zero wrong answers** across all 474 state/mode records of both
builds (90 repair, 280 replay, 26 negative, 30 extra, and 48 held-out) and the
72 session step/mode records of each, and zero worker replacements. Main has 60
partial answers in the states and the branch 66: the four `util-Vector.v3`
continuations' cold reference lists omit uses inside the emptied body, as their
warm fallback lists do, and one held-out two-body state's reference list is
partial in each mode.

All 13 negative controls pass in both modes in both builds and reject their
injected predecessor answers. `repair-in-two-bodies`, which main refuses, now
excludes both bodies in both modes, and its definition, hover, references, and
diagnostics match the current program's oracle exactly. Both builds refuse the
unterminated string and the deleted header, whose extra `}` leaves the braces
unbalanced, so their answers are permitted suppression. In
`repair-missing-return-limit`, both builds exclude one body, main for its
sentinel's error and the branch for the emptied body's `MissingReturn`: either
error is the fifteenth, so the intact caller's definition, hover, and references
are absent and the diagnostics are a partial set without the limit notice,
permitted and never wrong. The other controls' answers and substitutions are
unchanged, and so are all 30 extra-state records.

The 24 [held-out states](#semantics-during-syntax-errors) retain all four
features in every state and mode after blanking:

| Held-out kind | Warm before → after | Cold before → after |
| --- | ---: | ---: |
| Next line | 8 → 8 / 8 | 4 → 8 / 8 |
| Body end | 8 → 8 / 8 | 8 → 8 / 8 |
| Two bodies | 0 → 8 / 8 | 0 → 8 / 8 |
| All | 16 → 24 / 24 | 12 → 24 / 24 |

In both modes, definition and hover match the oracle at all 96 queries,
references exactly at 93 and partially at 3, and diagnostic sets exactly in all
24 states, with no wrong answer; main had 64 and 48 exact definitions warm and
cold. Each two-body state excludes two bodies and every other state one.

The editing sessions' counts are unchanged except in the error-limit histories:

| Step types | Step/mode records | Exact answers before → after | Partial sets before → after | Missing answers | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: |
| In-turn, overlapping, newest parseable | 24 | 240 → 240 | 0 → 0 | 0 | 0 |
| Save, close, reopen, restore | 10 | 80 → 80 | 0 → 0 | 20 | 0 |
| New-file creation and fixes | 8 | 98 → 98 | 0 → 0 | 0 | 0 |
| Watched disk changes and fixes | 10 | 80 → 80 | 0 → 0 | 20 | 0 |
| Error-limit stress and fixes | 12 | 115 → 119 | 5 → 1 | 0 | 0 |
| Cache pressure and broken bodies | 8 | 56 → 56 | 0 → 0 | 0 | 0 |

The emptied body in the limit histories returns nothing, so it excludes no
error, and the cold step of each history and `limit-repair`'s warm step now
publish the oracle's `ErrorLimit` notice. The remaining partial set is
`limit-fallback-excluded`'s warm step, whose cached body's hidden errors
suppress the notice, as before. In `cache-pressure`,
`Arm64AddrPatcher.v3` is still repaired in both modes, now by blanking, and the
retained file is still substituted by fallback; the driver now tells repair by
an effective interval as long as the current one.

Latencies are unchanged within noise where the outcome is unchanged: cold
definition's p95 is 691.8 → 694.7 ms on replays and 683.1 → 678.3 ms on repair
states. Cold held-out diagnostics' median rose from 459.6 to 736.8 ms, because
twelve more held-out states now verify and collect semantics cold. The branch's
unfiltered run took 313.97 seconds, against 306.79 for main's suite-by-suite
runs and sessions.

A blanked cold open costs as many parses as an insertion repair: the current
text's own parse, then one parse of the blanked text, or two for errors in two
bodies, within the same three-parse budget. A native probe linking each build's
`LspCurrentRepair` timed eleven full repairs of each of the 200 prototype files
and 24 held-out texts and took the median, on the same CPU and lock. On the 123
files that both builds repair, all with one repaired parse, the median repair
took 0.170 ms before and 0.208 ms after, the p90 3.73 and 5.38 ms, and the
largest increase was 2.06 ms, on the 106 KB `aeneas-call-nested` file (3.75 →
5.82 ms): blanking scans the whole text for balanced braces and again for the
body's matching `}`, where an insertion scanned only up to its site. The 24 files
that only blanking repairs, 11 of them with two parses, took a median of
0.208 ms and at most 6.52 ms, a 66 KB file with two bodies. Refusals took a
median of 1.43 ms before and 1.61 ms after, on the 77 mostly 106 KB Aeneas files
that neither build repairs, about 15 ns per byte of scanning. A cold open of an
Aeneas file waits hundreds of milliseconds for its analysis, so the largest
difference is under 1% of it.

Successful operations were compared with `bench-lsp.py`, alternating the two
executables over six process blocks, with two warmups and five measured
operations per block (30 samples per build, project, and operation), on the
same CPU and lock. Every semantic check passes, and none of the 33 comparisons
is flagged: all have overlapping p10–p90 intervals. The largest median change,
Aeneas `syntax_diagnostic`, is 1.467 → 0.417 ms, with bimodal samples and
intervals spanning 0.377–3.635 and 0.383–4.546 ms; successful operations don't
run repair. Worker allocated bytes and live-after-GC bytes match exactly for
fresh and reused analyses in all three projects. Aeneas results are
**median (p10–p90), milliseconds**:

| Successful operation | Before | After |
| --- | ---: | ---: |
| Fresh analysis | 353.977 (343.019–365.569) | 365.069 (350.178–379.567) |
| Reused analysis | 295.418 (281.899–313.367) | 299.709 (283.391–319.544) |
| Definition | 0.204 (0.169–0.222) | 0.191 (0.164–0.217) |
| Hover | 0.162 (0.152–0.192) | 0.174 (0.148–0.208) |
| References | 0.817 (0.788–3.797) | 0.860 (0.788–4.478) |
| Syntax diagnostics | 1.467 (0.377–3.635) | 0.417 (0.383–4.546) |
| Semantic publication | 527.441 (512.860–541.943) | 539.847 (516.921–573.498) |

The builds and measurements used these commands from each clean clone and from
the branch, with `evidence` set to the task's scratch directory, and the
branch's clone rebuilt with main's `BuildInfo.v3`. Main's sessions ran the same
`--suite session` command from main's clone:

```sh
make BUILD=build BENCH=1 all bench-metadata
for suite in repair replay negative extra heldout; do
  flock bench.lock taskset -c 12 python3 scripts/bench-tolerance.py --suite $suite \
    --exe "$evidence/base/build/virgil-lsp" --cpu 12 \
    --work-dir "$evidence/bench-work" --output "$evidence/tolerance-base-$suite.json"
done
flock bench.lock taskset -c 12 python3 scripts/bench-tolerance.py \
  --exe "$evidence/head/build/virgil-lsp" --cpu 12 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-head.json"
flock bench.lock taskset -c 12 python3 scripts/bench-lsp.py \
  --exe "$evidence/base/build/virgil-lsp" --exe "$evidence/head/build/virgil-lsp" \
  --blocks 6 --cpu 12 --require-probes --output "$evidence/lsp-comparison.json"
```

Server and worker SHA-256 hashes, respectively:

- Before: `6f53035cb33571ff9a93fc2e8918075923241cb1a04ca7af4870325fe476d883`,
  `7186b8f04dcd5f342b14627ea4d6a068ea15bc4a920d2d4d626668bda1085dc2`.
- After: `944e0a8d16c1e342a5e7f3dedbd46a3099eb9d569b552a1f806edca34fffcdf1`,
  `dc8302642f4f9af943eeb212b32dac943ae7db01c99d75803f724704ea05ee96`.

### Brace rules comparison

The [brace rule](architecture.md#current-text-repair) for
[#317](https://github.com/SaltedTan/virgil-language-server/issues/317), rule 2
of [ADR-0010](decisions/0010-analyze-unrepairable-files.md), which repairs a
file that lost one `{`, is compared with main at `54800ce`, which refuses every
file whose braces don't balance. Rule 3, which would repair a lost `}`, was
measured with it in earlier rounds, at `a514662`, `de9803e`, and `13a5309`; three
reviews in a row found wrong readings, most of them in rule 3, where indentation
alone decided whether a declaration was a member or a local, and it was left
out for a redesign in
[#320](https://github.com/SaltedTan/virgil-language-server/issues/320). The
counts below are rule 2's alone, at `cd3bc2b`.

**Stress sets and the contract.** Rule 2 trusts indentation, under the
contract that [Current-text repair](architecture.md#current-text-repair)
states: when every brace pair of the intended file agrees with its indentation,
by the layout check's own reading, with tabs reaching the next multiple of 8
columns and blank and comment-only lines skipped, rule 2 gives no wrong answer.
A native probe linking the production `AeneasAdapter.parseFile`,
`LspCurrentRepair.repair`, and `LspLayout` read every state of eight generated
sets, independent of this corpus, with each state's intended program. A reading
is wrong unless the removed brace lies in an excluded body and the intended
text parses with each excluded body as a method or constructor body at the same
place. The probe also asked `LspLayout`, as the rule reads a file, whether every
brace pair of the intended text agrees with its layout, which places each state
inside or outside the contract:

| Stress set | States | Inside | Accepted inside | Wrong inside | Outside | Accepted outside | Wrong outside |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Single-brace removals (seed 9317) | 4,428 | 2,946 | 561 | 0 | 1,479 | 0 | 0 |
| Members (seed 31701) | 22,740 | 10,774 | 1,217 | 0 | 11,966 | 0 | 0 |
| Locals (seed 31702) | 42,060 | 5,786 | 433 | 0 | 36,274 | 0 | 0 |
| First review | 1,000 | 860 | 65 | 0 | 140 | 0 | 0 |
| Second review | 6,270 | 5,050 | 230 | 0 | 1,220 | 0 | 0 |
| Second review, extended | 7,360 | 5,430 | 250 | 0 | 1,930 | 0 | 0 |
| Third review | 59,792 | 30,512 | 800 | 0 | 29,280 | 0 | 0 |
| Block holders | 13,536 | 0 | 0 | 0 | 13,536 | 720 | 720 |

The first set holds a seeded sample of up to four single-brace removals, of a
`{` that ends a line or a line that is only `}`, in each of the 199 pinned
`aeneas/src` and `lib/util` files in each of six indentation styles (tabs, four
and two spaces per tab, half the lines' tabs as eight or as four spaces, and a
few lines off by one column); three of its states still parse and aren't
counted inside or outside. The member set indents a member after a method one or
two tabs deeper than its siblings, splits it over lines, or moves it onto the
method's last line, in the same files and styles, and removes up to six braces
from each of the 4,522 such programs that parse. The local set adds local
functions and local values whose initializer is a function, written as deep as
the method's header or spanning lines, and methods whose `{` is followed by
`;`, and removes every brace from the inserted code through the next member in
each of 5,310 programs. The three reviews' sets remove every brace from
hand-built programs in four or five styles; the extended set adds 20 programs to
the second review's, and the third review's adds, among other shapes,
method headers with a comment or a line break inside them before a local
function as deep as the header. The probe found no wrong reading in any of these, and for the third
review's 800 accepted states, definition, hover, and references on `target`'s
uses of `count` and `local()` in a cold open and a warm session match the
intended program's in every state.

The block-holder family is the shape that the contract excludes. Each of its
864 parseable programs has a method or a constructor `h` whose body begins with
a block, on `h`'s line or its own, or with an `if`, whose `}` is written as deep
as `h`, and then a local function `local` written as deep as `h` too, before a
`target` that calls the top-level `local()`, from 216 templates in four
styles; removing each brace once gave 13,536 states. Every intended file has a
brace pair that disagrees with its indentation. Rule 2 accepts 720 of these
states, from 432 intended files, and reads every one wrongly:
it takes `h` as ending at the block's `}`, `local` as a member whose `if` lost
its `{`, and empties `local`'s body, so `local()` in `target` binds to the
former local function instead of the top-level one, cold and warm. A guard that
refused every such reading was tried and refused all 62 primary states and 12
of the 15 held-out states below that rule 2 recovers, so none was added.

**The tolerance benchmark.** A full run of rule 2 at `cd3bc2b`, built with
probes and pinned to CPU 12 under the shared lock, completed 634 state/mode
records and 72 session steps in 461.72 seconds. Main's counts are its run at
`54800ce` under the driver at `8110f39`, which added the brace controls and the
42 brace held-out states; the primary and held-out states haven't changed
since. Whether a state's file was repaired cold is read from the cold record's
snapshot, as in the body blanking comparison, and the table counts states
retaining **all four features**, allowing correct plus partial reference and
diagnostic sets, as before → after counts over the group's states.

| Corpus | Edit kind | Warm before → after | Cold before → after |
| --- | --- | ---: | ---: |
| Repair | Body | 34 → 34 / 37 | 34 → 34 / 37 |
| Repair | Header | 4 → 6 / 6 | 4 → 6 / 6 |
| Repair | Whole file | 0 → 0 / 2 | 0 → 0 / 2 |
| Replay | Body | 78 → 78 / 78 | 78 → 78 / 78 |
| Replay | Header | 2 → 62 / 62 | 2 → 62 / 62 |
| Combined | All | 118 → 180 / 185 | 118 → 180 / 185 |

The 62 states that gain all four features, in both modes, are the Aeneas
condition and for-header states: the repair site and 13 replays in `Parser.v3`'s
condition, and the repair site and 47 replays in `Verifier.v3`'s for header.
Each lacks the end of its header line, including the `{` that ends it, so rule
2 blanks the method body that layout delimits, one body in each, and no offset
moves. No state loses a feature. Five remain refused: the two truncated files
and the two field initializers, which phase 3b addresses, and
`missing-brace-end-of-method`, whose method lost its `}`, which no rule now
repairs. ADR-0010 estimated 181 with rule 3; with rule 3, this state was
refused anyway, since its method's last statement, `var n = 1;`, could equally
begin a field after the method, and `def other()`, whose header a local
function's could equally be, follows it.

Per-feature primary counts after rule 2 follow; main's rows are the
[body blanking comparison](#body-blanking-comparison)'s.

| Corpus / mode | Exact definition | Exact hover | Exact references | Partial references | Exact diagnostic sets | Partial diagnostics | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Repair / warm | 76/87 | 76/87 | 75/87 | 1 | 40/45 | 0 | 0 |
| Repair / cold | 76/87 | 76/87 | 75/87 | 1 | 40/45 | 0 | 0 |
| Replay / warm | 540/540 | 540/540 | 514/540 | 26 | 140/140 | 0 | 0 |
| Replay / cold | 540/540 | 540/540 | 514/540 | 26 | 140/140 | 0 | 0 |

There are **zero wrong answers** across all 634 state/mode records (90
repair, 280 replay, 102 negative, 30 extra, and 132 held-out) and the 72
session step/mode records, zero unmet control expectations, and zero worker
replacements. In each mode, 35 answers in the states are partial: 27 primary
reference lists, four held-out reference lists that omit uses inside an
emptied body, one of them a brace state's, a negative control's reference list,
and three negative controls' diagnostic sets, among them the brace
missing-return control's. One session answer is partial. The editing sessions'
answer counts are identical to main's: no history leaves its braces
unbalanced.

All 51 negative controls pass in both modes and reject their injected
predecessor answers. Main refuses every one of the 38 brace controls. Rule 2
excludes the bodies that four of them expect, in both modes: one for the
braceless `if` before a `while` that lost its `{`, one for tabs and spaces of
equal width, two for the lost `{` beside an error in another body, and one for
the error-limit control, whose emptied body's `MissingReturn` is the fifteenth
error, so the intact caller's definition, hover, and references are absent and
the diagnostics are a partial set without the limit notice, permitted and never
wrong. Their definition, hover, references, and diagnostics otherwise match the
current program's oracle exactly. The other 34 are refused, so their answers
are permitted suppression, and each that has a second query, on a name that a
wrong reading would bind elsewhere, gets no wrong answer: the layouts rule 2
refuses, the deeper members after a method, the local functions and locals as
deep as a method's header, the method after one that lost its `{` right before
a `;`, the review's commented and split headers before such a method, and every
control for a lost `}`, which rule 3 repaired in earlier rounds and which now
expect refusal.

The [held-out states](#semantics-during-syntax-errors) for the brace rules
(seed 317) gain all four features in 15 of 42 states, in each mode; the 24
held-out states of #312 are unchanged:

| Held-out kind | Warm before → after | Cold before → after |
| --- | ---: | ---: |
| Next line, body end, and two bodies (#312) | 24 → 24 / 24 | 24 → 24 / 24 |
| `if-opener` | 0 → 6 / 7 | 0 → 6 / 7 |
| `loop-opener` | 0 → 5 / 7 | 0 → 5 / 7 |
| `match-opener` | 0 → 4 / 7 | 0 → 4 / 7 |
| `method-opener` | 0 → 0 / 7 | 0 → 0 / 7 |
| `block-closer` | 0 → 0 / 7 | 0 → 0 / 7 |
| `method-closer` | 0 → 0 / 7 | 0 → 0 / 7 |
| All | 24 → 39 / 66 | 24 → 39 / 66 |

By indentation, rule 2 keeps 4 of the 12 `tabs` states, 6 of the 12 `spaces`
states, 5 of the 12 `mixed` states, and none of the 6 `mixed-width` states. In
both modes, each accepted state excludes one body, definition and hover match
the oracle at all 60 queries, references exactly at 59 and partially at 1, and
diagnostic sets exactly in all 15 states, with no wrong answer. Every refused
state gets no semantic answer, as on main. The 14 block and method closers are
refused because rule 3 isn't implemented. Among the 28 lost `{`, the seven
`method-opener` states are refused because their header line no longer ends in
`{` and so is no header, and six are refused by the conservative readings:
three `mixed-width` states, where four-space and tab widths disagree with the
braces, and three states in `X86MacroAssembler.v3`, `Vst.v3`, and `Dwarf.v3`,
pinned files whose layout already disagrees with one brace pair elsewhere.

A cold open that rule 2 repairs costs as many parses as body blanking: the
current text's own parse, then one parse of the repaired text, or two when
another body holds an error, within the same three-parse budget. A native probe
linking each build's `LspCurrentRepair` timed eleven full repairs of each of
the 171 broken primary texts and 57 brace control and held-out texts, and took
the median, on CPU 12 under the shared lock, main's build and then rule 2's. On
the 104 files that both builds repair, all with balanced braces and the same
number of parses, the median repair took 0.366 ms before and 0.359 ms after,
and the slowest 5.54 and 5.52 ms. The 81 files that only rule 2 repairs, 80 with
one repaired parse and 1 with two, took a median of 4.13 ms and at most
4.23 ms, a 106 KB `Verifier.v3` of a for-header state. Refusals took a median of
0.067 ms before and 0.069 ms after on the 43 files that neither build repairs,
and at most 1.02 and 1.44 ms, a 72 KB mixed-width held-out text. A cold open of
an Aeneas file waits hundreds of milliseconds for its analysis, so rule 2's
repair is about 1% of it.

Successful operations don't run current-text repair. They were compared at
`a514662`, with rule 3 then included, using `bench-lsp.py`, alternating the two
executables over six process blocks, with two warmups and five measured
operations per block (30 samples per build, project, and operation), on the
same CPU and lock. Every semantic check passes, and none of the 33 comparisons
is flagged: all have overlapping p10–p90 intervals. The largest relative median
change, util `completion_hit`, is 0.763 → 1.126 ms, with bimodal samples and
intervals spanning 0.690–1.340 and 0.692–1.755 ms. Worker allocated bytes, heap
used after handling, and live-after-GC bytes match exactly for fresh and reused
analyses in all three projects. Aeneas results are **median (p10–p90),
milliseconds**:

| Successful operation | Before | After |
| --- | ---: | ---: |
| Fresh analysis | 361.323 (347.339–365.274) | 361.270 (346.473–375.827) |
| Reused analysis | 301.495 (292.513–326.368) | 311.265 (294.902–327.055) |
| Definition | 0.189 (0.166–0.559) | 0.192 (0.169–0.534) |
| Hover | 0.187 (0.148–0.199) | 0.185 (0.154–0.466) |
| References | 0.823 (0.786–0.956) | 0.827 (0.794–1.166) |
| Syntax diagnostics | 0.397 (0.375–1.499) | 0.391 (0.376–1.700) |
| Semantic publication | 512.871 (499.062–520.607) | 513.341 (501.385–519.195) |

The builds and measurements used these commands, with `evidence` set to the
task's scratch directory; the comparison of successful operations used clean
clones of main and `a514662` with equal-length paths and the same
`BuildInfo.v3`:

```sh
make BUILD=build/r3-bench BENCH=1 all bench-metadata
flock bench.lock taskset -c 12 python3 scripts/bench-tolerance.py \
  --exe build/r3-bench/virgil-lsp --cpu 12 \
  --work-dir "$evidence/bench-work" --output "$evidence/tolerance-r3.json"
flock bench.lock taskset -c 12 python3 scripts/bench-lsp.py \
  --exe "$evidence/base/build/virgil-lsp" --exe "$evidence/head/build/virgil-lsp" \
  --blocks 6 --cpu 12 --require-probes --output "$evidence/lsp-comparison.json"
```

Server and worker SHA-256 hashes, respectively:

- Main, `54800ce`: `151c82d1ff105175ae0584e690b3743c478ad846e6ff08d204ee3633d475958d`,
  `a61c77fb664ce94b4c24810f8daa74882a4183198b0de898a29e8e11c5c1a199`.
- `a514662`, the successful-operation comparison: `01402691b2f20a9d7ebc647b4977ac2b38ae044c7c73eee6efd706db0177b902`,
  `708eb64db15d022775c38377132ff06555fda01e4d5989a5dd7b6d0de2f3397c`.
- `cd3bc2b`, the tolerance run: `42744f96613ec296f71b7ef1e78bac623a9c6b0fa59b18f93c1308f28fff6a4f`,
  `1c164f5f4e3598c67b35754834a5af4287f2afd062dde69eb8c6c1075bb42cea`.

### Brace rule 3 comparison

[Rule 3](architecture.md#current-text-repair) for
[#320](https://github.com/SaltedTan/virgil-language-server/issues/320), which
repairs a file that lost one `}` where every place for it agrees, is compared
with main at `e8018870`, which refuses every file one `}` short. It replaces
ADR-0010's rule 3, which read the lost `}` from indentation and was left out of
the [brace rules comparison](#brace-rules-comparison) after three reviews found
wrong readings.

**The ambiguity test.** Rule 3 was designed with a native probe linking the
production `AeneasAdapter.repairParse` and `documentSymbols`,
`LspCursorScanner`, and `LspLayout`, before any server change. For each state one
`}` short, it inserted a `}` at every token gap after the last `{` that raised
the braces' balance from 0 to 1, through the end of the line of the parser's
first error, and parsed each text. Each reading that parsed gave the method or
constructor body that holds the inserted `}` and the outline outside it that
call and type hierarchy read: each declaration's name, kind, container, and the
offsets of its name and extent, in current-text offsets. Document symbols and
folding parse the current text, so repair doesn't change them. Accepting when
every reading has the same body and outline but ignoring where the method ends
accepted six of the 24 earlier rule-3 controls, review round 3's counterexample
in all five styles, and 12,905 more states of the six stress sets below that
were read in full, in each of which some reading put the method's end where the
intended text doesn't. Requiring the same end too, and refusing a reading whose
inserted `}` ends the body itself, gave no wrong reading. That is the rule.

On the corpus, one primary state and 14 of the 42 brace held-out states are one
`}` short. The primary state, `missing-brace-end-of-method`, and every
`method-closer` state lost a method's own `}`, which the rule refuses. Two
`block-closer` states, in `ssa/SsaInterpreter.v3` (a match case's block) and
`core/Program.v3`, both re-indented with spaces, are accepted with the intended
body. The full test parsed 531 and 932 texts for them, and up to 9,826, taking
4.0 seconds, for a refused 41 KB `jvm/SsaJvmGen.v3`. Of the 28 held-out states
generated for rule 3 afterwards, it accepted three, each with the intended
body. With every token gap through the end of the text, on the five reviews'
sets and the block holders, no verdict changed. The
production form tries only the places right after `;`, `{`, or `}` outside
parentheses, inside the innermost `{` open at the error, groups them by
their innermost `{`, and stops at the first refusal, as
[Current-text repair](architecture.md#current-text-repair) describes; it took
6 and 3 parses for the two held-out states, and its verdict and body matched the
full test's in every state of every set, except 12 local-set states outside the
contract that it refuses.

**Stress sets.** The eight generated sets of rule 2's comparison, and a fourth
review's, give the states one `}` short. The fourth review's set removes every
`}` from 3,390 programs of an independent grammar-shaped generator (seed
27031704): blocks, loops, matches, lambdas, local functions and values, and
strings and comments that hold braces, in six indentation styles, 41 of each
style with CRLF line ends and comments. A native probe linking the production
`LspCurrentRepair.repair` read every state. A reading is
wrong unless its excluded body is the intended text's method or constructor
body that held the removed `}`, at the same place, and the blanked text's
outline outside it matches the intended text's, names, kinds, containers, and
name and extent offsets. Inside and outside the contract are rule 2's, from
the intended text's layout:

| Stress set | States | Inside | Accepted inside | Wrong inside | Outside | Accepted outside | Wrong outside |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Single-brace removals (seed 9317) | 2,192 | 1,466 | 135 | 0 | 726 | 62 | 0 |
| Members (seed 31701) | 11,365 | 5,354 | 350 | 0 | 6,011 | 394 | 0 |
| Locals (seed 31702) | 23,097 | 3,246 | 131 | 0 | 19,851 | 1,793 | 0 |
| First review | 500 | 430 | 30 | 0 | 70 | 0 | 0 |
| Second review | 3,135 | 2,525 | 200 | 0 | 610 | 0 | 0 |
| Second review, extended | 3,680 | 2,715 | 200 | 0 | 965 | 10 | 0 |
| Third review | 29,896 | 15,256 | 496 | 0 | 14,640 | 532 | 0 |
| Fourth review | 39,072 | 30,695 | 4,485 | 0 | 8,377 | 1,035 | 0 |
| Block holders | 6,768 | 0 | 0 | 0 | 6,768 | 432 | 0 |

Main accepts none of these states. Rule 3 accepts 10,285 and reads none
wrongly, inside or outside the contract. Of its 109,420 refusals, 91,816 have
the first error in the outermost open block, 16,370 a reading whose inserted
`}` ends the method, 392 a reading in no method or constructor body, and 422
would take more than 16 parses; the full test accepts 392 of those last (354 of
the fourth review's, 8 single-brace removals, 3 members, and 27 locals). The
other 420, all among the fourth review's CRLF states, are refused after
blanking, as rules 1 and 2 refuse such files: their emptied body held comments
that end in a CR, which the pinned compiler rejects outside a comment. For the
1,436 accepted states of the first three reviews' sets that have a `target`
method, definition, hover, and references on its 2,404 uses of
`count` and `local` match the intended program's exactly in a cold open and in
a warm session.

**Independent adversarial review.** The independent review's own generator
made 94,692 checks over 47,622 brace sites, with 29,568 acceptances and no wrong
readings. On accepted cases it compared every feature, cold and warm:
definition, hover, references, diagnostics, symbols, folding, call and type
hierarchy, semantic tokens, and inlay hints. It also reran the full tolerance
benchmark. All 51 earlier captures reproduced exactly.

**The tolerance benchmark.** Full runs of main at `e8018870`, under the driver
at `11e69a45`, which added the rule-3 controls and the 28 held-out states, and of
rule 3 at `2c8a90bb`, each built with probes and pinned to CPU 12 under the
shared lock, completed 700 state/mode records and 72 session steps each, in
517.95 and 523.65 seconds. The table counts states retaining **all four
features**, as before → after counts over the group's states:

| Corpus | Edit kind | Warm before → after | Cold before → after |
| --- | --- | ---: | ---: |
| Repair | Body | 34 → 34 / 37 | 34 → 34 / 37 |
| Repair | Header | 6 → 6 / 6 | 6 → 6 / 6 |
| Repair | Whole file | 0 → 0 / 2 | 0 → 0 / 2 |
| Replay | Body | 78 → 78 / 78 | 78 → 78 / 78 |
| Replay | Header | 62 → 62 / 62 | 62 → 62 / 62 |
| Combined | All | 180 → 180 / 185 | 180 → 180 / 185 |

No primary state changes. The one primary state one `}` short,
`missing-brace-end-of-method`, lost its method's own `}`, which rule 3
refuses; the other four refused states are the two truncated files and the two
field initializers. The per-feature primary counts are the
[brace rules comparison](#brace-rules-comparison)'s exactly.

There are **zero wrong answers** across all 700 state/mode records (90 repair,
280 replay, 112 negative, 30 extra, and 188 held-out) and the 72 session
step/mode records, zero unmet control expectations, and zero worker
replacements, in both runs. Partial answers go from 70 to 72 in the states: the
rule-3 missing-return control's diagnostic set, in each mode. The editing
sessions' answers are identical to main's.

All 56 negative controls pass in both modes and reject their injected
predecessor answers. Rule 3 excludes the body that three of the five new
controls expect, in both modes: the match case's block, the `if`'s block in a
lambda, and the match case whose emptied body's `MissingReturn` is the
fifteenth error, whose diagnostics are a partial set without the limit notice,
permitted and never wrong, and whose intact caller's answers are absent. The
others match the current program's oracle exactly. The match case with an error
in a later body and review round 3's counterexample are refused, and the
counterexample's second query, on `count`, gets no wrong answer. The 24 earlier
controls for a lost `}` stay refused.

The held-out states gain all four features in 5 of the 42 brace and 28 rule-3
states, in each mode:

| Held-out kind | Warm before → after | Cold before → after |
| --- | ---: | ---: |
| Next line, body end, and two bodies (#312) | 24 → 24 / 24 | 24 → 24 / 24 |
| Lost `{` (#317) | 15 → 15 / 28 | 15 → 15 / 28 |
| `block-closer` (#317) | 0 → 2 / 7 | 0 → 2 / 7 |
| `method-closer` (#317) | 0 → 0 / 7 | 0 → 0 / 7 |
| `block-closer` (#320) | 0 → 3 / 20 | 0 → 3 / 20 |
| `method-closer` (#320) | 0 → 0 / 8 | 0 → 0 / 8 |
| All | 39 → 44 / 94 | 39 → 44 / 94 |

The five are the two spaces-indented #317 states above and three of the seed
320 states: `ssa/SsaPrinter.v3` with tabs, `vst/VstMatcher.v3` with spaces, and
`x86-64/SsaX86_64Gen.v3` with mixed indentation. Each excludes one body, and in
both modes definition, hover, and references match the oracle exactly at all 20
queries, and so do the diagnostic sets. Every refused state gets no semantic
answer, as on main. All 15 `method-closer` states lost a method's own `}`.
Of the 22 other refused `block-closer` states, 15 have the first error in the
outermost open block, where a block's lost `}` lets later methods parse as local
functions, and seven have a reading whose inserted `}` ends the method.

**Cost.** Rule 3 reads its places beyond the three-parse budget, up to 16
parses of texts with a `}` inserted. On the stress sets, the phase-1 form of
the test, without that limit, took a median of 2 to 9 and a 95th percentile of
6 to 17 parses per acceptance in each set, and at most 89; refusals usually
take none, since a first error in the outermost block needs no parse. On the benchmark, no state
or control needs more than 9. A native probe linking each build's
`LspCurrentRepair` timed eleven full repairs of each broken text and took the
median, on CPU 12 under the shared lock, main's build and then rule 3's, as in
the brace rules comparison. On the 247 benchmark texts that both builds repair,
the median repair took 0.573 ms before and 0.585 ms after, and the slowest 6.72
and 6.70 ms. The eight that only rule 3 repairs, the five held-out states and
the three controls, took a median of 0.560 ms and at most 5.23 ms, an 82 KB
`SsaX86_64Gen.v3`. The 103 that neither repairs took a median of 0.014 and
0.019 ms, and at most 1.39 and 1.73 ms. On the 2,192 single-brace removal
states one `}` short, of every pinned `aeneas/src` and `lib/util` file in six
styles, the 197 that rule 3 repairs took a median of 1.28 ms and at most
31.4 ms, a 106 KB `Verifier.v3`, and the 1,995 refusals a median of 0.108 ms
before and 0.199 ms after, a 99th percentile of 1.51 and 4.02 ms, and at most
1.88 and 7.76 ms. A cold open of an Aeneas file waits hundreds of milliseconds
for its analysis.

The smoke replay, now 58 states, has a median of five runs on CPU 12 of 1.60
seconds. Successful operations don't run current-text repair, and rule 3 adds
only bookkeeping to `LspLayout`, which only repair builds, so they weren't
compared again.

The builds and measurements used these commands, with `evidence` set to the
task's scratch directory, and the same two commands in `base`, a clone at
`11e69a45`:

```sh
make BUILD=build/r3-bench BENCH=1 all bench-metadata
flock bench.lock taskset -c 12 python3 scripts/bench-tolerance.py \
  --exe build/r3-bench/virgil-lsp --cpu 12 \
  --work-dir "$evidence/bench-work-head" --output "$evidence/tolerance-head.json"
```

Server and worker SHA-256 hashes, respectively:

- Main's sources at `11e69a45`: `023805e0ffcdf3bc0c253d374b473c3a0e5a7611f172d447fd6a6ff825fcb758`,
  `a3df77221af87c4ea1da6f060e0f0e58c16374c92b852d78232d77a08372ba90`.
- `2c8a90bb`: `7d7d75f12ead3a215a02ee85101f1eed98c7ea4aa1dc35f378201025f49a5f33`,
  `478b321bcf9fcb9155df35df9210f4ce3997efd353bb50c6ce4954360c9e9288`.

### Release scorecard

[`scripts/check-tolerance.py`](../scripts/check-tolerance.py) qualifies one
complete capture against the phase-3a release floors. The floors cover phase
3a's bounded open-body scope: rule 2 reads a missing `{` only where the file's
indentation places it, and header and field-initializer errors and truncated
files remain phase 3b. Capture and qualification
stay separate commands: the checker only reads a `bench-tolerance.py` JSON
capture, refuses `--smoke`, `--suite`, and `--only` runs whether the filter
appears in the `capture` metadata or the `command`, and checks the stable
state, suite, group, warm/cold, intended-text oracle group, and session-step
inventory in
[`test/benchmarks/tolerance-inventory.json`](../test/benchmarks/tolerance-inventory.json)
before it trusts an aggregate. An oracle must answer exactly the owning state's
queries, and recorded measurements must be finite, nonnegative numbers, never
booleans. Each state's snapshot must hold one current document for each
configured source, as the driver checks before it scores. State and session
snapshots, including session-oracle steps, must have a current project
configuration and current documents with an explicit null or non-boolean integer
version; session snapshots must include every queried document. Every negative
control and designated session-oracle step must carry a rejected injection with
every answer its oracle has.

The modes must use distinct server processes. Each state's warm and cold records
must name the same nonempty list of distinct changed paths from `texts.current`.
Changed open sources must be at version 1 cold and version 2 warm; changed
sources in the closed-file-on-disk states must have null versions in both modes.
Every other open source must be at version 1 in both modes.

It rescores every recorded verdict, including each injection's, from
the recorded answers with `bench-tolerance.py`'s own scoring, so it refuses any
capture that an incomplete, filtered, interrupted, or buggy run could produce,
or whose verdicts its answers don't support; it can't detect answers and
verdicts that were rewritten consistently, which only rerunning the benchmark
can. A capture doesn't record which intended text an oracle captured, so two
states' oracles swapped where no verdict changes, for example where both
states' answers are missing, still pass; the scorecard's counts are then the
same. It reports availability per mode,
separates exact, partial, missing, and wrong answers per feature, requires zero
wrong answers and zero control-expectation mismatches, and prints a zero floor
as an uncovered class. Captures now record a `capture` object with `smoke`,
`suite`, `only`, and `full`; older captures are read from their `command`. Run:

```sh
python3 scripts/check-tolerance.py build/bench-results/v1-tolerance.json
```

The command exits nonzero if capture validation or any per-mode floor fails.
Fully exact state totals are informational baselines, not enforced floors.

Regenerate the inventory after a corpus change with
`python3 scripts/tolerance-inventory.py --write`, which needs a built server;
its `--check` default replays the recorded parse verdicts, so it needs none.

The full run on 2026-10-29 at `ced30385` (server
`d585307457921dda3bdb19ae0177f5cdc9a0367e119d9b282a321e6edbcab88d`,
542.08 seconds) passed every floor in both modes with zero wrong answers, zero
expectation mismatches, all 56 negative controls in both modes, and all 56
stale-predecessor injections rejected:

| Mode | Primary all four features | Held-out | Exact definition | Exact hover | Exact references | Exact diagnostic sets | Wrong answers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Warm | 180/185 | 44/94 | 616/627 | 616/627 | 589/627 | 180/185 | 0 |
| Cold | 180/185 | 44/94 | 616/627 | 616/627 | 589/627 | 180/185 | 0 |

Fully exact states were 153 of 185 primary and 40 of 94 held-out. The held-out
subgroups were the same in both modes: 24/24 body quarantine, 15/28 lost openers,
2/7 seed-317 block closers, 3/20 seed-320 block closers, and 0/15 method closers.
The last is an uncovered class, as are the two truncated files; header and
field-initializer errors and truncated files remain phase 3b.

## Pinned Virgil revision comparison (2026-10-27)

The bump from `7cf038fc` to `57318d37` ([#311](https://github.com/SaltedTan/virgil-language-server/issues/311))
was measured from the same server sources at `239a65f2`, with pending documentation,
the same stable compiler executable, byte-identical generated `BuildInfo.v3`,
probes enabled, and the default 384 MiB worker heap. Both builds used this
physical checkout and equal-length `build/pin-old` and `build/pin-new` paths.
The build metadata records their actual source hashes; the shared BuildInfo
retains the old revision strings deliberately to avoid layout differences.

The entire run held the shared benchmark lock and ran on CPU 12 of the Intel
Core i7-14700KF host. Each pin used its own reviewed corpus: switch the gitlink
and corpus together before each invocation. Six alternating old/new, then
new/old process blocks ran each project with two warmups and five measured
operations per build, giving 30 samples per build, project, and operation.
Every semantic check passed, no samples were removed, and all 33 latency
comparisons have overlapping p10–p90 intervals. The short pin-update benchmark
also passed with one block, one operation, and no warmup.

Analysis times are median (p10–p90), milliseconds:

| Project / analysis | Old pin | New pin |
| --- | ---: | ---: |
| Small / fresh | 0.420 (0.414–0.454) | 0.421 (0.413–0.445) |
| Small / reused | 0.145 (0.130–0.162) | 0.143 (0.131–0.164) |
| Util / fresh | 14.830 (14.731–15.063) | 14.833 (14.748–15.022) |
| Util / reused | 12.871 (11.804–13.777) | 12.844 (11.878–13.914) |
| Aeneas / fresh | 349.211 (339.693–358.896) | 358.876 (346.122–364.555) |
| Aeneas / reused | 288.734 (284.006–314.423) | 297.046 (286.981–318.331) |

Small and util worker allocation, post-collection live bytes, and heap used
after handling match exactly. Aeneas grows from 205 files, 3,022,555 bytes,
and 83,322 lines to 206 files, 3,072,972 bytes, and 84,585 lines, including
the debugger line editor and expanded debugger and optimizer sources. Its
larger input explains additional parse, verify, occurrence, and encoding work;
these timings do not isolate the verifier fix from the changed corpus.
Aeneas worker memory is median (p10–p90), bytes:

| Metric | Old pin | New pin |
| --- | ---: | ---: |
| Fresh allocation | 193,927,776 (193,927,776–193,927,776) | 197,749,072 (197,749,072–197,749,072) |
| Reused allocation | 193,774,288 (193,764,240–193,778,576) | 197,602,440 (197,593,928–197,604,072) |
| Fresh heap after handling | 196,969,880 (196,969,880–196,969,880) | 200,841,688 (200,841,688–200,841,688) |
| Reused heap after handling | 196,992,704 (196,982,656–196,996,992) | 200,871,944 (200,863,432–200,873,576) |
| Live after collection, fresh and reused | 176,768 (176,768–176,768) | 177,344 (177,344–177,344) |
| Reserved worker heap | 402,653,184 (402,653,184–402,653,184) | 402,653,184 (402,653,184–402,653,184) |

Full tolerance runs on both builds each completed 420 single-edit state/mode
records and 72 session steps, with zero wrong answers and zero worker
replacements. The 185 primary states retain all four measured features in
116 warm and 110 cold states on both pins. There are 59 partial answers on
each, including session diagnostics; each is an exact subset of its own
current-program oracle. Reference lists follow the changed upstream uses and
source locations. The old run took 248.26 seconds and the new 250.89 seconds.

Those runs predate [body blanking](#body-blanking-comparison). After this change
was rebased onto main at `6e56a45`, which includes it, a full tolerance run on
the rebased build at the new pin completed 474 state/mode records and 72 session
steps, again with zero wrong answers, zero unmet control expectations, and zero
worker replacements, in 317.21 seconds. The 185 primary states retain all four
measured features in 118 warm and 118 cold states, the counts that the body
blanking comparison measured at the old pin. The 24 held-out states retain all
four in every state and mode. The generator picks them with its fixed seed from
the pinned Aeneas sources, which this bump changes, so 18 of the 24 are on
different files than at the old pin: 5 next-line, 6 body-end, and 7 two-bodies
states. There are 67 partial answers, including session diagnostics, as in that
comparison's run; each is an exact subset of its own current-program oracle.
