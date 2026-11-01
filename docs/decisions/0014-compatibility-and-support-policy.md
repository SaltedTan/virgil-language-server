# 0014. Supported Virgil, library, and editor combinations

- Status: Accepted
- Date: 2026-11-01
- Discussion: [#340](https://github.com/SaltedTan/virgil-language-server/issues/340)

## Context

[ADR-0002](0002-pinned-virgil-adapter-boundary.md) builds the server against one exact Virgil revision, and [ADR-0007](0007-bundled-virgil-library.md) ships that revision's `lib/` beside the executables. [ADR-0012](0012-project-configuration-stability.md) says what server 1.x keeps the same in a version 1 project file. The [compatibility page](../compatibility.md) records each release's pin and which extension versions work with which servers, but nothing says which combinations a release supports, how a change to them is announced, or how long a release gets fixes. The [roadmap](../../ROADMAP.md#v10-is-ready-when) makes a documented compatibility policy a v1.0 gate.

"Virgil version" means several things that change separately, and treating them as one leads to wrong expectations. For example, a client-supplied Virgil root selects library sources, but it doesn't change the compiler that analyzes them.

## Decision

1. **One exact supported Virgil pin per server release.** Each release supports the one revision it was built with. Upstream `master` is advisory: the scheduled `virgil-master` workflow reports breakage early and blocks nothing. The server has no runtime compiler switching, and there is no matrix of supported revisions.
2. **Six layers, each with its own rule:**
   - (a) **Embedded compiler sources.** The Aeneas front end and the Virgil libraries it needs, from `vendor/virgil` at the pin, are compiled into `virgil-lsp` and `virgil-lsp-worker`. They are fixed when the server is built, and nothing at run time changes them. `virgil-lsp --version` prints the pin as `virgil revision`.
   - (b) **Analyzed source and `compilerArgs`.** A program is parsed and type-checked as the pinned front end does it, with the `-lang:` options of that compiler ([ADR-0012](0012-project-configuration-stability.md)). Source that the pinned compiler accepts is supported. Source written for another revision, older or newer, is analyzed as the pinned compiler sees it, with no promise. The project doesn't keep analyzing older Virgil dialects: Virgil's language still changes, and a user with older code keeps a release whose pin accepts it.
   - (c) **Library root.** The bundled `virgil/` beside the executables is used only when its `REVISION` matches the pin, so the bundle is always the supported library. An explicit client root, `initializationOptions.virgilRoot`, which the clients take from their setting or `VIRGIL_LOC`, takes precedence ([ADR-0007](0007-bundled-virgil-library.md)). It only selects the files that `virgilDependencies` reads; the embedded compiler and the libraries compiled into the server stay the pin's. A root at the pinned revision is supported. The server doesn't check the revision of an explicit root, so it neither rejects nor supports a root at any other revision: that is best effort.
   - (d) **Matching worker.** The server uses only a worker from its own build: the handshake rejects another worker protocol, server version, or source revision. The worker protocol is internal and may change in any release. Keeping the server and worker of one build together is mandatory.
   - (e) **Server and extension versions.** The server follows Semantic Versioning, and the VS Code extension has its own independent version. A platform VSIX supports the server, worker, and library it bundles. With an external server, the [extension table](../compatibility.md#editors) says which server versions each extension version works with.
   - (f) **Editors and hosts.** The [editor table](../compatibility.md#editors) gives each editor's minimum version. Host platforms stay as [ADR-0003](0003-supported-platforms.md) and the [platform table](../compatibility.md#host-platforms) state them.
3. **Fixes go on the current minor line only.** A routine fix ships in the next release of the latest minor line. Older releases stay downloadable, archives, VSIX files, manifests, and checksums included, so a user can keep the release whose pin matches their code. The project doesn't backport fixes, keep a long-term-support line, or maintain a second compiler branch.
4. **Editor minimum versions may rise in a minor release**, with a notice in `CHANGELOG.md`. A patch release doesn't raise them.
5. **Security or correctness exceptions are at the maintainer's discretion.** For such a fix, the maintainer may backport it, publish a release on an older line, or make a change that this policy would otherwise leave to a later release. An exception sets no precedent and promises no further fixes on that line.
6. **Each change is announced in `CHANGELOG.md`**, whose release section becomes the release notes: a pin change, with the old and new revisions; a removed or changed language option, by name, as ADR-0012 requires; an editor minimum increase; and a change to the public command line (`--stdio`, its `--max-message-bytes` option, `--version`, and `--help`), the documented initialization options, the client settings, or the advertised capabilities. From 1.0, an intentional incompatible change to the command line or the initialization options needs a new server major version, and one to a client setting a new major version of the extension; project files follow ADR-0012. Before 1.0, a prerelease may make one, and its notes say so.
7. **Outside the guarantee:** the development commands that `--help` lists as unstable (`parse`, `analyze`, `repair`, and their options), the worker policy options, the [development stdio requests](../development.md#development-stdio-requests), the worker protocol, and the wording of logs and messages. They may change in any release.
8. **Evidence is labeled.** The compatibility tables record what each release was built with and what was tested. A row isn't a claim that every combination in it was tested: a combination counts as verified only when a named automated test or a recorded result covers it.

[Support policy](../compatibility.md#support-policy) states this for users, and CONTRIBUTING's [pin-update steps](../../CONTRIBUTING.md#updating-the-pinned-virgil-revision) and the [release runbook](../releasing.md#making-a-release) check that every record of the pin agrees.

This builds on ADR-0002, ADR-0007, and ADR-0012, and doesn't supersede them.

## Consequences

- A user can find a release's exact pin in `virgil-lsp --version`, the [Virgil revision table](../compatibility.md#virgil-revision), the release's manifest (`virgilRevision` and `library.revision`), and its release notes.
- Upstream fixes and language changes reach users only through a release with a new pin. A user who needs an older dialect keeps an older release, without fixes.
- An explicit root at another revision can give results that disagree with the embedded compiler, such as a library declaration that the pinned compiler doesn't accept. The server doesn't detect this.
- The worker's mismatch rejection has no test that sends a handshake from another build. The [support policy](../compatibility.md#support-policy) lists the tests that do cover this policy's paths.
- Raising an editor minimum, such as for a newer `vscode-languageclient` ([#211](https://github.com/SaltedTan/virgil-language-server/issues/211)), waits for a minor release.

## Alternatives considered

- **Switch compilers at run time, or support several pins.** Aeneas's internals aren't a stable API (ADR-0002), so each pin would need its own adapter and tests.
- **Track upstream `master`.** Releases would be unreproducible, and an upstream commit could break any of them.
- **Keep analyzing older Virgil dialects.** Virgil's language is still changing, and doing so would need either several compilers or a server-side dialect layer that drifts from Aeneas.
- **Backport fixes, or keep a long-term-support line.** Each line would need its own release workflow runs, editor tests, and pin. Keeping old artifacts gives users a working older release without that cost.
- **Reject an explicit root at another revision.** A checkout has no `REVISION` file, which only packaging writes, and a user who points at a checkout on purpose, such as one with local changes, would lose a working setup.
