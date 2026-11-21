# v1.0 readiness

This page is the evidence ledger for the roadmap's [v1.0 gates](../ROADMAP.md#v10-is-ready-when). It traces each of the six detailed readiness clauses to what would show that a v1.0 candidate meets it: the scope, an observable pass condition, the command or accepted policy that checks it, the evidence recorded for the candidate, the issues still open, and the clause's state.

It is the v1.0 release gate: a v1.0 release needs a candidate whose every clause is satisfied or covered by an approved exception. The maintainer approved it on 2026-11-01 as a ledger only ([#337](https://github.com/SaltedTan/virgil-language-server/issues/337)) and adopted it as the gate on 2026-11-21. The policies it waited for are now adopted: the configuration contract ([#339](https://github.com/SaltedTan/virgil-language-server/issues/339)), the support policy ([#340](https://github.com/SaltedTan/virgil-language-server/issues/340)), and the benchmark resource checks ([#347](https://github.com/SaltedTan/virgil-language-server/issues/347)). On 2026-11-20 the maintainer answered the ledger's open questions; [Gate decisions](#gate-decisions) records the answers, and the rows below apply them. Adoption followed a [trial fill-in](#trial-fill-in-on-010-alpha8) on 2026-11-20 that treated `0.1.0-alpha.8` as if it were the candidate, and this page includes the trial's amendments. Prereleases before v1.0 follow the [release runbook](releasing.md) and don't need this page.

No v1.0 candidate exists yet, so every clause is *not ready*. Apart from the gate decisions, the ledger records no new policy, no new test, and no new number.

## How to read it

Each clause has one of three states:

- **Not ready:** the candidate lacks evidence that the pass condition holds, or the policy that defines the condition isn't adopted.
- **Satisfied:** the candidate's own evidence, recorded in the ledger, shows the pass condition.
- **Approved exception:** the maintainer accepted a named gap for this candidate. An exception records its owner, its issue, its impact on users, and when it expires or is checked again. The [approved exceptions](#approved-exceptions) section records the two advance approvals for the first v1.0 candidate.

A row marked *pending* depends on a policy that the maintainer hasn't adopted. It stays not ready until the policy is adopted and the candidate meets it.

These rules apply to every row:

- Evidence counts only for the candidate's exact commit and its own build. Measurements and runs of an earlier commit or release don't qualify a new candidate, however recent they are. The rows below name earlier runs as examples of the evidence a candidate needs, not as evidence for it. The benchmark captures use a separate `BENCH=1` build of the candidate, and the resource checks use the candidate build that `scripts/bench-compare.py` makes beside its base. Each build counts when its recorded `server_revision` matches the candidate's full SHA. A benchmark capture also needs a clean build because a checkout with uncommitted changes still records the candidate's SHA as `server_revision`. The capture's `git_status` must be empty, each `builds[].build.git_status` recorded at build time must be present and empty, and the source revision in `version` and `build_info` must not end in `-dirty`. The comparison's builds are clean by construction, since `scripts/bench-compare.py` builds each ref in a new worktree.
- Missing evidence isn't a pass. A required check that was skipped, not run, or not recorded leaves its row not ready.
- A rerun that passes doesn't erase a product defect. If a failure was caused by the server, the extension, or the repository's own tests, it needs a fix or an approved exception; only a failure shown to come from outside the repository, such as a runner or a download, can be rerun away. A failure in an advisory lane doesn't block its clause, but a product defect it shows still gets an issue.
- A proposal, a draft, or an advisory heuristic isn't an adopted policy.

## The candidate

A candidate's evidence starts with what was built and from what. Record these for the candidate before any row can be satisfied:

| Field | Where it comes from |
| --- | --- |
| Candidate commit | The full SHA of the commit to be tagged and the release workflow runs that tested it, including the pre-tag dry run on `main` |
| Pinned Virgil revision | `virgil-lsp --version`, the `vendor/virgil` gitlink, the release manifests' `virgilRevision` and `library.revision`, the bundled `virgil/REVISION`, `THIRD_PARTY_NOTICES.md`, the benchmark corpus's `virgil_source_revision`, the release notes, and the [Virgil revision table](compatibility.md#virgil-revision), which must all agree, as clause 1's compatibility review lists |
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
| 1. Configuration and compatibility | Versions 1 and 2 of `.virgil-lsp.json`: their fields, strict parsing, patterns, file selection, and active projects. The support policy: one pinned Virgil revision per release, the bundled library, the worker from the same build, the extension pairs, editor minimums, fixes, and how changes are announced. | The candidate passes the tests that the configuration contract's inventory and the support policy's evidence table name, and every record of its pinned revision agrees. | Adopted: the [configuration contract](configuration.md#stability-and-evolution) ([ADR-0012](decisions/0012-project-configuration-stability.md)) and the [support policy](compatibility.md#support-policy) ([ADR-0014](decisions/0014-compatibility-and-support-policy.md)). Checked by `make test`, `npm test` in `clients/vscode`, and the compatibility review in [Making a release](releasing.md#making-a-release). | None. | [#339](https://github.com/SaltedTan/virgil-language-server/issues/339) and [#340](https://github.com/SaltedTan/virgil-language-server/issues/340) are closed: their policies were merged on 2026-11-01 in [PR #423](https://github.com/SaltedTan/virgil-language-server/pull/423) and [PR #426](https://github.com/SaltedTan/virgil-language-server/pull/426). The refusal of a worker whose handshake reports another protocol version, server version, or source revision is tested by `AnalysisSupervisor:refuses_other_protocol_version`, `refuses_other_server_version`, and `refuses_other_source_revision` ([#429](https://github.com/SaltedTan/virgil-language-server/issues/429)). | Not ready: policy adopted; candidate evidence missing. |
| 2. Everyday use | References, prepare rename and rename, signature help, and completion in configured, target-independent programs within the documented limits; in open and closed files, and with unsaved edits. Completeness is bounded by the active project, excluded method bodies, and resource refusals. Whole-document formatting ([ADR-0019](decisions/0019-formatter.md)) in the editors and on the command line, which the maintainer added on 2026-11-20. | The candidate passes the unit tests, transcripts, and suites for these features in `make test`, and their editor checks in every [required lane](#required-editor-lanes). | No separate policy; the feature behavior is documented in [architecture](architecture.md) and its limits in [known limitations](known-limitations.md); the formatter's rules are ADR-0019's. Checked by `make test`. | None. | [#327](https://github.com/SaltedTan/virgil-language-server/issues/327) is fixed by [PR #329](https://github.com/SaltedTan/virgil-language-server/pull/329). Its tests need to pass on the candidate; it needs no second fix. | Not ready: candidate evidence missing. |
| 3. Incomplete syntax | Phase 3a within [ADR-0010](decisions/0010-analyze-unrepairable-files.md#decision): the bounded open-body scope of [No error-tolerant parsing](known-limitations.md#no-error-tolerant-parsing), with worker failures and stale answers. Phase 3b is out of scope. | A full tolerance capture of the candidate passes the release scorecard, and the worker-failure and stale-answer tests pass. | Accepted: ADR-0010's v1.0 gate for M6 and the [release scorecard](benchmarks.md#release-scorecard). Checked by `scripts/bench-tolerance.py`, then `scripts/check-tolerance.py`, and `make test`. | None. | [#335](https://github.com/SaltedTan/virgil-language-server/issues/335) is closed: the scorecard was merged in [PR #371](https://github.com/SaltedTan/virgil-language-server/pull/371). | Not ready: candidate evidence missing. |
| 4. Performance budgets | Latency and memory of the published benchmark fixtures, in CI or scheduled runs. | The candidate meets the adopted budget table, where a missed cell that passes its retest isn't a failure, and, for each platform the benchmark workflow runs (Linux and macOS), an enforce-mode report and its comparison input show that the applicable resource limits were evaluated and passed on its exact commit. | Adopted: the [performance budgets](performance-budgets.md) ([ADR-0016](decisions/0016-performance-budgets.md)) and the [resource checks](benchmarks.md#resource-checks) ([#347](https://github.com/SaltedTan/virgil-language-server/issues/347)), both required since 2026-11-20; timing checks in the [scheduled comparison](benchmarks.md#scheduled-comparison) remain advisory. | None. | [#345](https://github.com/SaltedTan/virgil-language-server/issues/345) and [#347](https://github.com/SaltedTan/virgil-language-server/issues/347) are closed; the budgets are adopted and the resource checks are enforced. | Not ready: policy adopted; candidate evidence missing. |
| 5. Artifacts and clean install | The release assets for the [host platforms](compatibility.md#host-platforms), the clean install test, and the editor tests on the release's own archives and VSIX. | Before tagging, the `release.yml` dry run on `main` must test the candidate's exact SHA and pass every required job, including checksum verification. Use individual job results to confirm that every required release job passed, including the clean install test and the editor tests on the release's own assets in every [required lane](#required-editor-lanes) at its pinned version. The two JVM editor lanes are advisory during preview. | Adopted release policy: the [release workflow](releasing.md#how-the-workflow-works) and the [editor evidence contract](../test/e2e/RESULTS.md#the-release-gate). The required lanes and their editor versions were decided on 2026-11-20. See the [clause 5 details](#5-artifacts-and-clean-install) for the evidence that covers macOS. | None. | [#348](https://github.com/SaltedTan/virgil-language-server/issues/348) is closed: [PR #424](https://github.com/SaltedTan/virgil-language-server/pull/424), merged on 2026-11-01, made the Linux LazyVim job a required check in the repository setup script, and the maintainer applied it to branch protection on 2026-11-02. [#211](https://github.com/SaltedTan/virgil-language-server/issues/211) and [#212](https://github.com/SaltedTan/virgil-language-server/issues/212) are open and wait for `vscode-languageclient` releases; both are [approved in advance for the first v1.0 candidate](#approved-exceptions). [#341](https://github.com/SaltedTan/virgil-language-server/issues/341) is closed: the headless Neovim test writes a result stream, and the other editor tests don't yet. The macOS LazyVim lanes use Neovim 0.12.6, pinned by SHA-256. | Not ready: candidate evidence missing. |
| 6. A release cycle | Upgrading from the previous release to the candidate and rolling back, and compatibility with Virgil revisions newer than the pin. | At least one completed release cycle on the way to v1.0 recorded an upgrade and rollback rehearsal that passed, and newer-Virgil evidence for the same cycle. | The [upgrade and rollback rehearsal](releasing.md#upgrade-and-rollback-rehearsal), the advisory `virgil-master` workflow, and the [pin update procedure](../CONTRIBUTING.md#updating-the-pinned-virgil-revision). The release workflow runs the rehearsal on every release; see [clause 6 evidence](#6-a-release-cycle). | None. | [#344](https://github.com/SaltedTan/virgil-language-server/issues/344) is closed: the rehearsal exists and the release workflow runs it. | Not ready: no cycle recorded for a candidate. |

## Details

Each section states what the row's pass condition needs, what evidence sources exist, and what earlier evidence shows about the form the candidate's evidence takes.

### 1. Configuration and compatibility

[ADR-0012](decisions/0012-project-configuration-stability.md) and the [configuration contract](configuration.md#stability-and-evolution) were adopted on 2026-11-01 ([#339](https://github.com/SaltedTan/virgil-language-server/issues/339), [PR #423](https://github.com/SaltedTan/virgil-language-server/pull/423)). From server 1.0 on, every 1.x release reads version 1 and version 2 files as the contract specifies, since the maintainer extended the promise to version 2 on 2026-11-20: their fields, types, defaults, the target names that version 2 accepts, and the version literals; strict parsing, in which unknown and repeated fields are errors and a file with any error defines no projects; the pattern syntax and how patterns select, order, own, and exclude files; and the Virgil root's precedence and the choice of each file's active project. Language options follow the pinned compiler, not the format, and capacity limits may change in any release with a changelog entry. Before 1.0, a prerelease may still change the rules, and its notes say so.

[ADR-0014](decisions/0014-compatibility-and-support-policy.md) and the [support policy](compatibility.md#support-policy) were adopted on 2026-11-01 ([#340](https://github.com/SaltedTan/virgil-language-server/issues/340), [PR #426](https://github.com/SaltedTan/virgil-language-server/pull/426)). A release supports the one Virgil revision it was built with; upstream `master` is only an advisory signal. It supports the bundled library when its `REVISION` matches, only the worker from its own build, a platform VSIX with the server it bundles, and the editor minimums in [Editors](compatibility.md#editors). Fixes go into the next release of the current minor line, with no backports. From 1.0, an incompatible change to the public command line or the initialization options needs a new server major version, and one to a client setting a new extension major version.

The candidate needs:

- `make test` passing at the candidate commit on both required hosts. It runs the server's tests that the contract's [inventory](configuration.md#inventory) and the policy's [evidence table](compatibility.md#evidence) name. It doesn't run the client tests, which `npm test` below runs, or the archive smoke check, which `test/release/run.sh --archive <archive>` runs in the release dry run. While working on one area, `make test-unit ARGS='ProjectConfig:* ProjectExpansion:* WorkspaceProjects:*'` runs the configuration's unit tests alone.
- `npm test` in `clients/vscode` passing, for the client's server selection, broken-bundle, and mismatched-host tests. The whole suite has to pass: a failure in the client's own test harness is in the repository, so a passing rerun doesn't clear it.
- The compatibility review of [Making a release](releasing.md#making-a-release): the candidate's row in the Virgil revision table agrees with `virgil-lsp --version`, the gitlink, `THIRD_PARTY_NOTICES.md`, the corpus's `virgil_source_revision`, the dry run's manifests, the bundled `virgil/REVISION`, and the release notes; and `CHANGELOG.md` announces every change that the policy names.

Version 2's rules are in the same inventory, beside version 1's, such as `ProjectConfig:target_version_two` and `project-version-2.jsonl`; analysis for a target stays [optional work](#optional-work). No open facts remain for this clause: since [#429](https://github.com/SaltedTan/virgil-language-server/issues/429), unit tests send worker handshakes with another protocol version, server version, or source revision, and check that the server refuses each one.

### 2. Everyday use

These features shipped in `0.1.0-alpha.2`, and the roadmap's question for v1.0 is whether they are suitable for everyday use. Their answers are bounded by rules that the ledger keeps as they are:

- Semantic answers come from the file's active project, chosen as the [configuration contract](configuration.md#stability-and-evolution) says. A file outside every project gets no semantic answers ([Semantic features need a project file](known-limitations.md#semantic-features-need-a-project-file)).
- A project is analyzed for the target that its project file names, or without a target, where code written for a native target reports unresolved machine-level types ([support policy](compatibility.md#what-a-release-supports)).
- References and rename find uses in the analyzed program only. A method body excluded by fallback or repair gives no answers, and reference lists can be partial then ([No error-tolerant parsing](known-limitations.md#no-error-tolerant-parsing)). Rename refuses what [What rename refuses](known-limitations.md#what-rename-refuses) lists, and is refused when a document or the project's files changed while it waited or while its edits were checked ([Stale results](known-limitations.md#stale-results)).
- Completion and signature help in code that doesn't parse work only where one repair at the cursor makes it parse. When an analysis is refused or replaced, completion offers keywords only, marked incomplete so that the editor asks again ([Stale results](known-limitations.md#stale-results)).
- Programs must stay within the [supported program size](known-limitations.md#supported-program-size).

The candidate needs these tests passing: the unit tests and diagnostics transcripts run in `make test`, while the VS Code and LazyVim editor checks run separately in the release workflow; among editor checks, `make test` runs only the headless Neovim test:

- The unit tests of each feature, run alone with `make test-unit ARGS='LspReferences:* LspPrepareRename:* LspRename:* LspSignatureHelp:* LspSignatureRepair:* LspCompletion:* LspCompletionScope:* LspCompletionMembers:*'`. Most use configured project fixtures with the real worker; `LspCompletion:closed_document` and `LspCompletion:keyword_only_complete` check the server's handling directly, without a configured project or worker. Among them, `LspReferences:file_changed_on_disk`, `LspRename:closed_file_changed_while_checked`, `LspCompletion:closed_document`, and `LspSignatureHelp:target_changed_on_disk` cover closed files and changes on disk, and `LspRename:changed_while_waiting` and `LspCompletionScope:stale_reruns_once` cover unsaved edits made while a request waits or runs.
- The diagnostics transcripts [`references.jsonl`](../test/diagnostics/references.jsonl), [`rename.jsonl`](../test/diagnostics/rename.jsonl), [`signature-help.jsonl`](../test/diagnostics/signature-help.jsonl), [`completion.jsonl`](../test/diagnostics/completion.jsonl), [`member-completion.jsonl`](../test/diagnostics/member-completion.jsonl), and [`completion-insert-replace.jsonl`](../test/diagnostics/completion-insert-replace.jsonl).
- The applicable editor checks of the `references`, `rename`, `signature`, and `completion` areas in [`coverage.json`](../test/e2e/coverage.json), such as `vscode.rename.apply-undo` and `lazyvim.completion.local` in the [required lanes](#required-editor-lanes), and `nvim.references.library` in the headless Neovim suite.

On 2026-11-20 the maintainer added the formatter of [ADR-0019](decisions/0019-formatter.md) to this clause. Its limits are the ADR's: whole-document formatting only, which keeps the author's line breaks except for its two joins and the end of the file, and which leaves a document that doesn't parse unchanged. The candidate also needs these passing:

- The `Formatting:*` unit tests, run alone with `make test-unit ARGS='Formatting:*'`.
- The suites that `make test` runs for the formatter: `test/protocol/formatting.py`, the corpus check `test/format/corpus.py` against its committed record on native builds, the `format` command's CLI tests including `--write` on native builds, and `make check-format`.
- The applicable formatting checks in [`coverage.json`](../test/e2e/coverage.json), such as `vscode.formatting.save` and `lazyvim.formatting.save` in the required lanes, and `nvim.formatting.buffer` in the headless Neovim suite.

Analysis for a native target ([ADR-0018](decisions/0018-native-target-analysis.md)) is shipped and supported, but it isn't part of this clause; it is [optional work](#optional-work).

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

The performance budgets are adopted: the maintainer adopted the proposal in [performance-budgets.md](performance-budgets.md) on 2026-11-03 ([ADR-0016](decisions/0016-performance-budgets.md)), exactly as proposed, with its numbers, reference environments, and enforcement policy. The clause can't be satisfied until the candidate passes a run on the reference host checked against the adopted table. The clause's requirements and their current status:

- a budget table: met, the adopted target tables in [performance-budgets.md](performance-budgets.md);
- the qualified host and workloads that it applies to: met, the i7-14700KF reference host, CPU 12, on the small, util, and Aeneas fixtures within the document's supported envelope;
- the endpoints, percentiles, and sample rules of each measurement: met, 30 measured operations per cell, p50/p95, no trimming;
- the definitions of the memory that is measured: met, the document's memory definitions and envelope;
- a failure and retest procedure: met, the document's failure and retest procedure. On 2026-11-20 the maintainer decided how it applies to a candidate: the candidate meets the table when every cell passes in its capture, or when every cell that missed passes in one retest under that procedure. Both captures are recorded with the candidate's evidence. A miss that the retest reproduces fails the clause until a fix or an approved exception;
- a run of the candidate on the reference host, checked against the table: not met, no candidate has been checked yet. The run is the document's four captures at the candidate commit, under the shared benchmark lock: the full scenarios, first semantic diagnostics, and the error-result workload with `scripts/bench-lsp.py`, and the memory soak with `scripts/bench-memory-soak.py`. Check the memory targets against the first-semantic, error-result, and soak captures;
- an enforce-mode report with its comparison input showing the candidate's exact commit passed the applicable [resource checks](benchmarks.md#resource-checks), required since the maintainer's decision of 2026-11-20: not met, no candidate has been checked yet.

Enforcement of the adopted latency and memory targets is advisory. The [weekly scheduled comparison](benchmarks.md#scheduled-comparison)'s timing heuristic only produces warning annotations, so it can't satisfy the clause alone. The workflow enforces the separately approved resource checks for worker allocation and in-handle collections. Passing those checks does not establish that the candidate meets the adopted table, and meeting the table does not replace them: the candidate needs both. The resource evidence is the enforce-mode report for a qualified comparison whose new build is the candidate's exact SHA. Keep the report with the `comparison.json` that produced it. Before recording a pass, check that `builds[1].build.server_revision` in that file matches the candidate's full SHA. For each required project and scenario, the report must show that the applicable relative limits, approved absolute limits, or both were evaluated and passed. A zero exit status can accompany warnings when neither set of limits was applied. If the builds are incomparable, the candidate needs a matching approved absolute baseline. If none applies, follow the [absolute baseline procedure](benchmarks.md#resource-checks) and rerun the resource report before recording a pass. A comparison of the candidate with itself, which the scheduled comparison can make when the candidate is the first commit after a pin change, counts only when it passes approved absolute limits. The benchmark workflow enforces the checks on Linux and on macOS, so the candidate needs a passing report for each platform that the workflow runs.

To produce both reports by hand, dispatch `benchmarks.yml` with `gh workflow run benchmarks.yml --ref <tooling branch or tag> -f base_ref=<base ref> -f new_ref=<candidate SHA>`. The tooling ref selected by `--ref` must contain the enforce-mode resource step and the approved `test/benchmarks/resource-policy.json`. The tooling checkout, the base, and the candidate must all share the same Virgil pin, so use `main` as the tooling ref when it meets both requirements. Use the previous release tag as the base if it shares that pin. If it has a different pin, set both inputs to the candidate SHA to check the approved absolute limits. A local `scripts/bench-compare.py` run matches the approved Linux baseline only when its tooling root and build directories have the same path lengths as CI's, because those lengths are part of the compared metadata.

The adopted targets apply to every candidate as they stand. The budget document's re-baseline after a build change refreshes its recorded baseline and evidence tables, which track drift; on 2026-11-20 the maintainer decided that it doesn't change a candidate's pass condition, so a candidate needs no new baseline before it is checked.

The existing evidence sources:

- `make bench`, which runs `scripts/bench-lsp.py --require-probes` on a `BENCH=1` build ([Run](benchmarks.md#run));
- `python3 scripts/bench-corpus.py --check`, the corpus pin check;
- `scripts/bench-compare.py --base-ref <ref> --new-ref <candidate-sha> --work-dir <dir> --output-dir <dir>`, the comparison of two builds that the scheduled workflow runs. A local comparison generates `comparison.json` for the resource report step. Apply the same tooling and base selection rules as for the workflow, using the local checkout for the corpus and approved policy;
- `python3 scripts/bench-report.py <dir>/comparison.json --resources test/benchmarks/resource-policy.json --mode enforce`, the resource report step that the scheduled workflow also runs on its comparison. Run it from the same tooling checkout;
- `scripts/bench-memory-soak.py`, the opt-in [memory soak](benchmarks.md#memory-soak).

### 5. Artifacts and clean install

The documented platform matrix is the [host platforms](compatibility.md#host-platforms) table: Linux x86-64 is supported; Apple Silicon macOS is supported, using the Intel build under Rosetta 2; WSL 2 and Intel macOS are best effort; native Windows, Linux arm64, and WSL 1 aren't supported. A release has an archive and a manifest for each of the three builds (Linux x86-64, Intel macOS, and `jvm-arm64-darwin` for Apple Silicon), a VSIX for `linux-x64`, `darwin-arm64`, `darwin-x64`, and `darwin-arm64-jvm`, a universal VSIX without a server, the bundled JVM runtime's source archive, and `SHA256SUMS` ([What a release contains](releasing.md#what-a-release-contains)).

The candidate needs a [release workflow](releasing.md#how-the-workflow-works) run in which every applicable job passes except the two advisory JVM editor lanes during preview. The required results include the build, `make test`, packaging, and the archive smoke test on both hosts, along with the JVM archive test, the VSIX packaging checks, the required Extension Host and other editor tests on the run's own archives and VSIX, the [clean install test](releasing.md#clean-install-test), and checksum verification. A [dry run](releasing.md#dry-runs) on the release branch and on `main` comes first, with `gh workflow run release.yml --ref main`. A dry run after a merge to `main`, whose event is `push`, only builds and isn't that run. On 2026-11-20 the maintainer made the `main` dry run part of the clause. Before tagging, record the ID of the completed `release.yml` dry run on `main`, verify that its head SHA matches the candidate's full SHA, and check that each required job passed, including checksum verification. The individual required job results determine the clause's pass even if a JVM editor job reports an advisory failure.

The existing editor policy stays as it is ([host platforms](compatibility.md#host-platforms), [LazyVim CI](../test/e2e/lazyvim/README.md#ci)):

- The macOS Extension Host test runs in pull-request CI. The VSIX packaging jobs also run the Extension Host test on the `linux-x64` and universal VSIX files.
- The Linux LazyVim job, `lazyvim-linux`, runs on every pull request and is a required check on `main`. It became required after it had passed 20 pull-request runs in a row; on 2026-11-01 the maintainer decided that only first-attempt successes of distinct pull-request runs that executed the job count, and an audit that day found 103. [PR #424](https://github.com/SaltedTan/virgil-language-server/pull/424) added it to the repository setup script for [#348](https://github.com/SaltedTan/virgil-language-server/issues/348), and the maintainer applied it to branch protection on 2026-11-02.
- The weekly editor tests and each release run the full matrix: LazyVim on Linux with Neovim 0.11 and 0.12 and on macOS with both the Rosetta archive and the JVM archive (`lazyvim-macos-jvm`), the Extension Host test on macOS with the installed `darwin-arm64` VSIX and the `darwin-arm64-jvm` VSIX (`vscode-vsix-macos-jvm`), and the Remote-SSH test on Linux. Both JVM editor jobs run on Apple Silicon with Rosetta 2 installed; the checks require an untranslated arm64 runner, reject translation in every observed descendant, and require the bundled arm64 Java runtime. See [automated editor coverage](manual-testing.md#what-the-automated-tests-cover).
- Each release runs the [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test), three times with the `linux-x64` VSIX.

#### Required editor lanes

The maintainer decided on 2026-11-20 which lanes a v1.0 candidate requires, each at a pinned editor version. These are the jobs of [`editor-test-suites.yml`](../.github/workflows/editor-test-suites.yml), which the release workflow runs on its own archives and VSIX files:

| Lane | Platform | Editor version | Required |
| --- | --- | --- | --- |
| `lazyvim-linux` | Linux x86-64 | Neovim 0.11.6 and 0.12.6, each pinned by SHA-256 | Yes |
| `lazyvim-macos` | Apple Silicon under Rosetta 2 | Neovim 0.12.6, pinned by SHA-256 | Yes |
| `vscode-vsix-macos` | Apple Silicon under Rosetta 2 | VS Code 1.142.0, pinned as `VSCODE_VERSION` in `clients/vscode/test/e2e/run.cjs` | Yes |
| `vscode-remote-ssh-linux` | Linux x86-64 | The same pinned VS Code | Yes |
| `lazyvim-macos-jvm` | Apple Silicon without Rosetta translation | As `lazyvim-macos` | No, advisory while the JVM build is a preview |
| `vscode-vsix-macos-jvm` | Apple Silicon without Rosetta translation | As `vscode-vsix-macos` | No, advisory while the JVM build is a preview |

Both JVM editor lanes still run in every release and are advisory while the JVM build is a preview. A candidate's required lane passes only at its pinned version, and a skip isn't a pass ([the release gate](../test/e2e/RESULTS.md#the-release-gate)).

The release workflow's `checksums` and `publish` jobs depend on the call that runs the required editor jobs. The two advisory JVM editor jobs run in a separate call and can still be queued or running when checksum verification and publication start. A failed advisory job remains visible in the run, which can still succeed. In the weekly editor tests, all editor jobs run in one call and either JVM lane's failure fails the run.

See the [editor evidence contract](../test/e2e/RESULTS.md) for how to find check IDs in the editor logs and validate the headless Neovim result stream. The clean install test is outside the contract. Its workflow log and `clean-install-linux` artifact are kept for 30 days.

The clean install test covers Linux x86-64 only. The macOS archive and VSIX get the archive smoke test and the editor tests, but no clean-install check ([Clean install test](releasing.md#clean-install-test)). On 2026-11-20 the maintainer decided that this covers the clause for macOS: the macOS checks run on the release's own archive and VSIX, and a hosted macOS runner, with its preinstalled tools, can't give a clean install.

Two client library issues follow the published state of `vscode-languageclient`, whose latest release, 11.0.0, the extension uses. The maintainer approved both in advance as [exceptions for the first v1.0 candidate](#approved-exceptions) on 2026-11-20:

- [#211](https://github.com/SaltedTan/virgil-language-server/issues/211) is open. Restarting the server during a pending edit logs `Delivering pending changes failed: TypeError: this.task is not a function`; no text is lost, and [known limitations](known-limitations.md) documents it. Upstream fixed it on its main branch in [microsoft/vscode-languageserver-node#1920](https://github.com/microsoft/vscode-languageserver-node/pull/1920), but no release has the fix yet.
- [#212](https://github.com/SaltedTan/virgil-language-server/issues/212) is open. The library can send a request after `shutdown` when a stop races a pending request. It is reported upstream as [microsoft/vscode-languageserver-node#1921](https://github.com/microsoft/vscode-languageserver-node/issues/1921), which is open. The extension guards against it since [PR #319](https://github.com/SaltedTan/virgil-language-server/pull/319), so nothing reaches the server after `shutdown`.

### 6. A release cycle

The clause needs a completed release cycle, not a single run. In one cycle on the way to v1.0, the candidate needs:

- **Upgrade and rollback.** The [upgrade and rollback rehearsal](releasing.md#upgrade-and-rollback-rehearsal) passing from the previous release's published assets to the candidate's and back, with its receipt and logs recorded in [Results](manual-testing.md#upgrade-and-rollback-rehearsals). The release workflow's **Upgrade and rollback rehearsal** job runs it on every release and dry run, from the previous published release to the run's own assets, and the run's `upgrade-rehearsal-attempt-<n>` artifact holds its output, logs, and receipt ([In the release workflow](releasing.md#in-the-release-workflow)). `make test` and pull request CI don't run it. Its command is `xvfb-run -a python3 test/e2e/upgrade/run.py --from-assets <dir> --to-assets <dir> --logs <dir>` on Linux x86-64. For a by-hand run, follow the [runbook prerequisites](releasing.md#upgrade-and-rollback-rehearsal), including Node.js with `npm ci` in `clients/vscode`, a new or empty `--logs` directory, and each release's Linux archive and `linux-x64` VSIX with matching tagged manifests and `SHA256SUMS`, with both the server and extension versions increasing. It covers only Linux x86-64, the archive's symlink layout, and VSIX files installed from disk into one local VS Code profile.
- **Newer Virgil revisions.** The supported compiler is the candidate's one pinned revision ([support policy](compatibility.md#what-a-release-supports)). Compatibility with newer revisions is advisory evidence, kept apart from that: the weekly `virgil-master` workflow builds and tests against current upstream `master` and blocks nothing; and a pin update during the cycle follows the [pin update procedure](../CONTRIBUTING.md#updating-the-pinned-virgil-revision), with `make clean test`, the regenerated corpus, and a short benchmark, as the [pinned revision comparison](benchmarks.md#pinned-virgil-revision-comparison-2026-10-27) did for the current pin.

[#344](https://github.com/SaltedTan/virgil-language-server/issues/344) added the rehearsal and is closed. Its recorded run of 2026-10-31 at `faa41901` passed from `0.1.0-alpha.4` to `0.1.0-alpha.5` and back, across the pin change from Virgil `7cf038fc` to `57318d37`, and three runs with deliberately broken installations failed as expected. That run shows what the evidence looks like, but it isn't a v1.0 candidate's cycle. The run of 2026-11-03 at `de966c31` passed from the published `0.1.0-alpha.5` to `0.1.0-alpha.6` and back, across the pin change from Virgil `57318d37` to `b86f43f1`; it isn't a candidate's cycle either.

## Approved exceptions

An exception needs the maintainer's approval for a named candidate and records its owner, its issue, its impact on users, and when it expires or is checked again. The two entries below are the only exceptions to the named-candidate approval rule. The maintainer approved #211 and #212 in advance on 2026-11-20 for the first v1.0 candidate because only upstream releases of `vscode-languageclient` can resolve them.

| Clause | Owner | Issue | Impact | Expires or rechecked |
| --- | --- | --- | --- | --- |
| 5 | Maintainer | [#211](https://github.com/SaltedTan/virgil-language-server/issues/211) | Restarting the server during a pending edit logs a `TypeError` in the VS Code output; no text is lost, and [known limitations](known-limitations.md) documents it. | Rechecked at each release; it expires when a `vscode-languageclient` release contains [microsoft/vscode-languageserver-node#1920](https://github.com/microsoft/vscode-languageserver-node/pull/1920) and the extension updates to it. |
| 5 | Maintainer | [#212](https://github.com/SaltedTan/virgil-language-server/issues/212) | The library can try to send a request after `shutdown` when a stop races a pending request; the extension's guard keeps it from reaching the server. | Rechecked at each release; it expires when upstream fixes [microsoft/vscode-languageserver-node#1921](https://github.com/microsoft/vscode-languageserver-node/issues/1921) in a release the extension uses. |

## Optional work

These stay optional for v1.0 unless the maintainer changes the scope. None of them blocks a clause above:

- publishing to the VS Code Marketplace ([#192](https://github.com/SaltedTan/virgil-language-server/issues/192)), and an `nvim-lspconfig` configuration and Mason entry;
- a native Apple Silicon build ([#191](https://github.com/SaltedTan/virgil-language-server/issues/191));
- phase 3b of error tolerance ([ADR-0010](decisions/0010-analyze-unrepairable-files.md#decision));
- semantic token deltas ([#324](https://github.com/SaltedTan/virgil-language-server/issues/324));
- the Remote-SSH check from a Mac client, which is deferred ([#186](https://github.com/SaltedTan/virgil-language-server/issues/186)).
- analysis for a native target, `x86-64-linux` ([ADR-0018](decisions/0018-native-target-analysis.md)), which ships and is supported but which the maintainer left out of clause 2 on 2026-11-20, so its follow-ups such as [#516](https://github.com/SaltedTan/virgil-language-server/issues/516) don't block a candidate.

## Gate decisions

On 2026-11-20 the maintainer answered the questions that the ledger had left for the gate decision. The rows and details above apply these answers:

- **Editor lanes (clause 5).** The required lanes are `lazyvim-linux` at Neovim 0.11.6 and 0.12.6, `lazyvim-macos` and `vscode-vsix-macos` under Rosetta 2, and `vscode-remote-ssh-linux`, each at a pinned editor version. While the JVM build is a preview, its two editor lanes still run and are advisory. The macOS lanes' Neovim is to be pinned to an exact version; both now use Neovim 0.12.6, pinned by SHA-256. See [Required editor lanes](#required-editor-lanes) for how the release workflow runs the advisory lanes.
- **macOS clean install (clause 5).** The Linux clean install test, with the macOS archive smoke test and editor tests on the release's own assets, covers the clause for macOS.
- **Upgrade and rollback rehearsal (clause 6).** The release workflow runs it on every release and dry run, and publishes only if it passes ([In the release workflow](releasing.md#in-the-release-workflow)).
- **Resource checks (clause 4).** The candidate must meet the adopted budget table and provide an enforce-mode report with its comparison input showing its exact commit passed the applicable resource checks of [#347](https://github.com/SaltedTan/virgil-language-server/issues/347).
- **Scope (clause 2).** The formatter of [ADR-0019](decisions/0019-formatter.md) is part of everyday use. Analysis for a native target ([ADR-0018](decisions/0018-native-target-analysis.md)) is optional work.
- **Client library issues (clause 5).** [#211](https://github.com/SaltedTan/virgil-language-server/issues/211) and [#212](https://github.com/SaltedTan/virgil-language-server/issues/212) are [approved in advance for the first v1.0 candidate](#approved-exceptions) and are rechecked at each release.
- **Adoption.** On 2026-11-21 the maintainer adopted the ledger, with the amendments below, as the v1.0 release gate, after a [trial fill-in](#trial-fill-in-on-010-alpha8) that treated `0.1.0-alpha.8` as if it were the candidate. The trial ran on 2026-11-20, and the maintainer answered the questions it raised the same day:
  - **A missed cell that passes its retest (clause 4).** The candidate meets the budget table when every missed cell passes one retest under the budget document's procedure, with both captures recorded.
  - **Baselines (clause 4).** The adopted targets apply to every candidate as they stand. Re-baselining refreshes only the recorded drift tables.
  - **The `main` dry run (clause 5).** Before tagging, the `release.yml` dry run on `main` at the candidate's exact commit must pass every required job, including checksum verification. Clause 5 uses the individual required job results.
  - **Project file version 2 (clause 1).** Versions 1 and 2 are both covered, and the [configuration contract](configuration.md#what-server-1x-keeps) promises both for 1.x. Analysis for a target stays optional.
  - **Advisory JVM lanes and publishing (clause 5).** During the JVM preview, `checksums` and `publish` wait only for the required editor jobs. The advisory JVM editor jobs run independently of both jobs. A failure remains visible on the advisory job and the release run can still succeed.

## Trial fill-in on 0.1.0-alpha.8

On 2026-11-20 the ledger was filled in as if `0.1.0-alpha.8` (`23b17e49`, [release run 44523132185](https://github.com/SaltedTan/virgil-language-server/actions/runs/44523132185)) were the v1.0 candidate, to find gaps before the maintainer adopted the ledger as the gate. It is a trial, not candidate evidence: a real candidate needs its own runs. Its results, by clause:

| Clause | Trial result | What stopped it, or would have |
| --- | --- | --- |
| 1 | `make test` passed, 48 suites and 1,269 unit tests, and every test that the inventory and evidence table name ran. Every record of the pinned revision agreed. | `npm test` failed once in four runs, in the client's Remote-SSH harness test `remote-ssh-external.cjs` › `evidence-directory` ("pid … survived"). The failure is in the repository's own test code, so a rerun doesn't clear it and it needs a fix. |
| 2 | Every feature's unit tests, transcripts, and formatter suites passed; every lane of the release run passed its editor checks. | Nothing. The trial's VS Code and LazyVim logs omitted check IDs for most results, which made the evidence hard to trace ([#558](https://github.com/SaltedTan/virgil-language-server/issues/558)). |
| 3 | The full tolerance capture passed the release scorecard with no wrong answers, and the worker-failure and stale-answer tests passed. | Nothing. |
| 4 | The first full capture missed two p95 targets slightly, util `rename` (28.0 against 25 ms) and Aeneas `references` (2.035 against 2 ms), at a host load of 2 to 4. The retest passed every cell. The memory targets, the soak, and the resource checks in enforce mode against the approved baseline passed. | The rules for a miss with a clean retest and for baselines, now decided above. Analysis medians are 3% to 14% above the adoption baseline, though every cell is within its target ([#557](https://github.com/SaltedTan/virgil-language-server/issues/557)). |
| 5 | Every one of the run's 21 jobs passed on its first attempt, including the clean install test. `SHA256SUMS` verified the 12 payloads, and the attestations verified for all 13 assets, including `SHA256SUMS` itself. | The macOS lanes used Homebrew's Neovim, which isn't pinned, and `0.1.0-alpha.8` had no `main` dry run. |
| 6 | The upgrade and rollback rehearsal passed from `0.1.0-alpha.7` to `0.1.0-alpha.8` and back, run by hand, and the `virgil-master` workflow passed against upstream `8b82bdeb`. The release workflow rehearsal job is planned. | Nothing. |

The trial's raw outputs, including the benchmark captures, the tolerance capture, and the rehearsal's logs, are kept outside the repository. The release run's editor test logs and clean-install results expire after 30 days. The `release` artifact and build artifacts are kept longer.
