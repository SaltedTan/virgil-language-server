# 0015. Compile the runtime's GC with the global register allocator

- Status: Accepted
- Date: 2026-11-02
- Discussion: [#434](https://github.com/SaltedTan/virgil-language-server/issues/434), upstream [titzer/virgil#826](https://github.com/titzer/virgil/issues/826)

## Context

Every executable here is one Aeneas invocation at the default optimization level, `-O1`. At that level the pinned Aeneas leaves its global register allocator off, so the x86-64 backend keeps loop variables in stack slots. The runtime's semispace collector zeroes the rest of the semispace after each collection with `RiGc.memClear`, one word per iteration, and copies live objects with `RiGc.memCopy`. Compiled at `-O1`, `memClear` stores and reloads its pointer through the stack for every 8-byte word it clears.

The analysis worker collects after sending each result, and that collection clears the roughly 190 MB a full Aeneas analysis filled. [Worker collection after sending](../benchmarks.md#worker-collection-after-sending-2026-11-02) measured it at about 20 ms of CPU, with wall times of 38 to 55 ms when the benchmark pins the server and worker to one CPU. Because the loop is limited by the core rather than by memory, its speed also moves by about 25% with the loop's address, so unrelated code-size changes moved the benchmark's collection by about 10 ms.

Aeneas's `-global-regalloc=<pattern>` option turns the global allocator on for the matching methods at any level, and `-O2` turns it on for every method.

## Decision

Compile every executable the `Makefile` builds, release and benchmark builds and the test executables alike, with `-global-regalloc=RiGc.*`. The flag is the `Makefile`'s `V3C_FLAGS`, and `benchmark-build.json` records it in each executable's command. The rest of the program stays at the default level.

## Consequences

`memClear` becomes a five-instruction register loop, and `memCopy` stays in registers too. In two pinned runs, the worker's collection after sending an Aeneas result fell from 17.5 and 21.4 ms of CPU at the median to 10.2 and 10.1 ms, and from 34.2 and 42.7 ms of wall time to 20.1 ms in both, for `reused_analysis`. In the quieter second run, separate processes of one binary took either about 15 or about 21 ms of CPU without the flag, and every block took 10.1 to 10.3 ms with it. Request latencies moved less, about 10 ms at the median and within their spread, because the collection overlaps the server's decoding. The release worker is 336 bytes smaller. [With the global register allocator](../benchmarks.md#worker-collection-after-sending-2026-11-02) has the tables.

A benchmark comparison across this change shows a step in every row that includes the worker's collection after sending, as `bench-compare.py` does for any ref pair that straddles it.

The flag doesn't change the pin, and it makes no difference on targets without the global allocator, such as 32-bit x86. It names the runtime's component, so a pin bump that renames or moves `RiGc` needs the flag revisited: the build doesn't fail when a pattern matches nothing. If upstream changes `memClear` or the compiler's default for these routines ([titzer/virgil#826](https://github.com/titzer/virgil/issues/826)), a later pin bump can drop the flag after measuring the collection without it.

## Alternatives considered

- **`-O2` for every executable.** It also turns on the global allocator everywhere, but it changes how all of the server, the worker, and the embedded compiler front end are compiled, and that hasn't been measured or tested here. It stays a separate question.
- **Wait for upstream.** A runtime change reaches us with the next pin bump, and a compiler change only after upstream also rebuilds its `bin/stable` compiler. The flag gives the same code now.
- **Patch the runtime here.** `vendor/virgil` is the unmodified upstream pin ([ADR-0002](0002-pinned-virgil-adapter-boundary.md)), and replacing runtime files with `-rt.files` would mean maintaining a copy of the collector.
