# Interpreter coverage for the unit tests

This note records an experiment for [#364](https://github.com/SaltedTan/virgil-language-server/issues/364): whether the pinned Virgil toolchain can report useful coverage for the project's tests, as part of the [roadmap's testing strategy](../../ROADMAP.md#testing-strategy). It describes what the compiler offers and what happened when the unit tests ran through it. It is a record, not a supported command, and nothing in `make test` or CI uses it.

**Result:** the unit suite can't run in the interpreter as it is built today, so the toolchain can't measure its coverage. Small groups that need no native code run, and their reports are accurate for what they measure: the share of a method's basic blocks that ran, for methods that were called at least once. That doesn't describe the native server, the worker, or code that is never called, so the note recommends against a coverage command or gate for now.

## Setup

- Compiler: `vendor/virgil` at `57318d37`, whose `bin/stable/x86-64-linux/Aeneas` prints `Aeneas III-12.1989 stable`, `Build Data: x86-64-linux 2026-09-29 15:55:21` (SHA-256 `5cb4a2f5e0133fd0fb7ec94f1c938da8560964bad99f1f4ba06252788efac65e`).
- Platform: Ubuntu 24.04 on x86-64 (Linux 7.0.0-42-generic, Intel i7-14700KF), repository at `4bf867c8`.
- Prerequisites: an initialized `vendor/virgil`, and `make build/unit-tests` for the full source list, which includes `build/gen/BuildInfo.v3`. There is no separate interpreter binary: `Aeneas -run` interprets the program's SSA.
- Times are wall-clock times from `/usr/bin/time`, three unpinned runs each, which agreed within 0.05 s.

The commands below run from the repository root, with `SCRATCH` set to an empty directory outside the checkout:

```sh
A=vendor/virgil/bin/stable/x86-64-linux/Aeneas
```

Pass `-output="$SCRATCH"` so that nothing the compiler writes lands in the checkout.

## What the compiler offers

`vendor/virgil/aeneas/src/main/CLOptions.v3` defines these interpreter options. `Aeneas.compileAndRun` in `vendor/virgil/aeneas/src/main/Aeneas.v3` uses only one of the first three, in this order:

| Option | What it reports |
| --- | --- |
| `-coverage=<matcher>` | For each matching method, the percentage of its SSA basic blocks that ran (`SsaCoverage.v3`). |
| `-iprofile=<matcher>` | Each matching method's SSA, printed with an execution count for each block (`SsaBlockProfiler`). |
| `-profile=<matcher>` | Calls and time for each method, as a calling-context tree (`SsaTreeProfiler`). |
| `-icoverage` | Nothing: `SsaCoverage` receives the flag, but the code that would use it is commented out. |

The matcher is a method pattern such as `'*'`, `'DocumentUri.*'`, or `'Main.pick'`. How `-coverage` counts:

- **Granularity:** one probe per basic block, inserted when the interpreter first builds a method's SSA, after the control-flow and post-pass optimizations of the default `-O1` (and early inlining at `-O3`). There are no line, branch, or instruction counts, and no source locations: a branch shows only as a block that didn't run.
- **Denominator:** the interpreter builds SSA lazily, on a method's first call, so a method that is never called is left out of the report rather than listed at 0%.
- **With `-ra`:** reachability analysis lists every statically reachable method, but each one at 0%, including ones that ran. Normalization copies the probes (`SsaRebuilder.visitProbe` calls `dup()`), the interpreter runs the copies, and the report counts the originals.
- **Output:** one line per method, sorted by percentage, printed to standard output in color when `main` returns: a percentage and a method name, with no block counts. Names aren't unique; `JsonParser.new` appears twice below.
- **With a target:** `-run` with `-target=x86-64-linux` compiles for the target, writes the executable, and then interprets the machine-lowered SSA. That stops at the first call, which is a `CallAddress` the evaluator doesn't implement. `scripts/v3c.sh` always adds the target, so `scripts/v3c.sh -run` fails the same way, and the experiments call `Aeneas` directly with no target.

### A minimal program

`min.v3`:

```virgil
component Main {
	def main() -> int {
		return pick(0);
	}
	// Reached when x is 0; the other side of the branch is not taken.
	def pick(x: int) -> int {
		if (x > 0) {
			return unreached(x);
		}
		return reached(x);
	}
	def reached(x: int) -> int {
		var n = 0;
		for (i < 3) {
			n += i + x;
		}
		return n;
	}
	def unreached(x: int) -> int {
		var m = 0;
		for (j < 10) {
			m -= j * 2 + x;
		}
		return m;
	}
}
```

Each run below exits with status 3, the value `main` returns; color codes are removed from the output.

```console
$ $A -run -coverage='*' -output="$SCRATCH" "$SCRATCH/min.v3"
100% Main.main
100% Main.reached
 66% Main.pick
$ $A -run -ra -coverage='*' -output="$SCRATCH" "$SCRATCH/min.v3"
  0% Main.pick
  0% Main.main
  0% Main.reached
  0% Main.unreached
$ $A -run -target=x86-64-linux -output="$SCRATCH" "$SCRATCH/min.v3"
!NotFoldable: unimplemented
	in Main.main() [min.v3 @ 3:28]
```

`Main.pick` ran two of its three blocks. `Main.unreached` is missing from the first report, though `Main.pick` calls it. With `-trace`, the target run shows the failing instruction: `CallAddress<#ptr64<&((AnyRef, int) -> int)>>(...Main.pick[code]...) = !NotFoldable`.

### A reached and an unreached project function

A driver that calls only `DocumentUri.filePath`, compiled with `src/documents/DocumentUri.v3` and `vendor/virgil/lib/util`. `DocumentUri.normalize` calls `filePath` but is never called itself:

```console
$ $A -run -coverage='DocumentUri.*' -output="$SCRATCH" "$SCRATCH/UriDriver.v3" \
    src/documents/DocumentUri.v3 vendor/virgil/lib/util/*.v3
/home/a/c.v3
100% DocumentUri.isFile
 84% DocumentUri.filePath
 72% DocumentUri.asciiEqual
```

The reached function and its callees are reported. The unreached `normalize` isn't listed.

## The unit tests in the interpreter

### With the build's sources

This passes the source list of the Makefile's `build/unit-tests` rule to the interpreter, with the arguments `make test-unit ARGS='ProjectExpansion:*'` would give the executable:

```sh
SRCS=$(make -s -n -B build/unit-tests | sed -n '/-program-name=unit-tests/{n;p;}')
$A -run -heap-size=200m -stack-size=2m -output="$SCRATCH" $SRCS 'ProjectExpansion:*'
```

It fails in 0.16 s with exit status 255, with and without `-coverage`, before any test runs. The compiler stops reporting after 15 errors, so these are only the first:

```text
[src/analysis/AnalysisSingleFileParser.v3 @ 507:31] UnresolvedType: type "Pointer" cannot be found
[src/analysis/AnalysisSingleFileParser.v3 @ 512:30] UnresolvedType: type "Pointer" cannot be found
[test/unit/AeneasAdapterTest.v3 @ 671:9] UnresolvedIdentifier: identifier "RiGc" cannot be found
[test/unit/AnalysisCallQueryTest.v3 @ 269:22] UnresolvedIdentifier: identifier "Semispace" cannot be found
[test/unit/AnalysisCallQueryTest.v3 @ 269:63] UnresolvedIdentifier: identifier "GcStats" cannot be found
```

`Pointer`, `CiRuntime`, `Linux.syscall`, and the runtime's `RiRuntime`, `RiGc`, `Semispace`, and `GcStats` exist only when compiling for a native target. The source that the unit tests share with the server uses them in:

- `src/os/x86-64-linux/HostOs.v3`, for every system call it makes;
- `AnalysisSingleFileParser`'s stack guard;
- the word-at-a-time loads in `AnalysisTables`'s hash and `LspAnalysisInputs`'s string comparison;
- the heap measurements in six unit-test files.

Shims would not be enough. The interpreter's `System` has only file open, read, and write, console output, and clocks: no directory listing, `stat`, pipes, processes, `poll`, or signals. So groups such as `ProjectExpansion`, `WorkspaceProjects`, and `AnalysisSupervisor` can't do their work there. `test/unit/run.py` also copies the executable into its slots, and the tests find their fixtures and workers next to the executable. An interpreted run has no executable of its own.

### With a subset of the sources

A group whose sources need no native code runs when it is compiled with a scratch entry point in place of `test/unit/main.v3`, whose worker mode needs every server source:

```virgil
component SubsetMain {
	def main(args: Array<string>) -> int {
		return UnitTests.run(args);
	}
}
```

```sh
$A -run -coverage='*' -output="$SCRATCH" "$SCRATCH/SubsetMain.v3" test/unit/JsonRpcJsonTest.v3 \
  src/protocol/JsonRpcJson.v3 vendor/virgil/lib/file/json/JsonParser.v3 \
  vendor/virgil/lib/util/*.v3 vendor/virgil/lib/test/*.v3 'JsonRpcJson:*'
```

All 24 `JsonRpcJson` tests pass in each mode:

| Run | Wall time | Peak memory |
| --- | --- | --- |
| Native `build/unit-tests 'JsonRpcJson:*'` | 0.00–0.01 s | 6.5 MB |
| Interpreter | 0.89–0.91 s | 172 MB |
| Interpreter with `-coverage='*'` | 1.08–1.13 s | 172 MB |

The same command with a pattern that matches no test takes under 0.01 s, so the time is spent running the tests, not compiling. The coverage report has 151 lines, identical across runs, after the tests' own output. Every method of `JsonRpcJson` and `JsonRpcJsonParser` is listed, all at 100% except `96% JsonRpcJson.render`. The `DocumentUri` group also passes this way, in 0.01 s.

`ProjectExpansion` and `LspWorkspaceSymbols` can't be run this way: they need `HostOs` or the Aeneas adapter, which brings in `AnalysisSingleFileParser`.

## What coverage can't describe

- **The native executables.** `virgil-lsp`, `virgil-lsp-worker`, and `unit-tests` are compiled for x86-64. The interpreter runs a different program: SSA that is never lowered to machine code, without the native runtime, the garbage collector, and the stack guard, and with its own `System`. Native code has no coverage instrumentation in this toolchain.
- **The worker process and other children.** The unit tests start `virgil-lsp-worker`, the stall worker, and the unit-test executable as a synthetic worker through `HostOs.spawn`, which can't compile for the interpreter. A started child would be a native executable anyway, outside the interpreter's report.
- **Aggregation.** Each interpreter process prints one report for itself. `test/unit/run.py` runs each group in a process of its own, and the reports give percentages without block counts or unique names, so they can't be merged exactly. Nothing combines them.
- **Unreached code.** Methods that never run are missing, so a percentage over the program can't be formed from the report. `-ra` would supply that list but makes every count 0%.
- **Standard output.** The report goes to standard output, after the program's own output. For the server, that would follow the protocol messages.

## Recommendation

- **Usefulness:** low. Only leaf groups such as `JsonRpcJson` and `DocumentUri` run, and their tests already reach almost all of their code. The groups where coverage would say most, which use the analysis, the workers, and project files, can't run.
- **Accuracy:** good within what it measures, for interpreted code only. It reports the share of executed blocks of the optimized SSA, and only for called methods. It says nothing about native builds and gives no source lines.
- **Maintenance:** high. The full suite would need an interpreter-only `HostOs`, replacements for the native-only paths above, and tests that don't depend on the native heap. Its file-system and process tests still couldn't run. The subset route needs a source list for each group, kept by hand. Both would be a second build mode to keep in step with the Makefile.

So the project doesn't add a coverage command or a coverage gate. This is worth revisiting if the pinned toolchain gains native coverage instrumentation, or interpreter coverage that lists unreached methods.
