# Editor test results

The editor tests give each of their checks a stable ID, and will record what each run observed as a stream of JSON events, from which the results of a release can be generated. This page is the contract for both:

- [`coverage.json`](coverage.json), the coverage manifest: every automated check, what it covers in the [manual test matrix](../../docs/manual-testing.md#matrix) and the VS Code [manual checklist](../../clients/vscode/README.md#manual-checklist), and what no check covers.
- [`results-v1.schema.json`](results-v1.schema.json), the JSON Schema of version 1 of the result events.
- [`test_coverage.py`](test_coverage.py), which `make test` runs, checks that the two files are consistent.

The tests don't write results yet; they still print their `PASS` and `FAIL` lines. [`scripts/editor-results.py`](../../scripts/editor-results.py) [validates a stream](#validating-a-stream) against both files, and [renders a release's tables](#rendering-a-release) from valid streams. Writers in the tests, the release gate, and generated tables for a new release in `docs/manual-testing.md` come in later changes, which follow this page's rules. Past results and prose stay hand-written.

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
| `xfails` | The [expected failure](#expected-failures-and-workaround-profiles), with the `label` that generated tables call it by. |

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

`run-start` comes first. It names the `suite`, the matrix `setup`, the `lane`, the `purpose` (`release`, `development`, `compatibility`, `canary` for unreviewed newer editor or plugin versions, or `demo` for test data), the `release` it is evidence for, the `command`, the SHA-256 of `coverage.json`, and `expected`: every planned observation, sorted. The plan is each check of the suite, in each of its profiles that the run includes, if its condition holds, once per repetition of a repeated profile. `provenance` records the server's `--version` output and SHA-256s, the archive or VSIX, the checkout, the editor, the client and the host, the transport, the pinned versions, each profile's configuration, and the fixture and Virgil root; a tree digest is the SHA-256 of LF-terminated `<sha256>  <path>` lines, sorted by path relative to the tree's root, with `link <target>` in place of the file digest for a symbolic link, as the Remote-SSH test computes it. The client's and the host's `os` are the setup's. `configurationSha256` has a digest for each profile in `expected` but `wrapper`, and for no other profile, except that a profile in which no check ran, because its checks were skipped or are missing, may lack one, as when the run failed before it created the profile's configuration; a passing run has them all. A workaround profile's digest differs from the digest of each non-workaround profile that has one. A manual run's `attestation` names the person and their statement; other runs have none.

If a prerequisite check fails before provenance can be obtained, exactly these fields may use `{"value": null, "unavailable": "<reason>"}` instead of their normal value: `provenance.server.versionText`, `provenance.server.target`, `provenance.server.sha256`, `provenance.server.workerSha256`, `provenance.vsix`, and `provenance.fixtureSha256`. Both members are required, and the reason must be a nonempty string explaining why the value could not be obtained. For example, a missing worker prevents obtaining `workerSha256`, a VSIX that can't be read prevents obtaining its name, SHA-256, and version for `vsix`, and a failure before fixture copying prevents obtaining `fixtureSha256`. Every other field retains its existing form. A run with any unavailable provenance cannot pass. The [validator](#validating-a-stream) enforces this across `run-start` and `run-end`, since a single-event schema cannot see the run's outcome.

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
- the `vsix` condition holds exactly when `provenance.clientMode` is `vsix` and `provenance.vsix` is the VSIX's metadata or unavailable, not null; if the run's profiles have `vsix` checks, it plans them exactly then, and in a suite that the condition's `always` names, every run's provenance says that it holds, whatever its profiles;
- a repeated profile has iterations 1 to N, each with all of its checks, with the same N for every repeated profile of the run, and any other profile has iteration 1 only.

Which profiles and repetitions a release requires comes with the release gate.

## Expected failures and workaround profiles

The only expected failure, `noice-1074-7bfd942`, is the missing-root warning in LazyVim's stock configuration: noice.nvim at commit `7bfd942445fb63089b59f97ca487d605e715f155` drops every server's `window/showMessage` ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)). An observation of `lazyvim.configuration.warning` is an `xfail` only if all of these hold; otherwise it is a `fail`:

