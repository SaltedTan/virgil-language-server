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
queued/canceled requests, members, and broader cursor distributions need separate
benchmarks.

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
Edit kinds are `body` (an expression or statement inside a routine or member
body), `header` (a condition or for header, or a declaration header such as a
parameter list, return type, or variable type), and `whole_file` (truncation).
Each fixture's kind is pinned in the script's classification table.
`summary.features` splits availability, correctness, exact matches, and wrong
answers by suite, mode, kind, and feature;
`summary.latency_ms` reports p50/p95 by suite, mode, and query kind. Definition is
queried first and includes analysis waiting; later requests generally use the
completed snapshot. Diagnostic latency is an **upper bound** from state start to
the post-navigation publication inspection, not the exact publication instant.
Process records retain worker replacements and stderr; probe-enabled builds add
the existing phase, allocation, and heap counters. RSS is sampled before shutdown.
These measurements include unavailable answers and must not be compared directly
with the successful-operation benchmark above.

`make test` runs scoring and cleanup regression checks and a five-state smoke
replay (two small repair states and all three negative controls, in both modes).
The smoke run takes about 0.2 seconds on the reference host. The full suite
remains opt-in. Run these checks separately with:

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

| Corpus (per mode) | States with all four features | Correct definition / hover / reference answers, each | Correct diagnostic sets | Wrong answers |
| --- | ---: | ---: | ---: | ---: |
| Repair | 6/45 | 15/87 | 6/45 | 0 |
| Replay | 9/140 | 35/540 | 9/140 | 0 |
| Combined | 15/185 | 50/627 | 15/185 | 0 |

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
