# Helix

> **Best effort.** This configuration is outside the [supported editor matrix](../../docs/compatibility.md#editors). It registers the released server with Helix without a Tree-sitter grammar, so there is no syntax highlighting and no text objects for Virgil. It is not tested in CI.

Requires Helix and the [installed server on PATH](../../README.md#editor-setup). Keep `virgil-lsp-worker` and the `virgil/` library beside `virgil-lsp`, as a [release archive lays them out](../../docs/releasing.md#what-a-release-contains): the server starts the worker from the directory of its own resolved path, and finds the bundled library there ([a worker missing beside the server](../../docs/troubleshooting.md#a-worker-missing-beside-the-server)).

## Installation

Merge the two tables of [`languages.toml`](languages.toml) into your existing `languages.toml` without overwriting it: the `[[language]]` array entry and the `[language-server.virgil-lsp]` table. A TOML merge replaces arrays, so appending a second `languages.toml` that redefines `file-types` or `roots` replaces, not extends, the earlier list. The snippet names no grammar, formatter, or auto-format setting.

Make sure `virgil-lsp` is on the `PATH` that Helix sees, or edit `command` to give the server's absolute path.

Open a project containing a [`.virgil-lsp.json` project file](../../docs/configuration.md). The other roots in the snippet, `DEPS`, `.git`, and `virgil-lsp`, select the editor's root directory for the buffer, but they do not define a semantic source set: the project file alone decides which files form a program.

## The Virgil root

The bundled library works without an explicit root: with no client root, the server uses the `virgil/` directory beside its resolved executable, which release archives include ([The Virgil root](../../docs/configuration.md#the-virgil-root)). To override it, give the server an absolute `virgilRoot` in its `config`, which Helix sends as `initializationOptions`:

```toml
[language-server.virgil-lsp]
config = { virgilRoot = "/home/you/virgil" }
```

Replace the path with a Virgil checkout or installation that holds `lib/`. Unlike the [Neovim configurations](../nvim/README.md#the-virgil-root), this client does not read `VIRGIL_LOC`, so exporting `VIRGIL_LOC` alone does not configure it. The option is read when the server starts: after changing it, run `:lsp-restart`.

## What the server sees

The server attaches to `.v3` buffers only. Helix does not attach it to `.virgil-lsp.json` buffers, so unsaved project-file edits get no configuration diagnostics here, and the server never sees other JSON files.

## Verification

With Helix 25.07.1 and a `0.1.0-alpha.5` server installed from a release-style layout (the two executables and the `virgil/` library together), the following were exercised: a diagnostic, and its clearing after an unsaved correction; hover (`space k`); go to definition (`g d`) into the bundled `lib/util`; rename (`space r`); and `:lsp-restart`. The diagnostic, hover, and definition steps ran on a project whose `virgilDependencies` use `lib/util` from the bundled library; the rename ran on a separate project file, because rename needs a program that verifies and that project deliberately contains a type error. `hx --health virgil` reports the server as installed and executable.

## Troubleshooting

- `hx --health virgil` (or `:healthcheck` inside Helix) shows whether the server is on `PATH` and executable.
- [Troubleshooting](../../docs/troubleshooting.md) covers a server that doesn't start, files without diagnostics or navigation, and the Virgil root. The [privacy statement](../../PRIVACY.md) says what the server and the editor integrations read, write, and start.
