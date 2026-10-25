# Releases

A version tag publishes a GitHub prerelease of the server and the VS Code extension through the [release workflow](../.github/workflows/release.yml). This page describes what a release contains, how to make one, what to do when it fails, and how to verify and install it.

## What a release contains

Every release is a GitHub **prerelease**. Before v1.0, use tags such as `vX.Y.Z-alpha.N`. Release assets are:

| Asset | Contents |
| --- | --- |
| `virgil-lsp-<version>-x86-64-linux.tar.gz` | Linux x86-64; WSL 2 on x86-64 Windows on a best-effort basis |
| `virgil-lsp-<version>-x86-64-darwin.tar.gz` | macOS. This is the Intel (`x86-64-darwin`) build, which Apple Silicon runs under Rosetta 2; it is not a native arm64 build ([ADR-0003](decisions/0003-supported-platforms.md)). |
| `virgil-lsp-<version>-<target>.manifest.json` | A copy of each archive's build manifest |
| `virgil-lsp-vscode-<extension version>-<target>.vsix` | Bundled server, worker, and library for `linux-x64`, `darwin-arm64` (Rosetta 2), or `darwin-x64` |
| `virgil-lsp-vscode-<extension version>.vsix` | The VS Code extension, a preview, for every platform. It holds no server. |
| `SHA256SUMS` | The SHA-256 of every other asset, in `sha256sum` format |

Each archive holds one directory, `virgil-lsp-<version>-<target>/`, with:

- `virgil-lsp` and `virgil-lsp-worker`, executable. The server starts the worker from the directory that holds its own resolved path, so the two must stay together.
- `LICENSE` and `THIRD_PARTY_NOTICES.md`, which cover the server and the Virgil compiler and libraries compiled into it.
- `virgil/lib/`, the entire unchanged pinned Virgil library, `virgil/LICENSE` (from upstream `rt/LICENSE`), and `virgil/REVISION` (the full pinned SHA plus LF). No compiler, runtime, or Aeneas sources are bundled. Keep this directory beside the executables.
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
| `library` | `revision`, `files` (SHA-256s keyed by path relative to `virgil/`, including `LICENSE` and `REVISION`), and `treeSha256` |
| `workerHeap` | The analysis worker's heap size, `WORKER_HEAP` in the `Makefile` |
| `compiler` | `path`, the Aeneas binary that compiled the executables, relative to the Virgil checkout when inside it and otherwise absolute, and its `sha256` |
| `sha256` | The SHA-256 of `virgil-lsp` and `virgil-lsp-worker` |

