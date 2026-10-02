# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Server and VS Code extension versions are independent; see [docs/compatibility.md](docs/compatibility.md).

## [Unreleased]

### Added

- Repository foundation: Apache-2.0 license, roadmap, community documents, issue and pull-request templates, and CI.
- ADR-0001 (Virgil-native server) and ADR-0002 (pinned Virgil adapter boundary).
- Pinned Virgil submodule at `9945e430` and a `make`-based build that compiles against it.
- `virgil-lsp` executable with `--version`, `--help`, and a development `parse` command that reports Aeneas syntax diagnostics.
- Unit tests for the Aeneas adapter and command-line smoke tests.
- ADR-0003: supported platforms are Linux x86-64 (including WSL 2) and macOS on Apple Silicon (Rosetta 2 for now).
- macOS CI job on an Apple Silicon runner, and macOS in the weekly Virgil-master check.
- Whole-program analysis in the Aeneas adapter: in-memory sources are parsed and verified as one program (no initialization or code generation). Syntax and semantic errors come back as `AnalysisDiagnostic`s, and a verified use can be followed to its declaration.
- Development command `analyze` (with `--bindings`, `--stats`, `--repeat=<n>`) and `scripts/bench-analysis.sh`.
- JSON-RPC 2.0 message model under `src/protocol/`: distinct request, notification, response, and error-response shapes; integer and string IDs echoed in replies; `params` absent, object, array, or null; `MethodNotFound` for unknown requests; unknown notifications ignored; standard error codes for malformed messages.
- JSON parsing and rendering on top of Virgil's `lib/file/json`, with fixes for string escapes, carriage returns, numbers that aren't 32-bit integers, deep nesting, messages with more values than the heap can hold (more than 500,000 are rejected with `ParseError`), and invalid JSON output.
- LSP base-protocol frame reader: `Content-Length` counted in bytes, input split at any point, optional `Content-Type` with a UTF-8 charset (semicolons inside quoted parameter values don't separate parameters), a configurable payload limit (16 MiB by default) that skips longer messages without keeping them in memory, whatever the chunk size, incremental payload buffering ([framing details](docs/architecture.md#framing)), and an 8 KiB header limit. A header without a valid `Content-Length` stops the stream.
- `--stdio` transport: reads framed messages from standard input, dispatches them, and writes framed replies to standard output, handling partial writes. `--max-message-bytes=<n>` sets the payload limit within the [supported memory budget](docs/architecture.md#framing). Skipped messages are logged to stderr. A malformed header, a failed write, or input that ends inside a message exits with status 1. No LSP methods are handled yet, so every request gets `MethodNotFound`.
- Pending-request table and response matching for server-initiated requests. See [Server requests](docs/architecture.md#server-requests) for the completion contract, logging, and current limitations.
