# Configuration

A `.virgil-lsp.json` project file says which source files form each program in a repository. This page specifies version 1 of its format. [ADR-0005](decisions/0005-project-file-format.md) records why the format looks like this. [Stability and evolution](#stability-and-evolution) says what later servers keep the same.

The parser, bounded source expansion, workspace-folder discovery, and project contexts are implemented (`src/workspace/`). Configuration errors are published on the project file. Configured projects get automatic [semantic diagnostics](architecture.md#semantic-diagnostics), and whole-program analysis is also available through an [unadvertised development request](development.md#development-stdio-requests). Rules not yet implemented are marked *(planned)*.

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

All files selected by `sources`, `dependencies`, and `virgilDependencies` form one program. The files selected by `sources` are the project's *members*. When a file is a member of one project and only a dependency of another, the server uses the distinction to choose its active project. A file's [semantic diagnostics](architecture.md#semantic-diagnostics), open or closed, come from its active project.

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
- A workspace retains at most 128 configuration files, 128 projects, 8,192 source references, 4 MiB of configuration text, and 4 MiB of source path bytes. Explicit preflight releases cached source lists before rebuilding them, so aggregate admission counts selections from the new pass and capacity transfers between configurations take effect together. Capacity failures disable the affected configuration rather than retaining a partial program.

### Symbolic links

Project discovery, configuration reads, expansion, and project source reads **never follow symbolic links**, including links to regular files and links in ancestor directories. Traversal opens each path component relative to an already-open directory descriptor with `O_NOFOLLOW`; it does not rely on a prior `stat` check. Links, FIFOs, sockets, and device files are not sources. A symlink cycle therefore cannot recurse or block. Hidden directories are entered only by explicit dot-prefixed pattern segments, not by `**`.

This deliberately favors bounded, reproducible traversal over support for symlink-based dependency layouts. Use physical paths for workspace folders and the Virgil root. An unreadable or symlinked configuration marker reports `SourceIO` rather than silently falling through to an ancestor configuration. There is no `realpath` canonicalization: the document store retains its lexical URI identity rules, and the explicit-URI development API retains its existing disk-read policy. Files reachable through hard links keep their distinct lexical paths.

### The Virgil root

`virgilDependencies` name files relative to a Virgil root: a directory holding `lib/`, or a full Virgil checkout or installation. Patterns are relative to that directory, as in `aeneas/DEPS`, which lists `lib/util/*.v3` and `lib/asm/x86-64/*.v3`. Other paths to the library need rewriting: `$VIRGIL_LIB/util/*.v3` in a build script, or `../../lib/util/*.v3` in the `DEPS` file of one of Virgil's apps, becomes `lib/util/*.v3`.

A project file can't name the Virgil root, because its location differs from machine to machine. During `initialize`, the server selects the root once and logs its path and source:

1. A client-supplied `initializationOptions.virgilRoot` takes precedence. It must be an absolute physical path. An empty, relative, or non-string value leaves no root and blocks fallback; a project with `virgilDependencies` gets `MissingVirgilRoot`. An absolute root whose required directories cannot be listed reports `SourceIO`.
2. When the key is absent or JSON `null`, the server tries `virgil/` beside its own resolved executable, including when launched through a symlink. The bundle's `REVISION` must match the full Virgil revision compiled into the server. The log includes that revision.
3. A missing, unreadable, or mismatched bundle leaves no root, with a log explaining why. A project requiring `virgilDependencies` then gets `MissingVirgilRoot`; single-file parsing still works.

[Release archives](releasing.md#what-a-release-contains) include the complete pinned `lib/` under `virgil/`. Keep it beside the executables when moving an install. Projects selecting sources outside `lib/` still need a client-supplied checkout. Development builds have no bundle by default. The root stays fixed for the session; changes take effect when the server restarts. The [bundle ADR](decisions/0007-bundled-virgil-library.md) records the decision and rationale.

The editor clients set the option:

- **VS Code:** the machine setting `virgil.virgilRoot`, or, when it is empty, `VIRGIL_LOC` from the extension host's environment. With Remote-SSH or Remote-WSL, that is the remote machine's environment. The client logs the root it sends, and changing the setting restarts the server ([client settings](../clients/vscode/README.md#settings)).
- **Neovim:** `init_options.virgilRoot`, which the shipped configurations take from `VIRGIL_LOC` ([Neovim setup](../editors/nvim/README.md#the-virgil-root)).
- **Helix:** see [The Virgil root](../editors/helix/README.md#the-virgil-root) in the best-effort setup guide.

The server never searches `PATH`, reads `VIRGIL_LOC`, runs `v3c`, or uses the network to find a root. Environment fallback belongs to the clients; executable-relative bundle discovery belongs to the server. Library traversal still rejects symlinks, including a symlinked `virgil/` directory.

A repository that includes Virgil, for example as a submodule, can list the library in `dependencies` instead: `"vendor/virgil/lib/util/*.v3"`.

### Inlay hints

Both hint kinds are on by default. Set `initializationOptions.inlayHints.types` or `initializationOptions.inlayHints.parameterNames` to `false` to turn one off independently:

```json
{"initializationOptions":{"inlayHints":{"types":true,"parameterNames":false}}}
```

VS Code passes the settings `virgil.inlayHints.types` and `virgil.inlayHints.parameterNames` and restarts the server when either changes. Neovim uses `init_options = { inlayHints = { types = true, parameterNames = false } }`; restart its language server after a change. The editor's own inlay-hint switch applies on top. Clients that advertise refresh support receive a refresh only when enabled labels, UTF-16 positions, or link targets change in documents the client has open; a text version or unrelated trailing comment alone does not trigger one. See the [hint rules](architecture.md#inlay-hints), [VS Code settings](../clients/vscode/README.md#settings), and [Neovim setup](../editors/nvim/README.md#inlay-hints).

### Semantic tokens

Semantic tokens are on by default. Set `initializationOptions.semanticTokens` to `false` to turn them off; the server then doesn't advertise them:

```json
{"initializationOptions":{"semanticTokens":false}}
```

VS Code passes its `virgil.semanticTokens` setting, whose default, `auto`, turns them off if the `titzer.virgil` extension is enabled when the server starts, and restarts the server when it changes. Neovim uses `init_options = { semanticTokens = false }`; restart its language server after a change. Clients that advertise refresh support receive a refresh only when an analysis, or a change in which project includes a file, changes the tokens of an open document from those the client was last given for its current text: as when an edit to another file turns a class that it uses into a variant, when a project starts to include it, or when the project analyzes again after a failure that left an edited document without tokens ([refresh rules](architecture.md#semantic-tokens)). See the [token table](architecture.md#semantic-tokens), [VS Code settings](../clients/vscode/README.md#settings), and [Neovim setup](../editors/nvim/README.md#semantic-tokens).

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

Aeneas keeps these options in process-wide state (`CLOptions`). Each analysis of a project sends its flags to the analysis worker, which applies them before parsing and verification and restores the defaults afterwards. Projects with different flags therefore share one worker, and a file in several projects is analyzed with each project's flags in turn. Its semantic diagnostics come from its [active project](#workspace-folders-and-active-projects).

The server parses an open document with the flags of its active project, so that syntax diagnostics and document symbols agree with whole-program analysis. It restores the defaults after each parse. A document in no project, or under a project file with errors, uses the defaults. When a configuration change gives an open document's active project different flags, the server parses the document again and republishes its diagnostics. The development `analyze` command and explicit `uris` in the [development request](development.md#development-stdio-requests) always use the defaults.

### Target

In version 1, `target` must be `null` or absent. Every project is analyzed without a target, as `v3c` does for its interpreter. Aeneas then supplies a synthetic `System` component, and machine-level types such as `Pointer` don't exist. A version 1 project can therefore describe a target-independent build, like the [example](#example). Code written for a native target, such as Wizard engine's `src/engine/x86-64` or Virgil's `rt/` directories, reports unresolved types. Per-target analysis is later work ([ROADMAP](../ROADMAP.md#workspace-and-project-model)), and a later version may accept target names.

## Configuration diagnostics

Each problem in a project file is reported as a configuration diagnostic. None is ignored. A file with any diagnostic defines no projects: the documents it governs stay in single-file mode until it is fixed.

| Code | Reported for |
| --- | --- |
| `InvalidJson` | Text that isn't JSON or exceeds the nesting or value limits, or an open configuration buffer over the file-size limit that fits within configuration retention capacity |
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
| `SourceIO` | A selected, non-excluded source could not be opened or inspected, a selected file became nonregular, a directory required for traversal could not be listed, or configuration could not be read within its size/capacity limits |
| `MissingVirgilRoot` | Nonempty `virgilDependencies` without a root selected by [the root-selection rules](#the-virgil-root) |

Each diagnostic has a range of bytes in the file. It covers the offending value, or a field's name for `UnknownField` and `DuplicateField`. `MissingField` points at the opening brace of the object that lacks the field. `InvalidJson` points where parsing stopped, or at the start of a file that is too large. Diagnostics come in the file's order, at most 100 of them.

The server publishes them with `textDocument/publishDiagnostics` for the project file's URI, with `source: "virgil-lsp"`, the code, error severity, and UTF-16 LSP ranges converted from the byte ranges. It replaces them when it observes a change, and clears them when the file is fixed or deleted. The file needn't be open in the editor, so the server also sends one `window/showMessage` warning each time a project file becomes invalid, because its projects' semantic features stop working. Unchanged diagnostic lists are not republished.

## Workspace folders and active projects

`initialize.workspaceFolders` supplies the local workspace roots. A null or absent value falls back to `rootUri`; an empty array explicitly means no roots. Nonlocal/non-file URIs are ignored. Folder paths are normalized, deduplicated, and sorted; client folder order does not influence selection.

The server advertises `workspace.workspaceFolders` with `changeNotifications: true` and handles `workspace/didChangeWorkspaceFolders`. Removed folders are applied before added ones. Once discovery has run, an added folder is discovered at once, with a fresh discovery budget. A removed folder's configurations are forgotten unless a remaining folder contains them: their projects are retired, and their configuration and semantic diagnostics are cleared. An open document under a removed folder loads its nearest configuration again when it is next used.

The nearest `.virgil-lsp.json` in a document's ancestor directories is its governing file, using the same marker as `editors/nvim/virgil_lsp.lua`. Search stops at the deepest containing workspace folder, or at the filesystem root if no workspace folder contains the document. A nearer empty or invalid configuration does not fall through to a parent's project. Workspace configurations are discovered recursively on first use, excluding hidden directories and symlinks; configurations outside those discovered roots can also be loaded by opening a governed document.

A file selected by several discovered projects has a separate `ProjectContext` (and retained analysis snapshot) for each program. The active one is chosen by:

1. Membership in a project from the nearest configuration.
2. Membership in another discovered project.
3. Being a dependency of a discovered project.

Ties use canonical configuration URI byte order, then project declaration order within that file. No map iteration order or client folder ordering participates. A file with no selected context stays in single-file mode.

Known configuration bytes are refreshed on document updates, [file events](#changes-on-disk), project analysis submissions/completions (including automatic analyses), and development snapshot inspection. Expanded source lists and expansion diagnostics are cached until their configuration changes, a file event reports a created or deleted source, or an explicit project analysis request refreshes them. Ordinary source edits, automatic analyses, inspection, and completion reuse those lists; development project submissions repeat expansion and budget checks before reading sources. Accepted edits to open JSON configuration buffers take precedence over disk. The VS Code client and the shipped Neovim configurations send `.virgil-lsp.json` buffers to the server, so configuration diagnostics follow unsaved edits. A changed configuration gets a new revision token; all snapshots stamped with its old revision become stale, including analyses that were already pending. Restoring old bytes does not revive those results.

### Changes on disk

The server learns about changes made outside the editor, such as a checkout, from `workspace/didChangeWatchedFiles` file events for `**/.virgil-lsp.json` and `**/*.v3`:

- After `initialized`, if the client declares `workspace.didChangeWatchedFiles.dynamicRegistration`, the server asks it to watch both patterns with `client/registerCapability`. The client has 10 seconds to answer. A failure or a missing answer is logged as a warning, and the server falls back as below. A late answer completes nothing.
- The VS Code client also sends events for both patterns from its own watchers (`synchronize.fileEvents`), which needs no request. Events reach the server whichever way they were requested, and a repeated event counts once.
- A created or changed project file is loaded if discovery would find it: inside a workspace folder, not in a hidden directory, and within the depth bound. Then every loaded configuration is refreshed, so fixed, changed, and deleted project files republish their diagnostics, and their projects are analyzed again.
- A created or deleted source, or a deleted directory, expands every project's sources again. A source changed on disk keeps its project's source list. Either way, the project contexts that contained or now contain the file are analyzed after the 200 ms debounce interval, so a burst of events analyzes each project once. A change to a file that is open in the editor is ignored, because its overlay is what gets analyzed.
- Events about nonlocal URIs are ignored. A notification with a malformed event is ignored as a whole and logged.
- Each reported change to a source replaces its disk stamp, so snapshots that read the file from disk stop being current, and an analysis that was running when the event arrived is discarded rather than published (see [Analysis snapshots](architecture.md#analysis-snapshots-partly-present)).

Clients that send no file events keep refreshing on use: configuration bytes are read again on the occasions listed above, closed-source edits are read on the next scheduled analysis, and created or deleted sources are noticed only by an explicit project analysis request or a configuration change. Restart the server to discover unopened configurations added elsewhere in the workspace. Neovim 0.11.6 on Linux declares `didChangeWatchedFiles.dynamicRegistration = false`, so the shipped Neovim configurations are such clients ([Neovim setup](../editors/nvim/README.md#changes-on-disk)).

## Security

Nothing in a project file can make the server download or execute code:

- No field names a program, a command, a URL, or an environment variable, and unknown fields are errors.
- Patterns can't leave the project file's directory or the Virgil root: absolute paths and `..` are errors, and the file can't choose the Virgil root.
- `compilerArgs` accepts only language options. Compiler actions (`-run`, `-test`), targets, output paths, runtime files, and field redefinitions are rejected.
- Analysis only parses and type-checks ([ADR-0002](decisions/0002-pinned-virgil-adapter-boundary.md#decision)). It never initializes, compiles, or runs the program.

The file does decide which files the server reads. Open editor buffers override selected files on disk.

## Without a project file

The server falls back to single-file mode: parsing, syntax diagnostics, document symbols, [folding ranges](architecture.md#folding-ranges), [selection ranges](architecture.md#selection-ranges), and [keyword completion](architecture.md#completion). Once per session, when a document has no active project, it logs that semantic features are limited. Configuration discovery itself does not start an analysis worker.

## Stability and evolution

Three version numbers apply to a project file, and each changes on its own:

- The **project-file format version** is the file's `version` field. This page specifies version 1, the only one. It changes only when what a file can say changes.
- The **server version**, such as `0.1.0-alpha.5`, is what `virgil-lsp --version` prints first. It follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html), and the [changelog](../CHANGELOG.md) section for each release is its release notes.
- The **pinned compiler revision** is the Virgil commit that the server is built with. `virgil-lsp --version` prints it as `virgil revision`, and the [Virgil revision table](compatibility.md#virgil-revision) lists it for each release. It decides which language options `compilerArgs` accepts and what they do.

[ADR-0012](decisions/0012-project-configuration-stability.md) records this boundary and why.

### What server 1.x keeps

From server 1.0 on, every 1.x release reads version 1 files as this page specifies:

- the fields, their types and defaults, the reserved `target` value, and the exact version literal `1`;
- strict parsing: unknown and repeated fields are errors, and a file with any error defines no projects;
- the pattern syntax, and how patterns select, order, own, and exclude files, with no symbolic links followed;
- the Virgil root's precedence, the governing project file, and the choice of each file's active project;
- the configuration diagnostic codes and what each reports.

So a version 1 file that one 1.x release accepts, every later 1.x release accepts too, and it selects the same files with the same active projects, as long as each `compilerArgs` entry is still a language option of the later release's pinned compiler and each program stays within the documented limits. Prereleases before 1.0 follow the same rules. A prerelease that has to change one says so in its release notes.

This doesn't promise the wording of diagnostic messages and log lines, when the server refreshes after a change, or anything about the analysis itself. Type errors and the other semantic results follow the pinned compiler.

### Language options follow the compiler pin

`compilerArgs` accepts the language options of the pinned compiler, read from `CLOptions.langOpt` through [`AeneasAdapter.languageOptions()`](../src/analysis/AeneasAdapter.v3). Which names exist, their defaults, and what they change in parsing and type checking belong to that compiler, not to the format. For example, `-lang:read-only-arrays`, `-lang:covariant-arrays`, `-lang:open-types`, and `-lang:descriptors` exist only because the pinned Aeneas is an unstable build ([Compiler flags](#compiler-flags)).

When a new pin removes or renames a language option, the release's notes say so, and a file that still lists it gets an `UnsupportedCompilerFlag` error on that entry. The error's message names the flag and lists the options that remain, as in `unknown language option '-lang:x'; the language options are -lang:read-only-arrays, …`. To migrate, remove the entry or replace it with the option that took its place. Alternatively, keep using the last release whose pin had it ([Virgil revision table](compatibility.md#virgil-revision)). The format version stays 1.

When a new pin changes an option's default or meaning but keeps its name, the file stays valid and gets no configuration diagnostic. The release's notes describe the change, and its effect shows only in the analysis.

### What a change needs

| Change | What it needs |
| --- | --- |
| The server disagrees with this page | A fix in any release, with a changelog entry |
| A capacity limit is raised | Any 1.x release, with a changelog entry |
| A field is added, a new value is accepted (such as a target name), reserved pattern characters get a meaning, or comments are allowed | A new format version, which a 1.x server may read alongside version 1 |
| A version 1 field is removed, renamed, or given another type or default; validation is tightened; a limit is lowered; or patterns, ownership, exclusion, symbolic links, root precedence, or active-project choice change for version 1 | A new server major version |
| A server stops reading version 1 | A new server major version |
| A pin adds, removes, or changes a language option | A note in the release's changelog; no new format version |

A new field isn't forward-compatible. A server rejects a field that it doesn't know as `UnknownField`, so every earlier server would reject a version 1 file that used it, and that file would define no projects there. A new format version makes that failure explicit: an earlier server reports `UnsupportedVersion` and checks nothing else.

### What stays as it is

- Parsing stays strict. Making unknown fields warnings, or accepting a file with errors in part, needs its own approved issue.
- Version 1 accepts only `null` for `target`, and analyzes every project without a target ([Target](#target)). Programs that need a native target aren't promised to analyze cleanly.
- Per-target analysis, and reading `DEPS`, `TARGETS`, or other target-aware build manifests in place of a project file, are outside this contract. They would need a new format version.

### Inventory

Each rule that the promise covers, the code that implements it, and the tests that check it. *Format* rows are fixed for version 1. *Selection* rows are kept by every 1.x server. *Pin* rows follow each release's pinned compiler. *Limit* rows may rise in a 1.x release but not fall. *Behavior* rows describe when the server reads and refreshes, which may change in 1.x as long as the result for a saved file stays the same. A row marked *gap* has no test of its own.

| Behavior | Production code | Existing tests | Scope |
| --- | --- | --- | --- |
| `version` must be the integer `1`; any other value is `UnsupportedVersion`, and nothing else is checked | [`ProjectConfigValidator.version`](../src/workspace/ProjectConfig.v3), [`ProjectConfigValidator.file`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:version` | Format |
| The top level has `version` and `projects`; any other field is `UnknownField` | [`ProjectConfigValidator.file`](../src/workspace/ProjectConfig.v3), [`ProjectConfigValidator.unknown`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:unknown_fields`, `ProjectConfig:no_code_execution`, `ProjectConfig:no_projects` | Format |
| Project fields, their types, and their defaults (`[]`, and `null` for `target`); unknown project fields are `UnknownField` | [`ProjectConfigValidator.project`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:minimal`, `ProjectConfig:all_fields`, `ProjectConfig:unknown_fields`, `ProjectConfig:fixtures` | Format |
| A missing `version`, `projects`, `name`, or `sources` is `MissingField` at the object's opening brace | [`ProjectConfigValidator.missing`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:missing_fields`, `ProjectConfig:version` | Format |
| Wrong JSON types, including `null` for an optional array, are `WrongType` | [`ProjectConfigValidator.strings`](../src/workspace/ProjectConfig.v3), [`ProjectConfigValidator.projectList`](../src/workspace/ProjectConfig.v3), [`ProjectConfigValidator.projectName`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:wrong_types`, `ProjectConfig:root_not_object` | Format |
| A repeated field is `DuplicateField`, even when spelled with escapes | [`ProjectConfigValidator.firstMember`](../src/workspace/ProjectConfig.v3), [`ProjectJsonParser.parseFile`](../src/workspace/ProjectJson.v3) | `ProjectConfig:duplicate_fields`, `ProjectConfigMaps:project_keys`, `ProjectConfigMaps:file_keys` | Format |
| Project names: ASCII letters, digits, `.`, `_`, `-`; case-sensitive; unique in the file | [`ProjectConfigValidator.projectName`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:project_names`, `ProjectConfigMaps:project_names` | Format |
| `sources` lists at least one pattern | [`ProjectConfigValidator.project`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:empty_sources` | Format |
| `target` must be `null` or absent | [`ProjectConfigValidator.target`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:target` | Format |
| Strict JSON: no comments, trailing commas, or byte order mark | [`ProjectJsonParser.parseFile`](../src/workspace/ProjectJson.v3) | `ProjectConfig:invalid_json`, `ProjectConfig:byte_order_mark` | Format |
| A file with any diagnostic defines no projects; diagnostics come in file order, at most 100 | [`ProjectConfigValidator.result`](../src/workspace/ProjectConfig.v3), [`ProjectConfigValidator.error`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:diagnostic_order`, `ProjectConfig:diagnostic_limit`, `WorkspaceProjects:active_precedence` | Format |
| Configuration diagnostics are published on the project file with `source: "virgil-lsp"` and their code, plus one warning when it becomes invalid; unchanged lists aren't sent again | [`LspProjectDiagnostics.update`](../src/protocol/LspProjectDiagnostics.v3), [`WorkspaceProjects.publish`](../src/workspace/WorkspaceProjects.v3) | `LspProjectAnalysis:configuration_diagnostics`, [`watched-project-file.jsonl`](../test/diagnostics/watched-project-file.jsonl) | Selection (codes); messages not promised |
| Pattern syntax: relative, `/`-separated, no `.` or `..`, `\`, empty segments, or control characters; `**` a whole segment; `[`, `]`, `{`, `}` reserved; source patterns end in `.v3` | [`ProjectPatterns.check`](../src/workspace/ProjectPatterns.v3) | `ProjectConfig:pattern_syntax`, `ProjectConfig:invalid_patterns` | Format |
| `*`, `?`, and `**` match by code point and case-sensitively, and never match a name that starts with `.` | [`ProjectGlobMatcher.segment`](../src/workspace/ProjectGlob.v3), [`ProjectGlobMatcher.matches`](../src/workspace/ProjectGlob.v3) | `ProjectExpansion:glob`, `ProjectExpansion:glob_selects` | Format |
| Files come from `sources`, then `dependencies`, then `virgilDependencies`, each pattern's matches in byte order; a repeated file keeps its first place; a file in `sources` is a member; a pattern matching nothing isn't an error | [`ProjectExpansion.expand`](../src/workspace/ProjectExpansion.v3), [`ProjectExpansion.add`](../src/workspace/ProjectExpansion.v3) | `ProjectExpansion:order_exclude_deduplicate`, `ProjectExpansion:fixtures` | Selection |
| `exclude` prunes `sources` and `dependencies` before inspecting them, but not `virgilDependencies` | [`ProjectExpansion.excluded`](../src/workspace/ProjectExpansion.v3), [`ProjectExpansion.walk`](../src/workspace/ProjectExpansion.v3) | `ProjectExpansion:fixtures`, `ProjectExpansion:unrelated_and_excluded_entries` | Selection |
| No symbolic link is followed; links and special files aren't sources; a symlinked project file is `SourceIO` | [`ProjectFiles.open`](../src/workspace/ProjectFiles.v3), [`ProjectFiles.configMarker`](../src/workspace/ProjectFiles.v3) | `ProjectExpansion:symlinks_and_special_files` | Selection |
| A selected source that can't be read is `SourceIO`, which disables the whole file | [`ProjectExpansion.walk`](../src/workspace/ProjectExpansion.v3), [`WorkspaceProjects.update`](../src/workspace/WorkspaceProjects.v3) | `ProjectExpansion:unreadable_matches` | Selection |
| `virgilDependencies` without a Virgil root is `MissingVirgilRoot` | [`ProjectExpansion.expand`](../src/workspace/ProjectExpansion.v3) | `ProjectExpansion:fixtures`, `LspProjectAnalysis:configuration_diagnostics`, [`lifecycle-initialize-without-virgil-root`](../test/protocol/lifecycle-initialize-without-virgil-root/) | Selection |
| Virgil root precedence: the client's `virgilRoot`, else the bundle with a matching `REVISION`; an invalid client value blocks the fallback | [`WorkspaceProjects.initialize`](../src/workspace/WorkspaceProjects.v3) | [`bundled_library.py`](../test/protocol/bundled_library.py), [`lifecycle-initialize-null-virgil-root`](../test/protocol/lifecycle-initialize-null-virgil-root/), [`lifecycle-initialize-relative-virgil-root`](../test/protocol/lifecycle-initialize-relative-virgil-root/), [`lifecycle-initialize-non-string-virgil-root`](../test/protocol/lifecycle-initialize-non-string-virgil-root/) | Selection |
| Workspace folders, or `rootUri` without them, are normalized, deduplicated, and sorted; an empty array means no folders | [`WorkspaceProjects.initialize`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.addFolder`](../src/workspace/WorkspaceProjects.v3) | `LspProjectAnalysis:initialize_folders`, `WorkspaceProjects:workspace_roots`; *gap*: an empty array | Selection |
| Folder changes apply removals first, discover added folders, and forget configurations that only removed folders held | [`WorkspaceProjects.changeFolders`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:folder_changes`, [`watched-new-project.jsonl`](../test/diagnostics/watched-new-project.jsonl) | Selection |
| The nearest project file governs, searching up to the deepest containing folder; an empty or invalid nearer file doesn't fall through to a parent's | [`WorkspaceProjects.nearest`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.select`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:workspace_roots`, `WorkspaceProjects:active_precedence` | Selection |
| Discovery skips hidden directories and symbolic links; a project file reported by a file event loads only where discovery would find it | [`WorkspaceProjects.discover`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.watched`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:watched_configurations`, `WorkspaceProjects:discovery_overflow` | Selection |
| Active project: membership in the nearest file's project, then membership in another, then dependency; ties by configuration URI, then declaration order | [`WorkspaceProjects.select`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.rank`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.contextBefore`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:active_precedence`, `WorkspaceProjects:shared_contexts`, `LspProjectAnalysis:compiler_args_per_project`; *gap*: membership in another file's project over a dependency alone, and the tie between two files' URIs | Selection |
| A file in several projects has a context for each; its semantic diagnostics come from its active project | [`WorkspaceProjects.owners`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.select`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:shared_contexts`, [`two-projects.jsonl`](../test/diagnostics/two-projects.jsonl) | Selection |
| An open project-file buffer takes precedence over disk; each change gets a new revision, and restoring old text doesn't revive old results | [`WorkspaceProjects.update`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:configuration_revision`, `LspProjectAnalysis:configuration_diagnostics`, `LspProjectAnalysis:contexts_and_revisions` | Behavior |
| An unsaved change to a project's `compilerArgs` parses its open documents again | [`LspParserDiagnostics.reparse`](../src/protocol/LspParserDiagnostics.v3) | `LspProjectAnalysis:compiler_args_change_reparses`, [`compiler-flags.jsonl`](../test/diagnostics/compiler-flags.jsonl) | Behavior |
| Source lists are reused until the configuration changes, a file event creates or deletes a source, or a project analysis request expands them again | [`WorkspaceProjects.update`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.reexpand`](../src/workspace/WorkspaceProjects.v3) | `LspProjectAnalysis:routine_events_reuse_expansion`, `WorkspaceProjects:cached_expansion_preflight` | Behavior |
| File events for project files and sources refresh configurations and the projects they affect | [`LspWorkspaceEvents.didChangeWatchedFiles`](../src/protocol/LspWorkspaceEvents.v3), [`WorkspaceProjects.affected`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:affected_contexts`, [`watched-project-file.jsonl`](../test/diagnostics/watched-project-file.jsonl), [`watched-sources.jsonl`](../test/diagnostics/watched-sources.jsonl) | Behavior |
| `compilerArgs` accepts exactly the pinned compiler's language options | [`ProjectCompilerFlags.isLanguageOption`](../src/workspace/ProjectCompilerFlags.v3), [`AeneasAdapter.languageOptions`](../src/analysis/AeneasAdapter.v3) | `ProjectConfig:compiler_flags` | Pin |
| Flag syntax: `-lang:<name>`, `=true`, or `=false`, each option once, applied in order | [`ProjectCompilerFlags.check`](../src/workspace/ProjectCompilerFlags.v3) | `ProjectConfig:compiler_flags`, `ProjectConfig:invalid_compiler_flags` | Format |
| Other flags, such as `-O2`, `-run`, or `-target`, are `UnsupportedCompilerFlag` | [`ProjectCompilerFlags.check`](../src/workspace/ProjectCompilerFlags.v3) | `ProjectConfig:invalid_compiler_flags`, `ProjectConfig:no_code_execution` | Format |
| An unknown or removed language option is `UnsupportedCompilerFlag` on its entry, and the message names it | [`ProjectCompilerFlags.check`](../src/workspace/ProjectCompilerFlags.v3), [`ProjectConfigValidator.compilerArgs`](../src/workspace/ProjectConfig.v3) | `ProjectConfig:invalid_compiler_flags` | Format |
| Each analysis and parse applies its project's options, then restores the defaults | [`AeneasAdapter.setLanguageOptions`](../src/analysis/AeneasAdapter.v3), [`AeneasAdapter.resetLanguageOptions`](../src/analysis/AeneasAdapter.v3) | `LspProjectAnalysis:compiler_args_per_project`, [`compiler-flags.jsonl`](../test/diagnostics/compiler-flags.jsonl) | Pin |
| A project file holds at most 256 KiB, 256 levels of nesting, and 50,000 values | [`ProjectConfigParser.parse`](../src/workspace/ProjectConfig.v3), [`ProjectJsonParser.parseFile`](../src/workspace/ProjectJson.v3) | `ProjectConfig:size_limit`, `ProjectConfig:nesting_limit`, `JsonRpcJson:parse_default_value_limit`; *gap*: the value limit in a project file | Limit |
| A program selects at most 1,024 files within 4 MiB, checked before any source is read | [`ProjectExpansion.add`](../src/workspace/ProjectExpansion.v3) | `ProjectExpansion:limits_before_reads` | Limit |
| Expansion stops at 10,000 entries, depth 64, or 4 MiB of path bytes, and discovery at 10,000 entries or depth 64 | [`ProjectExpansion.walk`](../src/workspace/ProjectExpansion.v3), [`WorkspaceProjects.discover`](../src/workspace/WorkspaceProjects.v3) | `ProjectExpansion:symlinks_and_special_files`, `WorkspaceProjects:discovery_overflow`; *gap*: the path-byte budget | Limit |
| A workspace keeps at most 128 project files, 128 projects, 8,192 source references, 4 MiB of configuration text, and 4 MiB of source paths | [`WorkspaceProjects.load`](../src/workspace/WorkspaceProjects.v3), [`WorkspaceProjects.update`](../src/workspace/WorkspaceProjects.v3) | `WorkspaceProjects:configuration_capacity_recovery`, `WorkspaceProjects:project_capacity_recovery`, `WorkspaceProjects:aggregate_preflight_transfer`; *gap*: the configuration-text limit | Limit |

`make test` runs all of these. Names such as `ProjectConfig:version` are unit tests, run on their own with `make test-unit ARGS='ProjectConfig:*'`. Files under `test/diagnostics/` are transcripts that `test/diagnostics/run.py` replays, and those under `test/protocol/` run in `test/protocol/run.sh` and `test/protocol/bundled_library.py` ([Tests](development.md#tests)).
