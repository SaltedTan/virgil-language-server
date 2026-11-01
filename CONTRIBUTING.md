# Contributing

Thank you for your interest in the Virgil Language Server. Bug reports, documentation, tests, fixtures, and code are all welcome.

This is an unofficial, independently maintained project. By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Where to start

- **Questions and design ideas:** open a [GitHub Discussion](https://github.com/SaltedTan/virgil-language-server/discussions).
- **Bugs and concrete work items:** open an [issue](https://github.com/SaltedTan/virgil-language-server/issues/new/choose) using a template.
- **First contributions:** look for issues labelled `good first issue` or `help wanted`. Fixture and documentation work is a good entry point.
- **Plan and priorities:** see [ROADMAP.md](ROADMAP.md). The GitHub Project tracks the next one or two milestones.

Please comment on an issue before starting substantial work, so that effort isn't duplicated and the design can be agreed first.

## Work items

The [work item form](.github/ISSUE_TEMPLATE/task.yml) asks for enough that a coding agent can start with the issue as its only context:

- **Goal** and **Acceptance criteria** say what changes and when it is done.
- **Current state** and **Scope and non-goals** say what exists today and what the item deliberately leaves alone.
- **Verification** gives the commands to run and the observable result that shows each criterion is met.
- **Affected docs** and **Dependencies** name the documentation to update and anything that must land first.
- **Open question for the maintainer** holds the concrete question when the item needs a decision.
- **Notes** holds the approach, risks, and sources.

The required **Size** field is S for up to half a day, M for one or two days, or needs splitting for anything larger. Split larger work before it starts; don't stretch it into M. Size guides review and how far one agent session goes, and the project doesn't add labels for it.

The required **Merge** field records who merges. Normal review follows the [pull request rules](#pull-requests). Human merge is for the changes those rules reserve for the maintainer's own merge. Maintainer decision needed means the open question must be answered before work starts.

The maintainer applies labels when reviewing an issue. `blocked/dependency` marks work that waits on another issue or upstream; `good first issue` and `help wanted` mark approachable work; the `area/*` and `type/*` labels say where the work lives. `agent-ready` marks a reviewed, self-contained S or M item with its verification stated, no open dependency, no open maintainer decision, and a stated merge owner, so a new agent can start now. `needs-decision` marks an item that waits on a maintainer decision; the issue states the question.

## Development setup

```sh
git clone --recurse-submodules https://github.com/SaltedTan/virgil-language-server.git
cd virgil-language-server
make test
```

[docs/development.md](docs/development.md) covers the build, tests, and repository layout in more detail. Coding agents and people starting from a single issue can read the repository instructions in [AGENTS.md](AGENTS.md) first.

## From a branch to a pull request

This is the short path from a clone to a reviewable pull request.

1. Check the [prerequisites](docs/development.md#prerequisites).
2. Fork the repository on GitHub, since outside contributors can't push to `SaltedTan/virgil-language-server`, and clone your fork with its submodule. If you cloned without `--recurse-submodules`, run `git submodule update --init --recursive` in the clone.

   ```sh
   git clone --recurse-submodules https://github.com/<you>/virgil-language-server.git
   cd virgil-language-server
   ```

3. Create a branch from an up-to-date `main`; in a fork, use GitHub's "Sync fork" first.

   ```sh
   git switch main
   git pull
   git switch -c fix/short-name
   ```

4. Make one focused change, with tests that exercise it, and the documentation and `CHANGELOG.md` updates that the [pull request rules](#pull-requests) ask for. Commit the change on your branch with a message that explains why (`git add <files>`, then `git commit`).
5. Run the checks that apply:
   - `make test` builds the server and runs the unit tests and the server suites, [including the headless Neovim smoke test](docs/development.md#tests), which skips locally without Neovim 0.11 or later.
   - When anything under `clients/vscode/` changes, run `npm ci`, `npm run check`, and `npm test` in `clients/vscode`, after the [client setup](clients/vscode/README.md#setup); see the [client development commands](clients/vscode/README.md#development).
   - The real Extension Host test is a separate run: `npm run test:e2e` against the server that `make` built, and `xvfb-run -a npm run test:e2e` on Linux without a display; see the [Extension Host test](clients/vscode/README.md#extension-host-test).
   - When the LazyVim configuration, or a feature that its test drives, changes, run `make test-lazyvim`, which needs tmux and network access; see [Running it](test/e2e/lazyvim/README.md#running-it).

   `make test` runs neither the VS Code client checks nor the LazyVim test, and the editor tests aren't required for every change. If you skip a check, name it in the pull request with the reason, as [AGENTS.md](AGENTS.md#which-checks-apply) says.

6. Push the branch to your fork with `git push -u origin fix/short-name`, then open a pull request against `main` with GitHub's "Compare & pull request" button, or with `gh pr create --repo SaltedTan/virgil-language-server --base main`. Link the issue (`Fixes #N`), and say what you validated and what you skipped.
7. CI runs the required checks and the maintainer reviews. Changes that need the maintainer's own merge wait for it, as the [pull request rules](#pull-requests) say.

## Pull requests

- Keep pull requests small and focused on one change. Include tests that exercise it.
- `make test` and all required CI checks must pass before merge. See the [development guide](docs/development.md#tests) for the server suites and VS Code client checks.
- Update documentation and `CHANGELOG.md` (under *Unreleased*) for user-visible changes.
- Changes under `.github/workflows/`, dependencies (including the `vendor/virgil` pin and npm dependencies), and suite-wide test settings (timeouts, retries, which tests run, or test-runner behavior) need the maintainer's own merge. Other files under `.github/` don't.
- Never advertise an LSP capability that isn't fully implemented and tested.
- Keep standard output protocol-only. All logging goes to standard error through `Log`.
- Only code under `src/analysis/` may reference Aeneas compiler types ([ADR-0002](docs/decisions/0002-pinned-virgil-adapter-boundary.md)).
- Conventional commit prefixes are welcome but not required. Clear commit messages matter more.

The maintainer also works through branches and pull requests, so that CI and decisions stay visible.

## Architecture decisions

Durable technical decisions are recorded as short ADRs in [docs/decisions/](docs/decisions/). To propose a new one, open a pull request that adds the next numbered file using the [template](docs/decisions/0000-template.md), and link the discussion that motivated it.

## Project file compatibility review

Server 1.x keeps the version 1 project file's format and project selection ([ADR-0012](docs/decisions/0012-project-configuration-stability.md)). Review a change to `src/workspace/`, to `LspProjectDiagnostics` or `LspWorkspaceEvents` in `src/protocol/`, or to the Virgil pin with these questions:

- Does it change how a version 1 file is parsed or validated: a field, type, default, or reserved value, the handling of unknown or repeated fields, or the version literal?
- Does it change which files a project selects or owns: pattern syntax or matching, ordering, exclusion, symbolic links, or the Virgil root?
- Does it change the governing project file, workspace folders, the active project, or how unsaved and on-disk configuration changes are picked up?
- Does it change a capacity limit?

If it does, find the change in the [table of what each change needs](docs/configuration.md#what-a-change-needs). A fix that makes the server match the documentation needs only a changelog entry. A new field, a new accepted value, a meaning for reserved pattern characters, or comments need a new format version. These changes to version 1 need a new server major version: removing or renaming a field, giving it another type or default, tightening validation, changing how patterns select, order, own, or exclude files, following symbolic links, changing the Virgil root's precedence or the choice of governing file or active project, and no longer reading version 1. Changes to when the server reads and refreshes configuration (*Behavior* rows), changes to capacity limits, and language option changes from a compiler pin need only a changelog entry. Update the matching row of the [inventory](docs/configuration.md#inventory) and its tests.

For a Virgil pin update, compare the language options (`CLOptions.langOpt` in `vendor/virgil/aeneas/src/main/CLOptions.v3`) at the old and new revisions: their names, their defaults, and whether they need an unstable build. Update the [compiler flags table](docs/configuration.md#compiler-flags). For each option that was removed, renamed, or changed, add a changelog entry that names it and says how to migrate.

## Updating the pinned Virgil revision

Virgil upgrades are separate pull requests labelled `dependency/virgil`:

1. `git -C vendor/virgil fetch && git -C vendor/virgil checkout <sha>`
2. Regenerate the benchmark corpus with `python3 scripts/bench-corpus.py --write`
   and review the diff, including file hashes and query/edit anchors. Tests read
   the gitlink from the index, so stage the new gitlink and the regenerated
   corpus together before running the checks below.
3. `make clean test` and fix any adapter breakage inside `src/analysis/`. Run a
   short `make bench BENCH_ARGS='--blocks 1 --operations 1 --warmups 0 --output build/pin-bump-bench.json'`
   before finalizing the upgrade.
4. Update the revision in `THIRD_PARTY_NOTICES.md` and `docs/compatibility.md`.
5. Review the language options as [Project file compatibility review](#project-file-compatibility-review) says.
6. Check that every record of the pin agrees on the new full SHA, as the [support policy](docs/compatibility.md#support-policy) requires:
   - `build/virgil-lsp --version`, whose `virgil revision` comes from the checkout;
   - the staged gitlink, `git rev-parse :vendor/virgil`, and the pin tables in `THIRD_PARTY_NOTICES.md` and in the `main` row of the [Virgil revision table](docs/compatibility.md#virgil-revision);
   - the `virgil_source_revision` of the regenerated, reviewed corpus in `test/benchmarks/corpus.json`;
   - after committing, the packaged bundle and manifest: `scripts/package-release.sh build/release` refuses a Virgil checkout other than the gitlink, `scripts/check-release.sh --archive <archive>` checks the archive's `virgil/REVISION` and its manifest's `virgilRevision` and `library.revision` against the gitlink, and `scripts/smoke-test-release.sh <archive>` checks `--version` against the manifest.
7. In `CHANGELOG.md` under `## [Unreleased]`, name the old and new revisions and each language option that changed.

The pin update is a dependency change, so it needs the maintainer's own merge, as do workflow changes and suite-wide test settings ([pull requests](#pull-requests)).

## Licensing of contributions

This project is licensed under the [Apache License 2.0](LICENSE). Unless you explicitly state otherwise, any contribution you intentionally submit is licensed under the same terms (Apache-2.0, section 5). You keep the copyright in your work. No CLA is required.

Only submit code that you wrote or have the right to submit. If a contribution includes or adapts third-party material, say so in the pull request and add an entry to [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Code from repositories without an explicit license can't be accepted.

New source files should start with:

```
// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0
```
