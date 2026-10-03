# Fixture projects

Small repositories with `.virgil-lsp.json` project files ([format](../../../docs/configuration.md)). Each directory is one repository root.

| Directory | Projects | What it exercises |
| --- | --- | --- |
| `two-programs/` | `server`, `client` | Two programs that share `shared/Greeting.v3` and both declare a top-level `Program`. Each program verifies alone, but analyzing all three files as one program reports `TypeRedefined`. |
| `virgil-lib/` | `words` | A program that uses Virgil's `lib/util` through `virgilDependencies`, which are relative to the Virgil root. It verifies only with `lib/util/*.v3`. |

`prepare.py <build-directory>` creates additional filesystem cases under the build directory, rather than storing special files in Git: a sparse source larger than 4 MiB, a FIFO, file/directory symlinks, and a symlink cycle. The unit-test build runs this script on both CI hosts. `ProjectExpansionTest.v3` tests enumeration, UTF-8 glob semantics, deterministic ordering, exclusions, library bases, source admission, and the no-symlink policy. `LspProjectAnalysisTest.v3` uses `two-programs` to test separate snapshots and configuration changes during pending analyses.
