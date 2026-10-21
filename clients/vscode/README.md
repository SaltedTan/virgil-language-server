# VS Code extension

> **Prerelease.** The extension is packaged as a VSIX to [install by hand](#installing-from-a-vsix); it isn't published to the Visual Studio Marketplace or Open VSX. It doesn't include the server, which you install separately. See the [project status](../../README.md) for current server features, [compatibility](../../docs/compatibility.md#editors) for the supported VS Code and server versions, and [known limitations](../../docs/known-limitations.md) for what the alpha doesn't do yet.

A thin TypeScript extension built on `vscode-languageclient/node`. It:

- contributes the `virgil` language for `.v3` files, with comment and bracket configuration and a small TextMate grammar;
- starts `virgil-lsp --stdio` when a `virgil` document opens or the workspace contains a `.virgil-lsp.json` project file;
- sends the server `virgil` documents (saved files and untitled buffers) and `.virgil-lsp.json` buffers, so that configuration diagnostics follow unsaved edits to project files;
- sends the [Virgil root](#settings) that project files' `virgilDependencies` are relative to;
- watches `.virgil-lsp.json` files and `.v3` files, so that the server notices changes made outside the editor, such as a checkout ([changes on disk](../../docs/configuration.md#changes-on-disk)), and passes on workspace folders added to or removed from a multi-root workspace;
- reports startup problems, the server's stderr, and the optional message trace in the "Virgil Language Server" output channel;
- sends `shutdown` and then `exit` when it is deactivated, for example when the window closes;
- runs on the workspace side (`extensionKind: ["workspace"]`), so that with Remote-SSH and Remote-WSL the server starts on the remote machine, next to the files ([ADR-0003](../../docs/decisions/0003-supported-platforms.md)).

## Installing from a VSIX

Requirements: a VS Code version and a server version that work with the extension's version, both listed in [compatibility](../../docs/compatibility.md#editors). Follow the [release installation instructions](../../docs/releasing.md#installing), or [build the server from source](../../README.md#building).

1. Get `virgil-lsp-vscode-<version>.vsix`: from a [GitHub prerelease](https://github.com/SaltedTan/virgil-language-server/releases), which attaches the VSIX that the release workflow tested against that release's server, and lists it in `SHA256SUMS` ([Releases](../../docs/releasing.md#vs-code-extension)); from the `vsix` artifact of a [CI](../../.github/workflows/ci.yml) run (GitHub downloads artifacts as a zip file that contains the VSIX); or by [packaging](#packaging) it yourself.
2. Install it, either in VS Code with **Extensions: Install from VSIX...** from the Command Palette, or from a terminal:

   ```sh
   code --install-extension virgil-lsp-vscode-0.1.0.vsix
   ```

3. Unless `virgil-lsp` is on `PATH`, set `virgil.server.path` to its absolute path, and for projects with `virgilDependencies`, set `virgil.virgilRoot` ([Settings](#settings)).

With Remote-SSH or Remote-WSL, the extension runs on the remote machine, next to the server and the files, so install it there. In a window connected to the remote, either run **Extensions: Install from VSIX...**, or copy the VSIX to the remote machine and run `code --install-extension` in that window's integrated terminal. The Extensions view then lists the extension under the remote, such as "SSH: host" or "WSL: Ubuntu", not under "Local". Set `virgil.server.path` and `virgil.virgilRoot` in the remote settings.

VS Code doesn't update an extension installed from a VSIX. To upgrade, install the newer VSIX the same way. To remove the extension, uninstall it from the Extensions view, or run `code --uninstall-extension virgil-language-server.virgil-lsp-vscode`.

### Versions

The extension's versions are a sequence of their own, independent of the server's; [compatibility](../../docs/compatibility.md#editors) records which server versions each one works with. They are plain `major.minor.patch` numbers, starting at `0.1.0`, without a suffix such as `-alpha.1`, because the Visual Studio Marketplace rejects those. Every VSIX is a prerelease for now, marked in two ways:

- `package.json` sets `"preview": true`. VS Code shows a "Preview" label for the extension, including when it's installed from a VSIX.
- `npm run package` passes `--pre-release` to vsce, which marks the package as a pre-release in its manifest. VS Code doesn't use this mark for a VSIX installed by hand; it takes effect only if the package is published to a marketplace, where it puts the version on the pre-release channel. VS Code's own `--pre-release` option for `--install-extension` likewise only applies to extensions installed from a marketplace.

## Setup

To run the extension from source instead of installing a VSIX, you need a [supported VS Code version](../../docs/compatibility.md#editors), Node.js 22 or later with `npm` (required by the Extension Host test and packaging dependencies), and a built server.

1. Build the server from the repository root (`make`), and either put `build/` on your `PATH` or set `virgil.server.path` to the absolute path of `build/virgil-lsp`. Follow the [executable placement guidance](../../README.md#building).
2. Install the extension's dependencies from the lockfile and compile it:

   ```sh
   cd clients/vscode
   npm ci
   npm run compile
   ```

3. Start VS Code with the extension loaded, either from a terminal:

   ```sh
   code --extensionDevelopmentPath="$PWD" path/to/virgil/project
   ```

   or by opening `clients/vscode` as a folder and running **Run Virgil extension** (F5). With Remote-SSH or Remote-WSL, open `clients/vscode` in the remote window and use F5. The Extension Development Host then connects to the same remote machine, and the extension runs there.

The extension runs only in [trusted workspaces](https://code.visualstudio.com/docs/editor/workspace-trust), because the server analyzes the workspace's files.

The server's features need no further settings. [Completion](../../docs/architecture.md#completion) of keywords and of the declarations in scope, for example, appears in VS Code's suggestion list as you type, like other completion providers' items, and typing `.` lists the receiver's [members](../../docs/architecture.md#member-completion).

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `virgil.server.path` | `virgil-lsp` | A command name looked up on the extension host's `PATH`, or an absolute path. Relative paths are rejected. This is a machine setting: set it in user settings, or in the remote settings with Remote-SSH and Remote-WSL. Workspace settings can't change it. Changing it restarts the server. |
| `virgil.virgilRoot` | empty | The absolute physical path of the Virgil checkout or installation, the directory that holds `lib/`. Project files' `virgilDependencies`, such as `lib/util/*.v3`, are relative to it ([Configuration](../../docs/configuration.md#the-virgil-root)). When empty, the extension uses `VIRGIL_LOC` from the extension host's environment: with Remote-SSH or Remote-WSL, the remote machine's. A machine setting, like `virgil.server.path`. Changing it restarts the server; a change to `VIRGIL_LOC` needs a new window. |
| `virgil.trace.server` | `messages` | How much of each LSP message the trace records: `messages`, `compact`, or `verbose`. |

To record the trace, set the output channel's log level to Trace: run **Developer: Set Log Level...**, choose "Virgil Language Server", then Trace. The trace stops when you set the level back to Info.

At startup, the output channel names the Virgil root and where it came from, or says there is none. Without one, a project file with `virgilDependencies` gets a `MissingVirgilRoot` diagnostic, and its documents stay in single-file mode. A relative `virgil.virgilRoot` isn't sent: the extension shows a warning instead, and doesn't fall back to `VIRGIL_LOC`.

There is no setting for the project file: the server uses the nearest `.virgil-lsp.json` above each document ([Configuration](../../docs/configuration.md#workspace-folders-and-active-projects)).

Command: **Virgil: Restart Language Server**.

## Startup checks

Before it starts the server, the extension checks that:

- VS Code isn't running natively on Windows. Use WSL 2 instead ([compatibility](../../docs/compatibility.md#host-platforms)).
- `virgil.server.path` names an executable file, directly or on `PATH`.
- `virgil-lsp-worker` is executable in the same directory as `virgil-lsp`. Symlinks are followed, because the server looks for the worker beside its own resolved path. A wrapper script therefore needs the worker beside it too.

If a check fails, or the server doesn't start, the extension writes the reason to the "Virgil Language Server" output channel and shows an error notification with a **Show Output** button. Fix the problem, then run **Virgil: Restart Language Server**.

## Syntax highlighting

`syntaxes/virgil.tmLanguage.json` was written for this project from Virgil's lexical rules in the pinned Virgil checkout (`vendor/virgil/doc/virgil-grammar.ebnf` and the Aeneas lexer). It covers comments, string and character literals with their escapes, numbers, keywords, primitive type names, declaration names, and representation hints. It doesn't use or adapt any other Virgil grammar or extension. Code from repositories without an explicit license, such as [`linxuanm/virgil-vsc`](https://github.com/linxuanm/virgil-vsc), must not be copied ([ROADMAP](../../ROADMAP.md#open-source-and-licensing-plan)).

## Development

```sh
npm run check     # type-check only
npm test          # compile and run lifecycle regression tests
npm run test:e2e  # compile and run the Extension Host test against build/virgil-lsp
npm run package   # compile and package the extension as a VSIX
npm run watch     # recompile on change
```

CI installs the dependencies with `npm ci`, then runs `npm run check` and `npm test` on Linux, [packages](#packaging) the VSIX, and uploads it as the workflow run's `vsix` artifact. A separate Linux job builds the server and runs the [Extension Host test](#extension-host-test), first on the extension in this directory and then on a VSIX that it packages and installs. Each job packages its own VSIX, so the uploaded artifact isn't byte-identical to the tested package. The [release workflow](../../docs/releasing.md#how-the-workflow-works) instead packages one VSIX, tests that file against the server from the release's Linux archive, and attaches the same file. The [manual checklist](#manual-checklist) covers what the tests don't.

### Packaging

`npm run package` compiles the extension and runs the pinned [`@vscode/vsce`](https://github.com/microsoft/vscode-vsce), which writes `virgil-lsp-vscode-<version>.vsix` in this directory. The VSIX contains `out/extension.js`, `package.json`, the grammar, the language configuration, this README, the repository's `LICENSE` (through the symbolic link `LICENSE` here), and the runtime dependencies from `node_modules`, each with its own license file ([third-party notices](../../THIRD_PARTY_NOTICES.md#vs-code-client-npm)). vsce leaves out the development dependencies, and `.vscodeignore` leaves out the TypeScript sources, tests, source maps, type declarations, and the Extension Host test's VS Code download. To see what a VSIX contains, run `unzip -l virgil-lsp-vscode-0.1.0.vsix`; `npx vsce ls` lists what the next package would contain.

In the packaged README, vsce turns relative links into links to `main` on GitHub. It also warns that the extension isn't bundled: the runtime dependencies stay separate files, so that their license files ship unchanged. `"private": true` in `package.json` only stops `npm publish`; vsce ignores it.

### Extension Host test

`npm run test:e2e` runs the compiled extension in a real VS Code Extension Host against the built server. It needs the [setup prerequisites](#setup) and `build/virgil-lsp` and `build/virgil-lsp-worker` (`make` at the repository root); to test another build, pass its `virgil-lsp`: `npm run test:e2e -- path/to/virgil-lsp`. On first use it downloads the [tested VS Code version](../../docs/compatibility.md#editors) into `.vscode-test/` with [`@vscode/test-electron`](https://github.com/microsoft/vscode-test). On Linux without a display, run it under `xvfb-run -a npm run test:e2e`. It hasn't been run on macOS yet.

The test copies the [Neovim smoke-test fixture](../../test/e2e/nvim/fixture/) into a temporary directory, so that both editors check the same behaviour and edits don't touch the checkout. It starts VS Code twice, each time with temporary user data and extensions directories, only the built-in extensions besides this one, workspace trust disabled, and `VIRGIL_LOC` removed from the environment. It also removes the `VSCODE_*` variables that a terminal inside VS Code sets, so that a test started from, for example, a Remote-SSH terminal doesn't reach the editor that owns the terminal. User settings point `virgil.server.path` at the server and `virgil.virgilRoot` at the checkout's `vendor/virgil`. Through VS Code APIs and commands, it checks that:

- with no folder open, opening `bad.v3` activates the extension, which starts the server from `virgil.server.path`;
- with the fixture as the workspace folder, its `.virgil-lsp.json` activates the extension before any document opens, and the output channel names the Virgil root from `virgil.virgilRoot`;
- `bad.v3` has the parser diagnostic "invalid start of expression" (`ParseError`, from `aeneas`) with the empty range at line 3, column 27, and no document symbols;
- an unsaved edit clears the diagnostic, and `vscode.executeDocumentSymbolProvider` then returns the component `C` and the variant `V`, with its case `A` as its child; undoing the edit brings the diagnostic back;
- `words/Words.v3` has the single semantic diagnostic that needs `lib/util` from the Virgil root, and `vscode.executeDefinitionProvider` on its `StringBuilder` returns the name of `class StringBuilder` in the closed `lib/util/StringBuilder.v3`, `vscode.executeHoverProvider` there returns its signature in a `virgil` code block, `vscode.executeSignatureHelpProvider` just after the `(` of `.puts(a)` on the same line returns `def puts(s: Array<byte>) -> this` with its one parameter at label offsets 9 to 23, `vscode.executeReferenceProvider` there returns the use, the class's name, and uses in other closed `lib/util` files, each spelling `StringBuilder`, and `vscode.executeWorkspaceSymbolProvider` for `join`, which nothing uses, returns that method in `words/Words.v3` first;
- once an unsaved edit fixes that type error, `vscode.executeDocumentRenameProvider` on the local `line` returns exactly its two uses, renamed to `text`; `vscode.workspace.applyEdit` applies the server's versioned edit to the open document, one undo restores both uses, and another restores the type error;
- in `words/Words.v3`, after an unsaved line `whi` that the file doesn't parse with, `vscode.executeCompletionItemProvider` at its end returns, as its `Keyword` items, the keywords that start a statement, in order, among them `while`, which replaces `whi`; applying it gives `while`, and two undos restore the file;
- after an unsaved line `li` there instead, the items that the server ranks start with the local `line`, then `a`, `b`, `this`, and `join`; `line` is a `Variable` with the detail `var line: int` that replaces `li`, applying it gives `line`, and two undos restore the file;
- after an unsaved line `a.` there instead, completion with the trigger character `.` returns only the string's member `length`, a `Field` with the detail `def length: int` that inserts after the `.`; applying it gives `a.length`, and two undos restore the file;
- an unsaved edit to `.virgil-lsp.json` produces an `UnknownField` diagnostic, and undoing it clears the diagnostic;
- **Virgil: Restart Language Server** makes the server exit with status 0 (`Server process exited successfully`), and a new server starts and answers document symbol requests.

To run the same checks on a packaged extension, package it and pass the VSIX: `npm run package`, then `npm run test:e2e -- --vsix virgil-lsp-vscode-0.1.0.vsix`. Before each start, the test then installs the VSIX into the empty extensions directory with VS Code's `--install-extension`, as a user would. VS Code loads the installed copy instead of this directory, and the suite checks that the extension it finds is that copy.

Each start of VS Code and each installation of a VSIX has a 120-second timeout and runs in a process group of its own. On a timeout, SIGINT, or SIGTERM, the test sends SIGTERM to the whole group, then SIGKILL if it remains after five seconds. It waits without a deadline for the group to exit before removing its temporary directory. On a launch failure, the test prints the "Virgil Language Server" output channel's log, which also holds the server's stderr. The temporary directory is removed afterward.

## Manual checklist

Run it on each platform before a release, with the extension [installed from the release's VSIX](#installing-from-a-vsix), and after changes to the extension or to server startup and shutdown. Use a copy of the [Neovim smoke-test fixture](../../test/e2e/nvim/fixture/) (`cp -R test/e2e/nvim/fixture /tmp/virgil-check`), so that edits don't touch the checkout. Its `bad.v3` contains a syntax error on line 3. Its project file defines a project `words`, which uses Virgil's `lib/util`. Set `virgil.virgilRoot` to the absolute path of the checkout's `vendor/virgil` before you start.

On Linux, the [Extension Host test](#extension-host-test) automates the steps marked *(automated on Linux)*. Run them by hand on the other platforms.

For each VS Code setup in the [manual test matrix](../../docs/manual-testing.md#matrix), check the [known limitations](../../docs/known-limitations.md) and follow these steps:

1. Open the fixture folder with the extension installed or loaded from source (see [Setup](#setup)) and open `bad.v3`. The status bar shows "Virgil" as the language. *(Automated on Linux.)*
2. The "Virgil Language Server" output channel shows `Virgil root: <root> (from virgil.virgilRoot)`, `Starting <path> --stdio`, and `Started virgil-lsp <version>`. With a remote, both paths are on the remote machine. *(Automated on Linux.)*
3. The Problems view shows one error, "invalid start of expression" from `aeneas`, at line 3, column 27. *(Automated on Linux.)*
4. Without saving, replace the file's contents with `component C { def value = 1; }` and, on a second line, `type V { case A(x: int); }`. The error disappears. *(Automated on Linux.)*
5. The Outline view shows the component `C` and the variant `V`, with its case `A` inside it. *(Automated on Linux.)*
6. Undo the edit. The error comes back without saving. *(Automated on Linux.)*
7. Open `words/Words.v3`. The Problems view shows one error for it, "expected int in var initialization, got Array<byte>" from `aeneas`, on line 7. Without the library, it would also report that `StringBuilder` cannot be found. **Go to Definition** on `StringBuilder` opens the library's `lib/util/StringBuilder.v3` at `class StringBuilder`, hovering over it shows `class StringBuilder`, **Trigger Parameter Hints** (Ctrl+Shift+Space) just after the `(` of `.puts(a)` on the same line shows `def puts(s: Array<byte>) -> this` with `s: Array<byte>` highlighted, **Find All References** on it lists this use, the class's name, and its uses in other `lib/util` files, and **Go to Symbol in Workspace** (Ctrl+T) for `join` lists the method `join` in `words/Words.v3`. *(Automated on Linux.)*
8. In `words/Words.v3`, run **Rename Symbol** (F2) on `line` in line 7. A notification says "the project's analysis has errors; rename needs a program that verifies". Without saving, change line 7 to `var line = StringBuilder.new().puts(a).putc(' ').puts(b).toString();` and line 8 to `return line.length;`; the error disappears. **Rename Symbol** on `line` now selects it; enter `text`. Both uses become `text`, unsaved. Undo once: both are `line` again. Undo once more: the type error comes back. *(Automated on Linux, except the notification.)*
9. In `words/Words.v3`, without saving, add a line after line 7, indented like it, and type `w` on it. The suggestion list shows `while`, marked as a keyword. Keep typing: after `whi`, the list shows `while` and not the other keywords, such as `return`. Accept `while` (Enter or Tab): the line reads `while`. Undo once: it reads `whi` again. Undo until the line is gone and the file is unmodified. Add the line again and type `li`: after a moment, the list shows `line` first, marked as a variable, with `var line: int` beside it. Accept it: the line reads `line`. Undo until the line is gone and the file is unmodified. Add the line again and type `a.`: as you type the `.`, the list shows only `length`, marked as a field, with `def length: int` beside it, since `a` is a string. Accept it: the line reads `a.length`. Undo until the line is gone and the file is unmodified. Inside the comment on line 3, Ctrl+Space and typing `.` show no suggestions from the server. *(Automated on Linux, except the suggestion list.)*
10. Open `.virgil-lsp.json` and, without saving, add `"bogus": 1,` after the opening brace. The Problems view shows an `UnknownField` error from `virgil-lsp` on that line. Undo the edit; the error disappears. *(Automated on Linux.)*
11. Close `.virgil-lsp.json`. From a terminal, add `"bogus": 1,` after its opening brace on disk. Without editing a `.v3` file, the Problems view shows the `UnknownField` error. Remove it on disk; the error disappears, and `words/Words.v3` reports its error again.
12. Clear `virgil.virgilRoot`, with `VIRGIL_LOC` unset. The server restarts, the output channel says there is no Virgil root, and `.virgil-lsp.json` gets a `MissingVirgilRoot` error. Restore the setting.
13. Run **Virgil: Restart Language Server**. The output channel shows `Server process exited successfully`, which means the server exited with status 0 after `shutdown` and `exit`, and then the start messages again. *(Automated on Linux.)* Closing the window stops the server the same way.
14. Set `virgil.server.path` to a nonexistent absolute path. The output channel reports that the file is missing or not executable, and an error notification appears. Restore the setting.
15. With a copy of `virgil-lsp` in a directory without `virgil-lsp-worker`, point `virgil.server.path` at the copy. The output channel reports the missing worker. Restore the setting.
16. Set the channel's log level to Trace. The channel shows the LSP messages, including `textDocument/didChange` after an edit.
17. Close any buffers that contain a known parser crash input, then terminate the server process to trigger automatic recovery. During recovery, separately try **Virgil: Restart Language Server**, changing `virgil.server.path`, and closing the window. Each action waits for recovery to finish; restart and configuration changes stop the recovered server before starting its replacement, and closing the window sends `shutdown` then `exit` to the recovered server. Confirm that only one server remains after a restart and none remains after closing the window.

Record the VS Code version, the platform, and the server version in the pull request, and for a release in the [manual test results](../../docs/manual-testing.md#results).
