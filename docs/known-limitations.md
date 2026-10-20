# Known limitations

This page lists what the v0.1 alpha doesn't do, or does only in part. Each item links to the page that owns the details. Check it before [reporting a bug](https://github.com/SaltedTan/virgil-language-server/issues/new?template=bug_report.yml).

## Features not yet implemented

- Completion, rename, and signature help aren't implemented. The server doesn't advertise them ([protocol invariants](architecture.md#protocol-invariants)), and [M5](../ROADMAP.md#m5--references-rename-signature-help-and-completion-48-weeks) plans them.
- Formatting and debugging are out of scope, and incremental analysis comes after v0.1 ([v0.1 scope](../README.md#v01-scope)).

## Semantic features need a project file

Without a `.virgil-lsp.json` that selects it, a document gets syntax diagnostics and a document outline, but no type errors, definition, hover, or references, and it contributes no workspace symbols ([Without a project file](configuration.md#without-a-project-file)). A project that lists `virgilDependencies` also needs the editor to send a Virgil root; without one, its project file gets a `MissingVirgilRoot` error and its documents stay in single-file mode ([The Virgil root](configuration.md#the-virgil-root)).

## No error-tolerant parsing

- While any file of a project has a syntax error, Aeneas doesn't type-check the program. The project's type errors disappear, and definition, hover, and references return nothing, until every file parses again ([Semantic diagnostics](architecture.md#semantic-diagnostics), [Definition](architecture.md#definition)). The server doesn't fall back to an earlier analysis, which described other text: that needs an edit-mapping policy, and error tolerance belongs to [M6](../ROADMAP.md#m6--error-tolerance-and-incrementality-48-weeks-evidence-driven).
- If document-symbol parsing fails, the outline is empty, never stale or truncated. That parse normalizes carriage returns, so a CRLF file can have syntax diagnostics and still an outline ([Document symbols](architecture.md#document-symbols)).
- An analysis stops at 15 errors, and an Information diagnostic (`ErrorLimit`) then says that errors elsewhere may be missing. Aeneas rejects carriage returns, as `v3c` does, with one error per CRLF line, so a single CRLF file reaches the limit. Convert CRLF files to LF, which WSL users' checkouts may need ([error limit](architecture.md#semantic-diagnostics)).

## Stale results

Semantic results come from the newest analysis of the document's project. An analysis starts 200 ms after the last edit, or at once on open, save, or close ([scheduling](architecture.md#semantic-diagnostics)).

- **Diagnostics.** After an edit, a file's type errors disappear until the next analysis completes; its syntax errors follow the edit at once. An analysis that a later edit made stale is discarded, not published.
- **Definition, hover, and references.** A request against a stale or missing snapshot waits for the pending analysis, then answers from it. The answer is empty if that analysis is stale too, or if the document changed while the request waited. Without a pending analysis, for example after a failed one, the answer is empty at once ([Definition](architecture.md#definition), whose rules [Hover](architecture.md#hover) and [References](architecture.md#references) share).
- **Failed analyses.** When an analysis fails, for example because the worker crashed, ran past 10 seconds, or returned a result over the size budget, the published diagnostics stay as they were, and the server logs a warning. The first failure also shows a warning message; later ones are only logged until an analysis succeeds again. The server doesn't retry: the next open, edit, save, close, or file event starts a new analysis, in a new worker after a worker failure ([failures](architecture.md#semantic-diagnostics), [restart policy](decisions/0004-analysis-worker-process.md#restart-policy)).
- **Changes outside the editor.** The server learns about them only from the client's file events. Clients without file events, such as Neovim 0.11.6 on Linux and in WSL 2 with the shipped configurations, see them when a project is next used; restart the server after a checkout that adds or removes files ([Changes on disk](configuration.md#changes-on-disk)).

## Supported program size

The largest program tested is the Aeneas sources and their dependencies. [Supported program size](decisions/0004-analysis-worker-process.md#supported-program-size) owns its measured result size, the regression bound, and capacity estimates. The result's size, not a line count, is the main limit.

A project's program may also select at most 1,024 files and 4 MiB of paths and source ([Patterns](configuration.md#patterns)), and each analysis gets the worker's 384 MB heap and 10 seconds. A project over the file or source limit gets a configuration diagnostic. A program whose result is over budget gets no semantic results: its analyses fail with a warning. Analyze fewer files together, as a smaller project; a larger `WORKER_HEAP` doesn't raise the result budget.

Every analysis checks the whole program again. For the Aeneas sources, diagnostics arrived about 220 ms after a save on the measured machine ([measurements](architecture.md#semantic-diagnostics)).

Open documents share 1 MiB of text and metadata. Beyond that, the server shows a warning and ignores the open or edit; close other documents, then reopen the document or edit it again ([Document store](architecture.md#document-store)).

## Platforms and editors

- Supported hosts are Linux x86-64, including SSH remotes and WSL 2 on x86-64 Windows, and macOS on Apple Silicon, which runs the Intel (`x86-64-darwin`) build under Rosetta 2 ([host platforms](compatibility.md#host-platforms)).
- Native Windows, WSL 1, and Linux arm64 aren't supported. On Windows, use WSL 2; the VS Code extension refuses to start natively on Windows ([startup checks](../clients/vscode/README.md#startup-checks)). macOS on Intel uses the same build but is untested.
- Apple Silicon needs Rosetta 2 until Virgil can build for `arm64-darwin`. Apple has said that macOS 27 is the last release with full Rosetta 2 support ([ADR-0003](decisions/0003-supported-platforms.md)).
- Neovim needs version 0.11 or later, and VS Code 1.91 or later. The LazyVim configuration is untested ([editors](compatibility.md#editors)). The [manual test matrix](manual-testing.md) records which editor and platform combinations were tried.

## Installation

- The server executables are unsigned and not notarized. On macOS, a browser marks a download as quarantined, and macOS may then refuse to run it: download with `curl` or `gh`, or remove the attribute as [Installing on macOS](releasing.md#macos) shows. [Verifying a download](releasing.md#verifying-a-download) checks an archive against `SHA256SUMS`.
- Nothing is published to a package manager, the Visual Studio Marketplace, or Open VSX. Install the server from a release archive and the extension from its VSIX by hand ([Installing](releasing.md#installing), [Installing from a VSIX](../clients/vscode/README.md#installing-from-a-vsix)). VS Code doesn't update an extension installed from a VSIX.
- The archives don't include Virgil's library sources. A project with `virgilDependencies` needs a Virgil checkout at the revision that `virgil-lsp --version` prints ([The Virgil root](releasing.md#the-virgil-root)).

## Names that definition and hover don't cover

Only the uses that the [occurrence index](architecture.md#compiler-adapter) records resolve; it owns the full list. In particular:

- Builtin types, such as `int`, `Array` in `Array<Foo>`, `set` in `E.set`, and `Ref` in `Ref<L>`, have no definition. Hover shows the type of a builtin used in an expression, such as `.length`, but nothing for a builtin type in a type position ([Hover](architecture.md#hover)).
- The letters of a bit pattern, such as `0b_aabb`, and the names in a `#packing` hint on a variant case aren't indexed ([#111](https://github.com/SaltedTan/virgil-language-server/issues/111)).
- Hover shows nothing at a keyword, a literal, or an unresolved name, and doesn't show the type of a larger expression.

## Crashes in the compiler front end

Some inputs crash or hang the pinned Aeneas front end. [Aeneas crash paths](aeneas-crash-paths.md) lists the known ones and how each is contained. Whole-program analysis runs in a separate worker process, so a crash fails that analysis, a hang is stopped after 10 seconds, and a new worker runs the next analysis (see [Stale results](#stale-results)). Single-file parsing runs in the server, with guards for every known parser crash; none of the known inputs stops the server. Report a new one with the smallest input that shows it.
