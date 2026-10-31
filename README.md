# Virgil Language Server

[![CI](https://github.com/SaltedTan/virgil-language-server/actions/workflows/ci.yml/badge.svg)](https://github.com/SaltedTan/virgil-language-server/actions/workflows/ci.yml)

A [Language Server Protocol](https://microsoft.github.io/language-server-protocol/) implementation for the [Virgil](https://github.com/titzer/virgil) programming language, written in Virgil and built on the Aeneas compiler's own parser and type checker.

> **Unofficial project.** This is an independent community tool. It is not affiliated with or endorsed by the Virgil maintainers.

> **Status: alpha.** `0.1.0-alpha.5` is the fifth [prerelease](docs/releasing.md). It adds [semantic tokens](docs/architecture.md#semantic-tokens), [selection ranges](docs/architecture.md#selection-ranges), [folding ranges](docs/architecture.md#folding-ranges), [type hierarchy](docs/architecture.md#type-hierarchy), [call hierarchy](docs/architecture.md#call-hierarchy), the [missing-semicolon quick fix](docs/architecture.md#code-actions), and analysis of files with a damaged method body or one missing `{` or `}`; the pinned Virgil now reports inheritance cycles instead of hanging or crashing the analysis worker; and the VS Code client has fixes for requests after a stop and for empty breadcrumbs after a restart. The diagnostics and navigation milestones (M2–M4) were completed with `0.1.0-alpha.1`, and the references, rename, signature help, and completion milestone (M5) with `0.1.0-alpha.2`; see [ROADMAP.md](ROADMAP.md). The server reports syntax errors and a document outline for open files, including unsaved edits, plus type errors, [go-to-definition](docs/architecture.md#definition), [go-to-type-definition](docs/architecture.md#type-definition), [hover](docs/architecture.md#hover), [references](docs/architecture.md#references), [rename](docs/architecture.md#rename), [workspace symbols](docs/architecture.md#workspace-symbols), and [signature help](docs/architecture.md#signature-help), including for [calls still being typed](docs/architecture.md#incomplete-calls), across the files of a [configured project](docs/configuration.md). [Document highlights](docs/architecture.md#document-highlights) mark a symbol's declaration and uses in the current document. [Go to implementation](docs/architecture.md#implementation) lists the classes that extend a class and the methods that override a method. [Type hierarchy](docs/architecture.md#type-hierarchy) shows a class's or variant's supertype and the types that extend it, one level at a time. [Call hierarchy](docs/architecture.md#call-hierarchy) shows the callers of a method or constructor and the functions it calls, one level at a time. [Completion](docs/architecture.md#completion) offers keywords in any open file, even one that doesn't parse, the declarations in scope at the cursor in a configured project, and, after `.`, the [members](docs/architecture.md#member-completion) of the receiver's type, repairing incomplete code at the cursor. The [occurrence index](docs/architecture.md#compiler-adapter) defines which names resolve, and the [architecture guide](docs/architecture.md) describes how each part works.

Since `0.1.0-alpha.5`, syntax-based [folding ranges](docs/architecture.md#folding-ranges) fold brace blocks and multiline comments from the current open document, including unsaved edits.

Since `0.1.0-alpha.5`, [selection ranges](docs/architecture.md#selection-ranges) expand selections through tokens, bracket groups, declarations, and the document from the current open text.

Since `0.1.0-alpha.5`, [semantic tokens](docs/architecture.md#semantic-tokens) color the names in a project by the declarations that they resolve to.

## Why

Virgil has a self-hosted compiler. Running `v3c` on save gives diagnostics for saved files only. It can't provide go-to-definition or hover, which need the compiler's bound syntax tree. This project uses Aeneas's front end so editors can get the compiler's real semantics instead of a reimplementation; see the [process model](docs/decisions/0004-analysis-worker-process.md).

`0.1.0-alpha.5` also provides the [missing-semicolon quick fix](docs/architecture.md#code-actions) through versioned code actions in VS Code and Neovim.

## v0.1 scope

One server, used by both LazyVim/Neovim and VS Code, providing:

- `.v3` file detection
- parser and type-checker diagnostics for unsaved buffers
- document symbols
- go-to-definition for locals, members, methods, and named types
- go-to-type-definition from resolved value and call-result types, including generic types and type parameters
- inlay hints for inferred local types and direct-call parameter names
- [hover](docs/architecture.md#hover) with declaration signatures and inferred types; on `main`, leading comments also appear below them
- a [`.virgil-lsp.json` project file](docs/configuration.md) describing which sources form a program

Incremental analysis comes after v0.1. Some later features are already available: [references](docs/architecture.md#references) and [signature help](docs/architecture.md#signature-help) since `0.1.0-alpha.1`, and [rename](docs/architecture.md#rename) and [completion](docs/architecture.md#completion) of keywords, in-scope declarations, and [members](docs/architecture.md#member-completion) since `0.1.0-alpha.2`. Formatting and debugging are out of scope. [Known limitations](docs/known-limitations.md) lists what else the alpha doesn't do yet.

## Architecture

The [architecture guide](docs/architecture.md) describes the server and its compiler adapter. The [analysis worker](docs/architecture.md#analysis-worker) serves the development `analyze` command and the unadvertised [development stdio requests](docs/development.md#development-stdio-requests). See the [configuration guide](docs/configuration.md) for project discovery and configuration diagnostics.

## Building

Requirements: Linux x86-64 or an Apple Silicon Mac with Rosetta 2, plus the [development prerequisites](docs/development.md#prerequisites). WSL 2 on Windows is best effort. See [docs/compatibility.md](docs/compatibility.md) for the full platform list. The Virgil compiler comes from the pinned submodule, so you don't need a separate Virgil install.

```sh
git clone --recurse-submodules https://github.com/SaltedTan/virgil-language-server.git
cd virgil-language-server
make test        # builds build/virgil-lsp and build/virgil-lsp-worker, and runs available tests
```

Keep `virgil-lsp-worker` next to `virgil-lsp` when moving the executables. New release archives also include `virgil/` with the pinned library: keep it beside the executables to analyze `virgilDependencies` under `lib/` without a separate checkout. An optional client root overrides the bundle; sources outside `lib/` still need a checkout ([root selection](docs/configuration.md#the-virgil-root)). [Releases](docs/releasing.md) describes the release archives, how to verify them, and how to install them.

Try the current development command:

```sh
build/virgil-lsp --version
build/virgil-lsp parse test/fixtures/syntax/tab-error.v3
build/virgil-lsp analyze --bindings test/fixtures/analysis/two-file/*.v3
```

See [docs/development.md](docs/development.md) for details.

## Editor setup

For VS Code, [install the VSIX for your workspace platform](clients/vscode/README.md#installing-from-a-vsix): `linux-x64`, `darwin-arm64` (requires Rosetta 2), or `darwin-x64`. It includes the server, worker, and pinned library. No separate server installation or path setting is needed. The universal VSIX is available for external-server installations.

For Neovim, LazyVim, Emacs, or an external VS Code server, install the server first. On Linux x86-64, with `curl`, `tar`, and `sha256sum` installed, run the following in a terminal. Replace `server_version` with the release version and `download_url` with that release's asset download location (or a directory served over HTTP containing the downloaded assets). The checksum comes from the same release; download it along with the archive.

<!-- clean-install: server -->
```sh
server_version=0.1.0-alpha.5
download_url=https://github.com/SaltedTan/virgil-language-server/releases/download/v$server_version
archive=virgil-lsp-$server_version-x86-64-linux.tar.gz
mkdir -p "$HOME/Downloads/virgil-lsp" &&
cd "$HOME/Downloads/virgil-lsp" &&
curl --fail --location "$download_url/$archive" -o "$archive" &&
curl --fail --location "$download_url/SHA256SUMS" -o SHA256SUMS &&
awk -v file="$archive" '$2 == file' SHA256SUMS | sha256sum --check --strict &&
mkdir -p "$HOME/.local/opt" "$HOME/.local/bin" &&
tar -xzf "$archive" -C "$HOME/.local/opt" &&
ln -sf "$HOME/.local/opt/virgil-lsp-$server_version-x86-64-linux/virgil-lsp" "$HOME/.local/bin/virgil-lsp" &&
export PATH="$HOME/.local/bin:$PATH" &&
virgil-lsp --version
```

Keep the worker and `virgil/` beside the server. Add the `export PATH` line to your shell's startup file for future terminals, and start your editor from this terminal for the first install. For macOS downloads, checksum verification, and Rosetta 2, see [Installing](docs/releasing.md#installing).

For VS Code, [install the prerelease VSIX](clients/vscode/README.md#installing-from-a-vsix). Neovim and LazyVim setup and integration status live in [`editors/nvim/`](editors/nvim/). For Helix, a best-effort, grammar-free configuration and setup guide live in [`editors/helix/`](editors/helix/); it is outside the [supported editor matrix](docs/compatibility.md#editors). A best-effort Emacs configuration for Eglot, outside the [tested editor matrix](docs/compatibility.md#editors), is in [`editors/emacs/`](editors/emacs/). Once the server is installed, [try your first project](docs/first-project.md): a small checked-in example with a deliberate type error, its one-word fix, and a hover and go-to-definition to check.

If the server doesn't start, or a file gets no diagnostics or navigation, see [Troubleshooting](docs/troubleshooting.md). [PRIVACY.md](PRIVACY.md) states what the server and the editor integrations read, write, and start; they collect no telemetry and make no network connections.

## Contributing

Bug reports, documentation, tests, and code are all welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report security issues as described in [SECURITY.md](SECURITY.md).

## License

The project's code is licensed under the [Apache License, Version 2.0](LICENSE). Third-party material retains its upstream licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
