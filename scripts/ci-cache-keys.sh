#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Prints the keys of CI's dependency caches as NAME=VALUE lines, for
# $GITHUB_OUTPUT. Pull request CI (ci.yml) restores these caches and only
# seed-caches.yml, on main, saves them, so both compute their keys here.
#
#   scripts/ci-cache-keys.sh CACHE...
#
# CACHE is one of:
#   node-modules  clients/vscode/node_modules, keyed on the client's
#                 package.json and package-lock.json and Node's major version
#                 (node on PATH);
#   vscode        clients/vscode/.vscode-test, keyed on the Extension Host
#                 test's pinned VS Code version, which it also prints as
#                 vscode-version;
#   lazyvim       the LazyVim test's seeded cache (test/e2e/lazyvim/run.sh
#                 --seed-cache), keyed on Neovim's version (nvim on PATH), the
#                 starter's commit, and the files that decide what it installs:
#                 lazy-lock.json, parsers.lua, the plugin spec virgil.lua, and
#                 run.sh itself.
#
# Every key also names the runner's OS and architecture: RUNNER_OS and
# RUNNER_ARCH, or uname's outside GitHub Actions. A key changes whenever what
# the cache holds could, so a pull request that changes any of these inputs
# misses the cache and installs from scratch.
set -euo pipefail

cd "$(dirname "$0")/.."

fail() {
  echo "ci-cache-keys.sh: $*" >&2
  exit 1
}

# digest FILE...: one SHA-256 digest of the files' names and contents.
digest() {
  local sum
  if command -v sha256sum >/dev/null; then sum=(sha256sum); else sum=(shasum -a 256); fi
  "${sum[@]}" "$@" | "${sum[@]}" | cut -d ' ' -f 1
}

(( $# > 0 )) || fail "usage: $0 (node-modules | vscode | lazyvim)..."
platform="${RUNNER_OS:-$(uname -s)}-${RUNNER_ARCH:-$(uname -m)}"
for cache in "$@"; do
  case $cache in
    node-modules)
      node=$(node -p 'process.versions.node.split(".")[0]') || fail "cannot run node"
      echo "node-modules=node-modules-$platform-node$node-$(digest clients/vscode/package.json clients/vscode/package-lock.json)"
      ;;
    vscode)
      version=$(sed -n "s/^const VSCODE_VERSION = '\([^']*\)';\$/\1/p" clients/vscode/test/e2e/run.cjs)
      [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
        fail "no VSCODE_VERSION in clients/vscode/test/e2e/run.cjs: '$version'"
      echo "vscode-version=$version"
      echo "vscode=vscode-$platform-$version"
      ;;
    lazyvim)
      nvim=$(nvim --version | head -n 1) || fail "cannot run nvim"
      nvim_re='^NVIM v([0-9]+\.[0-9]+\.[0-9]+)'
      [[ $nvim =~ $nvim_re ]] || fail "could not determine Neovim's version: $nvim"
      nvim=${BASH_REMATCH[1]}
      starter=$(sed -n 's/^starter_commit=\([0-9a-f]\{40\}\)$/\1/p' test/e2e/lazyvim/run.sh)
      [[ -n $starter ]] || fail "no starter_commit in test/e2e/lazyvim/run.sh"
      inputs=$(digest test/e2e/lazyvim/lazy-lock.json test/e2e/lazyvim/parsers.lua \
        editors/nvim/lazyvim/virgil.lua test/e2e/lazyvim/run.sh)
      echo "lazyvim=lazyvim-$platform-nvim$nvim-$starter-$inputs"
      ;;
    *) fail "unknown cache: $cache" ;;
  esac
done
