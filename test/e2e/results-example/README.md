# Rendered results example

This directory shows what [`scripts/editor-results.py render`](../../../scripts/editor-results.py) generates ([Rendering a release](../RESULTS.md#rendering-a-release)). It isn't evidence for any release. Its streams are demo runs that [`test_editor_results_render.py`](../test_editor_results_render.py) builds from [`coverage.json`](../coverage.json) with the validator tests' builder: the headless Neovim test on Linux, the LazyVim test on Linux, in the two legs of its lane, at Neovim 0.11.6 and 0.12.6, each with its expected failure, and the Extension Host test on macOS, on an installed VSIX, plus an attested manual selection of hover and VS Code checklist step 7 on Linux, shown separately. [`selection.json`](selection.json) lists them. `make test` checks that the streams, the selection, and the tables below are up to date. After a change to `coverage.json`, regenerate them:

```sh
python3 test/e2e/test_editor_results_render.py --write-example
```

## Results

### 0.0.0-example

The prose of a release's section stays hand-written, outside the markers. Only the tables between them are generated.

<!-- editor-results:0.0.0-example:runs:begin -->

| Date | Commit | Where | Command | Editor | Result |
| --- | --- | --- | --- | --- | --- |
| 2026-10-24 | `2222222` | VS Code, macOS; `vscode-vsix-macos`; macOS 15.5, arm64, Apple M1, the server under rosetta-2; demo run | `npm run test:e2e -- --vsix '<vsix>' '<archive>/virgil-lsp'` | VS Code 1.142.0, extension 0.1.2 installed from a VSIX | Passed (52 of 52 observations): 52 pass |
| 2026-10-24 | `2222222` | Neovim, Linux; `test`; Linux 6.8.0, x86_64; demo run | `make test` | Neovim 0.11.6 | Passed (29 of 29 observations): 29 pass |
| 2026-10-24 | `2222222` | LazyVim, Linux; `lazyvim-linux`; Linux 6.8.0, x86_64; demo run | `test/e2e/lazyvim/run.sh --archive '<archive>' --out '<dir>'` | Neovim 0.11.6 | Passed (63 of 63 observations): 62 pass, 1 xfail; XFAIL: missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)) |
| 2026-10-24 | `2222222` | LazyVim, Linux; `lazyvim-linux`; Linux 6.8.0, x86_64; demo run | `test/e2e/lazyvim/run.sh --archive '<archive>' --out '<dir>'` | Neovim 0.12.6 | Passed (63 of 63 observations): 62 pass, 1 xfail; XFAIL: missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)) |

Optional manual observations (do not change automated results):

| Date | Who | Setup | Editor | Observations | Result | Attestation |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-24 | Example tester | VS Code, Linux | VS Code 1.142.0 | Hover: Pass (manual); Checklist step 7: Pass (manual) | Passed | I observed the listed targets in this setup.; observed 2026-10-24T00:00:02Z; recorded 2026-10-24T00:01:00Z |

<!-- editor-results:0.0.0-example:runs:end -->

<!-- editor-results:0.0.0-example:matrix:begin -->

| Setup | Diagnostics | Document symbols | Definition | Type definition | Implementation | Type hierarchy | Inlay hints | Hover | Configuration changes | Restart | References | Document highlights | Workspace symbols | Signature help | Rename | Completion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| VS Code, Linux | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| VS Code, macOS | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) | Pass (API) |
| VS Code Remote-SSH, Linux client | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| VS Code Remote-SSH, Mac client | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) | Not tested (deferred) |
| VS Code Remote-WSL | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) |
| Neovim, Linux | Pass (API); not covered: undoing the fix of bad.v3, which brings the parser error back | Pass (API) | Pass (API); not covered: the jump with CTRL-\] | Pass (API + UI state) | Pass (API + UI state) | Pass (API + UI state) | Pass (API) | Pass (API) | Pass (API); not covered: a change to the project file on disk; a missing Virgil root, with its MissingVirgilRoot diagnostic and Invalid project file warning | Not covered | Pass (API) | Pass (API) | Pass (API) | Pass (API); not covered: a call still being typed, and nothing just past the ) | Pass (API); not covered: the refusal while the type error stands | Pass (API); not covered: no completion from the server in a comment |
| Neovim, macOS | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not covered | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| Neovim, WSL 2 | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) |
| LazyVim, Linux | Pass (API) | Pass (API) | Pass (API + UI state) | Pass (API + UI state) | Pass (API + UI state) | Not covered | Pass (API) | Pass (API + UI state) | Known limitation: missing-root warning ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)); workaround profile separate | Pass (API) | Pass (API) | Pass (UI state) | Pass (API) | Pass (API + UI state) | Pass (API + UI state) | Pass (UI state) |
| LazyVim, macOS | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not covered | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run | Not yet run |
| LazyVim, WSL 2 | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) | Not tested (best effort) |

<!-- editor-results:0.0.0-example:matrix:end -->
