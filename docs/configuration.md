# Configuration

A `.virgil-lsp.json` project file says which source files form each program in a repository. This page specifies version 1 of its format. [ADR-0005](decisions/0005-project-file-format.md) records why the format looks like this.

The parser and its checks are implemented (`src/workspace/`). The server doesn't read project files while it runs yet. It will find the project file for each document, expand the patterns, and analyze projects in later M3 work ([#55](https://github.com/SaltedTan/virgil-language-server/issues/55), [#58](https://github.com/SaltedTan/virgil-language-server/issues/58)). Rules that take effect only then are marked *(planned)*. To check a project file now, run:

```sh
virgil-lsp check-config path/to/.virgil-lsp.json
```

## Why a project file

Virgil compiles whole programs: all participating `.v3` files are passed together. A repository can contain several programs, test files with duplicate declarations, and several targets, so analyzing every `.v3` file in a workspace would be wrong. A project file states which files form each program.

## Example

A project file for two programs in [Wizard engine](https://github.com/titzer/wizard-engine), as its `build.sh` builds them for the `x86-linux` target. That target uses the portable engine in `src/engine/v3` (see [Target](#target)). The programs share most of their files and select one object model directory, `boxed`. Both use Virgil's `lib/util` library, which is outside the repository, and the test program also uses `lib/test`. The flags are the ones `build.sh` passes.

```json
{
  "version": 1,
  "projects": [
    {
      "name": "wizeng",
      "sources": [
        "src/engine/*.v3",
        "src/engine/compression/*.v3",
        "src/engine/objmodel/*.v3",
        "src/engine/objmodel/boxed/*.v3",
        "src/engine/v3/*.v3",
        "src/util/*.v3",
        "src/modules/*.v3",
        "src/modules/wave/*.v3",
        "src/modules/wasi/*.v3",
        "src/modules/wali/*.v3",
        "src/modules/wizeng/*.v3",
        "src/monitors/*.v3",
        "test/wasm-spec/*.v3",
        "src/SpectestMode.v3",
        "src/WasmMode.v3",
        "src/wizeng.main.v3"
      ],
      "virgilDependencies": ["lib/util/*.v3"],
      "compilerArgs": ["-lang:fun-exprs", "-lang:simple-bodies", "-lang:descriptors"]
    },
    {
      "name": "unittest",
      "sources": [
        "src/engine/*.v3",
        "src/engine/compression/*.v3",
        "src/engine/objmodel/*.v3",
        "src/engine/objmodel/boxed/*.v3",
        "src/engine/v3/*.v3",
        "src/util/*.v3",
        "test/unittest/*.v3",
        "test/wasm-spec/*.v3",
        "test/unittest.main.v3"
      ],
      "virgilDependencies": ["lib/util/*.v3", "lib/test/*.v3"],
      "compilerArgs": ["-lang:fun-exprs", "-lang:simple-bodies", "-lang:descriptors"]
    }
  ]
}
```

The [fixture projects](../test/fixtures/projects/) are smaller, complete examples.

## File format

The file is JSON as in [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259), encoded in UTF-8. Comments and trailing commas aren't allowed. A UTF-8 byte order mark at the start is ignored. A file may be at most 262,144 bytes (256 KiB), and it has the server's other JSON limits: arrays and objects may nest at most 256 deep, and the file may hold at most 50,000 values. A file that lists patterns, not files, is far smaller.

### Top level

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `version` | integer | Yes | The format version. Must be `1`. |
| `projects` | array of project objects | Yes | The programs, in order. It may be empty: documents under the file then stay in [single-file mode](#without-a-project-file). |

### Project

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | Required | Names the project in messages. One or more ASCII letters, digits, `.`, `_`, or `-`. Names are case-sensitive and must be unique within the file. |
| `sources` | array of [patterns](#patterns) | Required | The files the project owns, relative to the directory that holds the project file. At least one pattern. |
| `dependencies` | array of patterns | `[]` | Files compiled with the project that it doesn't own, such as a vendored library, relative to the directory that holds the project file. |
| `virgilDependencies` | array of patterns | `[]` | Files from Virgil's own sources, relative to the [Virgil root](#the-virgil-root), such as `lib/util/*.v3`. |
| `exclude` | array of patterns | `[]` | Files removed from `sources` and `dependencies`, relative to the directory that holds the project file. |
| `compilerArgs` | array of strings | `[]` | [Language options](#compiler-flags) for parsing and type checking. |
| `target` | `null` | `null` | [Reserved](#target). Version 1 accepts only `null`. |

All files selected by `sources`, `dependencies`, and `virgilDependencies` form one program. The files selected by `sources` are the project's *members*. When a file is a member of one project and only a dependency of another, the server uses the distinction to choose its active project. It also uses it to decide which closed files get diagnostics. *(planned)*

### Patterns

A pattern is a relative path whose segments are separated by `/`. Its segments may contain wildcards:

| Wildcard | Matches |
| --- | --- |
| `*` | Zero or more characters within one segment |
| `?` | Exactly one character within one segment |
| `**` | Zero or more whole segments. It must be a whole segment by itself, as in `src/**/*.v3`. |

- A wildcard never matches a name that starts with `.`. Write the dot to match one, as in `.github/*.v3`. `**` therefore doesn't enter directories such as `.git`.
- Matching is case-sensitive. A character is one UTF-8 encoded code point.
- Patterns in `sources`, `dependencies`, and `virgilDependencies` must end in `.v3`. `exclude` patterns may match any file, so `build/**` excludes everything under `build/`.
- These are errors: an empty pattern, an absolute path (`/x.v3`), a backslash, an empty segment (`a//b.v3`, or a trailing `/`), a `.` or `..` segment, `**` inside a segment (`a**b`), and control characters. `[`, `]`, `{`, and `}` are reserved for later syntax.

*(planned)* Expansion selects regular files only:

- `exclude` is matched against paths relative to the project file's directory. It removes files from `sources` and `dependencies`, but not from `virgilDependencies`, which list exactly the library files a project uses.
- The program's files are those of `sources`, then `dependencies`, then `virgilDependencies`. Each pattern adds its matches in byte order of their paths, in the order the patterns are listed. A file that an earlier pattern already selected keeps its first place, and a file in both `sources` and `dependencies` is a member.
- Limits on the number and total size of selected files, how symbolic links are followed, and what happens when a pattern matches nothing are specified with expansion ([#55](https://github.com/SaltedTan/virgil-language-server/issues/55)).

### The Virgil root

`virgilDependencies` name files in a Virgil checkout or installation: the directory that holds Virgil's `lib/`, `rt/`, and `bin/`. Patterns are relative to that directory, as in `aeneas/DEPS`, which lists `lib/util/*.v3` and `lib/asm/x86-64/*.v3`. Other paths to the library need rewriting: `$VIRGIL_LIB/util/*.v3` in a build script, or `../../lib/util/*.v3` in the `DEPS` file of one of Virgil's apps, becomes `lib/util/*.v3`.

A project file can't name the Virgil root, because its location differs from machine to machine. *(planned)* The server takes the root from the first of these that is set:

1. `virgilRoot` in the client's `initializationOptions`: an absolute path, set in the editor.
2. The `VIRGIL_LOC` environment variable of the server process, which Virgil build scripts such as Wizard engine's `build.sh` also use.
3. The parent of the directory that holds `v3c` on `PATH`, which those scripts also fall back to. The server only looks for the file and never runs it.

The server logs which root it uses. If none is found, a project with `virgilDependencies` gets a configuration diagnostic.

A repository that includes Virgil, for example as a submodule, can list the library in `dependencies` instead: `"vendor/virgil/lib/util/*.v3"`.

### Compiler flags

`compilerArgs` accepts only the pinned compiler's language options, which change how Aeneas parses and type-checks a program:

| Flag | Default | Notes |
| --- | --- | --- |
| `-lang:fun-exprs` | `true` | Setting it to true also sets `-lang:simple-bodies` to true. |
| `-lang:simple-bodies` | `true` | |
| `-lang:legacy-infer` | `true` | |
| `-lang:descriptors` | `false` | Unstable builds only |
| `-lang:open-types` | `false` | Unstable builds only |
| `-lang:read-only-arrays` | `false` | Unstable builds only |
| `-lang:covariant-arrays` | `false` | Unstable builds only |

The server's pinned Aeneas is an unstable build (`Version.UNSTABLE`), so it accepts all of these. A unit test keeps this list equal to the pinned compiler's language options. A Virgil update that adds or removes one changes it.

- Write a flag as `-lang:<name>`, which sets it to true, or as `-lang:<name>=true` or `-lang:<name>=false`. Any other value is an error, although `v3c` would read it as false.
- Each option may appear once per project. Flags apply in the order given, as in `v3c`.
- Every other flag is an error, including real `v3c` flags such as `-O2`, `-heap-size`, `-target`, `-redef-field`, `-rt.files`, and `-run`. They select compiler actions, targets, outputs, code generation, the runtime, or initial field values, which analysis doesn't use. Copy only the `-lang:` flags from a build script.

*(planned)* Aeneas keeps these options in process-wide state (`CLOptions`). The analysis worker sets a project's flags before each of its analyses. The server's single-file parser uses the flags of a document's active project, so that syntax diagnostics and document symbols agree with whole-program analysis. A document in no project uses the defaults.

### Target

In version 1, `target` must be `null` or absent. Every project is analyzed without a target, as `v3c` does for its interpreter. Aeneas then supplies a synthetic `System` component, and machine-level types such as `Pointer` don't exist. A version 1 project can therefore describe a target-independent build, like the [example](#example). Code written for a native target, such as Wizard engine's `src/engine/x86-64` or Virgil's `rt/` directories, reports unresolved types. Per-target analysis is later work ([ROADMAP](../ROADMAP.md#workspace-and-project-model)), and a later version may accept target names.

## Configuration diagnostics

Each problem in a project file is reported as a configuration diagnostic. None is ignored. A file with any diagnostic defines no projects: the documents it governs stay in single-file mode until it is fixed.

| Code | Reported for |
| --- | --- |
| `InvalidJson` | Text that isn't JSON, or a file over the size, nesting, or value limits |
| `UnsupportedVersion` | A `version` other than 1. Nothing else is checked, because a later version may define other fields. |
| `UnknownField` | A field this version doesn't define, such as `virgilRoot` or `$schema` |
| `DuplicateField` | A field that appears twice in one object, even if one spelling uses JSON escapes |
| `MissingField` | A required field that is absent |
| `WrongType` | A value of the wrong JSON type, including `null` for an optional array |
| `InvalidValue` | A `name` with other characters, an empty `name` or `sources`, or a non-null `target` |
| `DuplicateProjectName` | A `name` used by an earlier project |
| `InvalidPattern` | A pattern that breaks the [pattern rules](#patterns) |
| `UnsupportedCompilerFlag` | A `compilerArgs` entry that isn't a language option, or that repeats one or gives it a value other than true or false |

Each diagnostic has a range of bytes in the file. It covers the offending value, or a field's name for `UnknownField` and `DuplicateField`. `MissingField` points at the opening brace of the object that lacks the field. `InvalidJson` points where parsing stopped, or at the start of a file that is too large. Diagnostics come in the file's order, at most 100 of them.

`virgil-lsp check-config <file>...` prints them as `file:line:column: Code: message`, with one-based lines and columns, and columns counted in characters. It exits with status 0 if every file is valid, 1 if any isn't, and 2 for a usage error.

*(planned)* The server publishes them with `textDocument/publishDiagnostics` for the project file's URI, with `source: "virgil-lsp"`, the code, error severity, and LSP ranges converted from the byte ranges. It replaces them when the file changes, and clears them when it is fixed or deleted. The file needn't be open in the editor, so the server also sends one `window/showMessage` warning each time a project file becomes invalid, because its projects' semantic features stop working.

## Security

Nothing in a project file can make the server download or execute code:

- No field names a program, a command, a URL, or an environment variable, and unknown fields are errors.
- Patterns can't leave the project file's directory or the Virgil root: absolute paths and `..` are errors, and the file can't choose the Virgil root.
- `compilerArgs` accepts only language options. Compiler actions (`-run`, `-test`), targets, output paths, runtime files, and field redefinitions are rejected.
- Analysis only parses and type-checks ([ADR-0002](decisions/0002-pinned-virgil-adapter-boundary.md#decision)). It never initializes, compiles, or runs the program.

The file does decide which files the server reads. *(planned)* Open editor buffers override the files on disk.

## Without a project file

The server falls back to single-file mode: parsing, syntax diagnostics, and document symbols. *(planned)* It also reports once that semantic features are limited.
