# 0005. Project file format, version 1

- Status: Accepted
- Date: 2026-10-03
- Discussion: [#53](https://github.com/SaltedTan/virgil-language-server/issues/53)

## Context

Virgil compiles whole programs, so semantic analysis needs to know which files form each program. A repository can hold several programs that share files and declare the same top-level names. [ROADMAP](../../ROADMAP.md#workspace-and-project-model) proposed a versioned `.virgil-lsp.json` and asked for its fields to be settled after studying real projects. The format becomes user-facing and must be stable by v1.0.

What real projects do:

- **Aeneas** lists its library dependencies in `aeneas/DEPS` as globs relative to the Virgil checkout, not to the project: `lib/util/*.v3`, `lib/asm/x86-64/*.v3`, `lib/file/elf/*.v3`, and `lib/test/*.v3`, which this repository's `Makefile` filters out.
- **Virgil's apps** have `DEPS` files with paths relative to the app, such as `../../lib/util/*.v3`. Their `TARGETS` files list targets such as `x86-linux` and `x86-64-linux` (`apps/HelloLinux`) or `wasm` (`apps/WebMandelbrot`).
- **Wizard engine** builds several programs (`wizeng`, `objdump`, `unittest`) from one repository. Their file sets differ by target, and one object-model directory is chosen from `src/engine/objmodel/`. Library files come from `$VIRGIL_LIB/util/*.v3`, outside the repository. `build.sh` finds the library through `VIRGIL_LIB`, then `VIRGIL_LOC`, then the location of `v3c` on `PATH`. Every build passes `-lang:fun-exprs -lang:simple-bodies -lang:descriptors`.

Constraints from the compiler and the server:

- Aeneas spells language options `-lang:<name>` and keeps them in process-wide `CLOptions`. The parser reads four of them, and the type checker reads the others. A boolean option given any value other than `true` reads as false.
- Without a target, Aeneas supplies a synthetic `System` component, and native-only types such as `Pointer` don't exist. Wizard's `src/engine/x86-64` files report unresolved types when analyzed this way, even with Virgil's `rt/` files added.
- `JsonRpcJson` builds `JsonValue`s, which carry no source positions, and its object maps keep only the last of repeated keys.

## Decision

1. **One JSON file, `.virgil-lsp.json`, with `version: 1` and a list of projects.** Each project has a `name`, `sources`, and optional `dependencies`, `virgilDependencies`, `exclude`, `compilerArgs`, and `target`. [Configuration](../configuration.md) specifies the fields, types, and defaults.
2. **Strict by default.** Unknown fields, repeated fields, wrong types, unsupported versions, duplicate project names, invalid patterns, and unsupported compiler flags are errors. A file with any error defines no projects. A later version can relax a rule without breaking existing files, but tightening one would break them. An unsupported `version` stops all other checks, because a later version may define other fields.
3. **Patterns are relative, `/`-separated globs with `*`, `?`, and whole-segment `**`.** They can't be absolute, contain `..`, or use `\`. `[`, `]`, `{`, and `}` are reserved for later syntax. Wildcards don't match names that start with a dot. Patterns that select source files must end in `.v3`.
4. **`sources` and `dependencies` describe ownership.** Both are compiled into the program. Only `sources` makes a file a member of the project, which decides a shared file's active project and which closed files get diagnostics.
5. **Virgil library files go in `virgilDependencies`, relative to the Virgil root**, written as in `aeneas/DEPS` (`lib/util/*.v3`). The project file can't name the root. The server takes it from the client's `initializationOptions.virgilRoot`, then `VIRGIL_LOC`, then the parent of the directory holding `v3c` on `PATH`. It never runs `v3c`, and it logs the root it uses.
6. **`compilerArgs` accepts only the pinned compiler's language options**, as `-lang:<name>`, `-lang:<name>=true`, or `-lang:<name>=false`, each at most once. They apply to every analysis of the project and to single-file parsing of documents whose active project it is. The adapter supplies the accepted options from the pinned compiler.
7. **`target` must be `null`.** Version 1 analyzes every project without a target.
8. **Configuration diagnostics have byte ranges.** `src/workspace/ProjectJson.v3` parses the file into values that record their byte ranges and keep repeated keys. The token decoding and limits are those of `JsonRpcJson`. The server will publish configuration diagnostics with `textDocument/publishDiagnostics` on the project file's URI, and send one `window/showMessage` warning when a project file becomes invalid.
9. **The parser does no file-system access.** `ProjectConfigParser.parse` takes the file's bytes and returns either a model or its diagnostics. Finding project files, reading them, and expanding patterns are separate ([#55](https://github.com/SaltedTan/virgil-language-server/issues/55)).

## Consequences

- Nothing in a project file can make the server download or execute code. No field names a command, a URL, or an environment variable. Patterns can't leave the project file's directory or the Virgil root. Compiler actions and targets are rejected, and analysis stays front-end only ([ADR-0002](0002-pinned-virgil-adapter-boundary.md)).
- A shared repository's project file contains no machine-specific paths. Each user configures the Virgil root once, in the editor or the environment.
- Native-target code, such as Wizard's x86-64 engine, can't be verified in version 1. Users can describe the target-independent builds, and per-target analysis needs a later version.
- Applying `compilerArgs` means setting process-wide state. The worker does it before each analysis, and the server's single-file parser needs the same flags for documents in a project. Until projects are mapped to documents, both use the defaults.
- A Virgil update that adds or removes a language option changes which files are valid. [Compatibility](../compatibility.md) records the pinned revision.

## Alternatives considered

- **Read `DEPS` and `TARGETS` directly.** They don't say which files form a program in a multi-program repository, and they don't record flags. Their paths are relative to different directories: the Virgil root in `aeneas/DEPS`, and the app in the apps' `DEPS` files. The lines of `aeneas/DEPS` carry over unchanged into `virgilDependencies`.
- **A `$VIRGIL_LIB/...` or `virgil:` prefix inside ordinary patterns.** A `$` prefix suggests that environment variables are expanded, and a prefix needs escaping rules for file names. A separate field is explicit.
- **A `virgilRoot` field in the project file.** It would hold a machine-specific path in a shared file, and it would let a project file point the server at any directory.
- **Pass any `v3c` flag through, or ignore unknown flags.** Most flags don't affect parse or verify. Accepting them silently would hide typing mistakes, and some, such as `-run` or `-rt.files`, name actions or files that analysis must not touch.
- **Accept and record a `target` that analysis ignores.** That would suggest native code is analyzed for that target when it isn't.
- **Report problems only with `window/showMessage`.** Users would lose the location of each problem, and one message can't list several problems well.
- **Lenient parsing with warnings and partial models.** That is easy to relax into later, but a lenient version 1 could not become strict without breaking files.
- **JSON with comments, TOML, or YAML.** The server already parses JSON, and the file name was set by the roadmap and the Neovim root markers. Comments may come in a later version.
