# 0019. Format Virgil code in its author's style, keeping line breaks

- Status: Proposed; supersedes the roadmap's formatter non-goal
- Date: 2026-11-18
- Discussion: [#504](https://github.com/SaltedTan/virgil-language-server/issues/504)

## Context

The [roadmap](../../ROADMAP.md) previously listed a formatter as a non-goal for the first release ("Formatting is a separate language-design/tooling project"), and its feature table deferred `formatting` to "a future canonical formatter". Virgil has no formatter. Aeneas's `VstPrinter` reprints a syntax tree without its comments, so it can't format source. Editors therefore can't format Virgil: LazyVim's format on save and `<leader>cf` do nothing for `.v3` files, nor does VS Code's Format Document, and this repository's style is enforced only by review.

Ben Titzer's code in Virgil and Wizard engine, about 160,000 lines, has a consistent style that a formatter can follow. Tabs indent 99% of indented lines. `{` ends its line, and `} else {` shares one. A statement is written `if (c)`, and the expression form `if(c, a, b)`. Annotations are `x: T`, and empty bodies `{ }`. There are many one-line braceless `if (...) stmt;` and hand-aligned columns of `=`, `=>`, `//`, and `:`. There is no line limit; with tabs eight columns wide, 99% of lines fit in 130 columns, and the longest has 239. A canonical reprinter, which chooses every line break and wraps long lines, would rewrite most of this code and lose its one-line forms and its alignment.

The server already parses an open document's text without verifying it, for outlines, folding ranges, and selection ranges (`AeneasAdapter.foldingRanges`), under the 350,000-token [parse budget](../architecture.md#document-symbols). Its `SymbolSyntaxIndex` records each token the parser consumes, and after a parse without errors the gaps between those tokens hold only whitespace and comments; the folding collector relies on that.

A spike ran a throwaway prototype over `aeneas/src`, `lib`, `apps`, and `test` of the pinned Virgil (`b86f43f1`), over Wizard engine at `958c6ad5`, and over this repository. The spike note has line-diff totals, counts by edit kind, and sample diffs. [Formatter spike](../testing-notes/formatter-spike.md)

| Corpus | Lines | Lines changed |
| --- | ---: | ---: |
| `aeneas/src` | 70,481 | 448 (0.64%) |
| `lib` | 17,424 | 258 (1.48%) |
| `apps` | 8,419 | 83 (0.99%) |
| Wizard engine | 65,861 | 1,040 (1.58%) |
| This repository's `src/` and `test/`, without `test/fixtures/` and `test/e2e/` | 63,939 | 35 (0.05%) |

Most of the changes to Titzer's code are whitespace at line ends and at the end of files, and lines indented with spaces, many of them in files from other contributors. The rest bring a line to the form that the code itself uses nearly everywhere: for example, 41 statement `if(` against 13,692 `if (`, and 24 `}` and `else` on separate lines against 1,389 `} else`. Two places in 162,185 lines are judgment calls. Every file that the prototype formatted kept its tokens, and formatting it again changed nothing. The corpora and a review exposed fourteen rule bugs in the prototype, all fixed; the note lists them.

## Decision

1. **The server formats Virgil code in its author's style, keeping line breaks, as `gofmt` does for Go.** It keeps comments, one-line braceless statements, and hand-aligned columns, and normalizes indentation and spacing between tokens. The line-break exceptions in decision 3 are the `} else` and body `{` joins and end-of-file normalization. On 2026-11-18 the maintainer chose the `gofmt` convention of exactly one line end for command-line formatting and left insertion of a missing final line end to the editor for LSP formatting. It doesn't reprint the program, wrap lines, reorder anything, or take style options. This supersedes the roadmap's non-goal.

2. **Only whitespace around tokens and comments changes.** The formatter reads the tokens that the parser consumed and scans each gap once, including before the first token and after the last. It emits one nonoverlapping byte-offset edit for each changed whitespace run, with comments separating runs and an empty range for an insertion. Each token and each comment keeps its bytes, except that trailing whitespace after a line comment is removed. Lines inside a block comment keep theirs, and a block comment left open at the end of the file, which the parser accepts, runs to the end untouched. Line breaks stay where they are except for the two joins and the end-of-file rule in decision 3, which leaves at most one line end after the last token or comment and requires one for command-line formatting where the rule applies. CR LF and a lone CR each count as one line end, as for the parse. Edits preserve the spelling of retained line ends and never split a CR LF pair or fuse a lone CR and a separate LF. A file that doesn't parse with its project's language options isn't changed, so a half-typed file is never mangled. A file with more tokens than the parse budget is refused, as an outline is.

3. **The rules.** Each one is the majority form in Titzer's code; the [spike note](../testing-notes/formatter-spike.md#the-counts-behind-each-rule) gives the counts.

   *Indentation*, in tabs:

   - A file-level line has no indentation. A line that starts a statement, a declaration, an enum case, or a match case or one of its patterns gets one tab more than the line where the statement that holds its block started. A `}` line gets that line's indentation. A `{` inside parentheses, such as a `fun` body passed as an argument, indents from its own line. The parse tree, not the tokens, says where enum cases start, since their commas are optional.
   - A braceless body on the line after `if (...)`, `while (...)`, `for (...)`, `else`, or a match case's `=>`, and a match case's `=>` at the start of a line, get one tab more than their statement's line.
   - Any other line continues an expression, a parameter list, or a header. It keeps its indentation, including spaces that align it. When its statement's first line is reindented, and that line's old indentation is a prefix of this line's, the prefix is replaced by the first line's new indentation; otherwise this line is left alone. Titzer indents these lines in several ways (one tab 75%, two tabs 11%, aligned under a parenthesis), and a rule that made them uniform would change 1,769 lines of that code.
   - A comment line takes the indentation of the code line after it, or the block's for a comment before `}`. A comment at column 0 stays there: 211 such lines in Titzer's code are section dividers and commented-out code. A comment inside parentheses or brackets follows that list's continuation lines.

   *Spacing* within a line:

   - `if (`, `while (`, `for (`, and `match (` for statements, and `if(` for an `if` expression. Whether an `if` is a statement or an expression comes from the parse tree: tokens alone misread an `if` expression in a `for` header after `;`, or in a `fun` body inside a match case.
   - `x: T`: no space before a colon that follows a name, and at least one after it.
   - At least one space on each side of `=` and the compound assignments, of `->`, including in function types such as `int -> bool`, and of `=>`. A layout's size, such as `=16;`, is not an assignment.
   - At least one space after a comma, unless the line ends there. Spaces before a comma are left alone: 20 of the 23 in Titzer's code align the columns of a table.
   - At least one space before a `{` that follows a token on its line, and before a `//` comment after code.
   - Exactly one space in `if (`, `while (`, `for (`, `match (`, `} else`, `else {`, `else if`, and `{ }`, and none in `if(` for an expression. An empty body `{}` becomes `{ }`.
   - Every other rule only inserts a missing space, or removes the single space before a colon. A gap of a tab or of two or more spaces is alignment and is kept, also before a colon, as in layout fields. Nothing else changes: `Array<int>`, `x.f(y)`, and `#hints` keep their spacing, and so do binary operators.

   *Lines*:

   - Trailing whitespace is removed, and whitespace-only lines become empty, including those before the first token. A whitespace-only line between a lone CR and an LF keeps its whitespace, since emptying it would fuse two line ends into one CR LF. Blank-line runs before the last token or comment are kept.
   - At the end of the file, remove whitespace after the last token or comment, keeping only the first line end if one follows it. If none follows it, the formatted result for every `virgil-lsp format` mode ends with the kind of line end used first in the file, or LF if the file has no line ends. `textDocument/formatting` leaves a missing final line end absent. A file with no tokens or comments becomes empty. If the file ends inside a block comment, the comment runs to EOF untouched and no line end is added.
   - `else` after a `}` that starts its line joins it as `} else`. After a one-line block, `if (c) { ... }`, an `else` on the next line stays.
   - A body's `{` on a line of its own joins the line before, which ends a header. A `{` after `;`, `{`, or `}` starts a block statement and stays.
   - Both joins apply only across exactly one line end with nothing but whitespace around it: never across a blank line or a comment.
   - An empty body whose `{` and `}` are on separate lines stays: 61 places in Titzer's code, mostly after long class or constructor headers.

4. **The formatter lives in `src/analysis/`, beside the folding collector.** `DocumentFormatter` walks the input's tokens and the gaps between them, with the few facts it needs from the parse tree: each `if` keyword's kind and where enum cases start. It computes the formatted byte size before building replacement text, so callers can enforce their output limits before allocating edits. It produces byte-offset edits of a server-owned type for callers that need them. `AeneasAdapter.formatting(path, input, options)` parses the current text with the project's language options under the parse budget, as `foldingRanges` does. It reports the first parse error or a parse-budget refusal. No Aeneas type leaves `src/analysis/` ([ADR-0002](0002-pinned-virgil-adapter-boundary.md)).

5. **The server answers `textDocument/formatting`.** It formats the open document's current text and returns the edits from decision 2 as LSP ranges in document order. Each edit covers one changed whitespace run, keeping tokens and comments out of the replacement ranges so editors can preserve cursors, marks, and folds there. If there are more than 10,000 edits, the server returns one merged edit from the first change to the last, including any intervening comments with their original bytes.

   - If the server drops a message without handling it, it marks every open document on that connection out of sync before handling the next message. This includes a skipped transport frame and any payload the JSON-RPC decoder rejects, such as one that exceeds the JSON value limit. A dropped message could have been a document change whose URI the server could not determine. If document synchronization rejects a change to an identified open document, for example because the store is over capacity, it marks that document out of sync. Formatting an out-of-sync Virgil document returns `RequestFailed` before parsing, saying the server's copy is out of date. Each document's mark clears when it gets an accepted full-text change or is closed and successfully reopened. A version-only change (empty `contentChanges`) keeps the mark because it cannot restore the text. The maintainer chose these rules on 2026-11-18.
   - The request removes trailing whitespace and extra line ends under decision 3. It leaves a missing final line end to the editor. For example, Neovim sends a buffer with `noeol` without a final line end and adds one on save with `fixeol`.
   - Indentation can make a document grow: a small file of deeply nested empty statements can grow past 1 MiB. The server uses the formatted size from decision 4 to refuse with `RequestFailed` if the document store, whose 1 MiB budget covers every open document and its metadata, couldn't accept the formatted text in place of the current one. It also refuses a reply whose JSON would exceed 4 MiB, the bound that document symbols use.
   - The request's `FormattingOptions` must include `tabSize` as an unsigned integer (`uinteger` in LSP 3.17) and `insertSpaces` as a boolean. A missing or wrongly typed member gets `InvalidParams`. The formatter uses its tab indentation rules regardless of these values.
   - A document that doesn't parse gets no edits and no error, since format on save of a half-typed file shouldn't interrupt with an error, and its diagnostics already show the syntax error. A document over the parse budget gets `RequestFailed`, as an outline does. A document that isn't Virgil, such as a project file, gets no edits.

   The server registers formatting dynamically for documents of the `virgil` language when the client supports `textDocument.formatting.dynamicRegistration`, as VS Code and Neovim do, so that VS Code doesn't offer it as a second formatter for `.virgil-lsp.json`. Otherwise it advertises `documentFormattingProvider` statically. Range formatting isn't offered. As [CONTRIBUTING](../../CONTRIBUTING.md#pull-requests) requires, the capability is advertised only by the change that implements and tests it.

6. **The same code formats files from the command line.** On 2026-11-18 the maintainer chose to offer standard-output mode for scripts and tests that need the formatted bytes, and `--write` so contributors can fix a failed check without an editor. The command is `virgil-lsp format [--check | --write] [-lang:<option>...] <file.v3>...`.

   - In every mode, the command checks each file's formatted byte size against a fixed output limit before building replacement text or formatted output. If the size exceeds the limit, it reports the failure for that file as it does for a syntax error.
   - With neither flag, it accepts one file and prints the formatted bytes to standard output.
   - `--check` finds the first changed line without building replacement text or formatted output. It names each file that would change, with that line, and prints nothing for a formatted file.
   - `--write` rewrites the files that change, and only those, so that a failed write leaves the original file in place.
   - It exits with 0 on success, with 1 if any file would change under `--check` or can't be formatted (unreadable, with a syntax error, over the parse budget, or over the output limit), and with 2 for invalid arguments.
   - It accepts the language options that a project file's `compilerArgs` accepts, with the same rules for values and repetition.

   `make check-format` runs `--check` on this repository's tracked `.v3` files, except `vendor/`, `test/fixtures/`, the editor tests' fixtures under `test/e2e/`, and `docs/examples/`, whose bytes and positions tests depend on. `make test` runs it as a suite, so CI fails on unformatted code.

7. **Editors format through the server.** LazyVim needs no change: its default autoformat formats `.v3` files on save through the language server, and `<leader>cf` formats on demand. Plain Neovim formats on demand with `vim.lsp.buf.format()`, and Helix's `:format` uses the server too. VS Code supports Format Document. Its extension contributes `"[virgil]": { "editor.formatOnSave": true, "editor.insertSpaces": false, "files.insertFinalNewline": true }` as configuration defaults, which users can override. Format on save calls the server, and VS Code adds a missing final newline when it saves. The maintainer chose these defaults on 2026-11-18.

8. **Tests.**

   - Unit tests cover each rule and each rule bug that the spike found. Cases include end-of-file handling and preservation of the whitespace that separates a lone CR from an LF on a blank line.
   - Every file that parses in the pinned `aeneas/src`, `lib`, `apps`, and `test` keeps its parser tokens when formatted, and formatting the result again changes nothing.
   - A suite formats `aeneas/src`, `lib`, and `apps` at the pin and compares a checked-in record: the pin, every file with whether it formatted or why not, a hash of each formatted file, and each file's changed lines. A rule change that starts rewriting Titzer's code fails it, as does a file that no longer formats. A pin update regenerates and reviews the record.
   - Protocol transcripts and an interactive test cover both registrations alongside watched-files registration, UTF-16 edit positions, CR LF and mixed line ends, separate nonoverlapping edits on either side of a comment, the merged edit above the limit, the growth and reply bounds, missing or wrongly typed formatting options, syntax errors, project files, and the parse budget. The pinned `lspcheck` profile still passes with `--strict`.
   - Protocol tests apply formatting edits to documents with and without a final line end. They verify that the request removes surplus trailing whitespace and line ends while leaving a missing final line end absent.
   - A protocol test verifies that formatting refuses after a rejected update and still refuses after a version-only update. It covers recovery after an accepted full-text update and after a close and reopen.
   - Protocol tests open multiple Virgil documents and exercise dropped messages, including a frame over the payload limit, a frame with an unsupported charset, and a full-text `didChange` that fits in a frame but exceeds the JSON value limit. Formatting returns `RequestFailed` for every open document, including after a version-only change. An accepted full-text change or a close and reopen restores formatting for that document while the others still get `RequestFailed`.
   - CLI tests verify that each mode reports failure and exits with 1 when a file parses but its formatted size exceeds the output limit. No formatted bytes are printed and the input file stays unchanged.
   - CLI tests use a file missing its final line end. `--check` reports the file, and standard-output mode and `--write` produce text with exactly one final line end.
   - `make test-nvim` formats on demand. `make test-lazyvim` and the VS Code Extension Host test format on demand and on save, save a file with a syntax error unchanged and without an error, and show that the project file has no Virgil formatter. In each editor test, a file opened without a final newline saves with exactly one after formatting.

## Consequences

- LazyVim and VS Code users get formatting with no setup, and it runs on every save. A team with another style turns off format on save, since the formatter has no options.
- Titzer's code isn't format-clean: formatting the pinned Virgil sources would change 0.6–1.5% of their lines, and Wizard engine 1.6%. The corpus record makes each of those changes visible, and a pin update shows how they move.
- The formatter's output is part of the server's behavior. A rule change that formats existing code differently is a user-visible change, recorded in the changelog.
- Each request costs one parse of the document, as folding ranges do, and a linear pass over its tokens.
- A file with a syntax error saves unformatted, with no message from the formatter. A file whose formatted text wouldn't fit in the document store is refused with a message.
- Code actions and rename can currently return edits against an out-of-date copy after a rejected document change or a message dropped without handling. The follow-up applies both synchronization rules to these features. [#514](https://github.com/SaltedTan/virgil-language-server/issues/514)
- `--write` needs a rename that the platform layers don't have yet, in `src/os/` for each target. [#508](https://github.com/SaltedTan/virgil-language-server/issues/508)
- This repository's sources need 35 lines changed once, mostly `{ if(c) stmt; }` in one-line blocks.
- Pin updates gain a step: regenerate and review the corpus record. The [pin update steps](../../CONTRIBUTING.md#updating-the-pinned-virgil-revision) will name it.
- The work is split into issues under [#504](https://github.com/SaltedTan/virgil-language-server/issues/504): the formatter's indentation and line rules, its spacing rules, the command, its `--write`, the corpus check, this repository's check, the LSP request, and the editors.

## Alternatives considered

- **Reprint from the syntax tree, as `VstPrinter` does.** It drops comments, and a canonical reprint discards the author's line breaks, one-line forms, and alignment.
- **A canonical formatter with a line limit, as `rustfmt` or Prettier do.** Titzer's code has no line limit and relies on hand alignment; such a formatter would rewrite most of it.
- **Indent every continuation line one tab more than its statement.** That would change 1,769 lines of Titzer's code, which aligns these lines in several ways.
- **Indent comments at column 0 like the code after them.** That would move 211 section dividers and commented-out lines in Titzer's code.
- **Tell an `if` expression from its tokens.** A comma in its parentheses misreads type arguments, as in `if (eq<T, int>())`, 130 times in `vendor/virgil/test`, and a statement-start rule over tokens misreads `for` headers and `fun` bodies inside match cases. The parser already decides.
- **Remove spaces before commas.** Twenty of the 23 in Titzer's code align a table's columns.
- **A separate formatter, outside the server.** It would need its own parser; the server already parses current text with recovery and a budget, and one formatter serves the editors and the check.
- **Replace the whole document.** Edits confined to changed whitespace runs let editors preserve cursors, marks, and folds in the surrounding text.
- **Answer a document with syntax errors with an error.** With format on save, every save of a half-typed file would show an error.
- **Follow `FormattingOptions.insertSpaces`.** The result would depend on each editor's settings, and Virgil code is indented with tabs.
- **Leave VS Code's format on save to each user.** LazyVim formats on save by default; the maintainer chose the same for VS Code.
