# Repository instructions for coding agents

This file is for a coding agent or person who starts from one issue and needs to implement and validate a change here. Read the issue and [CONTRIBUTING.md](CONTRIBUTING.md) first, then the [development guide](docs/development.md). This file summarizes the repository-wide rules; the linked sources own the details.

## What this is

The Virgil Language Server provides an LSP server for the Virgil programming language, written in Virgil on the Aeneas compiler's parser and type checker, plus a VS Code client and Neovim/LazyVim configurations. See the [README](README.md) and the [architecture guide](docs/architecture.md).

| Path | What it holds |
| --- | --- |
| `src/main.v3` | Command-line entry point. |
| `src/analysis/` | The compiler adapter, analysis snapshots, symbol indexes, syntax folding ranges, and selection chains. This is the only code that may touch Aeneas types. |
| `src/protocol/` | Byte framing, JSON-RPC messages, lifecycle, document synchronization, feature handlers, diagnostics, and analysis scheduling. |
| `src/worker/` | The analysis worker's entry point and supervisor, its framing, and the message codec. |
| `src/documents/` | URI normalization, versioned overlays, and position mapping. |
| `src/workspace/` | Project files, bounded glob expansion, workspace discovery, diagnostic ownership, and configuration revisions. |
| `src/os/<target>/` | System calls that the Virgil `System` component lacks, one file per target. |
| `src/Log.v3` | Logging to standard error. |
| `test/` | Virgil unit tests, protocol and CLI transcripts, diagnostics, release checks, editor tests under `test/e2e/`, and fixtures. |
| `clients/vscode/` | The TypeScript VS Code extension. |
| `editors/nvim/` | The plain Neovim and LazyVim configurations. |
| `docs/` | Guides, with durable decisions in [`docs/decisions/`](docs/decisions/README.md). |
| `vendor/virgil` | The pinned upstream Virgil submodule. Don't edit it. Its own `CLAUDE.md` belongs upstream and does not apply here. |

The [source layout](docs/architecture.md#source-layout) section describes the server's directories and the [build model](docs/architecture.md#build-model) explains why every source is compiled together.

## Setup

Clone with the submodule, or initialize it in an existing clone:

```sh
git clone --recurse-submodules https://github.com/SaltedTan/virgil-language-server.git
git submodule update --init --recursive
```

Development hosts are Linux x86-64, or an Apple Silicon Mac with Rosetta 2; WSL 2 on Windows is best effort. The pinned submodule supplies Virgil, so no separate compiler install is needed. See the [prerequisites](docs/development.md#prerequisites).

## Build and test

`make` builds `build/virgil-lsp` and `build/virgil-lsp-worker`; its default target is `all`. `make test` builds those and the test-only executables, runs the unit tests, and then the suites in the Makefile's `SUITES` list. It does not run the VS Code client or LazyVim suites.

- `make test-unit` runs only the unit tests. `make test-unit ARGS='ProjectExpansion:*'` runs one focused group in a single process; multiple space-separated patterns are allowed.
- `make test-nvim` runs the headless Neovim smoke test and is also part of `make test`. Locally it skips with a message if Neovim is missing or older than 0.11; `REQUIRE_NVIM=1` makes that a failure, as CI does.
- `make test-protocol-shapes` runs the bounded offline LSP shape checks on their own; the same checks also run in `make test`.

In `clients/vscode`, install from the lockfile and run the client checks:

```sh
cd clients/vscode
npm ci
npm run check
npm test
```

`npm run check` type-checks only. `npm test` compiles and runs the extension's lifecycle and test-runner tests in Node. `npm run test:e2e` is a separate run in a real Extension Host, needs the built server, and on Linux without a display needs `xvfb-run -a npm run test:e2e`; see the [client development commands](clients/vscode/README.md#development) and the [Extension Host test](clients/vscode/README.md#extension-host-test).

`make test-lazyvim` runs the LazyVim configuration under the pinned LazyVim starter in tmux. It is not part of `make test` and needs Neovim 0.11 or later, tmux, git, Python 3, GitHub access, curl, tar, a C compiler for the tree-sitter parsers, and an initialized `vendor/virgil`. See [Running the LazyVim test](test/e2e/lazyvim/README.md#running-it).

Before running a suite directly, build the executables it expects. The [Tests](docs/development.md#tests) section lists each suite and its command.

## Which checks apply

- Use focused tests while working, such as `make test-unit ARGS='Thing:*'`.
- Run `make test` before opening a pull request.
- Run the client checks when anything under `clients/vscode/` changes.
- Run `make test-nvim` when `editors/nvim/` or behavior the Neovim smoke test checks changes, and `make test-lazyvim` when the LazyVim configuration or a feature its test drives changes; the [LazyVim test](test/e2e/lazyvim/README.md) lists what it covers.

If you skip a check, name it and say why: the tool is missing, there is no display, there is no network access, or the suite is not relevant. Don't leave the impression that a skipped check passed.

## Rules that keep the server correct

- Standard output carries only protocol messages. All logging goes to standard error through `Log`, as [CONTRIBUTING](CONTRIBUTING.md#pull-requests) and the [source layout](docs/architecture.md#source-layout) state.
- Only code under `src/analysis/` may reference Aeneas compiler types. The adapter returns server-owned types, and server type names must not collide with compiler names ([ADR-0002](docs/decisions/0002-pinned-virgil-adapter-boundary.md)).
- Never advertise an LSP capability that isn't fully implemented and tested ([CONTRIBUTING](CONTRIBUTING.md#pull-requests)).

## Concurrency and cleanup

Unit-test groups run in parallel, one per CPU up to 8 by default, or `UNIT_JOBS`. The other suites run up to 4 at once by default, or `SUITE_JOBS`; for example `make test SUITE_JOBS=1`. See [Build and test](docs/development.md#build-and-test) and the [test runner notes](docs/development.md#tests).

`make test` and `make test-unit` restore the fixture directories that must be unreadable and remove extra unit-test slots, so the build directory stays removable with a plain `rm -rf` or `git worktree remove`, without `make clean` or `chmod` first. The [fixture lifecycle](test/fixtures/projects/README.md) describes the details.

## Changes and PRs

- Keep pull requests small and focused on one change, and include tests that exercise it.
- Update documentation and `CHANGELOG.md` under *Unreleased* for user-visible changes.
- Record durable technical decisions as an ADR in [docs/decisions/](docs/decisions/README.md), using the [template](docs/decisions/0000-template.md) for the next number.
- New source files start with the copyright and SPDX header that [CONTRIBUTING](CONTRIBUTING.md#licensing-of-contributions) shows.
- Contributions are licensed under Apache-2.0; no CLA is required. Say so in the pull request and update [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) when a contribution includes or adapts third-party material. See [Licensing of contributions](CONTRIBUTING.md#licensing-of-contributions).

## Human merge

Changes to GitHub workflows under `.github/workflows/`, dependencies (including the `vendor/virgil` pin and npm dependencies), or suite-wide test settings (timeouts, retries, which tests run, or test runner behavior) need a maintainer's merge. Other files under `.github/`, such as issue templates, don't.

The maintainer reviews and merges pull requests, and `make test` plus all required CI checks must pass first. See [Pull requests](CONTRIBUTING.md#pull-requests).
