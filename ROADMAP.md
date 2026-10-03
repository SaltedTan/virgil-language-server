# Virgil Language Server: Greenfield Open-Source Roadmap

_Revised 13 August 2026 for a new open-source GitHub project. Repository observations and external links should be rechecked when implementation begins._

## Executive recommendation

Create a new public repository from an empty Git history under your GitHub account. Use the working repository name `virgil-language-server`, the executable name `virgil-lsp`, and keep the server, VS Code extension, and LazyVim/Neovim setup in one repository through v0.1. This is a new implementation, not a fork of the existing prototype.

Build one editor-independent server executable in Virgil, reuse Aeneas's parser and semantic verifier through a narrow adapter, and keep the VS Code and Neovim/LazyVim integrations thin. Start with full-document synchronization and fresh whole-program analysis; add incremental behavior only after the non-incremental path is correct and measured.

You will be the initial maintainer, but structure the repository as an ordinary open-source project: publish the roadmap, accept issues and pull requests, document contribution expectations, and make technical decisions in public. Additional maintainers can be added later if sustained contribution and project growth make that useful.

The recommended v0.1 is deliberately smaller than a “complete IDE”: correct protocol handling, file synchronization, syntax and semantic diagnostics, document symbols, go-to-definition, hover, and documented setup for LazyVim and VS Code. Completion, references, rename, error-tolerant parsing, and incremental compilation should follow after that foundation. Expect approximately 12–20 part-time weeks to reach a credible v0.1 alpha from an empty repository.

## Open-source project model

| Area                | Initial approach                                                                                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity            | A new, unofficial Virgil tooling project with its own repository, commit history, README, release stream, and issue tracker.                                              |
| Initial maintenance | You triage issues, review changes, merge pull requests, and publish the first releases.                                                                                   |
| Decisions           | Discuss user-facing changes in issues; record durable architectural choices as short ADRs under `docs/decisions/`.                                                        |
| Contributions       | Welcome bug reports, documentation, tests, and code through pull requests governed by `CONTRIBUTING.md` and a code of conduct.                                            |
| Quality controls    | Require automated tests and lint/build checks before merging. Add review requirements once there is another active maintainer.                                            |
| Releases            | Use semantic versioning, GitHub prereleases before v1.0, generated checksums, and a compatibility table for Virgil revisions and host platforms.                          |
| Community growth    | Grant triage or maintainer access gradually to reliable contributors when the workload justifies it; there is no need to design a foundation-style governance system now. |

The project is independent without being isolated. You can cooperate with Virgil maintainers and other tooling authors, report compiler integration problems upstream, and accept compatible contributions while keeping this repository's implementation and release cycle separate.

## What already exists

