# Formatter spike: rules measured on Titzer's code (2026-11-18)

This note records a spike using a throwaway Python prototype of the formatting rules, run over Ben Titzer's Virgil and Wizard engine code and over this repository. It gives line-diff totals, counts by edit kind, and the rule bugs that the corpora and a review exposed. [ADR-0019](../decisions/0019-formatter.md)

## Corpora

Only files that the server's parser accepts with the default language options are formatted, since the formatter leaves a file that doesn't parse alone.

| Corpus | Files that parse | Lines |
| --- | ---: | ---: |
| `vendor/virgil/aeneas/src` at the pin (`b86f43f1`) | 168 of 168 | 70,481 |
| `vendor/virgil/lib` | 66 of 67 | 17,424 |
| `vendor/virgil/apps` | 64 of 64 | 8,419 |
| `titzer/wizard-engine` at `958c6ad5` | 226 of 228 | 65,861 |
| `vendor/virgil/test` | 20,364 of 22,153 | 429,829 |
| This repository's `src/` | 97 of 97 | 27,128 |
| This repository's `test/`, without `test/fixtures/` and `test/e2e/` | 75 of 75 | 36,811 |

`lib/file/class/Layouts.v3` and most of the 1,789 test files that don't parse need language options, such as descriptors and open types, or are parser error tests. Wizard engine has many contributors, and most of its space-indented lines are in its `test/` directory.

## How the prototype works

It changes only whitespace around tokens and comments, including at the start and end of a file. Tokens, comments, and string and character literals keep their bytes, except that trailing whitespace after a line comment is removed. `else` after a `}` that starts its line joins that line, and a body's `{` on a line of its own joins the line before. Each join crosses exactly one line end with only whitespace around it. The proposed formatter computes indentation from the lines after the joins. The other exception to keeping line breaks is at EOF, where the last token or comment gets exactly one line end, as in `gofmt`. Whitespace-only files become empty. Every file the prototype formatted kept its token sequence, and formatting it again changed nothing. Its lexer couldn't read `vendor/virgil/test/core/parser/multiline00.v3`, which ends inside a block comment. The parser accepts that file, and the proposed formatter leaves the comment untouched through EOF.

