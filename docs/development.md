# Development guide

## Prerequisites

- Linux x86-64, or an Apple Silicon Mac with Rosetta 2 (`softwareupdate --install-rosetta --agree-to-license`). WSL 2 on Windows is best effort.
- `git`, `make`, `bash`; Python 3.9+ for project-fixture preparation and cleanup, interactive protocol tests, and editor tests
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
comparisons.

## Running

```sh
build/virgil-lsp --version
build/virgil-lsp parse path/to/file.v3    # development command: syntax diagnostics
build/virgil-lsp analyze a.v3 b.v3        # development command: parse and verify as one program
build/virgil-lsp repair --cursor=a.v3:120 a.v3 b.v3  # development command: the completion repair spike
build/virgil-lsp --stdio                  # LSP over stdio
```

`analyze` runs the compiler spike: the files are read, then parsed and verified together in an [analysis worker](architecture.md#analysis-worker) process, and semantic diagnostics are printed. `--bindings` also prints each indexed use and its source declaration according to the [binding contract](architecture.md#compiler-adapter). `--display` also prints, in byte order, each declaration site with its signature, each indexed use with its declaration's signature and any use-site type, and each builtin use with its type, as the [display text](architecture.md#display-text) defines them, and each [call site](architecture.md#call-sites). A call is shown by byte offset, from its opening parenthesis to just past its closing one, as `test/fixtures/analysis/call-sites/main.v3:@1457-1463 call METHOD add: def add(a: int, b: int) -> int; parameters [a: int] [b: int]; arguments @1458-1459, @1461-1462` for `add(1, 2)`, with its callee's kind and name, or `builtin`, if it has one, and its signature, each parameter in brackets, and its arguments' spans. `--stats` prints timing and compiler global-state counters, as measured in the worker, and the result's charged decoded allocation to stderr; ADR-0004 owns the [result budget](decisions/0004-analysis-worker-process.md#supported-program-size). With `--bindings` or `--display`, the statistics also include the binding count, collection time, and skipped-use total defined by the [binding contract](architecture.md#compiler-adapter). `--repeat=<n>` runs the analysis n times and fails if any run differs; workers are replaced between runs as the [restart policy](decisions/0004-analysis-worker-process.md#restart-policy) requires. `--worker-timeout-ms=<n>` and `--worker-max-analyses=<n>` set the policy's time and analysis-count limits, for testing; like `--repeat`, they accept positive decimal values of at most nine digits and can raise or lower the defaults. If the worker crashes or times out, its stack trace (if any) and the supervisor's report go to stderr, and the command exits with status 1. An oversized request also exits with status 1; the worker protocol's [request admission rule](decisions/0004-analysis-worker-process.md#restart-policy) includes paths and encoding overhead, not just source bytes. An over-budget result exits with status 1 and reports that the program is too large. ADR-0004 owns the [decoded allocation budget](decisions/0004-analysis-worker-process.md#consequences) and [supported program size and recovery guidance](decisions/0004-analysis-worker-process.md#supported-program-size). For example:

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
- Only one development analysis may be outstanding. A second gets `InvalidParams` rather than being queued. A request that arrives while an automatic or [temporary analysis](architecture.md#names) runs gets `InternalError` from the supervisor's busy rejection. Other requests keep receiving replies while startup, pipe transfer, or analysis is pending.
- Completion returns `{"generation": 1, "diagnostics": [...], "documentVersions": [...]}` with the original request ID. Generations increase only for completed analyses, including analyses reporting syntax/type errors and temporary analyses, so inspected generations can have gaps. Each diagnostic has `path` (or null), `beginLine`, `beginColumn`, `endLine`, `endColumn`, `kind` (or null), and `message`. These are **one-based compiler coordinates**, not LSP UTF-16 ranges; they have the same limitations as the unstable CLI's reports.
- A crashed, timed-out, unavailable, or malformed worker returns `InternalError` (-32603). An over-budget result also returns `InternalError`, with a distinct too-large message. The message states the failure and that the previous snapshot is kept; stderr records the failure. ADR-0004 owns the [replacement policy](decisions/0004-analysis-worker-process.md#restart-policy), including its rationale for over-budget results, and [size limits and recovery guidance](decisions/0004-analysis-worker-process.md#supported-program-size).
- Shutdown cancels pending analysis with `RequestCancelled` (-32800) before replying to `shutdown`; exit and EOF cancel and reap any worker. All preserve the previous snapshot.

Alternatively, submit the project for a document with `{"uri":"file:///absolute/shared.v3"}`. Its deterministic active context is selected. `uri` and `uris` are mutually exclusive. This expands and preflights the entire project before reading sources; overlays take precedence, and project disk reads reject symlinks. A document without a project or an empty expanded program gets `InvalidParams`. The project's [`compilerArgs`](configuration.md#compiler-flags) apply to its analysis; explicit `uris` use the default language options.

Project results add `"projectConfiguration":{"uri":"file:///absolute/.virgil-lsp.json","revision":1,"name":"server","current":true}`. The [analysis snapshot policy](architecture.md#analysis-snapshots-partly-present) owns context retention, its aggregate budget and eviction rules, and configuration-revision invalidation. A project or explicit-URI submission that cannot fit within that retention budget returns `InvalidParams`. An invalidated configuration revision makes the stamp and every source's `current` false. Stamps are server-owned and are not added to the worker protocol.

`virgil-lsp/snapshot` (no params required) returns the same response shape for the last completed automatic or development analysis, or `null` if none exists; [temporary analyses](architecture.md#names) leave it unchanged. It refreshes known configurations but reads analysis results only from server-owned data, so it works during a hang, after a crash, and across routine replacements. Each `documentVersions` entry, in submission order, has the source's canonical `uri`, its version (an integer, including zero or negative values), or `null` for a disk input, and a `current` boolean computed against the document store at the time of the reply. An open source's version is the live overlay version while it retains the analyzed identity, so it includes the version a version-only change accepted, and otherwise the version captured with the analysis. For example:

```json
{"generation":1,"diagnostics":[],"documentVersions":[{"uri":"file:///absolute/a.v3","version":3,"current":false},{"uri":"file:///absolute/b.v3","version":null,"current":true}]}
```

The [analysis snapshot freshness policy](architecture.md#analysis-snapshots-partly-present) defines `current`, including overlay invalidation and the assumptions for disk inputs, and configuration-revision invalidation. This remains deliberately stale inspection data: no semantic results are published or discarded by this request.

`--stdio` accepts the same unstable `--worker-timeout-ms=<n>` and `--worker-max-analyses=<n>` policy overrides as the CLI `analyze` command. They also apply to automatic analyses. See the [restart policy](decisions/0004-analysis-worker-process.md#restart-policy) for the defaults and [semantic diagnostics](architecture.md#semantic-diagnostics) for automatic scheduling triggers.

## Tests

| Location | What | How it runs |
| --- | --- | --- |
| `test/analysis/RetainProbe.v3` | Fresh-process live-heap regression for dropped analysis snapshots, including errors | `build/retain-probe [source.v3 ...]` (no arguments runs generated fixtures; see [measurements](architecture.md#adapter-retention-workaround-and-regression-probe)) |
| `test/analysis/TypeDepthProbe.v3` | Analysis and subsequent tiny analysis of 100,000 inferred array and tuple levels, including lazy snapshot bindings | `build/type-depth-probe` (1 GB heap, default stack) |
| `test/unit/` | Virgil unit tests using Virgil's `lib/test` (`UnitTests.register`). `AnalysisSupervisor:*` starts `virgil-lsp-worker` from the same directory for compiler crash and hang cases; the crash traces on stderr are expected. Result-budget and malformed-result cases use the unit-test executable as a synthetic worker on the same pipes. | `make test-unit` (with `ARGS='glob'` for a subset) |
| `test/fixtures/projects/test_cleanup.py` | Fixture permission restoration after passing and failing runs, including through the Makefile's `run-unit-tests` recipe with a stand-in executable, and removal through `make clean`, independent of the build directory | `python3 test/fixtures/projects/test_cleanup.py` |
| `test/e2e/test_coverage.py` | Consistency of the editor tests' coverage manifest and result schema, described in [Editor test results](../test/e2e/RESULTS.md) | `python3 test/e2e/test_coverage.py` |
| `test/e2e/test_editor_results.py` | The result stream validator, `scripts/editor-results.py`, on streams of each editor suite built from the coverage manifest, each breaking one of the [stream's rules](../test/e2e/RESULTS.md#result-events) | `python3 test/e2e/test_editor_results.py` |
| `test/e2e/test_editor_results_render.py` | The results renderer, `scripts/editor-results.py render`, on streams built as `test_editor_results.py` builds them: each cell's wording, the selection's and the markers' errors, the bytes outside the markers, determinism, and `--check`; and that the [rendering example](../test/e2e/results-example/README.md) is up to date | `python3 test/e2e/test_editor_results_render.py`, or with `--write-example` to regenerate the example |
| `test/cli/run.sh` | Command-line behaviour, exit codes, stdout cleanliness, worker crash, hang, and replacement, 20 analyses of the Aeneas sources, the `repair` command's report on a few corpus cases, benchmark driver regressions (also runnable with `bash test/cli/bench-analysis.sh`) | `test/cli/run.sh build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/benchmarks/` | Benchmark corpus pins, rejection checks, and the small LSP benchmark smoke case | `python3 test/benchmarks/test_client.py` and `python3 scripts/bench-lsp.py --smoke --exe build/virgil-lsp --output /dev/null`; also in `make test` |
| `test/protocol/worker.py` | Interactive transcripts: concurrent replies during startup/hangs, compiler crash, retained snapshots, 20 Aeneas analyses using the default time limit with an elapsed-time summary, separate short-limit hang sessions, lifecycle cleanup, and large JSON alongside analysis allocations and snapshot replacement | `python3 test/protocol/worker.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/coordinates.py` | Exact worker diagnostic, declaration, and occurrence byte ranges, compiler-coordinate binding exports, CLI and stdio compatibility, allocation rejection/recovery, and bounded malformed-string replay | `python3 test/protocol/coordinates.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/fingerprints.py` | Snapshot freshness against disk changes made while the worker is paused mid-analysis: same-length, shorter, longer, and deleted files, file and directory symlinks, an unrelated source edited, recovery, and overlay identity | `python3 test/protocol/fingerprints.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/bundled_library.py` | Bundled-library discovery, client override precedence, root errors, and diagnostics, hover, and definition into bundled sources | `python3 test/protocol/bundled_library.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/client_initialization.py` | Replay of the initialization exchanges that VS Code 1.142.0 and Neovim 0.11.6 and 0.12.4 sent, from `test/protocol/clients/`: the normalized `initialize`, `initialized`, and the client's reply to `client/registerCapability`, then a file's `didOpen`, its document symbols, `shutdown`, and `exit`. The [protocol test README](../test/protocol/README.md#capturing-a-new-clients-initialization-messages) covers capturing a new client | `python3 test/protocol/client_initialization.py build/virgil-lsp` |
| `test/protocol/` | Golden transcripts: `--stdio` input bytes, expected output bytes and exit status | `test/protocol/run.sh build/virgil-lsp` (runs every case) |
| `test/diagnostics/` | Golden [semantic diagnostic](architecture.md#semantic-diagnostics) transcripts that interleave input with the expected messages, using the project fixtures. The [runner's header](../test/diagnostics/run.py) owns the transcript format. | `python3 test/diagnostics/run.py build/virgil-lsp [case ...]` (with `virgil-lsp-worker` beside it) |
| `test/release/run.sh` | Tag, `VERSION`, and `CHANGELOG.md` checks in `scripts/check-release.sh`, which the [release workflow](releasing.md) runs for every tag; the VSIX checks in `scripts/check-vsix.py`; the archive and VSIX listings and changelog-link rewriting in `scripts/release-notes.py`, on constructed release assets and a fixture changelog; and bundled-library integrity and archive reproducibility in `test/release/test_library.py` | `test/release/run.sh`, or `test/release/run.sh --archive <archive>` to include the [archive installation smoke test](releasing.md#how-the-workflow-works) |
| `test/e2e/nvim/` | Headless plain Neovim configuration | See [Neovim testing](../editors/nvim/README.md#headless-smoke-test) for coverage, commands, and skip policy |
| `test/e2e/lazyvim/` | The LazyVim configuration under the pinned LazyVim starter and plugins, through LazyVim's own keys in a terminal UI in tmux, against a release archive or a build | `make test-lazyvim`, or `test/e2e/lazyvim/run.sh --archive <archive>`; not run by `make test`. CI job `lazyvim-linux` runs it on Linux, not yet as a required check, and the weekly editor tests workflow on Linux and macOS. See the [LazyVim test](../test/e2e/lazyvim/README.md) for coverage, requirements, outputs, and pins |
| `clients/vscode/` | Type-check, lifecycle regression tests with a simulated extension host, and the regression tests of the Extension Host and Remote-SSH test runners, run by CI job `vscode-client` on Linux, which also packages the VSIX and uploads it as an artifact ([coverage](manual-testing.md#what-the-automated-tests-cover)), and a [manual checklist](../clients/vscode/README.md#manual-checklist) | See [client development commands](../clients/vscode/README.md#development); not run by `make test` |
| `clients/vscode/test/e2e/` | The VS Code client in a real Extension Host, locally and through Remote-SSH | See the [Extension Host test](../clients/vscode/README.md#extension-host-test) and [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test) for coverage, commands, and requirements, and [automated editor coverage](manual-testing.md#what-the-automated-tests-cover) for CI platforms; not run by `make test` |
| `test/fixtures/` | Source files used by tests. Bytes are preserved exactly (`-text` in `.gitattributes`). `test/fixtures/projects/` holds whole repositories with project files. | — |

The [manual test matrix](manual-testing.md) covers the editor and platform combinations that these tests don't. [Known limitations](known-limitations.md) lists what the alpha doesn't do yet.

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

Run a subset with `make test-unit ARGS='Thing:*'`. Space-separated patterns are passed without shell pathname expansion, so `make test-unit ARGS='*'` runs every test. `make test` always runs the full unit suite, ignoring `ARGS`. `make build/unit-tests` also prepares host-filesystem fixtures under `build/project-fixtures` (sparse oversized source, symlink cycle, linked file/directory, and FIFO). `ProjectExpansion:*`, `WorkspaceProjects:*`, and `LspProjectAnalysis:*` run on both Linux and macOS CI; they reuse the version-1 project fixtures, including the file shared by two programs. Use `make test` or `make test-unit` for the full unit suite: a bare `build/unit-tests` run fails the two `ProjectExpansion` cases that require unreadable directories; see the [fixture permission lifecycle](../test/fixtures/projects/README.md).

## Learning the compiler front end

The [roadmap's learning path](../ROADMAP.md#learning-path-for-the-compiler-front-end) lists which parts of Aeneas to read, and in what order. Useful entry points in `vendor/virgil`:

- `aeneas/src/vst/Parser.v3` — `Parser.parseFile`, `parseToplevelDecl`
- `aeneas/src/vst/Vst.v3` — syntax tree, `VarBinding`, visitors
- `aeneas/src/vst/Verifier.v3` — name resolution and type checking
- `aeneas/src/main/Compiler.v3` — `Compilation.parse()` / `verify()`
- `aeneas/src/main/Error.v3` — `Error`, `ErrorGen`
- `apps/vctags/` — a small standalone tool built on the parser
