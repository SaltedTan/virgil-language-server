# 0001. Implement the server in Virgil

- Status: Accepted
- Date: 2026-10-01

## Context

Diagnostics, go-to-definition, hover, references, and rename all need the same answers the compiler computes: parsed declarations, resolved names, and inferred types. These answers are needed for unsaved editor buffers, not only for files on disk.

Aeneas, the Virgil compiler, is self-hosted. Its front end (`Parser`, `VstFile`, `Verifier`, `ErrorGen`) is written in Virgil and exposes bound syntax-tree nodes (`VarExpr.varbind`, `TypeRef.binding`, expression types) with source ranges. The language is still evolving.

## Decision

Implement the language server as a native Virgil program. It compiles the Aeneas front end into the server executable and calls it in-process. Editors launch one executable, `virgil-lsp --stdio`. The VS Code extension and Neovim configuration contain no language semantics.

## Consequences

- The server uses the compiler's real parser and type checker, so it can't drift from the language.
- Unsaved buffers can be analyzed by passing in-memory bytes to the parser. No temporary files or subprocesses are needed.
- The bound syntax tree is available for definition, hover, references, and rename.
- JSON-RPC framing, the LSP message model, and lifecycle handling must be written in Virgil. No mature Virgil LSP library exists, and Virgil's `lib/file/json` is the starting point for serialization.
- Distribution is per host platform. Native targets cover Linux and macOS on x86-64. Windows is documented through Remote–WSL until a native or JVM build is validated.
- Contributors need a working Virgil toolchain, which the pinned submodule provides ([ADR-0002](0002-pinned-virgil-adapter-boundary.md)).

## Alternatives considered

- **TypeScript or Rust server that shells out to `v3c`.** Gives diagnostics for saved files only. It can't analyze unsaved buffers without temporary-file workarounds, and it has no access to resolved bindings, so definition and hover would need a second, divergent implementation of Virgil semantics.
- **Reimplement Virgil's parser and type system in another language.** Large up-front cost and continuous drift from a changing language. Explicitly a non-goal.
- **Fork the existing prototype (`linxuanm/virgil-lsp`).** Its protocol layer has known correctness problems, such as responses not echoing request IDs. A greenfield implementation gets an architecture designed around these requirements, and the prototype remains useful prior art.
