# 0006. Semantic context for completion in incomplete code

- Status: Proposed. The maintainer decides after reading the results below; until then, this record holds a recommendation, not a decision.
- Date: 2026-10-21
- Discussion: [#128](https://github.com/SaltedTan/virgil-language-server/issues/128), for [#129](https://github.com/SaltedTan/virgil-language-server/issues/129) and [#130](https://github.com/SaltedTan/virgil-language-server/issues/130)

## Context

Completion is asked for while code is incomplete. [Keyword completion](../architecture.md#completion) needs only the text before the cursor, but in-scope declarations (#129) need the scope at the cursor, and members after `.` (#130) need the receiver's type, which only verification knows. Verification runs only on a program that parses: while any file of a project fails to parse, its snapshot has no semantic data ([Semantic diagnostics](../architecture.md#semantic-diagnostics)). A user who has typed `x.` or `f(a,` has a program that doesn't parse, so the ordinary pipeline has nothing for #129 and #130 to use. [ROADMAP](../../ROADMAP.md) M5's exit criterion asks that completion stay useful "with a missing identifier or delimiter near the cursor".

The maintainer chose to measure a narrow repair first (#128, 2026-10-21) and to settle the boundary between M5 and M6 afterwards. The question for this record: which incomplete-code cases must M5 support semantically, and with what: the narrow repair measured here, a part of M6's error-tolerant parsing brought forward, or the last snapshot that parsed?

## The spike

The development `repair` command ([Running](../development.md#running), [Repair spike](../architecture.md#repair-spike)) takes a program's files and a cursor and tries these repairs, each one insertion at the cursor, in order, skipping one that inserts the same text as an earlier one: nothing (`unchanged`); a sentinel identifier, `virgilLspCursor` (`sentinel`); the closers of the parentheses and brackets open in the innermost block (`close`); both; each with `;`; a `}` for the innermost block; and the sentinel, closers, `;`, and a `}` for the innermost block or for every open block. A repair is named by what it inserts, so `sentinel+semicolon` inserts `virgilLspCursor;`. The cursor scanner of [keyword completion](../architecture.md#completion) finds the open delimiters. Each repaired program is analyzed in the worker, through the CLI's own supervisor, so nothing is published and no context's snapshot changes. A repair is:

- **usable** if the program parsed and verification resolved the cursor's position: after `.`, it reported the sentinel as an `UnresolvedMember` of a type, which is the receiver's type that #130 needs; elsewhere, an `UnresolvedIdentifier` or `UnresolvedType` at the sentinel, which shows that verification looked the name up in the scope at the cursor, which #129 needs; or a typed prefix resolved;
- **verified** if the program parsed and has no diagnostic but the sentinel's.

A usable repair can leave other errors, such as a type error elsewhere or `not a statement` for the `p.virgilLspCursor;` that a member access at the end of a statement becomes; they don't hide the context.

## Cases

`scripts/repair-corpus.py` runs 45 typing states ([Running](../development.md#running)):

- 31 fixtures in `test/fixtures/repair/cases/`, each a whole program with the cursor marked: `x.` at the end of a statement, mid-expression, with a prefix, on a call's result, on `this`, on a type, in a condition, in a lambda, in a match case, in a class field initializer, and in a component field; `f(a,`, `f(`, `f(a,)`, a nested call missing its `)`, a generic call `f<T>(`, and a call after a string and a comment holding `(`; `a[`; an unfinished `if (`; `var y =`; a missing `;`; a missing `}` at the end of a method; `return`; `else`; a keyword's prefix; a local's type; and `x.` with a syntax error elsewhere in the file, after the cursor, and in another file of the program, and with a type error elsewhere in the file and in another file.
- 14 states from the Aeneas sources (`test/fixtures/repair/aeneas-sites.txt`), analyzed with the whole Aeneas program (205 files and their dependencies): real member sites on a component, a class, a field, a parameter, and a chain; real calls at their second, a later, and their first argument, and a nested call; a condition, a for loop's header, and a statement's start, each with the rest of the line deleted at the cursor; and a member site and a call with the rest of the file deleted.

## Results

Measured on `763d2f9` with this change, on an Intel Core i7-14700KF.

| Typing states | Usable | Verified |
| --- | ---: | ---: |
| All | 39 of 45 (87%) | 28 of 45 |
| After `.` | 19 of 23 | |
| Elsewhere | 20 of 22 | |
| Fixtures | 28 of 31 | |
| Aeneas sources | 11 of 14 | |

The first usable repair of each state, in the ladder's order:

| First usable repair | States |
| --- | ---: |
| `unchanged` (a typed prefix that already parses, `p.no`) | 1 |
| `sentinel` (inside a complete expression or argument list) | 2 |
| `semicolon` (a statement missing only its `;`) | 2 |
| `sentinel+semicolon` | 19 |
| `sentinel+close` | 3 |
| `sentinel+close+semicolon` | 10 |
| `sentinel+semicolon+brace` (a method missing its `}`) | 1 |
| `sentinel+semicolon+all-braces` (the rest of the file deleted) | 1 |
| None | 6 |

One fixed rule, the sentinel, the closers of the innermost block's open delimiters, and `;`, which `sentinel+semicolon` is where no delimiter is open, is usable in 35 of the 45 states. The ladder adds 4: a sentinel alone inside a complete expression (`p.<|> + 1`, `f(a,<|>)`), a method's missing `}`, and a truncated file.

No repair at the cursor helps the 6 others, because each has a syntax error away from it:

- a syntax error elsewhere in the file, after the cursor, or in another file of the program (3 fixtures): Aeneas stops at a file's first syntax error, and a whole program verifies only if every file parses;
- an Aeneas condition and a for loop's header whose line, cut at the cursor, lost the `{` that opened their body, whose `}` remains below;
- an Aeneas call whose file was cut at the cursor: closing every block parses, but the deleted rest of `Verifier.v3` declared classes that other files use, and verification stops at its error limit before it reaches the sentinel.

**Latency.** A repaired analysis of the Aeneas sources costs what an ordinary analysis costs. Each figure is the wall-clock time of one analysis through the supervisor, from submission to the decoded snapshot, with occurrences, in one process whose worker is reused; `repair --runs=10` alternates 10 analyses of the first usable repair with 10 of the files as given, for each of the 11 usable Aeneas states:

| Worker heap | Repaired, median of each state | Original, median of each state | p90 of each state |
| --- | --- | --- | --- |
| 1 GB (`WORKER_HEAP=1g`) | 304.9–319.0 ms | 306.2–323.7 ms | 312–343 ms |
| 384 MB, the default | 300.7–312.5 ms, or 448.0–452.5 ms | 448.1–475.8 ms | 302–542 ms |

With the default heap, one of each pair of analyses collects the worker's heap, which adds about 150 ms here, as [ADR-0004](0004-analysis-worker-process.md#measurements) describes for a worker that collects; which of the two collects depends on the phase, not on the repair. With a 1 GB heap, which never collects here, the two are within 2% of each other. The ranges leave out the truncated member site, whose shorter program analyzes in 233 ms. An attempt that fails to parse costs about 70 ms, the parse alone, so trying the ladder in order costs a few of those before the usable one. A small program, such as the [measured](../architecture.md#semantic-diagnostics) two-file fixture, analyzes in under a millisecond.

## Options

1. **Narrow repair, as measured.** For a completion request in a document whose project fails to parse, analyze the captured inputs once more with one repair at the cursor, as a job like [rename's preflight](../architecture.md#rename), and read the scope or the receiver's type at the sentinel. The scanner chooses the repair from the context: the sentinel, the open delimiters' closers, and `;` where the cursor ends its statement, the sentinel alone inside a complete expression. #129 and #130 then need the worker to report what the verifier knows at the sentinel, the names in scope or the receiver type's members, which the snapshot does not hold today.
2. **Bring part of M6's error-tolerant parsing forward.** An Aeneas `EDITOR` parser mode that synchronizes at top-level, member, statement, and delimiter boundaries ([ROADMAP](../../ROADMAP.md) M6), so that a file with syntax errors still yields declarations and bodies to verify. It is the only option that also helps the six states above and gives semantic results everywhere in a file being edited, not only at the cursor.
3. **Keep the last good snapshot.** Answer #129 and #130 from the context's last snapshot that parsed, mapping the cursor into its text through the edits since. It needs no analysis per request, but it has no scope for code typed since, and it needs the edit-mapping policy that [Definition](../architecture.md#definition) leaves out of scope.

## Costs

- **Narrow repair.** One analysis per completion request while the program fails to parse, at the cost of an ordinary analysis: about 300 ms for the Aeneas sources, 450 ms when the worker collects, and under a millisecond for small programs. It shares the worker with automatic analyses and rename preflights, so a request can wait for one; a client typing quickly would need the newest request to replace older ones, and its result to be reused for the same document version. No compiler change. It fails whenever a syntax error is away from the cursor, including in another file.
- **Error-tolerant parsing.** A parser mode in the pinned compiler, or a fork of its parser in the adapter, against [ADR-0002](0002-pinned-virgil-adapter-boundary.md)'s boundary, kept in step with every Virgil update. The verifier would run on trees with synthetic nodes, which it has never seen and which can reach the [known trap paths](../aeneas-crash-paths.md) and new ones. Weeks of work, which ROADMAP gives to M6.
- **Last good snapshot.** An edit-mapping policy, and results that are stale in exactly the lines being typed. Each context keeps its last snapshot within the [snapshot budget](../architecture.md#analysis-snapshots-partly-present), but every completed analysis replaces it, including one that fails to parse; keeping the last one that parsed would change that.

## Recommendation

Use the narrow repair for M5's semantic completion, with the repair chosen from the cursor context rather than a ladder, and leave error-tolerant parsing in M6. It makes 87% of the measured typing states usable, 35 of 45 with one fixed rule, gives #129 the scope at the cursor and #130 the receiver's type, needs no change to Aeneas, and costs one ordinary analysis. Every state it cannot repair has a syntax error away from the cursor; if those matter for M5, falling back to the last good snapshot covers them more cheaply than an `EDITOR` parser mode, and counting how often a context keeps failing to parse would show how much they matter before M6 decides on error tolerance.

## Consequences

To be written when the maintainer decides. If the recommendation is accepted: #129 and #130 add worker queries for the scope and the members at the sentinel, a completion job that takes turns with automatic analyses as rename's preflight does, and replacement of superseded requests; keyword completion is unchanged; and the M6 item "Optional Aeneas `EDITOR` parser mode" stays where it is.
