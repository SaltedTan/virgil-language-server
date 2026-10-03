# Configuration

A `.virgil-lsp.json` project file says which source files form each program in a repository. This page specifies version 1 of its format. [ADR-0005](decisions/0005-project-file-format.md) records why the format looks like this.

The parser, bounded source expansion, workspace-folder discovery, and project contexts are implemented (`src/workspace/`). Configuration errors are published on the project file. Whole-program analysis is available through an [unadvertised development request](development.md#development-stdio-requests); automatic semantic diagnostic publication remains later M3 work ([#58](https://github.com/SaltedTan/virgil-language-server/issues/58)). Rules not yet implemented are marked *(planned)*.

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

The file is JSON as in [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259), encoded in UTF-8. Comments, trailing commas, and a UTF-8 byte order mark aren't allowed. A file may be at most 262,144 bytes (256 KiB), and it has the server's other JSON limits: arrays and objects may nest at most 256 deep, and the file may hold at most 50,000 values, counting object keys. A file that lists patterns, not files, is far smaller.

### Top level

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `version` | integer | Yes | The format version. Must be the integer literal `1`; `1.0` and `1e0` are unsupported. |
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

All files selected by `sources`, `dependencies`, and `virgilDependencies` form one program. The files selected by `sources` are the project's *members*. When a file is a member of one project and only a dependency of another, the server uses the distinction to choose its active project. *(planned)* It will also use membership to decide which closed files get semantic diagnostics.

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
- These are errors: an empty pattern, an absolute path (`/x.v3`), a backslash, an empty segment (`a//b.v3`, or a trailing `/`), a `.` or `..` segment, `**` inside a segment (`a**b`), and ASCII control characters (U+0000–U+001F and U+007F). `[`, `]`, `{`, and `}` are reserved for later syntax.

Expansion selects regular files on disk only (open overlays replace their contents at analysis submission):

- `exclude` is matched against paths relative to the project file's directory. It removes matching files and prunes matching directories before inspection or traversal for `sources` and `dependencies`, but not for `virgilDependencies`, which list exactly the library files a project uses.
- The program's files are those of `sources`, then `dependencies`, then `virgilDependencies`. Each pattern adds its matches in byte order of their paths, in the order the patterns are listed. A file that an earlier pattern already selected keeps its first place, and a file in both `sources` and `dependencies` is a member.
- A pattern matching nothing contributes no files and is not an error. Empty expanded programs remain available as contexts but cannot be submitted for analysis.
- Each program may select at most **1,024 files**. Its source bytes, path bytes, and conservative wire metadata allowance must fit in **4 MiB**. File metadata and overlay lengths are checked for the *entire* selection before reading any source contents. Reads are bounded again, so files growing after enumeration cannot bypass admission. Rejected expansion returns no partial source list and reports a configuration diagnostic.
- Traversal is limited to 10,000 directory entries/walk states, depth 64, and 4 MiB of visited path bytes per program. These bounds also stop pathological glob patterns. Workspace discovery has a separate 10,000-entry/depth-64 bound; reaching its limit logs a warning. Opening a document still searches for its nearest configuration.
- A workspace retains at most 128 configuration files, 128 projects, 8,192 source references, 4 MiB of configuration text, and 4 MiB of source path bytes. Capacity failures disable the affected configuration rather than retaining a partial program.

### Symbolic links

Project discovery, configuration reads, expansion, and project source reads **never follow symbolic links**, including links to regular files and links in ancestor directories. Traversal opens each path component relative to an already-open directory descriptor with `O_NOFOLLOW`; it does not rely on a prior `stat` check. Links, FIFOs, sockets, and device files are not sources. A symlink cycle therefore cannot recurse or block. Hidden directories are entered only by explicit dot-prefixed pattern segments, not by `**`.

This deliberately favors bounded, reproducible traversal over support for symlink-based dependency layouts. Use physical paths for workspace folders and the Virgil root. An unreadable or symlinked configuration marker reports `SourceIO` rather than silently falling through to an ancestor configuration. There is no `realpath` canonicalization: the document store retains its lexical URI identity rules, and the explicit-URI development API retains its existing disk-read policy. Files reachable through hard links keep their distinct lexical paths.

### The Virgil root

`virgilDependencies` name files in a Virgil checkout or installation: the directory that holds Virgil's `lib/`, `rt/`, and `bin/`. Patterns are relative to that directory, as in `aeneas/DEPS`, which lists `lib/util/*.v3` and `lib/asm/x86-64/*.v3`. Other paths to the library need rewriting: `$VIRGIL_LIB/util/*.v3` in a build script, or `../../lib/util/*.v3` in the `DEPS` file of one of Virgil's apps, becomes `lib/util/*.v3`.

A project file can't name the Virgil root, because its location differs from machine to machine. Set `virgilRoot` in the client's `initializationOptions` to an absolute physical path. If it is absent or invalid, a project with `virgilDependencies` gets a `MissingVirgilRoot` configuration diagnostic.

*(planned)* Fallback discovery will use `VIRGIL_LOC`, then the parent of the directory containing `v3c` on `PATH`, and log the chosen root. These fallbacks are not implemented yet; the server never runs `v3c` to discover a root.

A repository that includes Virgil, for example as a submodule, can list the library in `dependencies` instead: `"vendor/virgil/lib/util/*.v3"`.

### Compiler flags

`compilerArgs` accepts only the pinned compiler's language options, which change how Aeneas parses and type-checks a program. The names and defaults below are generated from `CLOptions.langOpt` in the [pinned compiler](compatibility.md#virgil-revision), exposed by [`AeneasAdapter.languageOptions()`](../src/analysis/AeneasAdapter.v3):

| Flag | Default | Notes |
| --- | --- | --- |
| `-lang:read-only-arrays` | `false` | Unstable builds only |
| `-lang:covariant-arrays` | `false` | Unstable builds only |
| `-lang:open-types` | `false` | Unstable builds only |
| `-lang:legacy-infer` | `true` |  |
| `-lang:fun-exprs` | `true` | Setting it to true also sets `-lang:simple-bodies` to true. |
| `-lang:simple-bodies` | `true` |  |
| `-lang:descriptors` | `false` | Unstable builds only |

The server's pinned Aeneas is an unstable build (`Version.UNSTABLE`), so it accepts all of these. The parser takes the accepted options from the pinned compiler through `AeneasAdapter.languageOptions()`. A Virgil update that adds or removes one changes it.

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
| `ExpansionLimit` | Source count, traversal (including directory-entry overflow), project count, or retained source-list capacity exceeded |
| `SourceBudget` | Aggregate source bytes, paths, and metadata exceed the analysis budget |
| `SourceIO` | A selected, non-excluded source could not be opened or inspected, a selected file became nonregular, a directory required for traversal could not be listed, or configuration reading exceeded its size/capacity limits |
| `MissingVirgilRoot` | `virgilDependencies` is present without a configured Virgil root |

Each diagnostic has a range of bytes in the file. It covers the offending value, or a field's name for `UnknownField` and `DuplicateField`. `MissingField` points at the opening brace of the object that lacks the field. `InvalidJson` points where parsing stopped, or at the start of a file that is too large. Diagnostics come in the file's order, at most 100 of them.

The server publishes them with `textDocument/publishDiagnostics` for the project file's URI, with `source: "virgil-lsp"`, the code, error severity, and UTF-16 LSP ranges converted from the byte ranges. It replaces them when it observes a change, and clears them when the file is fixed or deleted. The file needn't be open in the editor, so the server also sends one `window/showMessage` warning each time a project file becomes invalid, because its projects' semantic features stop working. Unchanged diagnostic lists are not republished.

## Workspace folders and active projects

`initialize.workspaceFolders` supplies the local workspace roots. A null or absent value falls back to `rootUri`; an empty array explicitly means no roots. Nonlocal/non-file URIs are ignored. Folder paths are normalized, deduplicated, and sorted; client folder order does not influence selection. Dynamic workspace-folder changes are not advertised or handled yet.

The nearest `.virgil-lsp.json` in a document's ancestor directories is its governing file, using the same marker as `editors/nvim/virgil_lsp.lua`. Search stops at the deepest containing workspace folder, or at the filesystem root if no workspace folder contains the document. A nearer empty or invalid configuration does not fall through to a parent's project. Workspace configurations are discovered recursively on first use, excluding hidden directories and symlinks; configurations outside those discovered roots can also be loaded by opening a governed document.

A file selected by several discovered projects has a separate `ProjectContext` (and retained analysis snapshot) for each program. The active one is chosen by:

1. Membership in a project from the nearest configuration.
2. Membership in another discovered project.
3. Being a dependency of a discovered project.

Ties use canonical configuration URI byte order, then project declaration order within that file. No map iteration order or client folder ordering participates. A file with no selected context stays in single-file mode.

Known configurations are refreshed on document updates, project analysis submissions/completions, and development snapshot inspection. Accepted edits to open JSON configuration buffers take precedence over disk. A changed configuration gets a new revision token; all snapshots stamped with its old revision become stale, including analyses that were already pending. Restoring old bytes does not revive those results.

**Follow-up:** dynamic `workspace/didChangeWatchedFiles` registration, background discovery of newly created configurations, and automatic reanalysis are not implemented. External configuration changes are noticed on the next refresh, not immediately. Restart the server to rediscover unopened configurations added elsewhere in the workspace.

## Security

Nothing in a project file can make the server download or execute code:

- No field names a program, a command, a URL, or an environment variable, and unknown fields are errors.
- Patterns can't leave the project file's directory or the Virgil root: absolute paths and `..` are errors, and the file can't choose the Virgil root.
- `compilerArgs` accepts only language options. Compiler actions (`-run`, `-test`), targets, output paths, runtime files, and field redefinitions are rejected.
- Analysis only parses and type-checks ([ADR-0002](decisions/0002-pinned-virgil-adapter-boundary.md#decision)). It never initializes, compiles, or runs the program.

The file does decide which files the server reads. Open editor buffers override selected files on disk.

## Without a project file

The server falls back to single-file mode: parsing, syntax diagnostics, and document symbols. Once per session, when a document has no active project, it logs that semantic features are limited. Configuration discovery itself does not start an analysis worker.
