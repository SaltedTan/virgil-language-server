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

The required **Size** field is S for up to half a day, M for one or two days, or needs splitting for anything larger. Split larger work before it starts; don't stretch it into M. Size guides review and how far one agent session goes, and the project doesn't add labels for it.

The required **Merge** field records who merges. Normal review follows the pull-request rules below. Human merge is for changes under `.github/workflows/`, dependencies (including the `vendor/virgil` pin and npm dependencies), and suite-wide test settings (timeouts, retries, which tests run, or test-runner behavior). Maintainer decision needed names the open question, which the maintainer answers before work starts.

Labels are a maintainer-owned queue signal, so an unreviewed issue isn't ready just because it has one. `blocked/dependency` marks work that waits on another issue or upstream; `good first issue` and `help wanted` mark approachable work; the `area/*` and `type/*` labels say where the work lives. `agent-ready` marks a reviewed, self-contained S or M item with required verification, no open dependency, no open maintainer decision, and a stated merge owner, so a new agent can start now. `needs-decision` marks an item that waits on the maintainer question named in its Merge line.

## Development setup

```sh
git clone --recurse-submodules https://github.com/SaltedTan/virgil-language-server.git
cd virgil-language-server
make test
```

[docs/development.md](docs/development.md) covers the build, tests, and repository layout in more detail. Coding agents and people starting from a single issue can read the repository instructions in [AGENTS.md](AGENTS.md) first.

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

## Licensing of contributions

This project is licensed under the [Apache License 2.0](LICENSE). Unless you explicitly state otherwise, any contribution you intentionally submit is licensed under the same terms (Apache-2.0, section 5). You keep the copyright in your work. No CLA is required.

Only submit code that you wrote or have the right to submit. If a contribution includes or adapts third-party material, say so in the pull request and add an entry to [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Code from repositories without an explicit license can't be accepted.

New source files should start with:

```
// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0
```
