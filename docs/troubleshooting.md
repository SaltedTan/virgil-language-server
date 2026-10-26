# Troubleshooting

This guide is organized by symptom. Each entry says how to confirm the cause, usually from a message in the editor's log, and how to fix it. [Logs and restarting](#logs-and-restarting) says where each editor keeps those messages. Check the [known limitations](known-limitations.md) too: some behavior that looks like a bug is a limitation of the alpha.

Messages are quoted as the extension and server print them. A part in angle brackets, such as `<path>`, stands for a value that varies. The server writes its log to its standard error, and each line that it writes starts with `virgil-lsp:` or, for a warning, `virgil-lsp: warning:`. VS Code shows those lines in the "Virgil Language Server" output channel, with its own; Neovim keeps them in its LSP log.

## The server doesn't start

### VS Code: reading the output channel

Open the Output view and choose "Virgil Language Server". When the extension first activates or you request a restart, it writes a `Server selection:` line that names the server it chose and why, then `Starting <path> --stdio`, and, once the server has answered, `Started virgil-lsp <version>`. If a check fails before the server starts, or the server doesn't start, the channel says `The server did not start: <reason>`, and VS Code shows an error notification, "Virgil language server did not start: <reason>", with a **Show Output** button. The reason says what to fix. After fixing it, run **Virgil: Restart Language Server**. A change to `virgil.server.path` or `virgil.virgilRoot` restarts the server by itself.

