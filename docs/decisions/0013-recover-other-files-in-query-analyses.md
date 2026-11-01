# 0013. Recover other open files in completion and signature-help analyses

- Status: Accepted; amends [ADR-0006](0006-incomplete-code-repair.md), [ADR-0008](0008-method-body-fallback.md), and [ADR-0009](0009-current-text-repair.md)
- Date: 2026-11-01
- Discussion: [#346](https://github.com/SaltedTan/virgil-language-server/issues/346)

## Context

[ADR-0008](0008-method-body-fallback.md),
[ADR-0009](0009-current-text-repair.md), and
[ADR-0010](0010-analyze-unrepairable-files.md) keep a project's semantic
answers while an open file has a syntax error inside a method or constructor
body. Its automatic analysis substitutes that file: the file's last parseable
text when the edit is contained in one body, otherwise its current text with
the damaged body emptied.

Completion and signature help for a call that is still being typed don't read
the project's analysis. Each request runs an analysis of its own, a temporary
one, of the text repaired at the cursor
([ADR-0006](0006-incomplete-code-repair.md)). ADR-0008 and ADR-0009 left those
requests on their current-text cursor-repair paths, so the temporary analysis
captured every other file as it was typed. One other open file with a damaged
body then made the whole program fail to parse there:

- completion got the keywords alone, or after a `.` no members;
- signature help for an incomplete call got null;
- hover and definition outside the damaged body kept working.

ADR-0006 recorded the same failure for its narrow repair: "It fails whenever a
syntax error is away from the cursor, including in another file". Two-file
regressions reproduce it: completion at a name and after `Other.`, and
signature help in an incomplete call, while another open file has a syntax error
in a method body that both fallback and repair accept.

## Decision

The maintainer approved this on 2026-11-01.

**Other open files.** The temporary analysis of a completion, of names or
members, and of an incomplete call's signature help substitutes the project's
other open files as the automatic analysis does. Its capture applies ADR-0008's
fallback, then the current-text repair of ADR-0009 and ADR-0010, to every open
source but the requesting document. Both keep their eligibility, their parse
budgets, and the source-size budget unchanged. Closed files are still never
substituted.

**The requesting document.** It keeps its one repair at the cursor, applied
once. It is never substituted from its own last parseable text, nor repaired
again, so no answer comes from a cached version of the body being typed.

**What stays the same.**

- Automatic and project-context development analyses keep substituting every open file.
- Each request still runs one temporary analysis, with the single stale-generation
  rerun that [Names](../architecture.md#names) describes.
- A query result publishes no diagnostics and replaces no snapshot.
- Nothing a temporary analysis captures is cached as parseable text, so neither
  cursor-repaired nor substituted text can seed the cache.
- [#327](https://github.com/SaltedTan/virgil-language-server/issues/327)'s reply
  contract holds. A failed, replaced, or stale analysis gets the incomplete
  keyword fallback, and a completed analysis without an answer gets the
  complete one. Once a sibling is recovered, the reply is the semantic list with
  its usual truncation flag.

**Charges.** Admission charges each substitution's retained bytes,
`AnalysisSubstitution.bytes`, as it does for an automatic analysis. A
substitution that takes the sources past the 4 MiB source budget fails the
capture before admission, and the request gets the incomplete fallback that
any failed capture gets. The request keeps that provenance only until it is
answered.

**Answers.** A completion item or a signature carries labels, kinds, details,
and signatures, never a location inside a substituted file. Items declared in a
substituted file get no `completionItem/resolve` data, as before, since their
offsets aren't those of the file's current text. A local of an excluded body
can't be a candidate: it is in scope only in its own method, in another file.

## Consequences

While another open file has a syntax error that fallback or repair accepts,
completion keeps its names and members and an incomplete call keeps its
signature, warm and cold. The [benchmark](../benchmarks.md#completion-beside-a-broken-file-2026-11-01)
ran completions in Aeneas's `core/Program.v3` beside `vst/Verifier.v3`
(106 KB), intact or with a syntax error in a method body, in two runs:

- With the file intact, the median completion took 133 to 136 ms, before and
  after the change.
- With the error, it took 134 to 137 ms warm and 137 to 140 ms cold, and every
  completion got names. Before the change, every one got the keywords alone.
- The capture's substitution took 0.5 ms with the file intact, 5.2 ms warm, and
  11.7 ms cold: one parse of the cached text, or of the blanked text.

What stays unrecovered fails as before:

- a closed file with a syntax error;
- an error in a declaration header or a field initializer;
- an unterminated string, byte literal, or block comment;
- braces that the brace rules don't place;
- a second syntax error in the requesting document, which its cursor repair
  doesn't mend.

An analysis that completes without an answer still gets the complete fallback.
An excluded body's errors still count toward the 15-error limit, such as an
emptied body's missing return. Verification can then stop before the cursor's
member, which gets the complete fallback too.

Each request pays what the automatic capture pays for each broken open file: at
most three parses of it, besides brace rule 3's places. An intact file costs a
lookup of its last parse.

## Alternatives considered

- **Keep the exclusion.** Every damaged body in another open file would keep
  taking completion and signature help away while hover and definition work.
  That is the common case of editing several files at once.
- **Substitute the requesting document too.** Its last parseable text can hold
  locals and statements that the user has since changed, and its scope at the
  cursor would come from that cached body. Emptying its damaged body would empty
  the scope at the cursor. Its repair at the cursor is what gives the answer.
- **Answer from the project's last snapshot.** ADR-0006 rejected it: the
  snapshot is stale in exactly the lines being typed, and the server has no
  edit-mapping policy.
- **Recover closed files, headers, or field initializers.** No analysis
  recovers them today. That is phase 3b of ADR-0010, not this decision.