- it is in the `noroot` profile, and the run's pins have noice.nvim at that commit;
- the warning wasn't observed, with the `failureCode` `warning-not-observed`;
- `lazyvim.startup.ui-ready`, `lazyvim.configuration.rootless-attach`, and `lazyvim.configuration.missing-root` passed in the same profile and iteration.

If the warning is observed there, it is an `xpass`, which fails the run, so that the plugin update gets reviewed. A stream that records a `pass` there, or a `fail` with that `failureCode` where all three hold, is invalid. The `noroot-noice-workaround` profile, with noice's LSP message handler turned off, is a separate profile with its own configuration digest: every check in it must pass, and its passes never count for the stock configuration. A lock update that changes noice.nvim changes the rule with it.

## Validating a stream

`scripts/editor-results.py validate` requires Python 3.9 or later and uses only the standard library. It checks streams, each of one run, against `coverage.json` and `results-v1.schema.json` in this directory, or the files that `--manifest` and `--schema` name, by the rules above:

```sh
python3 scripts/editor-results.py validate [--manifest PATH] [--schema PATH] STREAM...
```

It reads the schema itself, with draft 2020-12's meaning for the keywords that the schema uses, and refuses a schema with any other keyword. A `pattern` is an ECMA-262 regular expression, an `integer` is written without a fraction or an exponent, and a `time`'s date must exist. It prints a line for each stream: `valid`, with whether the run passed and the count of each status, or `invalid`, with the line of the first error it finds. It exits with:

- 0 if every stream is valid and its run passed;
- 1 if every stream is valid, but a run failed, was aborted, or was unavailable;
- 2 if a stream is invalid, or the manifest or the schema can't be used.

[`test_editor_results.py`](test_editor_results.py), which `make test` runs, builds a passing stream for each suite from the manifest, and breaks each rule in turn.

## Rendering a release

`scripts/editor-results.py render` generates a release's results in a Markdown document, such as [`docs/manual-testing.md`](../../docs/manual-testing.md#results): a table of its runs and its matrix, from the valid streams that a selection lists. It has the validator's requirements, and its output depends only on its inputs, not on the clock, the locale, the order of the selection or of a directory, or where the files are:

```sh
python3 scripts/editor-results.py render [--manifest PATH] [--schema PATH] --selection FILE --doc PATH (--write | --check)
```

With `--write`, it writes the tables into the document. With `--check`, it compares them with the document's, without writing, and prints a unified diff of any drift. It exits with:

- 0 if it rendered the tables, or `--check` found no drift, and every selected run passed;
- 1 if `--check` found drift, or a selected run didn't pass, which it renders all the same and names;
- 2 if an input is malformed, the selection, a stream, or the document, or the manifest or the schema can't be used. It writes nothing then.

[`test_editor_results_render.py`](test_editor_results_render.py), which `make test` runs, renders streams that it builds as the validator's tests do, checks each of the rules below, and checks that [`results-example/`](results-example/README.md), which shows the output, is up to date.

### The selection

A selection names the release and lists the streams that count for it, each by its path relative to the selection's directory, with the SHA-256 of its bytes. [`selection-v1.schema.json`](selection-v1.schema.json) is its schema, which the script reads as it reads the result schema:

```json
{
  "version": 1,
  "release": "0.1.0-alpha.4",
  "streams": [
    {"path": "lazyvim-linux.jsonl", "sha256": "<the file's SHA-256>"},
    {"path": "vscode-remote-ssh-linux.jsonl", "sha256": "<the file's SHA-256>"}
  ]
}
```

The selection is malformed unless:

