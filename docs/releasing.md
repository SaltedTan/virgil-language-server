# Releases

A version tag publishes a GitHub prerelease of the server through the [release workflow](../.github/workflows/release.yml). This page describes what a release contains, how to make one, what to do when it fails, and how to verify and install it.

## What a release contains

Until v1.0 every release is a GitHub **prerelease**, tagged `vX.Y.Z-alpha.N` by default. Its assets are:

| Asset | Contents |
| --- | --- |
| `virgil-lsp-<version>-x86-64-linux.tar.gz` | Linux x86-64, including WSL 2 on x86-64 Windows |
| `virgil-lsp-<version>-x86-64-darwin.tar.gz` | macOS. This is the Intel (`x86-64-darwin`) build, which Apple Silicon runs under Rosetta 2; it is not a native arm64 build ([ADR-0003](decisions/0003-supported-platforms.md)). |
| `virgil-lsp-<version>-<target>.manifest.json` | A copy of each archive's build manifest |
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
| `tag` | The release tag, or `null` for a [dry run](#dry-runs) or a local build |
| `sourceRevision` | The full commit SHA of the server source |
| `dirty` | `true` only for local builds with `--allow-dirty`; releases are always `false` |
| `virgilRevision` | The full commit SHA of the Virgil checkout compiled into the server |
| `workerHeap` | The analysis worker's heap size, `WORKER_HEAP` in the `Makefile` |
| `compiler` | `path`, the Aeneas binary that compiled the executables, relative to the Virgil checkout, and its `sha256` |
| `sha256` | The SHA-256 of `virgil-lsp` and `virgil-lsp-worker` |

The executables are unsigned and not notarized. Nothing is published to the VS Code Marketplace or a package manager. The VS Code extension is versioned separately and isn't part of the release yet; when it is, its package becomes another asset and is listed in `SHA256SUMS`.

## How the workflow works

The workflow runs for every pushed tag that starts with `v`, and by hand as a [dry run](#dry-runs). Its jobs run in this order, and each needs the previous one to succeed:

1. **Check version.** For a tag, [`scripts/check-release.sh`](../scripts/check-release.sh) requires the tag to be `v` followed by `VERSION`, `VERSION` to be a semantic version, and `CHANGELOG.md` to have a `## [<version>]` section.
2. **Build, test, and package**, on `ubuntu-24.04` (Linux x86-64) and on `macos-15` (Apple Silicon, under Rosetta 2), from the tagged commit with submodules. Each runs `make test` as CI does, then [`scripts/package-release.sh`](../scripts/package-release.sh), which refuses a modified checkout or a Virgil checkout other than the pinned one, builds in a new directory, and writes the archive and its manifest. [`scripts/smoke-test-release.sh`](../scripts/smoke-test-release.sh) then extracts the archive in a new temporary directory and runs it there with an empty environment: it checks the files against the manifest, runs `--version`, and runs `analyze` on a two-file program and on a program with a type error, directly and through a symlink. Because the build is deterministic, the job also checks that the packaged executables are byte for byte the ones `make test` tested.
3. **Write and verify SHA256SUMS** over every file the build jobs produced, and write the release notes with [`scripts/release-notes.py`](../scripts/release-notes.py): the version's `CHANGELOG.md` section, the archives, and the server and Virgil revisions. The notes script fails if the manifests disagree about the version, tag, or revisions.
4. **Publish prerelease**, only for a pushed tag. It verifies `SHA256SUMS` again, refuses to continue if the tag already has a release, and creates the prerelease with all assets and the notes.

Only the publish job can write to the repository (`contents: write`); the others can only read. A failure in any job means nothing is published.

## Making a release

1. Choose the version. Before v1.0 it is a prerelease such as `0.1.0-alpha.1`; raise `N` for each further alpha of the same version.
2. On a branch, prepare the release in one pull request:
   - Set `VERSION` to the version.
   - In `CHANGELOG.md`, move the entries under `## [Unreleased]` into a new section, `## [0.1.0-alpha.1] - YYYY-MM-DD`, and leave an empty `## [Unreleased]` above it. The new section becomes the release notes.
   - In [docs/compatibility.md](compatibility.md), add a row for the version to the Virgil revision table, and update the extension compatibility table if needed.
3. Merge the pull request, then [dry-run](#dry-runs) the workflow on `main` and read its release notes and manifests.
4. Tag the merged commit on `main` with an annotated tag, so that `--version` reports the tag as the source revision, and push the tag:

   ```sh
   git switch main && git pull --ff-only
   git tag -a v0.1.0-alpha.1 -m "virgil-lsp 0.1.0-alpha.1"
   git push origin v0.1.0-alpha.1
   ```

5. Follow the run under **Actions**, then open the release and check its assets, notes, and checksums as described in [Verifying a download](#verifying-a-download).

`VERSION` keeps the released version until the next release's preparation pull request. Builds from later commits report the same version, and `--version` tells them apart by their source revision (`v0.1.0-alpha.1-3-g…`).

## When a job fails

Nothing is published unless every job succeeds, so a failed run leaves at most a pushed tag behind.

- **Check version** failed: the tag doesn't match `VERSION`, or `CHANGELOG.md` lacks the section. Delete the tag (`git push origin --delete <tag>` and `git tag -d <tag>`), fix `main` through a pull request, and tag again.
- **Build, test, and package** or **Write and verify SHA256SUMS** failed because of the code, the scripts, or the workflow: delete the tag the same way, fix `main`, and tag the fixed commit. A release always comes from a tagged commit that passed every job.
- A job failed for a reason outside the repository, such as a runner or network failure: use **Re-run failed jobs** on the run. The re-run uses the same commit.
- **Publish prerelease** failed while uploading: `gh release create` uploads assets to a draft release and publishes it only after every upload succeeds. Delete the leftover draft under **Releases**, then re-run the failed job.
- **Publish prerelease** reports that the tag already has a release: the workflow never replaces one. Once a release is published, people may have downloaded it, so publish a fix as a new version rather than replacing it. If a release must be withdrawn, delete it on GitHub and say why in the next release's notes.

## Dry runs

To try the whole release path without publishing, run the workflow by hand on a branch:

```sh
gh workflow run release.yml --ref main
gh run watch    # choose the new Release run
```

A dry run builds, tests, packages, and smoke-tests both archives, and writes and verifies `SHA256SUMS`, but never publishes. The release notes use the `## [Unreleased]` section, and the manifests' `tag` is `null`. Download the result, the same files that a release would attach plus `notes.md`, with `gh run download <run-id> --name release`. Each build job also keeps its archive and manifest as `archive-<target>`.

A dry run from a tag (`--ref v0.1.0-alpha.1`) also checks the tag, but still doesn't publish.

To package locally, run the same scripts as the workflow:

```sh
scripts/package-release.sh build/release
scripts/smoke-test-release.sh build/release/virgil-lsp-<version>-<target>.tar.gz
```

The packaging script uses the host target and the same environment variables as `make` ([Build and test](development.md#build-and-test)). It refuses uncommitted changes, including untracked files under `src/`, and a Virgil checkout other than the pinned one; `--allow-dirty` packages anyway and records `"dirty": true`. If Virgil's `bin/v3c` launcher has replaced itself with a symlink, the script restores it, since the build doesn't use it. On Linux, the macOS archive can be built for inspection, but not run, with `VIRGIL_V3C=$PWD/vendor/virgil/bin/stable/x86-64-linux/Aeneas V3C_TARGET=x86-64-darwin scripts/package-release.sh build/release`.

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

Both print `OK` for a good download. After installing, `virgil-lsp --version` prints the version and the Virgil revision, which match the archive's `manifest.json` and the release notes.

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

Make sure `~/.local/bin` is on the `PATH` that the editor sees. A copy of `virgil-lsp` or a wrapper script elsewhere doesn't work without a `virgil-lsp-worker` beside it.

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

- **VS Code:** the extension looks up `virgil-lsp` on the extension host's `PATH`, or uses the absolute path in the machine setting `virgil.server.path`. With Remote-SSH or Remote-WSL, the extension runs on the remote side, so set it in the remote settings ([client settings](../clients/vscode/README.md#settings)).
- **Neovim and LazyVim:** the configurations run `virgil-lsp` from `PATH`, or the absolute path you give in `cmd` ([Neovim setup](../editors/nvim/README.md)).

### The Virgil root

The archives don't include Virgil's library sources. A project whose `.virgil-lsp.json` lists `virgilDependencies`, such as `lib/util/*.v3`, needs the editor to send the location of a Virgil checkout as its Virgil root ([The Virgil root](configuration.md#the-virgil-root)). Use a checkout at the Virgil revision that `virgil-lsp --version` prints, so that the libraries match the compiler front end built into the server:

```sh
git clone https://github.com/titzer/virgil.git ~/src/virgil
git -C ~/src/virgil checkout <virgil revision>
export VIRGIL_LOC=~/src/virgil    # or set virgil.virgilRoot in VS Code
```

With Remote-SSH or WSL, the checkout and `VIRGIL_LOC` belong on the remote machine or in the WSL distribution, next to the server.
