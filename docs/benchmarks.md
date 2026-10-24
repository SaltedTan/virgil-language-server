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

Measured on 2026-10-24 at server source revision
`2c1ebafd31ad4d40019e8f12c06aef2b167f144a`, Virgil
`7cf038fc13a5474836bec8b14c7a3e796fd07fa5`. The host was an Intel Core i7-14700KF
(28 logical CPUs), Linux `7.0.0-38-generic` x86-64/glibc 2.43, Python 3.14.4,
pinned with `taskset` to CPU 4; its readable governor was `powersave`. The worker
reserved 402,653,184 bytes (384 MiB), and the server used the default 200 MiB heap.
Other work ran on this shared host. These are reference measurements, not budgets.

The first run used this command, after creating `build/bench-results`:

```sh
make bench BENCH_ARGS='--blocks 3 --operations 5 --warmups 2 --cpu 4 --output build/bench-results/run-1.json'
```

The repeat used the same binaries without rebuilding:

```sh
python3 scripts/bench-lsp.py --exe build/bench/virgil-lsp --require-probes \
  --blocks 3 --operations 5 --warmups 2 --cpu 4 --output build/bench-results/run-2.json
```

Both files record server SHA-256
`1dda2bfb4b8f5f7d6398a840be7246644967717c8ea288b9b78403ca74e039fa`
and worker SHA-256
`a6d2814a727b3991ec04e9f87df44d81753a721e8864ba6693f0b2c1393b6ed2`.
Every operation passed semantic/version checks and complete probe checks. Each
cell below is the first run's **median (p10–p90), in milliseconds**, from 15 measured
operations over three process blocks; raw JSON also includes min/max and MAD.

| Scenario | small | util | Aeneas |
| --- | ---: | ---: | ---: |
| `fresh_analysis` | 0.464 (0.430–0.492) | 16.576 (15.859–17.686) | 363.062 (357.443–367.868) |
| `reused_analysis` | 0.139 (0.131–0.153) | 14.162 (13.602–15.275) | 470.304 (464.167–500.762) |
| `syntax_diagnostic` | 0.073 (0.068–0.086) | 0.300 (0.272–0.342) | 0.411 (0.401–0.435) |
| `semantic_diagnostic` | 200.677 (200.566–200.710) | 215.598 (215.088–216.176) | 525.567 (511.718–532.621) |
| `completion_miss` | 0.689 (0.644–0.764) | 7.873 (7.570–8.710) | 135.928 (132.513–149.710) |
| `completion_hit` | 0.452 (0.444–0.513) | 0.731 (0.693–0.822) | 2.792 (2.705–3.046) |
| `definition` | 0.072 (0.070–0.074) | 0.391 (0.379–0.409) | 1.840 (1.593–2.023) |
| `hover` | 0.069 (0.066–0.072) | 0.385 (0.365–0.404) | 1.897 (1.675–15.796) |
| `references` | 0.074 (0.072–0.079) | 0.420 (0.398–0.439) | 2.370 (2.246–2.530) |
| `prepareRename` | 0.172 (0.167–0.179) | 1.980 (1.938–2.091) | 18.955 (18.642–19.897) |
| `rename` | 0.463 (0.448–0.469) | 17.713 (17.137–18.874) | 362.372 (352.339–372.686) |

The repeat's median changes ranged from −2.5% to +5.7% for small, −2.8% to +2.6%
for util, and −9.3% to +0.6% for Aeneas. Aeneas fresh analysis moved from 363.062
to 348.685 ms (−4.0%); reused analysis from 470.304 to 462.337 ms (−1.7%);
completion miss from 135.928 to 133.164 ms (−2.0%); and semantic publication from
525.567 to 528.827 ms (+0.6%). The largest relative Aeneas movement was hover,
1.897 to 1.721 ms (−9.3%, only −0.176 ms). Its first-run p90 above also shows a
large tail. Treat such differences as host/GC variability, not an optimization.

First-run Aeneas phase medians follow. Time rows are **milliseconds**; count and
byte rows retain their units. These come from the same 15 operations per scenario.

| Metric | Fresh full analysis | Reused full analysis | Completion miss |
| --- | ---: | ---: | ---: |
| Capture, ms | 9.940 | 8.162 | 12.216 |
| Request decode, ms | 2.814 | 2.149 | 2.145 |
| Parse, ms | 64.791 | 41.618 | 45.758 |
| Verify, ms | 48.524 | 36.323 | 38.845 |
| Collect, ms | 113.226 | 95.612 | 0.000 |
| Query, ms | 0.001 | 0.001 | 1.026 |
| Request encode, ms | 5.306 | 4.138 | 4.053 |
| Result encode, ms | 91.356 | 234.496 | 0.898 |
| Result decode, ms | 15.544 | 13.570 | 0.268 |
| Final GC, ms | 3.720 | 24.461 | 23.670 |
| GCs during handle | 0 | 1 | 0 |
| Allocated bytes during handle | 197,599,392 | 197,445,968 | 159,740,296 |
| Heap bytes after handle | 200,643,952 | 129,150,584 | 171,183,872 |
| RESULT frame bytes | 3,936,046 | 3,936,046 | 59,140 |

The fresh full analysis stays just below its semispace capacity of 201,326,592
bytes; the reused full analysis collects during handling, so its encode interval
includes GC. Source path lengths and worker history matter when interpreting that
boundary.

