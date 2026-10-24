# Performance benchmarks

The benchmark measures successful whole-program analyses and editor requests over
real stdio pipes. It uses Python 3.9+ and its standard library, including the
[`Session` protocol client](../test/protocol/worker.py). Timing budgets and scheduled
CI comparisons are separate work; these measurements do not gate releases.

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

`make test` runs the small smoke case and the client's rejection checks. The smoke
case takes under a second on the reference host; the full benchmark is not part
of `make test`. Smoke runs need no benchmark build metadata. The tests compare
the corpus revision to the superproject's `vendor/virgil` gitlink in the index
(which matches `HEAD` unless a pin bump is staged). A changed gitlink requires
corpus regeneration. The manifest must list exactly the three reviewed projects
(`small`, `util`, and `aeneas`) in every mode; deleting one cannot skip it. If
the checkout differs from a matching gitlink (the advisory Virgil-master
workflow), or there is no Git metadata, they print one notice and skip vendor
corpus projects while checking the small fixture. Strict benchmarks always
require matching pins and hashes.
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
require no diagnostics, the exact source list, current document versions, and an
advancing generation. Navigation checks the definition's exact location, hover's
signature prefix and range, and every reference location. Completion requires the
named semantic item and its detail, so a keyword-only fallback fails. Rename
requires every expected edit, the new name, and each document's expected version;
the returned edits are never applied. Diagnostics must have the exact code,
message, range, source, severity, URI, and edited version.

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
queued/canceled requests, members, and broader cursor distributions need separate
benchmarks.

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
| worker `gc_count`, `allocated_bytes` | Collections and allocation from request decoding through result encoding; exclude the explicit final collection. Allocation is the pinned runtime's cumulative allocated bytes plus current-cycle allocation delta. |
| server `gc_count`, `allocated_bytes` | Corresponding deltas from supervisor admission through result decoding; exclude capture and later LSP response rendering. |
| worker `force_gc_us`, `live_after_gc_bytes` | Explicit final collection duration and the existing retirement-policy live-byte count, excluding the result byte-array length. One final collection runs per transaction. |
| worker `heap_after_handle_bytes`, server `heap_after_decode_bytes` | Space used at those boundaries, not peak live memory. |
| worker `result_bytes`, server `worker_heap_bytes` | Encoded RESULT frame bytes and the worker's actual reserved heap from HELLO. |

Phase times include GC that occurs within the phase; do not add `adapter_us` to
parse/verify, or `roundtrip_us` to its constituent phases. Probe syscalls add some
end-to-end overhead despite allocating nothing. Prefer normal builds for the
most precise external latency comparison and instrumented builds for attribution.

JSON includes raw samples grouped by build, project, and process block; summaries
report sample count, median, linearly interpolated p10/p90, min/max, and median
absolute deviation (MAD), with no trimming. Summaries pool measured operations;
use raw block IDs when comparing correlated samples. Process records include
current RSS, and Linux adds process peak RSS when readable; macOS uses `ps` and
leaves peak RSS null. Metadata includes the corpus, both revisions, generated
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

## Reference baseline

Measured on 2026-10-25 at server source revision
`154f08c7187452272ace803a995260306c0f2597`, Virgil
`7cf038fc13a5474836bec8b14c7a3e796fd07fa5`. The host was an Intel Core i7-14700KF
(28 logical CPUs), Linux `7.0.0-38-generic` x86-64/glibc 2.43, Python 3.14.4,
pinned with `taskset` to CPU 10; its readable governor was `powersave`. CPU 10 and
its sibling were idle when selected; other agents used other cores, including
CPU 4. The worker reserved 402,653,184 bytes (384 MiB), and the server used the
default 200 MiB heap. These are reference measurements, not budgets.

The comparison kept three builds in this physical checkout: baseline `6406edc`
(`build/base-0`), cached keyed declaration hashes at `32cf580` (`build/hash-0`),
and both that change and smaller RESULT buffer growth at `154f08c`
(`build/next-0`). Each was built with `BENCH=1 all bench-metadata` at its own
revision. Their directory names and BuildInfo revision strings have equal
lengths. A second set with suffix `-1` used `WORKER_HEAP=1g`. All three corpus
projects ran with two warmups and five measured operations in each of three
process blocks; build order reversed each block:

```sh
python3 scripts/bench-lsp.py \
  --exe build/base-0/virgil-lsp --exe build/hash-0/virgil-lsp \
  --exe build/next-0/virgil-lsp --require-probes \
  --blocks 3 --operations 5 --warmups 2 --cpu 10 \
  --output build/bench-results/compare-default.json
```

Repeat with the `-1` executables for the 1 GiB control. The retained evidence files
are `baseline.json`, `compare-default.json`, `compare-1g.json`,
`repeat-default.json`, and `repeat-1g.json`; the repeats select `--project aeneas`
with the same blocks, operations, and warmups. Each JSON records binary and
compiler hashes, exact build inputs, every sample, and stderr traces. Every
operation passed semantic/version checks and complete probe checks. No outliers
were removed.

