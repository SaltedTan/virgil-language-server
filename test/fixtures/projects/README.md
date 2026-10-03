# Fixture projects

Small repositories with `.virgil-lsp.json` project files ([format](../../../docs/configuration.md)). Each directory is one repository root.

| Directory | Projects | What it exercises |
| --- | --- | --- |
| `two-programs/` | `server`, `client` | Two programs that share `shared/Greeting.v3` and both declare a top-level `Program`. Each program verifies alone, but analyzing all three files as one program reports `TypeRedefined`. |
| `virgil-lib/` | `words` | A program that uses Virgil's `lib/util` through `virgilDependencies`, which are relative to the Virgil root. It verifies only with `lib/util/*.v3`. |