The bundle tree digest is SHA-256 of LF-terminated `<sha256>  <path>` lines sorted by path relative to `virgil/`, matching the [result-event tree digest](../test/e2e/RESULTS.md#result-events) for regular files. Packaging rejects symlinks and nonregular library entries. Archives sort members and normalize ownership, mtimes to the server commit time, and the gzip header. `scripts/check-release.sh --archive <archive>` verifies the layout, entry types, bundle files and license hashes, tree digest, and pinned revision before extraction.

The executables are unsigned and not notarized. Nothing is published to the VS Code Marketplace or a package manager.

The VSIX files are packaged by `scripts/package-vsix.py`, using `npm run package` in `clients/vscode` ([Packaging](../clients/vscode/README.md#packaging)). Its version is the extension's own, from `clients/vscode/package.json`, which is independent of the server's `VERSION` ([versions](../clients/vscode/README.md#versions)); the release notes give both. The VSIX isn't signed: `@vscode/vsce-sign`, which vsce can use to sign packages, is a build tool that packaging never runs and that the project doesn't redistribute ([third-party notices](../THIRD_PARTY_NOTICES.md#vs-code-client-npm)).

## How the workflow works

The workflow runs for every pushed tag that starts with `v`; [Dry runs](#dry-runs) describes manual runs and the pull-request paths that trigger it. Its jobs run in this order, and each needs the previous one to succeed:

1. **Check version.** For a tag, [`scripts/check-release.sh`](../scripts/check-release.sh) requires the tag to be `v` followed by `VERSION`, `VERSION` to be a semantic version, and `CHANGELOG.md` to have a `## [<version>]` section.
2. **Build, test, and package**, on `ubuntu-24.04` (Linux x86-64) and on `macos-15` (Apple Silicon, under Rosetta 2), from the selected commit with submodules. Each runs `make test` as CI does, then [`scripts/package-release.sh`](../scripts/package-release.sh), which refuses a modified checkout or a Virgil checkout other than the pinned one, builds in a new directory, and writes the archive and its manifest. [`test/release/run.sh --archive <archive>`](../test/release/run.sh) then checks the release scripts and bundle integrity and invokes [`scripts/smoke-test-release.sh`](../scripts/smoke-test-release.sh), which extracts the archive in a new temporary directory and runs it there with an empty environment: it checks the files against the manifest, runs `--version`, runs `analyze` on a two-file program both directly and through a symlink, and checks semantic-error reporting directly. It also moves the install to a path with spaces, makes the library read-only, and drives diagnostics, hover, and a definition into bundled `lib/util` through the symlink with no client root or `VIRGIL_LOC`. Because the build is deterministic, the job also checks that the packaged executables are byte for byte the ones `make test` tested.
3. **Package and test the VS Code extension**, in the reusable [`package-vsix.yml`](../.github/workflows/package-vsix.yml) matrix after both build jobs. `scripts/package-vsix.py` stages each tested archive's runtime files, with no rebuild, then uses `vsce package --target linux-x64|darwin-arm64|darwin-x64 --pre-release`. Both Darwin targets use the same Intel archive. The universal package has no server. [`scripts/check-vsix.py`](../scripts/check-vsix.py) verifies canonical ZIP paths, extension identity, runtime dependencies and exclusions, target and prerelease metadata, notices, permissions, and exact byte equality of every bundled file with the archive, including its library and manifest. Each job records a SHA-256 before testing. The Linux package runs the installed [Extension Host test](../clients/vscode/README.md#extension-host-test) without an external server or root; the universal package runs with the Linux archive's external server. The job verifies the digest again before uploading `vsix-<target>`, containing the VSIX and producer digest.
4. **Editor tests**, the jobs of [`editor-test-suites.yml`](../.github/workflows/editor-test-suites.yml), which the weekly [editor tests workflow](../.github/workflows/editor-tests.yml) also runs, after the extension job. They test this run's archives and VSIX, without building the server or packaging the VSIX again: the [LazyVim test](../test/e2e/lazyvim/README.md#ci) on the Linux archive on `ubuntu-24.04`, with Neovim 0.11.6 and 0.12.6, each checked against its pinned SHA-256, and on the darwin archive on `macos-15`, with Homebrew's Neovim; the [Extension Host test](../clients/vscode/README.md#extension-host-test) on `macos-15`, with the `darwin-arm64` VSIX and its bundled server; and the [Remote-SSH test](../clients/vscode/README.md#remote-ssh-test) on `ubuntu-24.04`, three times, with the `linux-x64` VSIX installed on the host and no external server uploaded or configured. A [clean install test](#clean-install-test) also follows the README commands in a fresh Ubuntu container as a non-root user, without a repository or Virgil checkout. Each VS Code job first checks the VSIX against the SHA-256 that the extension job recorded. The LazyVim test's one expected failure, the warning that noice.nvim drops, must still fail ([expected results](../test/e2e/lazyvim/README.md#expected-results)).
5. **Write and verify SHA256SUMS** over every file the build and extension jobs produced, after checking that the VSIX still has the SHA-256 recorded when it was packaged, so the attached VSIX is byte for byte the tested one. It then writes the release notes with [`scripts/release-notes.py`](../scripts/release-notes.py): the version's `CHANGELOG.md` section, the archives, the VSIX with the extension version recorded in its `extension/package.json`, and the server and Virgil revisions. GitHub doesn't resolve relative links in a release, so the script rewrites the section's repository links, such as `[Hover](docs/architecture.md#hover)`, to `<server>/<repository>/blob/<ref>/<path>`, using exactly the repository URL and ref of the Downloads links: the manifests' tag when present, otherwise the source revision. Fragments are preserved but aren't checked; a fragment alone links to `CHANGELOG.md`. It doesn't parse Markdown: it treats every `](` as the start of a link target, even in a code span or after a backslash escape, rewrites a plain repository target, and keeps a target with a URI scheme. It fails on any other text after `](`, such as a title, angle brackets, whitespace, or parentheses; on any `]:`, which every reference link definition holds; on any HTML tag with an attribute, whatever the attribute, since a URL reaches an HTML element only through one, even in a code span, so write `a < b` rather than `a<b c`; on a target outside the repository or a root-relative or protocol-relative one; and on a missing file, so a dry run catches broken `CHANGELOG.md` links before the tag. The notes script reports unreadable, non-UTF-8, malformed, or incomplete build manifests as errors naming the file and problem; missing and invalid fields are reported together for each manifest. It validates the types of the fields it reads, and requires their strings and the VSIX's name, publisher, and version to be non-empty and encodable as UTF-8. It also fails if the manifests disagree about the version, tag, or revisions, or if the assets do not contain exactly the three platform VSIX files and the universal VSIX with a shared extension identity and version.
6. **Publish prerelease**, only for a pushed tag. It verifies `SHA256SUMS` again, refuses to continue if the tag already has a release, and creates the prerelease with all assets and the notes.

Only the publish job can write to the repository (`contents: write`); the others can only read. It uses the job's `GITHUB_TOKEN`, with no protected environment configured. Publishing starts only after the build, extension, editor test, and checksum jobs succeed, so a VSIX that fails to package, fails its checks, or fails the Extension Host test stops the release as a failed archive does, and so does an editor test that fails on either host.

Unlike the server's executables, a VSIX isn't reproducible: its zip entries record when it was packaged, so two packages of the same commit differ, as the ones that CI's `vscode-client` and `vscode-extension-host` jobs package do. The release workflow therefore tests and attaches each original file, rather than comparing a fresh package with a tested one.

## Clean install test

The reusable editor workflow runs `clean-install-linux` on release dry runs, tags, and the weekly editor run. A separate runner job builds a small [standalone bundle](../test/e2e/clean-install/run.py) containing the three install READMEs, the two public Neovim configs, the existing LazyVim plugin lock, and the drivers. The test job uses `container: ubuntu:24.04`, installs prerequisites explicitly, creates a non-root user with an empty home, and never checks out this repository or Virgil. Each editor gets its own fresh home and profile. The fixture is written by the driver inside that home, rather than copied from this repository.

The test extracts the shell fences immediately after `<!-- clean-install: NAME -->` comments and executes them in README order for each installation path. Missing, duplicated, reordered, or malformed marked steps fail before installation; a failed command names the README step in `run.log`. Neovim and LazyVim install the archive; VS Code initially installs only the Linux platform VSIX, with no server on `PATH` and no path setting. For its later explicit-root check, VS Code also installs the archive to supply an alternative library. All installations follow the marked commands. Plain Neovim downloads `virgil_lsp.lua` into `${XDG_CONFIG_HOME:-$HOME/.config}/nvim/plugin/`, which Neovim loads at startup after the init file without creating or changing `init.lua` or `init.vim`. `noloadplugins`, `--noplugin`, and `--clean` skip that automatic loading. The download uses a neighboring `.part` file and moves it into place only on success; Neovim does not source `.part` files. Drivers only observe the installed clients: each checks a string-to-int diagnostic, a definition into `lib/util/StringBuilder.v3`, and a hover naming its class, first with no client root or `VIRGIL_LOC`, then after the README's explicit-root command and an editor restart. The explicit root selects the installed library; no separate Virgil checkout is needed.

Only these README-documented substitutions are made:

| README example | Test value |
| --- | --- |
| `server_version` and the version in both root paths | The caller's `version` input |
| `extension_version` | The version in the Linux VSIX filename |
| `download_url`, the release asset location | An ephemeral localhost HTTP directory containing the caller's archive and VSIX |
| `config_url`, the raw source download location | The same HTTP server's `editors/nvim` directory from the source bundle |

The driver reads these example values from the marked blocks, checks their expected assignment and path shapes, and requires the server version to agree across the install command, both root paths, and both `config_url` tags. `make test` also requires the server examples and config tags to match `VERSION`, and the extension examples to match `clients/vscode/package.json`, including this page's Installing and Verifying a download sections. It runs the README blocks in plain Bash without `errexit` to check that failed or missing checksums prevent installation, and checks Neovim init-file preservation and repeat installs in temporary homes. The standalone editor run keeps `errexit` enabled to catch failing README commands. A release preparation must update the examples together, so the test can use the bundled configs before the new tag exists.

The final release `SHA256SUMS` is written after the editor tests, so it is not available yet. Each caller records its archive's SHA-256 immediately after packaging in `checksum-<target>`. The test builds a two-file `SHA256SUMS` from that recorded Linux digest and the Linux VSIX producer digest in `vsix-linux-x64/linux-x64.sha256`, before serving the files. The README's download and checksum commands then verify the received bytes against those producer expectations. This tests the user's verification procedure without pretending to download a published release or deriving the expectation from the downloaded copy.

Named prerequisites are `curl`, `tar`, checksum tools, VS Code with `code` on PATH, Neovim 0.11+, and an existing LazyVim starter with its prerequisites. The [prerequisite script](../test/e2e/clean-install/prerequisites.sh) installs Neovim 0.11.6, VS Code 1.142.0, system libraries, Xvfb, Git, a C compiler, Python, and Node explicitly. It installs the LazyVim starter and plugin versions pinned by the full editor suite before running the LazyVim README step. Xvfb and the small Extension Host test extension are test infrastructure; they do not configure the Virgil extension. The test disables Chromium's sandbox for the container. It requires a JSON receipt from every editor session before reporting success, and keeps those receipts, command logs, generated shell scripts, editor logs, and checksum expectations in the `clean-install-linux` artifact for 30 days, including on failure.

This is an installation check, not the full editor behavior suite or a manual UI check. It covers Linux x86-64 only. Hosted macOS runners do not provide a fresh Linux-style container, and creating and driving a clean macOS user would require separate login/session machinery; the existing macOS archive and editor tests still run, but do not provide this clean-install guarantee. Remote installs, upgrades, shell startup files, and a root outside the shipped library are not checked here. This job has no result-event IDs yet, so its evidence stays in its workflow logs and artifact rather than the historical manual results or the result-stream contract.

To reproduce without a container, package an archive and VSIX as above, record their digests when produced, and create the standalone bundle with `run.py bundle <directory>`. Use the bundled `run.py run --help` to see the required arguments, then execute `run.py run` from a directory outside the checkout with an empty HOME and a minimal PATH containing the named prerequisites. Use a new `--out` directory each time. `bundle` is the only operation that reads the checkout; `run` reads only that bundle and the supplied release assets.

## Making a release

1. Choose the version. Before v1.0 it is a prerelease such as `0.1.0-alpha.1`; raise `N` for each further alpha of the same version.
2. On a branch, prepare the release in one pull request:
   - Set `VERSION` to the version.
   - In `CHANGELOG.md`, move the entries under `## [Unreleased]` into a new section, `## [0.1.0-alpha.1] - YYYY-MM-DD`, and leave an empty `## [Unreleased]` above it. The new section becomes the release notes.
   - If the VS Code client or bundled server changed since the last release, raise its version with `npm version --no-git-tag-version <x.y.z>` in `clients/vscode`, which updates `package.json` and `package-lock.json`, so that the release doesn't attach different contents under a version that an earlier release attached ([versions](../clients/vscode/README.md#versions)).
   - Update the marked install examples in [README.md](../README.md#editor-setup), [clients/vscode/README.md](../clients/vscode/README.md#installing-from-a-vsix), and [editors/nvim/README.md](../editors/nvim/README.md), and this page's [Installing](#installing) and [Verifying a download](#verifying-a-download) examples. Keep the procedures in sync: server versions, both explicit root paths, and both `config_url` tags must use `VERSION`; extension versions must use the extension's version. `make test` checks these values.
   - In [docs/compatibility.md](compatibility.md), add a row for the version to the Virgil revision table, and update the extension compatibility table if needed.
   - Update [docs/known-limitations.md](known-limitations.md). [Dry-run](#dry-runs) the workflow on the branch. The dry run must pass, its editor test jobs included, among them the Remote-SSH test that each release needs ([VS Code](manual-testing.md#vs-code)). A release needs automated evidence only ([release gate](../test/e2e/RESULTS.md#the-release-gate)). Manual checks with its archives and VSIX are optional; follow the [manual procedure](manual-testing.md#procedure) and add any to the [results](manual-testing.md#results).
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
- **Build, test, and package**, **Package and test the VS Code extension**, an **Editor tests** job, or **Write and verify SHA256SUMS** failed because of the code, the scripts, or the workflow: delete the tag the same way, fix `main`, and tag the fixed commit. A release always comes from a tagged commit that passed every job. For the Extension Host test's failure output and log artifacts, see the [Extension Host test](../clients/vscode/README.md#extension-host-test). Each editor test job keeps its results and logs as an artifact ([Dry runs](#dry-runs)); the Remote-SSH test ends with `RESULT: INFRA` when it failed for a reason outside the repository, such as a download or its SSH server.
- A job failed for a reason outside the repository, such as a runner or network failure: use **Re-run failed jobs** on the run. The re-run uses the same commit.
- **Publish prerelease** failed while uploading: `gh release create` uploads assets to a draft release and publishes it only after every upload succeeds. Delete the leftover draft under **Releases**, then re-run the failed job.
- **Publish prerelease** reports that the tag already has a release: the workflow never replaces one. Once a release is published, people may have downloaded it, so publish a fix as a new version rather than replacing it. If a release must be withdrawn, delete it on GitHub and say why in the next release's notes.

## Dry runs

To try the whole release path without publishing, run the workflow by hand on a branch:

```sh
gh workflow run release.yml --ref main
gh run watch    # choose the new Release run
```

A dry run builds, tests, packages, and smoke-tests both archives, packages and tests the VSIX, runs the editor tests on them, and writes and verifies `SHA256SUMS`, but never publishes. On a branch or pull request, the release notes use the `## [Unreleased]` section, and the manifests' `tag` is `null`. Download the result, the same files that a release would attach, the VSIX included, plus `notes.md`, with `gh run download <run-id> --name release`. Each build job also keeps its archive and manifest as `archive-<target>`, and the matrix keeps each VSIX and its producer digest as `vsix-<target>`. The extension job's [Extension Host log artifact](../clients/vscode/README.md#extension-host-test) is named `vsix-<target>-logs-attempt-<n>` and retained for 30 days, where `<n>` is the run's attempt. The editor test jobs keep their results and logs for 30 days, also when they fail, as `lazyvim-linux-nvim-<version>`, `lazyvim-macos`, `vscode-vsix-macos`, `vscode-remote-ssh-linux`, and `clean-install-linux`; these hold neither keys nor Microsoft's software, such as VS Code, Remote-SSH, or the VS Code Server. Once the editor tests write result streams, the release's results in [docs/manual-testing.md](manual-testing.md#results) will be generated from those of its dry run, between markers under the release's heading, by `scripts/editor-results.py render` ([Rendering a release](../test/e2e/RESULTS.md#rendering-a-release)). Until then, the results stay hand-written.

A dry run from a tag (`--ref v0.1.0-alpha.1`) checks and records the tag and uses that version's changelog section, but still doesn't publish.

A pull request that changes `release.yml`, `check-release.sh`, `check-vsix.py`, `package-release.sh`, `package-vsix.py`, `vsix-payload.py`, `package-vsix.yml`, `release-notes.py`, or `smoke-test-release.sh`, the extension's packaging inputs (`clients/vscode/package.json`, `package-lock.json`, and `.vscodeignore`), or the editor tests and their pins (`editor-test-suites.yml`, `editor-tests.yml`, `test/e2e/lazyvim/`, the shared fixture in `test/e2e/nvim/fixture/`, `clients/vscode/test/e2e/`, and `test/e2e/clean-install/`), also dry-runs the workflow, because GitHub only runs a workflow by hand once it is on the default branch. Extension source changes also trigger the dry run. README changes alone do not trigger a full release dry run; an install command that breaks is caught by the weekly editor run and the release's required dry run before the tag. Other changes that could break packaging or the editor tests, such as to the `Makefile` or `scripts/v3c.sh`, don't trigger it; CI still packages the VSIX and runs the Extension Host test and the LazyVim test on Linux on every pull request, and you can dry-run `main` after merging them.

To package locally, run the same scripts as the workflow:

```sh
scripts/package-release.sh build/release
scripts/smoke-test-release.sh build/release/virgil-lsp-<version>-<target>.tar.gz
```

To check a Linux platform VSIX locally after building and smoke-testing the archive:

```sh
(cd clients/vscode && npm ci)
scripts/package-vsix.py linux-x64 --archive build/release/virgil-lsp-0.1.0-alpha.3-x86-64-linux.tar.gz --out build/vsix
(cd clients/vscode && xvfb-run -a npm run test:e2e -- --vsix ../../build/vsix/virgil-lsp-vscode-0.1.3-linux-x64.vsix)
```

For the universal fallback, package with `scripts/package-vsix.py universal --out build/vsix`, then pass its VSIX and an extracted archive's `virgil-lsp` as the test's positional argument. Platform checks can also be rerun with `scripts/check-vsix.py <vsix> --target <target> --archive <tested-archive>`; universal checks omit the last two options.

## Verifying a download

Download the archive for your platform and `SHA256SUMS` from the release page, or with the GitHub CLI:

```sh
gh release download v0.1.0-alpha.3 --repo SaltedTan/virgil-language-server \
  --pattern 'virgil-lsp-*-x86-64-linux.tar.gz' --pattern SHA256SUMS   # x86-64-darwin on macOS
```

Then check the archive's line of `SHA256SUMS`:

```sh
awk -v file=virgil-lsp-0.1.0-alpha.3-x86-64-linux.tar.gz '$2 == file' SHA256SUMS | sha256sum --check        # Linux
awk -v file=virgil-lsp-0.1.0-alpha.3-x86-64-darwin.tar.gz '$2 == file' SHA256SUMS | shasum -a 256 --check   # macOS
```

Both print `OK` for a good download. Check the VSIX the same way, with its own line: `awk -v file=virgil-lsp-vscode-0.1.3-linux-x64.vsix '$2 == file' SHA256SUMS | sha256sum --check`. Matching the whole file name, rather than a pattern in which `.` matches any character, keeps another file's line from checking the download. After installing, `virgil-lsp --version` prints the version and the Virgil revision, which match the archive's `manifest.json` and the release notes.

## Installing

For VS Code, install the [platform VSIX](#vs-code-extension); it includes the server and library, so no separate archive installation is needed. The archive instructions below apply to Neovim, LazyVim, and external-server installations. Install the server where it runs, which is the machine that holds the files ([Where the server runs](compatibility.md#where-the-server-runs)):

| Setup | Archive | Install on |
| --- | --- | --- |
| Editor on a Linux machine | `x86-64-linux` | That machine |
| VS Code Remote-SSH, or Neovim over SSH | `x86-64-linux` | The remote Linux machine, not the one showing the editor |
| VS Code Remote-WSL, or Neovim inside WSL 2 | `x86-64-linux` | The WSL distribution, not Windows |
| Editor on a Mac, local files | `x86-64-darwin` | The Mac |

Extract the archive and put a symlink to `virgil-lsp` on your `PATH`. The worker stays beside the real file:

```sh
mkdir -p ~/.local/opt ~/.local/bin
tar -xzf virgil-lsp-0.1.0-alpha.3-x86-64-linux.tar.gz -C ~/.local/opt
ln -sf ~/.local/opt/virgil-lsp-0.1.0-alpha.3-x86-64-linux/virgil-lsp ~/.local/bin/virgil-lsp
virgil-lsp --version
```

Make sure `~/.local/bin` is on the `PATH` that the editor sees. If you copy `virgil-lsp` elsewhere, copy `virgil-lsp-worker` and `virgil/` beside it too; a wrapper that starts the original server can leave them in the original directory.

To upgrade, extract the new archive next to the old one and point the symlink at it. To roll back, point the symlink at the old directory again. To uninstall, remove the symlink and the directory.

### macOS

On Apple Silicon, install Rosetta 2 first:

```sh
softwareupdate --install-rosetta --agree-to-license
```

The executables are unsigned. A web browser marks what it downloads as quarantined, and macOS may then refuse to run them. Download with `curl` or `gh`, which don't set the quarantine attribute, or remove it after extracting:

```sh
xattr -dr com.apple.quarantine ~/.local/opt/virgil-lsp-0.1.0-alpha.3-x86-64-darwin
```

### Editors

The editors start `virgil-lsp --stdio`:

- **VS Code:** install the [extension](#vs-code-extension) from the release's VSIX. It uses an explicit `virgil.server.path`, otherwise the bundled server if the VSIX target matches the workspace host's platform and architecture, otherwise `virgil-lsp` on the extension host's `PATH` when the VSIX has no bundle ([server selection](../clients/vscode/README.md#settings)). A mismatched bundle fails startup. With Remote-SSH or Remote-WSL, the extension runs on the remote side, so install the VSIX for that host, and set any `virgil.server.path` in the remote settings.
- **Neovim and LazyVim:** the configurations run `virgil-lsp` from `PATH`, or the absolute path you give in `cmd` ([Neovim setup](../editors/nvim/README.md)). Plain Neovim loads the downloaded configuration from its `plugin/` directory at startup; its init files are untouched.

### VS Code extension

Install `virgil-lsp-vscode-<extension version>-<target>.vsix` for the workspace host: `linux-x64`, `darwin-arm64`, or `darwin-x64`. The platform VSIX includes the tested archive's server, worker, and pinned library. Both Darwin packages contain an Intel server, with Rosetta 2 required on Apple Silicon. Intel macOS remains best effort. The universal `virgil-lsp-vscode-<extension version>.vsix` contains no server and needs a compatible external server. Native Windows remains unsupported; use WSL 2.

With Remote-SSH or WSL, install the package for the remote host in the remote window. VS Code or the extension's output channel rejects a mismatched bundle. Download and verify it against `SHA256SUMS`, then install it:

```sh
gh release download v0.1.0-alpha.3 --repo SaltedTan/virgil-language-server \
  --pattern 'virgil-lsp-vscode-*.vsix' --pattern SHA256SUMS
code --install-extension virgil-lsp-vscode-0.1.3-linux-x64.vsix
```

or use **Extensions: Install from VSIX...** in VS Code. VS Code doesn't update an extension installed from a VSIX; install the next release's matching VSIX to upgrade. To roll back, install the older matching VSIX with `--force` and reload. An explicit `virgil.server.path` switches to an external server; clear it to use the bundle again. Bump the independent numeric extension version for any client or bundled-server change, and never reuse a version/target pair for different contents. [Installing from a VSIX](../clients/vscode/README.md#installing-from-a-vsix) covers remote windows, upgrading, and removal, and [compatibility](compatibility.md#editors) records which server versions each extension version works with.

### The Virgil root

New archives include the complete pinned `lib/` under executable-adjacent `virgil/`. With no client root, projects selecting `lib/util/*.v3` work without a checkout. See [The Virgil root](configuration.md#the-virgil-root) for root selection and errors.

Projects selecting sources outside `lib/`, such as `rt/` or `aeneas/`, and development builds without a bundle still need a checkout. Use the Virgil revision that `virgil-lsp --version` prints:

```sh
git clone https://github.com/titzer/virgil.git ~/src/virgil
git -C ~/src/virgil checkout <virgil revision>
export VIRGIL_LOC=~/src/virgil    # or set virgil.virgilRoot in VS Code
```

With Remote-SSH or WSL, the server's `virgil/` library must be on the same machine as the server: install the whole archive on the remote machine or in the WSL distribution, or the platform VSIX for that host. Any optional checkout and client root must also be on that machine.
