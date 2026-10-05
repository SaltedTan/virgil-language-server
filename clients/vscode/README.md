# VS Code extension

> **Development client.** It is not packaged or published yet; run it from this directory as described below. See the [project status](../../README.md) for current server features and [compatibility](../../docs/compatibility.md#editors) for the supported VS Code and server versions.

A thin TypeScript extension built on `vscode-languageclient/node`. It:

- contributes the `virgil` language for `.v3` files, with comment and bracket configuration and a small TextMate grammar;
- starts `virgil-lsp --stdio` for `virgil` documents (saved files and untitled buffers);
- reports startup problems, the server's stderr, and the optional message trace in the "Virgil Language Server" output channel;
- sends `shutdown` and then `exit` when it is deactivated, for example when the window closes;
- runs on the workspace side (`extensionKind: ["workspace"]`), so that with Remote-SSH and Remote-WSL the server starts on the remote machine, next to the files ([ADR-0003](../../docs/decisions/0003-supported-platforms.md)).

## Setup

Requirements: a [supported VS Code version](../../docs/compatibility.md#editors), Node.js 20 or later with `npm`, and a built server.

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

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `virgil.server.path` | `virgil-lsp` | A command name looked up on the extension host's `PATH`, or an absolute path. Relative paths are rejected. This is a machine setting: set it in user settings, or in the remote settings with Remote-SSH and Remote-WSL. Workspace settings can't change it. Changing it restarts the server. |
| `virgil.trace.server` | `messages` | How much of each LSP message the trace records: `messages`, `compact`, or `verbose`. |

To record the trace, set the output channel's log level to Trace: run **Developer: Set Log Level...**, choose "Virgil Language Server", then Trace. The trace stops when you set the level back to Info.

The `virgil.project.config` setting described in the [ROADMAP](../../ROADMAP.md#visual-studio-code) is planned. See [Configuration](../../docs/configuration.md) for the server's current project discovery behavior.

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
npm run watch     # recompile on change
```

CI installs the dependencies with `npm ci`, then runs `npm run check` and `npm test` on Linux. Extension Host end-to-end tests and VSIX packaging are planned (ROADMAP backlog item 18). Until then, use the manual checklist below.

## Manual checklist

Run it on each platform before a release, and after changes to the extension or to server startup and shutdown. Use a copy of the [Neovim smoke-test fixture](../../test/e2e/nvim/fixture/) (`cp -R test/e2e/nvim/fixture /tmp/virgil-check`), so that edits don't touch the checkout. Its `bad.v3` contains a syntax error on line 3.

For each of **Linux**, **Remote-SSH** (from any desktop into Linux x86-64), **Remote-WSL** (from Windows into WSL 2), and **macOS** (Apple Silicon, with Rosetta 2):

1. Open the fixture folder with the extension loaded (see [Setup](#setup)) and open `bad.v3`. The status bar shows "Virgil" as the language.
2. The "Virgil Language Server" output channel shows `Starting <path> --stdio` and `Started virgil-lsp <version>`. With a remote, `<path>` is on the remote machine.
3. The Problems view shows one error, "invalid start of expression" from `aeneas`, at line 3, column 27.
4. Without saving, replace the file's contents with `component C { def value = 1; }`. The error disappears.
5. The Outline view shows the component `C`.
6. Undo the edit. The error comes back without saving.
7. Run **Virgil: Restart Language Server**. The output channel shows `Server process exited successfully`, which means the server exited with status 0 after `shutdown` and `exit`, and then the start messages again. Closing the window stops the server the same way.
8. Set `virgil.server.path` to a nonexistent absolute path. The output channel reports that the file is missing or not executable, and an error notification appears. Restore the setting.
9. With a copy of `virgil-lsp` in a directory without `virgil-lsp-worker`, point `virgil.server.path` at the copy. The output channel reports the missing worker. Restore the setting.
10. Set the channel's log level to Trace. The channel shows the LSP messages, including `textDocument/didChange` after an edit.
11. Close any buffers that contain a known parser crash input, then terminate the server process to trigger automatic recovery. During recovery, separately try **Virgil: Restart Language Server**, changing `virgil.server.path`, and closing the window. Each action waits for recovery to finish; restart and configuration changes stop the recovered server before starting its replacement, and closing the window sends `shutdown` then `exit` to the recovered server. Confirm that only one server remains after a restart and none remains after closing the window.

Record the VS Code version, the platform, and the server version in the pull request.
