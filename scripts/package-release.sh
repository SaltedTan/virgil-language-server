#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Build the server for one target and package it as a release archive. See
# docs/releasing.md.
# Usage: package-release.sh [--tag <tag>] [--allow-dirty] <output-directory>
#
# Writes virgil-lsp-<version>-<target>.tar.gz and a copy of its build manifest,
# virgil-lsp-<version>-<target>.manifest.json, to the output directory, and
# prints their paths. The archive holds one directory of the same name with
# virgil-lsp, virgil-lsp-worker, LICENSE, THIRD_PARTY_NOTICES.md, README.md,
# manifest.json, and virgil/{lib/,LICENSE,REVISION}.
#
# --tag checks the tag with scripts/check-release.sh and records it in the
# manifest; without it, the manifest's tag is null. The checkout must be clean
# and Virgil must be at the pinned revision. See docs/releasing.md for the
# --allow-dirty option for local builds; releases never use it.
#
# VIRGIL, VIRGIL_V3C, V3C_TARGET, and WORKER_HEAP select the build as they do
# for make (docs/development.md). The build runs in a new temporary directory,
# so the manifest describes exactly what is packaged.
#
# V3C_TARGET=jvm packages the JVM build with a bundled Java runtime instead, as
# virgil-lsp-<version>-jvm-<platform>.tar.gz: virgil-lsp and virgil-lsp-worker
# are launchers that run virgil-lsp.jar and virgil-lsp-worker.jar beside them
# on runtime/, the Temurin runtime that scripts/jvm-runtime.py links from the
# pinned JDKs (scripts/jvm-runtime.json). JVM_PLATFORM selects the platform:
# arm64-darwin, the release asset (the default), or x86-64-linux, for testing
# the package on a Linux host. JVM_JDK_CACHE holds the downloaded JDK archives
# (default build/jdk-cache). The pinned host JDK compiles the runtime classes,
# so JAVA is not used. The JVM package also writes, and prints the path of,
# the runtime's corresponding source archive, Adoptium's own
# OpenJDK21U-jdk-sources_<version>.tar.gz for the pinned build, under its
# upstream name; a release attaches it beside the archive.
set -euo pipefail

usage="usage: package-release.sh [--tag <tag>] [--allow-dirty] <output-directory>"
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

tag=""
allow_dirty=0
while [ "$#" -gt 0 ]; do
    case $1 in
        --tag) tag=${2:?$usage}; shift 2 ;;
        --allow-dirty) allow_dirty=1; shift ;;
        -*) echo "$usage" >&2; exit 2 ;;
        *) break ;;
    esac
done
[ "$#" -eq 1 ] || { echo "$usage" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd)

# make runs in the checkout, so relative VIRGIL and VIRGIL_V3C paths are
# relative to it. VIRGIL keeps the Makefile's relative default: the executables
# record their source paths, so an absolute one would make them differ from
# the ones that make test builds.
cd "$ROOT"
virgil=${VIRGIL:-vendor/virgil}

if [ -n "$tag" ]; then
    version=$(scripts/check-release.sh "$tag")
else
    version=$(tr -d '[:space:]' < VERSION)
fi
case $version in
    "" | *[!0-9A-Za-z.+-]*) echo "error: VERSION '$version' is not a version" >&2; exit 1 ;;
esac

target=$(scripts/v3c.sh -print-target)
compiler=$(scripts/v3c.sh -print-compiler)
worker_heap=${WORKER_HEAP:-$(make -s --no-print-directory print-worker-heap)}

# Virgil's bin/v3c launcher replaces itself with a symlink to a host compiler
# the first time it runs. The build doesn't use it (scripts/v3c.sh passes the
# compiler explicitly), so restore it rather than build a "-dirty" revision.
if [ "$(git -C "$virgil" status --porcelain -- bin/v3c)" = " T bin/v3c" ]; then
    echo "note: restoring $virgil/bin/v3c, which Virgil replaced with a symlink" >&2
    git -C "$virgil" checkout -- bin/v3c
fi

revision=$(git rev-parse HEAD)
virgil_revision=$(git -C "$virgil" rev-parse HEAD)
pinned=$(git rev-parse HEAD:vendor/virgil)
problems=""
# Untracked files matter only under src/, which the Makefile compiles whole.
if [ -n "$(git status --porcelain --untracked-files=no --ignore-submodules=all)" ] ||
   [ -n "$(git status --porcelain --untracked-files=all -- src)" ]; then
    problems="$problems  the checkout has uncommitted changes (git status)\n"
fi
if [ "$virgil_revision" != "$pinned" ]; then
    problems="$problems  Virgil is at $virgil_revision, not the pinned $pinned\n"