The extension chooses the server in this order ([Settings](../clients/vscode/README.md#settings)):

1. A nonempty `virgil.server.path`. The channel says `Server selection: <value> (explicit virgil.server.path)`.
2. Otherwise, the server bundled in a platform VSIX: `Server selection: <path> (bundled for <target>; no explicit virgil.server.path)`.
3. Otherwise, in the universal VSIX, which has no bundle, `virgil-lsp` on the extension host's `PATH`: `Server selection: virgil-lsp on PATH (no bundle and no explicit virgil.server.path)`.

If the channel doesn't exist, the extension hasn't started. It starts when a `.v3` file opens or when the workspace contains a `.virgil-lsp.json`, and only in a [trusted workspace](https://code.visualstudio.com/docs/editor/workspace-trust), because the server analyzes the workspace's files. With Remote-SSH or Remote-WSL, the extension must be installed on the remote side: the Extensions view lists it under the remote, such as "SSH: host" or "WSL: Ubuntu", not under "Local" ([Installing from a VSIX](../clients/vscode/README.md#installing-from-a-vsix)).

### An explicit `virgil.server.path` that doesn't work

`virgil.server.path` overrides the bundle, so a stale value keeps the bundled server from being used. It must be an absolute path or a command name, and these messages say which part is wrong:

- `<path> (from virgil.server.path) is missing or not an executable file.` The absolute path names no file, or the file isn't executable. Correct the path, or make the file executable.
- `virgil.server.path must be a command name or an absolute path, not the relative path <value>.` Relative paths are rejected. Give the absolute path.
- `<name> was not found on the extension host's PATH (with Remote-SSH or Remote-WSL, the remote machine's). Install it there, or set virgil.server.path to its absolute path.` A command name is looked up on the `PATH` of the extension host, which may differ from a terminal's. With Remote-SSH or Remote-WSL, it is the remote machine's `PATH`.

To go back to the bundled server, clear the setting. It is a machine setting, so set or clear it in user settings, or in the remote settings with Remote-SSH and Remote-WSL; workspace settings can't change it.

### A bundled server for another platform

`This VSIX bundles a server for <target>, but the workspace extension host is <host>. Install the VSIX for the workspace host (including Remote-SSH or WSL), or set virgil.server.path.` The installed platform VSIX doesn't match the machine where the extension runs. VS Code may refuse to install such a VSIX in the first place. With Remote-SSH or Remote-WSL, that is the remote machine or the WSL distribution, not the computer showing the editor. Install the VSIX whose target matches that machine, `linux-x64`, `darwin-arm64`, or `darwin-x64`, in a window connected to it ([Installing from a VSIX](../clients/vscode/README.md#installing-from-a-vsix)). `Invalid bundled server metadata: <reason>. Reinstall the VSIX.` means that the VSIX's record of its target is damaged; reinstall the VSIX.

### No bundle, and nothing on `PATH`

With the universal VSIX and an empty `virgil.server.path`, the extension looks for `virgil-lsp` on the extension host's `PATH`, and reports `virgil-lsp was not found on the extension host's PATH …` if it isn't there. Either install the platform VSIX for the workspace host, which includes the server, or install the server from a release archive ([Installing](releasing.md#installing)) and put it on that `PATH` or name it in `virgil.server.path`. The extension host's `PATH` can differ from a terminal's; an absolute `virgil.server.path` doesn't depend on it.

### Native Windows

`native Windows is not supported. Open the folder in WSL 2 with the WSL extension, and install virgil-lsp inside the WSL distribution.` The extension refuses to start on native Windows. Open the folder in WSL 2, which is supported on a best-effort basis, and install the `linux-x64` VSIX in the WSL window ([host platforms](compatibility.md#host-platforms)).

### Apple Silicon without Rosetta 2

There is no Apple Silicon build of the server yet: both macOS packages carry the Intel (`x86-64-darwin`) build, and an Apple Silicon Mac runs it under Rosetta 2. Without Rosetta 2, macOS can't run the server. In VS Code, a bundled server for `darwin-arm64` adds `The bundled x86-64 server requires Rosetta 2 on Apple Silicon.` to the channel after its `Server selection:` line, and the server then fails to start. In a terminal, `virgil-lsp --version` fails too. Install Rosetta 2, then restart the server:

```sh
softwareupdate --install-rosetta --agree-to-license
```

See [macOS](releasing.md#macos) in the installation instructions.

### macOS quarantine on an archive install

The server's executables are unsigned and not notarized. A web browser marks what it downloads as quarantined, and macOS may then refuse to run `virgil-lsp` or `virgil-lsp-worker` from an archive that was downloaded that way. If `virgil-lsp --version` is refused in a terminal, remove the attribute from the extracted directory, replacing the version with yours:

```sh
xattr -dr com.apple.quarantine ~/.local/opt/virgil-lsp-0.1.0-alpha.4-x86-64-darwin
```

Downloading with `curl` or `gh` instead doesn't set the attribute. Verify the download against `SHA256SUMS` first ([Verifying a download](releasing.md#verifying-a-download), [macOS](releasing.md#macos)).

### A worker missing beside the server

The server runs whole-program analysis in a separate process, `virgil-lsp-worker`, which it starts from the directory of its own resolved path, following symbolic links. A copy of `virgil-lsp` in a directory without the worker can't analyze projects.

- **VS Code** checks for the worker before it starts the server: `<directory>/virgil-lsp-worker is missing or not executable (<path> resolves to <resolved path>). Keep virgil-lsp-worker in the same directory as virgil-lsp.` The part in parentheses appears when the server path is a symbolic link. A wrapper script needs the worker beside the script itself.
- **Neovim** starts the server anyway. It parses open files and reports their syntax errors, but each analysis of a project fails, and the log shows `virgil-lsp: warning: cannot start the analysis worker: cannot execute <path> (errno <n>)`, followed by `virgil-lsp: warning: automatic analysis of project <name> failed: …`. The editor also shows a warning, "Semantic diagnostics for project <name> could not be updated: …", for the first failure.

Move or copy `virgil-lsp-worker`, and the `virgil/` library directory, back beside the real `virgil-lsp`, or point a symbolic link at the original instead of copying the server ([Installing](releasing.md#installing)). A worker from another build or version is refused too, and the log says `analysis worker <pid> is from a different build; it ended with <status>`. Keep the executables of one archive together, and replace them together when upgrading.

### Neovim doesn't find `virgil-lsp`

The shipped configurations start `virgil-lsp --stdio` from the `PATH` that Neovim sees ([Neovim setup](../editors/nvim/README.md)). If it isn't there, no client attaches and Neovim shows no error when a `.v3` file opens. `:checkhealth vim.lsp` then lists the `virgil_lsp` configuration with the warning "'virgil-lsp' is not executable. Configuration will not be used." on Neovim 0.11.6. Check that `virgil-lsp --version` works in the terminal that starts Neovim. A directory that the install added to `PATH`, such as `~/.local/bin`, must also be in the shell's startup file for new terminals ([Editor setup](../README.md#editor-setup)). Alternatively, edit `cmd` in the configuration to give the server's absolute path.

LazyVim's configuration sets `mason = false`, because the server isn't in mason.nvim: install it from a release archive yourself.

## No diagnostics or navigation in a file

Syntax errors and the outline work in any open `.v3` file. Type errors, definition, type definition, hover, references, document highlights, signature help, rename, workspace symbols, and the names in completion need a whole-program analysis, and the causes below stop it.

### The file is in no project

Without a `.virgil-lsp.json` that selects it, a file stays in [single-file mode](configuration.md#without-a-project-file): syntax errors, the outline, folding and selection ranges, and keyword completion only. The first time that happens in a session, the server logs `virgil-lsp: warning: Without a project file selecting this document, semantic features are limited; using single-file mode.` A project must select the file through its `sources` or `dependencies`, and the server looks first in the nearest `.virgil-lsp.json` in the file's directory or above it. Add a project file, or add the file to a project's `sources` ([Configuration](configuration.md)).

### Too much open text

The server keeps the text of open documents in a shared budget of 1 MiB, which also counts some metadata for each document. An open or an edit that would exceed it is rejected: the server keeps the document as it was, or doesn't open it, and logs this warning; the editor also shows the message after the `virgil-lsp: warning:` prefix:

```text
virgil-lsp: warning: ignored textDocument/didOpen: aggregate open-document capacity exceeded; close documents or reduce text and retry
```

For an edit, the message names `textDocument/didChange` instead. Close other documents, then reopen the rejected document, or repeat the edit ([Supported program size](known-limitations.md#supported-program-size), [Document store](architecture.md#document-store)).

### The project file has errors

A project file with any error defines no projects, so every file it governs stays in single-file mode. Its errors appear on `.virgil-lsp.json` itself, with the source `virgil-lsp` and a code such as `UnknownField` or `InvalidPattern`, even while it isn't open. The server also shows the warning "Invalid project file <path>; its semantic project contexts are disabled." each time a project file becomes invalid. LazyVim's noice.nvim drops that warning, but not the diagnostics ([LazyVim](../editors/nvim/README.md#lazyvim)). Open the project file and fix each diagnostic; [Configuration diagnostics](configuration.md#configuration-diagnostics) explains each code.

### The Virgil root and the bundled library

A project's `virgilDependencies`, such as `lib/util/*.v3`, are relative to a Virgil root. Without a root, the project file gets `MissingVirgilRoot`, "virgilDependencies requires a Virgil root (initializationOptions.virgilRoot)", and its files stay in single-file mode. The server chooses the root once, when it starts, and logs it ([The Virgil root](configuration.md#the-virgil-root)):

- `virgil-lsp: Virgil root: <path> (client)`: the editor sent a root, from `virgil.virgilRoot` or `VIRGIL_LOC`.
- `virgil-lsp: Virgil root: <path> (bundle, revision <revision>)`: the editor sent none, and the server uses the library in `virgil/` beside its resolved executable, which release archives and platform VSIX files include.
- `virgil-lsp: Virgil root: none (invalid client root; bundle fallback disabled)`: the editor sent a root that isn't an absolute path, such as an empty or relative one. The server doesn't fall back to its bundle then, so that the mistake isn't hidden. Fix the setting or `VIRGIL_LOC`, or clear it.
- `virgil-lsp: Virgil root: none (<reason>)`: the editor sent none, and there is no usable bundle. The reason is that `bundle REVISION is missing or unreadable`, as for a development build, which has no bundle; that `bundle REVISION does not match the server`, when `virgil/` comes from another release; that `bundle lib directory is missing or unreadable`; or that the server `cannot resolve the executable path`. Keep the archive's `virgil/` beside the executables, or set a root.

VS Code writes the root that it sends before the server's line: `Virgil root: <path> (from virgil.virgilRoot)`, `Virgil root: <path> (from VIRGIL_LOC)`, or `No Virgil root: neither virgil.virgilRoot nor VIRGIL_LOC is set`, which leaves the choice to the server. A relative `virgil.virgilRoot` gives the warning "virgil.virgilRoot must be an absolute path, not <value>. Projects with virgilDependencies stay disabled until it is fixed.", and a relative `VIRGIL_LOC` gives `Invalid Virgil root: VIRGIL_LOC is not an absolute path: <value>`. In Neovim, `:lua =vim.lsp.get_clients({ name = "virgil_lsp" })[1].config.init_options` shows what was sent ([Neovim's Virgil root](../editors/nvim/README.md#the-virgil-root)).

The bundle holds only Virgil's `lib/`. A project that selects `rt/`, `aeneas/`, or other files outside it needs a Virgil checkout at the revision that `virgil-lsp --version` prints, set as the root ([The Virgil root](releasing.md#the-virgil-root)). A root whose directories can't be listed gives `SourceIO` on the project file. The root stays fixed until the server restarts: in VS Code, a change to `virgil.virgilRoot` restarts the server, but a change to `VIRGIL_LOC` needs a new window; in Neovim, restart the server after changing `VIRGIL_LOC`.

### A syntax error in the project

Whole-program analysis needs every file of the project to parse. While a file has a syntax error, the analysis can't type-check the project, so its type errors disappear and definition, type definition, hover, references, and document highlights give nothing, even in other files. Rename is refused while the project has any error, a syntax error or a type error. Fix the syntax error that the Problems view or the diagnostics list shows, which may be in another file than the one you are looking at.

Two exceptions keep most answers while you edit a method. Both leave definition, type definition, hover, references, document highlights, and type errors working outside the method or constructor body that has the error; the damaged body itself gets nothing, references and highlight lists can be partial, and rename stays refused:

- The [method-body fallback](architecture.md#method-body-fallback): after a file has been analyzed without a syntax error, a later syntax error strictly inside one body is analyzed from the file's last parseable text for that body.
- The [current-text repair](architecture.md#current-text-repair): when the fallback can't help, as when you open a file that already has the error, or the server has no parseable text for it yet, one or two small insertions where the parser reports the error, such as a missing name, a closing `)`, `]`, or `>`, or a `;`, inside one body can make the open file parse.

Neither applies to an unclosed brace, an edit to a declaration's header or a field initializer, errors in two bodies, an unterminated string or comment, or a closed file ([No error-tolerant parsing](known-limitations.md#no-error-tolerant-parsing)).

Completion and signature help repair the code at the cursor in their own way ([Completion](architecture.md#completion)).

### Waiting for an analysis

An analysis starts 200 ms after the last edit, or at once when a file is opened, saved, or closed, and checks the whole program again. After an edit, the file's type errors disappear until that analysis finishes; syntax errors follow the edit at once. A definition, hover, or similar request made meanwhile waits for the analysis and answers from it, but gets nothing if the document changed while it waited: ask again. For the Aeneas sources, diagnostics arrived about 220 ms after a save on the measured machine ([Stale results](known-limitations.md#stale-results), [Supported program size](known-limitations.md#supported-program-size)).

An analysis stops at 15 errors, and an Information diagnostic says "Aeneas stops checking after 15 errors, so errors elsewhere in this program may be missing." Fix the reported errors first. A file with Windows line endings (CRLF) gets one error per line, because Aeneas rejects carriage returns, as `v3c` does: convert it to LF line endings.

### The analysis worker failed or was replaced

Each analysis runs in the worker process. When an analysis fails, the published diagnostics stay as they were, and the log has a warning that names the project and the reason:

```text
virgil-lsp: warning: automatic analysis of project <name> failed: <reason>; published diagnostics are kept
```

The first failure also shows a warning in the editor, "Semantic diagnostics for project <name> could not be updated: <reason>. The last published diagnostics are kept; see the server log." Later ones are only logged until an analysis succeeds. Another warning before it says what happened to the worker, for example `analysis worker <pid> crashed during an analysis; it ended with <status>`, or `analysis worker <pid> exceeded the analysis time limit of 10000 ms and was killed; it ended with <status>`. The worker's own error output, such as a crash's message, goes to the same log.

The server doesn't retry by itself: the next open, edit, save, close, or change on disk starts a new analysis, in a new worker. Make an edit or save the file to try again. If the same code fails every time, it may be an input that crashes or hangs the compiler's front end: [Aeneas crash paths](aeneas-crash-paths.md) lists the known ones, and a new one is worth [reporting](#reporting-a-bug) with the smallest input that shows it. A failure that says `the program is too large to analyze` means that the analysis result is over its budget; split the program into smaller projects ([Supported program size](known-limitations.md#supported-program-size)).

The worker has a fixed heap of 384 MB. A program that needs more makes it run out of memory: the worker writes `!HeapOverflow: insufficient space after GC` and a stack trace to the log, and exits with status 255, so the server reports `analysis worker <pid> crashed during an analysis; it ended with exit status 255`, and the analysis fails as a crash. Analyzing the same program again usually fails the same way. Reduce the program's source set, for example by splitting it into smaller projects ([Supported program size](known-limitations.md#supported-program-size)).

The server also replaces a healthy worker after 100 analyses, or sooner when its live heap or its counter of compiler IDs reaches a limit, and logs that as information, such as `virgil-lsp: replacing analysis worker <pid> after 100 analyses (limit 100)`. That is routine and loses nothing ([restart policy](decisions/0004-analysis-worker-process.md#restart-policy)).

## Changes on disk aren't noticed

The server learns about changes made outside the editor, such as a `git checkout` or a project file edited in another program, only from the editor's file events ([Changes on disk](configuration.md#changes-on-disk)).

- **VS Code** watches `.virgil-lsp.json` and `.v3` files in the workspace and sends the events, so a project file fixed on disk, or a source created or deleted, is noticed without an edit.
- **Neovim on macOS** declares support for the server's file watchers, and the server registers them, so it notices the same changes.
- **Neovim on Linux**, including in WSL 2, doesn't declare that support in versions 0.11 and 0.12. With the shipped configurations, the server then refreshes a project when it is used: a project file fixed on disk is noticed when a `.v3` buffer is next opened, changed, or saved, and a source created or deleted outside Neovim only when a project file changes. `:lua =vim.lsp.protocol.make_client_capabilities().workspace.didChangeWatchedFiles` shows what your Neovim declares ([Neovim's changes on disk](../editors/nvim/README.md#changes-on-disk)).

In Neovim on Linux, restart the server after a checkout that adds or removes files, and to find project files added elsewhere in the workspace that no open file uses. A change to a file that is open in the editor is ignored, because the editor's buffer is what the server analyzes.

## Logs and restarting

### VS Code

The "Virgil Language Server" output channel holds the extension's start and stop messages and the server's standard error, which includes the worker's. To add the LSP messages that the editor and the server exchange, run **Developer: Set Log Level...**, choose "Virgil Language Server", then Trace. The `virgil.trace.server` setting chooses how much of each message the trace records: `messages`, the default, records each message's name and, for a response, how long it took; `compact` and `verbose` also record its contents. Setting the level back to Info stops the trace ([Settings](../clients/vscode/README.md#settings)).

Run **Virgil: Restart Language Server** from the Command Palette to restart the server. The channel shows `Server process exited successfully` and then the start messages again. If the server exits on its own, the extension restarts it; after 5 crashes within 3 minutes, it stops trying and logs "The Virgil Language Server server crashed 5 times in the last 3 minutes. The server will not be restarted. See the output for more information." Fix the cause, then run the restart command.

### Neovim and LazyVim

- `:checkhealth vim.lsp` shows whether the `virgil_lsp` client attached, its configuration, and the path of the LSP log.
- The server's standard error goes to Neovim's LSP log, as `ERROR` lines marked `"stderr"`. `:LspLog` opens the log with nvim-lspconfig on Neovim 0.11, which LazyVim includes; otherwise, `:lua =vim.lsp.log.get_filename()` prints its path.
- To add the LSP messages to the log, run `:lua vim.lsp.log.set_level("debug")` before reproducing the problem.
- `:LspInfo` (with nvim-lspconfig on Neovim 0.11) or `:lua =vim.lsp.get_clients()` lists the running clients.

To restart the server, run `:LspRestart` with nvim-lspconfig, as in LazyVim, or `:lsp restart` on Neovim 0.12 and later, where nvim-lspconfig no longer defines `:LspRestart`. In plain Neovim 0.11, run `:lua vim.lsp.stop_client(vim.lsp.get_clients({ name = "virgil_lsp" }))`, then `:edit`, or restart Neovim. If the server exits with a nonzero status, Neovim warns "Client virgil_lsp quit with exit code …" ([Collecting logs](manual-testing.md#collecting-logs)).

## Reporting a bug

Check the [known limitations](known-limitations.md) and the [existing issues](https://github.com/SaltedTan/virgil-language-server/issues) first, then use the [bug report form](https://github.com/SaltedTan/virgil-language-server/issues/new?template=bug_report.yml). It asks for:

- **The server version.** `virgil-lsp --version` prints the version, the source revision, and the pinned Virgil revision. For a server bundled in a VSIX, run that command with the path that the output channel's `Server selection:` line names; the channel's `Started virgil-lsp <version>` line gives the version too. For a release archive, include its `manifest.json`; for a build from source, the commit.
- **The editor and extension versions.** `code --version` and the extension's version from the Extensions view, or `nvim --version`, and LazyVim's version if you use it.
- **The platform where the server runs.** With Remote-SSH, Remote-WSL, or Neovim over SSH, that is the remote machine or the WSL distribution, not the computer showing the editor. Include the operating system's version.
- **A minimal project.** The smallest `.v3` files that show the problem, the `.virgil-lsp.json` that governs them (the nearest one above them), and the steps you took in the editor.
- **The log.** The output channel's contents, or the lines from Neovim's LSP log, as described in [Logs and restarting](#logs-and-restarting). Logs name your files and directories. A trace with message contents, from `virgil.trace.server` set to `compact` or `verbose`, or from Neovim's debug log level, also includes the text of open files: remove anything private before pasting ([Privacy](../PRIVACY.md#logs)).

Report security problems privately, as [SECURITY.md](../SECURITY.md) describes, not in a public issue.
