# Contributing

Thank you for your interest in the Virgil Language Server. Bug reports, documentation, tests, fixtures, and code are all welcome.

This is an unofficial, independently maintained project. By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Where to start

- **Questions and design ideas:** open a [GitHub Discussion](https://github.com/SaltedTan/virgil-language-server/discussions).
- **Bugs and concrete work items:** open an [issue](https://github.com/SaltedTan/virgil-language-server/issues/new/choose) using a template.
- **First contributions:** look for issues labelled `good first issue` or `help wanted`. Fixture and documentation work is a good entry point.
- **Plan and priorities:** see [ROADMAP.md](ROADMAP.md). The GitHub Project tracks the next one or two milestones.

Please comment on an issue before starting substantial work, so that effort isn't duplicated and the design can be agreed first.

## Development setup

```sh
git clone --recurse-submodules https://github.com/SaltedTan/virgil-language-server.git
cd virgil-language-server
make test
```

[docs/development.md](docs/development.md) covers the build, tests, and repository layout in more detail.

## Pull requests

- Keep pull requests small and focused on one change. Include tests that exercise it.
- `make test` and all required CI checks must pass before merge. See the [development guide](docs/development.md#tests) for the server suites and VS Code client checks.
- Update documentation and `CHANGELOG.md` (under *Unreleased*) for user-visible changes.
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
2. `make clean test` and fix any adapter breakage inside `src/analysis/`.
3. Update the revision in `THIRD_PARTY_NOTICES.md` and `docs/compatibility.md`.

## Licensing of contributions

This project is licensed under the [Apache License 2.0](LICENSE). Unless you explicitly state otherwise, any contribution you intentionally submit is licensed under the same terms (Apache-2.0, section 5). You keep the copyright in your work. No CLA is required.

Only submit code that you wrote or have the right to submit. If a contribution includes or adapts third-party material, say so in the pull request and add an entry to [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Code from repositories without an explicit license can't be accepted.

New source files should start with:

```
// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0
```
