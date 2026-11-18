# 0018. Analyze a project for a native target

- Status: Accepted; amends [ADR-0007](0007-bundled-virgil-library.md)
- Date: 2026-11-18
- Discussion: [#489](https://github.com/SaltedTan/virgil-language-server/issues/489), [#496](https://github.com/SaltedTan/virgil-language-server/pull/496)

## Context

[ADR-0005](0005-project-file-format.md) analyzes every project without a target, and its project files must say `"target": null`. [ADR-0012](0012-project-configuration-stability.md) keeps that for version 1 and leaves open how a later version would add targets. Without a target, Aeneas installs a synthetic `System` component, and `Pointer`, `AnyRef`, `CiRuntime`, and the `Linux` kernel component don't exist. Adding Virgil's `rt/` files doesn't help, because `rt/native/RiRuntime.v3` itself needs `CiRuntime` and `Pointer`.

Native code is where this matters. Wizard engine's x86-64-linux unit-test program (`build.sh unittest x86-64-linux`, 174 files at `titzer/wizard-engine` `958c6ad5`) stops at the compiler's 15-error limit in its first x86-64 files (`type "RiUserCode" cannot be found`, `type "Pointer" cannot be found`) and resolves 1,928 uses, 28 of them in `src/engine/x86-64`. On a development branch with more x86-64 code, 17 of the 27 files in `src/engine/x86-64` and 18 of the 27 in `test/unittest/x86-64-linux` name `Pointer`, `RiUserCode`, `RiRuntime`, or `Linux`, and its largest x86-64-linux program resolves 2,430 uses without a target, against 182,437 with one.

How the pinned Virgil (`b86f43f1`) builds for a target:

- `bin/v3c-x86-64-linux` passes `-target=x86-64-linux` and `-rt.files` with `rt/x86-64-linux/*.v3 rt/native/*.v3 rt/gc/*.v3`, which `Aeneas.makeProgram` appends after the program's files. Its other options, `-heap-size`, `-stack-size`, `-rt.sttables`, `-rt.gc`, and `-rt.gctables`, are read by the back end and the runtime's metadata tables. The verifier reads one of them, `-stack-size`, as the value of the constant `CiRuntime.STACK_SIZE`, which a `match` compares with its other values, so analysis applies the wrapper's stack size; the parser and the verifier read none of the others.
- `Compiler.new(target)` calls `Target.configureCompiler`, and `Compilation.new` calls `Target.configureProgram`, before anything is parsed. For a `LinuxTarget`, that makes an empty `MachProgram` and `MachRuntime`, which add `Pointer` and `AnyRef` (`MachModule.install`), `CiRuntime`, and `Linux` to the program's type environment. `System`, `RiRuntime`, and `RiUserCode` are declared in the `rt/` sources.
- The verifier reads the target only to check the main method's signature (`Target.verifyMain`), and the parser doesn't read it at all.
- The server already compiles every registered target into the worker, since it compiles all of `aeneas/src`. `Aeneas.getTarget` prints to standard output for an unknown name.

A spike (branch `spike/x86-64-target-analysis`, `0a05b786`) passed the registered target to those two constructors, skipped `SystemCalls.install`, and appended the three runtime directories. Upstream Wizard's x86-64-linux unit tests then report exactly the two errors that the pinned `v3c-x86-64-linux` reports for the same files, an incompatibility between that Wizard revision and the pin, and resolve 137,059 uses, 26,882 of them in `src/engine/x86-64`. All nine x86-64-linux programs on the development branch verify with no errors, as they do with the pinned compiler, and a deliberately broken program gets the compiler's error at the compiler's position. In a LazyVim session, hover, definition, references, and diagnostics as you type worked in the x86-64 files, and definition went into `rt/`.

## Decision

1. **A project can name a target in project file version 2.** Version 2 is version 1 with one difference: a project's `target` can also be a string naming a supported target. `null`, or no `target`, still means no target. Version 1 doesn't change, and the server reads both versions ([ADR-0012](0012-project-configuration-stability.md), point 4).
2. **The server supports a list of targets, starting with `x86-64-linux`.** Each entry names an Aeneas target, the runtime sources that the pinned `bin/v3c-<target>` wrapper passes for it, as patterns relative to the Virgil root, and the stack size that the wrapper passes. Any other name, including Aeneas's other registered targets and its `-test` variants, is an `InvalidValue` that lists the supported names. Each new target is a change of its own, with its tests. A test compares every entry with its wrapper at the pin, so a pin update that changes a wrapper's runtime files or stack size fails until the entry follows.
3. **The runtime sources come with the target.** A target project's program is its `sources`, `dependencies`, and `virgilDependencies`, followed by its target's runtime sources from the Virgil root, as `v3c` appends `-rt.files`. They are dependencies, not members. A file that the project's own patterns already select isn't added twice. A missing Virgil root, or a root without those files, is a configuration diagnostic at the `target` value.
4. **The worker configures the target and stays front end only.** The [`ANALYZE` request](0004-analysis-worker-process.md#channel-and-framing) carries the target's name. The adapter finds the registered `Target` by name in `Aeneas.targets`, never through `Aeneas.getTarget`, and keeps one `Compiler` per target, configured once. Each analysis builds a fresh `Program`, without `SystemCalls.install` when it has a target, and a `Compilation` with that target's `Compiler`, which configures the program, and sets the target's stack size until it ends. It then runs the parser and `Compilation.verify()` only, as [ADR-0002](0002-pinned-virgil-adapter-boundary.md) requires. Configuring a target installs type constructors and makes the target's empty program records; nothing is compiled, initialized, or run.
5. **The target belongs to the project, and nothing else is per target.** Each project already has its own context and snapshot, so a target project's snapshot is its target's. A file in both a portable project and a target project is analyzed in each, as any file shared by two projects is, and the [active-context rules](../configuration.md#workspace-folders-and-active-projects) choose which answers. Single-file parsing doesn't depend on the target. This follows the roadmap's rule to add per-target analysis only where semantics differ: each program is analyzed once, for the one target it is built for.
6. **Builtin names follow the project's target.** For `x86-64-linux`, `Pointer`, `AnyRef`, `CiRuntime`, and `Linux` are builtin types, with their members (those of pointer values and of the `Pointer` type, `Linux.syscall`, and `CiRuntime`'s), and `System` is the runtime's source component, not the synthetic one. Completion, and implementation's check for a builtin type's name, take the project's target as they take its language options.
7. **Release archives bundle the supported targets' runtime sources.** Beside `lib/`, `virgil/` holds the `.v3` files that the supported targets' runtime patterns select, with their hashes in the manifest: for `x86-64-linux`, the 12 files of `rt/x86-64-linux`, `rt/native`, and `rt/gc`. The rest of `rt/` stays out. This amends [ADR-0007](0007-bundled-virgil-library.md), whose archives hold no `rt/`.

## Consequences

- Code written for x86-64-linux gets the same features as target-independent code. A project file for Wizard engine's x86-64-linux unit tests needs `"version": 2`, `"target": "x86-64-linux"`, and the `build.sh` lists, without the runtime:

  ```json
  {
    "version": 2,
    "projects": [
      {
        "name": "unittest-x86-64-linux",
        "target": "x86-64-linux",
        "sources": ["src/engine/*.v3", "src/engine/continuation/UnboxedContinuation.v3", "src/util/*.v3",
          "src/engine/native/*.v3", "src/engine/compiler/*.v3", "src/engine/x86-64/*.v3",
          "src/engine/x86-64/continuation/X86_64UnboxedContinuation.v3",
          "src/modules/*.v3", "src/modules/wasi/*.v3", "src/modules/wasi/x86-64-linux/*.v3",
          "test/unittest/*.v3", "test/wasm-spec/*.v3", "test/unittest.main.v3", "test/unittest/x86-64-linux/*.v3"],
        "virgilDependencies": ["lib/util/*.v3", "lib/test/*.v3", "lib/asm/x86-64/*.v3", "lib/x86-64-linux/SyscallLayouts.v3"]
      }
    ]
  }
  ```

- Servers before this change reject a version 2 file with `UnsupportedVersion` and check nothing else in it, so their users keep a version 1 file, or a separate one, without targets.
- The costs measured by the spike are in the table below: Linux x86-64, Intel Core i7-14700KF, the default 384 MB worker heap, `analyze --stats --bindings`, and the first analysis in a fresh worker unless the row says otherwise. Every program counts its 12 runtime files.

  | Measurement | Upstream Wizard, x86-64-linux unit tests | Largest program on the development branch | Its eight other x86-64-linux programs |
  | --- | ---: | ---: | ---: |
  | Files, source | 186, 2.30 MB | 225, 3.40 MB (81% of the 4 MiB source budget, before paths) | 151–153, 2.03–2.13 MB |
  | Resolved uses, without and with the target | 1,928 and 137,059 | 2,430 and 182,437 | — and 104,473–106,886 |
  | Parse and verify | 62 and 33 ms | 79–88 and 46–50 ms; 51 and 37 ms in a reused worker | 46–53 and 27–35 ms |
  | Charged result allocation, of the 16 MiB result budget | 9,616,153 bytes (57%) | 12,259,479 bytes (73%) | 8,089,781–8,230,239 bytes (48–49%) |
  | Worker peak resident memory, five analyses | — | 189 MiB, as for the Aeneas sources (188 MiB) | — |

  Twenty analyses of the largest program in one worker gave identical results, and the process-wide type cache grew only during the first. Portable and target analyses interleaved in one worker answered as each does alone.
- Two snapshots of the largest program, 24.5 MB together, fit in the 32 MiB snapshot budget with little room beside them, so moving between several large target projects evicts snapshots more often. The latest is never evicted, and an evicted snapshot is analyzed again on demand ([Analysis snapshots](../architecture.md#analysis-snapshots-partly-present)).
- An edit schedules every project containing the file. With nine target projects that share Wizard's x86-64 engine, one edit cost the worker 1.7 s of CPU in all, against 0.5 s with one; hover after the edit took 0.34 and 0.29 s, since the edited document's own project is analyzed first. The configuration guide will say so.
- The release archive grows by the runtime's 12 files: 80,837 bytes, 22,122 bytes compressed, beside the 135,527 bytes of the compressed `lib/`. The worker executable grows by 59,144 bytes, to 978,376, since target configuration becomes reachable.
- A build that passes a different `-stack-size` from its wrapper's analyzes with the wrapper's value.
- Pin updates gain a step: the wrapper test compares each target's runtime patterns and stack size, and the bundle check covers the new files. The [pin update steps](../../CONTRIBUTING.md#updating-the-pinned-virgil-revision) will name both.
- A client-supplied Virgil root supplies its own `rt/`, as it supplies its own `lib/`, so a checkout at another revision can disagree with the pin about the runtime as about the library ([Compatibility](../compatibility.md)).
- The work is split into issues under [#489](https://github.com/SaltedTan/virgil-language-server/issues/489): the adapter, the worker protocol and command line, project file version 2, the runtime sources in expansion and analysis, the bundle, and target-aware builtins. The maintainer accepted the design on 2026-11-18 with the bundle limited to the supported targets' runtime files, as decision 7 says, and with target-aware builtins (decision 6) in the first milestone.

## Alternatives considered

- **List the runtime in `virgilDependencies`.** Each target project would repeat the runtime's three patterns, and forgetting one reports `type "RiUserCode" cannot be found` far from the cause. A project could also list another target's runtime. The compiler's own wrappers supply the runtime, and so does the server.
- **Accept any registered Aeneas target.** The runtime files differ by target and come from wrapper scripts that the server can't run, and targets nobody has tested would claim semantics that nobody has checked. The JVM and Wasm targets configure programs differently, and the `-test` variants serve Aeneas's own test suites.
- **Pass the target in `compilerArgs`, as `-target=x86-64-linux`.** `compilerArgs` holds language options only, and ADR-0005 rejected passing other `v3c` flags. A target changes which files form the program, which a flag list shouldn't do silently.
- **Analyze each project for several targets, or give a project a list of targets.** That multiplies analyses and snapshots for every project, including portable ones whose semantics don't change. A program is built for one target, and a project that needs two can be two projects.
- **Install the target's types into a target-free program.** Copying `configureProgram`'s work into the adapter would duplicate compiler internals that change with the pin, and would differ from what the compiler checks.
- **Read `build.sh`, or run the wrapper.** Under [ADR-0005](0005-project-file-format.md), nothing in a project file makes the server run code, and a build script doesn't say which files form an analysis program.
- **Accept target names in version 1.** That is a new accepted value, which [ADR-0012](0012-project-configuration-stability.md) reserves for a new format version.
- **Bundle all of `rt/`.** Its 47 `.v3` files are 247,524 bytes, mostly for targets that the server doesn't support; bundling them would suggest that it does.
