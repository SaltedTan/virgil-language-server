# 0004. Run whole-program analysis in a replaceable worker process

- Status: Accepted
- Date: 2026-10-03
- Discussion: [#13](https://github.com/SaltedTan/virgil-language-server/issues/13)

## Context

The compiler spike ([#1](https://github.com/SaltedTan/virgil-language-server/issues/1), [PR #11](https://github.com/SaltedTan/virgil-language-server/pull/11)) found that whole-program analysis (`Compilation.parse()` and `verify()`) cannot run safely in the language server's process:

- **Ordinary input can kill or hang the process.** Virgil has no exception handling, so a trap inside Aeneas ends the process. `def x: i32 = 0i;` (typed on the way to `0i32`) and `enum E { A(6) }` trap with `!NullCheckException`; `class S extends S { def f() { me; } }` loops forever. [Aeneas crash paths](../aeneas-crash-paths.md) lists the known inputs.
- **Analyses leave process-wide state behind.** `UID.next` never resets, and verification of any program fails once it passes 2^29, which is about 16,500 analyses of the Aeneas sources. [#12](https://github.com/SaltedTan/virgil-language-server/issues/12) removed the type-cache retention that made the second such analysis run out of heap, but the adapter cannot reset the compiler in general.

Two properties of the Virgil runtime shape the answer:

- `System` has no way to create a process, so this needs raw system calls through `Linux.syscall` and `Darwin.syscall`.
- The heap size is fixed when an executable is compiled (`-heap-size`, 200 MB by default in `bin/v3c-*`). The semispace collector allocates through its whole from-space before each collection, so a busy process's resident memory approaches the configured heap.

## Decision

### Processes

1. **Two executables from the same sources.** `virgil-lsp` is the server. `virgil-lsp-worker` runs analyses and nothing else. Its entry point is `src/worker/WorkerMain.v3`, and it is compiled with its own heap (`WORKER_HEAP` in the `Makefile`, 384 MB; see [Measurements](#measurements)). The server keeps the default heap.
2. **The server never parses or verifies a whole program.** Of the two product executables, only the worker calls `AeneasAdapter.analyzeProgram`; adapter tests and probes still call it directly. See [Compiler adapter](../architecture.md#compiler-adapter) for the shared parser and verification driver. This supersedes the in-process whole-program analysis portion of [ADR-0001](0001-virgil-native-server.md), retaining its choice of Virgil and the compiler's semantics. Virgil compiles only reachable code, so the verifier isn't even in the server executable (before stdio integration: 306 KB, against 607 KB for the worker, on Linux). Single-file parsing (`parseFile`, document symbols) stays in the server and has its own, smaller set of [known crash paths](../aeneas-crash-paths.md). Keeping it in-process allows syntax diagnostics independently of worker availability.
3. **Starting a worker.** The server looks for `virgil-lsp-worker` in its own executable's directory. On Linux it reads `/proc/self/exe`; on macOS it calls `proc_info` with `PROC_PIDPATHINFO`, as `proc_pidpath()` does. `src/os/<target>/HostOs.v3` holds the system calls for each target, and the `Makefile` compiles the one for the host. The server creates two pipes and moves their descriptors to 10 and above, close-on-exec. It then forks and `execve`s the worker with the argument `--serve` and an empty environment. In the child, descriptor 0 is `/dev/null`, descriptors 1 and 2 are the server's standard error, and descriptors 3 and 4 carry requests and results. A worker can therefore never write to the server's standard output, which belongs to the protocol. The server checks that the file is executable before forking; a child that still cannot `execve` exits with status 127. The supervising process ignores `SIGPIPE`, so that writing to a dead worker fails with `EPIPE` instead of killing it.
4. **Handshake.** A worker starts by sending its protocol version, server version, source revision, and heap size. The server refuses a worker whose version or revision differs from its own (a stale or mismatched executable) and reports why.

### Channel and framing

Requests and results each use one pipe. A frame is a 4-byte little-endian payload length followed by the payload, at most 16 MiB (`WorkerFraming.MAX_PAYLOAD`). The first payload byte gives the message kind. Unless noted, integers are signed LEB128, and strings are a length (-1 for null) followed by the bytes. `RESULT` starts with fixed-width request ID, analysis count, and UID counter (32 bits each), then live heap bytes (64 bits), all little-endian. Its flags and declaration-kind tags are single bytes. `src/worker/AnalysisWire.v3` holds the encoder and decoder for both directions, and `AnalysisWire.PROTOCOL_VERSION` changes with the layout.

| Message | Direction | Contents |
| --- | --- | --- |
| `HELLO` | worker → server | Protocol version, server version, source revision, heap bytes |
| `ANALYZE` | server → worker | Request ID, flags (collect occurrences, collect statistics), the server's time limit, then each file's path and bytes, in program order |
| `RESULT` | worker → server | Request ID, and the worker's analysis count, UID counter, and live heap; parse/verify flags and statistics; diagnostics; a path table; each distinct declaration once; each file's occurrences as ranges and declaration indexes |

The server's pipe ends are nonblocking. The stdio event loop polls standard input and the worker pipes together, using the startup or analysis deadline as its poll timeout. Startup, including the handshake, and request transmission are asynchronous too. It therefore never waits on a worker that has stopped reading or writing. The decoder bounds every count by the bytes that remain in the message, and every index by its table. A malformed message is rejected without trapping, and the worker that sent it is killed.

### What crosses the boundary

Requests carry source bytes that the server has read: open-document overlays, or files from disk. The worker never reads a file. Results carry only server-owned snapshot data: diagnostics, declaration and occurrence indexes, and measurements. No `Program`, syntax tree, or other compiler object leaves the worker. The server decodes a result into an `AnalysisSnapshot` (`src/analysis/AnalysisSnapshot.v3`), which keeps occurrences as int arrays rather than an object per use. A snapshot answers `occurrences(path)` and `definitionAt(path, line, column)` the way `ProgramAnalysis` does, and stays valid after its worker has exited. Hover text, and any other data the server needs later, will join `RESULT` with a protocol version change when the feature that uses it lands (M4).

### Restart policy

Analyses run one at a time. A worker starts on demand, before an analysis, and is replaced (killed with `SIGKILL` and reaped) when any of these happens:

| Trigger | Limit (`AnalysisWorkerLimits`) | Outcome of that analysis |
| --- | --- | --- |
| It exits, traps, or closes its result pipe during an analysis | — | Failed: crashed |
| An analysis runs past the wall-clock limit, or a start sends no handshake in time | 10 s each | Failed: timed out, or unavailable for a start |
| It sends a malformed result payload | — | Failed: malformed |
| Its result exceeds the decoded allocation budget | 16 MiB (`AnalysisWire.MAX_RESULT_ALLOCATION`) | Failed: too large; reduce the program's source set |
| After a completed analysis, its analysis count reaches the limit | 100 | Completed |
| After a completed analysis, its UID counter reaches the limit | 2^28 | Completed |
| After a completed analysis, its live heap (after a forced collection, excluding the result) reaches the limit | ⅛ of its heap (48 MB) | Completed |

Each worker also arms a timer (`setitimer`) for twice the time limit plus a second while it analyzes, and `SIGALRM` ends it. The server's limit normally expires first. The timer matters when the supervising process dies during an analysis: an idle orphan reads the end of its request pipe and exits, but one caught in a compiler loop would otherwise run forever.

Before allocating the encoded request, the supervisor measures its aggregate size with `AnalysisWire.analyzeFrameSize`: framing, message metadata, file counts, path lengths and bytes, and source lengths and bytes. If the payload would exceed the frame limit, it returns `TOO_LARGE` without starting or replacing a worker, sending bytes, or changing the current snapshot. The existing worker can handle the next accepted request. `AnalysisSupervisor:request_too_large`, `AnalysisSupervisor:request_size_boundary`, `AnalysisSupervisor:aggregate_request_too_large`, and `AnalysisSupervisor:oversized_request_keeps_snapshot` cover this admission rule.

If no worker can be started, the analysis fails as unavailable, and the next analysis tries again. Worker startup and execution failures are logged to standard error as warnings, with the process ID and exit status when available. Invalid frame lengths or truncated frames close the result stream and are reported as crashes; malformed result payloads are reported as malformed. Exceeding the decoded allocation budget is instead reported as `TOO_LARGE`, with the budget in bytes and advice to reduce the program's source set. Decoding stops before the allocation that would exceed the budget, so the remaining payload and restart-policy state are not validated. The supervisor conservatively replaces that worker too, without calling its result malformed; replacement does not make the same oversized program fit. `AnalysisSupervisor:over_budget_result_keeps_snapshot` and `AnalysisSupervisor:malformed_result_replaces_worker` cover the distinct outcomes, snapshot preservation, and replacement. Routine replacements get an informational line. A failure never stops the supervising process. The most recent snapshot remains available for inspection, subject to the [snapshot freshness policy](../architecture.md#analysis-snapshots-partly-present), and the next analysis after a worker failure runs in a new worker. The worker inherits standard error, so its trap message and stack trace appear in the server's log.

The UID limit leaves 2^28 IDs of headroom, while one analysis of the Aeneas sources uses 32,462. The count limit is a backstop against growth that the live-heap check cannot see, such as the UID counter itself; replacing a worker costs well under a millisecond. The live-heap limit catches a retention leak long before it can overflow the heap: without #12's cleanup, one analysis of the Aeneas sources retained about 70 MB.

### First clients

`virgil-lsp analyze` and the stdio server share the supervisor state machine. The CLI drives it synchronously; stdio calls `begin`, adds `pollFds` to its stdin poll set, uses `pollTimeout` for the wait, and calls `onPoll` for progress or completion. No worker is started until an analysis is submitted.

**Option 2a is the pre-M3 trigger:** the unadvertised, development-only requests `virgil-lsp/analyze` and `virgil-lsp/snapshot` submit analysis and inspect retained server-owned results. [Development stdio requests](../development.md#development-stdio-requests) owns their unstable formats, input capture, concurrency, failure, and lifecycle behavior.

This is not automatic semantic analysis of edits: [project configuration](../configuration.md) is not integrated into analysis, and there is no version-gated publication, semantic capability, or semantic diagnostic notification yet. M3 will supply those. Interactive [protocol transcripts](../../test/protocol/worker.py) exercise a compiler crash, a hang with concurrent replies and fragmented stdin, 20 Aeneas analyses across replacements, retained snapshot reads, lifecycle cleanup, and a worker that never handshakes. Both platform CI jobs run these tests.

### Measurements

Linux x86-64, Intel Core i7-14700KF, `virgil-lsp analyze --stats --bindings --repeat=10` on the Aeneas sources and their dependencies (198 files, 80,285 lines, 2.9 MB). The server's heap was the default 200 MB in every row; only the worker's heap varied. Each analysis collected 162,929 bindings, and the result frame was 2.0 MB. The parse and verify columns are for analysis 10.

| Worker heap | Parse | Verify | Collect bindings (runs 1 / 2 / 10) | Wall time (10 runs) | Peak RSS |
| --- | ---: | ---: | --- | ---: | ---: |
| 200m | 23 ms | 140 ms | 253 / 249 / 247 ms | 7.09 s | 205 MB |
| 256m | 23 ms | 43 ms | 209 / 184 / 166 ms | 5.05 s | 247 MB |
| 384m | 23 ms | 45 ms | 32 / 28 / 21 ms | 3.54 s | 328 MB |
| 512m | 23 ms | 33 ms | 29 / 28 / 17 ms | 3.49 s | 329 MB |
| 768m | 23 ms | 44 ms | 32 / 29 / 21 ms | 3.56 s | 329 MB |

Run in process before the worker existed, the same analysis ran out of heap at 128 MB and 160 MB, so its peak live heap is between 80 and 100 MB. At 200 MB and 256 MB the collector dominates. From 384 MB up, more heap makes no measurable difference, and the resident memory a worker reaches still grows with its heap. 384 MB therefore leaves about twice the peak live heap of the largest Virgil program available. Other measurements:

- Over 300 analyses of the Aeneas sources in one worker, the live heap after each analysis stayed at 164,163 bytes, and the UID counter grew by 32,462 per analysis.
- Replacing the worker after every analysis of a two-file program cost about 0.6 ms per replacement (200 analyses: 0.14 s, against 0.02 s with one worker).

## Consequences

- A compiler trap, an endless loop, or a leak ends or slows only the worker. The supervisor reports it, keeps the last snapshot, and continues.
- Releases must preserve the [executable placement](../../README.md#building) (M7). The handshake rejects a pair from different builds.
- Each new host target needs a `src/os/<target>/HostOs.v3`.
- Every analysis copies its sources into the worker and its results back. For the Aeneas sources that is 2.9 MB and 2.0 MB, which takes a few milliseconds.
- A worker's resident memory approaches 384 MB on programs the size of the Aeneas sources. A program needing more than about 190 MB of live heap, about twice the Aeneas sources, overflows the worker heap. Every such analysis is reported as a crash until `WORKER_HEAP` is raised.
- Compact result counts can expand beyond the [frame limit](#channel-and-framing) when decoded. `AnalysisWireReader` separately admits result allocations against a 16 MiB budget: each count reserves array and record costs before allocating. Diagnostic strings reserve 32 times their length plus 64 bytes, including headroom for JSON escaping and response buffers; index strings reserve their length plus 64 bytes for snapshot storage. Null strings reserve nothing. Diagnostic record charges include both byte offsets and the conservative JSON response allowance; [`AnalysisWire.decodeResult`](../../src/worker/AnalysisWire.v3) owns those charges. Declarations reserve 128 bytes per record, path entries 64, files 256, and occurrences 20; each count also reserves 64 bytes for array headers. An over-budget result is a distinct too-large outcome, not a malformed result; the supervisor replaces the worker conservatively as described in the [restart policy](#restart-policy) and preserves the old snapshot. The [development source budget](../development.md#development-stdio-requests) and [client message limits](../architecture.md#framing) leave space for JSON parsing, source and encoded request buffers, an incoming worker frame, old and new snapshots during replacement, and the [document store](../architecture.md#document-store). The CLI uses the [request admission rule](#restart-policy).

### Supported program size

The supported envelope is determined by source/request size, decoded result size, worker heap, and analysis time, not a fixed file or line count. The Aeneas sources and dependencies (198 files, 2.9 MB of source) and Wizard's `wizeng` v3i sources (164 files, 1.3 MB) fit the result budget with occurrences. Measurements from an instrumented decoder on `main` at `c7791a1`, against the unchanged 16,777,216-byte allocation budget:

| Program | Files | Source | Bindings | Charged result allocation | Share of budget |
| --- | ---: | ---: | ---: | ---: | ---: |
| Aeneas sources and dependencies | 198 | 2.9 MB | 162,929 | 10,328,561 bytes | 62% |
| Wizard engine, `wizeng` v3i sources | 164 | 1.3 MB | 65,748 | 4,631,312 bytes | 28% |
| `vendor/virgil/lib/util/*.v3` | — | — | — | 545,369 bytes | 3% |

Users can check total source bytes with `wc -c` over the files supplied to analysis (using current overlay contents for unsaved files). The development stdio request must fit within **4 MiB (4,194,304 bytes), including paths and metadata**, so source bytes alone must be below that. The CLI instead admits the exact encoded request against the 16 MiB frame limit. Neither source limit guarantees a result will fit: declarations, occurrences, and especially diagnostics have different allocation charges. `analyze --bindings` can help check binding counts for programs that fit, but is not a result-allocation meter.

With similar binding and diagnostic density, about **1.6 times the Aeneas sources** would exhaust the result budget; the development request's source limit rejects about **1.4 times** that source set first. These are sizing guides, not guaranteed capacities for arbitrary programs. If the decoded result exceeds its budget, reduce the files analyzed together to a smaller valid program/project; retrying the unchanged program or increasing `WORKER_HEAP` does not increase the server's result budget.

### Other limits

- The time limit uses `System.ticksMs()`, which reads the wall clock (`gettimeofday`). A clock change during an analysis can shorten or lengthen the limit.
- `analyze` no longer traps when the compiler does; [Running](../development.md#running) owns its failure output and exit behavior.

## Alternatives considered

- **Keep analysis in the server with adapter workarounds only.** This is the interim option from #12. It leaves the crash, hang, and UID paths.
- **One executable that starts itself as the worker.** This is simpler to ship, but the heap is fixed per executable. The server's resident memory would grow to the worker's 384 MB instead of staying within its own 200 MB.
- **`fork` without `execve`.** This avoids finding an executable, but the child has the server's heap size and a copy of its heap, including open documents. `execve` gives each worker a fresh compiler state and its own heap.
- **Threads.** Virgil has none, and a trap would end the whole process anyway.
- **JSON over the pipe.** The server already parses JSON, but its [value limit and per-value heap costs](../architecture.md#json-limitations-and-workarounds) make it unsuitable for worker results. One analysis of the Aeneas sources already reports 162,929 bindings, each with a range and a declaration.
- **Keeping the program in a long-lived worker and answering queries there.** A crash or a replacement would lose the results with the worker. The policy above depends on the server owning its snapshot.