- it lists every file whose name ends in `.jsonl` in its directory and the directory's subdirectories, so that no attempt is left out unnoticed, and each file has the SHA-256 that it gives;
- each stream is valid, by the rules above, with the selection's `release`;
- no two streams have the same run ID, or the same slot: a suite, a setup, a lane, and the editor's version, `provenance.editor.version`. The version is part of it because one CI job can run its lane at several editor versions, as `lazyvim-linux` runs Neovim 0.11.6 and 0.12.6, and each such leg is evidence of its own. A stream whose editor version is null could be of any leg, so it shares the slot of every stream of its suite, setup, and lane. The renderer never chooses between two attempts, such as by taking the later one.

### Markers

The tables go between markers in the release's section of the document: the lines after its heading, `### <release>`, up to the next heading of level 1 to 3. Each marker is a line of its own, and the four go in this order:

```html
<!-- editor-results:0.1.0-alpha.4:runs:begin -->
<!-- editor-results:0.1.0-alpha.4:runs:end -->
<!-- editor-results:0.1.0-alpha.4:matrix:begin -->
<!-- editor-results:0.1.0-alpha.4:matrix:end -->
```

Only the lines between each pair change: they become a blank line, the table, and a blank line. Every other byte of the document stays as it was, so the release's prose and the other releases' results stay hand-written. The document must be UTF-8, with LF line endings. These are errors:

- no heading of the release, or a second one;
- a missing marker, or a second one;
- markers out of order, or outside the section;
- a marker of another release in the section;
- a line with `editor-results:` that isn't a marker of its own, in the section or naming the release.

### The runs table

The runs table has a row for each selected run, sorted by the manifest's order of the runs' setups, then by suite, lane, editor version, with its numbers compared as numbers, and run ID. Its columns come from the run-start and the run-end, and the stream holds the detail:

| Column | What it shows |
| --- | --- |
| Date | The run-start's date, in UTC. |
| Commit | The first seven digits of the harness's revision, or "Unknown"; "with local changes" if its checkout had them; and the server's version, if it differs from the release. |
| Where | The setup; the lane; the client's OS, its release, architecture, and CPU, the transport to the host, and the translator that ran the server, if any; a link to the CI run; and the run's purpose, unless it is `release`. |
| Command | The command, quoted for a shell. |
| Editor | The editor and its version, and for VS Code, where the extension came from. |
| Result | Whether the run passed, failed, was aborted, or was unavailable, with its counts. For a run that didn't pass, its failed checks and its unexpected passes, by ID, the first three of each, then how many more; its exit code or signal; its cleanup; the number of its issues; and any unavailable provenance. Then the expected failure, if the run has it: "XFAIL:", with its rule's label and issue. |

The [example](results-example/README.md)'s rows of LazyVim on Linux, a run of each leg of its lane:

| Date | Commit | Where | Command | Editor | Result |
| --- | --- | --- | --- | --- | --- |
| 2026-10-24 | `2222222` | LazyVim, Linux; `lazyvim-linux`; Linux 6.8.0, x86_64; demo run | `test/e2e/lazyvim/run.sh --archive '<archive>' --out '<dir>'` | Neovim 0.11.6 | Passed (57 of 57 observations): 56 pass, 1 xfail; XFAIL: missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)) |
| 2026-10-24 | `2222222` | LazyVim, Linux; `lazyvim-linux`; Linux 6.8.0, x86_64; demo run | `test/e2e/lazyvim/run.sh --archive '<archive>' --out '<dir>'` | Neovim 0.12.6 | Passed (57 of 57 observations): 56 pass, 1 xfail; XFAIL: missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)) |

### The matrix

The matrix has a row for each setup in `coverage.json`, and a column for each feature, in the manifest's order. A cell of an automated setup comes from its manifest cell's checks: each observation that the setup's selected runs planned, in every profile and repetition, or would have planned in a profile that they left out. A workaround profile never counts, and of a pair of alternatives, the side that a run planned does. A cell reads the first of these that applies:

