# Virgil Language Server

[![CI](https://github.com/SaltedTan/virgil-language-server/actions/workflows/ci.yml/badge.svg)](https://github.com/SaltedTan/virgil-language-server/actions/workflows/ci.yml)

A [Language Server Protocol](https://microsoft.github.io/language-server-protocol/) implementation for the [Virgil](https://github.com/titzer/virgil) programming language, written in Virgil and built on the Aeneas compiler's own parser and type checker.

> **Unofficial project.** This is an independent community tool. It is not affiliated with or endorsed by the Virgil maintainers.

> **Status: alpha.** `0.1.0-alpha.3` is the third [prerelease](docs/releasing.md). The diagnostics and navigation milestones (M2–M4) were completed with `0.1.0-alpha.1`, and the references, rename, signature help, and completion milestone (M5) with `0.1.0-alpha.2`; see [ROADMAP.md](ROADMAP.md). The server reports syntax errors and a document outline for open files, including unsaved edits, plus type errors, [go-to-definition](docs/architecture.md#definition), [hover](docs/architecture.md#hover), [references](docs/architecture.md#references), [rename](docs/architecture.md#rename), [workspace symbols](docs/architecture.md#workspace-symbols), and [signature help](docs/architecture.md#signature-help), including for [calls still being typed](docs/architecture.md#incomplete-calls), across the files of a [configured project](docs/configuration.md). [Completion](docs/architecture.md#completion) offers keywords in any open file, even one that doesn't parse, the declarations in scope at the cursor in a configured project, and, after `.`, the [members](docs/architecture.md#member-completion) of the receiver's type, repairing incomplete code at the cursor. The [occurrence index](docs/architecture.md#compiler-adapter) defines which names resolve, and the [architecture guide](docs/architecture.md) describes how each part works.

## Why

Virgil has a self-hosted compiler. Running `v3c` on save gives diagnostics for saved files only. It can't provide go-to-definition or hover, which need the compiler's bound syntax tree. This project uses Aeneas's front end so editors can get the compiler's real semantics instead of a reimplementation; see the [process model](docs/decisions/0004-analysis-worker-process.md).

## v0.1 scope

One server, used by both LazyVim/Neovim and VS Code, providing:

- `.v3` file detection
- parser and type-checker diagnostics for unsaved buffers
- document symbols
- go-to-definition for locals, members, methods, and named types
- hover with declaration signatures and inferred types
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

Keep `virgil-lsp-worker` next to `virgil-lsp` when moving the executables. [Releases](docs/releasing.md) describes the release archives, how to verify them, and how to install them.

Try the current development command:

```sh
build/virgil-lsp --version
build/virgil-lsp parse test/fixtures/syntax/tab-error.v3
build/virgil-lsp analyze --bindings test/fixtures/analysis/two-file/*.v3
```

See [docs/development.md](docs/development.md) for details.

## Editor setup

For VS Code, [install the prerelease VSIX](clients/vscode/README.md#installing-from-a-vsix). Neovim and LazyVim setup and integration status live in [`editors/nvim/`](editors/nvim/).

## Contributing

Bug reports, documentation, tests, and code are all welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report security issues as described in [SECURITY.md](SECURITY.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE). The pinned Virgil compiler in `vendor/virgil` is a separately licensed upstream dependency; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