The current default-heap baseline follows. Each cell is **median (p10–p90), in
milliseconds**, from 15 measured operations in the first comparison.

| Scenario | small | util | Aeneas |
| --- | ---: | ---: | ---: |
| `fresh_analysis` | 0.455 (0.441–0.500) | 15.459 (15.293–15.784) | 318.797 (313.889–353.017) |
| `reused_analysis` | 0.136 (0.130–0.143) | 13.298 (12.852–13.764) | 292.147 (269.654–312.525) |
| `syntax_diagnostic` | 0.073 (0.071–0.082) | 0.309 (0.286–0.325) | 0.403 (0.394–0.443) |
| `semantic_diagnostic` | 200.667 (200.627–200.701) | 214.682 (214.363–217.621) | 518.104 (496.495–562.658) |
| `completion_miss` | 0.683 (0.655–0.841) | 8.230 (7.868–8.726) | 130.520 (124.821–143.977) |
| `completion_hit` | 0.470 (0.441–0.579) | 0.743 (0.712–0.763) | 2.708 (2.637–4.209) |
| `definition` | 0.074 (0.070–0.104) | 0.379 (0.370–0.396) | 1.775 (1.608–1.989) |
| `hover` | 0.068 (0.066–0.091) | 0.369 (0.363–0.383) | 1.839 (1.686–15.969) |
| `references` | 0.074 (0.073–0.098) | 0.407 (0.396–0.426) | 2.217 (2.057–2.518) |
| `prepareRename` | 0.173 (0.164–0.185) | 1.929 (1.896–2.006) | 18.487 (18.104–19.583) |
| `rename` | 0.458 (0.442–0.484) | 16.859 (16.572–17.610) | 323.467 (307.194–335.884) |

The same-run Aeneas comparison is below; cells are **median (p10–p90), ms**.
The 1 GiB control separates encoder work from the default heap's collection
boundary.

| Scenario | 384 MiB before | 384 MiB after | 1 GiB before | 1 GiB after |
| --- | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 364.715 (360.094–375.688) | 318.797 (313.889–353.017) | 358.381 (350.637–381.202) | 324.729 (318.360–330.614) |
| `reused_analysis` | 484.705 (458.007–546.474) | 292.147 (269.654–312.525) | 326.933 (311.123–351.139) | 289.532 (275.309–313.900) |
| `semantic_diagnostic` | 532.326 (525.075–546.358) | 518.104 (496.495–562.658) | 547.567 (530.398–616.160) | 510.274 (494.536–513.267) |
| `completion_miss` | 138.014 (132.737–149.929) | 130.520 (124.821–143.977) | 136.239 (132.099–149.405) | 127.574 (124.188–141.187) |
| `rename` | 368.989 (349.661–392.806) | 323.467 (307.194–335.884) | 360.162 (348.671–365.117) | 322.269 (309.533–325.990) |

Aeneas worker phase medians in the default-heap comparison follow. Time rows
are **milliseconds**; counts and bytes retain their units. `adapter_us` overlaps
parse and verify, so it is omitted here.

| Metric | Fresh before | Fresh after | Reused before | Reused after | Completion miss before | Completion miss after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Capture, ms | 9.889 | 9.729 | 8.142 | 9.432 | 11.828 | 12.304 |
| Request decode, ms | 2.809 | 2.964 | 2.146 | 2.114 | 2.125 | 2.149 |
| Parse, ms | 64.359 | 63.845 | 41.798 | 40.287 | 45.535 | 44.918 |
| Verify, ms | 49.014 | 47.229 | 37.054 | 36.389 | 38.933 | 38.683 |
| Collect, ms | 113.670 | 110.010 | 95.462 | 94.080 | 0.000 | 0.000 |
| Query, ms | 0.001 | 0.002 | 0.001 | 0.001 | 1.026 | 1.002 |
| Request encode, ms | 5.260 | 5.125 | 4.244 | 4.095 | 3.991 | 4.030 |
| Result encode, ms | 90.792 | 55.588 | 238.963 | 52.561 | 0.916 | 0.632 |
| Result decode, ms | 15.738 | 15.572 | 13.353 | 13.904 | 0.279 | 0.259 |
| Final GC, ms | 3.643 | 1.701 | 21.814 | 17.932 | 23.083 | 17.166 |
| GCs during handle | 0 | 0 | 1 | 0 | 0 | 0 |
| Allocated bytes during handle | 197,599,392 | 193,653,792 | 197,445,968 | 193,500,368 | 159,740,296 | 159,713,112 |
| Heap bytes after handle | 200,643,952 | 196,698,352 | 129,150,584 | 200,915,592 | 171,183,872 | 167,128,344 |
| Live bytes after final GC | 176,769 | 176,768 | 176,769 | 176,768 | 176,772 | 176,768 |
| RESULT frame bytes | 3,936,046 | 3,936,046 | 3,936,046 | 3,936,046 | 59,140 | 59,140 |