| Cell | Wording | Example |
| --- | --- | --- |
| A setup outside the automated scope | Its scope's wording | Not tested (best effort), Not tested (deferred) |
| A cell without checks | "Not covered" | Not covered |
| No selected run of the setup | "Not yet run" | Not yet run |
| A check failed, or passed unexpectedly | "Fail:", and the checks by ID, the first three, then how many more, each unexpected pass followed by "(unexpected pass)" | Fail: `lazyvim.configuration.warning` (unexpected pass) |
| A check was skipped, or has no observation | "Partial", and why: skipped, not observed, or both | Partial (skipped, not observed) |
| The only observation that didn't pass is the expected failure | "Known limitation:", the rule's label and issue, and that its workaround profile is separate | Known limitation: missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)); workaround profile separate |
| Every observation passed | The cell's level's wording, then its `behavior` gaps after "not covered:", each by its `what` | Pass (API); not covered: the jump with CTRL-\] |

An observation without a check is never a pass: a run that stopped early, or that left out a profile, leaves its cells partial. The expected failure is a known limitation only where [its rule](#expected-failures-and-workaround-profiles) made it an `xfail`, and only if the cell's other observations passed: beside a failure, the cell fails. A passing cell names its behavior gaps, which a check could cover, but not its visual gaps, which no check can: its level already says that no check saw how the editor showed it. A setup outside the automated scope has no cells, so it reads its scope's wording even if a run of it was selected. A manual record doesn't change a cell.

The example's rows of Neovim and LazyVim on Linux:

| Setup | Diagnostics | Document symbols | Definition | Hover | Configuration changes | Restart | References | Workspace symbols | Signature help | Rename | Completion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Neovim, Linux | Pass (API); not covered: undoing the fix of bad.v3, which brings the parser error back | Pass (API) | Pass (API); not covered: the jump with CTRL-\] | Pass (API) | Pass (API); not covered: a change to the project file on disk; a missing Virgil root, with its MissingVirgilRoot diagnostic and Invalid project file warning | Not covered | Pass (API) | Pass (API) | Pass (API); not covered: a call still being typed, and nothing just past the ) | Pass (API); not covered: the refusal while the type error stands | Pass (API); not covered: no completion from the server in a comment |
| LazyVim, Linux | Pass (API) | Pass (API) | Pass (API + UI state) | Pass (API + UI state) | Known limitation: missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)); workaround profile separate | Pass (API) | Pass (API) | Pass (API) | Pass (API + UI state) | Pass (API + UI state) | Pass (UI state) |

## Adding a check

1. Choose an ID as [above](#check-ids), and add the check to `checks` with the suites and profiles that run it, its level, its condition, and a description of what it asserts.
2. Add it to the matrix cell of its feature for each automated setup whose suite runs it, and to the checklist steps it covers; remove the gaps that it now covers, and update each cell's `level`.
3. Regenerate the [rendering example](results-example/README.md) with `python3 test/e2e/test_editor_results_render.py --write-example`, then run `python3 test/e2e/test_coverage.py`, `python3 test/e2e/test_editor_results.py`, and `python3 test/e2e/test_editor_results_render.py`, or `make test`.

`test_coverage.py` checks that the files parse; that keys and IDs are unique and well formed, with known areas, suites, profiles, levels, and conditions; that each suite in a condition's `always` runs some of the condition's checks and isn't in its alternative's `always` too; that each level has its wording, as the schema has it; that no required check is manual; that every cell of every setup is mapped, not covered, or out of scope; that the checklist's steps are 1 to 17 and each maps to checks, is not covered, or is out of scope; that every check of a feature is in its cells; that each gap is used; that the expected-failure rule is the approved warning-only exception, with its check, plugin, exact commit and issue URL, failure code, profiles, and prerequisites, and has a label; that unavailable prerequisite provenance requires a reason and is allowed only in the fields that [Result events](#result-events) lists; that a passing `run-end` has no failures, unexpected passes, or skips; and that the schema's references resolve. It reads only the two files, not the tests that they describe, so keeping `source` and the descriptions true is the reviewer's job.
