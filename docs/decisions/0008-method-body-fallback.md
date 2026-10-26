# 0008. Reanalyze last-parseable text for contained method-body edits

- Status: Accepted
- Date: 2026-10-25
- Discussion: [#243](https://github.com/SaltedTan/virgil-language-server/issues/243)

## Context

Aeneas verifies only a program whose files all parse. A syntax error in one
method therefore removes semantic answers throughout the project. The
[protocol benchmark](../benchmarks.md#semantics-during-syntax-errors) measures
that loss. ADR-0006 left fallback to the last snapshot that parsed in M6;
this decision amends that boundary with a narrower text fallback. It does
not reuse an old semantic snapshot.

The error-tolerance research considered lexical answers, last-parseable text,
bounded repairs, a recovering parser and verifier, and a second syntax engine.
It recommended starting with bounded text substitution and conservative
read-only answers, while retaining the worker's isolation and resource limits.

## Decision

Keep one last-parseable source text per file and project context, from a
completed analysis that analyzed the file as itself. Semantic errors do not
disqualify it. The cache uses one global LRU under 4 MiB, including a conservative
512-byte allowance per entry and its URI bytes. Remove entries when their
context retires or their file leaves its project. Eviction removes fallback
availability. There is no disk cache.

At capture, reuse the open overlay's parse outcome with its identity and language
options. If it has a syntax error, compare its current and cached bytes using
one common prefix and suffix. The cached interval must be strictly inside
one cached method or constructor's block body. Find those braces with the
server's parse and consumed-token index; declaration-site ranges cover names
only. Use keyword completion's existing cursor scanner for lexical state and
raw brace balance. The interval must start and end in ordinary code in both
texts, never drop below its starting brace depth, and finish at that depth.
Unterminated strings, byte literals, and block comments are ineligible even
when the cursor scanner recovers to ordinary code before the interval ends.

The byte-interval test alone was unsafe: an insertion could close a method and
its component and open another, changing an unchanged later declaration's owner.
The lexical and brace checks ensure the common suffix has the same lexical state
and open braces in both texts. Virgil's method headers fix externally visible
types; locals and nested scopes remain in the excluded body. Field initializers,
headers, whole-file truncation, and changes spanning bodies do not qualify.

Send one effective program to the existing worker, substituting eligible cached
texts only for open documents. No retry or repair ladder is added. The completed
result replaces its context's snapshot as usual. For each substitution, retain
the effective bytes, both changed intervals, and the excluded current body;
reserve and charge those bytes and metadata to the existing 32 MiB snapshot
budget. Cache eviction cannot remove a retained snapshot's effective bytes.
Its document stamp remains the current overlay identity compared at capture.
Configuration, membership, admission, and freshness checks remain in force.

Definition, type definition, implementation, hover, references, highlights,
and semantic diagnostics suppress queries and locations in the entire substituted body, including its braces.
Map other offsets before converting current bytes to UTF-16: offsets before
the interval stay fixed; offsets after it shift by the length difference.
Ranges spanning an excluded body are unmappable by the shared range helper;
see [type hierarchy](../architecture.md#type-hierarchy) for declaration extents.
Keep current-text syntax diagnostics. Reference and highlight lists can be partial; diagnostic sets
can omit errors in the excluded body. Prepare rename, rename, and its preflight
refuse a snapshot with any substitution. Completion and signature help retain
their current-text cursor-repair paths and do not consume degraded semantics.
Document symbols retain their current-text syntactic path. Workspace symbols
skip an analysis's substituted file entirely, trying that file's next analysis
or omitting it; other files in the degraded snapshot still contribute symbols.

## Consequences

Warm edits inside a contained body can preserve unaffected answers elsewhere
in that file and in other files. A later full-text change invalidates the
snapshot as before. Files without cached parseable text, closed broken files,
unbalanced braces, unterminated lexical regions, and edits outside one body
retain the existing empty semantic result when parsing fails. Eligibility uses the enclosing
common-prefix/suffix interval; history is not tracked.

This is an initial M6 improvement, not completion of error tolerance. A
recovering parser/verifier or bounded multi-site current-text repair is still
needed for cold and broader edits. The compiler adapter's parse gate and worker
protocol are unchanged. Tests cover boundary escapes, offset mapping, cache
and snapshot bounds, filtering, freshness, and edit refusal. The benchmark
reports exact, partial, missing, and wrong answers separately; a foreign
location or diagnostic remains wrong.

## Alternatives considered

- Reusing the last successful semantic snapshot avoids analysis but retains
  stale bindings and cannot describe current intact files' new errors.
- Unrestricted text substitution restores more answers but gives wrong
  bindings after signature, declaration, owner, and shadowing changes.
- A repair ladder adds analyses and guesses syntax beyond this milestone.
- Recovering Aeneas syntax and verification is the broader M6 path, with a
  larger contract for malformed nodes and error propagation.
- A second parser could supply structure but cannot replace Virgil's type
  semantics and adds grammar maintenance.