Caching each worker declaration's existing keyed path/position hash removes
repeated hashing in call keys and the encoder's declaration index. In the first
1 GiB protocol comparison, hash caching alone reduced fresh encoding from
88.675 to 55.687 ms and reused encoding from 89.217 to 52.343 ms, with identical
transaction allocation. Full equality checks, keyed string hashing, and type
collision handling remain in place.

The RESULT writer doubles power-of-two buffers instead of the library writer's
roughly tripled capacity for small appends. For the successful corpus analysis,
this saves 3,945,600 allocated bytes per transaction in the protocol workload.
The previous RESULT buffer is also smaller while the next analysis runs. The
reused default worker therefore avoids the collection during encoding; all
15 baseline and hash-only reused samples collected once, and all 15 final
samples collected zero times. Aeneas fresh handling uses 196,698,352 bytes,
leaving 4,628,240 bytes of its 201,326,592-byte semispace. Reused handling uses
200,915,592 bytes without collecting, leaving only 411,000 bytes: path lengths
and worker history can still move a program across that boundary. Its higher
post-handle heap than the baseline's 129,150,584 bytes reflects the avoided GC,
not increased retained analysis state.

Growth savings depend on the write sequence. The semantic-error workload
allocates 1,253,008 more bytes during handling, while its post-handle heap is
2,775,336 bytes lower because its preceding result is smaller. Its first-run
latency spread is large; use the repeated comparison below when assessing it.
Do not infer a universal allocation or latency improvement for every result.

A separate scratch attribution run used a 1 GiB heap, CPU 10, and ten reused
Aeneas analyses per variant. Rows that omit data are attribution controls only;
they are not product changes or correctness-preserving alternatives. Times are
medians in ms, and allocations are bytes per phase.

| Scratch variant | Collect, ms | Collect bytes | Encode, ms | Encode bytes |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 94.040 | 55,083,264 | 84.022 | 16,789,000 |
| Omit declaration signatures (drops data) | 84.790 | 54,062,144 | 77.420 | 15,994,304 |
| Omit call signatures (drops data) | 77.734 | 53,053,424 | 76.416 | 16,568,040 |
| Omit calls (drops data) | 69.752 | 51,741,992 | 69.190 | 16,567,944 |
| Cache keyed declaration hash | 93.091 | 55,083,264 | 48.376 | 16,789,000 |
| Reserve 4 MiB result buffer (control) | 91.255 | 55,083,264 | 77.769 | 8,535,960 |

None of those 1 GiB attribution phases collected. Scratch default-heap variants
and a separate final-writer probe retain per-phase allocation and collection
counts too. The latter reduces encode allocation from 16,789,000 to 12,730,096
bytes, with collection allocation unchanged at 55,083,264 bytes; its absolute
paths differ from the protocol workload, so its byte totals are not substituted
for the protocol table. A callback-based growth prototype was rejected because
it retained a few extra bytes. The final writer adds no fields or callback
state, and its bulk writes and header patches have a regression check.

Normalized RESULT data is byte-identical before, after hash caching, and after
both fixes: 21 cases at both heaps, each in fresh and reused workers. The cases
include all three corpus projects, semantic and syntax errors, occurrence and
call coverage, duplicate-use fixtures, and scope/member/call answers. Only the
three elapsed timing fields and reported live bytes were normalized. The wire
format, decoded allocation charges, frame and decode budgets, heap reservation,
and retirement policy are unchanged. Fresh live-after-GC bytes are no higher
on any corpus project (small 1,161 to 1,160; util 42,473 to 42,472; Aeneas
176,769 to 176,768). The one-byte difference is buffer alignment, not a retained
object removed from the analysis.

Aeneas confirmation runs used the same binaries and another 15 measured
operations per build at each heap. Median latencies in ms:

| Scenario | 384 MiB before | 384 MiB after | 1 GiB before | 1 GiB after |
| --- | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 370.450 | 331.045 | 358.933 | 324.323 |
| `reused_analysis` | 476.022 | 291.232 | 325.405 | 289.750 |
| `semantic_diagnostic` | 532.346 | 513.680 | 543.681 | 500.862 |
| `completion_miss` | 135.089 | 128.796 | 136.028 | 126.488 |
| `rename` | 357.355 | 323.022 | 364.342 | 318.147 |

Treat small-project and fast-request differences inside their observed spread
as noise. No macOS timing baseline or normal-build observer comparison was
collected for these changes.

The confirmation preserves the default reused-analysis GC change in all 30
measured operations across both runs: one collection before and zero after.
Semantic-diagnostic medians improve, but their p10–p90 intervals overlap
substantially; these shared-host runs do not establish a firm diagnostic
latency gain.

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
syscalls remain. It establishes no sub-millisecond equivalence. No macOS timing
baseline was collected.
