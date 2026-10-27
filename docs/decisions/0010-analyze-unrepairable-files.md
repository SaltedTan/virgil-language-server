# 0010. Analyze files that bounded repair can't fix

- Status: Accepted, with the conditions below; amends [ADR-0009](0009-current-text-repair.md)
- Date: 2026-10-27
- Discussion: [#300](https://github.com/SaltedTan/virgil-language-server/issues/300)

## Context

[ADR-0008](0008-method-body-fallback.md) and
[ADR-0009](0009-current-text-repair.md) keep semantic answers outside one
damaged method or constructor body: 116 of the
[tolerance benchmark](../benchmarks.md#semantics-during-syntax-errors)'s 185
primary states keep all four measured features warm and 110 cold, with no wrong
answers ([Current-text repair comparison](../benchmarks.md#current-text-repair-comparison)).
Elsewhere the whole project gets no semantic answer
([No error-tolerant parsing](../known-limitations.md#no-error-tolerant-parsing)).
The M6 research named a recovering parser and verifier as phase 3, which means
changing how Aeneas parses, while
[ADR-0002](0002-pinned-virgil-adapter-boundary.md) keeps compiler internals
behind the adapter and rejects copying them.

75 states stay unrecovered cold, and 69 warm: the cold set minus six that
fallback recovers. To count them, the corpus was regenerated, each current text
was checked against its SHA-256 in #293's records, and each broken file was
classified from its own text: its brace balance in ordinary code, the line of
the parser's first error against the line of the edit, and the reason the
production repair and eligibility functions refuse it.

| First blocker | Cold | Warm | Repair / replay |
| --- | ---: | ---: | --- |
| A control header's line lost its `{` (14 `if`, 48 `for`) | 62 | 62 | 2 / 60 |
| The next line continues an unfinished expression, so the parser's first error is past it | 6 | 0 | 2 / 4 |
| Truncated file | 2 | 2 | 2 / 0 |
| Field initializer | 2 | 2 | 2 / 0 |
| Errors in two bodies | 2 | 2 | 2 / 0 |
| Missing `}` at the end of a method | 1 | 1 | 1 / 0 |

No primary state has a damaged declaration header or CRLF; those appear only
among the controls, and the benchmark's "header" edit kind means control
headers. Nor is the corpus a sample of real editing: 60 of the 62 header states
replay two Aeneas lines one character at a time.

`Parser.v3` records an error and keeps going without resynchronizing or
building error nodes. A member that fails to start makes `parseMembers` drop
the component's members, and a failed top-level declaration ends `parseFile`.
The adapter verifies only clean parses, since partial trees trap the verifier
([row 14](../aeneas-crash-paths.md)).

Every semantic feature already suppresses the whole excluded body, braces
included. The declarations outside it, their types, and the bindings that
verification reaches there depend only on the file parsing with the same
declarations outside the body, not on what the body holds. Emptying a damaged
body serves them as well as repairing it, and guesses no syntax. One thing
outside still depends on the body: the 15-error limit is shared by the whole
program, so errors in the excluded body, a repair's sentinel today or an
emptied body's missing return, can use up the limit before verification
reaches later declarations, whose bindings and diagnostics are then missing,
and an excluded error suppresses the `ErrorLimit` notice
([error limit](../architecture.md#semantic-diagnostics)).

## Options

Option D was prototyped in scratch with a native probe that calls the
production parse, repair, and eligibility functions. Every accepted text was
then analyzed as a whole program: its diagnostics outside the excluded bodies
matched the predecessor program's, and none reached the 15-error limit. These
counts measure parsing, containment, and verification, not protocol answers,
though in #293 every eligible repair recovered all four features. Figures for
the other options are estimates.

| Option | Cold states recovered of 75 | Risk of a wrong answer outside the damage | Cost | Needs |
| --- | --- | --- | --- | --- |
| A. Recovery interface in Aeneas | Estimated 73 to 75 | Synchronizing by brace depth mis-ends a body that lost its `{`; needs D's layout check, or a refusal | Estimated 4 to 8 weeks, mostly verifier work | Upstream changes and a pin bump |
| B. Recovering parser in the adapter | Estimated 72 | As A; an inferred field type needs verifier changes, which B can't make | Estimated 3 to 5 weeks, and a re-port at each pin bump | An exception to ADR-0002 |
| C. Tree-sitter syntax fallback | None by itself | Locates damage by its own recovery guess, without a check | Estimated 2 to 4 weeks to evaluate | A license notice, grammar updates, a non-Virgil runtime |
| D. Further bounded text changes | Measured 71 | None found; brace rules need a layout check | Estimated 1 to 2 weeks | An amendment of ADR-0009 |
| E. Nothing more before v1.0 | 0 | None | None | Nothing |

**A. An opt-in recovery interface in Aeneas.** Behind an option that leaves
compilation unchanged, the parser would skip a failed declaration or member to
the next one, keeping what it parsed; quarantine a failing method body or field
initializer by recording its range and leaving it empty; and bound every
recovery loop. The larger part is the verifier: skipping quarantined bodies
without a missing-return error, and giving an inferred field whose initializer
is quarantined an error type that silences its dependents. Synchronizing by
brace depth mis-ends a body that lost its `{`, as a text repair does. Beyond
D's 71, A could recover the field initializers, and perhaps the truncated
files, though [ADR-0006](0006-incomplete-code-repair.md) measured 195 errors
for a truncated `Verifier.v3`, past the limit of 15.

**B. A recovering parser module in the adapter.** `AnalysisSingleFileParser`
already adapts parts of `Parser.parseFile` and `ParserState`
([notices](../../THIRD_PARTY_NOTICES.md#adapted-material)), but overriding
`ParserState` can't choose which parse function runs next. `parseMembers`,
`parseStmt`, and `parseList` are defs of one component that call one another,
so synchronizing means copying most of `Parser.v3`'s 1,644 lines. Without
verifier changes, B adds only the field with a declared type to D's 71. Each
copied function would record its upstream revision, each pin bump would diff
the upstream functions against it, and a test would require the copy to match
`Parser.parseFile` on every `.v3` file in the pinned checkout.

**C. A Tree-sitter syntax fallback.** A grammar gives syntax without bindings
or types, so alone it answers no semantic query. The one grammar the research
found, last changed in July 2024, declares ISC but ships no license file and
lacks the pinned grammar's layout, packing, and thread-component declarations.
Its runtime is C, and the server is a single Virgil executable
([ADR-0001](0001-virgil-native-server.md)).

**D. Further bounded text changes, amending ADR-0009.** Six variants were
measured:

| Blocker | Cold | (i) | (ii) | (iii) | (iv) | (v) | (vi) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Control header lost its `{` | 62 | 61 | 0 | 0 | 0 | 0 | 62 |
| Next-line continuation | 6 | 0 | 6 | 0 | 0 | 6 | 0 |
| Missing `}` | 1 | 0 | 0 | 1 | 0 | 0 | 0 |
| Errors in two bodies | 2 | 0 | 0 | 0 | 2 | 2 | 0 |
| Truncated file or field initializer | 4 | 0 | 0 | 0 | 0 | 0 | 0 |

- **(i)** inserts the missing `{` at the end of the header's line after
  completion's sentinel and closers, and a `for` header's missing clauses
  (`; virgilLspCursor; )`), without which only 34 states parse.
- **(ii)** tries a second site at the end of the line before the parser's
  error, for up to two more parses.
- **(iii)** inserts the missing `}` before the first later line no deeper than
  the block's opener.
- **(iv)** accepts ADR-0009's two sites when each is strictly inside its own
  body.
- **(v)** blanks the body that holds the parser's first error when the file's
  braces balance: it replaces the interior of the innermost method or
  constructor body holding the error with spaces, keeping line breaks, and
  reparses, for at most two bodies, each a body of the result's own parse. It
  moves no offset outside the body, and it also recovers all 95 states that
  ADR-0009's repair recovers. A method that returns a value gets a
  missing-return error at the body's own `{`, inside the excluded body. That
  error counts toward the 15-error limit, as a repair's sentinel does: with 14
  errors before it, it is the fifteenth, and verification skips a later
  component, whose bindings and diagnostics disappear.
- **(vi)** blanks the body that layout delimits when the braces are short by
  exactly one opener: the method header nearest above the first brace pair
  whose layout disagrees, through the next line at the header's indentation,
  which must start with `}`. The result must parse with that pair as a method
  body.

(ii) and (iv) keep ADR-0008's containment test; the brace rules can't. ADR-0009
rejected braces because "a brace can end a body early and move a later
declaration to another owner, which a single-interval test cannot rule out."
In a scratch case, `swallow-field`, an `if` loses its `{` in a method followed
by a field and a method. Inserting the `{` after the header makes the file
parse with the change strictly inside one body, and that body now owns the
field and the method. The text alone can't say which brace the user deleted.

A layout check can. Counting tabs as 8 columns and skipping blank and
comment-only lines, the excluded body must close on a line as deep as the line
where its header starts, with every line between deeper, and so must a pair
whose brace the rule inserts. As prototyped, the header is a single line that
begins with `def` or `new` after any modifiers and ends in `{`, and the closing
line begins with `}`; a header split over several lines is refused. An
implementation must state these forms, tab stops, continuation lines, nested
pairs, and what may follow the closing `}` on its line as precisely. If the
body passes, every other single-brace reading fails the check in it:

- A brace missing after the body would close the body at a deeper `}`.
- A brace missing before the body would close an earlier construct at a `}`
  inside the body, with the body's shallower header between them.
- Readings with the brace anywhere inside the body agree outside it.

The check trusts the file's indentation. That holds widely: 194 of the 198
pinned `aeneas/src` and `lib/util` files pass it as whole files, and only the
excluded body needs to. It refuses `swallow-field`, two closers at one
indentation, and a deleted method header, and accepts a legal `if` without
braces followed by the `while` that lost its `{`.

**E. Nothing more before v1.0.** Users keep 116 states warm and 110 cold.
While an open file has a missing brace, a damaged header or field initializer,
errors in two bodies, or an expression continued on the next line that neither
fallback nor the existing repair accepts, the project gets no definition,
hover, references, or type errors. The six continuation states keep their
answers warm, where fallback substitutes them.

## Decision

Phase 3a amends ADR-0009 with body quarantine, needing no upstream change and
no exception to ADR-0002. For an open file that fails to parse and that
fallback can't substitute:

1. When its braces balance and no lexical region is unterminated, blank the
   body that holds the parser's first error (D(v)). The method whose header the
   parser consumed gives one candidate per body; the prototype searched outward
   from the innermost pair instead. It replaces ADR-0009's insertions for
   whole-project analysis once it is measured (below); completion and
   signature help keep ADR-0006's cursor repair.
2. When they are short by exactly one opener, blank the body that layout
   delimits (D(vi)), under the layout check.
3. When they are short by exactly one closer, insert the `}` where layout ends
   the block (D(iii)), under the layout check, and exclude that body.

The substitution record holds a list of excluded bodies. Blanking moves no
offset, and only an inserted `}` shifts later ones. Every answer rule of
ADR-0008 and ADR-0009 stays, including the error limit's: an emptied body's
missing return counts toward it as a sentinel does. On this corpus, an
estimated 181 of 185 states would keep all four features, cold and warm: the
repair suite would go from 34 to 41 of 45 cold, and the replay suite from 76 to
all 140. That meets the research's phase-3 targets of 85% and 95%, but on the
corpus these rules were built from.

Phase 3b, declaration-level recovery of field initializers, damaged headers,
and truncated files, waits until phase 3a is measured on new states, and
prefers an upstream interface (A) to an adapter copy (B).

The maintainer decided, on 2026-10-27:

- **Upstream coordination:** not now. Phase 3a doesn't depend on it; option A's
  interface is reconsidered for phase 3b.
- **An exception to ADR-0002:** none. Only option B would need one.
- **Brace changes:** accepted, as a second step. Phase 3a ships in order: first
  rule 1, blanking a body whose braces balance; then rules 2 and 3, the brace
  rules under the layout check, only once negative controls (starting with
  `swallow-field`) and held-out states the rules weren't built from show no
  wrong answer. Without the brace rules, phase 3a recovers 8 states, not 71.
  60 of the 62 header states replay two Aeneas lines, so the corpus alone
  doesn't show the layout check holds on other code.
- **Replacing ADR-0009's insertions:** blanking replaces them once the
  benchmark shows it keeps every state they keep, with no wrong answer.
  ADR-0009's insertions stay until then.
- **The v1.0 gate for M6:** phase 3a shipped with no wrong answers, new negative
  controls for the brace rules and for the error limit, and held-out states the
  rules weren't built from, such as replays of other headers and indentation
  styles. Phase 3b is not needed for v1.0.

Later on 2026-10-27, after the brace rules were built and reviewed, the
maintainer decided two more things. Rule 3 is left out of phase 3a: three
reviews in a row found wrong readings, where indentation alone moved a
declaration to another owner, so a file one closer short is refused as any
other imbalance is, and rule 3 is redesigned in
[#320](https://github.com/SaltedTan/virgil-language-server/issues/320). Rule 2
ships trusting indentation, as this record states, under an explicit contract:
when every brace pair of the intended file agrees with its indentation, by the
layout check's own reading (tabs to the next multiple of 8 columns, blank and
comment-only lines skipped), rule 2 gives no wrong answer; when the file's own
indentation disagrees with its braces near the damage, a lost `{` can be read
wrongly, and answers outside the emptied body can point at the wrong
declaration. Every guard that rule 2 has stays, and no guard is added against
that second case, because one sound against it refuses every lost `{` that the
benchmark's primary states recover.

## Consequences

A cold open with a missing brace, an expression continued on the next line, or
errors in two bodies keeps answers outside the damaged body. Parseable text is
analyzed as before. Blanking reparses once per body, at most twice, as
ADR-0009's repair does, and the layout check reads only the excluded body's
lines. The whole damaged body still gets no semantic answer, and a file whose
indentation disagrees with its braces near the damage is refused, as today.
Field initializers, damaged declaration headers, and truncated files keep the
empty semantic result until phase 3b.

The benchmark needs negative controls for the brace rules, starting with
`swallow-field`, two closers at one indentation, a deleted method header, and
an unterminated string inside the body, a control where an emptied body's
missing return is the fifteenth error, and held-out states. The
`repair-in-two-bodies` control would expect two excluded bodies instead of a
refusal. [Architecture](../architecture.md#current-text-repair),
[known limitations](../known-limitations.md#no-error-tolerant-parsing), and
[ROADMAP M6](../../ROADMAP.md#m6--error-tolerance-and-incrementality-48-weeks-evidence-driven)
change with the implementation.

## Alternatives considered

- **Insertions (D(i), (ii), and (iv)).** They recover 69 of the 70 states that
  blanking does, but guess syntax and cost more parses for nothing the excluded
  body needs.
- **Brace insertion under ADR-0009's test alone.** It accepts `swallow-field`.
- **A recovering parser now (A or B).** It adds at most four states here for
  weeks of compiler work and upstream timing or a fork.
- **Tree-sitter (C)** answers no semantic query; **nothing more (E)** keeps the
  blackout for common typing states.
