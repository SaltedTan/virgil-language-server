# LazyVim test

`run.sh` tests the shipped [LazyVim plugin spec](../../../editors/nvim/lazyvim/virgil.lua) as a LazyVim user runs it: under the [LazyVim starter](https://github.com/LazyVim/starter) and the plugins that [`lazy-lock.json`](lazy-lock.json) pins, in Neovim's terminal UI, through LazyVim's own keys. It runs locally and in [CI](#ci), and isn't part of `make test`.

## Running it

It needs Neovim 0.11 or later, tmux, git, and Python 3 on `PATH`, and access to GitHub, from which it clones the starter and the plugins. nvim-treesitter also needs curl, tar, and a C compiler to install its parsers. The checkout's `vendor/virgil` must be initialized: the fixture's project uses `lib/util` from it. From the repository root:

```sh
test/e2e/lazyvim/run.sh --archive virgil-lsp-0.1.0-alpha.3-x86-64-linux.tar.gz
test/e2e/lazyvim/run.sh build/virgil-lsp
make test-lazyvim
```

The script also works from any directory when invoked by its path in the checkout; archive, server, and output paths are relative to the calling directory.

The first installs a release archive as its README says: extracted into its one directory, with a `virgil-lsp` symlink on `PATH`. The second puts a build's directory on `PATH`; `virgil-lsp-worker` must be beside `virgil-lsp`. `make test-lazyvim` builds the server and runs the second with `build/virgil-lsp`. The options are:

- `--out DIR`: where the results go, a new or empty directory. By default, a new directory under `$TMPDIR` (or `/tmp`), printed at the start and the end.
- `--keep-work`: keep the work directory, for debugging.
- `--update-lock`: install the latest plugins and write the lock; see [Pins](#pins).

The exit status is 0 only when all three runs give the [expected results](#expected-results).

## CI

- **Pull requests.** The CI job `lazyvim-linux`, "LazyVim test (built server, linux)" in [`ci.yml`](../../../.github/workflows/ci.yml), runs `run.sh` on every pull request and push to `main`, after the `test` job, with the server that `test` built, Neovim 0.11.6, and tmux from the runner's packages. See the [CI policy](../../../docs/compatibility.md#host-platforms) for its advisory status and promotion criteria.
- **Weekly and for each release.** The jobs of [`editor-test-suites.yml`](../../../.github/workflows/editor-test-suites.yml) run `run.sh --archive` on the release archives of the run: every Monday in the [editor tests workflow](../../../.github/workflows/editor-tests.yml), which builds them from source, and in the [release workflow](../../../docs/releasing.md#how-the-workflow-works), on each release's own archives, for every tag and dry run, including the pull requests that change this test ([Dry runs](../../../docs/releasing.md#dry-runs)). A release publishes only if they pass. They run on Linux with Neovim 0.11.6 and 0.12.6, each checked against its pinned SHA-256, and on macOS (Apple Silicon, with the server under Rosetta 2) with Homebrew's Neovim and tmux. See [automated editor coverage](../../../docs/manual-testing.md#what-the-automated-tests-cover) for the workflows' other editor tests, and the [CI policy](../../../docs/compatibility.md#host-platforms) for required-check status.
- **Artifacts.** Each job uploads the [output directory](#outputs) as `results/` and the script's own output as `run.log`, also when the test fails: `lazyvim-linux` from CI, and `lazyvim-linux-nvim-<version>` and `lazyvim-macos` from the weekly and release workflows. `gh run download <run-id> --name lazyvim-linux` fetches one.
- **Run history.** To count the CI job's runs in a row, list its results, newest first. Cancelled runs, which a newer push to the same branch replaces, and skipped ones, after `test` failed, don't count either way.

  ```sh
  gh run list --workflow ci.yml --limit 50 --json databaseId --jq '.[].databaseId' |
    while read -r id; do
      gh run view "$id" --json jobs,event,headBranch --jq '.event as $e | .headBranch as $b | .jobs[]
        | select(.name == "LazyVim test (built server, linux)")
        | "\(.completedAt) \(.conclusion) \($e) \($b) \(.url)"'
    done
  ```

- **No plugin cache.** Each job installs the plugins and the parsers from GitHub. On a local Linux machine, a cold install took 7 to 15 seconds of a run of about a minute, so a cache would save little and add a way for a run to differ from a user's installation.

## What it checks

Each run starts Neovim in a new tmux server, with `observer.lua` loaded with `--cmd`, before LazyVim, to record publications, requests, and exits, and `driver.lua` loaded with `-c` to drive it. Each step reports `PASS` or `FAIL`, with what it saw.

- **`main`** opens `bad.v3` with `VIRGIL_LOC` set to the checkout's `vendor/virgil`, and checks:
  - startup: a UI attached, `UIEnter` and LazyVim's `VeryLazy` fired, and noice.nvim loaded;
  - the client that LazyVim starts from the spec: its command, root, and `virgilRoot`;
  - parser diagnostics, cleared by an unsaved edit and back after undo; document symbols; and the project's semantic `TypeError` in `words/Words.v3`, which needs `lib/util` from the Virgil root;
  - definition and hover, by request and through LazyVim's `gd` and `K`; type definition by request and `gy`, checking the exact `StringBuilder` class destination of a call result and no location for the `int` local; and signature help, workspace symbols, and references by request;
  - document highlights in snacks.nvim words: exactly the declaration and four uses of `count` in `words/Highlights.v3`, with `]]` moving to the next use and `[[` returning to the previous use;
  - rename through `<leader>cr`: the server's refusal while the project has a type error, as noice shows it, then a rename typed into snacks.nvim's prompt after an unsaved fix, and two undos;
  - signature help in noice's window: as `(` is typed, with `<C-k>` in Insert mode and `gK` in Normal mode, and while a call is typed with and without mini.pairs's `)`, with `b: int` highlighted after the `,`; and that LazyVim maps Insert `<C-s>` to Save File;
  - completion in blink.cmp's menu as you type: the keyword `while`, the local `line` first, and the member `length` after `a.`, each accepted with Enter and undone; and nothing from the server in a comment;
  - a project-file edit on disk, then an unsaved one ([Changes on disk](#changes-on-disk));
  - a restart with the bare command, then with the named one ([Neovim 0.11 and 0.12](#neovim-011-and-012)), and a clean server exit.
- **`noroot`** uses deliberately bundle-less copies of the server and worker and opens `.virgil-lsp.json` with `VIRGIL_LOC` unset, and checks startup, the client's attachment, the `MissingVirgilRoot` diagnostic, the warning "Invalid project file …", and a clean server exit.
- **`noroot-noice-workaround`** repeats `noroot` with noice's LSP message handler turned off, as the [LazyVim section](../../../editors/nvim/README.md#lazyvim) suggests.

## Why tmux

lazy.nvim fires `VeryLazy` only after `UIEnter`, which needs an attached UI. Under `--headless`, LazyVim never loads noice.nvim, mini.pairs, or its keymaps, so the test would check another editor than a user's. tmux gives Neovim a real 200 × 50 terminal without a display. The driver types with `nvim_input()` and reads the editor's state, not the screen; tmux keeps the screen for failures. The startup step fails if no UI attached or `VeryLazy` never fired; the driver never fires it itself.

## Outputs

The output directory gets:

- `provenance.txt`: the date, the repository's commit and whether it is modified, the OS, CPU, Neovim, tmux, git, and Python, the server's `--version`, the archive's SHA-256, the starter's and every plugin's commit, the tree-sitter CLI's version, and every parser's revision;
- `lazy-install.log`: lazy.nvim's output while it installs the plugins;
- `parsers-install.log`: Neovim's output while it installs nvim-treesitter's parsers;
- `<run>.results`: a line per check, then `DONE <n> passed, <n> failed, <n> expected failure(s)`; a run whose results don't end with that line, whole and only once, fails;
- `<run>.lsp.log`: Neovim's LSP log at debug level, with the server's stderr;
- `<run>.screen.txt`: the screen at each failed step; when a run times out or is interrupted; and after Neovim exits with a nonzero or unknown status or without a complete `<run>.results`, with what Neovim left on the terminal, such as an error it printed as it exited;
- `summary.txt`: a line per run, its `XFAIL` lines, and the result.

The XDG directories, plugins, extracted server (in archive mode), and a copy of the [Neovim test's fixture](../nvim/fixture/) are in a work directory, which is removed at the end unless `--keep-work`. It is under `/tmp` on macOS, where tmux's socket path would be too long under `$TMPDIR`, as for the [Extension Host test](../../../clients/vscode/README.md#extension-host-test), and under `$TMPDIR` (or `/tmp`) elsewhere. A run writes nothing in the checkout, except with `--update-lock`.

Installing the plugins and the parsers has a 600-second deadline, and each run 240 seconds; `VIRGIL_LAZYVIM_RUN_TIMEOUT` sets another, to test the timeout path. A run ends when Neovim has exited and the pane's script has written its exit status to a file, or when that script is gone without writing it. The script doesn't wait for tmux to find the pane dead: Ubuntu 24.04's tmux 3.4, built with utempter, can miss the exit of the pane's shell and then never finds it dead ([tmux/tmux#4559](https://github.com/tmux/tmux/issues/4559)). Each run's tmux pane stays open after Neovim exits, so that its screen can be saved, until the script stops its tmux server. After each run, the server and worker processes must have exited by themselves. What else Neovim left running, such as a plugin's download, is listed as left running and stopped, and doesn't fail the run. On a timeout, SIGINT, SIGTERM, or SIGHUP, the script saves the screen and the LSP log, then stops the tmux server and every process of the runs: the tmux server's process tree, the server and worker processes that `observer.lua` recorded, the processes in the process group of each Neovim that it recorded, which keeps what Neovim started also once Neovim has exited, any process whose command line names the work directory, and their descendants. It freezes them with SIGSTOP, the tmux server first, until it finds no new one, so that none starts another process or leaves an orphan while it reads their trees, then kills them with SIGKILL. A frozen Neovim couldn't act on SIGTERM, and on SIGTERM it runs on for seconds and starts processes, such as parser downloads and a new language server. A Neovim's process group counts only while its anchor runs in it: a `sleep` that `observer.lua` has Neovim start in the group, named after the work directory, which outlives Neovim. Once a process group is empty, its number can go to an unrelated new group, but not while the anchor runs. So the script kills each anchor last, once nothing else in its group is left; if a Neovim's anchor is gone, it doesn't go by that group's number, and fails. A signal that comes while it freezes and kills them takes effect once they are killed, and the script gives up a query to a tmux server that gets no answer within seconds, since a stopped server never answers. The script looks again every 0.2 seconds for five seconds and removes the work directory only once none is left; otherwise, or if it couldn't list the processes after a few tries or a Neovim's anchor was gone, it reports so, keeps the work directory, and exits with a nonzero status. It can't find a process that left its Neovim's process group, whose command line doesn't name the work directory, and that `observer.lua` didn't record, once its parent has exited.

## Expected results

- `main`: every step passes.
- `noroot`: every step passes except "Invalid project file warning shown", which reports `XFAIL`: noice.nvim at the locked commit drops every server's `window/showMessage` ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)). If that step passes, it reports `XPASS` and fails the test: a lock update has fixed noice, so remove the expected failure from `driver.lua`, adjust `run.sh`'s expected XFAIL count for `noroot`, and update the [LazyVim section](../../../editors/nvim/README.md#lazyvim).
- `noroot-noice-workaround`: every step passes.

`INFO` and `WARN` lines don't change the result.

## Neovim 0.11 and 0.12

Neovim 0.12 has a built-in `:lsp` command, and the locked nvim-lspconfig then defines no `:Lsp*` commands. The driver chooses by capability, `exists(':lsp') == 2`, and the step names say which command ran:

- With `:lsp`, a bare `:lsp restart` restarts only the clients on the current buffer, so the step checks that `words/Words.v3`'s client is replaced and the library file's client keeps running.
- Otherwise, a bare `:LspRestart` restarts every client. On Neovim 0.11 it stops each of the two clients twice, and Neovim then ends the server with signal 15, which the step accepts.
- The named form, `:lsp restart virgil_lsp` or `:LspRestart virgil_lsp`, must exit with status 0 and no signal.

## Changes on disk

The step follows the client's declared file-watching capability ([Changes on disk](../../../editors/nvim/README.md#changes-on-disk)). If the client declares it, the step checks that the `UnknownField` error appears with no change to a `.v3` buffer. Otherwise the step checks that it doesn't appear within three seconds, and appears after a `.v3` change. The step's name says which case ran. It runs before the project file has been open in a buffer, since closing that buffer also makes the server read the file again.

## Pins

`starter_commit` in `run.sh` pins the starter; `lazy-lock.json` pins every plugin, lazy.nvim included. Before Neovim first starts, `run.sh` clones lazy.nvim and LazyVim at their locked commits. Otherwise the starter would clone lazy.nvim's `stable` branch, and lazy.nvim would install LazyVim alone to import its plugin specs, rewrite the lock from what it had installed, and install the other plugins at their latest commits. `:Lazy! restore` installs the rest; a normal run never syncs or updates. After that, and again after the runs, the script checks that every plugin is at its locked commit and that lazy.nvim's lock lists the same plugins and commits, and fails if not.

The parsers' source revisions come from the locked nvim-treesitter; the lock doesn't pin blink.cmp's prebuilt fuzzy matcher or the tools that mason.nvim installs, such as the Lua language server and the tree-sitter CLI. At a user's first start of LazyVim, they download in the background, and nvim-treesitter builds each parser with the C compiler. The script installs the parsers before the runs, under the install deadline, so that their many downloads and builds don't compete with the editor and the server during `main` or outlive Neovim: [`parsers.lua`](parsers.lua), in a headless Neovim, has LazyVim ensure the tree-sitter CLI is available, then has nvim-treesitter install the parsers that LazyVim's spec lists. It waits for them and for what mason.nvim installs meanwhile, such as stylua. A plugin spec, present only then, leaves LazyVim's configuration of nvim-treesitter only its setup, so that LazyVim starts no install of its own. The tree-sitter CLI is used from `PATH` if available; otherwise mason.nvim installs the version its registry offers. Its version is unpinned, and `provenance.txt` records it. The runs then start as a user's later starts do. The other downloads, such as the Lua language server and blink.cmp's matcher, still happen in the background during `main`, into the work directory; the checks neither wait for nor need them. lazy.nvim also checks for plugin updates in the background, without installing them.

To update the pins:

1. For a new starter, change `starter_commit` in `run.sh`.
2. Run `test/e2e/lazyvim/run.sh --update-lock` with an archive or a build. It installs the latest plugins with `:Lazy! sync`, writes `lazy-lock.json`, and then runs the tests with them. It is the only mode that writes to the checkout.
3. Review `git diff`. Run the test on Linux and on macOS, with Neovim 0.11 and 0.12, and update the docs for any change in behavior, such as an `XPASS`.

## What it doesn't check

The driver reads the editor's state: buffers, modes, mappings, noice's message history, the text and highlights of floating windows, and blink.cmp's menu items. It doesn't check what the screen looks like beyond those, such as the layout, colors, icons, or status line.