fi
if [ -n "$(git -C "$virgil" status --porcelain)" ]; then
    problems="$problems  the Virgil checkout at $virgil has changes (git -C $virgil status)\n"
fi
dirty=false
if [ -n "$problems" ]; then
    if [ "$allow_dirty" -eq 0 ]; then
        printf "error: refusing to package:\n%b" "$problems" >&2
        echo "       use --allow-dirty to package anyway; releases need a clean checkout" >&2
        exit 1
    fi
    printf "warning: packaging anyway (--allow-dirty):\n%b" "$problems" >&2
    dirty=true
fi

sha256() {
    if command -v sha256sum > /dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d ' ' -f 1
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
name=virgil-lsp-$version-$target
if [ "$target" = jvm ]; then
    jvm_platform=${JVM_PLATFORM:-arm64-darwin}
    case $jvm_platform in
        arm64-darwin) jvm_locale=en_US.UTF-8 ;;
        x86-64-linux) jvm_locale=C.UTF-8 ;;
        *) echo "error: JVM_PLATFORM '$jvm_platform' is not arm64-darwin or x86-64-linux" >&2; exit 1 ;;
    esac
    name=$name-$jvm_platform
    # Standard output is left for the paths of the files written.
    java=$(python3 scripts/jvm-runtime.py prepare "$jvm_platform" "$WORK/jdk")
    make --no-print-directory BUILD="$WORK/build" WORKER_HEAP="$worker_heap" JAVA="$java" \
        JVM_BUNDLED_RUNTIME=runtime JVM_LOCALE="$jvm_locale" all >&2
else
    make --no-print-directory BUILD="$WORK/build" WORKER_HEAP="$worker_heap" all >&2
fi

stage=$WORK/stage/$name
mkdir -p "$stage"
cp "$WORK/build/virgil-lsp" "$WORK/build/virgil-lsp-worker" "$stage/"
if [ "$target" = jvm ]; then
    cp "$WORK/build/virgil-lsp.jar" "$WORK/build/virgil-lsp-worker.jar" "$stage/"
    cp -R "$WORK/jdk/runtime" "$stage/runtime"
fi
cp LICENSE THIRD_PARTY_NOTICES.md "$stage/"
python3 scripts/release-library.py stage "$virgil" "$stage/virgil" "$virgil_revision"

case $target in
    x86-64-linux)
        platform="Linux x86-64. WSL 2 on x86-64 Windows is expected to run it, on a best-effort
basis: it isn't tested there."
        ;;
    x86-64-darwin)
        platform="macOS. This is an Intel (x86-64) build, not a native arm64 one: Apple Silicon
