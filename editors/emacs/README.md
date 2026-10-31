# Emacs / Eglot

> **Best effort.** This configuration is outside the [tested editor matrix](../../docs/compatibility.md#editors), and no CI job runs it. Its ERT tests run by hand, and the [tested versions](#tested-versions) section records a session with a real server. See the [project status](../../README.md) for current server features.

Requires Emacs 30 or later, whose built-in Eglot talks to `virgil-lsp`, and the [installed server on PATH](#installing-the-server). No package is needed.

[`virgil-eglot.el`](virgil-eglot.el) provides three things:

- `virgil-mode`, derived from `prog-mode`, for `.v3` files. It defines `//` and `/* */` comments, so comment commands such as `M-;` work, and nothing else: it provides no syntax fontification, no indentation, and no Tree-sitter grammar.
- An `eglot-server-programs` entry that starts `virgil-lsp --stdio` for `virgil-mode` buffers only, with the language ID `virgil`.
- A [project root](#project-roots) at the nearest `.virgil-lsp.json` for Virgil files outside a version-controlled checkout.

Loading the file starts no process and doesn't load or enable Eglot.

## Installing the server

Install the server from a release archive, and verify the archive against the release's `SHA256SUMS` first, as [Editor setup](../../README.md#editor-setup) shows for Linux, and [Verifying a download](../../docs/releasing.md#verifying-a-download) and [Installing](../../docs/releasing.md#installing) describe for both platforms. Keep `virgil-lsp-worker` and `virgil/` beside the server. Then check that `virgil-lsp --version` works in the terminal that starts Emacs. Emacs started from a desktop launcher may have another `PATH`; `M-: (executable-find "virgil-lsp")` shows what Emacs finds.

## Loading the configuration

Download [`virgil-eglot.el`](virgil-eglot.el) into a directory on your `load-path`, such as `~/.emacs.d/lisp/`. Releases up to 0.1.0-alpha.5 don't include the file, so take it from `main` until a later release does, and replace `config_url` with that release's raw source location then. The file is downloaded beside its destination and moved into place only once the download succeeds:

```sh
config_url=https://raw.githubusercontent.com/SaltedTan/virgil-language-server/main/editors/emacs
mkdir -p ~/.emacs.d/lisp
curl --fail --location "$config_url/virgil-eglot.el" -o ~/.emacs.d/lisp/virgil-eglot.el.part &&
mv ~/.emacs.d/lisp/virgil-eglot.el.part ~/.emacs.d/lisp/virgil-eglot.el
```

Then add to your init file:

```el
(add-to-list 'load-path (locate-user-emacs-file "lisp"))
(require 'virgil-eglot)
```

From a source checkout, add its `editors/emacs` directory to `load-path` instead. To try it without your own configuration:

```sh
emacs -Q -L editors/emacs -l virgil-eglot path/to/file.v3
```

## Using it

Open a `.v3` file and run `M-x eglot` to start the server for the file's project. Eglot then manages every Virgil buffer in that project. To start it whenever a Virgil file is visited instead, opt in with a hook:

```el
(add-hook 'virgil-mode-hook #'eglot-ensure)
```

Eglot binds no keys of its own. The server's features reach Emacs through the usual commands: Flymake for diagnostics (`M-x flymake-show-buffer-diagnostics`), ElDoc for hover and signature help, `M-.` (`xref-find-definitions`) and `M-?` (`xref-find-references`), `C-M-i` (`completion-at-point`), and `M-x eglot-rename`. Library files that `M-.` opens, such as `virgil/lib/util/StringBuilder.v3`, are outside the project, so Eglot doesn't manage them; with `(setq eglot-extend-to-xref t)`, they use the same server.

- `M-x eglot-shutdown` stops the server. `M-x eglot-reconnect` restarts it with the options it started with.
- `M-x eglot-list-connections` lists the running servers.
- `M-x eglot-events-buffer` shows the messages between Eglot and the server, and the server's standard error, which includes its log, in lines marked `[stderr]`. `M-x eglot-stderr-buffer` shows the standard error alone. Messages include the text of open files, so remove anything private before you share them ([Privacy](../../PRIVACY.md#logs)). `eglot-shutdown` kills both buffers; with a prefix argument, `C-u M-x eglot-shutdown`, it keeps them.

Rename needs no extra setup: Eglot declares the versioned `documentChanges` that the server sends. A refused rename shows the server's reason, such as "rename needs a program that verifies" while the project has errors ([Rename](../../docs/architecture.md#rename)).

## The Virgil root

Project files name Virgil's own libraries relative to a Virgil root, as in `"virgilDependencies": ["lib/util/*.v3"]` ([Configuration](../../docs/configuration.md#the-virgil-root)). The entry sends no initialization options, so the server uses the library in the `virgil/` directory beside it, which release archives include. Exporting `VIRGIL_LOC` alone changes nothing here: the server doesn't read it, and this configuration doesn't map it to the option, unlike the Neovim configurations and the VS Code extension.

To use another root, such as a Virgil checkout for a project that selects files outside `lib/`, give its absolute physical path as the `virgilRoot` initialization option, after the `require` in your init file:

```el
(with-eval-after-load 'eglot
  (add-to-list 'eglot-server-programs
               '((virgil-mode :language-id "virgil")
                 "virgil-lsp" "--stdio"
                 :initializationOptions (:virgilRoot "/home/me/src/virgil"))))
```

The server reads the option only when it starts, and `eglot-reconnect` keeps the options that the server started with. After changing it, run `M-x eglot` again in a Virgil buffer and answer `y` to shut down the current connection, or run `M-x eglot-shutdown` and then `M-x eglot`. The events buffer shows the root that the server chose: `virgil-lsp: Virgil root: <path> (client)` for this option, or `(bundle, revision <revision>)` for the bundled library ([The Virgil root and the bundled library](../../docs/troubleshooting.md#the-virgil-root-and-the-bundled-library)).

## Project roots

Eglot starts one server per project, and sends the project's root to the server as its workspace folder. For a Virgil buffer, the project is found in this order:

1. Emacs's version-control finder: in a Git checkout, the root is the checkout's top directory, even if a `.virgil-lsp.json` is nearer.
2. Otherwise, the nearest directory above the file that contains `.virgil-lsp.json`, so a project outside version control works. With nested project files, the nearest one is the root.

Other buffers, including `.virgil-lsp.json` itself, are unaffected. The finder only checks whether the file exists: it doesn't read the file, and nothing in a project file can make Emacs or the server run a command ([Security](../../docs/configuration.md#security)).

This workspace root is not what decides a file's project. The server reads the nearest `.virgil-lsp.json` above each file, within the workspace folder, and uses the sources that it selects ([Workspace folders and active projects](../../docs/configuration.md#workspace-folders-and-active-projects)). So in a checkout with several project files, one server handles all of them, and each file still gets the project of its nearest project file.

Eglot doesn't send `.virgil-lsp.json` buffers to the server, so the server sees a project file's saved contents, not unsaved edits. Eglot does register the server's file watchers, so changes on disk, such as a saved project file or a checkout, reach the server ([Changes on disk](../../docs/configuration.md#changes-on-disk)).

## Tests

From the repository root, run the ERT tests by hand:

```sh
emacs -Q --batch -L editors/emacs -l editors/emacs/test-virgil-eglot.el -f ert-run-tests-batch-and-exit
```

They check `.v3` detection and comment syntax, the explicit language ID and that no other mode gets the entry, project roots outside version control, nested, and in a Git checkout, that other buffers are unaffected, and that loading the file and visiting a Virgil file start no process. With `virgil-lsp` on `PATH`, a last test starts it through Eglot, waits for a type error, and shuts it down; without it, that test is skipped. `make test` doesn't run them.

## Tested versions

On 2026-10-31, on Linux x86-64 with Emacs 30.2 and its bundled Eglot 1.17.30 (jsonrpc 1.0.25, project 0.11.1), a scripted session drove Eglot against the 0.1.0-alpha.5 release archive, installed from its checksum-verified download, on a copy of the Neovim test's fixture. It ran the same steps against a server built from this branch. In both:

- `M-x eglot` sent the language ID `virgil` and the fixture's directory as the root, and the server used its bundled library.
- Flymake showed the fixture's type error, "expected int in var initialization, got Array<byte>", and cleared it after an unsaved correction of the buffer.
- Hover on a use of `StringBuilder` returned `class StringBuilder`, and `xref-find-definitions` opened `virgil/lib/util/StringBuilder.v3` beside the server.
- Completion after `StringBuilder.new().` offered the class's members, including `puts`.
- `eglot-rename` renamed a local and its three uses in a small project that verifies.
- After `eglot-shutdown`, the server exited with status 0.

Another session with the release archive set `virgilRoot` as [above](#the-virgil-root): the server logged that root as `(client)`, and definitions opened its `lib/`. `eglot-reconnect` kept the old root, and `M-x eglot` again used the changed one. A third session installed the file with the [commands above](#loading-the-configuration), with the init file and the `eglot-ensure` hook from this page: the hook started the server, the events buffer showed the server's log, and with `eglot-extend-to-xref`, the library file opened by a definition used the same server.

With Eglot 1.17.30, `eglot-shutdown` sometimes reports "Server exited with status 9": its jsonrpc library deletes the server process if it hasn't noticed the process's exit within about a tenth of a second. In these sessions, the server itself exited with status 0 each time.

Emacs 29 bundles an older Eglot and wasn't tested.

## Troubleshooting

- If `M-x eglot` can't start `virgil-lsp`, see [Emacs doesn't find `virgil-lsp`](../../docs/troubleshooting.md#emacs-doesnt-find-virgil-lsp).
- [Troubleshooting](../../docs/troubleshooting.md) covers files without diagnostics or navigation, the Virgil root, and changes on disk. The [privacy statement](../../PRIVACY.md) says what the server and this configuration read, write, and start.
