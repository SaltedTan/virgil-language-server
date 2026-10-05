#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

skip() {
  if [[ ${REQUIRE_NVIM:-0} == 1 ]]; then
    echo "FAIL: Neovim smoke test requires Neovim 0.11+: $1" >&2
    exit 1
  fi
  echo "SKIP: Neovim smoke test requires Neovim 0.11+: $1"
  exit 0
}

command -v nvim >/dev/null 2>&1 || skip 'nvim not found on PATH'
version=$(nvim --version)
if [[ $version =~ ^NVIM\ v([0-9]+)\.([0-9]+) ]]; then
  (( BASH_REMATCH[1] > 0 || BASH_REMATCH[2] >= 11 )) || skip "${version%%$'\n'*}"
else
  skip 'could not determine nvim version'
fi

repo=$(cd "$(dirname "$0")/../../.." && pwd -P)
server=${1:-build/virgil-lsp}
server_dir=$(cd "$(dirname "$server")" && pwd -P)
[[ -x $server_dir/virgil-lsp && -x $server_dir/virgil-lsp-worker ]] || {
  echo 'FAIL: build virgil-lsp and virgil-lsp-worker before running the smoke test' >&2
  exit 1
}
# Keep edits and Neovim state out of the checkout's fixture and the user's home.
work=$(mktemp -d "$server_dir/nvim-smoke.XXXXXX")
trap 'rm -rf "$work"' EXIT
cp -R "$repo/test/e2e/nvim/fixture" "$work/project"
export PATH="$server_dir:$PATH"
export XDG_CONFIG_HOME="$work/config" XDG_DATA_HOME="$work/data"
export XDG_STATE_HOME="$work/state" XDG_CACHE_HOME="$work/cache"
export NVIM_LOG_FILE="$work/nvim.log"
export VIRGIL_NVIM_CONFIG="$repo/editors/nvim/virgil_lsp.lua"
# The fixture project's virgilDependencies resolve against the pinned checkout.
export VIRGIL_LOC="$repo/vendor/virgil"

echo "Neovim smoke test: ${version%%$'\n'*}"
# A portable outer deadline also covers init errors before the Lua checks run.
if ! python3 - "$repo/test/e2e/nvim/check.lua" "$work/project/bad.v3" <<'PY'
import os
import signal
import subprocess
import sys

process = subprocess.Popen(
    ["nvim", "--headless", "--clean", "-n", "-u", sys.argv[1], sys.argv[2]],
    start_new_session=True,
)
try:
    sys.exit(process.wait(timeout=120))
except subprocess.TimeoutExpired:
    print("FAIL: Neovim smoke test exceeded 120 seconds", file=sys.stderr)
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    sys.exit(1)
PY
then
  # Surface the protocol log before the temporary directory is removed.
  find "$work" -name lsp.log -exec tail -n 100 {} \;
  exit 1
fi