| Project                                                                                             | Useful assets                                                                                                    | Current constraint                                                                                                          | How to use it                                                                                          |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| [`titzer/virgil`](https://github.com/titzer/virgil)                                                 | Self-hosted compiler; VST; parser; verifier; structured errors and source ranges; JSON library; `vctags` example | It remains an independently governed upstream dependency; whole-program analysis and incomplete editor text need adaptation | Pin an exact revision and access it only through your compiler adapter                                 |
| [`linxuanm/virgil-lsp`](https://github.com/linxuanm/virgil-lsp)                                     | Evidence that a Virgil-native LSP process is feasible; MIT-licensed protocol/lifecycle experiments               | It is a separate project and does not yet provide the target language features                                              | Treat as prior art rather than a starting repository; implement your architecture in the new codebase  |
| [`linxuanm/virgil-vsc`](https://github.com/linxuanm/virgil-vsc)                                     | Shows the likely shape of a thin TypeScript client                                                               | Very early, and no explicit license was visible during this review                                                          | Build your client from the official VS Code API documentation; do not copy this code without a license |
| [`btwj/tree-sitter-virgil`](https://github.com/btwj/tree-sitter-virgil)                             | Evidence that an incremental grammar has been attempted                                                          | Last visible commit was July 2024, and no explicit license was visible during this review                                   | Keep it outside v0.1; use only as a behavioral reference unless licensing is clarified                 |
| Virgil's [`vctags`](https://github.com/titzer/virgil/tree/master/apps/vctags)                       | Demonstrates using `Parser`, `VstFile`, tokens, and declarations in a standalone tool                            | Produces syntactic tags, not resolved semantic references                                                                   | Study it under Virgil's license and write your own small compiler-integration spike                    |
| [`AeneasQueryMode.txt`](https://github.com/titzer/virgil/blob/master/doc/ideas/AeneasQueryMode.txt) | Upstream design ideas for definition, use, type, AST, SSA, and evaluation queries                                | It is an idea document, not a stable public API                                                                             | Use it as design input; your analyzer API and release schedule remain independent                      |

Starting from scratch does not require pretending prior work is invisible. Study the existing MIT-licensed LSP to understand attempted design choices, then implement and test your own architecture against the official specifications and Virgil compiler APIs. If you intentionally copy or adapt licensed code, comply with its notice and attribution terms and record it in `THIRD_PARTY_NOTICES.md`. Do not copy code from repositories without an explicit license.

## Product scope

### v0.1 goal

A user can install the server following the [build and executable placement guidance](README.md#building), open a configured Virgil project in either LazyVim or VS Code, and receive:

- `.v3` file detection;
- parser and type-checker diagnostics for unsaved buffers;
- document symbols;
- go-to-definition for local variables, members, methods, and named types;
- hover text containing declaration signatures and inferred types;
- reliable startup, shutdown, logging, and recovery from malformed requests;
- a project configuration that describes the source files Aeneas must analyze;
- documented installation for the supported platforms: Linux x86-64 (including remote use over SSH), Windows through WSL 2, and Apple Silicon macOS ([ADR-0003](docs/decisions/0003-supported-platforms.md)).

### Later releases

- v0.2: workspace symbols, find references, prepare-rename/rename, signature help.
- v0.3: context-aware completion, better behavior on incomplete syntax, faster reanalysis.
- v0.5: release automation, platform-specific VS Code packages, an upstream `nvim-lspconfig` configuration, compatibility testing.
- v1.0: stable configuration format, defined performance budgets, robust error recovery, completion/references/rename suitable for everyday use, and a documented compatibility policy for Virgil versions.

### Non-goals for the first release

- Code generation, optimization, program initialization, or running user programs.
- A formatter. Formatting is a separate language-design/tooling project.
- Debug Adapter Protocol work; Virgil already contains a separate debugger-extension prototype.
- Reimplementing Virgil's parser or type system in TypeScript, Rust, or Lua.
- Supporting every Aeneas target before the target-independent semantic core works.
- Publishing to Mason before the server has releases and an accepted `nvim-lspconfig` entry. Mason's current admission rules make `nvim-lspconfig` acceptance the realistic route for a small new project.

## Recommended architecture

See the [architecture overview](docs/architecture.md#overview) and [ADR-0004](docs/decisions/0004-analysis-worker-process.md) for the process model and compiler boundary.

### Why keep the server in Virgil

- The compiler adapter can directly use Aeneas's parser, verifier, and bound syntax tree; see the [adapter boundary](docs/decisions/0002-pinned-virgil-adapter-boundary.md).
- It avoids translating or duplicating the language's rapidly changing semantics.
- Aeneas is already self-hosted, so the server can share the compiler's actual semantic model.
- A single native executable is easy for Neovim and VS Code to launch.

The trade-off is that you must implement protocol plumbing yourself and solve distribution for each host platform. That is still preferable to a TypeScript server that repeatedly shells out to `v3c`: a subprocess-only design handles saved diagnostics, but it does not naturally analyze unsaved buffers or expose the bound AST required for definition, hover, references, and rename.

### Compiler front-end map

You do not need to understand Aeneas's optimizer or backends. The relevant path is:

| Stage              | Compiler concept                         | Main Virgil code                                                                                                                                                                     | LSP use                                                         |
| ------------------ | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| Source acquisition | Ordered files plus in-memory input bytes | [`Program`](https://github.com/titzer/virgil/blob/master/aeneas/src/core/Program.v3), `Aeneas.makeProgram`                                                                           | Merge disk files with open unsaved document overlays            |
| Parsing            | Scannerless recursive-descent parser     | [`Parser.v3`](https://github.com/titzer/virgil/blob/master/aeneas/src/vst/Parser.v3), [`ParserState.v3`](https://github.com/titzer/virgil/blob/master/aeneas/src/vst/ParserState.v3) | Syntax diagnostics and raw syntax tree                          |
| Syntax tree        | Virgil Syntax Tree (VST)                 | [`Vst.v3`](https://github.com/titzer/virgil/blob/master/aeneas/src/vst/Vst.v3)                                                                                                       | Declarations, tokens, ranges, statements, expressions, visitors |
| Semantic analysis  | Name resolution and type checking        | [`Verifier.v3`](https://github.com/titzer/virgil/blob/master/aeneas/src/vst/Verifier.v3)                                                                                             | Bound identifiers, inferred types, semantic diagnostics         |
| Errors             | Structured compiler errors               | [`Error.v3`](https://github.com/titzer/virgil/blob/master/aeneas/src/main/Error.v3)                                                                                                  | `textDocument/publishDiagnostics`                               |
| LSP serialization  | JSON values, parser, builder, renderer   | [`JsonParser.v3`](https://github.com/titzer/virgil/blob/master/lib/file/json/JsonParser.v3)                                                                                          | JSON-RPC payloads                                               |

See the [front-end-only adapter contract](docs/decisions/0002-pinned-virgil-adapter-boundary.md#decision) for which compiler phases may run, and [ADR-0004](docs/decisions/0004-analysis-worker-process.md#processes) for where they run.

### Analysis snapshot

Treat each completed analysis as an immutable snapshot containing:

- workspace/project configuration revision;
- document provenance: see the [analysis snapshot contract](docs/architecture.md#analysis-snapshots-partly-present) *(present for stdio submissions)*;
- [server-owned snapshot data](docs/decisions/0004-analysis-worker-process.md#what-crosses-the-boundary), rather than compiler objects;
- diagnostics grouped by URI;
- declaration index: stable symbol key to declaration and source range;
- occurrence index: source range to semantic binding;
- reverse-reference index: declaration key to all occurrences;
- type/signature display cache.

The [analysis snapshot contract](docs/architecture.md#analysis-snapshots-partly-present) owns freshness and planned publication/navigation behavior.

## Important correctness problems to solve first

The existing prototype is a useful skeleton, but its protocol code should be considered untrusted until these cases pass tests:

1. A JSON-RPC response must echo the incoming request's `id`. The current prototype generates a new outgoing ID.
2. IDs may be integers or strings; notifications have no ID and must receive no response.
3. Requests, notifications, responses, and error responses are distinct message shapes. The server also needs a pending-request table before it can issue client requests.
4. `params` may be absent, an object, or an array; some valid messages use `null`.
5. Standard output must contain protocol bytes only. All logs go to standard error or an explicitly selected log file.
6. `Content-Length` is a byte count. Reads and writes may be partial, messages may be fragmented, and a message-size limit is needed.
7. `Content-Type` is optional and, when present, is normally a MIME value with a charset rather than the bare string `utf-8`.
8. Unknown-method handling follows the [JSON-RPC dispatch contract](docs/architecture.md#json-rpc-messages) and [lifecycle state](docs/architecture.md#lifecycle).
9. Lifecycle ordering, `$/cancelRequest`, shutdown, and exit behavior need transcript tests.
10. Compiler locations and LSP locations use different coordinate systems.

For the last item, use the per-document `PositionMap`; [Coordinates](docs/architecture.md#coordinates) owns its conversion rules and known compiler limitations.

## Workspace and project model

Virgil is a whole-program compiler: all participating `.v3` files are passed together. Recursively analyzing every `.v3` file in a repository is unsafe because one repository can contain separate programs, tests with duplicate declarations, and multiple targets.

Introduce a versioned project file such as `.virgil-lsp.json`:

```json
{
  "version": 1,
  "projects": [
    {
      "name": "app",
      "sources": ["src/**/*.v3"],
      "dependencies": ["vendor/lib/**/*.v3"],
      "exclude": ["build/**"],
      "compilerArgs": ["-fun-exprs"],
      "target": null
    }
  ]
}
```

The exact fields should be finalized after studying real Virgil projects and `DEPS`/`TARGETS` usage. Preserve these principles:

- Without a config, provide single-file parsing, syntax diagnostics, and document symbols; clearly report that semantic workspace features are limited.
- With a config, expand the declared globs relative to the config file and analyze exactly that project.
- Open documents override disk content.
- Unknown compiler flags produce a configuration diagnostic rather than silently changing semantics.
- A file that belongs to more than one project gets one analysis context per project, with a deterministic active context.
- `virgilRoot` or compiler-source discovery is explicit and inspectable. Do not silently download or execute code from the workspace.
- Multi-target analysis is later work. First establish one target-independent parse/verify snapshot; add per-target snapshots only for features whose semantics actually differ.

Where possible, align this manifest with Aeneas's emerging query-mode concepts rather than inventing an unrelated build system.

## Milestone roadmap

Estimates below assume one developer working part-time while learning the codebase. They are planning ranges, not deadlines.

### M0 — Project foundation and compiler feasibility (1–2 weeks)

Deliverables:

- Create `<your-account>/virgil-language-server` from an empty repository, not through GitHub's fork workflow.
- Write a concise README containing the problem, v0.1 scope, architecture sketch, build status, project status, and “unofficial project” notice.
- Choose Apache-2.0 for the new code and add `THIRD_PARTY_NOTICES.md` for dependencies or adapted assets.
- Add `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue templates, and a pull-request template.
- Enable Issues, Discussions, and a GitHub Project; create milestones and a small dependency-ordered backlog.
- Record ADR-001 (Virgil-native server) and ADR-002 (pinned Virgil adapter boundary).
- Protect `main` against force-push and deletion and require CI before merges.
- Pin a current Virgil revision as a submodule and make the build fail with an actionable message if it is absent.
- Write a throwaway-to-production compiler spike that parses one file, verifies two files, prints structured errors, and follows one bound identifier to its declaration.
- Add required CI for the pinned dependency and advisory scheduled CI against current Virgil `master`.

Exit criteria:

- The new repository has its own history, identity, license, roadmap, and community documents.
- A fresh Linux checkout with submodules builds and runs the compiler spike with one documented command.
- Every imported asset and dependency has a recorded license and revision.
- A prospective contributor can understand the goal, build the project, run tests, and choose a small issue without private instructions.

### M1 — Correct JSON-RPC/LSP core from scratch (2–4 weeks)

Deliverables:

- Typed message model for requests, notifications, responses, and errors.
- Correct ID preservation and server-initiated request tracking.
- Robust byte framing over stdio, partial I/O handling, and a configurable message-size ceiling.
- Lifecycle state machine following an LSP 3.17-compatible baseline; use capability negotiation rather than assuming one editor.
- `window/logMessage` or file/stderr logging with no stdout contamination.
- Unit and golden-transcript tests for valid and invalid packets.
- Compatibility smoke tests using both the VS Code language client and headless Neovim.

Exit criteria:

- Every request receives at most one response with the original ID.
- Notifications never receive responses.
- Fragmented, consecutive, malformed, Unicode, and oversized messages behave deterministically.
- Both editors can start and stop the server without protocol errors.

### M2 — Document store and syntax-diagnostics slice (2–3 weeks)

Deliverables:

- `initialize`, `initialized`, `didOpen`, `didChange`, `didSave`, and `didClose` handlers. *(present)*
- Full-text synchronization first (`TextDocumentSyncKind.Full`), with document version checks. *(present; stale/out-of-order changes rejected)*
- URI/path normalization and in-memory overlays. *(present; [document-store rules](docs/architecture.md#document-store))*
- Dedicated position/range conversion module.
- Single-file parser adapter for one open document. *(present; [compiler adapter](docs/architecture.md#compiler-adapter))*
- Parser errors mapped to `publishDiagnostics`, including clearing obsolete diagnostics. *(present; [publication contract](docs/architecture.md#parser-diagnostics))*
- `textDocument/documentSymbol` using VST declarations; the `vctags` traversal is a useful model.
- A manually launchable LazyVim config and VS Code development client.

Exit criteria:

- Editing an unsaved file updates syntax diagnostics in both editors.
- Tabs and Unicode position fixtures highlight the intended text.
- Closing or fixing a file clears stale diagnostics.
- No code generation or user-program execution occurs.

### M3 — Project model and semantic diagnostics (3–5 weeks)

Deliverables:

- `.virgil-lsp.json` schema, parser, docs, and config diagnostics.
- Source/dependency glob expansion and workspace-folder support.
- Fresh `Program` construction with all disk sources plus unsaved overlays.
- Parse-and-verify-only compiler adapter.
- Stdio integration of the [analysis worker](docs/architecture.md#analysis-worker), before semantic diagnostics run on unsaved edits.
- `ErrorGen` conversion to diagnostics grouped by document.
- Analysis revision/version gate so stale runs cannot overwrite new results.
- Initial performance measurements against a small project and the Virgil compiler sources.

Integrate the worker before unsaved semantic analysis, keeping the stdio loop responsive while it runs as described in [ADR-0004](docs/decisions/0004-analysis-worker-process.md#first-clients). Analyze on save if necessary. Do not add threads or incremental invalidation until repeatable full analysis works. If on-change whole-program verification is already fast enough, add a short debounce; otherwise keep fast per-file parsing on change and semantic verification on save.

Exit criteria:

- A two-or-more-file project reports unresolved names and type errors from unsaved content.
- Repeating the same analysis produces the same diagnostics and symbol bindings.
- Editing one project cannot leak symbols or diagnostics into another.
- Full analysis timing and memory use are recorded, not guessed.

### M4 — Semantic navigation MVP and v0.1 alpha (3–5 weeks)

Deliverables:

- VST visitor/walker for declarations, type references, statements, and expressions.
- Symbol identities for local variables, fields, methods, compounds, enum cases, layouts, and packings.
- Occurrence index extending the [compiler adapter's current coverage](docs/architecture.md#compiler-adapter) to applications and named type references.
- `textDocument/definition`.
- `textDocument/hover` using declarations and `Expr.effectiveType()`.
- Workspace/document-symbol polishing.
- End-to-end editor tests and a documented manual test matrix.
- First tagged prerelease with checksums and install instructions.

Exit criteria:

- The same server binary supplies diagnostics, symbols, definition, and hover in LazyVim and VS Code.
- Definition results are semantic, not name-only text searches.
- Requests against stale snapshots fail safely or return a newer matching snapshot.
- The alpha has known limitations and a reproducible issue template.

### M5 — References, rename, signature help, and completion (4–8 weeks)

Recommended order:

1. `workspace/symbol` from the existing declaration index.
2. `textDocument/references` from the reverse-occurrence index.
3. `textDocument/prepareRename` and `textDocument/rename`, with conflict checks and versioned workspace edits.
4. `textDocument/signatureHelp` using resolved call targets and method signatures.
5. Basic completion for keywords and in-scope declarations.
6. Member completion based on receiver type.

Completion comes late because users invoke it while code is incomplete. It requires a cursor context, partial syntax recovery, scope reconstruction, and deterministic ranking; it is not merely a list of every identifier.

Exit criteria:

- Rename edits exactly the occurrences bound to one declaration and refuses ambiguous/error states.
- Completion remains useful with a missing identifier or delimiter near the cursor.
- Feature tests cover shadowing, same-named members, generics, components, classes, variants, and enum cases.

### M6 — Error tolerance and incrementality (4–8+ weeks, evidence-driven)

Deliverables may include:

- Last-known-good semantic snapshot with syntax-aware fallback.
- Optional Aeneas `EDITOR` parser mode with synchronization at top-level, member, statement, and delimiter boundaries.
- Synthetic missing nodes/tokens where they improve completion without hiding diagnostics.
- Per-file parse caches and dependency invalidation.
- Further background analysis scheduling improvements if protocol responsiveness needs them; worker integration belongs to M3.
- Cancellation checkpoints and result-version rejection.
- Memory caps and cache eviction.

Consider a licensed, current Tree-sitter grammar as a syntax fallback, not as the semantic authority. If `tree-sitter-virgil` is adopted, first obtain an explicit license, update it to current Virgil grammar, and run it over the compiler/test corpus.

Do not promise “incremental compilation” until measurements identify the bottleneck. Incremental text synchronization, incremental parsing, incremental name resolution, and incremental type checking are separate capabilities.

### M7 — Distribution and ecosystem integration (2–4 weeks, then ongoing)

Deliverables:

- GitHub releases containing supported host binaries and SHA-256 checksums.
- A clear support matrix: Linux x86-64 (also used by WSL 2 and SSH remotes) and Apple Silicon macOS, per [ADR-0003](docs/decisions/0003-supported-platforms.md). Native Windows remains out of scope unless a native or JVM distribution is validated.
- VS Code extension setting for an external server path during development.
- Later, platform-specific VSIX packages or verified binary download/install logic.
- Marketplace README, changelog, privacy statement, and troubleshooting guide.
- `nvim-lspconfig` pull request after the executable has a stable command, release URL, root markers, and documentation.
- Mason registry submission only after its admission requirements are met, most likely through `nvim-lspconfig` approval.

Exit criteria:

- A user does not need a Virgil compiler checkout to install a released server.
- Release artifacts are built by CI from a tag and can be reproduced or traced to exact source and Virgil dependency revisions.
- VS Code and LazyVim installation paths are both tested from clean environments.

## Feature order and source of truth

| Priority | LSP method/capability | Source of truth                        | Main difficulty                               |
| -------- | --------------------- | -------------------------------------- | --------------------------------------------- |
| P0       | lifecycle and stdio   | LSP spec                               | Message shape and state correctness           |
| P0       | full document sync    | document store                         | Versions, URIs, overlays                      |
| P0       | publish diagnostics   | Aeneas `ErrorGen`                      | Project inputs and position conversion        |
| P0       | document symbols      | parsed VST declarations                | Complete ranges and hierarchy                 |
| P0       | definition            | verified bindings                      | Finding the smallest occurrence at the cursor |
| P0       | hover                 | declaration plus inferred type         | Stable readable type/signature rendering      |
| P1       | workspace symbols     | declaration index                      | Ranking and multi-project results             |
| P1       | references            | reverse binding index                  | Reads/writes and synthetic constructs         |
| P1       | rename                | references plus scope validation       | Conflicts, private names, stale edits         |
| P1       | signature help        | resolved call target                   | Nested calls and partial argument lists       |
| P1       | completion            | scope/type context plus partial parser | Incomplete syntax, ranking, latency           |
| P2       | semantic tokens       | verified occurrence kinds              | Delta encoding and overlap policy             |
| P2       | inlay hints           | inferred types/parameters              | Noise policy and performance                  |
| P2       | code actions          | classified diagnostics                 | Safe, mechanically provable edits             |
| Deferred | formatting            | a future canonical formatter           | Language-wide style and comment preservation  |

## Editor integration plan

### LazyVim / Neovim

Neovim 0.11+ can define a custom server with `vim.lsp.config()` and enable it with `vim.lsp.enable()`. No editor-specific language client needs to be written.

During development, register `.v3` and define a local config along these lines:

```lua
vim.filetype.add({ extension = { v3 = "virgil" } })

vim.lsp.config("virgil_lsp", {
  cmd = { "virgil-lsp", "--stdio" },
  filetypes = { "virgil" },
  root_markers = { ".virgil-lsp.json", "DEPS", ".git" },
})

vim.lsp.enable("virgil_lsp")
```

The final project should provide a LazyVim-specific copy-paste file, a minimal `nvim --clean` reproduction config, and troubleshooting commands such as `:checkhealth vim.lsp`. Syntax highlighting can initially reuse or adapt explicitly licensed Virgil syntax assets; semantic features must still come from the LSP.

### Visual Studio Code

Create a thin TypeScript extension using `vscode-languageclient/node`:

- contribute language ID `virgil` and extension `.v3`;
- contribute comment/bracket configuration and a TextMate grammar;
- start the same `virgil-lsp --stdio` executable;
- expose `virgil.server.path`, `virgil.project.config`, and trace/log settings;
- show actionable startup/configuration errors in an output channel;
- stop the client cleanly on deactivation;
- add end-to-end extension-host tests for activation, diagnostics, definition, and hover.

Use an external server path for early development. Once releases are stable, either publish platform-specific VSIX packages containing the matching binary or download a versioned release artifact after explicit verification. VS Code supports platform-targeted extension packages, which is appropriate for a native server.

Do not couple the core server release to the VS Code extension version. Record a compatibility range so either can receive fixes independently.

## Testing strategy

| Layer            | Required tests                                                                                                                           |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Framing          | Fragmented headers/payloads, consecutive packets, CRLF, missing/duplicate length, partial writes, byte-length Unicode, size limit, EOF   |
| JSON-RPC         | Integer/string/null IDs as permitted, request/notification/response distinction, result/error exclusivity, unknown methods, cancellation |
| Lifecycle        | Pre-initialize request, duplicate initialize, shutdown then exit, abnormal exit, client process disappearance                            |
| Position mapping | Tabs, spaces, CRLF, UTF-8, UTF-16 surrogate pairs, zero-length ranges, EOF, multiline ranges                                             |
| Document store   | Open/change/save/close, monotonically increasing versions, stale changes, disk/overlay precedence, URI identity; workspace folders in M3 |
| Compiler adapter | One and many files, parser error, semantic error, clean project, repeat analysis, compiler flags, no initialization/codegen              |
| Symbol index     | Shadowing, private/file scope, fields versus methods, inheritance, type parameters, variants, enum cases, synthetic declarations         |
| LSP features     | Golden request/response fixtures for every advertised capability; never advertise an unimplemented handler                               |
| Editor E2E       | Headless Neovim and VS Code Extension Host against the same fixture workspace                                                            |
| Compatibility    | Pinned Virgil revision as required CI; current Virgil `master` as scheduled advisory CI                                                  |
| Performance      | Cold start, one-file parse, project verify, hover/definition latency, repeated-edit memory growth                                        |

Useful protocol invariants for property tests:

- A notification produces no response.
- A request produces exactly one result or error carrying the same ID.
- Applying an edit and its inverse restores the exact document bytes and position map.
- A result computed from document version _n_ is never published after version _n + 1_ is accepted.
- Every returned range is within its document and round-trips through the position mapper.

Establish performance budgets only after measuring. Reasonable initial objectives to test—not promises—are sub-250 ms server startup, sub-100 ms per-file parsing for ordinary files, and sub-second full verification for a small project on a contemporary laptop. The Virgil compiler itself should be a separate large-workspace benchmark.

## Suggested repository structure

```text
virgil-language-server/
├── src/
│   ├── protocol/       # framing, JSON-RPC messages, lifecycle
│   ├── documents/      # URI, text, version, position maps
│   ├── workspace/      # config, globs, projects, scheduling
│   ├── analysis/       # Aeneas adapter, snapshots, indexes
│   ├── features/       # diagnostics, symbols, hover, definition, ...
│   └── main.v3
├── clients/
│   └── vscode/         # thin extension; optionally separate repo later
├── editors/
│   └── nvim/           # tested example configs, not another client
├── test/
│   ├── protocol/
│   ├── analysis/
│   ├── features/
│   ├── fixtures/
│   └── e2e/
├── docs/
│   ├── architecture.md
│   ├── configuration.md
│   ├── development.md
│   ├── compatibility.md
│   └── decisions/      # numbered architecture decision records
├── vendor/virgil/      # exact, pinned upstream submodule
├── .github/
│   ├── workflows/
│   ├── ISSUE_TEMPLATE/
│   └── CODEOWNERS
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── ROADMAP.md
├── SECURITY.md
├── THIRD_PARTY_NOTICES.md
├── LICENSE
└── README.md
```

Keep this as a monorepo through v0.1 so the server, both editor integrations, fixtures, and release automation change together. Split a client into another repository only if its independent release cadence or contributor base makes that operationally worthwhile.

## GitHub project setup

### Repository and community setup

- Create the public repository in your personal account initially; move it to a GitHub organization later only if multiple maintainers or related Virgil tools make an organization useful.
- Add a clear description, project website/documentation link when available, and topics such as `virgil`, `lsp`, `language-server`, `neovim`, and `vscode`.
- Protect `main`: require pull requests and passing CI, resolve review conversations, and disallow force-push and deletion. While you are the only maintainer, do not require an approval you cannot obtain.
- Use short-lived branches and pull requests for your own work as well as external contributions. This keeps CI and decisions visible.
- Put your handle in `CODEOWNERS` initially and add maintainers by subsystem later. Require code-owner review only after at least two people can satisfy it.
- Use GitHub Discussions for design questions and user support; use Issues for actionable defects and work items; keep the GitHub Project focused on the next one or two milestones.
- Store publishing credentials in protected GitHub environments, enable two-factor authentication, and use least-privilege release tokens.
- Publish changelogs, checksums, exact Virgil dependency revisions, and supported-platform notes with every GitHub release.

### Milestones

- `protocol-foundation`
- `diagnostics-slice`
- `semantic-navigation`
- `v0.1-alpha`
- `rich-language-features`
- `v1.0`

### Labels

- `area/protocol`, `area/documents`, `area/analyzer`, `area/workspace`
- `area/vscode`, `area/neovim`, `area/release`
- `area/docs`, `area/community`, `dependency/virgil`
- `type/bug`, `type/feature`, `type/refactor`, `type/docs`, `type/test`
- `good first issue`, `help wanted`, `blocked/dependency`, `breaking`, `performance`

### First issue backlog, in dependency order

1. Initialize the new repository, license, community files, branch rules, and CI.
2. Record the server-language and compiler-dependency decisions in ADR-001 and ADR-002.
3. Pin Virgil and complete the parse/verify/binding compiler-integration spike.
4. Implement the JSON-RPC envelope model so responses preserve incoming IDs.
5. Harden stdio framing and enforce protocol-only stdout.
6. Add golden transcript tests for requests, notifications, lifecycle, and malformed packets.
7. Implement versioned full-text document synchronization. *(present)*
8. Integrate [`PositionMap`](docs/architecture.md#coordinates) with the document store and diagnostics.
9. Publish parser diagnostics for unsaved single files.
10. Implement document symbols from the VST.
11. Specify `.virgil-lsp.json` and add fixture projects.
12. Build a fresh parse/verify-only `Program` from disk plus overlays.
13. Publish whole-project semantic diagnostics.
14. Build declaration and occurrence indexes from verified bindings.
15. Implement go-to-definition.
16. Implement hover.
17. Add headless Neovim E2E test and LazyVim documentation. *([present](editors/nvim/README.md))*
18. Add VS Code client E2E test and prerelease packaging.

Issues 4–10 are good candidates for small, reviewable pull requests. Do not create every later feature issue in excessive detail before the analyzer design is validated; refine the backlog at each milestone review. Label self-contained documentation and fixture work as `good first issue`, but keep architecture-critical tasks clearly owned and explained.

## Open-source and licensing plan

Use Apache-2.0 for the project's original code. It aligns with Virgil's stated license, is permissive for editor and commercial use, and includes an explicit patent grant. This is a practical project recommendation, not legal advice.

| Material                                                      | Treatment                                                                                                                                                 |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| New server, tests, documentation, and editor integrations     | License under Apache-2.0 and include the license at the repository root.                                                                                  |
| Pinned Virgil submodule                                       | Leave it as a separately licensed upstream dependency and record its exact revision.                                                                      |
| Virgil source intentionally copied or modified                | Preserve required copyright and notice text, mark modifications, and list the material in `THIRD_PARTY_NOTICES.md`. Prefer calling compiler APIs instead. |
| Existing MIT-licensed LSP code intentionally adapted          | Preserve the MIT notice and attribution and record the adapted files. The default plan is still an independent implementation.                            |
| VS Code, grammar, or other assets without an explicit license | Do not copy or redistribute them. Public visibility alone is not permission.                                                                              |
| External pull requests                                        | Accept under the repository's Apache-2.0 terms. Contributors retain credit and copyright in their work.                                                   |

Do not require a copyright-assignment CLA for v0.1; it creates administrative friction without helping the technical milestone. If contributions become substantial, a DCO sign-off can provide an explicit statement that contributors have the right to submit their work. Document the chosen process in `CONTRIBUTING.md` before enforcing it.

Additional policies:

- Mark the project “unofficial” unless the Virgil maintainers explicitly adopt or endorse it.
- Keep dependency versions and license notices reviewable in pull requests.
- Publish a compatibility table covering server version, pinned/tested Virgil revision, LSP baseline, editors, and host platforms.
- Prefer small pull requests with focused tests. Conventional commits are optional; consistent release notes matter more.
- Avoid telemetry in early releases. If added later, make it opt-in and document exactly what is collected.

## Risk register

| Risk                                                 | Consequence                                                             | Mitigation and trigger                                                                                                                                                                 |
| ---------------------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Aeneas parser stops early on incomplete input        | Completion and navigation disappear while typing                        | Keep last good snapshot; add single-file syntax path; design an editor recovery mode after v0.1                                                                                        |
| Whole-program verification is too slow on change     | Editor stalls or diagnostics lag                                        | Measure first; parse on change and verify on save; debounce; tune worker scheduling or caching if thresholds are exceeded                                                              |
| Compiler internals change                            | Frequent breakage after submodule updates                               | Isolate all compiler calls behind one adapter; pin revisions; run scheduled compatibility CI; optionally propose a query API upstream without making your roadmap depend on acceptance |
| Compiler and LSP columns differ                      | Diagnostics/navigation point to wrong text                              | Dedicated byte/display-column/UTF-16 mapper with adversarial fixtures                                                                                                                  |
| Raw protocol implementation is subtly wrong          | VS Code or Neovim disconnects                                           | Transcript tests, both-client smoke tests, strict stdout discipline, never advertise unfinished features                                                                               |
| Multiple programs exist in one repository            | False duplicate declarations and incorrect references                   | Explicit project manifest; single-file fallback; deterministic active project                                                                                                          |
| Unsaved buffers differ from disk                     | Incorrect diagnostics and definitions                                   | Overlay store is always the source of truth; snapshot document-version gates                                                                                                           |
| Native binaries do not cover Windows                 | Poor VS Code installation experience                                    | Document WSL initially; investigate JVM or supported native target later; do not claim unsupported hosts                                                                               |
| Existing related repositories lack explicit licenses | Legal inability to reuse code                                           | Request licenses; use them only as behavioral references until clarified                                                                                                               |
| Independent implementation duplicates prior effort   | Longer initial protocol work and possible community fragmentation       | Keep v0.1 narrow, explain the greenfield scope clearly, cooperate on shared compatibility problems, and avoid antagonistic project comparisons                                         |
| One initial maintainer creates a bus factor of one   | Reviews, security response, and releases pause when you are unavailable | Automate builds/releases, document operations, and add trusted maintainers if the contributor community develops                                                                       |
| Contributor onboarding is too difficult              | Few outside fixes and repeated setup questions                          | Maintain a one-command build/test path, small fixture-based issues, architecture notes, and prompt constructive reviews                                                                |
| Scope expands toward a complete IDE too early        | Core diagnostics and navigation never become reliable                   | Keep completion, formatting, debugging, and deep incrementality outside v0.1; require milestone evidence before expanding scope                                                        |
| Account or release credential loss                   | Official project and package channels become inaccessible               | Use hardware-backed 2FA, securely stored recovery codes, signed tags, least-privilege tokens, and reproducible release automation                                                      |

## Learning path for the compiler front end

Read and experiment in this order; skip SSA, optimization, machine lowering, and code generation until the LSP actually needs them.

1. Read Virgil's [`Compiler Design Overview`](https://github.com/titzer/virgil/blob/master/doc/impl/Compiler.md), stopping after semantic analysis.
2. Read `Compilation.parse()` and `Compilation.verify()` in [`Compiler.v3`](https://github.com/titzer/virgil/blob/master/aeneas/src/main/Compiler.v3).
3. Trace `Parser.parseFile` and `parseToplevelDecl` in `Parser.v3`.
4. Read the declaration, statement, expression, visitor, `VarBinding`, and `AppBinding` types in `Vst.v3`.
5. Read the first part of `Verifier.verify()` to understand how files, scopes, and bindings are built.
6. Read `Error`/`ErrorGen` and the token/file-range utilities.
7. Read the small `vctags` application and build a throwaway tool that prints every declaration and range from one file.
8. Extend the exercise to construct a `Program`, parse and verify two files, and print structured errors.
9. Walk every `VarExpr` after verification and print its `varbind`, inferred type, and target declaration.
10. Convert those ranges to LSP positions and verify them against a tab-indented Unicode fixture.

Those exercises directly become the document-symbol, diagnostics, definition, and hover implementation. They are more useful than reading the entire compiler.

For LSP, read only the relevant sections of the [3.17 specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/) first: base protocol, lifecycle, text synchronization, diagnostics, document symbols, definition, and hover. The official [VS Code Language Server Extension Guide](https://code.visualstudio.com/api/language-extensions/language-server-extension-guide) is a useful client and test reference. The 3.18 specification is current but still described as under development, so implement a conservative interoperable subset and negotiate capabilities.

## First 30 days

### Week 1

- Create the empty `virgil-language-server` repository, initial README, Apache-2.0 license, roadmap, and community documents.
- Configure branch protection, Issues, Discussions, the GitHub Project, and required Linux CI.
- Record ADR-001 and ADR-002 and create the first six foundation/protocol issues.
- Add the pinned Virgil submodule and prove a clean checkout can build the required compiler libraries.

### Week 2

- Complete the one-file parse, two-file verify, structured-error, and binding-query compiler spike.
- Turn the spike into the first tests for the narrow `AeneasAdapter` boundary.
- Write framing and JSON-RPC golden tests directly from the LSP 3.17 specification.
- Implement byte framing, request ID preservation, and request/notification/response distinction.

### Week 3

- Implement initialize/shutdown/exit state handling and route all logs away from stdout.
- Connect minimal VS Code and Neovim development clients to the lifecycle-only server.
- Add a versioned full-text document store.
- Implement URI normalization and connect [`PositionMap`](docs/architecture.md#coordinates) to the document store.

### Week 4

- Implement `didOpen`, `didChange`, and `didClose` with full-text synchronization.
- Use the [single-file compiler adapter](docs/architecture.md#compiler-adapter) on the unsaved overlay.
- Publish/clear syntax diagnostics.
- Implement document symbols with your own VST visitor informed by Virgil's licensed `vctags` example.
- Demo the same edit in LazyVim and VS Code and tag an internal `v0.0.1` checkpoint if the transcript suite is green.

At day 30, review the architecture, contributor setup, and dependency/licensing records. The new repository should contain a lifecycle-correct server and an unsaved-file syntax slice that both editor clients can launch. The next commitment should be the project manifest plus whole-project parse/verify integration, not completion.

## Release gates

### v0.1-alpha is ready when

- The license, contribution guide, code of conduct, security policy, roadmap, and third-party notices are present.
- Every third-party dependency or adapted asset has a compatible license and recorded attribution.
- Linux x86-64 and Apple Silicon macOS build and pass tests from a clean checkout, with both CI checks required.
- Both editors pass lifecycle and feature smoke tests with the same executable.
- Full text sync, syntax/semantic diagnostics, document symbols, definition, and hover work on unsaved multi-file fixtures.
- Tabs and Unicode have explicit position tests.
- The server does not execute user initializers or code generation.
- Advertised capabilities exactly match implemented handlers.
- Installation, configuration, logging, known limitations, and bug reporting are documented.
- A version tag produces a GitHub prerelease containing checksums and exact dependency revisions through documented automation.

### v1.0 is ready when

- The configuration schema and compatibility policy are stable.
- References, safe rename, signature help, and useful completion are shipped.
- Incomplete syntax does not routinely destroy all language intelligence.
- Performance budgets are based on published benchmark fixtures and pass in CI or scheduled runs.
- Release artifacts cover the documented platform matrix and clean-install tests.
- At least one release cycle has tested upgrades, rollback, and compatibility with newer Virgil revisions.

## Reference links

- [Virgil repository](https://github.com/titzer/virgil)
- [Existing Virgil LSP prototype](https://github.com/linxuanm/virgil-lsp)
- [Existing VS Code prototype](https://github.com/linxuanm/virgil-vsc)
- [Tree-sitter Virgil grammar](https://github.com/btwj/tree-sitter-virgil)
- [LSP 3.17 specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)
- [LSP 3.18 specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.18/specification/)
- [VS Code language-server extension guide](https://code.visualstudio.com/api/language-extensions/language-server-extension-guide)
- [VS Code extension publishing](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)
- [Neovim `nvim-lspconfig`](https://github.com/neovim/nvim-lspconfig)
- [LazyVim LSP configuration](https://lazyvim.github.io/plugins/lsp)
- [Mason registry contribution requirements](https://github.com/mason-org/mason-registry/blob/main/CONTRIBUTING.md)
- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)
- [Developer Certificate of Origin 1.1](https://developercertificate.org/)
- [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [GitHub `CODEOWNERS`](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
