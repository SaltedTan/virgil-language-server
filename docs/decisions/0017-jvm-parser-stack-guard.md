# 0017. JVM parser stack guard

- Status: Accepted
- Date: 2026-11-04
- Discussion: [#461](https://github.com/SaltedTan/virgil-language-server/issues/461)

## Context

The parser's recursion guard measures the stack from a mark taken where parsing starts, so a deeply nested document stops with the established `nesting too deep; parsing stopped here` diagnostic before the stack runs out. On native targets the mark is the real stack pointer, but on the [JVM target](../development.md#the-jvm-target) the stack lives in a Java thread, invisible to the Virgil runtime, so the guard never fired: a document nested beyond the thread's stack ended the server with `StackOverflowError` ([#461](https://github.com/SaltedTan/virgil-language-server/issues/461)). The guard must work on the JVM without catching `StackOverflowError` and without relying on a larger `-Xss`, because the JVM's default 1 MB thread stack is what users get.

The guard has two things to bound, and they are invisible to each other. Nested *parsing* (brackets, blocks, prefix operators, `while`, `else if`) recurses through the parser's own frames, out in the open where the per-token stack check can see it. Operator and suffix *chains* (`1+1+1+…`) are built in a loop and then handed to the recursive `Expr.range()` methods, which run out of sight of the token skips; the parser's `AnalysisChainCounter` bounds those by a count of `range()` levels. Both bounds must be sized for the JVM's compiled frames, which are larger than native's.

## Decision

On the JVM target, `HostRuntime` reads the thread's frame depth and scales it so the parser's existing 1 MB stack limit becomes a limit on frames of recursion, and it supplies its own chain bound:

- The depth comes from a new `stack` command in `V3S_System.java`, which returns `Thread.currentThread().getStackTrace().length`.
- HotSpot caps `getStackTrace()` at 1,024 frames, so the guard's unit is a frame. `stackMark()` returns minus the depth times `STACK_SCALE` (2,048), so the parser's 1 MB limit is a comparison threshold of `1 MiB / 2048 = 512` frames of growth.
- `stackBase()` samples the depth fresh, once, where a parse starts, so each parse measures from its own start rather than a later reading. `stackMark()` samples every `STACK_SAMPLE` (128) calls, so a token never triggers a full stack walk on its own; `getStackTrace()` walks the whole thread and costs about a microsecond per frame.
- `stackFree()` returns the headroom below the 1,024-frame cap, so a parse that starts deep leaves too little headroom to measure and the guard's limit shrinks to fit it, firing early (failing closed) rather than overflowing the thread's stack; a shallow base leaves the limit at 1 MiB, unchanged.
- `chainLimit()` returns 2,048, the JVM's cap on the `range()` levels that `AnalysisChainCounter` estimates. The native targets return 8,192, the existing `CHAIN_LIMIT`.

The 512-frame threshold is a comparison, not a hard cutoff, and the measured numbers on x86-64 Linux with JDK 25's default 1 MB stack are:

- **Baseline.** A parse starts at the server's baseline depth, a small constant of frames above the thread's bottom.
- **Overshoot.** Because `stackMark()` first samples at the 128th call, the guard's first check sees the depth after 128 tokens of descent, not the 512-frame threshold. A fresh deep-bracket parse takes its first measurement at **768 frames**, so the effective bracket cutoff is 768 frames, 256 frames above the nominal threshold.
- **Frames versus levels.** Brackets use about **6 frames per level**, so the 768-frame cutoff is about **124 brackets** (the `diagnostics-deep-nesting` case stops at column 132). The unguarded parse overflows at about **960 brackets** (about 5,800 frames), so the guard stops a bracket parse at roughly a sixth of the way to overflow.
- **Chain bound.** The case that matters is a cold server (the first didOpen, with the parse interpreted or only partly compiled): a `1+` chain then overflows at about **8,000 levels** on the default stack (about **131 bytes per level**, measured on the default stack with the limit raised). A warmed-up, JIT-compiled server overflows later, and the interpreter (`-Xint`) in between. The 2,048-level `chainLimit()` is a margin of at least four-fold in levels and keeps the recursion to about 268 KiB of the 1 MB stack.

The margins cover the interpreter's larger frames, which compiled code replaces, and the shipped darwin-arm64 target, which is untested. A parse that reaches a limit reports its diagnostic and stops; nothing catches `StackOverflowError`, and no larger `-Xss` is needed.

## Consequences

- Deep nesting and long chains on the JVM stop with the same diagnostics as native, and the server and its worker stay alive; `diagnostics-deep-nesting` and `diagnostics-long-chains` carry JVM expectations for the earlier cutoffs, and a below-bound case checks that ordinary documents are unaffected.
- The JVM's cutoffs depend on the frame accounting of the running JDK, not on a byte address, so the exact column where a parse stops can move between JDKs; the guard's job is to stop the parse before overflow, not to match native's column.
- `Thread.getStackTrace()` is a Java 8 API, so the runtime class keeps its Java 8 compatibility. The 1,024-frame cap bounds the *reading*, not the guard: a parse that starts shallow is stopped at the 512-frame threshold (or the 768-frame first measurement, the overshoot), and a parse that starts deep leaves too little headroom to measure, so `stackFree()` returns a small headroom and the guard fires early (failing closed) rather than overflowing. A JVM that reported more frames would read deeper depths accurately but would still stop a shallow parse at the same 512 frames of growth.
- The per-target `stackBase()`, `stackMark()`, and `chainLimit()` are read by the parser, so the native binaries gain those calls; the native behavior is unchanged (the native `stackBase()` returns the same pointer `stackMark()` does, and the native `chainLimit()` is the existing 8,192), which the `diagnostics-deep-nesting` cutoff at column 1854 and the passing native suites confirm.

## Alternatives considered

- **Catch `StackOverflowError` in the launcher.** The issue excludes it: the error can surface inside the parser's own frames, where a catch cannot restore a consistent parse state, and it would mask genuine overflow in non-parser code.
- **Require a larger `-Xss`.** The launchers already pass `-Xss16m` for development, but users who run the jar directly get the JVM's default 1 MB stack, and a bigger stack only delays the overflow; the guard must hold at the default.
- **Measure the Java stack with `StackWalker`.** It is uncapped and faster than `getStackTrace()` at depth, but it is a Java 9 API and its iterator carries per-frame state; `getStackTrace().length` keeps the runtime class at Java 8 and the whole measurement in one array.
- **Count parser calls as a proxy for depth.** Call counts are not frames: the interpreter re-enters through dispatch on every call, so a frame-based limit expressed in calls would be off by a factor that changes with the compiler's inlining.
- **Keep a balanced nesting counter.** A counter of open versus close brackets would measure nesting *levels* directly and be target-independent, but it only sees brackets: the parser's recursion also comes from blocks, prefix operators, `while`, and `else if`, none of which a bracket counter bounds. It also can't account for the target-dependent compiled frame sizes that a frame-based limit does, and maintaining it would require a parser change that the frame guard avoids.
