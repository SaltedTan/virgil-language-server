# Releases

A version tag publishes a GitHub prerelease of the server and the VS Code extension through the [release workflow](../.github/workflows/release.yml). This page describes what a release contains, how to make one, what to do when it fails, and how to verify and install it.

## What a release contains

Every release is a GitHub **prerelease**. Before v1.0, use tags such as `vX.Y.Z-alpha.N`. Release assets are:

| Asset | Contents |
| --- | --- |
| `virgil-lsp-<version>-x86-64-linux.tar.gz` | Linux x86-64; WSL 2 on x86-64 Windows on a best-effort basis |
| `virgil-lsp-<version>-x86-64-darwin.tar.gz` | macOS. This is the Intel (`x86-64-darwin`) build, which Apple Silicon runs under Rosetta 2; it is not a native arm64 build ([ADR-0003](decisions/0003-supported-platforms.md)). |
| `virgil-lsp-<version>-<target>.manifest.json` | A copy of each archive's build manifest |
| `virgil-lsp-vscode-<extension version>.vsix` | The VS Code extension, a preview, for every platform. It holds no server. |
| `SHA256SUMS` | The SHA-256 of every other asset, in `sha256sum` format |

Each archive holds one directory, `virgil-lsp-<version>-<target>/`, with:

- `virgil-lsp` and `virgil-lsp-worker`, executable. The server starts the worker from the directory that holds its own resolved path, so the two must stay together.
- `LICENSE` and `THIRD_PARTY_NOTICES.md`, which cover the server and the Virgil compiler and libraries compiled into it.
- `README.md`, short installation instructions.
- `manifest.json`, the build manifest:

