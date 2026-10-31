# Privacy

This statement covers the language server (`virgil-lsp` and its analysis worker, `virgil-lsp-worker`), the VS Code extension, the Neovim and LazyVim configurations in [`editors/nvim/`](editors/nvim/), the Emacs configuration in [`editors/emacs/`](editors/emacs/), and the [Helix configuration](editors/helix/README.md). It describes what they read, write, and start on the machine where they run. With Remote-SSH, Remote-WSL, or an editor over SSH, that is the remote machine or the WSL distribution.

**Neither the server nor the extension collects telemetry or makes network connections.** Your code and the server's logs stay on your machine, except where you copy them yourself, for example into a bug report.

## Network and telemetry

The server has no network code. It communicates with the editor only through its standard input and output, and with its worker only through pipes. It doesn't use the network to find a Virgil root or anything else ([The Virgil root](docs/configuration.md#the-virgil-root)).

The VS Code extension starts the server with the standard input and output transport and makes no network connections of its own. It doesn't collect or send telemetry, and the server sends the editor no telemetry events.

The editors, and other extensions and plugins, are separate software with their own telemetry and network use, which their own settings and policies govern. That includes VS Code's telemetry, Microsoft's Remote-SSH and WSL extensions, and plugin managers such as LazyVim's lazy.nvim. The installation commands in this repository's READMEs download releases from GitHub when you run them. The repository's tests download VS Code and other test tools, but they aren't part of a release archive or VSIX.

## What the server reads

The server opens files only to read them:

- **Open documents.** The editor sends the text of open documents to which it attaches the server, including unsaved changes. The server keeps that text in memory only. See each integration's setup guide for its buffer selection.
- **Project files.** To find each document's project, it reads the nearest `.virgil-lsp.json` in the document's directory or above it. On first use, it searches the editor's workspace folders for project files, listing their directories, except hidden directories ([Workspace folders and active projects](docs/configuration.md#workspace-folders-and-active-projects)).
- **Sources.** It reads the `.v3` files that a project file's `sources`, `dependencies`, and `virgilDependencies` select, which can't be outside the project file's directory or the Virgil root ([Security](docs/configuration.md#security)).
- **The Virgil root.** It reads the files under the Virgil root that `virgilDependencies` select. When the editor sends no root, it uses the library in the `virgil/` directory beside its own executable, which release archives and platform VSIX files include: it reads that directory's `REVISION` file and checks that its `lib/` can be listed ([The Virgil root](docs/configuration.md#the-virgil-root)).
- **Its own location.** It looks up the path of its own executable, to find the worker and the `virgil/` library beside it.

It never follows symbolic links when it looks for project files or reads sources ([Symbolic links](docs/configuration.md#symbolic-links)). It reads no environment variables, including `VIRGIL_LOC`.

The server only parses and type-checks your code. It never compiles or runs it, and nothing in a project file can make it run a program ([Security](docs/configuration.md#security)). Its [development commands](docs/development.md), such as `virgil-lsp parse`, read the files that you name on the command line, and an unadvertised development request can name files to analyze; editors don't send it.

## What the server writes

The server writes no files, and it keeps no cache on disk: everything it learns is held in memory and gone when it exits. It writes only to:

- its standard output, which carries its messages to the editor;
- its standard error, its log, which the editor collects (see [Logs](#logs));
- the pipes to its worker.

## Which processes start

- The **VS Code extension** starts the server it selects, `virgil-lsp --stdio`: the server in the platform VSIX, the one that `virgil.server.path` names, or `virgil-lsp` on `PATH` ([Settings](clients/vscode/README.md#settings)). It stops the server when the window closes, and starts it again on a restart or a crash.
- The **Neovim and LazyVim configurations** start `virgil-lsp --stdio` from `PATH`, or the command that you give in `cmd`.
- The **Helix configuration** starts `virgil-lsp --stdio` from `PATH`, or the command that you give in `command`.
- The **Emacs configuration** starts nothing when it is loaded. Eglot starts `virgil-lsp --stdio` from `PATH` when you run `M-x eglot`, or through `eglot-ensure` if you add it to `virgil-mode-hook`.
- The **server** starts its analysis worker, `virgil-lsp-worker`, from the directory of its own executable, when it first needs to analyze a project. It gives the worker an empty environment, and `/dev/null` as its standard input. It runs one worker at a time and replaces it after a crash, after a timeout, and routinely after 100 analyses ([restart policy](docs/decisions/0004-analysis-worker-process.md#restart-policy)). The server sends the worker the text of the files to analyze through a pipe, and the worker opens no files itself.

The worker starts no processes. Neither the server nor the worker runs the Virgil compiler, `v3c`, or your program.

## What the editor integrations read and write

The VS Code extension reads:

- its settings, `virgil.server.path`, `virgil.virgilRoot`, and `virgil.trace.server`;
- the `PATH` and `VIRGIL_LOC` environment variables of the extension host;
- the record of the bundled server's platform inside its own installation directory;
- whether the selected server, and the worker beside it, are executable files.

It asks VS Code to watch the workspace's `.virgil-lsp.json` and `.v3` files, and passes the server the changes that VS Code reports, so that the server notices changes made outside the editor ([Changes on disk](docs/configuration.md#changes-on-disk)). It sends the server the text of open Virgil files and project files.

The extension writes no files itself. VS Code keeps the extension's output channel, described below, in its own log files.

The Neovim and LazyVim configurations read `VIRGIL_LOC` and send it to the server as the Virgil root. To choose a file's root directory, they look for `.virgil-lsp.json`, `DEPS`, or `.git` in the file's directory and above it. They write no files.

The Helix snippet writes no files. Its [setup guide](editors/helix/README.md#the-virgil-root) describes its initialization options, and [What the server sees](editors/helix/README.md#what-the-server-sees) describes its buffer selection.

The Emacs configuration reads no environment variables and sends no Virgil root unless you add one. To choose a Virgil file's project root outside version control, it checks whether `.virgil-lsp.json` exists in the file's directory and above it, without reading it. It writes no files.

## Logs

The server's log goes wherever the editor puts the server's standard error:

- **VS Code:** the "Virgil Language Server" output channel, together with the extension's own messages. VS Code also saves the channel in its log directory, as `Virgil Language Server.log`.
- **Neovim:** Neovim's LSP log file, whose path `:lua =vim.lsp.log.get_filename()` prints.
- **Emacs:** Eglot's events buffer, `M-x eglot-events-buffer`, which also records each message with its contents, including the full text of open files. Emacs keeps it in memory, and `eglot-shutdown` removes it unless you give it a prefix argument.

The log names files and directories, such as the server's path, the Virgil root, project files, and sources, and the names of projects. It can include compiler messages, which quote names and types from your code. A message trace adds the messages between the editor and the server. In VS Code, with the channel's log level set to Trace, the default `virgil.trace.server` value, `messages`, records only each message's name and, for a response, how long it took; `compact` and `verbose` also record each message's contents, including the full text of open files, so they appear only if you change the setting from its default. In Neovim, `:lua vim.lsp.log.set_level("debug")` records each message with its contents, including the full text of open files ([Logs and restarting](docs/troubleshooting.md#logs-and-restarting)). Remove anything private from a log before you share it.

## Questions

Ask about this statement in an [issue](https://github.com/SaltedTan/virgil-language-server/issues). Report security problems as [SECURITY.md](SECURITY.md) describes.
