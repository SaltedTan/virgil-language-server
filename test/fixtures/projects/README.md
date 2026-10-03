# Fixture projects

Small repositories with `.virgil-lsp.json` project files ([format](../../../docs/configuration.md)). Each directory is one repository root.

| Directory | Projects | What it exercises |
| --- | --- | --- |
| `two-programs/` | `server`, `client` | Two programs that share `shared/Greeting.v3` and both declare a top-level `Program`. Each program verifies alone, but analyzing all three files as one program reports `TypeRedefined`. |
| `two-file/` | `shapes` | Copies of `test/fixtures/analysis/two-file` as a project: `main.v3` uses `Shapes` and `Rect` from the tab-indented `shapes.v3`. The [semantic diagnostic transcripts](../../diagnostics/run.py) edit them in unsaved buffers. |
| `virgil-lib/` | `words` | A program that uses Virgil's `lib/util` through `virgilDependencies`, which are relative to the Virgil root. It verifies only with `lib/util/*.v3`. |

`prepare.py <build-directory>` creates additional filesystem cases under the build directory, rather than storing special files in Git: a sparse source larger than 4 MiB, a FIFO, a socket, file/directory symlinks, a symlink cycle, and projects at aggregate source-reference and path-byte limits. It also records the repository root for tests that use the checked-in fixtures, independently of executable placement. The unit-test build runs this script on both CI hosts. `ProjectExpansionTest.v3` tests enumeration, UTF-8 glob semantics, deterministic ordering, exclusions, library bases, source admission, capacity transfer during preflight, and the no-symlink policy. `LspProjectAnalysisTest.v3` uses `two-programs` to test separate snapshots and configuration changes during pending analyses.
