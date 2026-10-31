# Neovim / LazyVim

> **Development setup.** See the [project status](../../README.md) for current server features. The plain Neovim configuration is smoke-tested headlessly in CI. A [scripted LazyVim test](../../test/e2e/lazyvim/README.md) runs the LazyVim configuration locally and in [CI](../../test/e2e/lazyvim/README.md#ci), and the [results](../../docs/manual-testing.md#results) record its runs.

Requires an installed Neovim 0.11 or later, `curl`, and the [installed server on PATH](../../README.md#editor-setup). LazyVim additionally requires an existing [LazyVim starter installation](https://www.lazyvim.org/installation) and its prerequisites (including Git and a C compiler for parsers). No editor plugin is needed: Neovim's built-in client talks to `virgil-lsp`. Once the server is installed, [walk through your first project](../../docs/first-project.md): a small example with a deliberate type error, its one-word fix, and a hover and go-to-definition to check.

With a `0.1.0-alpha.5` or later server, [folding ranges](../../docs/architecture.md#folding-ranges) come from brace blocks and multiline comments in the current buffer. Neovim can use them with `:setlocal foldmethod=expr foldexpr=v:lua.vim.lsp.foldexpr()`.

With a `0.1.0-alpha.5` or later server, `textDocument/selectionRange` returns [selection chains](../../docs/architecture.md#selection-ranges) through tokens, bracket groups, declarations, and the current buffer. The headless test checks an exact chain by sending the request directly.

## LazyVim

Download [`lazyvim/virgil.lua`](lazyvim/virgil.lua) to your existing LazyVim installation's `lua/plugins/virgil.lua`. The commands use `$XDG_CONFIG_HOME/nvim`, or `~/.config/nvim` when it is unset or empty. Replace `config_url` with the raw source download location for the release version you installed (or an HTTP directory containing its `editors/nvim/` files). The file is downloaded beside its destination and moved into place only once the download succeeds, so a failed download leaves an earlier installation as it was:

<!-- clean-install: lazyvim -->
```sh
config_url=https://raw.githubusercontent.com/SaltedTan/virgil-language-server/v0.1.0-alpha.5/editors/nvim
nvim_config="${XDG_CONFIG_HOME:-$HOME/.config}/nvim"
mkdir -p "$nvim_config/lua/plugins"
curl --fail --location "$config_url/lazyvim/virgil.lua" -o "$nvim_config/lua/plugins/virgil.lua.part" &&
mv "$nvim_config/lua/plugins/virgil.lua.part" "$nvim_config/lua/plugins/virgil.lua"
```

Make sure `virgil-lsp` is on your `PATH`, or edit `cmd` to give an absolute path. Release archives include the default library; an optional `VIRGIL_LOC` overrides it ([The Virgil root](#the-virgil-root)).

LazyVim's keys reach the server: `gd`, `gy` for type definition, `gI` for implementation, `K`, `gr`, and, for signature help, `gK` in Normal mode or `<C-k>` in Insert mode; noice.nvim also opens signature help as you type `(` or `,`. In LazyVim, `<C-s>` saves the file instead of showing signature help. For [completion](#completion), LazyVim needs nothing more. LazyVim binds no key to type hierarchy; `:lua vim.lsp.buf.typehierarchy("subtypes")` or `"supertypes"` lists the types one level below or above the one at the cursor in the quickfix list, in LazyVim and plain Neovim alike. For [call hierarchy](../../docs/architecture.md#call-hierarchy), `:lua vim.lsp.buf.incoming_calls()` and `vim.lsp.buf.outgoing_calls()` list the calls of the function at the cursor, and the calls it makes, in the quickfix list, and LazyVim's snacks picker binds them to `gai` and `gao`.

LazyVim's noice.nvim drops `window/showMessage` notifications from every language server ([folke/noice.nvim#1074](https://github.com/folke/noice.nvim/issues/1074)), so warnings such as "Invalid project file …" don't appear, although the project file's diagnostics do. To see them, turn off noice's LSP message handler in a plugin spec, so that Neovim's own handler shows them:

```lua
{ "folke/noice.nvim", opts = { lsp = { message = { enabled = false } } } },
```

With `0.1.0-alpha.5` or `main`, `:lua vim.lsp.buf.code_action()` offers **Insert missing ";"** when the current syntax error can be fixed by a proven semicolon insertion. The [code action](../../docs/architecture.md#code-actions) uses a versioned edit before any trailing comment. No other syntax or semantic fixes are provided.

## Plain Neovim

Download [`virgil_lsp.lua`](virgil_lsp.lua) into your configuration's `plugin/` directory (replace `config_url` as above). Neovim loads it automatically at startup after `init.vim` or `init.lua`. This uses the same configuration directory as the LazyVim step; neither init file is created or changed, even for a new configuration. Setting `noloadplugins` or starting with `--noplugin` or `--clean` skips this automatic loading. The file is downloaded beside its destination and moved into place only after a successful download, so a failed or interrupted rerun preserves an earlier installation. Any remaining `.part` file is not sourced: Neovim loads only `*.lua` and `*.vim` plugin files.

<!-- clean-install: plain-nvim -->
```sh
config_url=https://raw.githubusercontent.com/SaltedTan/virgil-language-server/v0.1.0-alpha.5/editors/nvim
nvim_config="${XDG_CONFIG_HOME:-$HOME/.config}/nvim"
mkdir -p "$nvim_config/plugin"
curl --fail --location "$config_url/virgil_lsp.lua" -o "$nvim_config/plugin/virgil_lsp.lua.part" &&
mv "$nvim_config/plugin/virgil_lsp.lua.part" "$nvim_config/plugin/virgil_lsp.lua"
```

Alternatively, add the contents of `virgil_lsp.lua` to your `init.lua`.

To reproduce a problem from a source checkout without your own configuration:

```sh
nvim --clean -u editors/nvim/virgil_lsp.lua path/to/file.v3
```

## The Virgil root

Project files name Virgil's own libraries relative to a Virgil checkout or installation, as in `"virgilDependencies": ["lib/util/*.v3"]` ([Configuration](../../docs/configuration.md#the-virgil-root)). Both configurations send its location as `init_options = { virgilRoot = vim.env.VIRGIL_LOC }`, so an unset `VIRGIL_LOC` omits the option and lets the server use its bundled library. To override it, set `VIRGIL_LOC` before starting Neovim to the absolute physical path of the directory that holds `lib/`:

```sh
export VIRGIL_LOC=$HOME/virgil
```

For a release install, you can select its bundled library explicitly instead (replace the version and target for your install):

<!-- clean-install: nvim-root -->
```sh
export VIRGIL_LOC="$HOME/.local/opt/virgil-lsp-0.1.0-alpha.5-x86-64-linux/virgil"
```

Or replace `vim.env.VIRGIL_LOC` with the path. See [The Virgil root](../../docs/configuration.md#the-virgil-root) for bundle placement, root errors, and projects needing a checkout override. `:lua =vim.lsp.get_clients({ name = "virgil_lsp" })[1].config.init_options` shows what was sent. The option is read when the server starts: after changing it, restart Neovim, or run `:LspRestart` with nvim-lspconfig (`:lsp restart` on Neovim 0.12 and later, where nvim-lspconfig no longer defines `:LspRestart`).

## Project files

Both configurations also attach the server to `.virgil-lsp.json` buffers, so configuration diagnostics follow unsaved edits. That is why `filetypes` includes `json`: the `root_dir` function skips every other JSON buffer, so the server never sees other JSON files.

## Changes on disk

The server notices changes made outside the editor, such as a checkout, only from `workspace/didChangeWatchedFiles` file events ([changes on disk](../../docs/configuration.md#changes-on-disk)). It asks for them when the client declares `workspace.didChangeWatchedFiles.dynamicRegistration`. Neovim 0.11 and 0.12 declare it true on macOS and false on Linux (`:lua =vim.lsp.protocol.make_client_capabilities().workspace.didChangeWatchedFiles`). On macOS, the server registers file watchers and notices project-file changes without a buffer edit. On Linux, with these configurations the server refreshes projects when they are used instead: a project file fixed on disk is noticed when a `.v3` buffer is next opened, changed, or saved, and a source created or deleted outside Neovim when a project file changes. Restart the server (`:LspRestart` with nvim-lspconfig, or `:lsp restart` on Neovim 0.12 and later) after a checkout that adds or removes files.

`vim.lsp.buf.add_workspace_folder()` and `vim.lsp.buf.remove_workspace_folder()` send `workspace/didChangeWorkspaceFolders`, which the server handles.

## Rename

`vim.lsp.buf.rename()` (`grn`) asks the server first whether the name under the cursor can be renamed, and then for the edit, which it applies to the open buffers and the closed files it names ([Rename](../../docs/architecture.md#rename)). The server sends versioned `documentChanges`, and refuses to rename for a client that does not declare them. Neovim 0.11 applies them but does not declare `workspace.workspaceEdit.documentChanges`, so both configurations add it to `capabilities`; `:lua =vim.lsp.get_clients({ name = "virgil_lsp" })[1].capabilities.workspace.workspaceEdit` shows it. Neovim checks the version of only the first document of an edit. A refused rename shows the server's reason, such as "rename needs a program that verifies" while the project has errors.

## Inlay hints

The server supplies inferred local types and direct-call parameter names ([rules](../../docs/architecture.md#inlay-hints)). Plain Neovim enables their display with `vim.lsp.inlay_hint.enable(true)`; LazyVim enables them by default. To disable a kind independently, add `inlayHints = { types = false, parameterNames = true }` to the server's `init_options`, alongside `virgilRoot`. Both default to true. Restart the language server after changing these initialization options. The editor's own hint switch still controls display. The headless and LazyVim tests read exact labels and UTF-16 positions through `vim.lsp.inlay_hint.get()`.

## Semantic tokens

The server colors names by the declarations that they resolve to ([token table](../../docs/architecture.md#semantic-tokens)). Neovim requests the tokens and highlights them by itself, in plain Neovim and in LazyVim, with the groups `@lsp.type.<type>.virgil` and `@lsp.mod.<modifier>.virgil`, such as `@lsp.type.function.virgil` (`:help lsp-semantic-highlight`). To turn them off, add `semanticTokens = false` to the server's `init_options`, alongside `virgilRoot`, and restart the language server. The headless and LazyVim tests read exact token types and UTF-16 positions through `vim.lsp.semantic_tokens.get_at_pos()`.

## Completion

The server offers [completion](../../docs/architecture.md#completion) of keywords in open buffers, even while they fail to parse, and of the declarations in scope in a configured project, after a moment for an analysis. In plain Neovim, ask for it: Neovim 0.11 sets `omnifunc` to `vim.lsp.omnifunc()` when the server attaches, so `<C-x><C-o>` in Insert mode completes the word before the cursor, with no setup. Neovim's built-in LSP completion also has `vim.lsp.completion.get()`, which asks only the servers that `vim.lsp.completion.enable()` enabled for the buffer, so enable it in an `LspAttach` autocommand and map a key to it, as `:help vim.lsp.completion.get()` suggests:

```lua
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(args)
    local client = vim.lsp.get_client_by_id(args.data.client_id)
    if client and client.name == "virgil_lsp" then
      vim.lsp.completion.enable(true, client.id, args.buf)
      vim.keymap.set("i", "<C-Space>", vim.lsp.completion.get, { buffer = args.buf })
    end
  end,
})
```

In either case, `<C-y>` accepts the selected item. Neovim 0.11.6's `autotrigger = true` asks only after one of the server's `completionProvider.triggerCharacters`, which is `.`: it shows a receiver's members as you type `.`, but not names or keywords as you type an identifier. That is read from Neovim's source; the headless test asks for completion directly.

LazyVim needs no setup, and shows the items as you type. Its default completion plugin, blink.cmp, asks every attached server that declares `completionProvider` through its `lsp` source (`sources.default` in LazyVim 16.0.1's `coding.blink` extra), and blink.cmp 1.10.2 asks its sources whenever you type a keyword character (`completion.trigger.show_on_keyword`, on by default, which LazyVim leaves unchanged), with or without trigger characters. A scripted LazyVim session with this server, LazyVim 16.0.1, and blink.cmp 1.10.2 showed keywords and names in the menu as an identifier was typed, and the receiver's members as `.` was typed, for 0.1.0-alpha.2 ([results](../../docs/manual-testing.md#010-alpha2)). blink.cmp added words from the buffer only where the server offered nothing, such as in a comment.

## Headless smoke test

From the repository root, run `make test-nvim` (also included in `make test`).
The test uses the plain configuration above with the built server on `PATH` and
`--headless --clean -n` (no swap files), with `VIRGIL_LOC` set to the pinned
checkout in `vendor/virgil`. It checks client attachment, parser diagnostics
published and cleared after an unsaved edit, document symbols, including a
variant case nested in its variant, semantic diagnostics for the fixture's
project, which uses `lib/util` from the Virgil root, a definition in that
closed library file, a hover over the same use, signature help in a call on the
same line, its references in that file and other closed library files, its
document highlight, only that use's exact range in the current buffer with the
Read kind, type definition on a call result into the library and no location for a built-in value, the jump through `vim.lsp.buf.type_definition()`, the exact ranges and kinds of a local's highlights in another
fixture file (its declaration Text, its assignment and increment Write, and
its other uses Read), the exact subclasses and overriding methods that
implementation returns in a class hierarchy, and the jump through
`vim.lsp.buf.implementation()` to a class's one subclass, the exact type
hierarchy items of a class, its supertype, and its subtypes, with their data,
and the quickfix list that `vim.lsp.buf.typehierarchy("subtypes")` fills, the
exact call hierarchy items and calls of a function, its two callers and the
calls of one of them, with their data, and the quickfix list that
`vim.lsp.buf.incoming_calls()` fills, a rename
of a local through `vim.lsp.buf.rename()` once an unsaved edit fixes the type
error, and its undo, keyword completion of `whi` to `while` and completion of
`li` to the local `line` in the unsaved, unparsable buffer, each applied with
Neovim's own edit and undone, a workspace symbol that nothing uses, configuration
diagnostics after an unsaved edit to the project file, that other JSON buffers
stay detached, and a graceful server shutdown with exit status 0. The fixture
is copied into a temporary directory; editor state is kept there, and the
directory is removed afterward. The VS Code
[Extension Host test](../../clients/vscode/README.md#extension-host-test) and
[LazyVim test](../../test/e2e/lazyvim/README.md) use the same fixture, so a
change to it affects all three suites. The LazyVim test adds one file of its
own to its copy, `words/CallPicker.v3`, for its outgoing-calls picker.

The test skips with a message if Neovim is missing or older than 0.11. Set
`REQUIRE_NVIM=1` to fail instead of skipping, as both CI jobs do. See the
[tested editor versions](../../docs/compatibility.md#editors).

### Result stream

To record the run as a stream of result events, give `run.sh` a file, then
validate it:

```sh
test/e2e/nvim/run.sh build/virgil-lsp --results build/nvim.jsonl
python3 scripts/editor-results.py validate build/nvim.jsonl
```

The test's output is the same, with a last line that sums up the run, and
`run.sh` exits with 0 only if the run passed. Without Neovim, the run fails
instead of skipping. `--release VERSION` and `--purpose PURPOSE` set the
stream's `release` and `purpose`, which are otherwise none and `development`.
[The headless Neovim writer](../../test/e2e/RESULTS.md#the-headless-neovim-writer)
describes what the stream records.

To inspect a failure, list the events that didn't pass, with their reasons, and
the run-end, with its issues and cleanup:

```sh
grep -v '"status":"pass"' build/nvim.jsonl
```

The first failing check's `reason` is the message of the assertion in
`check.lua` that failed, with its line; the checks after it are skipped. As
without `--results`, a failing run prints the last 100 lines of the protocol
log. With `--results`, cleanup kills and verifies every recorded descendant
PID and process group. Neovim reports `virgil-lsp` descendants on LspAttach,
every 500 ms, and in a final synchronous exit scan; the launcher also samples
the process tree at reports, about once a second, and before stopping Neovim.
On Linux the launcher attempts to become a child subreaper and reaps orphaned
descendants. On macOS, or if Linux subreaper setup fails, a process whose parent
exits before any scan has seen it is not covered.

A stream without a run-end is from a run that was interrupted or killed.
SIGINT, SIGTERM, and SIGHUP received before finalization stop Neovim and leave
no run-end. During finalization those signals are ignored by the launcher and
its finalizer, so the run-end and shell exit status agree. An interruption
already received still leaves no run-end and exits with 128 plus the signal
number.

## Troubleshooting

- `:checkhealth vim.lsp` shows whether the client attached.
- `:LspInfo` (with nvim-lspconfig on Neovim 0.11) or `:lua =vim.lsp.get_clients()` lists running clients.
- For the client log, including the server's stderr and LSP messages, see [Collecting logs](../../docs/manual-testing.md#collecting-logs).
- [Troubleshooting](../../docs/troubleshooting.md) covers a server that doesn't start, files without diagnostics or navigation, and changes on disk. The [privacy statement](../../PRIVACY.md) says what the server and these configurations read, write, and start.
