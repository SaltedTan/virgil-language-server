# Compatibility

## Virgil revision

| Server version | Pinned Virgil revision | Notes |
| --- | --- | --- |
| `main` (unreleased) | [`57318d37`](https://github.com/titzer/virgil/commit/57318d37e9fcca8de6fda5f9cd82d669479f49ff) (2026-10-27) | Required CI |
| `0.1.0-alpha.5` | [`57318d37`](https://github.com/titzer/virgil/commit/57318d37e9fcca8de6fda5f9cd82d669479f49ff) (2026-10-27) | Fifth prerelease |
| `0.1.0-alpha.4` | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Fourth prerelease |
| `0.1.0-alpha.3` | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Third prerelease |
| `0.1.0-alpha.2` | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Second prerelease |
| `0.1.0-alpha.1` | [`7cf038fc`](https://github.com/titzer/virgil/commit/7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | First prerelease |

The scheduled `virgil-master` workflow builds and tests against current upstream `master` as an advisory signal. A failure there does not block merges.

Each release supports exactly one Virgil revision, the one in this table. To find the revision of a server you have:

- run `virgil-lsp --version`, which prints it as `virgil revision: <full SHA>`;
- read its release's `manifest.json`, in the archive or as the `virgil-lsp-<version>-<target>.manifest.json` asset, where `virgilRevision` and `library.revision` give it, or the release notes' *Revisions* section;
- or read `virgil/REVISION` beside the executables, which the server checks against its own revision before it uses that library.

## Support policy

[ADR-0014](decisions/0014-compatibility-and-support-policy.md) records this policy and why. The [project file's stability contract](configuration.md#stability-and-evolution) covers `.virgil-lsp.json` itself, and this section doesn't change it.

### What a release supports

| Layer | Supported | Otherwise |
| --- | --- | --- |
| The compiler sources compiled into `virgil-lsp` and `virgil-lsp-worker` | The release's [pinned revision](#virgil-revision), fixed when it was built | Nothing at run time changes it. Upstream `master` is only an advisory CI signal. |
| The Virgil source you analyze, and `compilerArgs` | Target-independent analysis of source that the pinned compiler accepts, with its `-lang:` options ([Compiler flags](configuration.md#compiler-flags)), within the documented limits. Every project is analyzed without a target, so code written for a native target, which uses machine-level types such as `Pointer`, reports unresolved types even when the pinned compiler builds it for that target ([Target](configuration.md#target)). A program must also stay within the capacity limits ([supported program size](known-limitations.md#supported-program-size)). | Source written for another Virgil revision is analyzed as the pinned compiler sees it, best effort. Keep a release whose pin accepts it. |
| The library that `virgilDependencies` reads | The bundled `virgil/` library, which the server uses only when its `REVISION` matches the pin, or a client root at the pinned revision | A client root at another revision is best effort: the server doesn't check its revision, and doesn't reject it. |
| `virgil-lsp-worker` | The worker from the same build as the server | Unsupported. The server refuses a worker whose worker protocol version, server version, or source revision text differs from its own ([troubleshooting](troubleshooting.md#a-worker-missing-beside-the-server)). It doesn't compare the executables or their Virgil revision, so it doesn't detect every mismatch, such as two builds of one commit with different uncommitted changes; such a pair is still unsupported. |
| The VS Code extension | A platform VSIX with the server it bundles; with an external server, the versions in the [extension table](#editors) | Other pairs are untested. |
| Editors and hosts | The minimum versions in [Editors](#editors) and the platforms in [Host platforms](#host-platforms) | As those tables say, including best-effort WSL 2 and Intel macOS. |

A client root, `virgil.virgilRoot` or `VIRGIL_LOC`, only chooses the library files that a project's `virgilDependencies` reads ([The Virgil root](configuration.md#the-virgil-root)). It doesn't change the compiler compiled into the server, which still parses and type-checks everything at the pinned revision.

### Fixes and older releases

Routine fixes go into the next release of the current minor line only. There are no backports, no long-term-support line, and no second maintained compiler branch. Every published release stays on the [releases page](https://github.com/SaltedTan/virgil-language-server/releases) with its archives, VSIX files, manifests, and `SHA256SUMS`, so you can keep the release whose pin matches your code; it gets no further fixes. For a security or correctness problem, the maintainer may make an exception, such as a fix on an older line, without promising more.

### How changes are announced

`CHANGELOG.md` announces each of these, and its section for a release becomes the release notes:

- a new pinned Virgil revision, with the old and the new one;
- a language option that a new pin removes or changes, by name ([Language options follow the compiler pin](configuration.md#language-options-follow-the-compiler-pin));
- a higher minimum editor version, which may come in a minor release;
- a change to the public command line (`--stdio`, its `--max-message-bytes` option, `--version`, and `--help`), the documented initialization options, the client settings, or the advertised capabilities.

The project already adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), as `CHANGELOG.md` says, and that needs a declared public API. ADR-0014 declares it: the server's public command line above and its documented initialization options, and the extension's client settings; the project file has its own [contract](configuration.md#stability-and-evolution). So, from 1.0, an intentional incompatible change to the command line or the initialization options needs a new server major version, and one to a client setting a new major version of the extension. Before 1.0, a prerelease may make one, and its notes say so. A change to the advertised capabilities is announced in `CHANGELOG.md`, but isn't part of the declared API.

### Outside the guarantee

These may change in any release without notice:

- the development commands, which `virgil-lsp --help` lists as unstable (`parse`, `analyze`, `repair`, and their options), and the worker policy options that `--stdio` also accepts;
- the [development stdio requests](development.md#development-stdio-requests);
- the protocol between the server and its worker. The server and worker of one build must still stay together;
- the wording of logs and messages.

### Evidence

The tables on this page record what each release was built with and what was tested with it. A row doesn't mean that every combination in it was tested: a combination is verified only where a named test or a [recorded result](manual-testing.md#results) covers it. These tests cover the policy's paths, and `make test` runs those in the server:

| Path | Tests |
| --- | --- |
| `--version` prints the pin | `test/cli/run.sh` ("version reports virgil revision"); [`scripts/smoke-test-release.sh`](../scripts/smoke-test-release.sh) checks it against each release archive's manifest |
| Bundle revision and root precedence | [`test/protocol/bundled_library.py`](../test/protocol/bundled_library.py) (including "revision mismatch"); the `lifecycle-initialize-*-virgil-root` transcripts under [`test/protocol/`](../test/protocol/); [`test/release/test_library.py`](../test/release/test_library.py) for the archive's `REVISION` and manifest |
| Worker matching | `AnalysisSupervisor:missing_worker`, `AnalysisSupervisor:not_a_worker`, and `AnalysisWire:hello_round_trip`. No test sends a handshake with another protocol version, server version, or source revision. |
| Project file version | `ProjectConfig:version` |
| Client startup | In `npm test` in `clients/vscode`: [`test/lifecycle.cjs`](../clients/vscode/test/lifecycle.cjs)'s "server selection", "a broken bundle", and "a mismatched workspace host" tests |

A release adds the [release workflow's](releasing.md#how-the-workflow-works) evidence: the archive smoke test, the [Extension Host test](../clients/vscode/README.md#extension-host-test) on the `linux-x64`, `universal`, and `darwin-arm64` VSIX variants, the [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test), and the LazyVim test. The `darwin-x64` VSIX gets only the packaging checks.

## LSP baseline

The server targets a conservative subset of [LSP 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/). See [Lifecycle](architecture.md#lifecycle) for initialization behavior and [Document store](architecture.md#document-store) for synchronization capabilities.

Inlay hints use LSP 3.17 `textDocument/inlayHint` and optional `workspace/inlayHint/refresh` support. The server advertises no resolve provider and sends no insertion edits ([rules and settings](architecture.md#inlay-hints)).

## Host platforms

The reasons for this list are in [ADR-0003](decisions/0003-supported-platforms.md).

| Platform | Status | Build | CI |
| --- | --- | --- | --- |
| Linux x86-64 | **Supported** | `x86-64-linux` | Required |
| Windows x86-64 with WSL 2 | Best effort: the Linux build, run inside WSL 2, is untested | `x86-64-linux` | Linux job only; nothing runs inside WSL |
| macOS on Apple Silicon | **Supported** (interim), using the Intel build under Rosetta 2 | `x86-64-darwin` | Required, on `macos-15` (Apple Silicon) |
| macOS on Intel | Best effort: same build, untested | `x86-64-darwin` | None |
| Native Windows | Not supported. WSL 2 runs the Linux build on a best-effort basis. | — | — |
| Linux arm64 | Not supported yet | — | — |
| WSL 1 | Not supported | — | — |

The [repository setup script](../scripts/github-setup.sh) configures both pinned-Virgil CI jobs, the `vscode-client` job, and `lazyvim-linux`, the [LazyVim test](../test/e2e/lazyvim/README.md#ci) on Linux, as required checks on `main`. It does not include `vscode-extension-host` or `vscode-extension-host-macos`. `lazyvim-linux` runs on every pull request, and its failure blocks the merge. It became a required check after it had passed 20 pull-request runs in a row, counting only each run's first attempt: a rerun, a skipped job, or a cancelled job earned no credit. An audit on 2026-11-01 found 103 such passes, on Neovim 0.11.6 and LazyVim 16.0.1, after the last failure, the first attempt of [run 40343723363](https://github.com/SaltedTan/virgil-language-server/actions/runs/40343723363/attempts/1), where installing the tree-sitter parsers failed before any editor check ran. When the `test` job fails, `lazyvim-linux` is skipped, and GitHub counts a skipped job as passing, but the failed `test` check still blocks the merge. The weekly [editor tests workflow](../.github/workflows/editor-tests.yml) runs the LazyVim test on Linux with Neovim 0.11 and 0.12 and on macOS, the Extension Host test on macOS on an installed VSIX, and the [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test) on Linux, and none of its jobs are required. The [release workflow](releasing.md#how-the-workflow-works) runs the same jobs on each release's own archives and VSIX, and publishes only if they pass, the Remote-SSH test included ([manual testing](manual-testing.md#vs-code)); that is release policy, not a required check. Making any of these jobs required is a maintainer decision. Merging a change to the script does not apply it; a maintainer re-runs the script to update branch protection.

CI runs on every pull request, by hand, and for each push to `main`. The push trigger keeps the README's CI badge current. Because `main` requires pull requests to be up to date and merges squash them, a push usually carries exactly the tree that its pull request's CI passed on. A push-only check skips the build and test jobs only when a merged pull request associated with the pushed commit has the same tree at its head and a successful `pull_request` run of `ci.yml` on that head; the push run then reports success for that commit. Without that evidence, or if the check fails, the normal jobs remain eligible. Pull requests and manual runs skip the check and keep the normal jobs eligible too. Dependent editor jobs still require their build job to succeed, and cancellation prevents guarded jobs from starting. Required check names are unchanged.

Apple Silicon support will move to a native `arm64-darwin` build once Virgil supports that target. Rosetta 2 is needed until then. Install it with:

```sh
softwareupdate --install-rosetta --agree-to-license
```

## Where the server runs

The language server runs on the machine that holds the files, which isn't always the machine showing the editor.

| Setup | `virgil-lsp` runs on | Install it on |
| --- | --- | --- |
| Editor on a Linux machine | That Linux machine | Linux |
| VS Code Remote-SSH, or Neovim over SSH | The remote Linux machine | The remote machine |
| VS Code Remote-WSL, or Neovim inside WSL | Inside WSL 2 | The WSL distribution |
| Editor on a Mac, local files | The Mac | macOS |

The [manual test matrix](manual-testing.md) records which of these setups were tried with each editor for each release. [Known limitations](known-limitations.md#platforms-and-editors) lists what the platforms and editors don't support.

## Editors

| Editor | Minimum version | Status |
| --- | --- | --- |
| Neovim (plain config) | 0.11 (`vim.lsp.config`) | Headless smoke test: 0.11.6 locally and pinned in Linux CI; macOS CI uses Homebrew Neovim (0.11+) and logs its version |
| LazyVim | 0.11 (`vim.lsp.config`) | Configuration provided. The [LazyVim test](../test/e2e/lazyvim/README.md#ci) runs on Linux with Neovim 0.11.6 on every pull request as a [required check](#host-platforms), and weekly and on each release's archives, on Linux with Neovim 0.11.6 and 0.12.6 and on macOS. A scripted run on Linux with LazyVim 16.0.1 passed for 0.1.0-alpha.1, and for 0.1.0-alpha.2 with rename, completion through blink.cmp 1.10.2, and signature help for a call still being typed, also on macOS with Neovim 0.12.4, restarting with `:lsp restart`. For 0.1.0-alpha.3, the LazyVim test passed on Linux with Neovim 0.11.6 and 0.12.4, and on macOS with Neovim 0.12.4, with its one expected failure: noice.nvim drops the missing-root warning ([results](manual-testing.md#results)) |
| VS Code, including Remote-SSH and Remote-WSL | 1.91 (required by `vscode-languageclient` 11) | [Prerelease extension](../clients/vscode/README.md), [installed from a VSIX](../clients/vscode/README.md#installing-from-a-vsix), type-checked and lifecycle-tested in Linux CI, which also runs the [Extension Host test](../clients/vscode/README.md#extension-host-test) with VS Code 1.142.0, both on the extension loaded from source and on the packaged VSIX installed with `--install-extension`. macOS CI (Apple Silicon, under Rosetta 2) runs it on the extension loaded from source, and a weekly workflow on the packaged VSIX. See the [release runbook](releasing.md#how-the-workflow-works) for bundled and universal VSIX test coverage. The weekly workflow, on a packaged VSIX, and the release workflow, on the release's Linux platform VSIX, also run it through Remote-SSH, from a Linux client to the same machine ([Remote-SSH test](../clients/vscode/README.md#remote-ssh-test)). A [manual checklist](../clients/vscode/README.md#manual-checklist) covers Linux, Remote-SSH, Remote-WSL, and macOS. It was last run on Linux with VS Code 1.140 ([PR #75](https://github.com/SaltedTan/virgil-language-server/pull/75)), before the Virgil-root and project-file steps were added. For 0.1.0-alpha.2 and 0.1.0-alpha.3, the Extension Host test also passed on macOS (Apple Silicon) with VS Code 1.142.0, on each release's VSIX ([results](manual-testing.md#results)). For 0.1.0-alpha.3, it also passed through Remote-SSH, from a Linux client to a Linux host, with the release's VSIX installed on the remote ([results](manual-testing.md#010-alpha3)) |

Editor minimum versions may rise in a minor release, with a `CHANGELOG.md` notice ([support policy](#how-changes-are-announced)).

Helix is not in this matrix. A best-effort, grammar-free [Helix configuration](../editors/helix/README.md) registers the released server without a Tree-sitter grammar and is not tested in CI.

Server and VS Code extension versions are independent, and the extension's version doesn't follow the server's ([versions](../clients/vscode/README.md#versions)). Each server prerelease also attaches the VSIX of the extension's version at the tagged commit, tested against that release's Linux server, and its release notes give both versions ([Releases](releasing.md#vs-code-extension)). Starting with extension `0.1.3`, platform VSIX files bundle the tested server, worker, and pinned library; the universal VSIX needs an external server. An explicit `virgil.server.path` takes precedence over a bundle. This table records which versions work together.

The extension can be installed alongside `titzer.virgil`, the Virgil author's extension. [With the Virgil extension installed](../clients/vscode/README.md#with-the-virgil-extension-installed) describes what VS Code 1.142.0 did in a test with both installed, and what that test didn't cover.

| VS Code extension | Server versions |
| --- | --- |
| `0.1.4` (prerelease VSIX, attached to server `0.1.0-alpha.5`) | `0.1.0-alpha.5` and `main`, whose servers also answer semantic tokens, selection ranges, folding ranges, the missing-semicolon quick fix, type hierarchy, and call hierarchy; `0.1.0-alpha.4` and `0.1.0-alpha.3`, without them; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root (below). The `virgil.semanticTokens` setting requires a server that implements semantic tokens; older servers ignore this initialization option. |
| `0.1.3` (prerelease VSIX, attached to server `0.1.0-alpha.4`) | `0.1.0-alpha.5`, `0.1.0-alpha.4`, and `main`, whose servers also answer document highlights, type definition, implementation, and inlay hints, and `0.1.0-alpha.5`'s and `main`'s also semantic tokens, selection ranges, folding ranges, the missing-semicolon quick fix, type hierarchy, and call hierarchy; `0.1.0-alpha.3`, without them; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root (below). The inlay-hint settings require a server that implements inlay hints; older servers ignore these initialization options and provide no hints. |
| `0.1.2` (prerelease VSIX, attached to server `0.1.0-alpha.3`) | `0.1.0-alpha.4`, `0.1.0-alpha.3`, and `main`; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root (below) |
| `0.1.1` (prerelease VSIX, attached to server `0.1.0-alpha.2`) | `0.1.0-alpha.4` and `0.1.0-alpha.3`; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root |
| `0.1.0` (prerelease VSIX, attached to server `0.1.0-alpha.1`) | `0.1.0-alpha.4` and `0.1.0-alpha.3`; `0.1.0-alpha.2` and `0.1.0-alpha.1` only with a Virgil root |

The three earlier extension versions (`0.1.0`–`0.1.2`) have the same code, and differ only in their version numbers and READMEs; `0.1.3` adds the platform VSIX files that bundle the server, worker, and pinned Virgil library, the inlay-hint settings, and fixes for restarts during crash recovery, failed pipe writes, and lost framing. `0.1.4` uses `vscode-languageclient` 11.0.0, which sends every open document's `didOpen` as the client starts, before any request, so breadcrumbs survive a restart, drops a request that would follow `shutdown`, and adds the `virgil.semanticTokens` setting. The extension relies only on standard LSP: full-text document synchronization, `publishDiagnostics`, `textDocument/documentSymbol`, `textDocument/definition`, `textDocument/hover`, `textDocument/references`, `textDocument/signatureHelp`, `workspace/symbol`, file events (`workspace/didChangeWatchedFiles`), and workspace-folder changes. With `0.1.0-alpha.2` and later, it also uses `textDocument/prepareRename` and `textDocument/rename` with versioned `documentChanges`, and `textDocument/completion`, whose `completionProvider` declares the trigger character `.` for [member completion](architecture.md#member-completion), none of which `0.1.0-alpha.1` answers. With `0.1.0-alpha.4` and later, it also uses `textDocument/documentHighlight` ([document highlights](architecture.md#document-highlights)), `textDocument/typeDefinition` ([resolved type navigation](architecture.md#type-definition)), `textDocument/implementation` ([implementation navigation](architecture.md#implementation)), and `textDocument/inlayHint` ([inlay hints](architecture.md#inlay-hints)), which `0.1.0-alpha.3` and earlier don't answer. With `0.1.0-alpha.5` and `main`, it also uses `textDocument/codeAction` ([the missing-semicolon quick fix](architecture.md#code-actions)), `textDocument/prepareTypeHierarchy`, `typeHierarchy/supertypes`, and `typeHierarchy/subtypes` ([type hierarchy](architecture.md#type-hierarchy)), and `textDocument/prepareCallHierarchy`, `callHierarchy/incomingCalls`, and `callHierarchy/outgoingCalls` ([call hierarchy](architecture.md#call-hierarchy)), which `0.1.0-alpha.4` and earlier don't answer. On `main`, it also uses `completionItem/resolve` for [completion item documentation](architecture.md#item-documentation). It needs `virgil-lsp-worker` beside `virgil-lsp`, which its startup checks verify; a platform VSIX carries both.

With `0.1.0-alpha.5` and `main`, it also uses `textDocument/selectionRange` to expand selections through the current document's tokens, bracket groups, and declarations ([Selection ranges](architecture.md#selection-ranges)).

With `0.1.0-alpha.5` and `main`, it also uses `textDocument/semanticTokens/full` and, when its client supports it, `workspace/semanticTokens/refresh` ([Semantic tokens](architecture.md#semantic-tokens)). The extension's `virgil.semanticTokens` setting sends `initializationOptions.semanticTokens`, which servers without semantic tokens ignore.

With `0.1.0-alpha.5` and `main`, it also uses `textDocument/foldingRange` for syntax-based block and comment folds ([Folding ranges](architecture.md#folding-ranges)).

With a server before `0.1.0-alpha.3`, every extension version needs a Virgil root. When neither `virgil.virgilRoot` nor `VIRGIL_LOC` gives an absolute path, `0.1.0-alpha.1` and `0.1.0-alpha.2` exit as they start, so VS Code has no language server, not even for single files ([#175](https://github.com/SaltedTan/virgil-language-server/issues/175)). Set `virgil.virgilRoot` to a Virgil checkout ([The Virgil root](releasing.md#the-virgil-root)), or install `0.1.0-alpha.3`, which starts without a root.

Each release workflow tests the VSIX it attaches with its own server, in a new VS Code profile. For upgrade and rollback evidence from published VSIX files, see the [rehearsal results](manual-testing.md#upgrade-and-rollback-rehearsals). The Extension Host test also passed with `0.1.0` and `0.1.0-alpha.2`'s server, and with `0.1.1` and `0.1.0-alpha.1`'s ([results](manual-testing.md#010-alpha2)). For `0.1.0-alpha.3`, it passed with `0.1.0` and with `0.1.1` on that release's server, with that release's test, no-root checks included. It also passed with `0.1.2` on the `0.1.0-alpha.2` and `0.1.0-alpha.1` servers, each time with the test of the server's own release, which always sets a Virgil root. `0.1.0-alpha.3`'s test, which also starts the server without a root, failed only there with `0.1.1` on the `0.1.0-alpha.2` server ([results](manual-testing.md#010-alpha3)).
