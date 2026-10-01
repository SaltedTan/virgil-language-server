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
- JSON-RPC 2.0 message model under `src/protocol/`: distinct request, notification, response, and error-response shapes; integer and string IDs echoed in replies; `params` absent, object, array, or null; `MethodNotFound` for unknown requests; unknown notifications ignored; standard error codes for malformed messages. Responses from the client are accepted but not yet matched to server requests.
- JSON parsing and rendering on top of Virgil's `lib/file/json`, with fixes for string escapes, carriage returns, numbers that aren't 32-bit integers, deep nesting, and invalid JSON output.
