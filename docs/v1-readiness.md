# v1.0 readiness

This page is the evidence ledger for the roadmap's [v1.0 gates](../ROADMAP.md#v10-is-ready-when). It traces each of the six detailed readiness clauses to what would show that a v1.0 candidate meets it: the scope, an observable pass condition, the command or accepted policy that checks it, the evidence recorded for the candidate, the issues still open, and the clause's state.

It is not a release gate. The maintainer approved it on 2026-11-01 as a ledger only ([#337](https://github.com/SaltedTan/virgil-language-server/issues/337)). Adopting it as a release gate is a later decision, after the configuration contract ([#339](https://github.com/SaltedTan/virgil-language-server/issues/339)), the support policy ([#340](https://github.com/SaltedTan/virgil-language-server/issues/340)), and the benchmark resource checks ([#347](https://github.com/SaltedTan/virgil-language-server/issues/347)). Prereleases before v1.0 follow the [release runbook](releasing.md) and don't need this page.

No v1.0 candidate exists yet, so every clause is *not ready*. The ledger records no new policy, no new test, and no new number.

## How to read it

Each clause has one of three states:

- **Not ready:** the candidate lacks evidence that the pass condition holds, or the policy that defines the condition isn't adopted.
- **Satisfied:** the candidate's own evidence, recorded in the ledger, shows the pass condition.
- **Approved exception:** the maintainer accepted a named gap for this candidate. An exception records its owner, its issue, its impact on users, and when it expires or is checked again.

A row marked *pending* depends on a policy that the maintainer hasn't adopted. It stays not ready until the policy is adopted and the candidate meets it.

These rules apply to every row:

- Evidence counts only for the candidate's exact commit and its own build. Measurements and runs of an earlier commit or release don't qualify a new candidate, however recent they are. The rows below name earlier runs as examples of the evidence a candidate needs, not as evidence for it.
- Missing evidence isn't a pass. A check that was skipped, not run, or not recorded leaves its row not ready.
- A rerun that passes doesn't erase a product defect. If a failure was caused by the server or the extension, it needs a fix or an approved exception; only a failure shown to come from outside the repository, such as a runner or a download, can be rerun away.
- A proposal, a draft, or an advisory heuristic isn't an adopted policy.

## The candidate

A candidate's evidence starts with what was built and from what. Record these for the candidate before any row can be satisfied:

| Field | Where it comes from |
| --- | --- |
| Candidate commit | The full SHA of the commit to be tagged, and the release workflow run that tested it |
| Pinned Virgil revision | `virgil-lsp --version`, the `vendor/virgil` gitlink, the release manifests' `virgilRevision` and `library.revision`, and the [Virgil revision table](compatibility.md#virgil-revision), which must agree |
| Server version | `VERSION`, and the first line of `virgil-lsp --version` |
| Extension version | `clients/vscode/package.json`, and the VSIX files' `extension/package.json` |
| Platforms | The hosts and runner images of each recorded run, against the [host platforms](compatibility.md#host-platforms) table |
| Corpus hashes | The SHA-256 of `test/benchmarks/corpus.json`, `test/benchmarks/tolerance-inventory.json`, and `test/benchmarks/cursor-tolerance-histories.json` at the candidate commit |
| Executables | The SHA-256 of `virgil-lsp` and `virgil-lsp-worker` in each archive's manifest, and of the build used for each benchmark capture |

No candidate is recorded yet.

## Themes and clauses

The roadmap's v1.0 goal names five themes: a stable configuration format, defined performance budgets, robust error recovery, completion, references, and rename suitable for everyday use, and a documented compatibility policy for Virgil versions. Its release gates state six detailed clauses. The ledger has one row for each clause:

| Clause | Theme |
| --- | --- |
| 1. The configuration schema and compatibility policy are stable. | Stable configuration format; compatibility policy |
| 2. References, safe rename, signature help, and useful completion are shipped. | Completion, references, and rename for everyday use |
| 3. Incomplete syntax does not routinely destroy all language intelligence. | Robust error recovery |
| 4. Performance budgets are based on published benchmark fixtures and pass in CI or scheduled runs. | Defined performance budgets |
| 5. Release artifacts cover the documented platform matrix and clean-install tests. | None of the five; release and installation |
| 6. At least one release cycle has tested upgrades, rollback, and compatibility with newer Virgil revisions. | Compatibility policy; release and installation |

## Ledger

| Clause | Scope | Observable pass condition | Command or accepted policy | Candidate evidence | Unresolved issues | State |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Configuration and compatibility | Version 1 of `.virgil-lsp.json`: its fields, strict parsing, patterns, file selection, and active projects. The support policy: one pinned Virgil revision per release, the bundled library, the worker from the same build, the extension pairs, editor minimums, fixes, and how changes are announced. | The candidate passes the tests that the configuration contract's inventory and the support policy's evidence table name, and every record of its pinned revision agrees. | Adopted: the [configuration contract](configuration.md#stability-and-evolution) ([ADR-0012](decisions/0012-project-configuration-stability.md)) and the [support policy](compatibility.md#support-policy) ([ADR-0014](decisions/0014-compatibility-and-support-policy.md)). Checked by `make test`, `npm test` in `clients/vscode`, and the compatibility review in [Making a release](releasing.md#making-a-release). | None. | [#339](https://github.com/SaltedTan/virgil-language-server/issues/339) and [#340](https://github.com/SaltedTan/virgil-language-server/issues/340) are closed: their policies were merged on 2026-11-01 in [PR #423](https://github.com/SaltedTan/virgil-language-server/pull/423) and [PR #426](https://github.com/SaltedTan/virgil-language-server/pull/426). Some contract behaviors have no test of their own, and no test sends a worker handshake with another protocol version, server version, or source revision. | Not ready: policy adopted; candidate evidence missing. |
| 2. Everyday use | References, prepare rename and rename, signature help, and completion in configured, target-independent programs within the documented limits; in open and closed files, and with unsaved edits. Completeness is bounded by the active project, excluded method bodies, and resource refusals. | The candidate passes the unit tests and transcripts for these features in `make test`, and their editor checks in every lane that the release workflow runs. | No separate policy; the feature behavior is documented in [architecture](architecture.md) and its limits in [known limitations](known-limitations.md). Checked by `make test`. | None. | [#327](https://github.com/SaltedTan/virgil-language-server/issues/327) is fixed by [PR #329](https://github.com/SaltedTan/virgil-language-server/pull/329). Its tests need to pass on the candidate; it needs no second fix. | Not ready: candidate evidence missing. |
| 3. Incomplete syntax | Phase 3a within [ADR-0010](decisions/0010-analyze-unrepairable-files.md#decision): the bounded open-body scope of [No error-tolerant parsing](known-limitations.md#no-error-tolerant-parsing), with worker failures and stale answers. Phase 3b is out of scope. | A full tolerance capture of the candidate passes the release scorecard, and the worker-failure and stale-answer tests pass. | Accepted: ADR-0010's v1.0 gate for M6 and the [release scorecard](benchmarks.md#release-scorecard). Checked by `scripts/bench-tolerance.py`, then `scripts/check-tolerance.py`, and `make test`. | None. | [#335](https://github.com/SaltedTan/virgil-language-server/issues/335) is closed: the scorecard was merged in [PR #371](https://github.com/SaltedTan/virgil-language-server/pull/371). | Not ready: candidate evidence missing. |
| 4. Performance budgets | Latency and memory of the published benchmark fixtures, in CI or scheduled runs. | An adopted budget table passes on the candidate, as the details below describe. | Pending: no budget is adopted. [performance-budgets.md](performance-budgets.md) is a proposal, and the [scheduled comparison](benchmarks.md#scheduled-comparison) is advisory. | None. | [#345](https://github.com/SaltedTan/virgil-language-server/issues/345) is closed with a proposal that isn't adopted. [#347](https://github.com/SaltedTan/virgil-language-server/issues/347) is open and waits for the maintainer to adopt resource thresholds. | Not ready: pending policy. |
| 5. Artifacts and clean install | The release assets for the [host platforms](compatibility.md#host-platforms), the clean install test, and the editor tests on the release's own archives and VSIX. | The candidate's release workflow passes every job, including the clean install test and the editor tests, and the existing editor policy holds. | Adopted release policy: the [release workflow](releasing.md#how-the-workflow-works) and the [editor evidence contract](../test/e2e/RESULTS.md#the-release-gate). Which editor lanes a v1.0 gate requires is pending. | None. | [#348](https://github.com/SaltedTan/virgil-language-server/issues/348) is open; [PR #424](https://github.com/SaltedTan/virgil-language-server/pull/424), which prepares the Linux LazyVim job as a required check, isn't merged. [#211](https://github.com/SaltedTan/virgil-language-server/issues/211) and [#212](https://github.com/SaltedTan/virgil-language-server/issues/212) are open and wait for `vscode-languageclient` releases. [#341](https://github.com/SaltedTan/virgil-language-server/issues/341) is closed: the headless Neovim test writes a result stream, and the other editor tests don't yet. | Not ready: candidate evidence missing; lane requirements pending. |
| 6. A release cycle | Upgrading from the previous release to the candidate and rolling back, and compatibility with Virgil revisions newer than the pin. | At least one completed release cycle on the way to v1.0 recorded an upgrade and rollback rehearsal that passed, and newer-Virgil evidence for the same cycle. | The opt-in [upgrade and rollback rehearsal](releasing.md#upgrade-and-rollback-rehearsal), the advisory `virgil-master` workflow, and the [pin update procedure](../CONTRIBUTING.md#updating-the-pinned-virgil-revision). | None. | [#344](https://github.com/SaltedTan/virgil-language-server/issues/344) is closed: the rehearsal exists, but it is opt-in, and no release workflow runs it. | Not ready: no cycle recorded for a candidate. |

## Details

Each section states what the row's pass condition needs, what evidence sources exist, and what earlier evidence shows about the form the candidate's evidence takes.

### 1. Configuration and compatibility

[ADR-0012](decisions/0012-project-configuration-stability.md) and the [configuration contract](configuration.md#stability-and-evolution) were adopted on 2026-11-01 ([#339](https://github.com/SaltedTan/virgil-language-server/issues/339), [PR #423](https://github.com/SaltedTan/virgil-language-server/pull/423)). From server 1.0 on, every 1.x release reads version 1 files as the contract specifies: their fields, types, defaults, and the version literal; strict parsing, in which unknown and repeated fields are errors and a file with any error defines no projects; the pattern syntax and how patterns select, order, own, and exclude files; and the Virgil root's precedence and the choice of each file's active project. Language options follow the pinned compiler, not the format, and capacity limits may change in any release with a changelog entry. Before 1.0, a prerelease may still change the rules, and its notes say so.

[ADR-0014](decisions/0014-compatibility-and-support-policy.md) and the [support policy](compatibility.md#support-policy) were adopted on 2026-11-01 ([#340](https://github.com/SaltedTan/virgil-language-server/issues/340), [PR #426](https://github.com/SaltedTan/virgil-language-server/pull/426)). A release supports the one Virgil revision it was built with; upstream `master` is only an advisory signal. It supports the bundled library when its `REVISION` matches, only the worker from its own build, a platform VSIX with the server it bundles, and the editor minimums in [Editors](compatibility.md#editors). Fixes go into the next release of the current minor line, with no backports. From 1.0, an incompatible change to the public command line or the initialization options needs a new server major version, and one to a client setting a new extension major version.

The candidate needs:

- `make test` passing at the candidate commit on both required hosts. It runs every test that the contract's [inventory](configuration.md#inventory) and the policy's [evidence table](compatibility.md#evidence) name. While working on one area, `make test-unit ARGS='ProjectConfig:* ProjectExpansion:* WorkspaceProjects:*'` runs the configuration's unit tests alone.
- `npm test` in `clients/vscode` passing, for the client's server selection, broken-bundle, and mismatched-host tests.
- The compatibility review of [Making a release](releasing.md#making-a-release): the candidate's row in the Virgil revision table agrees with `virgil-lsp --version`, the gitlink, `THIRD_PARTY_NOTICES.md`, the corpus's `virgil_source_revision`, the dry run's manifests, the bundled `virgil/REVISION`, and the release notes; and `CHANGELOG.md` announces every change that the policy names.

Open facts that the gate decision should weigh: the inventory marks several behaviors as gaps with no test of their own, such as case-sensitive pattern matching and the configuration-text limit; and no test sends a worker handshake with another protocol version, server version, or source revision.

### 2. Everyday use

These features shipped in `0.1.0-alpha.2`, and the roadmap's question for v1.0 is whether they are suitable for everyday use. Their answers are bounded by rules that the ledger keeps as they are:

- Semantic answers come from the file's active project, chosen as the [configuration contract](configuration.md#stability-and-evolution) says. A file outside every project gets no semantic answers ([Semantic features need a project file](known-limitations.md#semantic-features-need-a-project-file)).
- Analysis is target-independent: code written for a native target reports unresolved machine-level types ([support policy](compatibility.md#what-a-release-supports)).
- References and rename find uses in the analyzed program only. A method body excluded by fallback or repair gives no answers, and reference lists can be partial then ([No error-tolerant parsing](known-limitations.md#no-error-tolerant-parsing)). Rename refuses what [What rename refuses](known-limitations.md#what-rename-refuses) lists, and is refused when a document or the project's files changed while it waited or while its edits were checked ([Stale results](known-limitations.md#stale-results)).
- Completion and signature help in code that doesn't parse work only where one repair at the cursor makes it parse. When an analysis is refused or replaced, completion offers keywords only, marked incomplete so that the editor asks again ([Stale results](known-limitations.md#stale-results)).
- Programs must stay within the [supported program size](known-limitations.md#supported-program-size).

The candidate needs these tests passing in `make test`, which they already are part of:

- The unit tests of each feature, run alone with `make test-unit ARGS='LspReferences:* LspPrepareRename:* LspRename:* LspSignatureHelp:* LspSignatureRepair:* LspCompletion:* LspCompletionScope:* LspCompletionMembers:*'`. They use configured project fixtures with the real worker. Among them, `LspReferences:file_changed_on_disk`, `LspRename:closed_file_changed_while_checked`, `LspCompletion:closed_document`, and `LspSignatureHelp:target_changed_on_disk` cover closed files and changes on disk, and `LspRename:changed_while_waiting` and `LspCompletionScope:stale_reruns_once` cover unsaved edits made while a request waits or runs.
- The diagnostics transcripts [`references.jsonl`](../test/diagnostics/references.jsonl), [`rename.jsonl`](../test/diagnostics/rename.jsonl), [`signature-help.jsonl`](../test/diagnostics/signature-help.jsonl), [`completion.jsonl`](../test/diagnostics/completion.jsonl), [`member-completion.jsonl`](../test/diagnostics/member-completion.jsonl), and [`completion-insert-replace.jsonl`](../test/diagnostics/completion-insert-replace.jsonl).
- The editor checks of the references, rename, signature help, and completion areas in [`coverage.json`](../test/e2e/coverage.json), such as `vscode.rename.apply-undo`, `lazyvim.completion.local`, and `nvim.references.library`, in every lane that the release workflow runs.

[#327](https://github.com/SaltedTan/virgil-language-server/issues/327) is fixed by [PR #329](https://github.com/SaltedTan/virgil-language-server/pull/329), merged on 2026-10-28 as `ced30385e75b9530cc980efcb15bdf926f549769`. A completion that answers with keywords alone, or with an empty list after `.`, now says `isIncomplete: true` when the names are missing for a reason that a later request may not have, so the client asks again as the user types. Its deterministic fallback tests are `LspCompletion:keyword_only_complete`, `LspCompletionScope:submit_refused`, and `LspCompletionScope:other_file_unparsed`, and the completion scheduling tests assert `isIncomplete` on every fallback. The `0.1.0-alpha.5` dry run's macOS LazyVim failure, "completion: the local line first as you type li (blink.cmp)", predates the fix ([results](manual-testing.md#010-alpha5)). The candidate needs these tests and that LazyVim check passing again; it needs no second fix and no timing-dependent transcript.

### 3. Incomplete syntax

[ADR-0010](decisions/0010-analyze-unrepairable-files.md#decision) sets the scope, and the ledger keeps it as it is:

- The scope is the bounded open-body scope of [No error-tolerant parsing](known-limitations.md#no-error-tolerant-parsing): a syntax error inside a method or constructor body, where the damaged body gets no semantic answer and answers outside it are kept.
- Rule 2 reads a missing `{` only where the file's indentation places it. When every brace pair of the intended file agrees with its indentation, rule 2 gives no wrong answer; when the file's own indentation disagrees with its braces near the damage, a lost `{` can be read wrongly.
- Rule 3, as redesigned in [#320](https://github.com/SaltedTan/virgil-language-server/issues/320), inserts no `}` and reads no layout. It accepts only when every reading that parses leaves the lost `}` in the same method or constructor body, refuses a method's own lost `}`, and refuses a file that would need more than 16 parses beyond the three-parse budget.
- Phase 3b, recovery of damaged declaration headers, field initializers, and truncated files, is deferred and not needed for v1.0.

The candidate needs a full capture of the tolerance benchmark that passes the [release scorecard](benchmarks.md#release-scorecard). The scorecard was added for [#335](https://github.com/SaltedTan/virgil-language-server/issues/335) in [PR #371](https://github.com/SaltedTan/virgil-language-server/pull/371). Capture and check are separate commands, and the checker refuses a `--smoke`, `--suite`, or `--only` capture:

```sh
mkdir -p build/bench-results/v1-tolerance
make bench-build
python3 scripts/bench-tolerance.py --exe build/bench/virgil-lsp --cpu 12 \
  --work-dir build/bench-results/v1-tolerance --output build/bench-results/v1-tolerance.json
python3 scripts/check-tolerance.py build/bench-results/v1-tolerance.json
```

Run the capture under the shared benchmark lock, as [Semantics during syntax errors](benchmarks.md#semantics-during-syntax-errors) describes. The checker exits nonzero unless the capture is complete and every per-mode floor holds, with zero wrong answers and zero control-expectation mismatches. That covers the negative controls, the held-out states that the rules weren't built from, the editing sessions, and the stale-predecessor injections, which must all be rejected. It prints a floor of zero as an uncovered class, not as a pass. The full run of 2026-10-29 at `ced30385` passed; it shows the form of the evidence, but doesn't qualify a later candidate.

Worker failures and stale answers are covered in `make test` by the `AnalysisSupervisor:*` unit tests for a crash, a hang, a malformed result, and worker replacement; by the stale-answer tests of each feature, such as `LspReferences:stale_waits_for_analysis` and `LspSignatureHelp:stale_without_analysis`; and by the worker-crash tests of completion, signature help, and rename.

The [cursor tolerance histories](benchmarks.md#cursor-tolerance-histories-2026-10-31) ([#342](https://github.com/SaltedTan/virgil-language-server/issues/342)) measure completion and signature help where the user types, through fixed typing histories. Their report shows where those features stop answering at damaged steps. It is evidence of the scope's boundaries, opt-in and outside `make test`; it adds no recovery behavior that v1.0 needs.

### 4. Performance budgets

No budget is adopted, so this row is pending. The clause can't be satisfied until the maintainer adopts all of these, and the candidate passes a run checked against them:

- a budget table;
- the qualified host and workloads that it applies to;
- the endpoints, percentiles, and sample rules of each measurement;
- the definitions of the memory that is measured;
- a failure and retest procedure;
- a run of the candidate in CI or a scheduled run, checked against the table.

[performance-budgets.md](performance-budgets.md) is a measured proposal from [#345](https://github.com/SaltedTan/virgil-language-server/issues/345), which is closed. It is not adopted: its numbers, reference environments, and enforcement policy are candidates for a decision, not budgets, and the ledger doesn't repeat them. The [weekly scheduled comparison](benchmarks.md#scheduled-comparison) is advisory; its change heuristic only produces warning annotations, so it can't satisfy the clause alone. [#347](https://github.com/SaltedTan/virgil-language-server/issues/347), which would add deterministic allocation and worker-collection checks to that workflow, is open and waits for the maintainer to adopt its thresholds.

The existing evidence sources, once a policy names them:

- `make bench`, which runs `scripts/bench-lsp.py --require-probes` on a `BENCH=1` build ([Run](benchmarks.md#run));
- `python3 scripts/bench-corpus.py --check`, the corpus pin check;
- `scripts/bench-compare.py --base-ref <ref> --new-ref <ref> --work-dir <dir> --output-dir <dir>`, the comparison of two builds that the scheduled workflow runs;
- `scripts/bench-memory-soak.py`, the opt-in [memory soak](benchmarks.md#memory-soak).

### 5. Artifacts and clean install

The documented platform matrix is the [host platforms](compatibility.md#host-platforms) table: Linux x86-64 is supported; Apple Silicon macOS is supported, using the Intel build under Rosetta 2; WSL 2 and Intel macOS are best effort; native Windows, Linux arm64, and WSL 1 aren't supported. A release has an archive and a manifest for each of the two builds, a VSIX for `linux-x64`, `darwin-arm64`, and `darwin-x64`, a universal VSIX without a server, and `SHA256SUMS` ([What a release contains](releasing.md#what-a-release-contains)).

The candidate needs a [release workflow](releasing.md#how-the-workflow-works) run in which every job passes: the build, `make test`, packaging, and the archive smoke test on both hosts; the VSIX packaging checks and the Extension Host test; the editor tests on the run's own archives and VSIX; the [clean install test](releasing.md#clean-install-test); and the checksums. A [dry run](releasing.md#dry-runs) on the release branch and on `main` comes first, with `gh workflow run release.yml --ref main`.

The existing editor policy stays as it is ([host platforms](compatibility.md#host-platforms), [LazyVim CI](../test/e2e/lazyvim/README.md#ci)):

- The macOS Extension Host test runs in pull-request CI.
- The Linux LazyVim job, `lazyvim-linux`, runs on every pull request and stays advisory until it has passed 20 runs in a row. On 2026-11-01 the maintainer decided that only first-attempt successes of distinct pull-request runs that executed the job count. Making it a required check is in progress in [#348](https://github.com/SaltedTan/virgil-language-server/issues/348) and its pull request, [PR #424](https://github.com/SaltedTan/virgil-language-server/pull/424), which isn't merged; changing branch protection is the maintainer's.
- The weekly editor tests and each release run the full matrix: LazyVim on Linux with Neovim 0.11 and 0.12 and on macOS, the Extension Host test on macOS with an installed VSIX, and the Remote-SSH test on Linux.
- Each release runs the [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test), three times with the `linux-x64` VSIX.

The [editor evidence contract](../test/e2e/RESULTS.md) defines the result stream that a release's editor results are generated from. It is a contract, not proof that every test writes it: only the headless Neovim test writes a stream, with `test/e2e/nvim/run.sh --results FILE` ([its writer](../test/e2e/RESULTS.md#the-headless-neovim-writer), from [#341](https://github.com/SaltedTan/virgil-language-server/issues/341)), and `python3 scripts/editor-results.py validate STREAM...` checks one. The LazyVim, Extension Host, and Remote-SSH tests still print only their `PASS` and `FAIL` lines. The clean install test is outside the contract; its evidence is its workflow log and its `clean-install-linux` artifact, kept for 30 days.

The clean install test covers Linux x86-64 only. The macOS archive and VSIX get the archive smoke test and the editor tests, but no clean-install check ([Clean install test](releasing.md#clean-install-test)).

Two client library issues follow the published state of `vscode-languageclient`, whose latest release, 11.0.0, the extension uses:

- [#211](https://github.com/SaltedTan/virgil-language-server/issues/211) is open. Restarting the server during a pending edit logs `Delivering pending changes failed: TypeError: this.task is not a function`; no text is lost, and [known limitations](known-limitations.md) documents it. Upstream fixed it on its main branch in [microsoft/vscode-languageserver-node#1920](https://github.com/microsoft/vscode-languageserver-node/pull/1920), but no release has the fix yet.
- [#212](https://github.com/SaltedTan/virgil-language-server/issues/212) is open. The library can send a request after `shutdown` when a stop races a pending request. It is reported upstream as [microsoft/vscode-languageserver-node#1921](https://github.com/microsoft/vscode-languageserver-node/issues/1921), which is open. The extension guards against it since [PR #319](https://github.com/SaltedTan/virgil-language-server/pull/319), so nothing reaches the server after `shutdown`.

### 6. A release cycle

The clause needs a completed release cycle, not a single run. In one cycle on the way to v1.0, the candidate needs:

- **Upgrade and rollback.** The [upgrade and rollback rehearsal](releasing.md#upgrade-and-rollback-rehearsal) passing from the previous release's published assets to the candidate's and back, with its receipt and logs recorded in [Results](manual-testing.md#upgrade-and-rollback-rehearsals). It is opt-in: `make test`, CI, and the release workflow don't run it. Its command is `xvfb-run -a python3 test/e2e/upgrade/run.py --from-assets <dir> --to-assets <dir> --logs <dir>` on Linux x86-64. It covers only Linux x86-64, the archive's symlink layout, and VSIX files installed from disk into one local VS Code profile.
- **Newer Virgil revisions.** The supported compiler is the candidate's one pinned revision ([support policy](compatibility.md#what-a-release-supports)). Compatibility with newer revisions is advisory evidence, kept apart from that: the weekly `virgil-master` workflow builds and tests against current upstream `master` and blocks nothing; and a pin update during the cycle follows the [pin update procedure](../CONTRIBUTING.md#updating-the-pinned-virgil-revision), with `make clean test`, the regenerated corpus, and a short benchmark, as the [pinned revision comparison](benchmarks.md#pinned-virgil-revision-comparison-2026-10-27) did for the current pin.

[#344](https://github.com/SaltedTan/virgil-language-server/issues/344) added the rehearsal and is closed. Its recorded run of 2026-10-31 at `faa41901` passed from `0.1.0-alpha.4` to `0.1.0-alpha.5` and back, across the pin change from Virgil `7cf038fc` to `57318d37`, and three runs with deliberately broken installations failed as expected. That run shows what the evidence looks like, but it isn't a v1.0 candidate's cycle.

## Approved exceptions

None. An exception needs the maintainer's approval for a named candidate, and records:

| Clause | Owner | Issue | Impact | Expires or rechecked |
| --- | --- | --- | --- | --- |

## Optional work

These stay optional for v1.0 unless the maintainer changes the scope. None of them blocks a clause above:

- publishing to the VS Code Marketplace ([#192](https://github.com/SaltedTan/virgil-language-server/issues/192)), and an `nvim-lspconfig` configuration and Mason entry;
- a native Apple Silicon build ([#191](https://github.com/SaltedTan/virgil-language-server/issues/191));
- phase 3b of error tolerance ([ADR-0010](decisions/0010-analyze-unrepairable-files.md#decision));
- semantic token deltas ([#324](https://github.com/SaltedTan/virgil-language-server/issues/324));
- the Remote-SSH check from a Mac client, which is deferred ([#186](https://github.com/SaltedTan/virgil-language-server/issues/186)).

## Questions for the gate decision

The ledger leaves these to the maintainer's decision on adopting it as a gate:

- which editor lanes, at which pinned editor versions, a v1.0 release requires, which the [editor evidence contract](../test/e2e/RESULTS.md#the-release-gate) says comes with the gate itself;
- whether the Linux-only clean install test, with the macOS archive and editor tests, covers clause 5 for macOS;
- whether the upgrade and rollback rehearsal stays opt-in with a recorded result, or joins the release workflow;
- the performance budget policy, through the proposal's [adoption decision table](performance-budgets.md#adoption-decision-table), and then [#347](https://github.com/SaltedTan/virgil-language-server/issues/347).
