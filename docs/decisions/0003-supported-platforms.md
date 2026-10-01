# 0003. Supported platforms

- Status: Accepted
- Date: 2026-10-01

## Context

The roadmap left the platform matrix open ("Linux x86-64 and supported macOS hosts, with Windows documented through Remote–WSL"). Testing every possible host would take effort away from the server itself, so the project needs a short list of platforms that are tested and promised.

The maintainer develops on three setups: an x86-64 Linux machine, usually reached over SSH from an Apple Silicon MacBook, and an x86-64 Windows machine using WSL. These are also common setups among developers in general.

The language server runs on the machine that holds the source files, not necessarily the one showing the editor:

| Setup | Where `virgil-lsp` runs | Binary needed |
| --- | --- | --- |
| Editor on a Linux machine | Linux | Linux x86-64 |
| VS Code Remote-SSH, or Neovim over SSH, into Linux | The remote Linux machine | Linux x86-64 |
| VS Code with Remote-WSL, or Neovim inside WSL | Inside WSL 2 | Linux x86-64 |
| Editor on a Mac, editing local files | The Mac | macOS for Apple Silicon |

The pinned Virgil revision can build for `x86-64-linux` and `x86-64-darwin`. It has no `arm64-darwin` target yet, although its arm64 code generator now exists (`arm64-linux`, upstream #797). Apple Silicon Macs can run `x86-64-darwin` programs through Rosetta 2. Apple has said that macOS 27 is the last release with full Rosetta 2 support.

## Decision

Two host platforms are supported. Both are built and tested in CI.

1. **Linux x86-64.** This includes WSL 2 on x86-64 Windows and remote use over SSH. Its CI check is required.
2. **macOS on Apple Silicon.** For now this is the `x86-64-darwin` build running under Rosetta 2. Its CI job runs on GitHub's Apple Silicon `macos-15` runner. It starts as a non-required check, and becomes required before the v0.1-alpha release.

Native `arm64-darwin` is the long-term target for Apple Silicon. Switching to it depends on Virgil adding the target. That change is limited to the build scripts and the release packaging, because of the adapter boundary ([ADR-0002](0002-pinned-virgil-adapter-boundary.md)). A native arm64 macOS binary must be code-signed (at least with an ad-hoc `codesign -s -`), because Apple Silicon won't run unsigned arm64 code.

The editor integrations must work when the server runs remotely. In particular, the VS Code extension must run on the workspace side (`extensionKind: ["workspace"]`), so that Remote-SSH and Remote-WSL start the Linux server next to the files.

Other platforms are not promised:

- **macOS on Intel** is best-effort. It uses the same `x86-64-darwin` build, but isn't tested.
- **Native Windows** is unsupported. Use WSL 2.
- **Linux arm64** is unsupported for now. It may be cheap to add later, since Virgil has an `arm64-linux` target.
- **WSL 1** is unsupported.

## Consequences

- One Linux x86-64 binary serves local Linux, SSH remotes, and WSL 2.
- The Apple Silicon build depends on Rosetta 2 until Virgil supports `arm64-darwin`. If that support hasn't arrived before Rosetta 2 is phased out, the fallback is Virgil's `jar` target, which runs natively anywhere Java is installed. This needs to be validated before it can be relied on.
- Release artifacts (M7) are `virgil-lsp` for Linux x86-64 and for macOS. A single universal macOS binary may make sense once native arm64 exists.
- Path and URI handling must be tested with WSL-style paths (`/mnt/c/...`) and with remote workspaces. These cases belong in the document-store tests.
- Scripts must keep working with macOS's bash 3.2 and BSD command-line tools, since macOS CI runs them.

## Alternatives considered

- **Require native `arm64-darwin` before claiming Apple Silicon support.** That would block macOS support on upstream work with no date. Rosetta 2 gives a tested path now.
- **Use the JVM build everywhere.** It's portable, including to native Windows, but adds a Java dependency, slower startup and more memory use for every user. It stays the fallback, not the default.
- **Support native Windows.** Virgil has no native Windows target, and WSL 2 already covers Windows developers with the Linux build.
