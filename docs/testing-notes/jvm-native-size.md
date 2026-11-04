# Native size accounting for the JVM build

The JVM build's native comparison is between `cda76ddb764a` and `b9f6cc82f0c9`,
using the pinned `b86f43f1` compiler, default native flags, and identical generated
`BuildInfo.v3` bytes. The 112-byte server and 176-byte worker increases reproduce
on x86-64 Linux. Exact file-size deltas also depend on compiler source paths:
Aeneas embeds those paths in its runtime source tables and aligns the tables and
the following data. Matching BuildInfo and compiler revisions alone does not
fix that layout.

## Reproduced comparisons

Both revisions used the same dependency path in each comparison. The two aliases
below pointed to the same pinned checkout under the test worktree; no compiler
or product source changed between runs. Paths are relative to that worktree in
this table, but `VIRGIL` received their absolute paths.

| Compiler checkout path | Absolute path bytes | Server base / target bytes | Worker base / target bytes | Increases |
| --- | ---: | ---: | ---: | --- |
| `build/size-check/base/absolute` | 108 | 982776 / 982888 | 925448 / 925624 | 112 / 176 |
| `build/size-check/base/absolutex` | 109 | 982824 / 982944 | 925520 / 925696 | 120 / 176 |
| `build/review/virgil` | 97 | 982224 / 982344 | 924608 / 924784 | 120 / 176 |

The last row reproduces the reported validation failure. The first two isolate
the cause to one additional byte in the source path. These are measurements for
this source revision and layout, rather than a portable exact-size assertion.

## Method accounting

Aeneas's `-print-size` output gives these changes in emitted method bytes.
A dash means that the method is absent. All other method sizes match, after
matching generated closures independently of their compiler-assigned numeric
suffixes. The method-size changes are identical in all three path layouts.

| Method | Server base / target | Worker base / target |
| --- | ---: | ---: |
| `AnalysisReport.puts` | 198 / 249 | — |
| `AnalysisReport.write` | 322 / 318 | — |
| `AnalysisSingleFileParser.stackLimit` | 127 / 118 | 127 / 118 |
| `AnalysisSingleFileParser.stackPointer` | 6 / — | 6 / — |
| `HostRuntime.stackFree` | — / 19 | — / 19 |
| `HostRuntime.stackMark` | — / 10 | — / 10 |
| `HostRuntime.heapBytes` | — | — / 17 |
| `HostRuntime.forceGC` | — | — / 6 |
| `HostRuntime.heapUsed` | — | — / 6 |
| `LspAnalysisCapture.equal` | 460 / 391 | — |
| `AnalysisTables.sipHash` | — | 1554 / 1515 |
| `AnalysisWorker.run` | — | 339 / 330 |

The method totals decrease by 8 and 5 bytes. With method alignment, the complete
machine-code regions change by 0 and 16 bytes respectively. The word loads
remain inside `equal` and `sipHash`; they add no per-word helper call.

## File accounting

The runtime region addresses from `-print-bin` account for the reference delta:

| Region | Server increase | Worker increase |
| --- | ---: | ---: |
| Machine code, including method alignment | 0 | 16 |
| GC stack maps | 4 | 16 |
| Source points | 16 | 20 |
| Source methods | 8 | 32 |
| Source strings, aligned to four bytes | 52 | 72 |
| Source classes | 8 | 8 |
| Exception entries | 24 | 12 |
| **Total** | **112** | **176** |

GC roots, type maps, extended GC maps, and initialized data sizes are unchanged.
In the reported 120-byte server layout, the aligned source strings grow by 56
bytes instead of 52, and alignment before initialized data adds another four
bytes. Those eight bytes explain the discrepancy completely.

For a repeat comparison, preserve the exact compiler source path as well as
BuildInfo and compiler flags. Build both revisions with the same BuildInfo file
and `make -o buildinfo all` so revision generation cannot replace it. Use
`-print-size` to compare methods and `-print-bin` to compare runtime region
boundaries when a file-size delta differs. These options report generated
output without changing the executable. Account for metadata and alignment
before treating a different total as additional product code.

## The #461 parser guard

The #461 parser guard adds `HostRuntime.stackBase()` and
`HostRuntime.chainLimit()` and a `chainLimit` field to `AnalysisParseSkipper`,
which the parser reads on every token skip. Built from this worktree path with
identical generated `BuildInfo.v3` bytes and the pinned compiler, the increases
are:

| Program | Before (`7fc31757`) | After (`ee982913`) | Increase |
| --- | ---: | ---: | ---: |
| `virgil-lsp` | 978616 | 978704 | 88 |
| `virgil-lsp-worker` | 918888 | 918976 | 88 |

`-print-size` accounts for the emitted method bytes; a dash means the method is
absent. The two new methods and the skipper's changes are:

| Method | Server before / after | Worker before / after |
| --- | ---: | ---: |
| `HostRuntime.stackBase` | — / 10 | — / 10 |
| `HostRuntime.chainLimit` | — / 6 | — / 6 |
| `AnalysisParseSkipper.new` | 89 / 100 | 89 / 100 |
| `AnalysisParseSkipper.skip` | 416 / 422 | 416 / 422 |

The method code grows by 33 bytes in each program: 16 for the two new methods
and 17 for the skipper (11 in `new`, 6 in `skip`); the skipper's `chainLimit`
field itself is data, not method code. With method alignment and the runtime's
per-method metadata, the complete regions change by:

| Region | Server | Worker |
| --- | ---: | ---: |
| Machine code, including method alignment | 40 | 40 |
| GC stack maps | 4 | 4 |
| Source points | 4 | 4 |
| Source methods | 16 | 16 |
| Source strings, aligned to four bytes | 20 | 24 |
| Exception entries | 4 | 0 |
| **Total** | **88** | **88** |

GC roots, extended GC maps, and initialized data are unchanged. The native
cutoffs are unchanged: deep nesting still stops at column 1854, and long chains
still stop at 8,192 levels.
