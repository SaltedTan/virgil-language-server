# Development guide

## Prerequisites

- Linux x86-64, or an Apple Silicon Mac with Rosetta 2 (`softwareupdate --install-rosetta --agree-to-license`). WSL 2 on Windows is best effort.
- `git`, `make`, `bash`, `curl`; Python 3.9+ for project-fixture preparation and cleanup, interactive protocol tests, and editor tests
- No separate Virgil install: the build uses the pinned submodule at `vendor/virgil`.
- Optional locally: Neovim 0.11+ for the headless editor smoke test (required in CI).
- Optional: the [VS Code development client's prerequisites](../clients/vscode/README.md#setup).

## Build and test

```sh
git submodule update --init --recursive   # if you cloned without --recurse-submodules
make            # builds build/virgil-lsp and build/virgil-lsp-worker
make test       # builds and runs the default suites listed below
make test-unit  # only the unit tests, with the worker; ARGS='ProjectExpansion:*' runs a subset
make test-nvim  # only the headless Neovim smoke test (see Neovim testing below)
make clean
```

`make test` runs the unit tests first, then the other suites through `test/suites.py`, as many at once as the process may use CPUs, up to 4, or `SUITE_JOBS`, as in `make test SUITE_JOBS=1`. The Makefile's `SUITES` list holds them, longest first. With more than one at once, each suite's output is printed whole once it finishes, after a line with its result and time; with `SUITE_JOBS=1` it streams as before. Every suite runs even after one fails, and the run ends with each suite's time, slowest first, and the failed ones.

On SIGTERM, SIGHUP, or SIGINT, the suite runner stops starting suites and forwards that signal to every running suite's process group so it can clean up. A second signal or a 10-second grace period sends SIGKILL to groups still running. The runner reaps its children and exits with 128 plus the first signal. Suites are responsible for cleaning up any processes they move into separate sessions.

`make test` and `make test-unit` leave the build directory removable with a plain `rm -rf` or `git worktree remove`, even after a failing test or an interrupt, with no `make clean` or `chmod` first. Two fixture directories are unreadable only while the unit tests run; the [project fixtures](../test/fixtures/projects/README.md) describe how.

Each executable is a single Aeneas invocation (see `Makefile` and [architecture.md](architecture.md#build-model)); follow the [README's executable placement guidance](../README.md#building). `scripts/v3c.sh` selects the host target and the pinned `bin/stable` compiler, and `scripts/v3c.sh -print-target` and `-print-compiler` print the target and the Aeneas binary it would use. It accepts these overrides:

| Variable | Purpose |
| --- | --- |
| `VIRGIL` | Use a different Virgil checkout, e.g. `make VIRGIL=~/src/virgil` |
| `VIRGIL_V3C` | Use a specific Aeneas binary, e.g. a bootstrapped `bin/current/x86-64-linux/Aeneas` |
| `V3C_TARGET` | Force a target such as `x86-64-darwin`. The `Makefile` also uses it to choose `src/os/<target>/`. |
| `WORKER_HEAP` | The analysis worker's heap for `make` and release packaging, e.g. `make WORKER_HEAP=1g` (see [ADR-0004](decisions/0004-analysis-worker-process.md#measurements) for the default and its measurements) |

[Releases](releasing.md) covers packaging the executables as release archives, locally and in the release workflow, including the [editor tests that each release runs on its own files](releasing.md#how-the-workflow-works).

## Benchmarks

See [Performance benchmarks](benchmarks.md) for the pinned small, util, and Aeneas
corpus, `make bench`, phase and GC measurements, baseline numbers, and fair build
comparisons. The [weekly benchmark workflow](benchmarks.md#scheduled-comparison)
compares two builds on Linux and macOS, reports advisory warnings, and retains
the JSON and summaries as artifacts.

## Running

```sh
build/virgil-lsp --version
build/virgil-lsp parse path/to/file.v3    # development command: syntax diagnostics
build/virgil-lsp analyze a.v3 b.v3        # development command: parse and verify as one program
build/virgil-lsp repair --cursor=a.v3:120 a.v3 b.v3  # development command: the completion repair spike
build/virgil-lsp --stdio                  # LSP over stdio
```

`analyze` runs the compiler spike: the files are read, then parsed and verified together in an [analysis worker](architecture.md#analysis-worker) process, and semantic diagnostics are printed, at most 100 for each file, with a line such as `main.v3: 7901 more errors in this file are not shown` after the last one printed for a file that has more ([file limit](architecture.md#semantic-diagnostics)). `--bindings` also prints each indexed use and its source declaration according to the [binding contract](architecture.md#compiler-adapter). `--display` also prints, in byte order, each declaration site with its signature, each indexed use with its declaration's signature and any use-site type, and each builtin use with its type, as the [display text](architecture.md#display-text) defines them, and each [call site](architecture.md#call-sites). A call is shown by byte offset, from its opening parenthesis to just past its closing one, as `test/fixtures/analysis/call-sites/main.v3:@1457-1463 call METHOD add: def add(a: int, b: int) -> int; parameters [a: int] [b: int]; arguments @1458-1459, @1461-1462` for `add(1, 2)`, with its callee's kind and name, or `builtin`, if it has one, and its signature, each parameter in brackets, and its arguments' spans. `--stats` prints timing and compiler global-state counters, as measured in the worker, and the result's charged decoded allocation to stderr; ADR-0004 owns the [result budget](decisions/0004-analysis-worker-process.md#supported-program-size). With `--bindings` or `--display`, the statistics also include the binding count, collection time, and skipped-use total defined by the [binding contract](architecture.md#compiler-adapter). `--repeat=<n>` runs the analysis n times and fails if any run differs; workers are replaced between runs as the [restart policy](decisions/0004-analysis-worker-process.md#restart-policy) requires. `--worker-timeout-ms=<n>` and `--worker-max-analyses=<n>` set the policy's time and analysis-count limits, for testing; like `--repeat`, they accept positive decimal values of at most nine digits and can raise or lower the defaults. If the worker crashes or times out, its stack trace (if any) and the supervisor's report go to stderr, and the command exits with status 1. An oversized request also exits with status 1; the worker protocol's [request admission rule](decisions/0004-analysis-worker-process.md#restart-policy) includes paths and encoding overhead, not just source bytes. An over-budget result exits with status 1 and reports that the program is too large. ADR-0004 owns the [decoded allocation budget](decisions/0004-analysis-worker-process.md#consequences) and [supported program size and recovery guidance](decisions/0004-analysis-worker-process.md#supported-program-size). For example:

```sh
build/virgil-lsp analyze --bindings test/fixtures/analysis/two-file/*.v3
scripts/bench-analysis.sh        # parse and verify timings: small fixture and the Aeneas sources
```

The benchmark accepts a positive run count (default 10), with `EXE` and `VIRGIL`
overrides for the executable (which needs `virgil-lsp-worker` beside it) and the
Virgil checkout. Each run starts a fresh worker. Each run must succeed and emit
parse/verify timings on stderr. Otherwise it reports the input set and run number,
the exit status for a failed command, and the command's stderr, then exits non-zero
without printing a summary for that input set. Temporary files are cleaned up on
exit, including failure or interruption.

`repair` runs the completion [repair spike](architecture.md#repair-spike) of [ADR-0006](decisions/0006-incomplete-code-repair.md). `--cursor=<file.v3>:<offset>` names one of the files, spelled as on the command line, and a byte offset in it. The command tries each repair at that cursor in order, analyzes the repaired files as one program in a worker, and prints one line for the cursor (`case:`, with its member access, syntactic positions, and open delimiters), one per repair (`attempt:`, whether it parsed, how many diagnostics are outside the probe and the first of them at its original byte offset, the probe's diagnostic, and the context it shows), and a `result:` line with the first repair that parsed, verified, and was usable, the usable context, and every usable repair. `--cut=line` or `--cut=file` first deletes the rest of the cursor's line or file, as when typing at the end of it. `--runs=<n>` then times n analyses of the first usable repair, alternating with n of the files as given, before any cut, and prints their medians and 90th percentiles (`time:`). `--worker-timeout-ms=<n>` applies as for `analyze`. It exits with 0 if a repair was usable, 1 if none was, and 2 for invalid arguments. It never runs from the server, and nothing it analyzes is published.

The corpus of typing states for it is in `test/fixtures/repair/`: whole programs with `<|>` at the cursor in `cases/`, and real call and member sites of the Aeneas sources in `aeneas-sites.txt`. `scripts/repair-corpus.py` runs them all and prints each one's first usable repair and context, the success rate overall and by repair, and, with `--runs=<n>`, the latency of the Aeneas cases; `--only=<text>` selects cases by name and `--verbose` adds each report. A run without `--runs` takes about 15 seconds, and with `--runs=10` a few minutes, so `make test` runs only the `test/cli/run.sh` checks of a few cases:

```sh
python3 scripts/repair-corpus.py build/virgil-lsp
python3 scripts/repair-corpus.py --runs=10 --only=aeneas build/virgil-lsp
```

### Development stdio requests

Whole-program analysis has explicit-URI (**option 2a**) and project-context triggers over `--stdio`. These requests are unstable development tools, not advertised capabilities. Neither publishes diagnostics nor changes the automatic [parser](architecture.md#parser-diagnostics) and [semantic](architecture.md#semantic-diagnostics) diagnostics.

After `initialize`, send `virgil-lsp/analyze` with a nonempty `uris` array:

```json
{"jsonrpc":"2.0","id":"analysis-1","method":"virgil-lsp/analyze","params":{"uris":["file:///absolute/a.v3","file:///absolute/b.v3"]}}
```

- Each URI uses the [document store's identity rules](architecture.md#uri-identity-on-linux-and-macos). Duplicates after normalization are rejected. Current open overlays win, including empty and non-file overlays; otherwise a local file is read from disk. The source bytes are captured at submission; later edits do not alter that analysis.
- Sources form one program in the given order. Missing/unreadable/over-budget sources and malformed parameters return `InvalidParams` (-32602). Disk reads are bounded before allocation; aggregate paths, source bytes, and a conservative metadata allowance must fit within 4 MiB, followed by the supervisor's exact frame-size check. The [client message limits](architecture.md#framing) leave room for analysis allocations and retained snapshots.
- Only one development analysis may be outstanding. A second gets `InvalidParams` rather than being queued. A request that arrives while an automatic or [temporary analysis](architecture.md#names) runs gets `InternalError` before snapshot admission. Other requests keep receiving replies while startup, pipe transfer, or analysis is pending.
- Completion returns `{"generation": 1, "diagnostics": [...], "documentVersions": [...]}` with the original request ID. Generations increase only for completed analyses, including analyses reporting syntax/type errors and temporary analyses, so inspected generations can have gaps. Each diagnostic has `path` (or null), `beginLine`, `beginColumn`, `endLine`, `endColumn`, `kind` (or null), and `message`. The last one reported for a path with more than 100 diagnostics also has `omitted`, the number of that path's diagnostics after it that were not reported ([file limit](architecture.md#semantic-diagnostics)). These are **one-based compiler coordinates**, not LSP UTF-16 ranges; they have the same limitations as the unstable CLI's reports.
- A crashed, timed-out, unavailable, or malformed worker returns `InternalError` (-32603). An over-budget result also returns `InternalError`, with a distinct too-large message. The message states the failure and that the previous snapshot is kept; stderr records the failure. ADR-0004 owns the [replacement policy](decisions/0004-analysis-worker-process.md#restart-policy), including its rationale for over-budget results, and [size limits and recovery guidance](decisions/0004-analysis-worker-process.md#supported-program-size).
- Shutdown cancels pending analysis with `RequestCancelled` (-32800) before replying to `shutdown`; exit and EOF cancel and reap any worker. All preserve the previous snapshot.

Alternatively, submit the project for a document with `{"uri":"file:///absolute/shared.v3"}`. Its deterministic active context is selected. `uri` and `uris` are mutually exclusive. This expands and preflights the entire project before reading sources; overlays take precedence, and project disk reads reject symlinks. A document without a project or an empty expanded program gets `InvalidParams`. The project's [`compilerArgs`](configuration.md#compiler-flags) apply to its analysis; explicit `uris` use the default language options.

Project results add `"projectConfiguration":{"uri":"file:///absolute/.virgil-lsp.json","revision":1,"name":"server","current":true}`. The [analysis snapshot policy](architecture.md#analysis-snapshots-partly-present) owns context retention, its aggregate budget and eviction rules, and configuration-revision invalidation. A project or explicit-URI submission that cannot fit within that retention budget returns `InvalidParams`. An invalidated configuration revision makes the stamp and every source's `current` false. Stamps are server-owned and are not added to the worker protocol.

`virgil-lsp/snapshot` (no params required) returns the same response shape for the last completed automatic or development analysis, or `null` if none exists; [temporary analyses](architecture.md#names) leave it unchanged. It refreshes known configurations but reads analysis results only from server-owned data, so it works during a hang, after a crash, and across routine replacements. Each `documentVersions` entry, in submission order, has the source's canonical `uri`, its version (an integer, including zero or negative values), or `null` for a disk input, and a `current` boolean computed against the document store at the time of the reply. An open source's version is the live overlay version while it retains the analyzed identity, so it includes the version a version-only change accepted, and otherwise the version captured with the analysis. For example:

```json
{"generation":1,"diagnostics":[],"documentVersions":[{"uri":"file:///absolute/a.v3","version":3,"current":false},{"uri":"file:///absolute/b.v3","version":null,"current":true}]}
```

A [method-body fallback](architecture.md#method-body-fallback) or
[current-text repair](architecture.md#current-text-repair) adds a
`substitution` object to each affected `documentVersions` entry. Its byte offsets
are `begin`, `effectiveEnd`, and `currentEnd` for the changed interval, and
`bodies`, a list of objects with `begin` and `end`, for the excluded current
bodies: fallback's one body, or each body that repair blanked, whose interval
spans the blanked bytes and keeps its length. The stamp and `current`
still describe the overlay identity compared at capture. Raw inspection
diagnostics use effective-text compiler coordinates, including those inside
an excluded body, such as an emptied body's missing return; published LSP
diagnostics are filtered and mapped to current text. Project `analyze` requests use this
capture policy; explicit `uris` requests continue to analyze exact source bytes.

The [analysis snapshot freshness policy](architecture.md#analysis-snapshots-partly-present) defines `current`, including overlay invalidation and the assumptions for disk inputs, and configuration-revision invalidation. This remains deliberately stale inspection data: no semantic results are published or discarded by this request.

`--stdio` accepts the same unstable `--worker-timeout-ms=<n>` and `--worker-max-analyses=<n>` policy overrides as the CLI `analyze` command. They also apply to automatic analyses. See the [restart policy](decisions/0004-analysis-worker-process.md#restart-policy) for the defaults and [semantic diagnostics](architecture.md#semantic-diagnostics) for automatic scheduling triggers.

## Tests

`make test` and `make test-unit` also build `build/stall/virgil-lsp-worker`,
a test-only build of the real worker with `WorkerStall.enabled` enabled through
`-redef-field`. It stalls on the source marker `workerStallsHere` after arming
the orphan alarm; ordinary builds compile the hook out. Before running the CLI
or worker protocol suite directly, build both workers and the server with
`make all build/stall/virgil-lsp-worker` (use the corresponding paths with a
custom `BUILD`).

| Location | What | How it runs |
| --- | --- | --- |
| `test/analysis/RetainProbe.v3` | Fresh-process live-heap regression for dropped analysis snapshots, including errors | `build/retain-probe [source.v3 ...]` (no arguments runs generated fixtures; see [measurements](architecture.md#adapter-retention-workaround-and-regression-probe)) |
| `test/analysis/TypeDepthProbe.v3` | Analysis and subsequent tiny analysis of 100,000 inferred array and tuple levels, including lazy snapshot bindings | `build/type-depth-probe` (1 GB heap, default stack) |
| `test/unit/` | Virgil unit tests using Virgil's `lib/test` (`UnitTests.register`). `AnalysisSupervisor:*` uses the worker builds described above for compiler crash and timeout cases; the worker crash traces are expected. Result-budget and malformed-result cases use the unit-test executable as a synthetic worker on the same pipes. | `make test-unit` (with `ARGS='glob'` for a subset) |
| `test/test_suites.py` | The suite runner, `test/suites.py`, with shell commands for suites: suites at once, each printed whole, live output one at a time, a failing suite, times slowest first, no input, and termination | `python3 test/test_suites.py` |
| `test/unit/test_run.py` | The unit-test runner, `test/unit/run.py`, with a stand-in executable: a process and restricted fixtures for each group, groups running at once in slots of their own, slots left by an interrupted run, failed and unfinished tests, a missed group, a pattern's single run, termination, and a count probe stopped by a signal or refused its group signal | `python3 test/unit/test_run.py` |
| `test/fixtures/projects/test_cleanup.py` | Fixture permission restoration after passing and failing runs, including through the Makefile's `run-unit-tests` recipe with a stand-in executable, and removal through `make clean`, independent of the build directory | `python3 test/fixtures/projects/test_cleanup.py` |
| `test/e2e/test_coverage.py` | Consistency of the editor tests' coverage manifest and result schema, described in [Editor test results](../test/e2e/RESULTS.md) | `python3 test/e2e/test_coverage.py` |
| `test/e2e/clean-install/` | [README installation checks](releasing.md#clean-install-test) | `test_readme.py` and `test_install.py` run in `make test`; see the release guide for the standalone editor run |
| `test/e2e/upgrade/` | The opt-in [upgrade and rollback rehearsal](releasing.md#upgrade-and-rollback-rehearsal), and its helper tests with synthetic assets and stand-in servers: rejected targets, versions, release pairs, checksums, and archive members, failure propagation to later phases and the receipt, and cleanup of the temporary installation and of process groups | `python3 -m unittest discover -s test/e2e/upgrade -p 'test_*.py'`, by hand; neither the tests nor the rehearsal run in `make test` or CI |
| `test/e2e/test_editor_results.py` | The result stream validator, `scripts/editor-results.py`, on streams of each editor suite built from the coverage manifest, each breaking one of the [stream's rules](../test/e2e/RESULTS.md#result-events) | `python3 test/e2e/test_editor_results.py` |
| `test/e2e/test_nvim_results.py` | The headless Neovim test's [result stream](../test/e2e/RESULTS.md#the-headless-neovim-writer), `test/e2e/nvim/run.sh --results`, with real Neovim 0.11+ when available and with a stand-in `nvim` and the built server: observed checks match the plan, passing, failing, incomplete, unavailable, interrupted, and killed streams, malformed observations, a deadline, descendant cleanup, reader exceptions, and edited streams that the validator rejects | `python3 test/e2e/test_nvim_results.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/e2e/test_editor_results_render.py` | The results renderer, `scripts/editor-results.py render`, on streams built as `test_editor_results.py` builds them: each cell's wording, the selection's and the markers' errors, the bytes outside the markers, determinism, and `--check`; and that the [rendering example](../test/e2e/results-example/README.md) is up to date | `python3 test/e2e/test_editor_results_render.py`, or with `--write-example` to regenerate the example |
| `test/cli/run.sh` | Command-line behaviour, exit codes, stdout cleanliness, worker crash, hang, and replacement, 20 analyses of the Aeneas sources, the `repair` command's report on a few corpus cases, benchmark driver regressions (also runnable with `bash test/cli/bench-analysis.sh`) | `test/cli/run.sh build/virgil-lsp` (after the test builds above) |
| `test/benchmarks/` | Benchmark corpus pins and rejection checks, the small [LSP benchmark](benchmarks.md#run) smoke case, [scheduled comparison](benchmarks.md#scheduled-comparison) flags, GC counts, ref selection and recorded baseline rendering, the [syntax-error tolerance benchmark](benchmarks.md#semantics-during-syntax-errors)'s scoring and cleanup checks, and its smoke replay | `python3 test/benchmarks/test_client.py`, `python3 test/benchmarks/test_report.py`, `python3 scripts/bench-lsp.py --smoke --exe build/virgil-lsp --output /dev/null`, `python3 test/benchmarks/test_tolerance.py`, and `python3 scripts/bench-tolerance.py --smoke --exe build/virgil-lsp --output /dev/null`; also in `make test` |
| `test/protocol/shapes.py` | Bounded offline LSP 3.17 request and successful reply shapes, diagnostics params, and semantic symbol/version oracles; see the [protocol README](../test/protocol/README.md#bounded-lsp-317-shape-tests) and its [reply coverage, open-structure policy, bounds, and limits](../test/protocol/README.md#successful-reply-shapes) | `make test-protocol-shapes` (`PROTOCOL_SHAPES_SEED` and `PROTOCOL_SHAPES_CASES` are overridable) |
| `test/protocol/test_reply_shapes.py` | The reply-shape validator, `test/protocol/reply_shapes.py`, on hand-written JSON: missing members, wrong primitives and array members, malformed `InsertReplaceEdit` ranges, markup kinds, and diagnostics, each failure naming the method, type, and JSON path; null, union, capability, and extensible-data alternatives; and its depth, work, and reference-cycle bounds | `python3 test/protocol/test_reply_shapes.py`; also in `make test` |
| `test/protocol/source_mutations.py` | Seeded, token-aware mutations of the repair seeds: framing, one reply per request with its ID type, documented errors and notifications, request, case, and run deadlines, worker failures matched to documented signatures, recovery on clean source, and shutdown, with replayable case records; see the [protocol README](../test/protocol/README.md#seeded-source-mutations) | `make test-source-mutations` (`SOURCE_MUTATIONS_SEED`, `SOURCE_MUTATIONS_CASES`, and `SOURCE_MUTATIONS_ARGS` are overridable); not run by `make test` or CI |
| `test/protocol/test_source_mutations.py` | The mutation harness's bounds and hard caps, deterministic manifests, coverage manifest, worker-failure signatures, and artifact budget; a stand-in server's deadline, end of output, notification reply, changed ID type, and undocumented error; and, with the built server, replay of a saved case, a known compiler trap, and worker cleanup and replacement | `python3 test/protocol/test_source_mutations.py`; also in `make test` |
| `test/protocol/worker.py` | Interactive transcripts: concurrent replies during startup/hangs, compiler crash, retained snapshots, 20 Aeneas analyses using the default time limit with an elapsed-time summary, separate short-limit hang sessions, lifecycle cleanup, and large JSON alongside analysis allocations and snapshot replacement | `python3 test/protocol/worker.py build/virgil-lsp` (after the test builds above) |
| `test/protocol/coordinates.py` | Exact worker diagnostic, declaration, and occurrence byte ranges, compiler-coordinate binding exports, CLI and stdio compatibility, allocation rejection/recovery, and bounded malformed-string replay | `python3 test/protocol/coordinates.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/capacity_warning.py` | Five rejected over-capacity opens produce one editor warning and five stderr rejection logs; see the [Document store](architecture.md#document-store) for the capacity policy | `python3 test/protocol/capacity_warning.py build/virgil-lsp`; also in `make test` |
| `test/protocol/inlay_hints.py` | Inlay-hint labels, UTF-16 ranges, settings, freshness, worker recovery, and refresh support | `python3 test/protocol/inlay_hints.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/semantic_tokens.py` | Interactive [semantic token checks](architecture.md#semantic-tokens): every token type and modifier at UTF-16 positions, the legend and encoding, the option, files outside a project and that don't parse, method-body fallback and repair, and refresh support | `python3 test/protocol/semantic_tokens.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/type_definition.py` | Interactive [type-definition checks](architecture.md#type-definition) | `python3 test/protocol/type_definition.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/implementation.py` | Interactive [implementation checks](architecture.md#implementation) | `python3 test/protocol/implementation.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/type_hierarchy.py` | Interactive [type hierarchy checks](architecture.md#type-hierarchy), including items resolved after edits | `python3 test/protocol/type_hierarchy.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/selection_ranges.py` | Interactive [selection range checks](architecture.md#selection-ranges) | `python3 test/protocol/selection_ranges.py build/virgil-lsp` |
| `test/protocol/folding_ranges.py` | Interactive [folding range checks](architecture.md#folding-ranges) | `python3 test/protocol/folding_ranges.py build/virgil-lsp` |
| `test/protocol/call_hierarchy.py` | Interactive [call hierarchy checks](architecture.md#call-hierarchy), including items resolved after edits and method-body fallback | `python3 test/protocol/call_hierarchy.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/code_actions.py` | Interactive [code action checks](architecture.md#code-actions) | `python3 test/protocol/code_actions.py build/virgil-lsp`; also in `make test` |
| `test/protocol/document_symbol_budget.py` | [Document-symbol](architecture.md#document-symbols) replies at and past their bounds with the default heap: outlines of 64,000 declarations, flat and hierarchical, and of 1,000 flat ones that repeat a 100,000-character URI get `RequestFailed`, the largest admitted replies are complete, and each is followed by a small outline, a ping, shutdown, and exit | `python3 test/protocol/document_symbol_budget.py build/virgil-lsp`; also in `make test` |
| `test/protocol/outline_parse_budget.py` | The [parse budget](architecture.md#document-symbols) of outlines, folding ranges, and selection ranges with the default heap: `var a,a,a,…;`, an expression list, nested parentheses, an expression list behind a line comment that a carriage return ends, and expression lists after malformed slash and quote representation hints, just under the overlay limit, and 350,001 tokens, get `RequestFailed`, exactly 350,000 tokens get a reply, dense documents including both malformed hints get no semicolon quick fix without client diagnostics, and each is followed by the same request for a small document, a ping, shutdown, and exit | `python3 test/protocol/outline_parse_budget.py build/virgil-lsp`; also in `make test` |
| `test/protocol/fingerprints.py` | Snapshot freshness against disk changes made while the worker is paused mid-analysis: same-length, shorter, longer, and deleted files, file and directory symlinks, an unrelated source edited, recovery, and overlay identity | `python3 test/protocol/fingerprints.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/body_fallback.py` | Method-body fallback with a real two-file project: mapped UTF-16 definition, type definition, and diagnostics, excluded queries and targets, partial references and Read/Write/Text highlights, unterminated lexical regions, edit refusals, header errors, recovery, cold and closed-file controls, and development capture | `python3 test/protocol/body_fallback.py build/virgil-lsp` (with `virgil-lsp-worker` beside it); also in `make test` |
| `test/protocol/current_repair.py` | Current-text repair with a real three-file project: cold opens with a syntax error in a method body, with an error that the next line continues, with errors in two bodies, with a lost `{`, and with a match case's lost `}`, each exact substitution, definition, hover, references, call hierarchy, and diagnostics outside the bodies, suppression inside them, no sentinel or missing return in any answer, rename and workspace-symbol refusal, blanking beside method-body fallback in one analysis, restoration, and refused repairs (a method's own lost `}`, a lost `{` that layout doesn't place, a header, a field initializer, three bodies, an unterminated string) | `python3 test/protocol/current_repair.py build/virgil-lsp` (with `virgil-lsp-worker` beside it); also in `make test` |
| `test/protocol/bundled_library.py` | Bundled-library discovery, client override precedence, root errors, and diagnostics, hover, and definition into bundled sources | `python3 test/protocol/bundled_library.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/client_initialization.py` | Replay of real editors' initialization exchanges; see the [protocol test README](../test/protocol/README.md) for coverage and capture instructions | `python3 test/protocol/client_initialization.py build/virgil-lsp` |
| `test/protocol/first_project.py` | Checks the [first-project walkthrough](first-project.md)'s quoted protocol results against its checked-in example | `python3 test/protocol/first_project.py build/virgil-lsp` (with `virgil-lsp-worker` beside it); also in `make test` |
| `test/protocol/` | Golden transcripts: `--stdio` input bytes, expected output bytes and exit status | `test/protocol/run.sh build/virgil-lsp` (runs every case) |
| `test/diagnostics/` | Golden [semantic diagnostic](architecture.md#semantic-diagnostics) transcripts that interleave input with the expected messages, using the project fixtures. The [runner's header](../test/diagnostics/run.py) owns the transcript format. | `python3 test/diagnostics/run.py build/virgil-lsp [case ...]` (with `virgil-lsp-worker` beside it) |
| `test/release/run.sh` | Tag, `VERSION`, and `CHANGELOG.md` checks in `scripts/check-release.sh`, which the [release workflow](releasing.md) runs for every tag; the VSIX checks in `scripts/check-vsix.py`; the archive and VSIX listings and changelog-link rewriting in `scripts/release-notes.py`, on constructed release assets and a fixture changelog; bundled-library integrity and archive reproducibility in `test/release/test_library.py`; archive-to-VSIX payload provenance in `test/release/test_vsix_payload.py`; and exact release asset selection in `test/release/test_release_assets.py` | `test/release/run.sh`, or `test/release/run.sh --archive <archive>` to include the [archive installation smoke test](releasing.md#how-the-workflow-works) |
| `test/e2e/nvim/` | Headless plain Neovim configuration | See [Neovim testing](../editors/nvim/README.md#headless-smoke-test) for coverage, commands, and skip policy, and its [result stream](../editors/nvim/README.md#result-stream) |
| `test/e2e/lazyvim/` | The LazyVim configuration under the pinned LazyVim starter and plugins, through LazyVim's own keys in a terminal UI in tmux, against a release archive or a build | `make test-lazyvim`, or `test/e2e/lazyvim/run.sh --archive <archive>`; not run by `make test`. CI job `lazyvim-linux` runs it on Linux, not yet as a required check, and the weekly editor tests workflow on Linux and macOS. See the [LazyVim test](../test/e2e/lazyvim/README.md) for coverage, requirements, outputs, and pins |
| `clients/vscode/` | Type-check, lifecycle regression tests with a simulated extension host, and the regression tests of the Extension Host and Remote-SSH test runners, run by CI job `vscode-client` on Linux, which also packages the VSIX and uploads it as an artifact ([coverage](manual-testing.md#what-the-automated-tests-cover)), and a [manual checklist](../clients/vscode/README.md#manual-checklist) | See [client development commands](../clients/vscode/README.md#development); not run by `make test` |
| `clients/vscode/test/e2e/` | The VS Code client in a real Extension Host, locally and through Remote-SSH | See the [Extension Host test](../clients/vscode/README.md#extension-host-test) and [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test) for coverage, commands, and requirements, and [automated editor coverage](manual-testing.md#what-the-automated-tests-cover) for CI platforms; not run by `make test` |
| `test/fixtures/` | Source files used by tests. Bytes are preserved exactly (`-text` in `.gitattributes`). `test/fixtures/projects/` holds whole repositories with project files. | — |

The [manual test matrix](manual-testing.md) covers the editor and platform combinations that these tests don't. [Known limitations](known-limitations.md) lists what the alpha doesn't do yet.

The tests have no coverage report. The [interpreter coverage note](testing-notes/interpreter-coverage.md) records why the pinned compiler's `-coverage` option, which works only in its interpreter, can't measure the unit suite, and what it reports for the small groups that do run there. The [unit-runner signals note](testing-notes/unit-runner-signals.md) records the diagnosis and regression tests for two macOS CI failures.

The [header of `test/protocol/run.sh`](../test/protocol/run.sh) owns the transcript format, including fragmented input and version placeholders. The files are raw bytes with CR LF header lines (`-text` in `.gitattributes`), and `Content-Length` must count the payload's bytes exactly, so write them with a tool rather than an editor, for example:

```sh
frame() { printf 'Content-Length: %d\r\n\r\n%s' $(( $(printf %s "$1" | wc -c) )) "$1"; }
frame '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' > input
```

Planned addition: `test/features/`.

To add a unit test, create `test/unit/<Thing>Test.v3` and register test functions:

```virgil
def T = UnitTests.register;
def X = [
	T("Thing:does_something", test_does_something),
	()
];

def test_does_something(t: Tester) {
	t.asserti(2, 1 + 1);
}
```

Without `ARGS`, `make test` and `make test-unit` run each group of unit tests (the part of a name before the colon) in a process of its own through `test/unit/run.py`, then list the 25 slowest tests. As many groups run at once as the process may use CPUs, up to 8, or `UNIT_JOBS`, as in `make test UNIT_JOBS=1`. Tests edit the fixtures next to their executable, so each extra slot has its own fixtures and copies of the executable and both worker builds; see the [fixture permission lifecycle](../test/fixtures/projects/README.md) for slot layout and cleanup. With `UNIT_JOBS=1`, stdout and stderr stream live. With more than one slot, each group's stdout followed by its buffered stderr is printed together to the runner's stdout when the group finishes. Analyses in one process leave compiler state behind that slows every later one, so the late groups of a single process ran several times slower than on their own. The executable has no option to list its tests, so the runner finds the groups in the names that `test/unit` registers with `T(...)` or `UnitTests.register(...)`, and fails if they don't add up to the executable's own count. Run a subset with `make test-unit ARGS='Thing:*'`; the matching tests run in one process. Space-separated patterns are passed without shell pathname expansion, so `make test-unit ARGS='*'` runs every test in one process. `make test` always runs the full unit suite, ignoring `ARGS`. `make build/unit-tests` also prepares host-filesystem fixtures under `build/project-fixtures` (sparse oversized source, symlink cycle, linked file/directory, and FIFO). `ProjectExpansion:*`, `WorkspaceProjects:*`, and `LspProjectAnalysis:*` run on both Linux and macOS CI; they reuse the version-1 project fixtures, including the file shared by two programs. Use `make test` or `make test-unit` for the full unit suite: a bare `build/unit-tests` run fails the two `ProjectExpansion` cases that require unreadable directories; see the [fixture permission lifecycle](../test/fixtures/projects/README.md).

## Learning the compiler front end

The [roadmap's learning path](../ROADMAP.md#learning-path-for-the-compiler-front-end) lists which parts of Aeneas to read, and in what order. Useful entry points in `vendor/virgil`:

- `aeneas/src/vst/Parser.v3` — `Parser.parseFile`, `parseToplevelDecl`
- `aeneas/src/vst/Vst.v3` — syntax tree, `VarBinding`, visitors
- `aeneas/src/vst/Verifier.v3` — name resolution and type checking
- `aeneas/src/main/Compiler.v3` — `Compilation.parse()` / `verify()`
- `aeneas/src/main/Error.v3` — `Error`, `ErrorGen`
- `apps/vctags/` — a small standalone tool built on the parser