A separate three-run heap control used the original worker at `9a9e384`, the new
worker with probes disabled, and the enabled worker, with identical source paths
and matching BuildInfo in every build. All nine fresh Aeneas analyses used exactly
**200,643,960 bytes after handling, with zero collections**: instrumentation added
**0 transient bytes** at that boundary. The normal server and worker contained
neither the `BENCH ` marker nor the probe metric names checked. The baseline above
uses 8 fewer heap bytes because its clean revision string is shorter than the
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

## Semantics during syntax errors

[`scripts/bench-tolerance.py`](../scripts/bench-tolerance.py) measures the semantic
answers an editor receives while another file fails to parse. It imports the
existing benchmark helpers and `Session`; a separate command keeps the successful
operation timings and output of `bench-lsp.py` unchanged. No server fallback is
implemented. Run both modes and all states with:

```sh
mkdir -p build/bench-results
make BUILD=build/bench BENCH=1 all bench-metadata
python3 scripts/bench-tolerance.py --exe build/bench/virgil-lsp --cpu 4 \
  --output build/bench-results/tolerance.json
```

A normal executable works too, after `make all bench-metadata`; its probe arrays
are empty. `--suite repair|replay|negative|extra` selects a group, and `--only TEXT`
filters state IDs. These filters appear in the recorded command. `--work-dir`
chooses the parent of disposable source copies (default `build/bench-results`);
use scratch storage on machines with a small shared `/tmp`. Output directories
must already exist. No fixture, vendor source, or real project file is edited.
The driver rechecks input hashes and removes its copies. Failed runs preserve
partial results and a failing session's transcript in `<output>.failure.json`.

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
they have no analyzed predecessor, although the disposable disk copy ordinarily
still contains the original program. Closed-file controls instead put the broken
text on disk. Each replay state gets its own session, seeded from the sequence's
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
reference and diagnostic sets ignore ordering only. `available` counts a nonempty
answer or an oracle-matching legitimate empty answer. `correct` counts an exact
match. Missing a nonempty oracle is unavailable and incorrect, but is not a
`wrong` binding. A nonempty mismatch is wrong, including a partial reference or
diagnostic set.

Three negative controls delete a declaration, change its signature, or introduce
a shadowing local, alongside a syntax error. Their policy accepts suppression or
the current program's oracle. The driver separately feeds each predecessor's
captured answer to the scorer and requires a wrong verdict. The
[scoring tests](../test/benchmarks/test_tolerance.py) repeat this with
[protocol captures](../test/benchmarks/tolerance-negative-oracles.json), including
the signature change whose identifier ranges still match. No permissive server
fallback is built. Fifteen extra states cover three variants each of two broken
files, fresh intact-file type errors, text coordinates (two UTF-16 cases with
non-ASCII before the query and one CRLF case), closed-file disk changes with
watched-file notifications, and project-file changes (remove/add membership and
change a language flag). The pinned compiler rejects CRLF, so that case uses an
LF oracle and expects semantic suppression today.

JSON has the performance harness's revision, binary SHA-256, build, host, affinity,
command, and input-hash metadata. `states` retains mode, seed policy, edit kind,
text hashes, current parse outcomes, snapshots, answers, scores, and raw samples.
Edit kinds are `body`, `header` (condition/for headers and declaration headers in
controls), and `whole_file` (truncation). `summary.features` splits availability,
correctness, exact matches, and wrong answers by suite, mode, kind, and feature;
`summary.latency_ms` reports p50/p95 by suite, mode, and query kind. Definition is
queried first and includes analysis waiting; later requests generally use the
completed snapshot. Diagnostic latency is an **upper bound** from state start to
the post-navigation publication inspection, not the exact publication instant.
Process records retain worker replacements and stderr; probe-enabled builds add
the existing phase, allocation, and heap counters. RSS is sampled before shutdown.
These measurements include unavailable answers and must not be compared directly
with the successful-operation benchmark above.

`make test` runs nine scoring tests and a five-state smoke replay (two small
repair states and all three negative controls, in both modes). The smoke run
takes about 0.2 seconds on the reference host. The full suite remains opt-in.

### Tolerance baseline

Measured on 2026-10-25 at `220733d` (server sources unchanged from `6406edc`) and pinned Virgil
`7cf038fc13a5474836bec8b14c7a3e796fd07fa5`, with probes enabled, the default
384 MiB worker heap, CPU 4, Python 3.9.25, and the Intel Core i7-14700KF host
described above. The build's `-dirty` marker reflects pending documentation only.
Other work ran on this machine. Warm and cold counts were identical:

| Corpus (per mode) | States with all four features | Correct definition / hover / reference answers, each | Correct diagnostic sets | Wrong answers |
| --- | ---: | ---: | ---: | ---: |
| Repair | 6/45 | 15/87 | 6/45 | 0 |
| Replay | 9/140 | 35/540 | 9/140 | 0 |
| Combined | 15/185 | 50/627 | 15/185 | 0 |

The same all-feature state counts split by edit kind follow, for either mode:

| Corpus | Body | Header | Whole file |
| --- | ---: | ---: | ---: |
| Repair | 6/41 | 0/2 | 0/2 |
| Replay | 7/78 | 2/62 | — |

Available answers were all correct. All three negative controls passed the safe
suppression policy in both modes and rejected the injected predecessor answer.
The fifteen extra states produced no wrong answers. This establishes the current
low-availability baseline; these deterministic cases do not estimate availability
across arbitrary editing sessions.

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
