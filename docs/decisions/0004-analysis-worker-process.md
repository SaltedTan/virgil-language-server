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

Requests and results each use one pipe. A frame is a 4-byte little-endian payload length followed by the payload, at most 16 MiB (`WorkerFraming.MAX_PAYLOAD`). The first payload byte gives the message kind. Unless noted, integers are signed LEB128, and strings are a length (-1 for null) followed by the bytes. `RESULT` starts with fixed-width request ID, analysis count, and UID counter (32 bits each), then live heap bytes (64 bits), all little-endian. Its flags and declaration-kind tags are single bytes. `src/worker/AnalysisWire.v3` holds the encoder and decoder for both directions. Released layouts require an `AnalysisWire.PROTOCOL_VERSION` change; version 4 remained unreleased, so the type-parameter declaration kind and skipped-uses statistic were added without another version change. Version 5 adds [display text](../architecture.md#display-text) for hover: a string table that also holds declaration names, a signature per declaration, use-site types, builtin uses, and declaration sites. It also drops the path table: a path is an index into the request's paths.

| Message | Direction | Contents |
| --- | --- | --- |
| `HELLO` | worker → server | Protocol version, server version, source revision, heap bytes |
| `ANALYZE` | server → worker | Request ID, flags (collect occurrences, collect statistics), the server's time limit, the program's [language options](../configuration.md#compiler-flags) in the order given, then each file's path and bytes, in program order |
| `RESULT` | worker → server | Request ID, and the worker's analysis count, UID counter, and live heap; parse/verify flags and statistics, including the uses dropped because they are not token spans; diagnostics; with occurrences, a string table, each distinct declaration once, and each file's index (below). Diagnostic and declaration ranges hold compiler coordinates and UTF-8 byte offsets. An occurrence is a single-line token whose compiler column span equals its byte length, so it is sent as its begin line, column, and offset and that length |

A `RESULT`'s occurrence section, in protocol version 5:

A path is an index into the distinct path strings of the `ANALYZE` request, in order of first appearance. The request still sends every source, so a repeated path takes the index of its first copy. Every declaration is in one of the request's files, so the result needs no path table, and the decoder never looks a path up by text.

1. The string table: a count, then each distinct string once, never null. Declaration names, signatures, use-site types, and builtin types are indexes into it, so text that many declarations or uses share, such as a common type, crosses once.
2. The declarations: a count, then for each its kind tag (one byte), its name's string index, its path index, its range (four compiler coordinates and two byte offsets), and its signature's string index plus one, or zero for none.
3. The files: a count, then for each:
   - its path index, and the index and count of its declaration sites, a run of the declarations that are in the file, in byte order without overlaps. The encoder puts each file's sites first, in file order, so that they are a run;
   - its occurrences: a count, then for each its begin line, column, and offset, its length, and its declaration index;
   - its use-site types: a count, then for each the difference between its occurrence index and the previous one's (the first's from zero), so that they strictly increase, and a string index. An occurrence without one costs nothing;
   - its builtin uses: a count, then for each a range as an occurrence's, and its type's string index.

The decoder rejects a string index out of the table or of a null entry, an occurrence index out of the file's occurrences or not increasing, and sites that are out of the declarations, in another file, empty, unordered, or overlapping. Occurrences and builtin uses must each be in byte order without overlaps, as the snapshot's binary searches require: a positive line and column, a nonnegative offset at or after the previous use's end, a positive length, and an end offset and column that do not overflow. A builtin use must not overlap an occurrence. A file must be one of the request's, named at most once, which bounds the file count by the request, and its site run must start at or after the end of the previous file's, so that the decoder checks each declaration as a site at most once. Each file is marked when it is decoded, by index. Decoding is therefore linear in the message's size, plus sorting the request's paths once by their bytes and dropping repeats (`AnalysisSnapshotFiles`), so that the snapshot finds a file by its path with a binary search rather than by hashing. Virgil's `HashMap` stops growing at 1,024 buckets and chains paths with equal hashes, so a map from the paths of a large request took time quadratic in their count: decoding a result for 16,000 request paths took 223 ms, and 1,077 ms when their hashes were equal, but takes about 6 ms either way with the sorted index.

The server's pipe ends are nonblocking. The stdio event loop polls standard input and the worker pipes together, using the startup or analysis deadline as its poll timeout. Startup, including the handshake, and request transmission are asynchronous too. It therefore never waits on a worker that has stopped reading or writing. The decoder bounds every count by the bytes that remain in the message, and every index by its table. A malformed message is rejected without trapping, and the worker that sent it is killed.

### What crosses the boundary

Requests carry source bytes that the server has read: open-document overlays, or files from disk. The worker never reads a file. Results carry only server-owned snapshot data: diagnostics, declaration and occurrence indexes, and measurements. No `Program`, syntax tree, or other compiler object leaves the worker. The server decodes a result into an `AnalysisSnapshot` (`src/analysis/AnalysisSnapshot.v3`), which keeps occurrences as int arrays rather than an object per use. A snapshot answers `occurrences(path)` and `definitionAt(path, line, column)` the way `ProgramAnalysis` does, and `definitionAtOffset(path, offset)` by byte offset, and stays valid after its worker has exited. Protocol version 5 added the [display text](../architecture.md#display-text) that hover needs ([#105](https://github.com/SaltedTan/virgil-language-server/issues/105)), which the snapshot answers by byte offset too. Any other data the server needs later will join `RESULT` with a protocol version change when the feature that uses it lands.

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

Automatic [semantic diagnostics](../architecture.md#semantic-diagnostics) submit analyses of configured projects; they wait while the supervisor is busy instead of being rejected, and their debounce interval also bounds the poll wait. The unadvertised, development-only requests `virgil-lsp/analyze` and `virgil-lsp/snapshot` submit analysis and inspect retained server-owned results. [Development stdio requests](../development.md#development-stdio-requests) owns their triggers, unstable formats, input capture, concurrency, failure, and lifecycle behavior.

[Configuration](../configuration.md) owns the project integration status and remaining M3 work. Interactive [protocol transcripts](../../test/protocol/worker.py) exercise a compiler crash, a hang with concurrent replies and fragmented stdin, 20 Aeneas analyses across replacements, retained snapshot reads, lifecycle cleanup, and a worker that never handshakes. Both platform CI jobs run these tests.

### Measurements

Initial worker-heap measurements, before the later occurrence and display-data extensions: Linux x86-64, Intel Core i7-14700KF, `virgil-lsp analyze --stats --bindings --repeat=10` on the Aeneas sources and their dependencies (198 files, 80,285 lines, 2.9 MB). The server's heap was the default 200 MB in every row; only the worker's heap varied. Each analysis collected 162,929 bindings, and the result frame was 2.0 MB. The parse and verify columns are for analysis 10. [Supported program size](#supported-program-size) records current result sizes, and [Display text](../architecture.md#display-text) records current collection timings.

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
- Every analysis copies its sources into the worker and its results back. For the Aeneas sources that is 3.0 MB and 3.6 MB, which takes a few milliseconds.
- A worker's resident memory approaches 384 MB on programs the size of the Aeneas sources. A program needing more than about 190 MB of live heap, about twice the Aeneas sources, overflows the worker heap. Every such analysis is reported as a crash until `WORKER_HEAP` is raised.
- Compact result counts can expand beyond the [frame limit](#channel-and-framing) when decoded. `AnalysisWireReader` separately admits result allocations against a 16 MiB budget: each count reserves array and record costs before allocating. Diagnostic strings reserve 32 times their length plus 64 bytes, including headroom for JSON escaping and response buffers; index strings, the string table's entries, reserve their length plus 64 bytes for snapshot storage. Null strings reserve nothing. Diagnostic record charges include both byte offsets and the conservative JSON response allowance; [`AnalysisWire.decodeResult`](../../src/worker/AnalysisWire.v3) owns those charges. Declarations reserve 128 bytes per record (their names and signatures are in the string table), string table entries 8, files 256, occurrences 20 (four range ints and a declaration index), use-site types 8 (an occurrence index and a string index), and builtin uses 20 (four range ints and a string index); each count also reserves 64 bytes for array headers. Counts are also checked against the remaining payload at each record's minimum encoded size: one byte for a string, ten for a declaration, six for a file, five for an occurrence or a builtin use, and two for a use-site type. An over-budget result is a distinct too-large outcome, not a malformed result; the supervisor replaces the worker conservatively as described in the [restart policy](#restart-policy) and preserves the old snapshot. The [development source budget](../development.md#development-stdio-requests) and [client message limits](../architecture.md#framing) leave space for JSON parsing, source and encoded request buffers, an incoming worker frame, old and new snapshots during replacement, and the [document store](../architecture.md#document-store). The CLI uses the [request admission rule](#restart-policy).

### Supported program size

The supported envelope is determined by source/request size, decoded result size, worker heap, and analysis time, not a fixed file or line count. The Aeneas sources and dependencies (205 files, 3.0 MB of source) and Wizard's `wizeng` v3i sources (164 files, 1.3 MB) fit the result budget with occurrences. Measurements with protocol version 5, which added [display text](../architecture.md#display-text) ([#105](https://github.com/SaltedTan/virgil-language-server/issues/105)), against the unchanged 16,777,216-byte allocation budget. `analyze --stats --bindings` prints a result's charged allocation:

| Program | Files | Source | Bindings | Charged result allocation | Share of budget |
| --- | ---: | ---: | ---: | ---: | ---: |
| Aeneas sources and dependencies | 205 | 3.0 MB | 188,307 | 12,816,826 bytes | 76% |
| Wizard engine, `wizeng` v3i sources | 164 | 1.3 MB | 65,748 | 4,631,312 bytes | 28% |
| `vendor/virgil/lib/util/*.v3` | 31 | 0.2 MB | 6,904 | 733,597 bytes | 4% |

For the Aeneas sources, protocol version 5 adds 1,465,667 bytes to the 11,351,159 of version 4 (68%). Their result has 42,011 declarations, 4,161 more, since a declaration that nothing uses has a site; 41,965 declaration sites; 10,485 use-site types; 9,146 builtin uses; and a string table of 34,791 distinct strings, 809,430 bytes. Names cross once in the table, rather than once per declaration, which pays for most of the added declarations. Its payload grows from 2,544,967 to 3,550,981 bytes. `lib/util` had 584,522 bytes with version 4. A retained snapshot is charged its result allocation plus its paths and provenance; that of the Aeneas sources is charged 12,863,116 bytes, 46,290 more than its allocation, as with version 4. Admission reserves a full result budget beside the snapshot it replaces, so a snapshot has half of the 32 MiB [snapshot budget](../architecture.md#analysis-snapshots-partly-present); this one uses 77% of that half. The Wizard sources were not measured again; their row is their protocol 3 measurement at `c7791a1`. `OccurrenceCoverage:aeneas_sources` requires the Aeneas sources' result allocation and retained snapshot charge each to stay within 80% of the 16 MiB result budget.

Users can check total source bytes with `wc -c` over the files supplied to analysis (using current overlay contents for unsaved files). Source bytes alone must be below the [development stdio request's admission limit](../development.md#development-stdio-requests), which also includes paths and metadata. The CLI uses the [encoded-request admission rule](#restart-policy). Neither source limit guarantees a result will fit: declarations, occurrences, and especially diagnostics have different allocation charges. For a program that fits, `analyze --stats --bindings` reports its binding counts and the result allocation charged against the budget.

With similar binding and diagnostic density, about **1.3 times the Aeneas sources** would exhaust the result budget, before the development request's source limit rejects about **1.4 times** that source set. These are sizing guides, not guaranteed capacities for arbitrary programs. If the decoded result exceeds its budget, reduce the files analyzed together to a smaller valid program/project; retrying the unchanged program or increasing `WORKER_HEAP` does not increase the server's result budget.

The remaining headroom is accepted for now. Adding occurrence kinds or display data must re-measure decoded result allocation and [binding collection time](../architecture.md#compiler-adapter). If it runs short, the snapshot could keep declarations in int arrays rather than an object each, as it keeps occurrences, at the cost of allocating a declaration for each query.

### Other limits

- The time limit uses `System.ticksMs()`, which reads the wall clock (`gettimeofday`). A clock change during an analysis can shorten or lengthen the limit.
- `analyze` no longer traps when the compiler does; [Running](../development.md#running) owns its failure output and exit behavior.

## Alternatives considered

- **Keep analysis in the server with adapter workarounds only.** This is the interim option from #12. It leaves the crash, hang, and UID paths.
- **One executable that starts itself as the worker.** This is simpler to ship, but the heap is fixed per executable. The server's resident memory would grow to the worker's 384 MB instead of staying within its own 200 MB.
- **`fork` without `execve`.** This avoids finding an executable, but the child has the server's heap size and a copy of its heap, including open documents. `execve` gives each worker a fresh compiler state and its own heap.
- **Threads.** Virgil has none, and a trap would end the whole process anyway.
- **JSON over the pipe.** The server already parses JSON, but its [value limit and per-value heap costs](../architecture.md#json-limitations-and-workarounds) make it unsuitable for worker results. The [supported program size](#supported-program-size) measurements show the binding counts that results must carry, each with a range and a declaration.
- **Keeping the program in a long-lived worker and answering queries there.** A crash or a replacement would lose the results with the worker. The policy above depends on the server owning its snapshot.
