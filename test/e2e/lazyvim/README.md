# LazyVim test

`run.sh` tests the shipped [LazyVim plugin spec](../../../editors/nvim/lazyvim/virgil.lua) as a LazyVim user runs it: under the [LazyVim starter](https://github.com/LazyVim/starter) and the plugins that [`lazy-lock.json`](lazy-lock.json) pins, in Neovim's terminal UI, through LazyVim's own keys. It runs locally, not in CI, and isn't part of `make test`.

## Running it

It needs Neovim 0.11 or later, tmux, git, and Python 3 on `PATH`, and access to GitHub, from which it clones the starter and the plugins. The checkout's `vendor/virgil` must be initialized: the fixture's project uses `lib/util` from it. From any directory:

```sh
test/e2e/lazyvim/run.sh --archive virgil-lsp-0.1.0-alpha.2-x86-64-linux.tar.gz
test/e2e/lazyvim/run.sh build/virgil-lsp
make test-lazyvim
```

The first installs a release archive as its README says: extracted into its one directory, with a `virgil-lsp` symlink on `PATH`. The second puts a build's directory on `PATH`; `virgil-lsp-worker` must be beside `virgil-lsp`. `make test-lazyvim` builds the server and runs the second with `build/virgil-lsp`. The options are:

- `--out DIR`: where the results go, a new or empty directory. By default, a new directory under `$TMPDIR` (or `/tmp`), printed at the start and the end.
- `--keep-work`: keep the work directory, for debugging.
- `--update-lock`: install the latest plugins and write the lock; see [Pins](#pins).

The exit status is 0 only when all three runs give the [expected results](#expected-results).

## What it checks

Each run starts Neovim in a new tmux server, with `observer.lua` loaded with `--cmd`, before LazyVim, to record publications, requests, and exits, and `driver.lua` loaded with `-c` to drive it. Each step reports `PASS` or `FAIL`, with what it saw.

- **`main`** opens `bad.v3` with `VIRGIL_LOC` set to the checkout's `vendor/virgil`, and checks:
  - startup: a UI attached, `UIEnter` and LazyVim's `VeryLazy` fired, and noice.nvim loaded;
  - the client that LazyVim starts from the spec: its command, root, and `virgilRoot`;
  - parser diagnostics, cleared by an unsaved edit and back after undo; document symbols; and the project's semantic `TypeError` in `words/Words.v3`, which needs `lib/util` from the Virgil root;
  - definition and hover, by request and through LazyVim's `gd` and `K`; and signature help, workspace symbols, and references by request;
  - rename through `<leader>cr`: the server's refusal while the project has a type error, as noice shows it, then a rename typed into snacks.nvim's prompt after an unsaved fix, and two undos;
  - signature help in noice's window: as `(` is typed, with `<C-k>` in Insert mode and `gK` in Normal mode, and while a call is typed with and without mini.pairs's `)`, with `b: int` highlighted after the `,`; and that LazyVim maps Insert `<C-s>` to Save File;
  - completion in blink.cmp's menu as you type: the keyword `while`, the local `line` first, and the member `length` after `a.`, each accepted with Enter and undone; and nothing from the server in a comment;
  - a project-file edit on disk, then an unsaved one ([Changes on disk](#changes-on-disk));
  - a restart with the bare command, then with the named one ([Neovim 0.11 and 0.12](#neovim-011-and-012)), and a clean server exit.
- **`noroot`** opens `.virgil-lsp.json` with `VIRGIL_LOC` unset, and checks startup, the client's attachment, the `MissingVirgilRoot` diagnostic, the warning "Invalid project file …", and a clean server exit.
- **`noroot-noice-workaround`** repeats `noroot` with noice's LSP message handler turned off, as the [LazyVim section](../../../editors/nvim/README.md#lazyvim) suggests.

## Why tmux

lazy.nvim fires `VeryLazy` only after `UIEnter`, which needs an attached UI. Under `--headless`, LazyVim never loads noice.nvim, mini.pairs, or its keymaps, so the test would check another editor than a user's. tmux gives Neovim a real 200 × 50 terminal without a display. The driver types with `nvim_input()` and reads the editor's state, not the screen; tmux keeps the screen for failures. The startup step fails if no UI attached or `VeryLazy` never fired; the driver never fires it itself.

## Outputs

The output directory gets:

- `provenance.txt`: the date, the repository's commit and whether it is modified, the OS, CPU, Neovim, tmux, git, and Python, the server's `--version`, the archive's SHA-256, and the starter's and every plugin's commit;
- `lazy-install.log`: lazy.nvim's output while it installs the plugins;
- `<run>.results`: a line per check, then `DONE <n> passed, <n> failed, <n> expected failure(s)`; a run whose results don't end with that line, whole and only once, fails;
- `<run>.lsp.log`: Neovim's LSP log at debug level, with the server's stderr;
- `<run>.screen.txt`: the screen at each failed step; when a run times out or is interrupted; and after Neovim exits with a nonzero or unknown status or without a complete `<run>.results`, with what Neovim left on the terminal, such as an error it printed as it exited, and tmux's line with its exit status;
- `summary.txt`: a line per run, its `XFAIL` lines, and the result.

Everything else, the XDG directories, plugins, server, and a copy of the [Neovim test's fixture](../nvim/fixture/), is in a work directory, which is removed at the end unless `--keep-work`. It is under `/tmp` on macOS, where tmux's socket path would be too long under `$TMPDIR`, as for the [Extension Host test](../../../clients/vscode/README.md#extension-host-test), and under `$TMPDIR` elsewhere. A run writes nothing in the checkout, except with `--update-lock`.

Installing the plugins has a 600-second deadline, and each run 240 seconds; `VIRGIL_LAZYVIM_RUN_TIMEOUT` sets another, to test the timeout path. Each run's tmux pane stays open after Neovim exits, so that its screen can be saved, until the script stops its tmux server. After each run, the server and worker processes must have exited by themselves. On a timeout, SIGINT, SIGTERM, or SIGHUP, the script saves the screen and the LSP log, then stops the tmux server and every process of the runs: the tmux server's process tree, the server and worker processes that `observer.lua` recorded, any process whose command line names the work directory, and their descendants. It freezes them with SIGSTOP, the tmux server first, until it finds no new one, so that none starts another process or leaves an orphan while it reads their trees, then kills them with SIGKILL. A frozen Neovim couldn't act on SIGTERM, and on SIGTERM it runs on for seconds and starts processes, such as parser downloads and a new language server. The script looks again every 0.2 seconds for five seconds and removes the work directory only once none is left; otherwise it reports them, keeps the work directory, and exits with a nonzero status.

## Expected results

- `main`: every step passes.
- `noroot`: every step passes except "Invalid project file warning shown", which reports `XFAIL`: noice.nvim at the locked commit drops every server's `window/showMessage` ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)). If that step passes, it reports `XPASS` and fails the test: a lock update has fixed noice, so remove the expected failure from `driver.lua` and update the [LazyVim section](../../../editors/nvim/README.md#lazyvim).
- `noroot-noice-workaround`: every step passes.

`INFO` and `WARN` lines don't change the result.

## Neovim 0.11 and 0.12

Neovim 0.12 has a built-in `:lsp` command, and the locked nvim-lspconfig then defines no `:Lsp*` commands. The driver chooses by capability, `exists(':lsp') == 2`, and the step names say which command ran:

- With `:lsp`, a bare `:lsp restart` restarts only the clients on the current buffer, so the step checks that `words/Words.v3`'s client is replaced and the library file's client keeps running.
- Otherwise, a bare `:LspRestart` restarts every client. On Neovim 0.11 it stops each of the two clients twice, and Neovim then ends the server with signal 15, which the step accepts.
- The named form, `:lsp restart virgil_lsp` or `:LspRestart virgil_lsp`, must exit with status 0 and no signal.

## Changes on disk

Neovim declares `workspace.didChangeWatchedFiles.dynamicRegistration` on macOS and not on Linux, in 0.11 and 0.12 alike ([Changes on disk](../../../editors/nvim/README.md#changes-on-disk)). If the client declares it, the server registers file watchers, and the step checks that the `UnknownField` error appears with no change to a `.v3` buffer. Otherwise the step checks that it doesn't appear within three seconds, and appears after a `.v3` change. The step's name says which case ran. It runs before the project file has been open in a buffer, since closing that buffer also makes the server read the file again.

## Pins

`starter_commit` in `run.sh` pins the starter; `lazy-lock.json` pins every plugin, lazy.nvim included. Before Neovim first starts, `run.sh` clones lazy.nvim and LazyVim at their locked commits. Otherwise the starter would clone lazy.nvim's `stable` branch, and lazy.nvim would install LazyVim alone to import its plugin specs, rewrite the lock from what it had installed, and install the other plugins at their latest commits. `:Lazy! restore` installs the rest; a normal run never syncs or updates. After that, and again after the runs, the script checks that every plugin is at its locked commit and that lazy.nvim's lock lists the same plugins and commits, and fails if not.

The lock doesn't cover what plugins download themselves: nvim-treesitter's parsers, blink.cmp's prebuilt fuzzy matcher, and the tools that mason.nvim installs, such as the Lua language server and the tree-sitter CLI. As for a user's first start of LazyVim, they download in the background during the first run, `main`, into the work directory, and nvim-treesitter builds each parser with the C compiler; the checks neither wait for nor need them. lazy.nvim also checks for plugin updates in the background, without installing them.

To update the pins:

1. For a new starter, change `starter_commit` in `run.sh`.
2. Run `test/e2e/lazyvim/run.sh --update-lock` with an archive or a build. It installs the latest plugins with `:Lazy! sync`, writes `lazy-lock.json`, and then runs the tests with them. It is the only mode that writes to the checkout.
3. Review `git diff`. Run the test on Linux and on macOS, with Neovim 0.11 and 0.12, and update the docs for any change in behavior, such as an `XPASS`.

## What it doesn't check

The driver reads the editor's state: buffers, modes, mappings, noice's message history, the text and highlights of floating windows, and blink.cmp's menu items. It doesn't check what the screen looks like beyond those, such as the layout, colors, icons, or status line.
