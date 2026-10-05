# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Server and VS Code extension versions are independent; see [docs/compatibility.md](docs/compatibility.md).

## [Unreleased]

### Added

- Automatic semantic diagnostics for configured projects and unsaved buffers. See [Semantic diagnostics](docs/architecture.md#semantic-diagnostics) for scheduling, publication, freshness limits, failure recovery, and measurements.

### Fixed

- Over-budget analysis results now report that the program is too large rather than blaming a malformed worker. See ADR-0004 for the [replacement policy](docs/decisions/0004-analysis-worker-process.md#restart-policy) and [supported program size and recovery guidance](docs/decisions/0004-analysis-worker-process.md#supported-program-size).
- Parser diagnostics and document symbols now contain deep parser nesting and the remaining malformed-range traps. See [Aeneas crash paths](docs/aeneas-crash-paths.md) for the recovery behavior and containment limits.
- Document symbols no longer kill the server on expression-start `...` or `..+` (for example, while typing `xs[...]`); the outline now shares the parser driver and recovery used for parser diagnostics. See [Document symbols](docs/architecture.md#document-symbols).
- Whole-program diagnostics now retain exact UTF-8 byte ranges through the analysis worker, including tabs in block comments, trailing line-comment EOF errors, and multi-byte source text. Compiler display coordinates remain available for CLI reports.
- Whole-program analyses no longer leave verified programs rooted in Aeneas's global type cache or its superseded, statically allocated bucket arrays. Dropped snapshots return close to the pre-analysis live-heap baseline; a fresh-process regression covers repeated analyses and error paths. See [compiler constraints](docs/architecture.md#known-constraints-of-the-compiler-front-end).
- Occurrence collection reports uses only once in default match cases, lambda bodies (including nested lambdas and field initializers), and superclass arguments shared with constructors.
- Match-case names and enum parameter fields now resolve in the [occurrence index](docs/architecture.md#compiler-adapter).
- Bound aggregate open-document overlays and warn clients when admission is rejected. See [Document store](docs/architecture.md#document-store) for the supported capacity, recovery policy, and parsing headroom.
- Corrected binding queries that targeted compiler-synthesized declarations or treated inline exports as uses. See the [binding contract](docs/architecture.md#compiler-adapter).
- Occurrence collection no longer traps on a null type binding left by verification errors, including `E.set.all` on enums with more than 64 cases.
- `scripts/bench-analysis.sh` reports failed input sets, command exit statuses and stderr, rejects runs without timing samples, and cleans up temporary files on exit.

### Changed

- The development `analyze` command now uses the analysis worker, with timeout and analysis-count tuning options; see [Running](docs/development.md#running) for options and failure output. `make` also builds `build/virgil-lsp-worker`; see [Building](README.md#building) for executable placement.
- Analysis adapter caches a lazy per-file binding index and binary-searches definition queries, reuses compiler configuration across analyses, and counts the global type cache only for requested statistics. The development `analyze --bindings` command reuses its report buffer without copying each file's output.

### Added

- VS Code development client in `clients/vscode/`: the `virgil` language for `.v3` files with comment and bracket configuration and a grammar written for this project, `virgil-lsp --stdio` started from `virgil.server.path`, startup checks and server stderr in a "Virgil Language Server" output channel, a trace setting, and a clean shutdown on deactivation. It runs on the workspace side for Remote-SSH and Remote-WSL. CI type-checks it and runs its lifecycle tests on Linux; see the [setup and manual checklist](clients/vscode/README.md) and the [supported versions](docs/compatibility.md#editors).
- Bounded deterministic project source/dependency glob expansion on Linux and macOS, workspace-folder and nearest-config discovery, separate contexts for shared files, active-project selection, configuration diagnostics, and project-revision snapshot stamps. Project traversal never follows symlinks. See [Configuration](docs/configuration.md) for limits, ordering, and reload follow-ups, and [development stdio requests](docs/development.md#development-stdio-requests) for project submissions.
- Server-side document version stamps for stdio analysis snapshots, with a freshness helper and recorded versions/currentness in the development inspection replies. See the [snapshot freshness policy](docs/architecture.md#analysis-snapshots-partly-present) and [development stdio formats](docs/development.md#development-stdio-requests).
- Headless Neovim smoke test of the documented plain configuration in both CI jobs. See [Neovim testing](editors/nvim/README.md#headless-smoke-test) for coverage and local usage.
- Version 1 of the `.virgil-lsp.json` project file. [Configuration](docs/configuration.md) specifies its fields, patterns, Virgil library dependencies, accepted language options, and configuration diagnostics, and [ADR-0005](docs/decisions/0005-project-file-format.md) records the design. A parser under `src/workspace/` reports each problem with its location. Fixture projects are in `test/fixtures/projects/`.
- Asynchronous analysis workers in `--stdio` and unadvertised development requests for analysis and retained-snapshot inspection. See [development stdio requests](docs/development.md#development-stdio-requests) for request behavior and lifecycle cleanup.
- A replaceable analysis worker and [ADR-0004](docs/decisions/0004-analysis-worker-process.md), which records its process model and restart policy. See [Analysis worker](docs/architecture.md#analysis-worker) for the current integration scope.
- Single-file parser diagnostics for unsaved document overlays, with corrected comment offsets. See [Parser diagnostics](docs/architecture.md#parser-diagnostics) for publication behavior and [Compiler adapter](docs/architecture.md#compiler-adapter) for parsing and recovery details.
- Hierarchical VST-derived document symbols for open, unsaved buffers. See [Document symbols](docs/architecture.md#document-symbols) for supported declarations, ranges, and failure behavior.
- Versioned full-text document synchronization (`didOpen`, `didChange`, `didSave`, `didClose`) with exactly matching `initialize` capabilities. Stale/out-of-order changes, duplicate opens, and unsupported incremental changes are ignored and logged to stderr. In-memory overlays override disk through an injected reader, and local Linux/macOS file URIs are normalized to canonical keys. See [Document store](docs/architecture.md#document-store) for version, save, and URI identity rules. Unit and golden transcript tests cover synchronization and rejection sequences.
- Repository foundation: Apache-2.0 license, roadmap, community documents, issue and pull-request templates, and CI.
- ADR-0001 (Virgil-native server) and ADR-0002 (pinned Virgil adapter boundary).
- Pinned Virgil submodule at `9945e430` and a `make`-based build that compiles against it.
- `virgil-lsp` executable with `--version`, `--help`, and a development `parse` command that reports Aeneas syntax diagnostics.
- Unit tests for the Aeneas adapter and command-line smoke tests.
- ADR-0003: supported platforms are Linux x86-64 (including WSL 2) and macOS on Apple Silicon (Rosetta 2 for now).
- macOS CI job on an Apple Silicon runner, and macOS in the weekly Virgil-master check.
- Whole-program analysis in the Aeneas adapter: in-memory sources are parsed and verified as one program (no initialization or code generation). Syntax and semantic errors come back as `AnalysisDiagnostic`s, and a verified use can be followed to its declaration.
- Development command `analyze` (with `--bindings`, `--stats`, `--repeat=<n>`) and `scripts/bench-analysis.sh`.
- JSON-RPC 2.0 message model under `src/protocol/`: distinct request, notification, response, and error-response shapes; integer and string IDs echoed in replies; `params` absent, object, array, or null. See the [dispatch contract](docs/architecture.md#json-rpc-messages) and [lifecycle](docs/architecture.md#lifecycle) for notification handling and request errors.
- JSON parsing and rendering on top of Virgil's `lib/file/json`, with fixes for string escapes, carriage returns, numbers that aren't 32-bit integers, deep nesting, messages with more values than the heap can hold, and invalid JSON output. See [JSON limits](docs/architecture.md#json-limitations-and-workarounds) for admission rules.
- LSP base-protocol frame reader: `Content-Length` counted in bytes, input split at any point, optional `Content-Type` with a UTF-8 charset (semicolons inside quoted parameter values don't separate parameters), a configurable payload limit that skips longer messages, incremental payload buffering and release of oversized staging buffers, and a bounded header. See [framing limits and buffering guarantees](docs/architecture.md#framing). A header without a valid `Content-Length` stops the stream.
- `--stdio` transport: reads framed messages from standard input, dispatches them, and writes framed output to standard output, handling partial writes. `--max-message-bytes=<n>` sets the payload limit within the [supported memory budget](docs/architecture.md#framing). Skipped messages are logged to stderr. A malformed header, a failed write, or input that ends inside a message exits with status 1.
- Pending-request table and response matching for server-initiated requests. See [Server requests](docs/architecture.md#server-requests) for the completion contract, logging, and current limitations.
- LSP lifecycle and `$/cancelRequest` notification handling. See [Lifecycle](docs/architecture.md#lifecycle) for initialization, shutdown, cancellation, and exit behavior.
- Golden transcript tests under `test/protocol/`: recorded `--stdio` input bytes with the expected output bytes and exit status, covering the lifecycle, fragmented, consecutive, malformed, Unicode, and oversized messages, and cancellation. `make test` runs them.
- `PositionMap` under `src/documents/`: coordinate conversion for document text. See [Coordinates](docs/architecture.md#coordinates) for supported encodings, line endings, and out-of-range handling.
