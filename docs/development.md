# Development guide

## Prerequisites

- Linux x86-64 (including WSL 2), or an Apple Silicon Mac with Rosetta 2 (`softwareupdate --install-rosetta --agree-to-license`)
- `git`, `make`, `bash`; Python 3.9+ for interactive protocol tests
- No separate Virgil install: the build uses the pinned submodule at `vendor/virgil`.

## Build and test

```sh
git submodule update --init --recursive   # if you cloned without --recurse-submodules
make            # builds build/virgil-lsp and build/virgil-lsp-worker
make test       # builds and runs all suites listed below
make clean
```

Each executable is a single Aeneas invocation (see `Makefile` and [architecture.md](architecture.md#build-model)); follow the [README's executable placement guidance](../README.md#building). `scripts/v3c.sh` selects the host target and the pinned `bin/stable` compiler, and `scripts/v3c.sh -print-target` prints the target it would use. It accepts these overrides:

| Variable | Purpose |
| --- | --- |
| `VIRGIL` | Use a different Virgil checkout, e.g. `make VIRGIL=~/src/virgil` |
| `VIRGIL_V3C` | Use a specific Aeneas binary, e.g. a bootstrapped `bin/current/x86-64-linux/Aeneas` |
| `V3C_TARGET` | Force a target such as `x86-64-darwin`. The `Makefile` also uses it to choose `src/os/<target>/`. |
| `WORKER_HEAP` | `Makefile` only: the analysis worker's heap, e.g. `make WORKER_HEAP=1g` (see [ADR-0004](decisions/0004-analysis-worker-process.md#measurements) for the default and its measurements) |

## Running

```sh
build/virgil-lsp --version
build/virgil-lsp parse path/to/file.v3    # development command: syntax diagnostics
build/virgil-lsp analyze a.v3 b.v3        # development command: parse and verify as one program
build/virgil-lsp --stdio                  # LSP over stdio
```

`analyze` runs the compiler spike: the files are read, then parsed and verified together in an [analysis worker](architecture.md#analysis-worker) process, and semantic diagnostics are printed. `--bindings` also prints each indexed use and its source declaration according to the [binding contract](architecture.md#compiler-adapter). `--stats` prints timing and compiler global-state counters, as measured in the worker, to stderr. `--repeat=<n>` runs the analysis n times and fails if any run differs; workers are replaced between runs as the [restart policy](decisions/0004-analysis-worker-process.md#restart-policy) requires. `--worker-timeout-ms=<n>` and `--worker-max-analyses=<n>` set the policy's time and analysis-count limits, for testing; like `--repeat`, they accept positive decimal values of at most nine digits and can raise or lower the defaults. If the worker crashes or times out, its stack trace (if any) and the supervisor's report go to stderr, and the command exits with status 1. An oversized request also exits with status 1; the worker protocol's [request admission rule](decisions/0004-analysis-worker-process.md#restart-policy) includes paths and encoding overhead, not just source bytes. For example:

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

### Development stdio requests

Before M3's project model, **option 2a** provides an explicit whole-program trigger over `--stdio`. These requests are unstable development tools, not advertised capabilities. Neither publishes semantic diagnostics nor changes the automatic syntax-diagnostic behavior.

After `initialize`, send `virgil-lsp/analyze` with a nonempty `uris` array:

```json
{"jsonrpc":"2.0","id":"analysis-1","method":"virgil-lsp/analyze","params":{"uris":["file:///absolute/a.v3","file:///absolute/b.v3"]}}
```

- Each URI uses the [document store's identity rules](architecture.md#uri-identity-on-linux-and-macos). Duplicates after normalization are rejected. Current open overlays win, including empty and non-file overlays; otherwise a local file is read from disk. The source bytes are captured at submission; later edits do not alter that analysis.
- The sources form one program in the given order. Missing/unreadable/over-budget sources and malformed parameters return `InvalidParams` (-32602). Disk reads are bounded before allocation; aggregate paths, source bytes, and a conservative metadata allowance must fit within 4 MiB, followed by the supervisor's exact frame-size check. The [client message limits](architecture.md#framing) leave room for analysis allocations and retained snapshots.
- Only one analysis may be outstanding. A second gets `InvalidParams` rather than being queued. Other requests keep receiving replies while startup, pipe transfer, or analysis is pending.
- Completion returns `{"generation": 1, "diagnostics": [...]}` with the original request ID. Generations increase only for completed analyses, including analyses reporting syntax/type errors. Each diagnostic has `path` (or null), `beginLine`, `beginColumn`, `endLine`, `endColumn`, `kind` (or null), and `message`. These are **one-based compiler coordinates**, not LSP UTF-16 ranges; they have the same limitations as the unstable CLI's reports.
- A crashed, timed-out, unavailable, or malformed worker returns `InternalError` (-32603). Worker results exceeding the [decoded allocation budget](decisions/0004-analysis-worker-process.md#consequences) are treated as malformed before exceeding that budget. The message states the failure and that the previous snapshot is kept; stderr records the process failure. The next analysis starts a new worker.
- Shutdown cancels pending analysis with `RequestCancelled` (-32800) before replying to `shutdown`; exit and EOF cancel and reap any worker. All preserve the previous snapshot.

`virgil-lsp/snapshot` (no params required) returns the same generation-and-diagnostics object for the last completed analysis, or `null` if none exists. It reads only server-owned data, so it works during a hang, after a crash, and across routine replacements. This is deliberately stale inspection data, not a promise that semantic results match the current overlays; document-version gates remain M3 work.

`--stdio` accepts the same unstable `--worker-timeout-ms=<n>` and `--worker-max-analyses=<n>` policy overrides as the CLI `analyze` command. The default limits remain 10 seconds per analysis and 100 analyses per worker. No worker starts for ordinary syntax-only sessions.

## Tests

| Location | What | How it runs |
| --- | --- | --- |
| `test/analysis/RetainProbe.v3` | Fresh-process live-heap regression for dropped analysis snapshots, including errors | `build/retain-probe [source.v3 ...]` (no arguments runs generated fixtures; see [measurements](architecture.md#adapter-retention-workaround-and-regression-probe)) |
| `test/analysis/TypeDepthProbe.v3` | Analysis and subsequent tiny analysis of 100,000 inferred array and tuple levels, including lazy snapshot bindings | `build/type-depth-probe` (1 GB heap, default stack) |
| `test/unit/` | Virgil unit tests using Virgil's `lib/test` (`UnitTests.register`). `AnalysisSupervisor:*` starts `virgil-lsp-worker` from the same directory and makes it crash and hang; the crash traces on stderr are expected. | `build/unit-tests [glob]` |
| `test/cli/run.sh` | Command-line behaviour, exit codes, stdout cleanliness, worker crash, hang, and replacement, 20 analyses of the Aeneas sources, benchmark driver regressions (also runnable with `bash test/cli/bench-analysis.sh`) | `test/cli/run.sh build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/worker.py` | Interactive transcripts: concurrent replies during startup/hangs, compiler crash, retained snapshots, 20 Aeneas analyses, lifecycle cleanup, and large JSON alongside analysis allocations and snapshot replacement | `python3 test/protocol/worker.py build/virgil-lsp` (with `virgil-lsp-worker` beside it) |
| `test/protocol/` | Golden transcripts: `--stdio` input bytes, expected output bytes and exit status | `test/protocol/run.sh build/virgil-lsp` (runs every case) |
| `test/fixtures/` | Source files used by tests. Bytes are preserved exactly (`-text` in `.gitattributes`). | — |

The [header of `test/protocol/run.sh`](../test/protocol/run.sh) owns the transcript format, including fragmented input and version placeholders. The files are raw bytes with CR LF header lines (`-text` in `.gitattributes`), and `Content-Length` must count the payload's bytes exactly, so write them with a tool rather than an editor, for example:

```sh
frame() { printf 'Content-Length: %d\r\n\r\n%s' $(( $(printf %s "$1" | wc -c) )) "$1"; }
frame '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' > input
```

Planned additions: `test/features/` and `test/e2e/` (headless Neovim and the VS Code Extension Host).

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

Run a subset with `build/unit-tests 'Thing:*'`.

## Learning the compiler front end

The [roadmap's learning path](../ROADMAP.md#learning-path-for-the-compiler-front-end) lists which parts of Aeneas to read, and in what order. Useful entry points in `vendor/virgil`:

- `aeneas/src/vst/Parser.v3` — `Parser.parseFile`, `parseToplevelDecl`
- `aeneas/src/vst/Vst.v3` — syntax tree, `VarBinding`, visitors
- `aeneas/src/vst/Verifier.v3` — name resolution and type checking
- `aeneas/src/main/Compiler.v3` — `Compilation.parse()` / `verify()`
- `aeneas/src/main/Error.v3` — `Error`, `ErrorGen`
- `apps/vctags/` — a small standalone tool built on the parser
