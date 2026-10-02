# 0002. Pin Virgil and isolate it behind an adapter

- Status: Accepted
- Date: 2026-10-01

## Context

The server depends on Aeneas internals (`Parser`, `VstFile`, `Compilation`, `Verifier`, `ErrorGen`, `FileRange`). These are not a stable public API. They change whenever the compiler changes. The upstream repository is independently governed, and this project's release cycle must not depend on upstream accepting changes. Upstream ideas such as `doc/ideas/AeneasQueryMode.txt` are useful design input but not a contract.

Virgil compiles whole programs from a flat list of source files into one global namespace. Server types can therefore collide with compiler types. For example, a server-side `ParseResult` class collided with Aeneas's `ParseResult<T>` during the initial build.

## Decision

1. **Pin an exact revision.** Virgil is a git submodule at `vendor/virgil`, checked out at a specific commit. The build uses that checkout's sources and its prebuilt `bin/stable` compiler. It fails with an actionable message if the submodule is missing. The revision is recorded in `THIRD_PARTY_NOTICES.md` and `docs/compatibility.md`, and printed by `virgil-lsp --version`.
2. **One adapter boundary.** Only code under `src/analysis/` may reference Aeneas types. The adapter returns server-owned types (for example `AnalysisDiagnostic` and `FileParseResult`) to the rest of the server. See [Coordinates](../architecture.md#coordinates) for the position contract and conversion to LSP positions.
3. **Front end only.** The adapter runs parsing and verification only. It never calls `Compilation.compile()`, program initialization, reachability, or code generation, and it never executes user code.
4. **Explicit upgrades.** Moving the pin is a dedicated pull request labelled `dependency/virgil`. Required CI runs against the pinned revision. A scheduled, advisory CI job builds against current upstream `master` to detect breakage early.
5. **Avoid namespace collisions.** Server type names must not collide with top-level names in the Virgil sources compiled into the server. Prefer specific names (for example `FileParseResult`, `LspMessage`) over generic ones.

## Consequences

- Compiler API changes are confined to `src/analysis/`, which keeps upgrades reviewable.
- The server is always built and tested against exactly one known compiler revision. Users can report bugs against that revision.
- The adapter is the natural place for a future upstream query API. If Aeneas adopts one, only the adapter changes.
- Upstream fixes reach users only after a pin update, so pin updates should happen regularly. The advisory CI job signals when one is due.
- Building from the prebuilt stable compiler avoids bootstrapping Aeneas. If the pinned sources ever require a newer compiler than `bin/stable`, the build can use `VIRGIL_V3C` to point at a bootstrapped compiler.

## Alternatives considered

- **Track upstream `master` directly.** Every upstream commit could break the build. Releases would be unreproducible.
- **Copy the needed Aeneas sources into this repository.** Creates a fork that must be merged by hand and makes license tracking harder.
- **Call compiler internals from anywhere.** Faster at first, but every upstream change would spread across the codebase.
