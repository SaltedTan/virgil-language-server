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
   code --install-extension virgil-lsp-vscode-0.1.2.vsix
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

### With the Virgil extension installed

Virgil's author has a VS Code extension of their own, [titzer/virgil-vscode](https://github.com/titzer/virgil-vscode) (`titzer.virgil`, version 0.1.0). It has syntax highlighting from a TextMate grammar, semantic highlighting from a tree-sitter parse of the open file, and a debugger that runs a program in the compiler's interpreter (`v3c -debug`), but no language server. As of 2026-10-22 it isn't published: it has no GitHub release, and it is on neither the Visual Studio Marketplace nor Open VSX.

This extension keeps its own grammar and language configuration, as decided in [#171](https://github.com/SaltedTan/virgil-language-server/issues/171). Both extensions contribute the `virgil` language for `.v3` files, a language configuration, and a TextMate grammar with the same scope name, `source.virgil`. VS Code 1.142.0 used one grammar for that scope, and merged the two language configurations property by property, the one registered later supplying each property that both define. A test with VS Code 1.142.0 on Linux, with both extensions installed in either order, found:

- `.v3` files were colored by this extension's grammar, and this extension's language configuration applied, so `"` and `'` didn't auto-close inside comments and strings. VS Code 1.142.0 ordered the installed extensions by folder name, and the grammar and configuration registered last won: `virgil-language-server.virgil-lsp-vscode-…` sorts after `titzer.virgil-…`. That is what the test observed; neither extension declares this order, and VS Code doesn't guarantee it.
- The other extension's tree-sitter semantic colors appeared on top of this grammar, coloring declarations, parameters, types, and calls. Its debugger started and stopped on entry.
- The release's [Extension Host test](https://github.com/SaltedTan/virgil-language-server/blob/v0.1.0-alpha.2/clients/vscode/README.md#extension-host-test) passed unchanged in both install orders; the [manual checklist](#manual-checklist) wasn't run.
- The only sign of the overlap was a message in the developer console, "Overwriting grammar scope name to file mapping for scope source.virgil". It is expected with both installed, not a bug. Nothing appeared in the UI or the log files.

Both extensions run on the workspace side, so with Remote-SSH both would be installed on the remote host. That case wasn't tested.

## Development

```sh
npm run check     # type-check only
npm test          # compile and run lifecycle regression tests, and the Remote-SSH test's own
npm run test:e2e  # compile and run the Extension Host test against build/virgil-lsp
npm run test:e2e:remote-ssh -- --vsix <vsix>  # run its checks through Remote-SSH (Linux)
npm run package   # compile and package the extension as a VSIX
npm run watch     # recompile on change
```

CI installs the dependencies with `npm ci`, then runs `npm run check` and `npm test` on Linux, [packages](#packaging) the VSIX, and uploads it as the workflow run's `vsix` artifact. A separate Linux job builds the server and runs the [Extension Host test](#extension-host-test), first on the extension in this directory and then on a VSIX that it packages and installs. Another job runs it on macOS, on the extension in this directory, with the server that the macOS build job built and tested. A weekly workflow, `editor-tests.yml`, also runs it on macOS on a VSIX that it packages and installs, and on Linux through [Remote-SSH](#remote-ssh-test). CI's two Linux jobs each package their own VSIX, so the uploaded artifact isn't byte-identical to the tested package. The [release workflow](../../docs/releasing.md#how-the-workflow-works) instead packages one VSIX, tests that file against the server from the release's Linux archive, and attaches the same file. The [manual checklist](#manual-checklist) covers what the tests don't.

### Packaging

`npm run package` compiles the extension and runs the pinned [`@vscode/vsce`](https://github.com/microsoft/vscode-vsce), which writes `virgil-lsp-vscode-<version>.vsix` in this directory. The VSIX contains `out/extension.js`, `package.json`, the grammar, the language configuration, this README, the repository's `LICENSE` (through the symbolic link `LICENSE` here), and the runtime dependencies from `node_modules`, each with its own license file ([third-party notices](../../THIRD_PARTY_NOTICES.md#vs-code-client-npm)). vsce leaves out the development dependencies, and `.vscodeignore` leaves out the TypeScript sources, tests, source maps, type declarations, and the Extension Host test's VS Code download. To see what a VSIX contains, run `unzip -l virgil-lsp-vscode-0.1.2.vsix`; `npx vsce ls` lists what the next package would contain.

In the packaged README, vsce turns relative links into links to `main` on GitHub. It also warns that the extension isn't bundled: the runtime dependencies stay separate files, so that their license files ship unchanged. `"private": true` in `package.json` only stops `npm publish`; vsce ignores it.

### Extension Host test

`npm run test:e2e` runs the compiled extension in a real VS Code Extension Host against the built server. It needs the [setup prerequisites](#setup) and `build/virgil-lsp` and `build/virgil-lsp-worker` (`make` at the repository root); to test another build, pass its `virgil-lsp`: `npm run test:e2e -- path/to/virgil-lsp`. On first use it downloads the [tested VS Code version](../../docs/compatibility.md#editors) into `.vscode-test/` with [`@vscode/test-electron`](https://github.com/microsoft/vscode-test). On Linux without a display, run it under `xvfb-run -a npm run test:e2e`. It also runs on macOS, where it needs no `xvfb-run`, and [CI](#development) runs it there too.

The test copies the [Neovim smoke-test fixture](../../test/e2e/nvim/fixture/) into a temporary directory, so that both editors check the same behavior and edits don't touch the checkout. On macOS that directory is under `/tmp`, because under the system temporary directory VS Code's socket path would be too long. It starts VS Code twice, each time with temporary user data and extensions directories, only the built-in extensions besides this one, workspace trust disabled, and `VIRGIL_LOC` removed from the environment. It also removes the `VSCODE_*` variables that a terminal inside VS Code sets, so that a test started from, for example, a Remote-SSH terminal doesn't reach the editor that owns the terminal. User settings point `virgil.server.path` at the server and `virgil.virgilRoot` at the checkout's `vendor/virgil`, and turn off VS Code's word-based suggestions, so that completion returns only the server's items. Through VS Code APIs and commands, it checks that:

- with no folder open, opening `bad.v3` activates the extension, which starts the server from `virgil.server.path`;
- with the fixture as the workspace folder, its `.virgil-lsp.json` activates the extension before any document opens, and the output channel names the Virgil root from `virgil.virgilRoot`;
- `bad.v3` has the parser diagnostic "invalid start of expression" (`ParseError`, from `aeneas`) with the empty range at line 3, column 27, and no document symbols;
- an unsaved edit clears the diagnostic, and `vscode.executeDocumentSymbolProvider` then returns the component `C` and the variant `V`, with its case `A` as its child; undoing the edit brings the diagnostic back;
- `words/Words.v3` has the single semantic diagnostic that needs `lib/util` from the Virgil root, and `vscode.executeDefinitionProvider` on its `StringBuilder` returns the name of `class StringBuilder` in the closed `lib/util/StringBuilder.v3`, `vscode.executeHoverProvider` there returns its signature in a `virgil` code block, `vscode.executeSignatureHelpProvider` just after the `(` of `.puts(a)` on the same line returns `def puts(s: Array<byte>) -> this` with its one parameter at label offsets 9 to 23, `vscode.executeReferenceProvider` there returns the use, the class's name, and uses in other closed `lib/util` files, each spelling `StringBuilder`, and `vscode.executeWorkspaceSymbolProvider` for `join`, which nothing uses, returns that method in `words/Words.v3` first;
- while that type error stands, `vscode.prepareRename` and `vscode.executeDocumentRenameProvider` on the local `line` fail with the server's reason, "the project's analysis has errors; rename needs a program that verifies";
- once an unsaved edit fixes that type error, `vscode.executeDocumentRenameProvider` on the local `line` returns exactly its two uses, renamed to `text`; `vscode.workspace.applyEdit` applies the server's versioned edit to the open document, one undo restores both uses, and another restores the type error;
- in `words/Words.v3`, after an unsaved line `whi` that the file doesn't parse with, `vscode.executeCompletionItemProvider` at its end returns, as its `Keyword` items, the keywords that start a statement, in order, among them `while`, which replaces `whi`; applying it gives `while`, and two undos restore the file;
- after an unsaved line `li` there instead, the items that the server ranks start with the local `line`, then `a`, `b`, `this`, and `join`; `line` is a `Variable` with the detail `var line: int` that replaces `li`, applying it gives `line`, and two undos restore the file;
- after an unsaved line `a.` there instead, completion with the trigger character `.` returns only the string's member `length`, a `Field` with the detail `def length: int` that inserts after the `.`; applying it gives `a.length`, and two undos restore the file;
- after an unsaved line with a call that has no `)` there instead, `vscode.executeSignatureHelpProvider` at its end returns `def puts(s: Array<byte>) -> this` with its parameter active for `StringBuilder.new().puts(`, and `def max(a: int, b: int) -> int` with the second parameter active for `var n = Ints.max(1, `;
- inside the comment on line 3, completion returns no items, both after the word `StringBuilder` and after a `.` typed there, with `.` as the trigger character;
- with `.virgil-lsp.json` closed, no editor open, and no analysis pending, `"bogus": 1,` written into it on disk produces an `UnknownField` diagnostic, which only the client's file watchers can bring; restoring the file clears it, and `words/Words.v3` has its type error again;
- an unsaved edit to `.virgil-lsp.json` produces an `UnknownField` diagnostic, and undoing it clears the diagnostic;
- **Virgil: Restart Language Server** makes the server exit with status 0 (`Server process exited successfully`), and a new server starts and answers document symbol requests;
- at the output channel's Trace log level, set from the Output view's menu, the channel's log records `textDocument/didChange` after an edit. This check uses internal output-channel commands; recheck them whenever `VSCODE_VERSION` in [`run.cjs`](test/e2e/run.cjs) changes;
- clearing `virgil.virgilRoot` restarts the server, and the output channel says that there is no Virgil root. In a separate check, the server starts without one, reporting its version, keeps running with no unclean exit, and answers document symbol requests, and `.virgil-lsp.json` gets a `MissingVirgilRoot` diagnostic, which clears once the setting is restored;
- a nonexistent absolute `virgil.server.path` makes the output channel report the missing file, and no server starts, so the diagnostics and document symbols are gone; restoring the setting starts the server again, which publishes the type error again;
- `virgil.server.path` set to a copy of `virgil-lsp` in a directory without `virgil-lsp-worker` makes the output channel report the missing worker, and no server starts; restoring the setting starts the server again.

Each check that changes a setting or a file on disk restores it, and waits until a server serves the project as before, so that a failed check doesn't affect the next. The error notifications and the rendered views remain for the [manual checklist](#manual-checklist).

To run the same checks on a packaged extension, package it and pass the VSIX: `npm run package`, then `npm run test:e2e -- --vsix virgil-lsp-vscode-0.1.2.vsix`. Before each start, the test then installs the VSIX into the empty extensions directory with VS Code's `--install-extension`, as a user would. VS Code loads the installed copy instead of this directory, and the suite checks that the extension it finds is that copy.

Each start of VS Code and each installation of a VSIX has a 120-second timeout and runs in a process group of its own. On a timeout, SIGINT, or SIGTERM, the test sends SIGTERM to the whole group, then SIGKILL if it remains after five seconds. When VS Code exits, closing its window, the servers that it started are in its group: if a `virgil-lsp` or `virgil-lsp-worker` process remains there five seconds later, the test fails and stops the group the same way. It also fails if the output channel's log records an unclean exit of the last server, but the channel usually closes before the server exits. It waits without a deadline for the group to exit before removing its temporary directory. On a launch failure, the test prints the "Virgil Language Server" output channel's log, which also holds the server's stderr. The temporary directory is removed afterward.

### Remote-SSH test

`npm run test:e2e:remote-ssh` runs the [Extension Host test](#extension-host-test)'s suite, unchanged, in a VS Code Remote-SSH window: official VS Code 1.142.0 and Microsoft's [Remote-SSH extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh) connect to an SSH host, the packaged extension is installed on that host, and the server runs there. It runs on Linux, where the host is the same machine. Besides the [setup prerequisites](#setup), it needs `ssh`, `ssh-keygen`, Xvfb, and `tar` on `PATH`, the OpenSSH server at `/usr/sbin/sshd` (or `--sshd PATH`), which it runs as the current user, with its privilege separation directory, such as `/run/sshd`, if that version of OpenSSH needs it (the test says so), and access to the Visual Studio Marketplace and VS Code's update service. For example, on a build and on a release's files:

```sh
npm run package
npm run test:e2e:remote-ssh -- --vsix virgil-lsp-vscode-0.1.1.vsix
npm run test:e2e:remote-ssh -- --vsix virgil-lsp-vscode-0.1.2.vsix \
  --archive virgil-lsp-0.1.0-alpha.3-x86-64-linux.tar.gz --repeat 3 --out results
```

`--vsix` is required. The server is `build/virgil-lsp` at the repository root unless `--server PATH` names another `virgil-lsp` or `--archive` a release archive, and the Virgil root is the checkout's `vendor/virgil` unless `--virgil-root DIR` names another. `--repeat N` runs the suite's two launches N times. `--out DIR`, a new or empty directory, receives the results; by default it is a new directory under the temporary directory. `--work-dir DIR` is where the work directory goes, by default the temporary directory; its path may contain only letters, digits, `.`, `_`, `/`, and `-`, and it needs about 700 MB, mostly for the VS Code Server. `--keep-work` keeps the work directory.

The test, in order:

1. Downloads VS Code as the Extension Host test does, and installs Remote-SSH 0.129.2026100915 into the work directory with VS Code's `--install-extension`, with the two extensions of its extension pack, each at a pinned version whose VSIX must have its pinned SHA-256.
2. Provisions the host: a private Xvfb display, and an SSH server of its own, which listens on a free port on 127.0.0.1 only and accepts only a key made for the run. The client's SSH configuration pins the host key that the run made. Remote-SSH's settings select that configuration, the work directory for the VS Code Server (`remote.SSH.serverInstallPath`), and a connection without the local server or the exec server, downloading the VS Code Server on the host.
3. Uploads over SSH the server, the Virgil root's `lib`, the [fixture](../../test/e2e/nvim/fixture/), `suite.cjs`, and [`remote-ssh/bootstrap.cjs`](test/e2e/remote-ssh/bootstrap.cjs). Everything after provisioning reaches the host only through SSH.
4. Connects once without the extension, so that Remote-SSH installs the VS Code Server for the client's commit. The VS Code Server's own CLI then installs the VSIX on the host, as `code` in a remote terminal does, and the test checks that it lists the VSIX's version, at the expected path, with `extensionKind` `["workspace"]`.
5. Starts VS Code twice for each repetition, without a folder and with the fixture as the remote folder, each time with new client user data. `virgil.server.path` and `virgil.virgilRoot` are in the host's machine settings. VS Code doesn't pass the client's environment to the host, so `bootstrap.cjs`, in the remote extension host, gives the suite the host's paths. It first checks that the window is a Remote-SSH window, that it runs in the VS Code Server's `node`, and that the extension is the installed copy, of the VSIX's version, with the Workspace extension kind.
6. After each window closes, checks for 5 seconds that no process on the host runs an executable from the work directory other than the VS Code Server's, which stays running. A `virgil-lsp` or `virgil-lsp-worker` still running then fails the test, which kills it. VS Code sometimes quits before it tells the window's remote extension host to terminate, and the VS Code Server would then keep that host and its language server for three hours: if the host still runs after those 5 seconds, the test reports it as `INFO`, stops it by its PID and start time, with SIGTERM and if need be SIGKILL, and fails only if the server or worker is still running 5 seconds later.

It exits with status 0 when every check passes, 1 when a check fails, 2 for bad arguments, 3 for an infrastructure failure (a missing prerequisite, a download, the SSH server, the connection, or the logs), and 130 or 143 when interrupted, even during the log collection or the teardown; its last line is `RESULT: PASS`, `FAIL`, `INFRA`, or `INTERRUPTED`. A launch counts only if `bootstrap.cjs` found the launch's workspace folders, none or the fixture, and ran the suite on the host, which passed and printed its `PASS` lines; one that never started the suite is an infrastructure failure, or a failure if VS Code exited successfully. The output directory holds `manifest.json`, with the versions and commits of VS Code, Remote-SSH, and the VS Code Server, the SHA-256 of the VSIX, the server's executables, and the archive, the client's and the host's OS, the installation checks, and each launch's result, times, processes left running, and remote extension host, which says whether the test stopped it; `summary.txt`; each launch's VS Code output; and the client's and the host's VS Code logs and the SSH server's and Xvfb's logs, collected before the teardown. The logs stay in the work directory until the VS Code Server's connection tokens are removed, and only those copies reach the output directory; logs that can't be collected or copied are an infrastructure failure. It never holds keys or Microsoft's software, and the work directory can't be inside it.

The test keeps its state out of the user's: VS Code's user data and extensions, the SSH keys and configurations, and the host's home and VS Code Server are in the work directory, and Unix sockets in a short directory under `/tmp`. It doesn't use or change `~/.ssh`, `~/.vscode`, `~/.vscode-server`, `~/.config/Code`, `/etc`, or the system's SSH server. The host is the same user account, so this separates state, not permissions. Every process that the test starts has a marker in its environment. On success, failure, a timeout, SIGINT, or SIGTERM, the test first collects the logs, then freezes and kills exactly the processes that have the marker, run an executable from the work directory, or descend from its servers, never processes found by name, and checks that the SSH server's port is closed. It tears down even when collecting the logs fails, and a signal during the collection or the teardown waits for them. It knows each process by its PID and start time, which it checks again before each signal, so it never signals a PID that has since passed to another process. If it can't confirm that nothing is left, it keeps the work directory and reports an infrastructure failure. Each start of VS Code has a 180-second timeout, 300 seconds for the first, which downloads the VS Code Server.

The test doesn't cover checklist step 17, forced server crashes and what happens during the recovery; what VS Code shows, as for the Extension Host test; a macOS client, which the [manual test policy](../../docs/manual-testing.md#vs-code) checks separately; Remote-SSH's default connection modes, with the local server and the exec server; or a host that is another machine, user, or architecture.

## Manual checklist

Run it on each platform before a release, with the extension [installed from the release's VSIX](#installing-from-a-vsix), and after changes to the extension or to server startup and shutdown. Use a copy of the [Neovim smoke-test fixture](../../test/e2e/nvim/fixture/) (`cp -R test/e2e/nvim/fixture /tmp/virgil-check`), so that edits don't touch the checkout. Its `bad.v3` contains a syntax error on line 3. Its project file defines a project `words`, which uses Virgil's `lib/util`. Set `virgil.virgilRoot` to the absolute path of the checkout's `vendor/virgil` before you start.

The [Extension Host test](#extension-host-test) covers the behavioral checks marked *(Automated)*, and the [Remote-SSH test](#remote-ssh-test) covers them through Remote-SSH from a Linux client; see [Development](#development) for CI platforms and source or installed-VSIX coverage. Rendered views, popups, notifications, and prompts still need visual review on every platform. Run the behavioral checks by hand for setups without automated coverage.

For each VS Code setup in the [manual test matrix](../../docs/manual-testing.md#matrix), check the [known limitations](../../docs/known-limitations.md) and follow these steps:

1. Open the fixture folder with the extension installed or loaded from source (see [Setup](#setup)) and open `bad.v3`. The status bar shows "Virgil" as the language. *(Automated.)*
2. The "Virgil Language Server" output channel shows `Virgil root: <root> (from virgil.virgilRoot)`, `Starting <path> --stdio`, and `Started virgil-lsp <version>`. With a remote, both paths are on the remote machine. *(Automated.)*
3. The Problems view shows one error, "invalid start of expression" from `aeneas`, at line 3, column 27. *(Automated.)*
4. Without saving, replace the file's contents with `component C { def value = 1; }` and, on a second line, `type V { case A(x: int); }`. The error disappears. *(Automated.)*
5. The Outline view shows the component `C` and the variant `V`, with its case `A` inside it. *(Automated.)*
6. Undo the edit. The error comes back without saving. *(Automated.)*
7. Open `words/Words.v3`. The Problems view shows one error for it, "expected int in var initialization, got Array<byte>" from `aeneas`, on line 7. Without the library, it would also report that `StringBuilder` cannot be found. **Go to Definition** on `StringBuilder` opens the library's `lib/util/StringBuilder.v3` at `class StringBuilder`, hovering over it shows `class StringBuilder`, **Trigger Parameter Hints** (Ctrl+Shift+Space) just after the `(` of `.puts(a)` on the same line shows `def puts(s: Array<byte>) -> this` with `s: Array<byte>` highlighted, **Find All References** on it lists this use, the class's name, and its uses in other `lib/util` files, and **Go to Symbol in Workspace** (Ctrl+T) for `join` lists the method `join` in `words/Words.v3`. *(Automated.)*
8. In `words/Words.v3`, run **Rename Symbol** (F2) on `line` in line 7. A notification says "the project's analysis has errors; rename needs a program that verifies". Without saving, change line 7 to `var line = StringBuilder.new().puts(a).putc(' ').puts(b).toString();` and line 8 to `return line.length;`; the error disappears. **Rename Symbol** on `line` now selects it; enter `text`. Both uses become `text`, unsaved. Undo once: both are `line` again. Undo once more: the type error comes back. *(Automated, except the notification and the prompt.)*
9. In `words/Words.v3`, without saving, add a line after line 7, indented like it, and type `w` on it. The suggestion list shows `while`, marked as a keyword. Keep typing: after `whi`, the list shows `while` and not the other keywords, such as `return`. Accept `while` (Enter or Tab): the line reads `while`. Undo once: it reads `whi` again. Undo until the line is gone and the file is unmodified. Add the line again and type `li`: after a moment, the list shows `line` first, marked as a variable, with `var line: int` beside it. Accept it: the line reads `line`. Undo until the line is gone and the file is unmodified. Add the line again and type `a.`: as you type the `.`, the list shows only `length`, marked as a field, with `def length: int` beside it, since `a` is a string. Accept it: the line reads `a.length`. Undo until the line is gone and the file is unmodified. Inside the comment on line 3, Ctrl+Space and typing `.` show no suggestions from the server. *(Automated, except the suggestion list.)*
10. Open `.virgil-lsp.json` and, without saving, add `"bogus": 1,` after the opening brace. The Problems view shows an `UnknownField` error from `virgil-lsp` on that line. Undo the edit; the error disappears. *(Automated.)*
11. Close `.virgil-lsp.json`. From a terminal, add `"bogus": 1,` after its opening brace on disk. Without editing a `.v3` file, the Problems view shows the `UnknownField` error. Remove it on disk; the error disappears, and `words/Words.v3` reports its error again. *(Automated.)*
12. Clear `virgil.virgilRoot`, with `VIRGIL_LOC` unset. The server restarts, the output channel says there is no Virgil root, and `.virgil-lsp.json` gets a `MissingVirgilRoot` error. Restore the setting. *(Automated.)*
13. Run **Virgil: Restart Language Server**. The output channel shows `Server process exited successfully`, which means the server exited with status 0 after `shutdown` and `exit`, and then the start messages again. *(Automated.)* Closing the window stops the server the same way. *(Automated, except the exit status, which the output channel usually closes too early to show.)*
14. Set `virgil.server.path` to a nonexistent absolute path. The output channel reports that the file is missing or not executable, and an error notification appears. Restore the setting. *(Automated, except the notification.)*
15. With a copy of `virgil-lsp` in a directory without `virgil-lsp-worker`, point `virgil.server.path` at the copy. The output channel reports the missing worker. Restore the setting. *(Automated.)*
16. Set the channel's log level to Trace. The channel shows the LSP messages, including `textDocument/didChange` after an edit. *(Automated.)*
17. Close any buffers that contain a known parser crash input, then terminate the server process to trigger automatic recovery. During recovery, separately try **Virgil: Restart Language Server**, changing `virgil.server.path`, and closing the window. Each action waits for recovery to finish; restart and configuration changes stop the recovered server before starting its replacement, and closing the window sends `shutdown` then `exit` to the recovered server. Confirm that only one server remains after a restart and none remains after closing the window.

Record the VS Code version, the platform, and the server version in the pull request, and for a release in the [manual test results](../../docs/manual-testing.md#results).
