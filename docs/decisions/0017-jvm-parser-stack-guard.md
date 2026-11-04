# 0017. JVM parser stack guard

- Status: Accepted
- Date: 2026-11-04
- Discussion: [#461](https://github.com/SaltedTan/virgil-language-server/issues/461)

## Context

The parser's recursion guard measures the stack from a mark taken where parsing starts, so a deeply nested document stops with the established `nesting too deep; parsing stopped here` diagnostic before the stack runs out. On native targets the mark is the real stack pointer, but on the [JVM target](../development.md#the-jvm-target) the stack lives in a Java thread, invisible to the Virgil runtime, so the guard never fired: a document nested beyond the thread's stack ended the server with `StackOverflowError` ([#461](https://github.com/SaltedTan/virgil-language-server/issues/461)). The guard must work on the JVM without catching `StackOverflowError` and without relying on a larger `-Xss`, because the JVM's default 1 MB thread stack is what users get.

## Decision

On the JVM target, `HostRuntime.stackMark()` reads the thread's frame depth and scales it so the parser's existing 1 MB stack limit becomes a limit on frames of parser recursion:

- The depth comes from a new `stack` command in `V3S_System.java`, which returns `Thread.currentThread().getStackTrace().length`.
- HotSpot caps `Thread.getStackTrace()` at 1,024 frames, so the guard's unit is a frame and its limit is 512 frames: half the cap, leaving room for the frames above the parser (the interpreter's dispatch, the worker's message loop) and for the depth to grow between samples.
- The depth is sampled once per 128 parser calls, not on every call: `getStackTrace()` walks the whole thread, and sampling keeps the overhead of a normal parse to about a millisecond on a 106 KB file. When the measured depth reaches the resample threshold (512 frames), the mark is retaken, so a parse that descends far below an old mark is still bounded.

The cutoff is far below where an unguarded parse overflows: on x86-64 Linux with JDK 25's default 1 MB stack, 10,000 nested brackets overflow at about 960 brackets of nesting, while the guard stops the parse at 512 frames of recursion. The margin covers the interpreter's larger frames and the untested darwin-arm64 target.

## Consequences

- Deep nesting on the JVM stops with the same diagnostic as native, and the server and its worker stay alive; the `diagnostics-deep-nesting` protocol case carries a JVM expectation for the earlier cutoff, and a below-bound case checks that ordinary documents are unaffected.
- The JVM's nesting cutoff depends on the frame accounting of the running JDK, not on a byte address, so the exact column where a parse stops can move between JDKs; the guard's job is to stop the parse before overflow, not to match native's column.
- `Thread.getStackTrace()` is a Java 8 API, so the runtime class keeps its Java 8 compatibility; the 1,024-frame cap is a HotSpot behavior, and a JVM that reports more frames would simply get a proportionally deeper cutoff, still far below its stack.

## Alternatives considered

- **Catch `StackOverflowError` in the launcher.** The issue excludes it: the error can surface inside the parser's own frames, where a catch cannot restore a consistent parse state, and it would mask genuine overflow in non-parser code.
- **Require a larger `-Xss`.** The launchers already pass `-Xss16m` for development, but users who run the jar directly get the JVM's default 1 MB stack, and a bigger stack only delays the overflow; the guard must hold at the default.
- **Measure the Java stack with `StackWalker`.** It is uncapped and faster than `getStackTrace()` at depth, but it is a Java 9 API and its iterator carries per-frame state; `getStackTrace().length` keeps the runtime class at Java 8 and the whole measurement in one array.
- **Count parser calls as a proxy for depth.** Call counts are not frames: the interpreter re-enters through dispatch on every call, so a frame-based limit in calls would be off by a factor that changes with the compiler's inlining.
