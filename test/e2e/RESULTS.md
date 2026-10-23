# Editor test results

The editor tests give each of their checks a stable ID, and will record what each run observed as a stream of JSON events, from which the results of a release can be generated. This page is the contract for both:

- [`coverage.json`](coverage.json), the coverage manifest: every automated check, what it covers in the [manual test matrix](../../docs/manual-testing.md#matrix) and the VS Code [manual checklist](../../clients/vscode/README.md#manual-checklist), and what no check covers.
- [`results-v1.schema.json`](results-v1.schema.json), the JSON Schema of version 1 of the result events.
- [`test_coverage.py`](test_coverage.py), which `make test` runs, checks that the two files are consistent.

The tests don't write results yet; they still print their `PASS` and `FAIL` lines. [`scripts/editor-results.py`](../../scripts/editor-results.py) [validates a stream](#validating-a-stream) against both files. Writers in the tests, a renderer, and generated tables for the new release in `docs/manual-testing.md` come in later changes, which follow this page's rules. Past results and prose stay hand-written.

## Check IDs

An ID is `<harness>.<area>.<subcase>`, in lowercase words joined by hyphens, such as `nvim.rename.apply-undo` or `lazyvim.signature.unclosed`:

- The harness is `nvim` ([headless Neovim test](../../editors/nvim/README.md#headless-smoke-test)), `lazyvim` ([LazyVim test](lazyvim/README.md)), `vscode` ([Extension Host test](../../clients/vscode/README.md#extension-host-test)), `ssh` ([Remote-SSH test](../../clients/vscode/README.md#remote-ssh-test)), or `manual`.
- The area is one of the matrix's features, in the order of its results table, or `startup`, `startup-errors`, `trace`, `shutdown`, `recovery`, `keymaps`, `identity`, or `infrastructure`.
- The subcase names what the group checks.

Each ID labels one existing group of assertions that pass or fail together, such as a LazyVim step, a VS Code scenario, or a launcher's check. Checks that differ get their own IDs, even for the same feature: `nvim.hover.response` and `lazyvim.hover.response` are separate. The same assertions keep one ID wherever they run: `suite.cjs`'s `vscode.*` checks run unchanged in the local and the remote extension host.

An ID leaves out the release, platform, editor version, plugin versions, profile, checklist step, and line numbers. It stays when its description changes or its code moves. When what a check asserts changes, it gets a new ID, and the old one moves to `retired`, so that it is never reused. A run observes a check once per profile and iteration: `(runId, id, profile, iteration)` identifies an observation.

## The manifest

| Member | What it holds |
| --- | --- |
| `version` | 1, the manifest's format. |
| `evidence`, `levels` | The kinds of evidence, and the [evidence levels](#evidence-levels) with their exact wording. |
| `areas` | The matrix's features, in the order of the results table's columns, and the other areas. |
| `suites` | Each test: its files, its ID prefixes, and its profiles. A profile is `workaround` if it changes the shipped configuration, and `repeated` if it runs once per repetition, as the Remote-SSH test's launches do with `--repeat`. `wrapper` is the launcher, outside the editor. |
| `conditions` | Branches of a test: a check with `when` runs only where its condition holds. `alternative` pairs branches of which a run takes exactly one, such as `file-watching` and `no-file-watching` for a change on disk in LazyVim. `always` names the suites in which the condition holds in every run, such as `remote-ssh` for `vsix`. |
| `scopes`, `setups` | The matrix's rows, each with its suite and scope. |
| `checks` | Each check: `id`, the `suites` and `profiles` that run it, its `level`, `when`, whether it is `required`, the `source` file, and a `description` of what it asserts. |
| `retired` | IDs that are no longer used. |
| `matrix` | A cell for each feature of each automated setup: its checks, its level, and the gaps that it doesn't cover. |
| `checklist` | Steps 1 to 17 of the VS Code manual checklist, each with its checks and gaps. |
| `gaps`, `gapKinds` | What no check covers. |
| `xfails` | The [expected failure](#expected-failures-and-workaround-profiles). |

## Evidence levels

Each check has the level of the evidence it gathers. Generated tables print a passing check or cell with its level's wording, exactly; they never print a bare "Pass".

| Level | Evidence | Wording |
| --- | --- | --- |
| `api` | Results read through the editor's API or its LSP client: requests, providers, published diagnostics, the extension's output channel | Pass (API) |
| `ui-state` | The editor driven through its keys, and the state of its UI read: windows and their text, menus and their items, mappings, the mode, message histories; not how they look | Pass (UI state) |
| `api-ui-state` | Both | Pass (API + UI state) |
| `api-process` | API, and processes: exit statuses, deadlines, what is left running | Pass (API + process) |
| `process` | Processes alone | Pass (process) |
| `configuration` | The test's own inputs and setup: prerequisites, pins, installations, key mappings, evidence | Pass (configuration) |
| `manual` | A person's observation | Pass (manual) |

A matrix cell's level combines the API, UI state, and manual evidence of its checks: a restart cell whose checks also observe exit statuses reads "Pass (API)". The wording is the evidence's labels joined by " + ", which the consistency test enforces, and the schema's `level` lists the same wording.

## The release gate

The release gate requires automated evidence only. A `required` check must pass wherever a run plans it, or fail only as its expected-failure rule allows; a skip is neither, so a skip fails the gate. No required check is manual. A person may record observations with the `manual` suite, but the gate never requires one, and a manual record doesn't change a cell's automated result. Which lanes the gate requires, at which pinned editor versions, comes with the gate itself.

A run's lane is the ID of the CI job that ran it, such as `lazyvim-linux`, `vscode-vsix-macos`, or `vscode-remote-ssh-linux`, or `local` outside CI. The editor version is in the run's provenance, not in the lane.

## Coverage of the matrix and the checklist

A cell's checks must all pass or satisfy the [expected-failure rule](#expected-failures-and-workaround-profiles), in every profile and repetition that runs them; a workaround profile never counts toward a cell. A cell can hold both checks of a pair of alternatives, of which a run plans one.

What a cell or a checklist step doesn't cover is in its `notCovered`, as gaps. "Not covered" is an explicit label, not a pending result:

- A `visual` gap is what the editor shows, which automation can't observe: rendered views, popups, menus, notifications, prompts, and layout. It stays not covered.
- A `behavior` gap is behavior that no check exercises, such as restarting the server from plain Neovim. It stays not covered until a check is added; `planned` names the issue that adds it, if any. Closing the window during a server's recovery through Remote-SSH is one: VS Code sometimes quits before it tells the remote extension host to terminate, so only the Extension Host test, which closes a local window, covers it automatically.

A cell with no checks reads "Not covered". Setups outside the automated scope have no cells, and nothing requires them: WSL 2 is best effort ("Not tested (best effort)"), and Remote-SSH from a Mac client is deferred ([#186](https://github.com/SaltedTan/virgil-language-server/issues/186), "Not tested (deferred)").

## Result events

A run writes one stream: UTF-8 JSON Lines, one object per line, each line ending in LF, without a byte order mark or blank lines. Duplicate keys, `NaN`, and `Infinity` make a stream invalid. Every required field in the event's schema is present; unknown or inapplicable values use `null` only where the schema allows it. Logs and screens stay in files of their own, which a check can list in `artifacts` with their SHA-256.

Only the outer launcher writes the stream, such as `run.sh` or `run.cjs`; code inside the editor passes its observations to it. Each event has:

- `schema`, 1;
- `event`, `run-start`, `check`, or `run-end`;
- `runId`, unique to the run, never reused for a retry;
- `seq`, 0 for the `run-start`, then counting up by one;
- `time`, in UTC: when the check ended for a `check` event, or when the event was written for `run-start` and `run-end`.

`run-start` comes first. It names the `suite`, the matrix `setup`, the `lane`, the `purpose` (`release`, `development`, `compatibility`, `canary` for unreviewed newer editor or plugin versions, or `demo` for test data), the `release` it is evidence for, the `command`, the SHA-256 of `coverage.json`, and `expected`: every planned observation, sorted. The plan is each check of the suite, in each of its profiles that the run includes, if its condition holds, once per repetition of a repeated profile. `provenance` records the server's `--version` output and SHA-256s, the archive or VSIX, the checkout, the editor, the client and the host, the transport, the pinned versions, each profile's configuration, and the fixture and Virgil root; a tree digest is the SHA-256 of LF-terminated `<sha256>  <path>` lines, sorted by path relative to the tree's root, with `link <target>` in place of the file digest for a symbolic link, as the Remote-SSH test computes it. The client's and the host's `os` are the setup's. `configurationSha256` has a digest for each profile in `expected` but `wrapper`, and for no other profile, except that a profile in which no check ran, because its checks were skipped or are missing, may lack one, as when the run failed before it created the profile's configuration; a passing run has them all. A workaround profile's digest differs from each other profile's digest that is present. A manual run's `attestation` names the person and their statement; other runs have none.

If a prerequisite check fails before provenance can be obtained, exactly these fields may use `{"value": null, "unavailable": "<reason>"}` instead of their normal value: `provenance.server.versionText`, `provenance.server.target`, `provenance.server.sha256`, `provenance.server.workerSha256`, and `provenance.fixtureSha256`. Both members are required, and the reason must be a nonempty string explaining why the value could not be obtained. For example, a missing worker prevents obtaining `workerSha256`, and a failure before fixture copying prevents obtaining `fixtureSha256`. Every other field retains its existing form. A run with any unavailable provenance cannot pass. The [validator](#validating-a-stream) enforces this across `run-start` and `run-end`, since a single-event schema cannot see the run's outcome.

A `check` reports one observation:

- `status` is `pass`, `fail`, `xfail` (the expected failure), `xpass` (an expected failure that passed), or `skip` (not run, because an earlier check that it needs failed, so a `fail` comes before it in the stream, in any profile or iteration). Every status but `pass` has a `reason`.
- `level` is the check's level in the manifest.
- `observation` is what the check observed, whatever its status; `failureCode` is a short, stable code for how it failed.
- `expectation` names the expected-failure rule, for `xfail` and `xpass` only.

`run-end` comes last, written only after the editor has exited or been stopped at its deadline, and the launcher has finished its own checks and teardown. It says whether the run is `complete` (every planned observation has its `check`), its `status` (`pass`, `fail`, `aborted`, or `unavailable`), the counts of expected and reported observations and of each status, the exit code or signal, whether the `cleanup` was verified, and any `issues` outside the checks. A run passes if, and only if, it is complete, every planned observation has exactly one `check`, none failed, was skipped, or passed unexpectedly, it exited with 0 and no signal, the cleanup was verified, it has no `issues`, and none of its provenance is unavailable: its `status` is `pass` exactly then.

A stream is valid only if it has one `run-start` first and one `run-end` last, with the same `runId`, `seq` counts without gaps, `expected` is the manifest's plan, no observation is repeated or unplanned, each `level` is the manifest's, `complete` is true exactly when no observation is missing, and the counts agree with the events. A missing observation is never a pass. A killed run has no `run-end`, and its stream is incomplete.

The `run-start` doesn't say which conditions held or which profiles ran, so `expected` is the manifest's plan if:

- each key's check is one of the suite's, in one of the check's profiles, and the keys are sorted by `id`, `profile`, then `iteration`, comparing strings by code point, without duplicates;
- for each profile in `expected`, every check of the suite in that profile is there, apart from those with a condition;
- of each pair of alternatives, the run plans one side's checks, all of them, and none of the other side's; it plans neither only if a side has no checks in the run's profiles;
- a condition without an alternative is planned for all of its checks in the run's profiles or for none of them;
- a condition holds in every run of a suite that its `always` names, so such a run plans all of the condition's checks in its profiles;
- the `vsix` condition holds exactly when `provenance.clientMode` is `vsix` and `provenance.vsix` isn't null; if the run's profiles have `vsix` checks, it plans them exactly then, and in a suite that the condition's `always` names, every run's provenance says that it holds, whatever its profiles;
- a repeated profile has iterations 1 to N, each with all of its checks, with the same N for every repeated profile of the run, and any other profile has iteration 1 only.

Which profiles and repetitions a release requires comes with the release gate.

## Expected failures and workaround profiles

The only expected failure, `noice-1074-7bfd942`, is the missing-root warning in LazyVim's stock configuration: noice.nvim at commit `7bfd942445fb63089b59f97ca487d605e715f155` drops every server's `window/showMessage` ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)). An observation of `lazyvim.configuration.warning` is an `xfail` only if all of these hold; otherwise it is a `fail`:

- it is in the `noroot` profile, and the run's pins have noice.nvim at that commit;
- the warning wasn't observed, with the `failureCode` `warning-not-observed`;
- `lazyvim.startup.ui-ready`, `lazyvim.configuration.rootless-attach`, and `lazyvim.configuration.missing-root` passed in the same profile and iteration.

If the warning is observed there, it is an `xpass`, which fails the run, so that the plugin update gets reviewed. A stream that records a `pass` there, or a `fail` with that `failureCode` where all three hold, is invalid. The `noroot-noice-workaround` profile, with noice's LSP message handler turned off, is a separate profile with its own configuration digest: every check in it must pass, and its passes never count for the stock configuration. A lock update that changes noice.nvim changes the rule with it.

## Validating a stream

`scripts/editor-results.py validate` checks streams, each of one run, against `coverage.json` and `results-v1.schema.json` in this directory, or the files that `--manifest` and `--schema` name, by the rules above:

```sh
python3 scripts/editor-results.py validate [--manifest PATH] [--schema PATH] STREAM...
```

It reads the schema itself, with draft 2020-12's meaning for the keywords that the schema uses, and refuses a schema with any other keyword. A `pattern` is an ECMA-262 regular expression, an `integer` is written without a fraction or an exponent, and a `time`'s date must exist. It prints a line for each stream: `valid`, with whether the run passed and the count of each status, or `invalid`, with the line of the first error it finds. It exits with:

- 0 if every stream is valid and its run passed;
- 1 if every stream is valid, but a run failed, was aborted, or was unavailable;
- 2 if a stream is invalid, or the manifest or the schema can't be used.

[`test_editor_results.py`](test_editor_results.py), which `make test` runs, builds a passing stream for each suite from the manifest, and breaks each rule in turn.

## Adding a check

1. Choose an ID as [above](#check-ids), and add the check to `checks` with the suites and profiles that run it, its level, its condition, and a description of what it asserts.
2. Add it to the matrix cell of its feature for each automated setup whose suite runs it, and to the checklist steps it covers; remove the gaps that it now covers, and update each cell's `level`.
3. Run `python3 test/e2e/test_coverage.py` and `python3 test/e2e/test_editor_results.py`, or `make test`.

`test_coverage.py` checks that the files parse; that keys and IDs are unique and well formed, with known areas, suites, profiles, levels, and conditions; that each suite in a condition's `always` runs some of the condition's checks and isn't in its alternative's `always` too; that each level has its wording, as the schema has it; that no required check is manual; that every cell of every setup is mapped, not covered, or out of scope; that the checklist's steps are 1 to 17 and each maps to checks, is not covered, or is out of scope; that every check of a feature is in its cells; that each gap is used; that the expected-failure rule is the approved warning-only exception, with its check, plugin, exact commit and issue URL, failure code, profiles, and prerequisites; that unavailable prerequisite provenance requires a reason; that a passing `run-end` has no failures, unexpected passes, or skips; and that the schema's references resolve. It reads only the two files, not the tests that they describe, so keeping `source` and the descriptions true is the reviewer's job.