The prototype's EOF rule matches the proposed command-line behavior for admitted files. The proposed LSP request leaves a missing final line end to the editor. Both proposed interfaces accept LF and CR LF, with each retained CR LF pair kept intact. They decline input with a CR that has no following LF because the folding parse ends a line comment there and the compiler's lexer continues it. [ADR-0019](../decisions/0019-formatter.md#decision)

The prototype classifies each line by the tokens before it:

- A **block line** starts a statement, a declaration, an enum case, or a match case or one of its patterns: the line after `;`, `{`, or `}`, after a `,` in the braces of an `enum` or `match`, or after an enum case, whose comma is optional. It gets one tab more than the line where the statement holding its block started, and none at file level. A `}` line gets that line's indentation. If a match case's `=>` starts a line, its braced body is one tab deeper than the arrow line and its closing `}` aligns with that line. For a body indented from its opening `{`, such as a `fun` passed as an argument, the line containing the brace after any join anchors both the body and the closing `}`.
- A **nested line** is a braceless body on the line after `if (...)`, `while (...)`, `for (...)`, `else`, or a match case's `=>`. It gets one tab more than its statement's line. A match case's `=>` that starts a line is also a nested line and gets one tab more than the start of the case.
- Any other line is a **continuation line**, including a line that starts with `=>` for a simple method body. It keeps its indentation. If its statement's first line was reindented, it moves with that line when the old indentation is a prefix of its own.
- A **comment line** takes the indentation of the code line after it, or of the block for a comment before `}`, except a comment at column 0, which stays.

## Results

Lines changed, with the files that change:

| Corpus | Lines changed | Share | Files |
| --- | ---: | ---: | ---: |
| `aeneas/src` | 448 | 0.64% | 88 |
| `lib` | 258 | 1.48% | 26 |
| `apps` | 83 | 0.99% | 38 |
| Wizard engine | 1,040 | 1.58% | 118 |
| `vendor/virgil/test` | 33,121 | 7.71% | 5,405 |
| This repository's `src/` | 28 | 0.10% | 10 |
| This repository's `test/`, without `test/fixtures/` and `test/e2e/` | 7 | 0.02% | 3 |

The totals come from a line diff of each file's input and output. A join counts as two lines, and each blank line removed at EOF counts separately. Kinds A–D count each edit once, at the line where it starts. A line with edits of two kinds counts under both. Even with that overlap, the different units give different sums, for example 441 against 448 in `aeneas/src` and 257 against 258 in `lib`.

The counts by edit kind in Titzer's code (lines where edits start, then files):

| Kind | `aeneas/src` | `lib` | `apps` | Wizard engine |
| --- | ---: | ---: | ---: | ---: |
| A. Trailing whitespace, whitespace-only lines, end of file | 162 (42) | 40 (13) | 37 (24) | 146 (84) |
| B. Lines indented with spaces or a mix of spaces and tabs | 89 (26) | 151 (6) | 9 (2) | 719 (25) |
| C. Lines with the wrong number of tabs | 19 (4) | 23 (2) | 15 (3) | 18 (4) |
| D. Spacing that differs from the majority | 171 (61) | 43 (16) | 32 (19) | 173 (49) |

Kind D by rule:

| Rule | `aeneas/src` | `lib` | `apps` | Wizard engine |
| --- | ---: | ---: | ---: | ---: |
| `if(` statement, `match(`, `for(`, or `while(` gets a space | 57 | 3 | 6 | 16 |
| `if (c, a, b)` expression loses its space | 47 | 1 | 13 | 79 |
| A space after a comma | 28 | 1 | 1 | 28 |
| `}` then `else` on the next line join | 15 | 3 | 0 | 6 |
| `{}` becomes `{ }` | 14 | 9 | 10 | 14 |
| `x:T` or `x : T` becomes `x: T` | 4 | 22 | 0 | 11 |
| A space each side of `=` | 3 | 0 | 0 | 3 |
| A space each side of `=>` | 2 | 0 | 0 | 0 |
| A space before `{` | 1 | 1 | 1 | 15 |
| A space before a trailing `//` comment | 0 | 3 | 1 | 1 |

In this repository, 32 of the 35 changed lines are an `if(` or `for(` statement inside a one-line block, such as `for (c in name) { if(j < n && lower(c) == lowered[j]) j++; }`, which gets `if (`; the others are one `}` and `else` join, which counts as two lines, and one `{}`.

### Counts in the corpus record

The implementation's [corpus record](../../test/format/virgil-corpus.txt), made at the same pin by `test/format/corpus.py`, changes the same 88, 26, and 38 files, with the same counts of edits of kinds A, B, and C, and the same counts of lines for each rule of kind D, except one. It counts each line of the input that an edit touches, which gives 458 changed lines in `aeneas/src`, 259 in `lib`, and 93 in `apps`, against 448, 258, and 83 above:

- `aeneas/src` and `apps` each have 10 files with no line end after their last line, and the only change to that line is the line end that formatting adds. The record counts those 20 lines; kind A above counts their edits too, but the totals above leave them out. `lib` has 4 such files, and both count them.
- In `lib`, `util/Sexpr.v3` is indented with spaces, and a line diff of it matches one reindented line with an identical line next to it, so it counts one line fewer than the record's 81.
- The rule that puts a space each side of `=` changes 4 lines of `aeneas/src`, not 3. One of them, `types/Type.v3:263`, `new() { if(typeEnv == null) var x=1/0; }`, also gets `if (`, and the table above has it under that rule only. The totals count it once either way.

### Classification of the changes to Titzer's code

Every change in kinds A, B, and D makes a line follow the majority form measured below; none is a rule bug. Kind C, checked line by line:

- Genuine inconsistencies: a comment one tab short (`ir/Normalization.v3:556`); two stray extra tabs (`ir/SsaNormalizer.v3:1201`, `1219`); match cases at the same indentation as their `match` (`vst/Vst.v3:155`, `wasm/WasmGcTarget.v3:17`, against more than 1,300 indented matches); a file-level method whose body has two tabs (`lib/asm/x86/X86Assembler.v3:23`); a loop at its enclosing loop's indentation inside that loop's braces (`apps/ZiVm/ZiVmTest.v3:204`); section comments one tab in among file-level definitions (`apps/ZiVm/ZiVm.v3:15`); and in Wizard engine, match cases two tabs in (`src/engine/CodeValidator.v3:236`), block bodies two tabs in (`src/engine/x86-64/X86_64Interpreter.v3:1940`, `2147`), a stray tab (`src/modules/wasi/x86-64-linux/WspOneErrors-x86-64-linux.v3:63`), and a braceless body at its `if`'s indentation (`src/monitors/IfMonitor.v3:16`).
- Judgment calls, two places in 162,185 lines: stacked loops, `for (f in pop.families)` with `for (z in f.zoomers) {` below it at the same indentation (`apps/Zoomers/Zoomers.v3:298`, 10 lines), which the formatter indents as a nested body; and a comment that annotates the continuation line above it (`src/engine/x86-64/X86_64Interpreter.v3:330`), which moves to the indentation of the declaration below it.

Two examples of kind D at Virgil revision `b86f43f1`.

`aeneas/src/mach/MachProgram.v3`, lines 409–415

```diff
 		if (PrimType.?(t)) return PrimType.!(t).byteSize;
-		match(t.typeCon.kind) {
+		match (t.typeCon.kind) {
 			COMPONENT => return 0;
 			CLASS,
 			ARRAY,
 			ANYREF => return refSize;
-			VARIANT => return if (prog.ir.isEnum(t), V3.getVariantTagType(t).byteSize, refSize);
+			VARIANT => return if(prog.ir.isEnum(t), V3.getVariantTagType(t).byteSize, refSize);
```

`aeneas/src/mach/LinearScanRegAlloc.v3`, lines 513–516

```diff
 				else next = USE_FIXED;
-			}
-			else if (uv == gen.KILL) next = KILL;
+			} else if (uv == gen.KILL) next = KILL;
 			else if (uv == gen.LIVE) next = KILL;
```

`vendor/virgil/test` changes much more: 12,456 lines of kind A, 12,044 of kind B, 6,244 of kind C (deep-nesting and generated tests, such as staircases of one-space indents), and 4,634 of kind D, 2,226 of them a missing space after a comma in dense literal tables. It serves to test robustness, not style.

## The counts behind each rule

Across `aeneas/src`, `lib`, `apps`, and Wizard engine:

| Choice | Majority | Minority |
| --- | --- | --- |
| Statement `if` | `if (` 13,692 | `if(` 41 |
| Expression `if` | `if(` 1,432 | `if (` 145 |
| `match`, `for`, `while` | `match (` 1,354, `for (` 3,002, `while (` 439 | `match(` 35, `for(` 4, `while(` 2 |
| `:` after a name | none before, 30,804; one space after, 30,330 | one space before, 31; none after, 12 |
| `=` | a space before, 33,631; after, 34,621 | none before, 4; after, 5 |
| `->` | a space each side, 8,298 | none |
| `=>` | a space before, 9,526; after, 8,599 | none before or after, 1 each |
| `,` | one space after, 81,085 | none after, 229 |
| Empty body | `{ }` 344 | `{}` 46, `{  }` 3; `{` and `}` on separate lines 61, kept |
| `else` after a block | `} else` 1,389 | `}` starting its line, then `else` on the next, 24 |

Tabs or several spaces in a gap are alignment, and are kept: 1,406 gaps before `=` and 412 after it, 547 before `=>` and 1,471 after it, 506 after `:` and 13 before it (layout fields), and 672 before `{` (tables of one-line methods). Spaces before a comma are kept too: 89,211 commas have none, but 20 of the 23 that do align the columns of a table in `apps/ZiVm/ZiVm.v3`, where the longest entry has one space, as in `put2(JMP , ref(label))` above `put2(MSTB     , reg.tag)`. After a one-line block, `else` on the next line is kept: 35 times, as in `if (isDouble) { ... }` followed by `else { ... }`. A body's `{` on a line of its own occurs nowhere; the 12 lone `{` lines are block statements, which are left alone. Runs of two or more blank lines are kept (36).

Indentation: a tab starts 142,305 indented lines and a space 927, so tabs indent 99% of them (98.7% in Wizard engine, 99.9% in `aeneas/src`). Lines have no limit: with tabs eight columns wide, 99% of lines fit in 129 columns, and the longest has 239.

| Lines | Count |
| --- | ---: |
| Comment lines at the indentation of the code after them | 11,231 |
| Comment lines at column 0 inside a body (section dividers, commented-out code) | 211 |
| Comment lines reindented, mostly in space-indented files | 62 |
| Continuation lines one tab deeper than their statement | 5,415 |
| Continuation lines two tabs deeper | 793 |
| Continuation lines at their statement's indentation, mostly a closing `)` or `]` | 548 |
| Continuation lines aligned with spaces after the tabs, 72 of them under the open bracket | 377 |
| Other continuation lines | 51 |
| Nested lines one tab deeper than their statement | 70 |

A rule that gave every continuation line one more tab than its statement would change 1,769 lines of Titzer's code. Continuation lines therefore keep their indentation.

## Rule bugs found and fixed

The corpora exposed the first ten in early versions of the prototype, and a review of this note and the ADR found the other four with constructed inputs that parse. Each is fixed in the prototype that gave the results above, and the implementation's tests should cover each one.

1. **Telling an `if` expression from a statement by a comma in its parentheses** misread type arguments: `if (eq<T, int>())` looks like `if(c, a, b)`. That happened 130 times in `vendor/virgil/test` and never in the other corpora. The prototype uses the parser's rule instead: an `if` that starts a statement is a statement, and any other `if` is an expression.
2. **Splitting operator member names.** `double.!=`, `float.!=`, `map.[]=`, and `Record.==` end in a single operator token; spacing the `=` in them would change the program. The implementation reads tokens from the parser, so it can't split them.
3. **Colons that aren't type annotations.** A packing's `): 8` and a constructor's `) : super(...)` are left alone; the colon rule applies only to a colon after a name.
4. **A layout's size**, `=16;`, isn't an assignment (90 lines), on a line of its own or after a field on the same line.
5. **Enum cases and match cases separated by commas** are block lines, not continuations, and so is a `}` after an enum's last case.
6. **A match case's `=>` at the start of a line** opens a body that is indented from that line, with the closing `}` aligned to the arrow, as `wasm/WasmGcLowering.v3:50` shows. A constructor's `super(...) {` on a continuation line opens a body indented from the constructor's first line, as `arm64/Arm64Backend.v3:53` and three other backends do.
7. **Alignment gaps before `=`, `=>`, and `->`** were collapsed at first.
8. **A comment inside parentheses or brackets**, such as one after a list's last element, belongs to the list and follows its continuation lines.
9. **`}` and `else` on separate lines** are joined only when the `}` starts its line; a one-line block keeps its `else` on the next line.
10. **Removing the space before a comma** broke that table in `apps/ZiVm/ZiVm.v3:119`, changing 20 lines; a single space can be part of an alignment, so spaces before a comma are left alone.
11. **Statement starts that tokens alone misread.** In `for (i = 0; if(c, a, b); if(c, i++, i--))`, the `if`s after `;` are expressions, and in a match case `1 => def g = fun() => if(c, a, b);`, the `if` after the second `=>` is one too. The prototype now ignores a `;` inside parentheses and an `=>` that follows another `=>`, `fun`, or `def` in the same case; the implementation reads each `if`'s kind from the parse tree instead.
12. **Overlapping edits.** A `}` with trailing spaces before an `else` on the next line got both a trim and a join over the same bytes, and a body's `{` was joined across a blank line, deleting it. Joins now apply only across exactly one line end with only whitespace around it. The proposed formatter combines rules into one edit per changed whitespace run, with separate edits on either side of a comment. The LSP reply merges the edits into one only above 10,000 edits.
13. **A tab before a layout field's colon**, as in `+0\twasm_func\t: i64;`, was still removed by the colon rule, in 13 lines of Wizard engine's `src/engine/x86-64/X86_64Stack.v3`. Like other alignment, it is kept now, and the counts above no longer include those lines.
14. **Enum cases without commas.** A case's comma is optional, so `B(2)` on the line after `A(1)` starts a case too.
