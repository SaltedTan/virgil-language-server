# 0011. Record declaration comment spans in the analysis result

- Status: Accepted
- Date: 2026-11-01
- Discussion: [#415](https://github.com/SaltedTan/virgil-language-server/issues/415)

## Context

[Hover](../architecture.md#hover) and
[completion item documentation](../architecture.md#item-documentation) show a
declaration's leading comment ([#357](https://github.com/SaltedTan/virgil-language-server/issues/357),
[#358](https://github.com/SaltedTan/virgil-language-server/issues/358)). Until
this decision, the server found that comment by parsing the declaration's whole
file again, building its outline, and looking the declaration up there. It did
so for every hover whose target was of a kind that can be documented, whether
or not the target had a comment, in any file of up to 256 KiB.

[Hover (2026-11-01)](../benchmarks.md#hover-2026-11-01) measured the cost on
the reference host. A hover at a declaration in Aeneas's `Verifier.v3`
allocated 5,338,872 bytes, of which the comment lookup took 5,269,976 (98.7%).
It also took about 2.2 of the request's 2.7 ms. Of that allocation, 3.7 MB was
the pinned parser's own syntax tree, tokens, and names, which no change on the
server's side of the parse can remove. A hover at a local, which can't be
documented, allocated 68,952 bytes. In every measured round of 55 hovers, the
server's heap filled during one of those parses, and that hover took 16 to
21 ms instead of under 3 ms.

The worker already parses every file of a program. It builds that outline only
for an analysis with a scope query, and it sent no comment.

[#361](https://github.com/SaltedTan/virgil-language-server/issues/361) listed
"wire/schema expansion for every declaration" as a non-goal. It resolves
workspace symbols' containers by parsing result files in the server, within a
512 KiB budget per request.
[#386](https://github.com/SaltedTan/virgil-language-server/issues/386) proposes
recording each declaration's container in the analysis result instead, and it
asks the maintainer to decide whether the wire and the snapshot may grow for
that.

## Decision

The worker records each declaration's leading comment as it parses, and the
analysis result carries the comment as a span of the file's text. The
maintainer chose this on 2026-11-01, as #415's first option. It overrides
#361's non-goal for comments only.

- **Same rule, same text.** As an analysis parses each file
  (`AeneasAdapter.analyzeProgram`), `AnalysisDeclarationComments` walks the
  file's outline without building symbols, and uses `DocumentCommentCollector`
  to find each listed declaration's comment, by the rule that the server
  applied before. The analysis parses the bytes that hover and resolve read
  later, with the project's options. The parser reads a carriage return
  outside a comment or literal as an error, but the outline reads one as a
  line end, as the server's own parse did after normalizing line ends. So a
  file that holds one is parsed again alone, normalized, for its comments. A
  file over 256 KiB still documents nothing, and so does a substituted file.
  No comment is recorded once a file fails to parse, because the result then
  has no declarations.
- **Sparse on the wire.** Protocol version 17 sets `DOCUMENTED` (0x40) in the
  kind byte of a declaration that has a comment. Two signed LEB128 integers
  follow its parent: the bytes from the comment's end to the start of the
  declaration's name, and the comment's length. Declarations without a comment
  are sent as before.
- **Checked when decoded.** The decoder rejects a span that doesn't lie before
  the name, a span on a kind that the outline doesn't list, and a span on a
  declaration without a source offset. It also rejects any declaration with an
  offset past the end of its file, whose length the supervisor gives from the
  request's sources, so the span before the name lies in the file too.
- **Sparse in the snapshot.** A documented declaration is a subclass of the
  snapshot's declaration with two ints (`AnalysisDocumentedDeclaration`), as a
  flagged declaration is a subclass with none. The existing conservative
  128-byte declaration charge covers the two ints, so no budget changes.
- **Rendering stays in the server.** Hover and resolve read the lines at the
  span from the analyzed text, which they already read and check for the
  answer's positions, and render them as before: the 4 KiB cut, U+FFFD for
  invalid UTF-8, and Markdown prose. They parse nothing. Resolve keeps each
  item's span instead of its declaration's kind and name end, and keeps its
  fingerprint, length, and hash checks. An item without a comment reads no
  file.

## Consequences

Measured as [Hover comment spans (2026-11-01)](../benchmarks.md#hover-comment-spans-2026-11-01)
records, a hover at a declaration in `Verifier.v3` allocates 68,872 bytes
instead of 5,338,872, and takes 0.446 ms instead of 2.620 ms (median), about
what a hover at a local allocates and takes: 68,952 bytes and 0.426 ms. A
hover at a class in the closed `Vst.v3` allocates 111,104 bytes instead of
1,805,632. In the same rounds, the server collected during 6 measured hovers
instead of 36.

The worker pays instead, once per analysis:

- **RESULT size.** The Aeneas sources' RESULT grows by 5,594 bytes, about 2.5
  bytes for each of the 2,235 documented declarations.
- **Allocation.** An Aeneas analysis allocates 909,312 bytes more, and
  926,680 more with the benchmark's semantic error and 900 extra functions.
  A util analysis allocates 121,680 bytes more. A completion's analysis,
  which already built an index for every file, allocates 17.5 MB less,
  because the index is now packed.
- **Time.** The worker's parse of the Aeneas sources takes 6.3 to 6.8 ms
  more when its result is reused and 9.3 ms more when it is fresh, and
  encoding the result takes 1.0 to 3.5 ms more. An analysis's latency rose
  by 4 to 11 ms, at most 3%. For `lib/util`, the parse takes 0.5 to 0.6 ms
  more. The worker's collection after it sends a result also took 3 to 7 ms
  longer, but it did in a copy that records no comments too, so these runs
  don't attribute that to recording them.
- **Heap room.** At the end of handling, the worker's heap has 9,080,792
  bytes of room under its collection boundary for `error_analysis` and
  9,841,432 for `reused_analysis`, down from 10,007,472 and 10,750,744. No
  scenario collects while handling a request.

For an analysis without a scope query, the worker now builds the syntax index
of every file that it records comments for. It reuses one index for the whole
program, which holds four ints for each token and makes room once for the
largest file. That depends on the index being packed, which this change also
made. It kept an object for each token: parsing each Aeneas file alone with
it allocated 34.3 MB, against 16.4 MB with the packed index. The difference,
about 17.9 MB, is more than the worker's whole room under its collection
boundary given above, which is why the index was packed.

Any later field added to every declaration must fit in the 128-byte charge too,
or raise it and the budgets that count it. [#386](https://github.com/SaltedTan/virgil-language-server/issues/386)
is still its own decision, about a field that every declaration would have.
This decision's pattern is a flag bit in the kind byte, as wire version 15
added, with values after the declaration only where the bit is set. A
container for each declaration would be a value on most declarations, not on
a few, and the worker's heap room is the cost to measure first.

## Alternatives considered

- **A cache in the server.** The server could keep each file's comment spans
  after the first lookup, keyed by its analyzed bytes. The first hover on each
  new text would still parse the whole file, 5.3 MB on `Verifier.v3`, and
  every edit makes new text. The cache would also hold memory between requests
  and need its own budget and eviction, beside the snapshots'. The worker
  already parses every file once per analysis.
- **The comment's text in the result.** This would send 176,422 bytes of
  comment lines for the Aeneas sources' 2,235 documented declarations, plus
  their strings in the snapshot, against 5,594 bytes of spans. The server
  reads the analyzed text anyway to map the answer's positions.
- **The declaration's first token and the end of the token before it.** This
  was #415's other form of the first option. The server would still apply the
  backward scan, the header rule, and the bounds at each request. Spans carry
  the finished answer, in fewer bytes.
- **Shrinking the server's parse.** Packing the server's index alone would
  save about 0.7 of the 5.3 MB, but the parser's 3.7 MB would stay. A textual
  check that skipped the parse when no comment line can precede the
  declaration would help only undocumented targets, and would have to restate
  the outline's rule for where a declaration starts.
