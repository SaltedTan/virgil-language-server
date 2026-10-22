# Known limitations

This page lists what the v0.1 alpha doesn't do, or does only in part, as of `main`. Rename and completion came after `0.1.0-alpha.1`, which has neither, so [What rename refuses](#what-rename-refuses) applies to `main` only. Each item links to the page that owns the details. Check it before [reporting a bug](https://github.com/SaltedTan/virgil-language-server/issues/new?template=bug_report.yml).

## Features not yet implemented

- Completion suggests the keywords that can start where the cursor is, from the open document's text, even while the file has syntax errors. In a project, it also suggests the names in scope there, and after a `.`, the members of the receiver's type instead. It suggests nothing in comments or strings. A file that isn't open gets no suggestions ([Completion](architecture.md#completion)).
- Formatting and debugging are out of scope, and incremental analysis comes after v0.1 ([v0.1 scope](../README.md#v01-scope)).

## Semantic features need a project file

Without a `.virgil-lsp.json` that selects it, a document stays in [single-file mode](configuration.md#without-a-project-file), with no type errors, definition, hover, references, signature help, or rename, and it contributes no workspace symbols. A project that lists `virgilDependencies` also needs the editor to send a Virgil root; without one, its project file gets a `MissingVirgilRoot` error and its documents stay in single-file mode ([The Virgil root](configuration.md#the-virgil-root)).

## No error-tolerant parsing

- While any file of a project has a syntax error, Aeneas doesn't type-check the program. The project's type errors disappear, definition, hover, references, and signature help return nothing, and rename is refused, until every file parses again ([Semantic diagnostics](architecture.md#semantic-diagnostics), [Definition](architecture.md#definition)). The server doesn't fall back to an earlier analysis, which described other text: that needs an edit-mapping policy, and error tolerance belongs to [M6](../ROADMAP.md#m6--error-tolerance-and-incrementality-48-weeks-evidence-driven).
- Signature help therefore shows nothing for a call that is still being typed, such as `f(a,` or an unclosed `f(`: it doesn't parse. A complete call with a semantic error, such as the `f()` that an editor leaves when it closes `(` at once, does parse, and gets its signature. [#128](https://github.com/SaltedTan/virgil-language-server/issues/128) measured a repair of incomplete code at the cursor, which [ADR-0006](decisions/0006-incomplete-code-repair.md) adopts for semantic completion; signature help doesn't use it yet ([Signature help](architecture.md#signature-help), [Repair spike](architecture.md#repair-spike)).
- Completion suggests names or members in code that doesn't parse only when one repair at the cursor makes it parse: a name, the closing `)`, `]`, or `>` of what is open in the innermost block that the text doesn't already have, and a `;` where the statement ends. An editor that closes a delimiter as soon as it is typed leaves the closers after the cursor, as in `var n = Util.add(|)`, and the repair puts the `;` after them, so the arguments still get names. Anything else gets keywords only, or nothing after a `.`: a syntax error elsewhere in the file or in another file of the project, or a missing `}`, as in a lambda or `if` body you are still typing. The type checker gives up after 15 errors, and completion then suggests names only where it got to: in a file that has lost declarations other files need, such as one cut off at the cursor, the method you are typing in may get keywords only ([Completion](architecture.md#names), [ADR-0006](decisions/0006-incomplete-code-repair.md)).
- Completion follows the type checker's rules for which names a spot can use, not for their types. In a match on an `int`, it suggests every constant a case can compare, whatever its type, and in a component's field initializer it can suggest a field whose type the compiler cannot infer there because the fields' initializers depend on each other in a cycle ([Completion](architecture.md#names)).
- With `-lang:descriptors` enabled, completion of plain names doesn't suggest a class's descriptor fields. After a `.`, as in `b.`, it does ([Completion](architecture.md#completion)).
- If document-symbol parsing fails, the outline is empty, never stale or truncated. That parse normalizes carriage returns, so a CRLF file can have syntax diagnostics and still an outline ([Document symbols](architecture.md#document-symbols)).
- An analysis stops at 15 errors, and an Information diagnostic (`ErrorLimit`) then says that errors elsewhere may be missing. Aeneas rejects carriage returns, as `v3c` does, with one error per CRLF line, so a single CRLF file reaches the limit. Convert CRLF files to LF, which WSL users' checkouts may need ([error limit](architecture.md#semantic-diagnostics)).

## Member completion

After a `.`, completion suggests the members that the type checker would accept for the receiver's type, and nothing else: no keywords, and no names in scope. It doesn't guess, so some places get an empty list ([Member completion](architecture.md#member-completion)):

- **After a number.** A `.` after a number, as in `1.` or a tuple element's `t.0.`, gets nothing, because the server can't tell it from a number such as `1.5` being typed. Write `(t.0).` to get the element's members.
- **Unknown receivers.** A receiver whose type the type checker couldn't work out, such as a misspelled name or a call to a method that doesn't exist, gets nothing.
- **Unreached code.** Code that the type checker didn't reach before its error limit gets nothing, as with names. Neither do the arguments of a call to a function that doesn't exist yet, as in `missing(a.`: the type checker skips them.
- **Layout fields as values.** With the cursor before an existing layout field's name, as in `L.f` used as a value, nothing is suggested, because the type checker records nothing there that shows it looked up the layout. With `|` marking the cursor, `L.|`, `L.|size`, and `L.|f.offset` suggest the layout's members; `L.f.|offset` suggests the field's `offset` member.
- **Integer values.** A value of an integer type, such as `x.` for an `x: int`, has no members to suggest. `int.max`, `int.view`, and the other integer members belong to the type, so `int.` suggests them.
- **Type positions.** Where a type goes, as in `var s: Flag.` before `set`, nothing is suggested; members are suggested only in expressions.
- **Operators.** Casts, queries, comparisons, and index methods, such as `int.!`, `int.?`, `==`, and `[]`, are written with symbols, not names, so they aren't suggested.
- **Pointers.** Pointer members aren't suggested, because the server analyzes programs without a compilation target, and Aeneas defines pointers only for one.
- **Strings.** A member's description shows types as the type checker resolved them, so a `string` appears as `Array<byte>`.

## Stale results

Semantic results come from the newest analysis of the document's project. An analysis starts 200 ms after the last edit, or at once on open, save, or close ([scheduling](architecture.md#semantic-diagnostics)).

- **Diagnostics.** After an edit, a file's type errors disappear until the next analysis completes; its syntax errors follow the edit at once. An analysis that a later edit made stale is discarded, not published.
- **Definition, hover, references, and signature help.** A request against a stale or missing snapshot waits for the pending analysis, then answers from it. The answer is empty if that analysis is stale too, or if the document changed while the request waited. Without a pending analysis, for example after a failed one, the answer is empty at once ([Definition](architecture.md#definition), whose rules [Hover](architecture.md#hover), [References](architecture.md#references), and [Signature help](architecture.md#signature-help) share).
- **Completion.** Names and members need an analysis of the whole project for each new cursor position or document version, about 130 ms for the Aeneas sources, after any analysis that is already running. Requests that need only keywords are answered at once; requests that need names wait and receive both together. A newer request replaces an older one that hasn't started; the older one gets only keywords, or an empty list after `.`. The server keeps no candidates from an earlier document version ([Completion](architecture.md#names)).
- **Rename.** A rename waits for a pending analysis as definition does, and is refused if its document changed while it waited, or if any document or the project's files changed while its edits were checked; try it again then ([Rename](architecture.md#rename)).
- **Failed analyses.** When an analysis fails, for example because the worker crashed, ran past 10 seconds, or returned a result over the size budget, the published diagnostics stay as they were, and the server logs a warning. The first failure also shows a warning message; later ones are only logged until an analysis succeeds again. The server doesn't retry: the next open, edit, save, close, or file event starts a new analysis, in a new worker after a worker failure ([failures](architecture.md#semantic-diagnostics), [restart policy](decisions/0004-analysis-worker-process.md#restart-policy)).
- **Changes outside the editor.** The server learns about them only from the client's file events. Clients without file events, such as Neovim 0.11.6 on Linux and in WSL 2 with the shipped configurations, see them when a project is next used; restart the server after a checkout that adds or removes files ([Changes on disk](configuration.md#changes-on-disk)).

## Supported program size

The largest program tested is the Aeneas sources and their dependencies. [Supported program size](decisions/0004-analysis-worker-process.md#supported-program-size) owns its measured result size, the regression bound, and capacity estimates. The result's size, not a line count, is the main limit.

A project's program may also select at most 1,024 files and 4 MiB of paths and source ([Patterns](configuration.md#patterns)), and each analysis gets the worker's 384 MB heap and 10 seconds. A project over the file or source limit gets a configuration diagnostic. A program whose result is over budget gets no semantic results: its analyses fail with a warning. Analyze fewer files together, as a smaller project; a larger `WORKER_HEAP` doesn't raise the result budget.

Every analysis checks the whole program again. For the Aeneas sources, diagnostics arrived about 220 ms after a save on the measured machine ([measurements](architecture.md#semantic-diagnostics)).

Open documents share 1 MiB of text and metadata. Beyond that, the server shows a warning and ignores the open or edit; close other documents, then reopen the document or edit it again ([Document store](architecture.md#document-store)).

## Platforms and editors

- Supported hosts are Linux x86-64, including SSH remotes, and macOS on Apple Silicon, which runs the Intel (`x86-64-darwin`) build under Rosetta 2 ([host platforms](compatibility.md#host-platforms)).
- WSL 2 on x86-64 Windows is best effort. The Linux build is expected to run inside it, but nobody tests it there, and CI runs only on Linux itself ([host platforms](compatibility.md#host-platforms)).
- Native Windows, WSL 1, and Linux arm64 aren't supported. On Windows, use WSL 2, on a best-effort basis; the VS Code extension refuses to start natively on Windows ([startup checks](../clients/vscode/README.md#startup-checks)). macOS on Intel uses the same build but is untested.
- Apple Silicon needs Rosetta 2 until Virgil can build for `arm64-darwin`. Apple has said that macOS 27 is the last release with full Rosetta 2 support ([ADR-0003](decisions/0003-supported-platforms.md)).
- Neovim needs version 0.11 or later, and VS Code 1.91 or later. The LazyVim configuration isn't tested in CI; a scripted run on Linux passed for this alpha ([editors](compatibility.md#editors)). In LazyVim, noice.nvim drops servers' `window/showMessage` warnings, such as "Invalid project file …", although the diagnostics still appear; [the LazyVim setup](../editors/nvim/README.md#lazyvim) shows how to turn its handler off. The [manual test matrix](manual-testing.md) records which editor and platform combinations were tried.

## Installation

- The server executables are unsigned and not notarized. On macOS, a browser marks a download as quarantined, and macOS may then refuse to run it: download with `curl` or `gh`, or remove the attribute as [Installing on macOS](releasing.md#macos) shows. [Verifying a download](releasing.md#verifying-a-download) checks an archive against `SHA256SUMS`.
- Nothing is published to a package manager, the Visual Studio Marketplace, or Open VSX. Install the server from a release archive and the extension from its VSIX by hand ([Installing](releasing.md#installing), [Installing from a VSIX](../clients/vscode/README.md#installing-from-a-vsix)). VS Code doesn't update an extension installed from a VSIX.
- The archives don't include Virgil's library sources. A project with `virgilDependencies` needs a Virgil checkout at the revision that `virgil-lsp --version` prints ([The Virgil root](releasing.md#the-virgil-root)).

## What rename refuses

[Rename](architecture.md#rename) is deliberately conservative: it refuses rather than risk an edit that changes what the program means, and its reason says why. [Prepare rename](architecture.md#prepare-rename) lists the rules for which names can be renamed, and [Rename](architecture.md#rename) the rules for new names and edits.

- Start rename or prepare rename from an open editor document. Requests from closed files are refused because their positions can move on disk without a file notification; the rename can still edit other closed files ([request document](architecture.md#rename)).
- It refuses to rename anything while the project's analysis has any error, a parse error or a semantic one, anywhere in the project. A use that the verifier did not resolve would be missed ([eligibility](architecture.md#prepare-rename)).
- It analyzes the whole program again with the edits applied before it replies, so a rename takes about as long as an analysis of the project ([measurements](architecture.md#rename)). One rename is checked at a time; another sent meanwhile is refused until the first is answered ([scheduling](architecture.md#rename)).
- It refuses a method that shares its name with any other method, in any class or component, since the analysis records no class hierarchy and cannot tell an override from an unrelated method ([eligibility](architecture.md#prepare-rename)).
- It refuses to give a method a new name that any other method already has, for the same reason: renaming a base class's method to a subclass method's name, or the reverse, would make one override the other while every use still binds as before ([new names](architecture.md#rename)).
- It refuses a name token that declares two things, each with uses: `x` in `class D(x: int) extends Base(x)` names both the class parameter and its field, and one token cannot take two new names ([shared name tokens](architecture.md#prepare-rename)).
- It refuses packings and their parameters, constructors, operators, and the navigation aliases `this` and `.new`, which it never edits when it renames their class ([eligibility](architecture.md#prepare-rename)).
- It edits only the project's own sources: not a dependency, a library under the Virgil root, or a file that another loaded program also selects ([ownership](architecture.md#prepare-rename)).
- The check of which loaded programs own the renamed files has a budget of 8,000,000 matching steps. A workspace with so many project patterns that the check exhausts it gets "the workspace has too many project patterns to check which programs own the renamed files" ([ownership budget](architecture.md#prepare-rename)).
- That check compares paths as text, without resolving them. Another program that reaches the same file through a hard link, or, on a case-insensitive file system, through a directory spelled with different case, is not found, so the rename can edit a file that program also analyzes ([path identity](architecture.md#uri-identity-on-linux-and-macos)).
- It refuses a new name that would make any use bind differently, even if the program still verifies, a new name that is a keyword, `this`, or not an identifier, and one longer than 255 bytes ([new names](architecture.md#rename)).
- A rename has at most 10,000 edits and a reply of at most 4 MiB of encoded JSON. A larger one is refused whole, never sent in part ([edits](architecture.md#rename)).
- The editor must accept versioned edits (`workspace.workspaceEdit.documentChanges`), so that an edit to a document that changed meanwhile is refused. VS Code does. Neovim 0.11.6 applies them but omits them from its capabilities, so the shipped [Neovim configurations](../editors/nvim/README.md#rename) declare them for it, and Neovim checks the version of only the first document of an edit ([client requirements](architecture.md#rename)).
- It never edits comments, strings, `#packing` hints, or file names.

## Names that definition, hover, and references don't cover

Only the uses that the [occurrence index](architecture.md#compiler-adapter) records resolve; it owns the full list. In particular:

- Builtin types, such as `int`, `Array` in `Array<Foo>`, `set` in `E.set`, and `Ref` in `Ref<L>`, have no definition or references. Hover shows the type of a builtin used in an expression, such as `.length`, but nothing for a builtin type in a type position ([Hover](architecture.md#hover)).
- The letters of a bit pattern, such as `0b_aabb`, and the names in a `#packing` hint on a variant case aren't indexed ([#111](https://github.com/SaltedTan/virgil-language-server/issues/111)).
- Hover shows nothing at a keyword, a literal, or an unresolved name, and doesn't show the type of a larger expression.

## Signature help

[Signature help](architecture.md#signature-help) shows the signature that the analysis recorded for the call around the cursor, and owns the details. In particular:

- A signature's label is cut at 256 bytes and then ends with `…`. Only the parameters that the cut label holds are listed, and a later active parameter is reported as its index past the list.
- A call with more arguments than its function has parameters, which is a type error, reports the parameter index past the last, as does a cursor after a trailing comma that follows as many arguments as there are parameters, as in `add(1, 2,)`.
- How a client shows an active parameter past the list varies: LSP 3.17 specifies that it defaults to parameter 0, VS Code highlights no parameter, and Neovim 0.11.6 highlights the last.
- A client without `labelOffsetSupport` gets each parameter's label as a string, and finds it in the signature's label itself: in `(int, int) -> int`, both parameters are `int`, and such a client may highlight the first for either. VS Code and Neovim both support label offsets, which are exact.
- A call without a known signature gets nothing: an unresolved callee, and the calls in its arguments, which verification doesn't check either.
- There is no documentation, since the analysis doesn't keep doc comments.

## Crashes in the compiler front end

Some inputs crash or hang the pinned Aeneas front end. [Aeneas crash paths](aeneas-crash-paths.md) lists the known ones and how each is contained. Whole-program analysis runs in a separate worker process, so a crash fails that analysis, a hang is stopped after 10 seconds, and a new worker runs the next analysis (see [Stale results](#stale-results)). Single-file parsing runs in the server, with guards for every known parser crash; none of the known inputs stops the server. Report a new one with the smallest input that shows it.