Macs run it under Rosetta 2, which you can install with
\`softwareupdate --install-rosetta --agree-to-license\`."
        ;;
    jvm)
        case $jvm_platform in
            arm64-darwin)
                platform="Apple Silicon (arm64) Macs, with its own Java runtime, so that it needs
neither Rosetta 2 nor an installed Java. This is the Java (JVM) build of the
server." ;;
            *)
                platform="the \`$jvm_platform\` platform, with its own Java runtime. This is the Java
(JVM) build of the server, for testing; it is not a release asset." ;;
        esac
        ;;
    *)
        platform="The \`$target\` target."
        ;;
esac
# The JVM package's own files, listed after the others; empty otherwise.
label=$target
jvm_files=
if [ "$target" = jvm ]; then
    label=jvm-$jvm_platform
    runtime_version=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
        scripts/jvm-runtime.json)
    source_name=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["source"]["name"])' \
        scripts/jvm-runtime.json)
    source_sha256=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["source"]["sha256"])' \
        scripts/jvm-runtime.json)
    jvm_files="
- \`virgil-lsp\` and \`virgil-lsp-worker\` are launcher scripts. They run
  \`virgil-lsp.jar\` and \`virgil-lsp-worker.jar\` beside them on \`runtime/\`, an
  Eclipse Temurin $runtime_version Java runtime with only the \`java.base\` module.
  Keep all of them together. \`runtime/legal/\` holds the runtime's license
  notices: GPLv2 with the Classpath Exception, and the notices of the
  libraries it includes. No Java installation is needed or used.
- The runtime's corresponding source is attached to the same GitHub release
  as this archive: \`$source_name\`, SHA-256
  \`$source_sha256\`, Eclipse Adoptium's
  source archive for this build. \`THIRD_PARTY_NOTICES.md\` gives its upstream
  tag and release, and \`manifest.json\` records the \`jlink\` options."
fi
cat > "$stage/README.md" <<README
# virgil-lsp $version ($label)

The [Virgil Language Server](https://github.com/SaltedTan/virgil-language-server),
an unofficial language server for the Virgil programming language, for
$platform

- \`virgil-lsp\` is the server. Editors start it as \`virgil-lsp --stdio\`.
- \`virgil-lsp-worker\` is its analysis worker. The server starts the worker from
  the directory that holds the server's real path, so keep the two together. A
  symlink to \`virgil-lsp\` from a directory on your \`PATH\` works.
- \`virgil/\` holds the complete pinned \`lib/\`, its Apache-2.0 license, and
  \`REVISION\`. Keep it beside the executables.
- \`manifest.json\` records the version, the exact server and Virgil revisions,
  and how the executables were built.
- \`LICENSE\` and \`THIRD_PARTY_NOTICES.md\` cover the server and the Virgil
  compiler and libraries compiled into it.$jvm_files

To install:

    mkdir -p ~/.local/opt ~/.local/bin
    tar -xzf $name.tar.gz -C ~/.local/opt
    ln -sf ~/.local/opt/$name/virgil-lsp ~/.local/bin/virgil-lsp
    virgil-lsp --version

With no client Virgil root, the server uses the bundled library beside its
resolved executable after checking its revision. A client root takes precedence;
an invalid override never falls back. Projects needing sources outside \`lib/\`
(such as \`rt/\` or \`aeneas/\`) still need an explicit checkout. The release
documentation covers checksums, editors, remote machines, and root selection:
https://github.com/SaltedTan/virgil-language-server/blob/${tag:-$revision}/docs/releasing.md#installing
README

case $compiler in /*) ;; *) compiler=$ROOT/$compiler ;; esac
virgil_root=$(cd "$virgil" && pwd)
case $compiler in
    "$virgil_root"/*) compiler_path=${compiler#"$virgil_root"/} ;;
    *) compiler_path=$compiler ;;
esac
python3 - "$stage/manifest.json" "$version" "$tag" "$target" "$revision" "$dirty" \
    "$virgil_revision" "$worker_heap" "$compiler_path" "$(sha256 "$compiler")" \
    "$(sha256 "$stage/virgil-lsp")" "$(sha256 "$stage/virgil-lsp-worker")" "${jvm_platform:-}" <<'PY'
import json, sys
from pathlib import Path
import importlib.util
spec = importlib.util.spec_from_file_location("release_library", "scripts/release-library.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
(out, version, tag, target, revision, dirty, virgil_revision, worker_heap,
 compiler_path, compiler_sha256, server_sha256, worker_sha256, platform) = sys.argv[1:]
manifest = {
    "manifestVersion": 1,
    "name": "virgil-lsp",
    "version": version,
    "tag": tag or None,
    "target": target,
    "sourceRevision": revision,
    "dirty": dirty == "true",
    "virgilRevision": virgil_revision,
    "workerHeap": worker_heap,
    "library": module.metadata(Path(out).parent / "virgil", virgil_revision),
    "compiler": {"path": compiler_path, "sha256": compiler_sha256},
    "sha256": {"virgil-lsp": server_sha256, "virgil-lsp-worker": worker_sha256},
}
if target == "jvm":
    # The JVM package's platform, bundled runtime, and jars.
    spec = importlib.util.spec_from_file_location("jvm_runtime", "scripts/jvm-runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    stage = Path(out).parent
    manifest["platform"] = platform
    manifest["runtime"] = runtime.metadata(platform, stage / "runtime")
    for jar in ("virgil-lsp.jar", "virgil-lsp-worker.jar"):
        manifest["sha256"][jar] = module.sha((stage / jar).read_bytes())
with open(out, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY

chmod 755 "$stage" "$stage/virgil-lsp" "$stage/virgil-lsp-worker"
chmod 644 "$stage/LICENSE" "$stage/THIRD_PARTY_NOTICES.md" "$stage/README.md" "$stage/manifest.json"
if [ "$target" = jvm ]; then
    chmod 644 "$stage/virgil-lsp.jar" "$stage/virgil-lsp-worker.jar"
fi
# Normalize ordering, ownership, mtimes, and the gzip header on Linux/macOS.
python3 scripts/release-library.py archive "$stage" "$OUT/$name.tar.gz" "$(git show -s --format=%ct HEAD)"
cp "$stage/manifest.json" "$OUT/$name.manifest.json"
echo "$OUT/$name.tar.gz"
echo "$OUT/$name.manifest.json"
if [ "$target" = jvm ]; then
    python3 scripts/jvm-runtime.py source "$OUT"
fi
