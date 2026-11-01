# 0012. Version 1 project configuration stability

- Status: Accepted
- Date: 2026-11-01
- Discussion: [#339](https://github.com/SaltedTan/virgil-language-server/issues/339)

## Context

[ADR-0005](0005-project-file-format.md) defined version 1 of `.virgil-lsp.json`, and the [roadmap](../../ROADMAP.md#v10-is-ready-when) makes a stable configuration format a v1.0 gate. The format has a version number and tests, but no stated promise about what later servers keep the same.

An unqualified promise that every version 1 file stays valid can't be kept. `compilerArgs` accepts the pinned compiler's language options, which [`ProjectCompilerFlags`](../../src/workspace/ProjectCompilerFlags.v3) reads from `CLOptions.langOpt` through `AeneasAdapter.languageOptions()`. ADR-0005 already notes that a Virgil update that adds or removes a language option changes which files are valid. Four of the seven options at the current pin exist only because the pinned Aeneas is an unstable build.

The parser is also strict. An unknown field is an error, and a file with any error defines no projects. A server therefore rejects every file that uses a field it doesn't know.

Three version numbers are involved: the project file's `version`, the server's version, and the pinned Virgil revision.

## Decision

1. **Server 1.x keeps the version 1 format and project selection.** That covers the fields, their types and defaults, the reserved `target` value, strict handling of unknown and repeated fields, the pattern, ownership, exclusion, and symbolic-link rules, and the precedence of the Virgil root, the governing project file, and the active project. Changing any of these for version 1 needs a new server major version.
2. **Language options follow each release's pinned compiler.** Which `-lang:` options exist, their defaults, and what they change belong to the pin, not to the format.
3. **A pin that removes or changes a language option is announced, not versioned.** The release notes say so, and the configuration diagnostic for a file that still lists a removed option names it. That alone doesn't change the format version. An option whose default or meaning changes under the same name produces no configuration diagnostic. The release notes are how users learn of it.
4. **New fields and new accepted values need a new format version.** A server may read that version alongside version 1. Version 1 itself doesn't grow, because a strict server rejects fields that it doesn't know.
5. **Capacity limits may rise in a 1.x release but not fall.**
6. **The strict parser and `target: null` stay.** Relaxing either needs its own approved issue. Per-target analysis and importing target-aware build manifests are outside this contract.

[Stability and evolution](../configuration.md#stability-and-evolution) states the promise for users. It lists each rule with the code that implements it and the tests that check it, and names the rules that have no test yet.

This builds on ADR-0005 and doesn't supersede it.

## Consequences

- A version 1 file that a 1.x release accepts keeps working in later 1.x releases, unless a pin removes a language option that it lists or one of its programs needs more than a documented limit.
- Every change to `src/workspace/` and every Virgil pin update needs a compatibility review. [CONTRIBUTING](../../CONTRIBUTING.md#project-file-compatibility-review) gives the checklist.
- Users find the pin behind each release in the [Virgil revision table](../compatibility.md#virgil-revision) and in `virgil-lsp --version`. They find option changes in the release notes.
- Semantic results, such as type errors, aren't part of the promise. A file that stays valid can still analyze differently under a new pin.
- Some rules have no test of their own yet. The inventory names them.

## Alternatives considered

- **Promise that every version 1 file stays valid forever.** That would freeze the pinned compiler's option set, or require the server to keep options that Aeneas no longer has.
- **Keep the server's own list of language options.** It would drift from the compiler that actually parses and type-checks the program, so an accepted option could do nothing or something else.
- **Raise the format version whenever a pin changes an option.** The file's structure doesn't change, and every user would have to edit the `version` of a file that is otherwise still valid.
- **Add optional fields to version 1.** Earlier servers report them as `UnknownField` and disable the whole file. A new format version makes the same failure explicit as `UnsupportedVersion`.
- **Make unknown fields warnings.** ADR-0005 rejected lenient parsing, and changing that is a separate decision.
