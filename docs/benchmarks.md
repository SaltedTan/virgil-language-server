# Performance benchmarks

The performance benchmark measures whole-program analyses and editor
requests over real stdio pipes. The [syntax-error tolerance benchmark](#semantics-during-syntax-errors)
measures semantic availability and correctness while editing. Both use Python
3.9+ and its standard library, including the
[`Session` protocol client](../test/protocol/worker.py). The [weekly comparison](#scheduled-comparison)
reports changes without timing budgets; these measurements do not gate releases. A measured performance-budget proposal, marked as not adopted, is in [performance-budgets.md](performance-budgets.md).

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
python3 scripts/bench-lsp.py --smoke --exe build/virgil-lsp --output build/lsp-smoke.json
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
analyses in one worker; process command lines record the override. The separate
[memory soak](#memory-soak) covers default worker retirement; members and broader
cursor distributions need separate benchmarks. The
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

### Parse work

With `--parse-work`, each block runs one session per project of fixed edits and
controls instead of the scenarios above; like `--first-semantic`, it can't be
combined with another workload option. It measures how much of an edit's
latency goes to parsing files that didn't change, for
[#359](https://github.com/SaltedTan/virgil-language-server/issues/359). After
the unsaved project configuration is open, the query file gets the pinned
unresolved-name edit, whose exact diagnostic is the semantic oracle for every
sample, then:

| JSON scenario | Timed interval |
| --- | --- |
| `unchanged_reanalysis` | `virgil-lsp/analyze` for the query document's project, with no change since the last analysis, to its reply. |
| `body_edit` | A full-text `didChange` that changes one expression in a method body, to the query file's semantic publication at that version, including the 200 ms debounce. |
| `signature_edit` | The same for a method signature, written with an equivalent type (`i32` for `int`, `Array<byte>` for `string`). |
| `membership_add`, `membership_remove` | A configuration `didChange` that adds `lib/math/Math.v3` to the program through `virgilDependencies`, or removes it again, to the query file's next nonempty publication. |
| `options_set`, `options_reset` | The same for adding `-lang:open-types`, a language option that the parser reads and the corpus doesn't need, to `compilerArgs`, or removing it. |

The edits are fixed anchors in the query file, each of which must occur once, and
iterations alternate two replacements, so every edit changes the text the
previous analysis parsed; neither moves a line, so the oracle's range holds. An
edit's parser publication is empty, and a configuration change clears the
project's semantic diagnostics before the replacement analysis, so only that
analysis can end an interval. An untimed snapshot then must hold exactly the
oracle, with the current version and, after a membership change, the new
document list. Configuration changes aren't debounced. The edits, all three
configurations, and the hashes of every text are recorded in each block's
`parse_work`.

With `--require-probes`, every sample must have exactly one worker transaction
and source capture, and no worker start. A build either has the parse-work
probes below in every transaction or in none, so an older build can be the
control; at least one build must have them. Likewise, a build either splits
every capture with `capture_read_us` and `capture_hash_us` or has neither, so
a build from before that split can be the control.

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
| server `capture_read_us`, `capture_hash_us` | Of `capture_us`: acquiring each source's bytes (`ProjectFiles.load` for a closed project file, `DocumentStore.readBounded` otherwise), and hashing a closed file's text. Their clock reads, two per source and two more per closed file, are inside `capture_us`. |
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
| worker `parse_unchanged_files`, `_bytes`, `_us`; `parse_changed_files`, `_bytes`, `_us` | A whole-program parse's files, their bytes, and the sum of their parse times, split by whether the previous analysis in the same worker parsed the same path and bytes with the same language options. Every file parsed by a fresh worker's first analysis is changed, and so is a file the previous analysis skipped at the error limit. Each file's time is the difference of two `System.ticksUs` calls around its parse, within `parse_us`. |
| worker `parse_unvisited_files`, `parse_classify_us` | Files the parse didn't reach because of the error limit, and the time to hash the paths and bytes and classify them, which runs before `parse_us` starts, within `adapter_us`. |
| server `substitution_us`, `substituted_files` | Method-body fallback and current-text repair for a project submission, after `capture_us`, and how many files they substituted. |
| server `prepare_us` | Capture through any substitution and snapshot admission, to the worker request, for automatic and explicit analyses, including explicit URI lists. |
| server `complete_us` | An automatic analysis's completion after `roundtrip_us`: recording document versions, updating the last-parseable text cache, and publishing diagnostics. |
| server `parser_diagnostics_us` | An open document's parse for its syntax diagnostics, on opening, an accepted text edit, or a change to its project's language options, before any whole-program analysis. |

Phase times include GC that occurs within the phase; do not add `adapter_us` to
parse/verify, or `roundtrip_us` to its constituent phases. Probe syscalls add some
end-to-end overhead. Prefer normal builds for the most precise external latency
comparison and instrumented builds for attribution.

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
`--error-result` or [`--parse-work`](#parse-work) run also keep a
`resource_summary`; the default run isn't sampled. A sampler thread reads the server and its child processes every 50 ms,
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

### Unchanged-file parse work (2026-11-01)

Measured on 2026-11-01 for [#359](https://github.com/SaltedTan/virgil-language-server/issues/359)
on the i7-14700KF host of the [reference baseline](#reference-baseline), with
pinned Virgil `57318d37` and the default 384 MiB worker heap (402,653,184 bytes
reserved, as each worker's HELLO reported). The control is main `e705e44e`,
which has no parse-work probes; the probed build is `242c3407`. Both were built
in this physical checkout, in equal-length `build/c359b` and `build/p359b`
directories, with `BENCH=1 all bench-metadata` and byte-identical generated
BuildInfo text (revision `p359-e705e44e8000`). SHA-256 hashes: control server
`818364973ba07a65cb5f5fa431ab236af1ccf6070a8f00e004c694041b030370` and worker
`a7f99ff8d5033e93ebcbe2f213d7d0938533376f4e735a6d441fbd4d3f26f348`; probed
server `2b5c640f1d965dfaf45e70b57472b0faae22df1be454dff83d61fe7e36aa55e4` and
worker `6b6bdca886e70f4c752f69eb80f89ba99c41d8b7fdb43a818cf549673bf606bd`;
corpus manifest `79ff4b89d9b18251202dadefd53cef96f2089c7949c59d52400495113e001c15`.
The run held the shared benchmark lock and ran on hardware thread 12 (governor
`powersave`), with every semantic check passing and no samples removed:

```sh
python3 scripts/bench-lsp.py --exe build/c359b/virgil-lsp --exe build/p359b/virgil-lsp --parse-work \
  --blocks 6 --operations 5 --warmups 2 --cpu 12 --require-probes \
  --output build/bench-results/parse-work.json
```

Each cell is **p50 / p95, in milliseconds**, of the probed build over 30
measured operations, with the percentile definition [above](#phases-allocation-and-output).
The edits include the 200 ms debounce. Every control p50 is within 3.1 ms of the
probed build's, and within 2.3 ms (0.5%) for Aeneas:

| Scenario | small | util | Aeneas |
| --- | ---: | ---: | ---: |
| `unchanged_reanalysis` | 0.293 / 1.344 | 14.349 / 15.697 | 318.543 / 344.969 |
| `body_edit` | 200.842 / 201.992 | 219.097 / 226.878 | 521.325 / 554.350 |
| `signature_edit` | 201.670 / 202.301 | 218.833 / 221.113 | 519.699 / 551.415 |
| `membership_add` | 0.887 / 0.988 | 14.918 / 15.584 | 297.746 / 305.461 |
| `membership_remove` | 0.426 / 0.481 | 14.368 / 15.022 | 321.848 / 329.651 |
| `options_set` | 0.400 / 0.434 | 14.692 / 15.268 | 299.126 / 310.286 |
| `options_reset` | 0.385 / 0.439 | 14.669 / 15.648 | 322.652 / 332.905 |

Every sample's classification was as designed: an edit changed one file and
left the rest unchanged (Aeneas: 205 files, 3,064,615 bytes, unchanged; the
edited file's 8,394 or 8,397 bytes changed), adding `Math.v3` changed only it, removing it
changed none, and an option change changed every file. No parse stopped at the
error limit. The probed build's classifier compared each file with every file of
the previous analysis, even one that analysis had skipped at the error limit
and so never parsed, and counted it unchanged if its bytes matched. It now
compares only with files the previous analysis parsed. Since no parse in these
runs skipped a file, the fix changes no classification here; its
allocation is given with the observer cost below. The Aeneas phases, from the same
samples, p50 / p95 in milliseconds:

| Phase | `body_edit` | `unchanged_reanalysis` | `membership_add` | `options_set` |
| --- | ---: | ---: | ---: | ---: |
| server `prepare_us` | 17.397 / 24.631 | 12.440 / 15.660 | 12.306 / 12.803 | 12.466 / 13.041 |
| of which `capture_us` | 17.049 / 24.227 | 12.013 / 15.136 | 11.881 / 12.383 | 11.882 / 12.484 |
| of which `substitution_us` | 0.392 / 0.471 | 0.425 / 0.512 | 0.391 / 0.465 | 0.545 / 0.658 |
| server `parser_diagnostics_us` | 0.134 / 0.141 | — | — | 0.119 / 0.143 |
| request encode + decode | 6.230 | 6.219 | 6.164 | 6.175 |
| worker `parse_us` | 43.209 / 44.810 | 42.855 / 44.501 | 43.072 / 45.017 | 43.034 / 44.690 |
| of which unchanged files | 43.028 / 44.624 | 42.781 / 44.416 | 42.882 / 44.816 | 0 |
| of which changed files | 0.117 / 0.123 | 0 | 0.103 / 0.116 | 42.968 / 44.620 |
| worker `parse_classify_us` | 1.234 / 1.312 | 1.246 / 1.390 | 1.246 / 1.373 | 1.250 / 1.351 |
| worker `verify_us` | 48.309 / 49.864 | 38.113 / 44.425 | 36.794 / 41.998 | 37.084 / 42.600 |
| worker `collect_us` | 106.665 / 114.442 | 102.083 / 111.872 | 102.768 / 107.627 | 103.395 / 109.247 |
| worker `result_encode_us` | 55.300 / 63.384 | 52.027 / 58.676 | 51.688 / 54.352 | 52.431 / 55.740 |
| server `result_decode_us` | 29.919 / 58.855 | 46.305 / 55.661 | 26.168 / 27.333 | 26.474 / 29.961 |
| server `roundtrip_us` | 295.916 / 329.908 | 293.680 / 318.389 | 274.491 / 282.457 | 277.348 / 288.007 |
| server `complete_us` | 4.303 / 7.568 | — | 4.866 / 8.484 | 4.768 / 7.886 |
| worker `force_gc_after_send_us` | 43.156 / 52.202 | 38.072 / 50.011 | 37.280 / 40.725 | 37.752 / 40.729 |

Request encode + decode is the sum of the two p50s. The worker collected zero
times while handling every sample of every project, in both builds; the server
collected at most once, in Aeneas's result decoding, whose p50 and p95 differ by
that collection. The collection after sending overlaps the server's decoding
and publication, so it isn't in these latencies; it delays only a request that
reaches the worker sooner. Capture, which reads and hashes the 205 closed
Aeneas files again for every analysis, is most of `prepare_us`.

Small and util follow the same pattern at their scale. A util edit spends
3.486 ms preparing, of which 3.260 ms is capture, then 2.466 ms parsing, 2.393 ms
of it in the 30 unchanged files, 1.533 ms verifying, 4.107 ms collecting, and
1.967 and 0.718 ms encoding and decoding the result, in a 13.854 ms round trip.
Small's whole round trip is 0.217 ms, its parse 0.026 ms.

**The best-case saving.** If unchanged files cost nothing to parse, an edit
would save their parse time and nothing else, since verification, collection,
encoding, and decoding still cover every file (see [parsed-tree ownership](architecture.md#parsed-tree-ownership)):

| Project | Unchanged-file parse, `body_edit` | Share of the edit's latency | Share without the debounce |
| --- | ---: | ---: | ---: |
| small | 0.008 ms | 0.004% | 1.1% |
| util | 2.393 ms | 1.1% | 15.8% |
| Aeneas | 43.028 ms | 8.3% | 13.5% |

Shares are the p50 of each sample's own ratio. The signature edit's are the
same within 0.1 points; the membership changes save the same 42.9 ms of Aeneas's
297.7 to 321.8 ms, 14.5% and 13.3%, and an option change saves nothing. The
unchanged reanalysis saves 13.5% of 318.5 ms. Small's absolute saving is a
few microseconds per edit.

That ceiling omits costs that a cache would add and that weren't measured,
because no prototype was authorized: a clean copy of each reused file's tree
before every verification (or the reset it would replace), rebinding its type
parameters to the new program's type cache, and the cache's own memory, which
for Aeneas would hold the parse of 3 MB of source in a worker that ends an
edit's analysis with about 13.6 MB of room under its collection boundary. Finding each
file's entry would cost at least what `parse_classify_us` measures: 1.25 ms for
Aeneas to hash every path and byte. Server-side time not covered by any probe,
such as the remainder of the debounce wait, frame transfer within `roundtrip_us`,
and JSON writing after `complete_us`, isn't separately measurable here.

**Observer cost.** The probed build's whole-loop `parse_us` is 0.63 to 0.94 ms
higher than the control's for Aeneas (about 2%: 412 `gettimeofday` system calls),
0.05 to 0.14 ms for util, and within 7 µs for small, where many files take a
few microseconds and their per-file times, in whole microseconds, are coarse.
The per-file sums leave 0.06 ms of Aeneas's loop unattributed. Classification
adds 1.25 ms outside `parse_us`. With the control's whole-loop parse, 42.385 ms,
and the edited file's 0.117 ms, the Aeneas ceiling is 42.3 ms rather than 43.0.
Allocation grows by the classifier's arrays: 3,528 bytes per Aeneas analysis,
552 for util, and 64 for small, and the worker keeps that much more live
between analyses, 180,872 bytes against 177,344 for Aeneas. The fixed
classifier, which also records which files each parse reached, allocates 216,
40, and 16 bytes more than that, and keeps 328, 152, and 128 more live, 181,200
bytes for Aeneas; a one-block run of both probed builds under the lock, with
otherwise the same options, classified every sample identically. Peak RSS was
unchanged: the Aeneas worker's high-water mark was 196,012,032 bytes against
196,001,792 (p50 of 6 sessions), and the server's 210,608,128 in both.

Ordinary builds were compared separately. Their workers' RESULT frames,
compared byte for byte as for [#380](#worker-heap-room-2026-10-31) with the
elapsed-time fields zeroed, were identical for all 22 corpus requests, and so
were their live bytes; neither build contains a parse-work probe name. The same
`--parse-work` command without `--require-probes`, on ordinary builds of
`e705e44e` and `242c3407` in `build/c359n` and `build/p359n`, recorded no
`BENCH` line, and every scenario's p50 matched within 3.8 ms for Aeneas and
0.8 ms for util. The [tolerance release scorecard](#release-scorecard), captured
with the probed build, passed every floor: 180/185 primary and 44/94 held-out in
both modes, with zero wrong answers, zero expectation mismatches, and no
publication at a stale version.

**Conclusion.** Reusing unchanged files' parses could save at most about 43 ms
of a 520 ms Aeneas edit, and 2.4 ms of a 219 ms util edit, before the copying
it would need. Collection alone takes 2.5 times as long as the whole parse, and
result encoding, decoding, and source capture together take 2.4 times as long,
with no compiler change. A parse-reuse experiment isn't justified now: it would
need a compiler change to copy or reset trees, whose cost could consume much of
that ceiling, for at most 8% of an Aeneas edit. Those other phases are the
better targets.

### Source capture's read and hash (2026-11-01)

Measured on 2026-11-01 for [#413](https://github.com/SaltedTan/virgil-language-server/issues/413),
a follow-up to the [unchanged-file parse work](#unchanged-file-parse-work-2026-11-01)
above, which found source capture to be most of the server's `prepare_us`.
Capture rereads and rehashes every closed project file before each analysis.
The `capture_read_us` and `capture_hash_us` probes [above](#phases-allocation-and-output)
split it into reading each source and hashing the closed files' text. The
"before" build is main `f3cf0707` with only those probes added (`28a99d07`);
the "after" build is `30c11dff`, which also changes how a closed file is read.
Both were built in this physical checkout, in equal-length `build/b413b` and
`build/a413b` directories, with `BENCH=1 all bench-metadata` and byte-identical
generated BuildInfo text (revision `p413-f3cf07078000`). SHA-256 hashes: before
server `ff584a7190a95c211aa1614308b7cc94c4593ad7a4dcdd5bf708dd43a6a40741`, after
server `c2de4625c6e6922e0266ef836a7a97aac6c4bfb45dd5188cbcbb1541b8d03e9a`, and
the same worker in both,
`f076af16fca072907c5e773de36d1ff29d74be57f046eb4a86612f2840e183e5`. The run held
the shared benchmark lock and ran on hardware thread 12, with every semantic
check passing and no samples removed:

```sh
python3 scripts/bench-lsp.py --exe build/b413b/virgil-lsp --exe build/a413b/virgil-lsp --parse-work \
  --blocks 6 --operations 5 --warmups 2 --cpu 12 --require-probes \
  --output build/bench-results/source-capture.json
```

**Hashing.** Before the change, hashing was 2.388 ms of Aeneas's 12.755 ms
capture (p50), 19%, and 0.131 of util's 1.093 ms, 12%. That is less than a
third of capture, so the hash, a byte-at-a-time 64-bit FNV-1a, is unchanged.

**Reading.** Reading was 9.059 ms of the 12.755 ms. A closed file was read in
4,096-byte chunks, each appended to a `StringBuilder` one byte at a time, and
the result was then copied out of the builder. A scratch program that reads the
same 205 Aeneas files (3,064,615 bytes) as `ProjectFiles.load` does, pinned to
the same hardware thread, took 11.0 ms per pass over them that way. Of that,
1.6 ms opened each file one path component at a time and closed it again,
which the change keeps for its symlink policy. Opening, `fstat`, and reads into
an array of the length `fstat` reported took 1.9 ms. Copying, not the system
calls, dominated. `ProjectFiles.load` now reads into an array of `fstat`'s
length, so a nonempty file whose length holds normally costs one read, one
end-of-file read, and no copy; short or interrupted reads are retried. The same
program took 2.5 ms per pass through the new `load`. A file
that shrinks between `fstat` and the read yields the bytes that remain, and one
that grows is read to its end in chunks within the same budget, as before.

Each cell is **p50 / p95, in milliseconds**, over all 210 measured samples of
the seven scenarios for each project and build, with the percentile definition
[above](#phases-allocation-and-output):

| Project | Build | `capture_read_us` | `capture_hash_us` | `capture_us` | `prepare_us` |
| --- | --- | ---: | ---: | ---: | ---: |
| small | before | 0.015 / 0.044 | 0.001 / 0.005 | 0.028 / 0.067 | 0.053 / 0.089 |
| small | after | 0.014 / 0.045 | 0.001 / 0.005 | 0.028 / 0.067 | 0.051 / 0.090 |
| util | before | 0.737 / 0.817 | 0.131 / 0.144 | 1.093 / 1.199 | 1.212 / 1.403 |
| util | after | 0.331 / 0.390 | 0.132 / 0.147 | 0.693 / 0.798 | 0.813 / 0.952 |
| Aeneas | before | 9.059 / 14.864 | 2.388 / 3.799 | 12.755 / 20.385 | 13.309 / 21.091 |
| Aeneas | after | 2.272 / 20.477 | 2.412 / 2.724 | 6.080 / 24.386 | 6.612 / 24.866 |

Each Aeneas scenario's p50 agrees with the pooled one within 0.6 ms, except
the after build's `body_edit` at 8.019 ms, explained next. The higher Aeneas p95
after the change is the server's garbage collection, which now sometimes falls
within capture. Capture allocates each file's bytes once, rather than into a
builder that grows by doubling and is then copied out, so the server collects
less often, but the 3 MB it still allocates can use up its heap's room. In the
before build, the server collected after
submitting the request in 102 of the 210 Aeneas samples. In the after build it
did in 30, and in 30 others capture took about 20 ms instead of 6, 12 of
them among the 30 `body_edit` samples. A scratch
build that also counted collections during capture, run for one Aeneas block,
found exactly one in each such sample and none in any other. Mean Aeneas
capture fell from 13.524 to 8.779 ms, and mean `prepare_us` from 14.058 to
9.303 ms. Round trips, at about 330 and 530 ms, moved by no more than this
run's spread.

After the change, hashing is 2.412 ms of Aeneas's 6.080 ms capture, 40%.
Reusing a closed file's bytes and hash across analyses would avoid both, but
it isn't done: keyed on the disk stamp, it would analyze stale text for a
client that doesn't report file changes. Caching closed files across analyses
is out of scope for this change and remains a maintainer decision.

**Observer cost.** A separate run under the same lock alternated main
`f3cf0707` with the probed build. Their `capture_us` p50s agreed within 0.24 ms
for every Aeneas scenario, which is within the noise of either direction,
within 0.03 ms for util, and within 2 µs for small. Ordinary builds of
`f3cf0707` and `30c11dff`, in `build/b413n` and `build/a413n`, contain no
capture probe name and have byte-identical workers; the workers' RESULT frames,
compared as for [#380](#worker-heap-room-2026-10-31), were identical for all 22
corpus requests.

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

For a change to the worker, include `--project aeneas --error-result` in the
comparison and report the room each Aeneas scenario leaves under the collection
boundary: 201,326,592 bytes, half the default 384 MiB heap, minus the worker's
median `heap_after_handle_bytes`. A scenario that collects during handling has
none; a `WORKER_HEAP=1g` control build shows how far over it ends. An analysis
that ends a few hundred KB under the boundary passes every check until the next
small change makes it collect, as [worker heap room](#worker-heap-room-2026-10-31)
records.

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

### Cost of a reply (2026-11-01)

Measured on 2026-11-01 for [#324](https://github.com/SaltedTan/virgil-language-server/issues/324) on the same host, pinned Virgil, and worker heap, holding the shared benchmark lock, with the client, server, and worker on hardware thread 12 unless noted. Main `bc63ae7f` and the change were built in separate checkouts with the same generated BuildInfo. A client that follows the method above ran six process blocks that alternated the two builds, with the same token and hover rounds. Other work on the machine kept its one-minute load average between 2 and 4, so the times compare the two builds with each other, not with the table above.

**Allocation.** Copies of both builds with allocation probes at the same points of the request path gave the same bytes for every full reply on `Verifier.v3`:

| Phase | Main | Change |
| --- | ---: | ---: |
| Decode the request | 2,072 | 2,072 |
| Find the project, snapshot, and document | 28,496 | 28,496 |
| Position map of the document's current text | 549,424 | 549,424 |
| Walk the tokens and build the result | 983,120 | 101,784 |
| Encode the response | 265,336 | 86,984 |
| Frame the response | 87,032 | 87,032 |
| Total | 1,915,480 | 855,792 |

On main, the walk put the 40,820 integers in a vector of `JsonValue`s, which grew by doubling, and wrapped it in an object; encoding rendered that tree into a buffer that tripled as it filled, then copied the result out of it. The change writes the result's bytes as it walks, into a buffer with room for 12 bytes for each declaration site and use in the file, which never grew on `Verifier.v3`, and hands that buffer to the response without copying it. The response reserves exactly its own size, so encoding copies the result once, and framing copies it once more, as before. Across the 201 files of the small, `lib/util`, and Aeneas projects, a token took 10.0 to 11.0 bytes on average per file, and every reply was byte for byte the same in both builds.

In both builds measured here, the position map, which an earlier split of this allocation didn't place, held 4 bytes for each byte of the document's 106,419-byte current text, and line tables. The snapshot request path builds it for every request, hover included; the token walk uses only its text.

**Times.** Normal builds, from writing a request to reading the last byte of its reply, **median (p10–p90, maximum), milliseconds**:

| `textDocument/semanticTokens/full` | Main | Change |
| --- | ---: | ---: |
| First on a snapshot (30) | 3.099 (2.643–18.163, 18.633) | 2.295 (2.037–3.720, 4.916) |
| Later (150) | 2.619 (2.470–3.020, 3.201) | 2.102 (1.962–2.272, 3.570) |

Later hover requests, at `verify` on line 24, took 2.960 ms on main and 2.931 ms with the change, and each allocated 5,853,200 bytes in both builds; this measurement doesn't show why hover costs more than in the table above.

**The tail.** The instrumented copies also counted the server's collections during each request. Each of the 24 measured requests that took 10 ms or more had one collection and took 17.5 to 27.6 ms; no request without a collection took more than 9.0 ms. The collections fell at the same points in every process, since each process allocates the same sequence: on main, during the first request of the fourth token round and the sixth request of the second hover round, counting the warmup rounds; with the change, only during `virgil-lsp/analyze` requests, which aren't timed. The change doesn't remove the tail: a token request still meets a collection whenever the server's heap fills during it, only less often, as each reply allocates less than half as much. These runs didn't measure what a collection copies.

**The first request.** The first token request on a snapshot allocated the same bytes as the later ones, so no additional allocation was observed. In the instrumented copies, among requests without a collection, its median was 4.238 ms on main and 3.947 ms with the change, against 2.624 and 2.096 ms for later requests. By the mean of each phase, about four fifths of the extra time was in writing the reply to the client, which on one CPU also waits for the client to read it, and the rest in the walk. With the worker's executable wrapped to run on CPU 14, the first request took 2.720 and 2.203 ms, about 0.1 ms more than the later ones' 2.604 and 2.094 ms. The slower first request is therefore the worker sharing the server's CPU just after an analysis. That fits the collection the worker runs after sending a result ([prepareRename on one CPU](#preparerename-on-one-cpu-2026-10-31)), but these runs didn't time the worker.

#### Position maps (2026-11-01)

Measured for [#414](https://github.com/SaltedTan/virgil-language-server/issues/414) by the method of the section above: main `f3cf0707` and the change, built in separate checkouts with the same generated BuildInfo, ran six process blocks that alternated the two builds, with the same token and hover rounds on `Verifier.v3`. Main's position map kept the LSP character of every byte of the text, 4 bytes each. The change keeps one byte for each line, which says whether the line is all ASCII, and builds a line's table of characters only when an offset on a line with a non-ASCII byte is converted. `Verifier.v3` has no such line.

**Allocation.** Copies of both builds with allocation probes gave the same bytes for every request of a kind. A standalone program also built the map of the file 51 times and timed each construction:

| `Verifier.v3` | Main | Change |
| --- | ---: | ---: |
| One position map, bytes | 547,592 | 36,048 |
| One position map, median microseconds | 593 | 304 |
| Full token request, bytes | 853,720 | 342,176 |
| Hover at `verify` on line 24, bytes | 5,850,408 | 5,338,864 |

These probes read the allocation just before and after the map is built, and a request's bytes start after its decoding. The section above's map phase also held the step that finds the analyzed text, so it gave 549,424 bytes, and its total adds the 2,072 bytes of decoding. With the change, the map is its line tables, which it sizes exactly for the file's 2,758 lines from a first count of the line ends, instead of growing them by doubling. Growing them, it allocated 132,160 bytes in 348 microseconds.

**Times.** Normal builds, **median (p10–p90, maximum), milliseconds**:

| Request | Main | Change |
| --- | ---: | ---: |
| `textDocument/semanticTokens/full`, first on a snapshot (30) | 2.209 (2.030–3.094, 4.798) | 1.954 (1.799–2.651, 4.211) |
| `textDocument/semanticTokens/full`, later (150) | 2.050 (1.881–2.216, 2.361) | 1.824 (1.712–1.951, 2.035) |
| `textDocument/hover`, first on a snapshot (30) | 2.861 (2.760–3.020, 5.062) | 2.681 (2.635–2.761, 2.849) |
| `textDocument/hover`, later (150) | 2.808 (2.726–3.056, 5.430) | 2.673 (2.588–2.771, 2.903) |

In the instrumented copies, no token request met a server collection in either build. The first hover on a snapshot met one in four of the six rounds on main and in five with the change, counting the warmup round. The `virgil-lsp/analyze` requests, which allocate about 23 MB each, meet the other collections, and the change moves where the heap fills.

**Identity.** All 1,174 replies were byte for byte the same in both builds, matched by request id: the semantic tokens of all 201 files of the small, `lib/util`, and Aeneas projects, and 973 hovers at the start of lines 1, 2, 25, and the last line of each file, and at character 6 of line 25. Three of those files have non-ASCII characters, all in comments. Token positions don't use the position map, and none of the captured hovers is on one of those lines, so these replies don't exercise a non-ASCII line's table; a unit test does. It compares the change's conversion of every offset with the old whole-text table, for ASCII, mixed, CRLF, lone CR, astral-plane, invalid UTF-8, empty, and long non-ASCII texts, in all three encodings.

#### Hover (2026-11-01)

Measured for [#415](https://github.com/SaltedTan/virgil-language-server/issues/415) on main `265fc8d6`, after the position map change above, by the method of the sections above. A client opened the Aeneas project as an unsaved project file, `Verifier.v3`, and `vendor/virgil/aeneas/src/util/IO.v3`, a 1,810-byte file of the same project. In each process, it ran six rounds, the first a warmup, each a `virgil-lsp/analyze` request for `Verifier.v3` and then five hovers at each of eleven positions in turn, with the plaintext content format. Six process blocks alternated the normal build and a copy with allocation probes, built in separate checkouts with the same generated BuildInfo. In a shorter run of the probe copy with the Markdown content format, each hover allocated 144 to 1,736 bytes more, all in rendering, encoding, and framing its text.

**Positions.** Bytes are from the probe copy and vary by 8 with the length of the request id. Times are from the normal build, **median (p10–p90, maximum), milliseconds**, over 150 requests each:

| Hover at | Bytes | Time |
| --- | ---: | ---: |
| `Verifier.v3` line 24, `verify`, a method's declaration without a comment | 5,338,872 | 2.648 (2.565–2.734, 2.844) |
| `Verifier.v3` line 26, `vst`, a local | 68,952 | 0.428 (0.397–0.467, 0.540) |
| `Verifier.v3` line 46, `typeCache`, a field without a comment | 5,338,968 | 2.632 (2.541–2.721, 2.830) |
| `Verifier.v3` line 213, `markNeedsDescribes`, a method with a comment | 5,340,088 | 2.681 (2.569–16.824, 20.611) |
| `Verifier.v3` line 7, `Verifier`, a class's declaration with a comment | 5,341,344 | 2.634 (2.553–2.753, 2.885) |
| `Verifier.v3` line 147, `VstClass`, a class with a comment in the closed `Vst.v3` | 1,916,264 | 1.477 (1.423–1.544, 1.688) |
| `IO.v3` line 45, `data`, a local | 33,800 | 0.149 (0.124–0.175, 0.217) |
| `IO.v3` line 44, `writer`, a field without a comment | 86,848 | 0.160 (0.148–0.196, 0.216) |
| `IO.v3` line 38, `flush`, a method with a comment | 87,416 | 0.161 (0.146–0.190, 0.222) |
| `IO.v3` line 19, `Buffer`, a class's declaration with a comment | 88,864 | 0.160 (0.148–0.191, 0.205) |
| `IO.v3` line 22, `DataWriter`, a class with a comment in the closed `lib/util/DataWriter.v3` | 474,448 | 0.358 (0.336–0.390, 0.440) |

**Phases.** Medians of the probe copy, over 150 requests each. Each probe reads the allocation and the clock where the previous one did, so the phases add up to the request:

| Phase | `verify`, bytes | `verify`, microseconds | `flush`, bytes | `flush`, microseconds |
| --- | ---: | ---: | ---: | ---: |
| Request parameters, document, and project, before the snapshot lookup | 24,832 | 126 | 24,808 | 96 |
| Find the snapshot, document, and analyzed and current text | 3,664 | 37 | 3,600 | 29 |
| Position map | 36,048 | 296 | 976 | 6 |
| Find the occurrence or declaration | 56 | 4 | 128 | 2 |
| Signature and type text | 56 | 0 | 56 | 0 |
| Comment: copy of the text with normalized line ends | 106,432 | 109 | 1,824 | 2 |
| Comment: parse of the whole file | 5,132,224 | 1,660 | 49,184 | 19 |
| Comment: outline | 31,320 | 410 | 2,016 | 4 |
| Comment: find the declaration's comment | 24 | 2 | 280 | 1 |
| Markdown of the comment | 0 | 0 | 240 | 0 |
| The Hover's `JsonValue` tree | 1,440 | 1 | 1,440 | 0 |
| Encode the response | 2,432 | 3 | 2,472 | 2 |
| Frame and write the response | 344 | 61 | 392 | 18 |
| Total | 5,338,872 | 2,709 | 87,416 | 179 |

**The cause.** To find a declaration's leading comment ([Hover](architecture.md#hover)), the server parses the declaration's whole file again and builds its outline, for every hover whose target is of a kind that can be documented, which is every kind but locals, parameters, and type parameters, whether or not it has a comment, in any file of up to 256 KiB. On `Verifier.v3` that is 5,269,976 bytes, 98.7% of the request, and about 2.2 of its 2.7 ms. A copy of the server that also read the allocation around each step of the parser's token skip split the parse's 5,132,224 bytes for the file's 24,033 token spans: 1,231,128 bytes in the server's table of spans, an object for each and the vector that holds them; and 167,504 bytes in the index that maps the parser's line and column positions to offsets for its errors. The rest, 3,733,592 bytes, is the pinned parser's own: its syntax tree, tokens, and names. A hover on a local, which can't be documented, skips the parse and allocated 68,952 bytes. A declaration in a closed file is read from disk to check its bytes, 152,296 bytes for `Vst.v3`, and then that file is parsed. `completionItem/resolve` reads a comment the same way.

In every measured round of both copies, the round's 55 hovers met exactly one server collection, during the hover at `markNeedsDescribes`. In the normal build, those 30 of that hover's 150 measured requests took 16.1 to 20.6 ms, and no other measured hover took more than 2.93 ms.

**Options.** No code was changed, because each way to remove the parse needs a decision:
- The worker, which already parses every file, could record each documented declaration's comment span, or the offsets of its first token and of the end of the token before, in the analysis result. Hover and resolve would then parse nothing, and the request's other phases at `verify` add up to 68,896 bytes. This adds a field for each declaration to the wire format and the snapshot, as [#386](https://github.com/SaltedTan/virgil-language-server/issues/386) asks for containers, and it costs worker heap room.
- The server could keep each file's comment spans after the first lookup, keyed by its analyzed bytes, so that later hovers on unchanged text parse nothing. The first hover on each new text would still parse, and the cache would hold memory between requests.
- Without either, only the server's share of the parse could shrink: packing the span table into integer arrays would save part of its 1.2 MB, but the parser's 3.7 MB would stay. A textual check that skipped the parse when no comment line can precede the declaration would help only undocumented targets, such as `verify`, and would have to restate the outline's rule for where a declaration starts.

#### Hover comment spans (2026-11-01)

Measured for [#415](https://github.com/SaltedTan/virgil-language-server/issues/415) after the change that the first option above describes: the worker records each declaration's comment span in the analysis result, and hover and `completionItem/resolve` read the comment's lines at that span, with no parse ([ADR-0011](decisions/0011-declaration-comment-spans.md)). Main `b4308cfe` and the change were built in separate checkouts with the same generated BuildInfo. The client, positions, and rounds were the section above's, with the plaintext content format, and six process blocks alternated four builds: each build's normal executable and its copy with allocation probes. The client, server, and worker ran on hardware thread 12, under the shared benchmark lock. Main now reads a closed file into an array of its length ([#413](https://github.com/SaltedTan/virgil-language-server/issues/413)), so a hover whose target is in a closed file allocates less on main than the section above records.

**Positions.** Bytes are from the probe copies, and times from the normal builds, **median (p10–p90, maximum), milliseconds**, over 150 requests each:

| Hover at | Bytes, main | Bytes, change | Time, main | Time, change |
| --- | ---: | ---: | ---: | ---: |
| `Verifier.v3` line 24, `verify`, a method's declaration without a comment | 5,338,872 | 68,872 | 2.620 (2.556–2.787, 19.739) | 0.446 (0.404–3.041, 4.237) |
| `Verifier.v3` line 26, `vst`, a local | 68,952 | 68,952 | 0.436 (0.403–0.469, 0.504) | 0.426 (0.396–1.527, 4.182) |
| `Verifier.v3` line 46, `typeCache`, a field without a comment | 5,338,968 | 68,968 | 2.607 (2.531–2.721, 18.465) | 0.425 (0.393–0.547, 3.995) |
| `Verifier.v3` line 213, `markNeedsDescribes`, a method with a comment | 5,340,088 | 70,208 | 2.607 (2.533–2.758, 18.110) | 0.429 (0.398–0.502, 4.103) |
| `Verifier.v3` line 7, `Verifier`, a class's declaration with a comment | 5,341,344 | 71,544 | 2.600 (2.539–2.708, 18.479) | 0.426 (0.401–0.488, 3.980) |
| `Verifier.v3` line 147, `VstClass`, a class with a comment in the closed `Vst.v3` | 1,805,632 | 111,104 | 1.353 (1.315–1.417, 15.090) | 0.475 (0.440–0.552, 18.159) |
| `IO.v3` line 45, `data`, a local | 33,800 | 33,800 | 0.149 (0.124–0.176, 0.199) | 0.148 (0.125–0.166, 0.185) |
| `IO.v3` line 44, `writer`, a field without a comment | 86,848 | 33,800 | 0.176 (0.147–0.193, 0.205) | 0.143 (0.123–0.163, 0.175) |
| `IO.v3` line 38, `flush`, a method with a comment | 87,416 | 34,448 | 0.154 (0.146–0.188, 0.221) | 0.142 (0.123–0.163, 0.178) |
| `IO.v3` line 19, `Buffer`, a class's declaration with a comment | 88,864 | 35,992 | 0.159 (0.149–0.189, 0.220) | 0.139 (0.124–0.165, 0.176) |
| `IO.v3` line 22, `DataWriter`, a class with a comment in the closed `lib/util/DataWriter.v3` | 453,944 | 49,536 | 0.345 (0.317–0.375, 0.423) | 0.163 (0.144–0.186, 0.205) |

A hover at a declaration now allocates and takes about what a hover at a local does, whether or not its file is open. 43 of the change's 1,650 measured hovers took 3 to 5 ms, against 2 on main: 16 of 150 at `verify`, 13 at `vst`, and fewer at the next three positions, all among the first 25 hovers after a `virgil-lsp/analyze`. In the probe copy, those requests spent about 0.5 ms in the server and the rest outside it. With the worker on another CPU, as below, they disappear, so the wait is for the worker, which collects after it sends a result and shares hardware thread 12 with the server ([prepareRename on one CPU](#preparerename-on-one-cpu-2026-10-31)); main's hovers, five times longer, met it less often. In six more blocks of the normal builds, with each build's worker wrapped to run on CPU 14, the hover at `verify` took 2.756 ms (2.623–3.017, 20.714) on main and 0.462 ms (0.421–0.540, 0.671) with the change, and no other measured hover of the change took more than 0.7 ms, except one at `VstClass` in each round, which met a server collection.

In the probe copies, the server collected during 36 measured hovers on main, at the five positions whose targets are in `Verifier.v3` or `Vst.v3`, and during 6 with the change, all at `VstClass`. That excludes the first hover of each round, which also holds its `virgil-lsp/analyze`: 18 such hovers collected on main and 12 with the change.

**Phases.** Medians of the probe copies, over 150 requests each, as in the section above. On main, finding the declaration's comment also rendered it:

At `verify` on line 24 of `Verifier.v3`:

| Phase | Main, bytes | Change, bytes | Main, microseconds | Change, microseconds |
| --- | ---: | ---: | ---: | ---: |
| Request parameters, document, and project, before the snapshot lookup | 24,832 | 24,832 | 136 | 114 |
| Find the snapshot, document, and analyzed and current text | 3,664 | 3,664 | 36 | 31 |
| Position map | 36,048 | 36,048 | 276 | 278 |
| Find the occurrence or declaration | 56 | 56 | 4 | 3 |
| Signature and type text | 56 | 56 | 0 | 0 |
| Comment: copy of the text with normalized line ends | 106,432 | — | 105 | — |
| Comment: parse of the whole file | 5,132,224 | — | 1,620 | — |
| Comment: outline | 31,320 | — | 390 | — |
| Comment: find and render the declaration's comment | 24 | — | 1 | — |
| Comment: read and render the recorded lines | — | 0 | — | 0 |
| Markdown of the comment | 0 | 0 | 0 | 0 |
| The Hover's `JsonValue` tree | 1,440 | 1,440 | 1 | 1 |
| Encode the response | 2,432 | 2,432 | 3 | 2 |
| Frame and write the response | 344 | 344 | 66 | 20 |
| Total | 5,338,872 | 68,872 | 2,638 | 450 |

At `flush` on line 38 of `IO.v3`:

| Phase | Main, bytes | Change, bytes | Main, microseconds | Change, microseconds |
| --- | ---: | ---: | ---: | ---: |
| Request parameters, document, and project, before the snapshot lookup | 24,800 | 24,800 | 92 | 97 |
| Find the snapshot, document, and analyzed and current text | 3,600 | 3,600 | 29 | 29 |
| Position map | 976 | 976 | 5 | 5 |
| Find the occurrence or declaration | 128 | 128 | 2 | 2 |
| Signature and type text | 56 | 56 | 0 | 0 |
| Comment: copy of the text with normalized line ends | 1,824 | — | 2 | — |
| Comment: parse of the whole file | 49,184 | — | 18 | — |
| Comment: outline | 2,016 | — | 4 | — |
| Comment: find and render the declaration's comment | 280 | — | 1 | — |
| Comment: read and render the recorded lines | — | 336 | — | 1 |
| Markdown of the comment | 240 | 240 | 1 | 1 |
| The Hover's `JsonValue` tree | 1,440 | 1,440 | 0 | 1 |
| Encode the response | 2,472 | 2,472 | 2 | 2 |
| Frame and write the response | 400 | 400 | 19 | 17 |
| Total | 87,416 | 34,448 | 174 | 155 |

**The worker.** `scripts/bench-lsp.py`, with the same benchmark builds of both, six alternating process blocks, two warmups, and five measured operations per block, with every semantic check passing. For the Aeneas project, with `--error-result`, **median (p10–p90), milliseconds**, and bytes:

| Aeneas | `reused_analysis`, main | `reused_analysis`, change | `error_analysis`, main | `error_analysis`, change |
| --- | ---: | ---: | ---: | ---: |
| Latency | 336.911 (322.008–355.754) | 346.514 (334.740–366.100) | 342.323 (325.200–349.782) | 350.731 (337.270–357.863) |
| Worker parse | 44.625 (44.189–45.467) | 51.397 (50.511–53.006) | 45.669 (44.828–47.198) | 51.964 (51.536–52.878) |
| Worker result encoding | 60.989 (58.936–64.282) | 64.535 (61.371–66.826) | 63.356 (60.298–65.044) | 64.401 (63.512–68.403) |
| Worker collection after sending | 40.456 (38.941–44.096) | 47.857 (45.076–51.919) | 41.057 (39.685–44.001) | 46.806 (45.392–51.014) |
| Worker allocation | 187,301,664 | 188,210,976 | 187,990,104 | 188,916,784 |
| Worker heap after handling | 190,575,848 | 191,485,160 | 191,319,120 | 192,245,800 |
| Room under the 201,326,592-byte semispace | 10,750,744 | 9,841,432 | 10,007,472 | 9,080,792 |
| RESULT frame | 4,379,197 | 4,384,791 | 4,510,801 | 4,516,395 |

No scenario collected while handling a request in either build, and the live bytes after the worker's explicit collection stayed 181,200. A fresh analysis's parse took 78.525 ms instead of 69.257, and it had 9,875,592 bytes of room instead of 10,784,904; `rename` and `semantic_diagnostic` moved as `reused_analysis` did. A `completion_miss`, whose analysis with a scope query already built an index for every file, allocated 145,642,312 bytes instead of 163,181,240, because the index is now packed, so its room grew from 34,871,160 to 52,410,088 bytes; its parse time was within 1.2 ms of main's. Decoding an Aeneas result took the server within 1.3 ms of main's time, and 23.5 KB more for the documented declarations. For `lib/util`, over the same six blocks, a reused analysis took 15.245 ms (13.975–18.917) instead of 14.267 (13.156–14.731), its parse 2.954 ms instead of 2.420, its allocation 121,680 bytes more, and its RESULT 183,623 bytes instead of 182,483.

A probe that repeated each step over the Aeneas sources' 206 files on hardware thread 12 put the parse's extra time at about 2 ms for the index, 1.8 ms to scan every file for a carriage return, 2.0 ms to walk the outlines of 15,305 symbols, and 0.9 ms for the comment rule and the records. The 2,235 documented declarations' spans take 5,594 bytes of the RESULT, where their comments' 176,422 bytes of text would have taken more than thirty times as much.

The worker's collection after sending a result took 3.3 to 7.4 ms longer with the change, and 0.5 ms longer in `rename`. That collection clears the semispace that the analysis filled, about 190 MB, which the change grows by 0.5%. An ablation copy of the change that records no comments, run in three more alternating blocks with main and the change, matched main's parse within 0.4 ms and its allocation within 728 bytes in every full analysis, but its collection after sending took about as long as the change's: 45.8 ms against 47.3 and main's 38.9 for `reused_analysis`. These runs therefore don't attribute that collection's extra time to recording the comments.

**Identity.** All 122,040 replies were byte for byte the same in both builds, matched by request id. For each project, a client ran twice, once accepting only plain text and once preferring Markdown, for both hover and completion documentation. It hovered at the name of every outline symbol of every file of the small, `lib/util`, and Aeneas projects, 16,129 hovers per format, with both small files, about half of `lib/util`'s, and five of the Aeneas project's 206 open, so most targets were in closed files; 2,701 of the Markdown hovers showed documentation. In one to three files of each project, it asked for completions at member accesses and at a statement's first letter, and resolved every item with data, 3,150 items per format, of which 1,428 had documentation. A fourth project held the edge cases, where it hovered at every identifier: comments with CRLF line ends and with a bare CR, a file whose bare CR in a comment leaves code on a line of its own once line ends are normalized, a file over 256 KiB, an open file whose unsaved text changes its comments, and a file whose method body fallback substituted. A file whose code lines end in CRLF doesn't parse, so every hover in its project was `null` in both builds. A server with no project also gave the same outlines of all 216 files in both builds.

After the review fix that bounds declarations by their source files' lengths,
37,454 more hover and resolve replies were byte-identical to main in plaintext
and Markdown. That check is additional to the 122,040 replies above; the
measurements and tables in this section are unchanged.

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
reference and diagnostic items compare exactly, ignoring set ordering. One
exception follows the server's rule for [hover documentation](architecture.md#hover):
when a hover's target declaration is in a file that the measured state's
snapshot substituted, the expected hover is the oracle's without its
documentation, the text after the first empty line, because the server shows a
substituted file's signatures but none of its comments. The target's file is
the oracle's definition's, or the queried file where the oracle has no
definition, as at a declaration's own name. Everywhere else, and in editing
sessions too, the oracle's hover is expected exactly, so a comment missing from
a state that substituted no target file is still a wrong answer. `available` counts a nonempty
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
python3 scripts/bench-tolerance.py --smoke --exe build/virgil-lsp --output build/tolerance-smoke.json
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
the recorded answers with `bench-tolerance.py`'s own scoring and its rule for
the hovers of substituted targets, so it refuses any
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

## Worker heap room (2026-10-31)

Measured on 2026-10-31 for [#380](https://github.com/SaltedTan/virgil-language-server/issues/380) against main `fa53d2a7`, this change's base, on the Intel Core i7-14700KF reference host with pinned Virgil `57318d37` and the default 384 MiB worker heap. Both candidates were built in this physical checkout, in equal-length `build/bmain` and `build/bnext` directories, with `BENCH=1 all bench-metadata`: main's before the change, and the change's from its commit `bfedd574`, each with generated BuildInfo revision strings of the same length (`bisect-` and twelve hex digits). The build metadata's source hashes differ only in BuildInfo and the six changed sources. The comparison held the shared benchmark lock, ran on P-core hardware thread 12, and used six alternating process blocks, two warmups, and five measured operations per block for the Aeneas project, with every semantic check passing and no samples removed:

```sh
python3 scripts/bench-lsp.py \
  --exe build/bmain/virgil-lsp --exe build/bnext/virgil-lsp \
  --project aeneas --error-result --require-probes --blocks 6 --cpu 12 \
  --output build/bench-results/heap-room.json
```

On main, the 900-function `error_analysis` collected once during every handled request, in result encoding, which took 207.0 ms instead of 54.3 ms. Each timing is **median (p10–p90), in milliseconds**, over 30 measured operations:

| Scenario | Main | Change | Worker collections |
| --- | ---: | ---: | ---: |
| `fresh_analysis` | 365.417 (346.031–369.846) | 359.910 (354.549–369.367) | 0 → 0 |
| `reused_analysis` | 321.486 (298.169–344.910) | 319.603 (298.774–342.674) | 0 → 0 |
| `error_analysis` | 482.804 (465.797–557.823) | 316.248 (301.428–333.048) | 1 → 0 |
| `rename` | 358.130 (341.975–367.275) | 357.809 (337.061–364.313) | 0 → 0 |
| `semantic_diagnostic` | 519.027 (501.975–528.291) | 511.277 (500.726–522.817) | 0 → 0 |
| `completion_miss` | 126.297 (118.233–139.352) | 124.234 (115.234–142.302) | 0 → 0 |
| `prepareRename` | 32.960 (17.571–49.785) | 32.930 (17.826–55.249) | — |
| `completion_hit` | 6.139 (5.549–44.802) | 5.783 (3.601–45.013) | — |

`error_analysis` block medians were 477, 557, 481, 489, 483, and 480 ms on main and 316, 305, 316, 338, 320, and 318 ms with the change, which puts its latency alongside `reused_analysis`, despite the error and 900 extra functions. Parse, verify, and collection medians moved by at most 4.4 ms, in both directions. Navigation, hover, references, and syntax diagnostics are unchanged.

Worker memory at the end of handling is the median, in bytes, which varied by at most 22 KB across samples. Room is what the 201,326,592-byte semispace has left, before the explicit collection that follows. Main's `error_analysis` collected, so its heap after handling, 130,755,152 bytes, is after that collection; a 1 GiB control build of main, run once with the same command and `--blocks 1`, put its end without one at 201,717,344 bytes, 390,752 over the boundary.

| Scenario | Allocated, main | Allocated, change | Heap after, main | Heap after, change | Room, main | Room, change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 197,749,952 | 188,502,184 | 200,843,232 | 191,595,464 | 483,360 | 9,731,128 |
| `reused_analysis` | 197,603,320 | 188,355,552 | 200,873,648 | 191,625,880 | 452,944 | 9,700,712 |
| `error_analysis` | 198,392,184 | 189,033,160 | 201,717,344 (1 GiB) | 192,358,320 | −390,752 | 8,968,272 |
| `rename` | 197,601,496 | 188,353,728 | 200,871,824 | 191,624,056 | 454,768 | 9,702,536 |
| `semantic_diagnostic` | 194,715,456 | 185,467,680 | 197,985,824 | 188,738,048 | 3,340,768 | 12,588,544 |
| `completion_miss` | 163,206,112 | 163,207,984 | 166,476,448 | 166,478,320 | 34,850,144 | 34,848,272 |

Live bytes after the explicit collection are unchanged, 177,344, and so are the RESULT frames' lengths: 4,379,196 bytes for a successful analysis and 4,510,800 for `error_analysis`. A completion miss collects no occurrences and encodes no files, so it doesn't change.

Most of the saving, 8.3 MB, is in occurrence collection. Each of the 206 files' collectors grew its own vectors of occurrences, builtin uses, and declaration sites, and its own call-record writer, by doubling; then `extract` copied each vector to its length, and the merge sort allocated one more array for it. Over 194,000 occurrences, the vectors alone ended at 287,000 slots. A program's collectors now use one set of buffers in turn (`AnalysisCollectorBuffers`), which grows only to the largest file's, and `AnalysisTables.sortPrefix` sorts a buffer's first elements into an array of exactly their number, merging through the buffer itself, so the sorted array is the only copy. Calls that the walk found in order are copied at their exact length rather than kept in the writer's larger array. Result encoding saves the other 1.07 MB: its per-file typed uses, builtin uses, type-target overrides, hint records, and calls were each written to a new `DataWriter`, which grows to twice its size plus what it needs, so they allocated 1.57 MB to send 568 KB. They now share one `AnalysisRecordWriter`, which hands each record out as an exact copy. With an instrumented build of main, the five encoders grew 90,925, 201,329, 87,824, 214,844, and 976,759 bytes of buffer for 30,612, 78,308, 32,897, 78,792, and 346,907 bytes sent, on a successful analysis. Encoding still allocates about 3.5 MB for its declaration and string tables, and the 4.4 MB frame itself.

A same-run comparison of all three projects with three blocks found no regression. Small and util collected zero times in both builds; their worker allocation fell from 54,080 to 53,320 bytes and from 10,302,520 to 9,957,408 bytes for a fresh analysis, and their result lengths and live bytes were unchanged. Their latencies matched within their p10–p90 intervals, except util's `completion_hit`, 0.786 (0.700–1.546) against 1.082 (0.697–1.436) ms, a cached reply that both builds gave in about 0.7 or 1.3 ms by turns, which the change doesn't run.

The worker's RESULT frames were compared byte for byte, with the three elapsed-time fields zeroed and the live-heap field compared apart. A script drove each build's worker directly with the same requests: for each corpus project, its analysis twice in one worker, then with the corpus's semantic error, with that error and the 900 functions (Aeneas only), with the syntax error, and a scope, a member, and a call query at the completion position. All 22 frames, and their live bytes, were identical.

## Encoder table room (2026-10-31)

Measured on 2026-10-31 for [#389](https://github.com/SaltedTan/virgil-language-server/issues/389) against main `20af330d`, on the same host, pinned Virgil, and default 384 MiB worker heap as [worker heap room](#worker-heap-room-2026-10-31). Both candidates were built in this physical checkout, in equal-length `build/bmain` and `build/bfix3` directories, with `BENCH=1 all bench-metadata`: main's before the change, and the change's from its commit `6386782a`, each with generated BuildInfo revision strings of the same length (`t389-` and twelve hex digits). The comparison held the shared benchmark lock, ran on P-core hardware thread 12, and used all three projects, six alternating process blocks, two warmups, and five measured operations per block, with every semantic check passing and no samples removed:

```sh
python3 scripts/bench-lsp.py \
  --exe build/bmain/virgil-lsp --exe build/bfix3/virgil-lsp \
  --error-result --require-probes --blocks 6 --operations 5 --warmups 2 --cpu 12 \
  --output build/bench-results/encoder-tables.json
```

Result encoding interned strings in a table that started at 64 slots and grew by doubling, both its list and its slots: for the Aeneas sources, 1.70 MB of growth for a table of 35,261 strings whose final arrays take about 0.85 MB. It now reserves one string for each of the 43,071 declarations before it fills the table, and sizes the call signature text table from a count of the strings it needs. Each declaration also kept its path's ID, 172 KB in all, which both passes now look up only when the path changes, 234 times. Worker memory at the end of handling is the median, in bytes; room is what the 201,326,592-byte semispace has left. No scenario collected in either build:

| Scenario | Allocated, main | Allocated, change | Room, main | Room, change |
| --- | ---: | ---: | ---: | ---: |
| `fresh_analysis` | 188,502,184 | 187,444,544 | 9,731,128 | 10,788,768 |
| `reused_analysis` | 188,355,552 | 187,297,912 | 9,700,712 | 10,758,352 |
| `error_analysis` | 189,033,160 | 187,986,328 | 8,968,272 | 10,015,104 |
| `rename` | 188,353,728 | 187,296,088 | 9,702,536 | 10,760,176 |
| `semantic_diagnostic` | 185,467,680 | 184,410,048 | 12,588,544 | 13,646,176 |
| `completion_miss` | 163,207,984 | 163,177,480 | 34,848,272 | 34,878,776 |

Latencies matched within their p10–p90 intervals for every scenario of every project. Each timing is **median (p10–p90), in milliseconds**, over 30 measured operations:

| Scenario | Main | Change |
| --- | ---: | ---: |
| `fresh_analysis` | 358.547 (343.167–364.256) | 356.356 (341.933–361.303) |
| `reused_analysis` | 305.364 (294.846–316.288) | 304.078 (293.934–314.363) |
| `error_analysis` | 307.752 (297.911–318.064) | 308.385 (295.008–313.239) |
| `rename` | 350.962 (338.094–357.252) | 349.793 (334.007–354.844) |
| `semantic_diagnostic` | 516.571 (509.202–523.338) | 509.803 (493.829–517.779) |
| `completion_miss` | 123.394 (119.122–136.803) | 124.575 (119.525–138.250) |

Util's fresh analysis allocates 66,928 bytes less, 9,890,480 bytes, and small's 88 bytes less. The worker's RESULT frames, compared byte for byte as for #380 with the elapsed-time fields zeroed, were identical for all 22 corpus requests, and so were their live bytes.

## prepareRename on one CPU (2026-10-31)

Measured on 2026-10-31 for [#390](https://github.com/SaltedTan/virgil-language-server/issues/390),
after the [worker heap room](#worker-heap-room-2026-10-31) comparison recorded
Aeneas `prepareRename` at 32.960 ms (17.571–49.785). The request is cached: it
starts no worker transaction, and its work is the server's eligibility check. On
the Aeneas corpus that check reads and hashes every source, 10.9 ms; expands
the project and compares each source's stamp, 5.8 ms; scans declarations,
members, and other programs, 0.8 ms; and does the rest in 0.1 ms, a floor of
17.6 ms.

Two steps in that column are explained here: 17.9 to 25.3 ms at
[#290](https://github.com/SaltedTan/virgil-language-server/pull/290)
(`06672f2b`), and 26.5 to 33.3 ms at the Virgil pin bump
[#315](https://github.com/SaltedTan/virgil-language-server/pull/315)
(`54800cef`). Both are the one-CPU effect that
[Scheduled comparison](#scheduled-comparison) records for `completion_hit`,
landing on a longer request.

Each step was reproduced with six alternating process blocks of thirty measured
operations for the Aeneas project on hardware thread 12, under the shared
benchmark lock, with `--error-result --require-probes --cpu 12` and every
semantic check passing. Each pair is the commit against its parent, built in its
own checkout at its own pin's corpus, with equal-length generated BuildInfo
(`bisect-` and twelve hex digits). Timings are median (p10–p90), milliseconds:

| Step | Parent | Commit |
| --- | ---: | ---: |
| #290, old-pin corpus | 17.649 (17.572–18.097) | 25.994 (17.655–30.574) |
| #315, new-pin corpus | 25.821 (17.890–42.018) | 33.164 (19.210–46.604) |
| #315, old-pin corpus | 25.542 (17.763–26.924) | 32.681 (19.396–36.183) |

`completion_hit` in the #290 runs moved from 2.712 to 5.907 ms, the step that
section already explains. Then the same pairs with the worker's executable
wrapped to run on CPU 14, leaving the server on CPU 12 as editors run them on
separate cores, and with no repository change:

| Step | Parent | Commit |
| --- | ---: | ---: |
| #290 | 17.827 (17.697–18.378) | 17.935 (17.732–18.133) |
| #315 | 18.324 | 18.200 |

Every block's median stayed between 17.7 and 18.0 ms in the #290 pair and
between 18.1 and 18.6 in the #315 pair. One sample in each build of every #315
block took about 35 ms, in both builds, so the comparison is unaffected.
Neither step changes the request's own work.

Instrumented scratch builds, not committed, timed the server's eligibility call
and the worker's collection window per sample. In the shared #290 run, the
request began 9 to 18 ms into the worker's 34 ms collection of the previous
`completion_miss` result, with 10 to 20 ms of it left. The eligibility call then
measured 23.4 ms against 17.6 ms uncontended, its extra time inside the project
expansion and member scan, while samples that began after the collection had
ended measured 17.5 ms. Those are the two modes. With the worker on CPU 14 the
same collection windows overlapped the same request windows in wall time, but
the call stayed at 17.7 ms because the processes no longer share a core.

#290 added that collection. Before it, the worker collected before sending a
result, so nothing was pending when the cached requests that follow ran; since
then it collects after sending, which takes 40.5 ms (33.9–52.0) for an Aeneas
result. #315 makes the collection 13 ms longer, 38.6 to 51.0 ms on the new
corpus and 38.8 to 50.9 on the old one, with the new pin and its corpus. More of
it remains when `prepareRename` starts, deepening the slow mode by the measured
7 ms without moving the floor. The collection stays for the reasons
[ADR-0004](decisions/0004-analysis-worker-process.md) and
[#294](https://github.com/SaltedTan/virgil-language-server/issues/294) record,
and it costs cached requests nothing on separate cores.

Read the column as two modes, not one median. The Aeneas floor is about 17.6 ms,
and a run pinned to one CPU can put the median anywhere between that and about
twice it, depending on where the worker's collections fall; p10–p90 spans both.
The scheduled Linux job pins server and worker to one CPU, so this column alone
is not a latency regression signal there, and with server and worker on
separate cores it is the floor.

## Cursor tolerance histories (2026-10-31)

The [tolerance benchmark](#semantics-during-syntax-errors) queries intact files,
so its 180/185 primary availability says nothing about completion and signature
help where the user types. [`scripts/bench-cursor-tolerance.py`](../scripts/bench-cursor-tolerance.py)
measures them through 17 fixed typing histories, for
[#342](https://github.com/SaltedTan/virgil-language-server/issues/342). It reuses
`Session`, the tolerance driver's process and scoring helpers, and its
corpus hash checks. It is opt-in: neither it nor its helper tests are part of
`make test` or any workflow. Build, then run it under the shared benchmark lock:

```sh
make bench-build
mkdir -p build/bench-results/cursor-work
flock ~/.t3/scratch-virgil/bench.lock python3 scripts/bench-cursor-tolerance.py \
  --exe build/bench/virgil-lsp --cpu 12 --work-dir build/bench-results/cursor-work \
  --output build/bench-results/cursor.json
python3 scripts/bench-cursor-tolerance.py --smoke --exe build/bench/virgil-lsp --output build/cursor-smoke.json
python3 test/benchmarks/test_cursor_tolerance.py
```

`--smoke` replays only the first two seed histories, in one block, and needs no
build manifest; the example also creates `build/cursor-smoke.json.transcripts.jsonl`.
The full run replays every history in six blocks, alternating
the order of the modes from block to block. With a file output, the raw protocol
transcripts and each process's stderr go to `<output>.transcripts.jsonl`, and a
failed run keeps its partial results in `<output>.failure.json`.

### Inventory

The histories were frozen in
[`cursor-tolerance-histories.json`](../test/benchmarks/cursor-tolerance-histories.json)
before any damaged state was measured. It records each small or seed file's
exact text, the hash of every Aeneas source a history reads, every edit
operation, the probe and observer anchors with their resolved positions, each
step's syntactic role and which files the CLI parser must accept, and the
oracle facts. The driver regenerates the inventory from its definitions and
refuses to run if the two differ. Each anchor must occur exactly once in its
text, so a missing or repeated site is refused rather than replaced by another.
`python3 scripts/bench-cursor-tolerance.py --write-inventory` regenerates it
after a reviewed change.

- **Six seeds** from the [repair corpus](decisions/0006-incomplete-code-repair.md#cases):
  `member-with-prefix`, `call-open-arguments`, `call-trailing-comma`,
  `member-in-field-initializer`, `member-in-component-field`, and
  `syntax-error-in-other-file`. Their fixtures must match the hashes in
  [`tolerance-seeds.json`](../test/benchmarks/tolerance-seeds.json). Each has
  four steps: the intended program, the fixture's damaged text, the intended
  program again, and the same query again. The intended program uses the seed's
  insertion where that is a real program, and otherwise the member `x`: the
  `virgilLspCursor` insertions of three seeds name no member.
- **Nine small histories** on one three-file fixture: `Util.add(left: int,
  right: int) -> int`; `A`, with typed and inferred `Util.add(1, 2)` fields, an
  `edit(n: int) -> int` holding `Util.add(n, 2)`, a string local, a block
  comment, and an inner `if`, an intact `sibling()` that calls `Util.add`, and a
  `third()` with `var x = 0;`; and `Consumer.use()`, which calls `A.sibling()`.
  The file is indented with tabs, within the brace rules' premise. Each history
  has five steps: the valid baseline, one token removed, one more typed
  character or damage, the restored baseline, and the restored query again.
  `parameter-type` and `return-type` remove `int` from `edit`'s header, then
  type `i`. `typed-initializer` removes the `2`, leaving `Util.add(1, )` paired,
  then its `)`. `inferred-initializer` removes the `)`, then types `3` before
  the `2`. `open-string`, `open-block-comment`, `missing-inner-closer`, and
  `missing-method-closer` remove the string's closing quote, the comment's
  `*/`, the inner `if`'s `}`, and `edit`'s own `}`, after the query's call, then
  type one character at the cursor. `third-damaged-body` renames `Util.add`
  to `sum` and its parameter `right` to `amount`, with every call site updated,
  then removes the `)` of the calls in `edit` and `sibling`, then types `Util.`
  in `third`, and finally restores both calls and completes `third`'s
  `Util.sum`.
- **Two real histories** in the pinned compiler's 206 sources: `var vst =
  prog.vst;` in `aeneas/src/vst/Verifier.v3` and `def pos = mw.pos;` in
  `aeneas/src/arm64/Arm64AddrPatcher.v3`. Each queries after `prog.` or `mw.`
  in the upstream file, with the member removed, with its first character
  typed, and in the restored file, then repeats that query. The upstream file is
  the intended program of every step. These are member completions only, not
  signature help. Only the edited file is open.

Signature help is asked inside a real call's argument list, including within a
number; completion at a `.` or a typed prefix. `third-damaged-body` asks for
both: completion in `third` and signature help at `edit`'s call.

Every step also asks for definitions: in the edited body or declaration, in an
intact sibling, in another file, at the probed call's callee, and, where its use
is typed, at the required completion candidate. A probe that has no such site
is recorded as inapplicable: the seeds are one file, or another file with
nothing to navigate, and `int` has no source declaration.

### Oracles and verdicts

Oracles are reviewed facts about the intended program, not captured replies. A
completion must offer every required candidate with its reviewed kind and
detail, such as `add` with `def add(left: int, right: int) -> int`, or `int`
as a builtin type. A signature must have the reviewed label, parameters, and
active parameter. A definition must point at the reviewed declaration's name.
Whole completion lists are not compared, and a nonempty reply is not correct by
itself:

- `exact`: every fact holds.
- `partial`: some required candidates or leading parameters, and nothing wrong.
- `keywords-only`: no required candidate, and every item is a keyword.
- `missing`: no reply, an empty one, or names in scope without the required
  candidates.
- `wrong`: a candidate or signature that only an older program has, a required
  candidate with another kind or type, another signature or active parameter,
  another definition, or, after a `.`, members without any of the receiver's
  required ones.

Before it measures anything, the driver checks the CLI parser's verdict on
every text, replays each history's intended texts in an oracle process, and
requires every fact there to be exact. The stale-answer control replays
`third-damaged-body` with `add` and `right` kept. Its answers, scored against
the renamed history's facts, must be wrong at all eight probes that the rename
affects: signature help at every step, and completion once `Util.` is typed.

### Modes and barriers

Each history runs in three modes, each in a fresh server process. Warm opens
the initial texts and replays every step. Cold starts with the first damaged
step's texts already open, so its baseline is recorded as not run. Saved replays
as warm, but writes each change to disk and sends `didSave`. It then closes a
saved file that fails to parse if no probe reads it: only the damaged
`other.v3` of `syntax-error-in-other-file`, which then changes on disk with a
watched-file event. A probe whose overlay is closed would be recorded as
unsupported; the inventory keeps every probe's file open. Every step waits only
on replies: the probes' replies, the definitions, which wait for the current
project analysis, and then one `virgil-lsp/snapshot` inspection. That
inspection must show a current configuration and current documents at the
versions sent, and records the substitution and excluded bodies of each file.
There are no sleeps or polling loops. Latency is from a request to its reply,
and a step's from its first change to the inspection.

### Results

Measured on 2026-10-31 with the driver at `901c35d9` and a probe-enabled
`build/bench` server built from `3a3ab37d`, pinned Virgil `57318d37`, the
default 384 MiB worker heap, Python 3.14.4, and hardware thread 12 of the Intel
Core i7-14700KF reference host, under the shared benchmark lock. The run took
69.71 seconds for 306 history/mode/block records. It had no wrong answers and
no worker replacements, and every probe's and definition's outcome was the same
in all six blocks. An earlier run at `dfe60b81`, whose only difference was
uncommitted step-status bookkeeping, had the same verdicts for every record.

Per block and mode, cold mode has no baseline step:

| Probe answers | Warm and saved | Cold |
| --- | ---: | ---: |
| Completion, valid steps, exact | 33/33 | 22/22 |
| Completion, damaged steps | 11 exact, 6 missing, 1 keywords-only, of 18 | same |
| Signature help, valid steps, exact | 21/21 | 14/14 |
| Signature help, damaged steps | 5 exact, 7 missing, of 12 | same |

The three modes agree at every damaged step. Definitions at damaged steps differ
by mode only at `syntax-error-in-other-file`, where saved mode closes
`other.v3`:

| Definitions at damaged steps, per block | Exact | Missing | Inapplicable |
| --- | ---: | ---: | ---: |
| Edited body or declaration | 7 | 21 | 0 |
| Intact sibling, warm and cold | 12 | 16 | 0 |
| Intact sibling, saved | 11 | 17 | 0 |
| Other file | 8 | 14 | 6 |
| Probed call's callee | 2 | 10 | 0 |
| Required candidate | 0 | 2 | 16 |

Damaged steps by history, the same in every mode except where noted.
Navigation counts the definitions that were exact out of those asked:

| History and step | Probe | Navigation | Substitution |
| --- | --- | ---: | --- |
| `member-with-prefix` damaged, parses | exact | 2/2 | none |
| `call-open-arguments` damaged | exact | 1/3 | warm and saved fallback, cold repair |
| `call-trailing-comma` damaged, parses | exact | 3/3 | none |
| `member-in-field-initializer` damaged | exact | 0/2 | none |
| `member-in-component-field` damaged | exact | 0/2 | none |
| `syntax-error-in-other-file` damaged | missing | 1/2, saved 0/2 | both files, saved `main.v3` only |
| `parameter-type`, `return-type` removed | exact | 0/3 | none |
| `parameter-type`, `return-type` extra, parses | exact | 3/3 | none |
| `typed-initializer` removed, parses | exact | 4/4 | none |
| `typed-initializer` extra | exact | 0/4 | none |
| `inferred-initializer` removed | exact | 0/4 | none |
| `inferred-initializer` extra | missing | 0/4 | none |
| `open-string`, `missing-inner-closer` removed and extra | missing | 0/4 | none |
| `open-block-comment`, `missing-method-closer` removed | missing | 0/4 | none |
| `open-block-comment`, `missing-method-closer` extra | missing | 0/3 | none |
| `third-damaged-body` removed | completion keywords-only, signature missing | 2/4 | repair of two bodies |
| `third-damaged-body` extra | both missing | 0/4 | none |
| Both Aeneas histories, dot | exact | 2/3 | warm and saved fallback, cold repair |
| Both Aeneas histories, prefix, parses | exact | 3/3 | none |

Every restored and repeated step was exact, in every mode, for every probe and
definition. So were the baselines.

Completion and signature help at the cursor held wherever the cursor repair
alone made the file parse: in a body, and in a field initializer or a
declaration header, where the project analysis substitutes nothing, so that
every definition in the project was missing there. They had nothing wherever another
part of the project still failed to parse after the cursor repair: an open
string, block comment, or brace in the same file, two other damaged bodies in
the same file, or a body error in another file. That held even where the
project analysis repaired those bodies and kept the other definitions, as at
`third-damaged-body`'s removed step, where it emptied both bodies.
[#346](https://github.com/SaltedTan/virgil-language-server/issues/346) proposes
that change; these rows are its baseline. `inferred-initializer`'s extra step is
the other gap: signature help for an unclosed call had nothing when the cursor
was in or right after a number, as in `Util.add(1, 3|2;`. The repair appends its
identifier to the number, which doesn't parse. The same call is answered with
the cursor after `, ` or an identifier, or once the call is closed.

Probe latency on the small fixture was 0.1 to 2.5 ms at p50 and at most 8.3 ms
at p95. Aeneas completion, p50 (p95) in milliseconds over the six blocks:

| History, step | Warm | Cold | Saved |
| --- | ---: | ---: | ---: |
| Verifier, baseline | 525.1 (532.6) | not run | 511.9 (533.9) |
| Verifier, dot | 149.6 (152.7) | 516.1 (524.2) | 469.9 (477.1) |
| Verifier, prefix | 143.3 (147.3) | 144.9 (148.5) | 431.2 (439.8) |
| Verifier, restored | 127.1 (131.9) | 156.6 (158.5) | 473.8 (487.0) |
| Verifier, repeat | 1.0 (1.0) | 1.0 (1.0) | 1.0 (3.9) |
| Arm64, baseline | 509.8 (531.3) | not run | 510.1 (518.3) |
| Arm64, dot | 142.0 (146.7) | 508.6 (513.3) | 479.9 (481.9) |
| Arm64, prefix | 128.2 (130.9) | 139.8 (145.3) | 481.8 (483.4) |
| Arm64, restored | 119.4 (120.3) | 126.5 (128.9) | 450.1 (456.4) |
| Arm64, repeat | 0.8 (4.1) | 0.7 (0.7) | 0.7 (0.7) |

A completion's analysis waits for one that already runs. The first query in a
process waits for the first project analysis. In saved mode, each `didSave`
starts a project analysis that the completion's then waits behind. The repeat
reuses the last reply. A step, through its definitions and inspection, took
437 to 534 ms at p50 on Aeneas, apart from the repeats.

The same session ran the full [tolerance capture](#release-scorecard) at
`dfe60b81` with the same build. It took 558.01 seconds, and the scorecard passed
with the current floors in both modes: primary 180/185, held-out 44/94, exact
definitions and hovers 616/627, exact references 589/627, exact diagnostic sets
180/185, zero wrong answers, zero expectation mismatches, and all 56 injections
rejected.

These counts describe this fixed inventory. They are not rates of how often
users meet these states, and this report sets no quality threshold.

Rerun on 2026-11-01 for [#398](https://github.com/SaltedTan/virgil-language-server/issues/398),
which repairs an unclosed call at a number literal without the sentinel, on the
same host, pinned Virgil `57318d37`, and hardware thread 12, under the shared
benchmark lock with six blocks:

```sh
flock ~/.t3/scratch-virgil/bench.lock python3 scripts/bench-cursor-tolerance.py \
  --exe build/bench/virgil-lsp --cpu 12 --work-dir build/bench-results/cursor-work \
  --output build/bench-results/cursor.json
```

The run took 69.34 seconds for 306 history/mode/block records, with no wrong
answers and no worker replacements. Per block and mode, signature help at
damaged steps went from 5 exact and 7 missing of 12 to 6 exact and 6 missing:
`inferred-initializer`'s extra step, where the cursor is in or right after the
`3` of `Util.add(1, 3|2)`, is now exact, and no other history changed. Completion
at damaged steps is unchanged at 11 exact, 6 missing, and 1 keywords-only of 18.
Every restored and repeated step, and every baseline, stayed exact. The same
session's full [tolerance capture](#release-scorecard) passed the current floors
in both modes: primary 180/185, held-out 44/94, zero wrong answers, and zero
expectation mismatches.

## Completion beside a broken file (2026-11-01)

Measured on 2026-11-01 for [#346](https://github.com/SaltedTan/virgil-language-server/issues/346),
whose completions and signature help now substitute the project's other open
files that fail to parse ([ADR-0013](decisions/0013-recover-other-files-in-query-analyses.md)).
A throwaway client used `completion_miss`'s method: a completion of names at the
two alternating positions inside `explicitName` in the Aeneas corpus's
`core/Program.v3`, timed from its request to its decoded reply, and checked for
that name and its detail. `vst/Verifier.v3`, 106,419 bytes, was open in one of
three states:

- **Intact**, as on disk.
- **Warm**: `buildFile(f);` in `Verifier.verify` became `buildFile(f;` after an
  analysis of the intact text had cached it, so method-body fallback substitutes
  that text.
- **Cold**: the same error, opened before any analysis read the file, so
  current-text repair empties `verify`'s body.

The base build is `f385487a` with this change's two source files ablated. Both
builds came from one checkout, in build directories of the same length, with
identical generated BuildInfo and `BENCH=1`. Each run had six alternating
process blocks of two warmups and ten measured completions in each state,
pinned to hardware thread 12 under the shared benchmark lock. Times are median
(p10–p90), milliseconds:

| Run, state | Base | With the change | Substitution, with the change |
| --- | ---: | ---: | ---: |
| 1, intact | 133.0 (127.2–146.9) | 134.3 (128.9–146.7) | 0.5 |
| 1, warm | no names | 133.8 (123.3–150.7) | 5.2 |
| 1, cold | no names | 137.2 (117.1–175.0) | 11.6 |
| 2, intact | 135.6 (128.7–146.2) | 135.1 (127.7–146.6) | 0.5 |
| 2, warm | no names | 137.4 (124.0–154.7) | 5.2 |
| 2, cold | no names | 139.5 (116.6–178.5) | 11.7 |

Every completion with the change got the name, 360 of 360. With the error,
every base completion got the keywords alone, in 77 to 81 ms, since its
analysis stopped at the parse. The substitution column is the server's
`substitution_us` probe in the completion's capture: with the file intact, a
lookup of each open file's last parse; warm, one parse of the cached text;
cold, the scan and one parse of the blanked text. Per-block medians with the
file intact were 129.4 to 140.3 ms across both builds, and the warm and cold
medians differ from them by less than that spread. Cold's p10–p90 was wider
in both runs. Its slowest samples took about 65 ms longer than its fastest,
with no server collection, and only 4 to 15 ms of that was in the worker round
trip and 6 to 10 ms in the substitution; this benchmark doesn't break the rest
down.

## Memory soak

[`scripts/bench-memory-soak.py`](../scripts/bench-memory-soak.py) qualifies
long-session memory with the default worker limits (100 analyses, 2^28 UIDs,
and a live heap of an eighth of the worker's heap) and the shared budgets for
retained snapshots (32 MiB) and parseable text (4 MiB), for
[#343](https://github.com/SaltedTan/virgil-language-server/issues/343). It
reuses `Session`, the per-PID `ResourceSampler`, and the tolerance driver's
physical-copy fixture. It is opt-in: neither it nor its helper tests are part of
`make test` or any workflow. It needs a `BENCH=1` build, because the snapshot
and text-cache charges come from probes, and it has been run only on Linux,
where `/proc` supplies high-water marks, start times, and process groups. The
server runs in its own process group, which its workers join. At the end, or
after a failure, the driver gives the server five seconds to exit, then kills
the whole group, by group, never by name, and waits until no member is left, so
that a worker started meanwhile can't outlive the run; it also kills any worker
it sampled that left the group, after checking its start time and executable.
A process still running afterward is a finding. Build, then run it under the
shared benchmark lock:

```sh
make bench-build
mkdir -p build/bench-results/soak-work
flock ~/.t3/scratch-virgil/bench.lock python3 scripts/bench-memory-soak.py \
  --exe build/bench/virgil-lsp --cpu 12 --work-dir build/bench-results/soak-work \
  --output build/bench-results/soak.json
python3 scripts/bench-memory-soak.py --smoke --exe build/bench/virgil-lsp --output build/soak-smoke.json
python3 test/benchmarks/test_memory_soak.py
```

The fixture is three complete copies of the pinned Aeneas sources and the
tolerance observer, `first`, `second`, and `third`, as three projects of one
configuration. Each copy fits the project input bound on its own (207 files and
about 3.1 MB as analysis capture counts them in the measured run below, under
1,024 files and 4 MiB), and each offers the parseable-text cache about 3.21 MB,
so that any two exceed its 4 MiB. Both charges include paths, so their exact
values depend on the work directory and context name. One server lifetime with
no worker options runs two warmup cycles, kept apart, and then 80 measured
cycles. Each cycle changes a numbered comment
at the end of each context's open observer in turn and waits until that
context's analysis completes: a definition of `stable` in the observer and of
`StringBuilder` in `aeneas/src/util/Paths.v3` must equal the context's first
answers, and `virgil-lsp/snapshot` must hold that context's sources, current,
at the edited version. After the second and third analyses, a definition in the
context analyzed just before must be answered without any analysis, which shows
that admission kept the latest snapshot. The cycle then sends one completion in
the third context and cancels it at once, recording whether the cancellation
came first. Every tenth cycle closes and reopens a context's observer, every
twentieth opens `aeneas/src/arm64/Arm64AddrPatcher.v3` with `def pos = mw.pos;`
broken, restores it, and closes it, and every fortieth adds a source with
descriptor clauses and `-lang:descriptors` to one context through an unsaved
configuration, then restores both and deletes the file. Each of these takes the
next context in turn. Every step waits for request barriers, never a sleep.
Each reply wait and non-blocking write has a 15-second deadline, capped by the
run's 30-minute deadline, so a server that stops reading ends the run with a
deadline finding instead of hanging it. `--smoke` runs only cycles 39 and 40,
without warmups or a build manifest; cycle 40 has every periodic action.

At every step the driver records the server's and worker's current RSS and
high-water mark, the worker's PID, each admission's charges and outcome, each
completed analysis's snapshot charge and parseable-text charge, the worker's
live bytes after its collection, and the server's semispace use after decoding.
The sampler adds each PID's largest RSS every 50 ms, per cycle, and the largest
sum of one pass. These are different quantities, in separate fields: RSS and
high-water marks are what the OS reports for each process; the worker's
reserved heap is the total for both semispaces from HELLO; live bytes are the worker's
retirement measure; allocation is cumulative; and charges are the server's own
accounting of what a snapshot or cached text retains, never its RSS. BENCH
builds add these server probes for the soak:

| Metric | Meaning |
| --- | --- |
| `admit_replaces`, `admit_incoming_bytes`, `admit_retained_bytes`, `admit_latest_bytes`, `admit_pinned_bytes`, `admit_protected_bytes` | One admission's kind (1 for an analysis that replaces the context's snapshot), its reservation, the charge of every retained snapshot, of the latest and a rename's pinned original, and what admission may not free. |
| `admit_own_freed_bytes`, `admit_evicted_bytes`, `admit_outcome`, `admit_after_bytes` | The context's own snapshot freed, each other context's snapshot evicted, the outcome (0 refused, 1 fit, 2 freed), and the retained charge after freeing. |
| `snapshot_bytes` | A completed automatic analysis's snapshot charge. |
| `text_cache_offered_bytes`, `text_cache_evicted_bytes`, `text_cache_bytes` | A completed analysis's parseable texts offered to the cache, the charge it evicted for them, and its total charge after. |
| `worker_pid`, `worker_analyses`, `worker_uid_next`, `worker_live_bytes`, `worker_live_limit_bytes` | A started worker's PID, and after each completed analysis the restart policy's inputs and its live limit. |

The driver checks that the cache charge stays within 4 MiB while the contexts'
texts together exceed it; that every retained snapshot charge stays within
32 MiB and every admitted reservation fits beside what admission keeps; that
some admission had to evict an inactive context's snapshot; that admission
never frees what it protects; that each retirement's logged reason is the one
the limits give for the worker's last analysis, and that none was due and
missed; that a worker crash, timeout, or other failure produces a retirement
violation while retaining its `failed` classification, including after its
query was cancelled; that each worker's reported analysis count, which decides
retirement by count, matches the completions observed for it, with no reset,
missing increment, or skip; that the live limit is an eighth of the worker's
heap; that no answer binds anywhere but the context's own copy; and that no nonempty publication for
an open document carries a version older than one already confirmed or
published. A refused admission is recorded with its cause, `pinned` when a
rename's original alone prevents it and `protected` otherwise. Any failure is a
finding: the run keeps that cycle's protocol transcript and the whole stderr,
writes its output, and exits nonzero. A barrier that can't complete ends the
run, with its transcript, in `<output>.failure.json`. The soak doesn't rename,
so pinned admission is covered by `WorkspaceProjects:rename_snapshot_budget`,
which injects the state, and by the helper test of refusal causes.

Analyses are attributed to the step in whose window the server decoded them,
so a cancelled completion's analysis, which the worker still finishes, counts
in the next step. The 50 ms sampler reads `/proc` from the driver, which shares
the pinned CPU with the server and worker. A sampled RSS is a lower bound of the
peak between samples; a worker that ran entirely between two passes would be
counted in `unobserved_workers`, with an unknown peak, never zero. A flat RSS
shows that the processes reused memory they had already committed, as the
semispaces do; it is not proof that nothing leaks.

### Measured envelope (2026-11-01)

Measured on 2026-11-01 at `500b9f53` (server
`5856521120f6d5cb40da223b9f63e039426625a1d5ba88c7bfd2b46f502b591e`, worker
`c611b6e99aaa7fab54693122ce58efc2acaccfc6fc8785547682c5f1d69c893e`, build
manifest `9a9b59aa69b17e214aff78e6b879214309b1f6e445ecfb4651dcc82f0b93d553`),
with the driver of `b767dece`
(`0fae6ec880d3d963b7b7cc48a911f92c6ed6222db1d8e520c998e5ec22f6f547`), on the
i7-14700KF host of the [reference baseline](#reference-baseline), pinned to CPU
12 (governor `powersave`), with the default 384 MiB worker heap and the command
above. The fixture's tree hash was
`30913a056ae8ab025a596973654a7eadf6825f8e7a975dd410cbef4e044b79f9`. The smoke
run took 8.5 seconds and the full run 106.3 seconds, with no findings. At the
end, the server's process group had no members left to kill.

The worker completed 381 analyses: 240 measured and 6 warmup main analyses, 3
at startup, and 132 others: 82 cancelled completions, 16 for the overlay closes
and reopens, 12 for the broken and restored file, and 22 for the descriptor
steps. Every cancellation arrived before its completion's reply. No analysis
was discarded. A main step took 324.7 ms at p50 and 437.7 ms at p95, through
its definitions and inspection.

| Bound | Observed | Limit |
| --- | ---: | ---: |
| Parseable-text charge, 299 completions | at most 4,161,519 bytes | 4,194,304 |
| Contexts' parseable texts together | 9,625,069 bytes, evictions in 271 completions | more than 4,194,304 |
| Retained snapshot charge, 381 admissions | at most 26,288,232 bytes | 33,554,432 |
| Retained charge and reservation after admission | at most 30,036,801 bytes | 33,554,432 |
| Snapshot charge of one context | 13,142,798 to 13,145,434 bytes | |
| Reservation | 16,891,367 to 16,892,902 bytes | |
| Admissions that evicted an inactive context | 265 (3 more freed only their own) | at least 1 |
| Refused admissions | 0, pinned charge 0 | |
| Latest snapshot kept, checked by navigation | 164 of 164 | |
| Stale bindings, stale-version publications | 0 and 0, over 583 publications | 0 |

Each Aeneas snapshot is charged about 13.1 MB and its analysis reserves
16 MiB plus provenance, so beside the protected latest snapshot no second one
fits. Each cycle's cancelled completion in the third context evicted the second
context's snapshot, so the next cycle's first analysis fit beside the latest
alone; its second and third analyses each evicted the context analyzed two
steps before, apart from the steps after a reopen, break, or descriptor action,
which had already evicted it.

Four workers ran, each with a 402,653,184-byte heap and a live limit of
50,331,648 bytes, an eighth. Three were replaced after 100 analyses each,
correctly classified by count; at retirement their UID counters were at
1,980,223 to 1,980,229, under 1% of 2^28, and their live bytes at 177,088, so
neither of the other limits was near. The fourth was live at the end, after 81
analyses. The sampler made 2,192 passes and observed all four workers; every
PID had a high-water mark. A pass took 162 µs on average, 0.36 seconds of the
run, including time the driver waited for the shared CPU.

Values settled after each measured cycle's third analysis, over 80 cycles:

| Value | Cycle 1 | Cycle 80 | First 10, median | Last 10, median | Slope per cycle |
| --- | ---: | ---: | ---: | ---: | ---: |
| Server RSS | 210,608,128 | 210,608,128 | 210,608,128 | 210,608,128 | 0 |
| Worker RSS | 193,056,768 | 193,069,056 | 193,062,912 | 193,069,056 | 662 |
| Worker live bytes after collection | 177,088 | 177,088 | 177,088 | 177,088 | 0 |
| Server semispace use after decoding | 82,805,952 | 56,331,296 | 75,558,020 | 75,562,736 | 5,947 |

The server's RSS and high-water mark reached 210,608,128 bytes in the first
cycle and never moved, and each worker's high-water mark was 193,064,960 to
193,077,248 bytes. The largest sum in one pass was 403,685,376 bytes. The
server's semispace use after decoding ranged from 31,034,304 to 101,563,728
bytes as its collector ran, and its first and last medians were within 5 KB.
The 662-byte worker slope is the difference between workers, whose settled RSS
ranged from 191,733,760 to 193,069,056 bytes, not growth within one. These are
this schedule's values, not a proof that nothing leaks.

The first full run, with the same executables, passed 79 measured cycles with
no findings, then stopped at a barrier in cycle 80's descriptor step. To read
the second context's snapshot, that step asked for an explicit project
analysis, and admission refused it: an explicit analysis keeps the context's
own snapshot, and two Aeneas snapshots with the reservation exceed the budget
whenever another context was analyzed last. That is the documented admission
rule, so the step now makes the context's analysis the latest with one more
edit. A full run with that driver, `fd7ee3bb`, before the review fixes of
`b767dece`, had the same counts, charges, and findings and the same server RSS
in every cycle; its settled worker RSS differed by at most 208,896 bytes in a
cycle. In those builds, a completion or signature help was admitted the same
way: with two Aeneas-size projects, a completion in the one not analyzed last
was refused and got keywords only, with nothing logged, until that project was
analyzed again. [Analysis snapshots](architecture.md#analysis-snapshots-partly-present)
owns the current query admission rules.

The same session ran the full [tolerance capture](#release-scorecard) with the
same server build. It took 514.5 seconds, and the scorecard passed with the
current floors in both modes: primary 180/185, held-out 44/94, zero wrong
answers, zero expectation mismatches, and zero session wrong answers.

## Server collections in completion hits (2026-11-02)

Measured on 2026-11-02 for [#433](https://github.com/SaltedTan/virgil-language-server/issues/433).
The 2026-11-01 re-measurement of the [budget proposal](performance-budgets.md)
at `bf9e70a8` found the fourth measured Aeneas `completion_hit` of every block
at 29.6 to 35.3 ms, in both the full-scenario and the error-result captures,
while the 2026-10-31 capture at `85baf49d` had no such tail. The cause is a
collection in the server process. No change made a cache hit allocate more, and
the bytes surviving each collection stayed broadly similar. [#418](https://github.com/SaltedTan/virgil-language-server/issues/418)
cut the server's allocation over the request loop by about 38%, so fewer
collections run, and one of them now falls in that hit.

The captures' JSON keeps each session's whole stderr. The traces of 1,918 to
1,920 characters are the fresh-analysis sessions, which end after one analysis.
The last session's trace is complete, but the default probes record server
collections only inside worker round trips, and a cache hit has none.

**Method.** The capture's command, restricted to `--project aeneas`, on a
`BENCH=1` build of main `aebe48eb` reproduced the tail: the fourth measured hit
of all six blocks took 30.7 to 36.9 ms, and the hits' median was 6.959 ms.
For attribution, a temporary probe, not committed, wrapped the server's
handling of each LSP message and wrote, before the handler returned, the
server's collections, their time (the runtime's `GcStats.collection_us`), the
bytes that survived them, and the bytes allocated. A completion reply leaves
from a deferred handler before that wrapper returns, so the client copy that
read these probes waited 5 ms after each validated reply, outside the timed
interval, and skipped them in the cache-hit check of `--require-probes`.
Nine builds carried the probe: `85baf49d` and the merge of each runtime change
since then, leaving out #423, which changed only a comment in `src/`. They were built in equal-length worktrees with
`BENCH=1 all bench-metadata` and byte-identical generated BuildInfo text, and
ran as one comparison: Aeneas only, six alternating process blocks, two
warmups and five measured operations per block, pinned to hardware thread 12
under the shared benchmark lock, with every semantic check passing and no
samples removed. Other agents' work on the machine put the one-minute load
average at 8 to 12 when the runs started, which may widen the spread of the
times below. The allocation and where each collection fell were the same in
every block of a build. The raw data, the temporary probe and client, and the
scripts are in the lead's scratch directory
`/home/sean-tan/.t3/scratch-virgil/tail-433/`: `README.md` indexes them, and
`runs/probe-main.json` and `runs/ablation.json` are the reproduction and the
ablation.

Allocation and collections are the same in every block of a build. The loop
is the seven iterations, two warmups and five measured, of definition through
`completion_hit`, with the inspections between them, from the first definition
to the last hit. A miss's own allocation is its message's handling, which includes the source
capture; its worker round trip allocated another 8,186 to 8,194 KB in every
build. Times are milliseconds:

| Build | Hit allocation (KB) | Miss allocation (KB) | Loop allocation (MB) | Loop collections | Surviving (MB) | Hits with a collection | Hit median without one |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `85baf49d` (#409) | 3,093.8 | 20,731.1 | 749.5 | 9 | 19.1–22.8 | none | 6.1 |
| `d7bd2444` (#411) | 3,093.8 | 20,731.1 | 749.5 | 9 | 19.1–22.8 | none | 5.9 |
| `1d296d55` (#412) | 3,093.8 | 20,731.1 | 749.5 | 9 | 19.1–22.8 | none | 7.3 |
| `f3cf0707` (#416) | 3,093.8 | 20,731.1 | 749.5 | 9 | 19.1–22.8 | none | 7.4 |
| `265fc8d6` (#417) | 3,056.6 | 20,693.9 | 747.4–747.5 | 9 | 19.1–22.8 | first measured, 6 of 6 blocks | 7.6 |
| `109d5ca5` (#418) | 3,056.6 | 12,645.3 | 465.6 | 5 | 19.1–23.9 | fourth measured, 6 of 6 | 7.1 |
| `b62d7f37` (#421) | 3,056.6 | 12,646.1 | 463.3 | 5 | 19.1–24.0 | fourth measured, 6 of 6 | 5.9 |
| `f8b1a37e` (#425) | 3,056.6 | 13,021.2 | 466.0 | 5 | 19.1–24.0 | fourth measured, 6 of 6 | 7.3 |
| `aebe48eb` (#427) | 3,056.6 | 13,021.2 | 466.0 | 5 | 19.1–24.0 | fourth measured, 6 of 6 | 7.5 |

The surviving bytes are those of each collection in the measured samples. They
were broadly similar in every build, though the largest rose from 22.8 MB to
24.0 MB; collections at different points don't measure retained data exactly.
A miss allocated 375 KB more after [#425](https://github.com/SaltedTan/virgil-language-server/issues/425). A hit's own allocation, about 3 MB,
fell by 37 KB with [#417](https://github.com/SaltedTan/virgil-language-server/issues/417).
#418 reads a closed project file into one array of its length. In the Aeneas
project, that took 8 MB from each completion miss and each prepareRename,
16 MB from each rename, and about 40 MB from each loop iteration in all. The
server's default 200 MiB heap has a copying collector that collects when its
100 MiB half fills, so the loop's collections fell from nine to five. Each
block runs the same messages with the same allocation, so a collection falls
on the same operation in every block. Before #417 they all fell in renames,
misses, prepareRename requests, and requests outside the samples. #417's 2 MB
less over the loop put one in the first measured hit, and #418 put one in the
fourth. The later changes moved allocation by less than 3 MB and
left it there.

A collection in a hit took 26.3 to 30.0 ms at each build's median, 18.2 to
52.4 ms in all, and a hit with one took 30.3 to 33.9 ms at each build's median.
Collections elsewhere in the samples took 18.7 to 22.7 ms at each build's
median with similar surviving bytes; this benchmark doesn't show why one in a
hit takes longer. Without a collection, hits took 5.9 to 7.6 ms at the median.

For a `completion_hit` budget target, the Aeneas p90 and p95 measure whether a
server collection falls in one of a block's five measured hits, not the hit's
own work. One of five hits is 20% of the samples, so both percentiles land on
it when it falls there and miss it when it doesn't, and a change that only
moves allocation, even one that reduces it as #418 did, can move it. A target
should either allow one server collection on top of the hit's own time, which
took 18 to 52 ms in a hit on this host, or apply to hits without a collection and count server
collections around each request separately, which the committed probes don't
yet do.
