# 0007. Bundled Virgil library and root precedence

- Status: Accepted
- Date: 2026-10-24
- Discussion: [#228](https://github.com/SaltedTan/virgil-language-server/issues/228)

## Context

Projects select Virgil library sources through `virgilDependencies`. Requiring a separate compiler checkout just to use `lib/util` makes a released server harder to install. The analyzer already includes the pinned compiler front end. Library selection must preserve explicit overrides and the existing rule that project traversal never follows symlinks.

## Decision

Release archives keep the server and worker together and add an adjacent `virgil/` directory containing the entire unchanged `vendor/virgil/lib/`, `LICENSE` copied from upstream `rt/LICENSE` (named by Virgil's README), and `REVISION` containing the full pinned SHA followed by LF. They contain no `rt/`, `aeneas/`, or `bin/` sources. Source headers and additional attribution remain intact.

The manifest records the bundle revision, each regular file's SHA-256 relative to `virgil/` (including the license and revision), and a tree digest: SHA-256 of sorted LF-terminated `<sha256>  <path>` lines. Packaging rejects symbolic links and other nonregular entries. Archive ordering, ownership, timestamps, and gzip headers are normalized for reproducibility.

At initialize, a client-supplied `virgilRoot` takes precedence. An absolute physical path is used unchanged apart from URI path normalization. An empty, relative, or non-string value leaves no root and blocks fallback. An unlistable absolute root retains its `SourceIO` diagnostic.

An absent key or JSON `null` means no client root. VS Code sends an empty options object when its setting and `VIRGIL_LOC` are unset; the Neovim configurations omit the Lua table field when the environment value is nil. JSON null is also treated as no value. Empty strings remain explicit invalid overrides at the server boundary.

With no client root, the server tries `virgil/` beside its resolved executable, sharing the worker's directory logic. On Linux `/proc/self/exe` supplies the resolved path; on Darwin the kernel's `proc_pidpath` operation supplies the executable vnode's path. The bundle must have a readable regular `REVISION` matching `BuildInfo.VIRGIL_REVISION` and an accessible physical `lib/` directory. Discovery runs once, during initialize, and logs the chosen path and source, with the revision for a bundle, or the reason no bundle was selected. Later expansion still rejects symlinks at every path component.

There is no environment, PATH, compiler execution, or network discovery in the server. A missing or mismatched bundle leaves no root; projects requiring it retain `MissingVirgilRoot`. The clients retain their environment fallback and forward invalid nonempty overrides so the server cannot silently choose another library.

## Consequences

A released install can analyze projects using `lib/` without a separate checkout, including when moved or launched through a symlink. The whole install must move together. Read-only libraries work because discovery and expansion only read files. Projects selecting sources outside `lib/` still need an explicit full checkout. Development builds without a bundle retain their existing missing-root behavior. Root changes require a server restart.

Release checks verify file types, paths, hashes, license, and pinned revision before extraction. The smoke test uses a moved install with spaces, a read-only bundle, and a symlinked server, proving diagnostics, hover, and definition into bundled sources with no environment or client root. Editor missing-root warning tests use deliberately bundle-less executable copies.

## Alternatives considered

- Shipping only Aeneas's dependencies would exclude valid projects using other libraries. The whole library is small and lets project patterns keep their meaning.
- Searching PATH, executing `v3c`, or reading an environment variable in the server adds unrelated installations and hidden version selection. Environment mapping remains an explicit client responsibility.
- Falling back from invalid client roots would hide configuration errors and silently change the selected library revision.
- Bundling `rt/` and `aeneas/` expands scope beyond the standard library and does not make target-specific analysis supported.
