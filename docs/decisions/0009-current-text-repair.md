# 0009. Analyze a broken open file from a bounded repair of its current text

- Status: Accepted; amends [ADR-0006](0006-incomplete-code-repair.md) and [ADR-0008](0008-method-body-fallback.md)
- Date: 2026-10-26
- Discussion: [#293](https://github.com/SaltedTan/virgil-language-server/issues/293)

## Context

[ADR-0008](0008-method-body-fallback.md) substitutes a file's last parseable
text when a syntax error lies strictly inside one method or constructor body.
That needs cached text, so it helps only warm edits. In the
[fallback comparison](../benchmarks.md#method-body-fallback-comparison), 116 of
185 states kept all four measured features (definition, hover, references, and
semantic diagnostics) in warm sessions but only 15 of 185 in cold ones, which
open a file that already has the error or edit one whose cached text was
evicted. The 97 body states that fallback recovers warm lost every semantic
answer cold.

[ADR-0006](0006-incomplete-code-repair.md) chose a narrow repair at the cursor
for completion, and later signature help, and left whole-project error
tolerance to M6. Its repairs, a missing name and the closers and `;` that the
cursor's context needs, are chosen from the cursor scanner's context rather than
tried as a ladder. Whole-project analysis never used them.

This is phase 2 of M6's error tolerance. Phase 3, a recovering parser and
verifier, would handle what a bounded text repair can't.

## Decision

Analyze an open document that fails to parse, and that fallback cannot
substitute, from a bounded repair of its current text. Fallback runs first; a
file it substitutes is never repaired. Closed files are never repaired.

Each repair site uses completion's repair (ADR-0006), computed at the end of the
token before the parser's first error: the sentinel where a member name, an
operand, an initializer, a type, or a pattern is missing, which also serves as
the placeholder expression for a missing initializer or operand, then the
closing `)`, `]`, and `>` that the innermost block's open constructs lack, and a
`;` where the statement ends. Each site has one candidate. Repair at most two
sites in a file, reparsing the file once after each; a text that still fails
stays unrepaired. There is no search, no ladder, and no retried analysis, and
the worker still gets one effective program per analysis.

Containment uses ADR-0008's test unchanged. The repaired text's own parse gives
its method and constructor bodies, and `LspBodyEligibility.compare(repaired,
current, repairedBodies)` must accept the pair: one changed interval, strictly
inside one body of the repaired text, in ordinary code in both texts, at the
same brace depth at both ends. A repair never inserts or removes a brace. A
missing `}`, a declaration header, a field initializer, and sites in two bodies
stay unrepaired until the recovering parser of phase 3.

A repaired file is a substituted file. Its substitution record holds the
repaired bytes as effective text and ADR-0008's map of the single interval and
the excluded body; the record needs no new field. Every rule of ADR-0008 then
applies: the bytes are charged to the snapshot budget, every semantic feature
suppresses the whole excluded body, rename, prepare rename, and the preflight
refuse the snapshot, workspace symbols skip the file, completion, signature
help, and document symbols keep their current-text paths, and current-text
syntax diagnostics are always published. Since the sentinel and the closers lie
in the excluded body, no answer contains a synthetic token's location. Repaired
text is never cached as parseable text.

Repair starts at the parser's error, not at the document's last edit. On the
tolerance corpus the error repaired 91 of the 97 warm-recovered body states and
the last edit would have repaired all 97, but those six are states that fallback
recovers warm, while cold opens, which repair is for, have no known edit. On the
states that fallback can't substitute, both points repaired the same states.
With full document sync, tracking the edit would mean comparing every change's
old and new text.

The capture parses each broken file at most three times: a current-text parse if
the syntax diagnostics' outcome can't be reused, fallback's predecessor, and the
repaired texts share the bound, under the existing parser guards. Repair adds no
worker message, wire version, or analysis.

## Consequences

Cold sessions and evicted cache entries now keep answers outside a body with a
contained, small syntax error: 91 of the 97 body states that fallback recovers
warm also recover cold
([Current-text repair comparison](../benchmarks.md#current-text-repair-comparison)).
The tolerance benchmark has no wrong answers, and five new negative controls
show that a site needing a brace, a header or field-initializer site, and sites
in two bodies are refused, and that a repaired call's arity and type stay inside
the excluded body. Analyses and requests on parseable text are unchanged.

What stays unrepaired gets the empty semantic result that ADR-0008 left it:
closed broken files, missing braces, headers, field initializers, sites in two
bodies, and errors that need more than two insertions or that the parser first
reports past the incomplete code, as when the next line continues an unfinished
expression. The sentinel's unresolved name is an error of the effective program,
so it counts toward the 15-error limit, which can leave later diagnostics out
of a set. A repaired analysis costs what a fallback analysis costs.

This amends ADR-0006's boundary, which left whole-project error tolerance to
M6, and ADR-0008's, which needed cached text. Neither the cursor repair of
completion and signature help nor fallback's rules change.

## Alternatives considered

- **Repair at the last edit.** It repaired the six warm-recovered states that
  the parser's error misses, but cold opens have no known edit, and full sync
  would need a text comparison for every change. On the states that need repair,
  it repaired none that the parser's error didn't.
- **Brace repairs.** Completion's ADR-0006 ladder also tried a `}` for the
  innermost or every open block. A brace can end a body early and move a later
  declaration to another owner, which a single-interval test cannot rule out.
- **A ladder of candidates, or more sites.** Trying several repairs, or repairing
  until the file parses, costs more parses and analyses and guesses more syntax,
  and each guess risks a structure that the user didn't write.
- **Repairing outside bodies.** A header or field-initializer repair changes a
  declaration's type or arity, which other files see; no excluded body could
  hide it.
- **A recovering parser and verifier** (phase 3) is the general path, with a
  larger contract for malformed nodes and error propagation.