| Field | Value |
| --- | --- |
| `manifestVersion` | `1`, the format of this file |
| `name`, `version`, `target` | `virgil-lsp`, the contents of `VERSION`, and the Virgil target, such as `x86-64-linux` |
| `tag` | The tag passed with `--tag`, or `null` when omitted, as in a branch [dry run](#dry-runs) |
| `sourceRevision` | The full commit SHA of the server source |
| `dirty` | `true` only for local builds with `--allow-dirty`; releases are always `false` |
| `virgilRevision` | The full commit SHA of the Virgil checkout compiled into the server |
| `workerHeap` | The analysis worker's heap size, `WORKER_HEAP` in the `Makefile` |
| `compiler` | `path`, the Aeneas binary that compiled the executables, relative to the Virgil checkout when inside it and otherwise absolute, and its `sha256` |
| `sha256` | The SHA-256 of `virgil-lsp` and `virgil-lsp-worker` |

The executables are unsigned and not notarized. Nothing is published to the VS Code Marketplace or a package manager.

The VSIX is the package that `npm run package` builds in `clients/vscode` ([Packaging](../clients/vscode/README.md#packaging)). Its version is the extension's own, from `clients/vscode/package.json`, which is independent of the server's `VERSION` ([versions](../clients/vscode/README.md#versions)); the release notes give both. The VSIX isn't signed: `@vscode/vsce-sign`, which vsce can use to sign packages, is a build tool that packaging never runs and that the project doesn't redistribute ([third-party notices](../THIRD_PARTY_NOTICES.md#vs-code-client-npm)).

## How the workflow works

The workflow runs for every pushed tag that starts with `v`; [Dry runs](#dry-runs) describes manual runs and the pull-request paths that trigger it. Its jobs run in this order, and each needs the previous one to succeed:

1. **Check version.** For a tag, [`scripts/check-release.sh`](../scripts/check-release.sh) requires the tag to be `v` followed by `VERSION`, `VERSION` to be a semantic version, and `CHANGELOG.md` to have a `## [<version>]` section.
2. **Build, test, and package**, on `ubuntu-24.04` (Linux x86-64) and on `macos-15` (Apple Silicon, under Rosetta 2), from the selected commit with submodules. Each runs `make test` as CI does, then [`scripts/package-release.sh`](../scripts/package-release.sh), which refuses a modified checkout or a Virgil checkout other than the pinned one, builds in a new directory, and writes the archive and its manifest. [`scripts/smoke-test-release.sh`](../scripts/smoke-test-release.sh) then extracts the archive in a new temporary directory and runs it there with an empty environment: it checks the files against the manifest, runs `--version`, runs `analyze` on a two-file program both directly and through a symlink, and checks semantic-error reporting directly. Because the build is deterministic, the job also checks that the packaged executables are byte for byte the ones `make test` tested.
3. **Package and test the VS Code extension**, on `ubuntu-24.04`, after both build jobs. It packages the VSIX once, with the same Node.js version and commands as CI (`npm ci`, then `npm run package` with the pinned vsce), and records the file's SHA-256. [`scripts/check-vsix.py`](../scripts/check-vsix.py) checks that the VSIX is a zip file whose entries are canonical relative paths in `extension/`, apart from the two files that vsce writes beside it, and whose `extension/package.json` has the name, publisher, and version of `clients/vscode/package.json`, that it holds the main file and every production package in `package-lock.json` other than optional or platform-specific ones, and that it holds no file that `.vscodeignore` or vsce's own default patterns exclude, matched the way vsce matches them against each file's name before vsce renames it (such as `README.md` to `readme.md`), and no package, at any depth, that the lockfile doesn't list as a production package at that path, such as a development dependency. The script refuses `.vscodeignore` syntax that it doesn't implement, such as braces and character classes (a backslash-escaped bracket or brace is a literal), and reports a malformed lockfile or a damaged VSIX as an error. The job then extracts the server from the Linux archive that the build job produced, without building it again, and runs the [Extension Host test](../clients/vscode/README.md#extension-host-test) with that server and the VSIX installed into an empty extensions directory, under `xvfb-run` as in CI. It checks that the file's SHA-256 hasn't changed and uploads exactly that file.
4. **Write and verify SHA256SUMS** over every file the build and extension jobs produced, after checking that the VSIX still has the SHA-256 recorded when it was packaged, so the attached VSIX is byte for byte the tested one. It then writes the release notes with [`scripts/release-notes.py`](../scripts/release-notes.py): the version's `CHANGELOG.md` section, the archives, the VSIX with the extension version recorded in its `extension/package.json`, and the server and Virgil revisions. GitHub doesn't resolve relative links in a release, so the script rewrites the section's repository links, such as `[Hover](docs/architecture.md#hover)`, to the files at the release's tag, or at the source revision for a dry run. It fails on a link that it can't rewrite or whose file is missing, so a dry run catches broken `CHANGELOG.md` links before the tag. It rewrites only inline links and images without a title or angle brackets, and refuses reference links, reference definitions, and HTML `href` and `src` attributes whatever their target. The notes script reports unreadable, non-UTF-8, malformed, or incomplete build manifests as errors naming the file and problem; missing and invalid fields are reported together for each manifest. It validates the types of the fields it reads, and requires their strings and the VSIX's name, publisher, and version to be non-empty and encodable as UTF-8. It also fails if the manifests disagree about the version, tag, or revisions, or if there isn't exactly one VSIX, named after the name and version it records.
5. **Publish prerelease**, only for a pushed tag. It verifies `SHA256SUMS` again, refuses to continue if the tag already has a release, and creates the prerelease with all assets and the notes.

Only the publish job can write to the repository (`contents: write`); the others can only read. It uses the job's `GITHUB_TOKEN`, with no protected environment configured. Publishing starts only after the build, extension, and checksum jobs succeed, so a VSIX that fails to package, fails its checks, or fails the Extension Host test stops the release as a failed archive does.

Unlike the server's executables, a VSIX isn't reproducible: its zip entries record when it was packaged, so two packages of the same commit differ, as the ones that CI's `vscode-client` and `vscode-extension-host` jobs package do. The release workflow therefore tests and attaches one file, rather than comparing a fresh package with a tested one.

## Making a release

1. Choose the version. Before v1.0 it is a prerelease such as `0.1.0-alpha.1`; raise `N` for each further alpha of the same version.
2. On a branch, prepare the release in one pull request:
   - Set `VERSION` to the version.
   - In `CHANGELOG.md`, move the entries under `## [Unreleased]` into a new section, `## [0.1.0-alpha.1] - YYYY-MM-DD`, and leave an empty `## [Unreleased]` above it. The new section becomes the release notes.
   - If the VS Code extension changed since the last release, raise its version with `npm version --no-git-tag-version <x.y.z>` in `clients/vscode`, which updates `package.json` and `package-lock.json`, so that the release doesn't attach different contents under a version that an earlier release attached ([versions](../clients/vscode/README.md#versions)).
   - In [docs/compatibility.md](compatibility.md), add a row for the version to the Virgil revision table, and update the extension compatibility table if needed.
   - Update [docs/known-limitations.md](known-limitations.md). [Dry-run](#dry-runs) the workflow on the branch, run the [manual tests](manual-testing.md#procedure) with its archives and VSIX, and add their [results](manual-testing.md#results).
3. Merge the pull request, then [dry-run](#dry-runs) the workflow on `main` and read its release notes and manifests, and check the VSIX's version.
4. Tag the merged commit on `main` with an annotated tag, so that `--version` reports the tag as the source revision, and push the tag:

   ```sh
   git switch main && git pull --ff-only
   git tag -a v0.1.0-alpha.1 -m "virgil-lsp 0.1.0-alpha.1"
   git push origin v0.1.0-alpha.1
   ```

5. Follow the run under **Actions**, then open the release and check its assets, notes, and checksums as described in [Verifying a download](#verifying-a-download).

`VERSION` keeps the released version until the next release's preparation pull request. Builds from later commits report the same version, and `--version` tells them apart by their source revision (`v0.1.0-alpha.1-3-g…`).

## When a job fails

Build and checksum failures leave at most a pushed tag behind. A publication failure may also leave a draft release, as described below.

- **Check version** failed: the tag doesn't match `VERSION`, or `CHANGELOG.md` lacks the section. Delete the tag (`git push origin --delete <tag>` and `git tag -d <tag>`), fix `main` through a pull request, and tag again.
- **Build, test, and package**, **Package and test the VS Code extension**, or **Write and verify SHA256SUMS** failed because of the code, the scripts, or the workflow: delete the tag the same way, fix `main`, and tag the fixed commit. A release always comes from a tagged commit that passed every job. If the extension's Extension Host test failed, its log shows the "Virgil Language Server" output channel, which also holds the server's stderr.
- A job failed for a reason outside the repository, such as a runner or network failure: use **Re-run failed jobs** on the run. The re-run uses the same commit.
- **Publish prerelease** failed while uploading: `gh release create` uploads assets to a draft release and publishes it only after every upload succeeds. Delete the leftover draft under **Releases**, then re-run the failed job.
- **Publish prerelease** reports that the tag already has a release: the workflow never replaces one. Once a release is published, people may have downloaded it, so publish a fix as a new version rather than replacing it. If a release must be withdrawn, delete it on GitHub and say why in the next release's notes.

## Dry runs

To try the whole release path without publishing, run the workflow by hand on a branch:

```sh
gh workflow run release.yml --ref main
gh run watch    # choose the new Release run
```

A dry run builds, tests, packages, and smoke-tests both archives, packages and tests the VSIX, and writes and verifies `SHA256SUMS`, but never publishes. On a branch or pull request, the release notes use the `## [Unreleased]` section, and the manifests' `tag` is `null`. Download the result, the same files that a release would attach, the VSIX included, plus `notes.md`, with `gh run download <run-id> --name release`. Each build job also keeps its archive and manifest as `archive-<target>`, and the extension job keeps the tested VSIX as `vsix`.

A dry run from a tag (`--ref v0.1.0-alpha.1`) checks and records the tag and uses that version's changelog section, but still doesn't publish.

A pull request that changes `release.yml`, `check-release.sh`, `check-vsix.py`, `package-release.sh`, `release-notes.py`, or `smoke-test-release.sh`, or the extension's packaging inputs (`clients/vscode/package.json`, `package-lock.json`, and `.vscodeignore`), also dry-runs the workflow, because GitHub only runs a workflow by hand once it is on the default branch. Other changes that could break packaging, such as to the `Makefile`, `scripts/v3c.sh`, or the extension's sources, don't trigger it; CI still packages the VSIX and runs the Extension Host test on every pull request, and you can dry-run `main` after merging them.

To package locally, run the same scripts as the workflow:

```sh
scripts/package-release.sh build/release
scripts/smoke-test-release.sh build/release/virgil-lsp-<version>-<target>.tar.gz
```

To check the VSIX the same way, with the server from a Linux archive, package it in `clients/vscode` and run the Extension Host test against it under a display ([Extension Host test](../clients/vscode/README.md#extension-host-test)):

```sh
mkdir -p /tmp/virgil-release && tar -xzf build/release/virgil-lsp-<version>-x86-64-linux.tar.gz -C /tmp/virgil-release
cd clients/vscode && npm ci && npm run package
../../scripts/check-vsix.py virgil-lsp-vscode-<extension version>.vsix
xvfb-run -a npm run test:e2e -- --vsix virgil-lsp-vscode-<extension version>.vsix \
  /tmp/virgil-release/virgil-lsp-<version>-x86-64-linux/virgil-lsp
```

The packaging script uses the host target and the same environment variables as `make` ([Build and test](development.md#build-and-test)). It refuses uncommitted changes, including untracked files under `src/`, and a Virgil checkout other than the pinned one; `--allow-dirty` permits those inputs and records `"dirty": true` when any are detected. If Virgil's `bin/v3c` launcher has replaced itself with a symlink, the script restores it, since the build doesn't use it. On Linux, the macOS archive can be built for inspection, but not run, with `VIRGIL_V3C=$PWD/vendor/virgil/bin/stable/x86-64-linux/Aeneas V3C_TARGET=x86-64-darwin scripts/package-release.sh build/release`.

## Verifying a download

Download the archive for your platform and `SHA256SUMS` from the release page, or with the GitHub CLI:

```sh
gh release download v0.1.0-alpha.1 --repo SaltedTan/virgil-language-server \
  --pattern 'virgil-lsp-*-x86-64-linux.tar.gz' --pattern SHA256SUMS
```

Then check the archive's line of `SHA256SUMS`:

```sh
grep ' virgil-lsp-0.1.0-alpha.1-x86-64-linux.tar.gz$' SHA256SUMS | sha256sum --check        # Linux
grep ' virgil-lsp-0.1.0-alpha.1-x86-64-darwin.tar.gz$' SHA256SUMS | shasum -a 256 --check   # macOS
```

Both print `OK` for a good download. Check the VSIX the same way, with its own line: `grep ' virgil-lsp-vscode-0.1.0.vsix$' SHA256SUMS | sha256sum --check`. After installing, `virgil-lsp --version` prints the version and the Virgil revision, which match the archive's `manifest.json` and the release notes.

## Installing

Install the server where it runs, which is the machine that holds the files ([Where the server runs](compatibility.md#where-the-server-runs)):

| Setup | Archive | Install on |
| --- | --- | --- |
| Editor on a Linux machine | `x86-64-linux` | That machine |
| VS Code Remote-SSH, or Neovim over SSH | `x86-64-linux` | The remote Linux machine, not the one showing the editor |
| VS Code Remote-WSL, or Neovim inside WSL 2 | `x86-64-linux` | The WSL distribution, not Windows |
| Editor on a Mac, local files | `x86-64-darwin` | The Mac |

Extract the archive and put a symlink to `virgil-lsp` on your `PATH`. The worker stays beside the real file:

```sh
mkdir -p ~/.local/opt ~/.local/bin
tar -xzf virgil-lsp-0.1.0-alpha.1-x86-64-linux.tar.gz -C ~/.local/opt
ln -sf ~/.local/opt/virgil-lsp-0.1.0-alpha.1-x86-64-linux/virgil-lsp ~/.local/bin/virgil-lsp
virgil-lsp --version
```

Make sure `~/.local/bin` is on the `PATH` that the editor sees. If you copy `virgil-lsp` elsewhere, copy `virgil-lsp-worker` beside it too; a wrapper that starts the original server can leave the worker in the original directory.

To upgrade, extract the new archive next to the old one and point the symlink at it. To roll back, point the symlink at the old directory again. To uninstall, remove the symlink and the directory.

### macOS

On Apple Silicon, install Rosetta 2 first:

```sh
softwareupdate --install-rosetta --agree-to-license
```

The executables are unsigned. A web browser marks what it downloads as quarantined, and macOS may then refuse to run them. Download with `curl` or `gh`, which don't set the quarantine attribute, or remove it after extracting:

```sh
xattr -dr com.apple.quarantine ~/.local/opt/virgil-lsp-0.1.0-alpha.1-x86-64-darwin
```

### Editors

The editors start `virgil-lsp --stdio`:

- **VS Code:** install the [extension](#vs-code-extension) from the release's VSIX. It looks up `virgil-lsp` on the extension host's `PATH`, or uses the absolute path in the machine setting `virgil.server.path`. With Remote-SSH or Remote-WSL, the extension runs on the remote side, so set it in the remote settings ([client settings](../clients/vscode/README.md#settings)).
- **Neovim and LazyVim:** the configurations run `virgil-lsp` from `PATH`, or the absolute path you give in `cmd` ([Neovim setup](../editors/nvim/README.md)).

### VS Code extension

The release's `virgil-lsp-vscode-<extension version>.vsix` is the extension that the workflow tested against the release's Linux server. The same VSIX works on every platform. Install it where the server runs, as the [table above](#installing) describes: with Remote-SSH or Remote-WSL, in a window connected to the remote machine or WSL distribution, not on the machine showing the editor. Download it and check it against `SHA256SUMS`, then install it:

```sh
gh release download v0.1.0-alpha.1 --repo SaltedTan/virgil-language-server \
  --pattern 'virgil-lsp-vscode-*.vsix' --pattern SHA256SUMS
code --install-extension virgil-lsp-vscode-0.1.0.vsix
```

or use **Extensions: Install from VSIX...** in VS Code. VS Code doesn't update an extension installed from a VSIX; install the next release's VSIX to upgrade. [Installing from a VSIX](../clients/vscode/README.md#installing-from-a-vsix) covers remote windows, upgrading, and removal, and [compatibility](compatibility.md#editors) records which server versions each extension version works with.

### The Virgil root

The archives don't include Virgil's library sources. A project whose `.virgil-lsp.json` lists `virgilDependencies`, such as `lib/util/*.v3`, needs the editor to send the location of a Virgil checkout as its Virgil root ([The Virgil root](configuration.md#the-virgil-root)). Use a checkout at the Virgil revision that `virgil-lsp --version` prints, so that the libraries match the compiler front end built into the server:

```sh
git clone https://github.com/titzer/virgil.git ~/src/virgil
git -C ~/src/virgil checkout <virgil revision>
export VIRGIL_LOC=~/src/virgil    # or set virgil.virgilRoot in VS Code
```

With Remote-SSH or WSL, the checkout and `VIRGIL_LOC` belong on the remote machine or in the WSL distribution, next to the server.
