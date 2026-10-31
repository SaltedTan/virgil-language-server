#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
# Usage: run.sh [SERVER] [--results FILE [--release VERSION] [--purpose PURPOSE]]
# With --results, results.py also writes the run's result stream to FILE, as
# test/e2e/RESULTS.md describes, and a missing Neovim fails the run instead of
# skipping it.
set -euo pipefail

usage() {
  echo "usage: $0 [SERVER] [--results FILE [--release VERSION] [--purpose PURPOSE]]" >&2
  exit 2
}

command=("$0" "$@")
server='' results='' release='' purpose=''
while (( $# )); do
  case $1 in
    --results|--release|--purpose)
      (( $# > 1 )) && [[ -n $2 ]] || usage
      case $1 in
        --results) results=$2 ;;
        --release) release=$2 ;;
        --purpose) purpose=$2 ;;
      esac
      shift 2
      ;;
    --*) usage ;;
    *) [[ -z $server ]] || usage; server=$1; shift ;;
  esac
done
[[ -n $results || -z $release$purpose ]] || usage
case ${purpose:=development} in
  release|development|compatibility|canary|demo) ;;
  *) usage ;;
esac
# Empty the stream before any other command, so that an earlier run's stream
# can't outlive a run that stops early.
if [[ -n $results ]]; then
  { : > "$results"; } 2>/dev/null || usage
fi

repo=$(cd "$(dirname "$0")/../../.." && pwd -P)
deadline=120
if [[ -n $results ]]; then
  results_dir=$(cd "$(dirname "$results")" && pwd -P) || usage
  results=$results_dir/$(basename "$results")
  stream=(--stream "$results" --server "${server:-build/virgil-lsp}" --purpose "$purpose"
    --launched-at "${EPOCHREALTIME:-$(date -u +%s)}" --runtime "bash=$BASH_VERSION")
  [[ -z $release ]] || stream+=(--release "$release")
  results_py() {
    local step=$1
    shift
    python3 "$repo/test/e2e/nvim/results.py" "$step" "${stream[@]}" --nvim-version "${nvim_version:-}" "$@" \
      -- "${command[@]}"
  }
  # results.py removes the temporary directory, verifies that, and writes the
  # run-end, unless the run was interrupted; its status is the run's.
  finish() {
    trap '' HUP INT TERM
    local code=$1 args=()
    set +e
    # Stop the wrapper that a signal interrupted, so that it stops Neovim, and
    # reap it before the cleanup.
    if [[ -n ${interrupted:-} && -n ${runner:-} ]]; then
      kill -s "$interrupted" "$runner" 2>/dev/null
      wait "$runner" 2>/dev/null
    fi
    [[ -z ${work:-} ]] || args+=(--work "$work")
    [[ -z ${preflight:-} ]] || args+=(--preflight-passed)
    [[ -z ${interrupted:-} ]] || args+=(--interrupted)
    results_py end --exit-code "$code" ${args[@]+"${args[@]}"}
    code=$?
    [[ -z ${work:-} ]] || rm -rf "$work"
    exit "$code"
  }
  trap 'finish "$?"' EXIT
  # Bash runs these at once while it waits for the Neovim wrapper, which runs in
  # the background for that reason, and otherwise after the command in
  # progress ends.
  trap 'interrupted=HUP; exit 129' HUP
  trap 'interrupted=INT; exit 130' INT
  trap 'interrupted=TERM; exit 143' TERM
fi

skip() {
  if [[ -n $results ]]; then
    echo "FAIL: Neovim smoke test requires Neovim 0.11+: $1" >&2
    results_py start --preflight-failure "Neovim 0.11+ is required: $1" --failure-code "$2"
    exit 1
  fi
  if [[ ${REQUIRE_NVIM:-0} == 1 ]]; then
    echo "FAIL: Neovim smoke test requires Neovim 0.11+: $1" >&2
    exit 1
  fi
  echo "SKIP: Neovim smoke test requires Neovim 0.11+: $1"
  exit 0
}

command -v nvim >/dev/null 2>&1 || skip 'nvim not found on PATH' neovim-missing
version=$(nvim --version)
nvim_version=${version%%$'\n'*}
if [[ $version =~ ^NVIM\ v([0-9]+)\.([0-9]+) ]]; then
  (( BASH_REMATCH[1] > 0 || BASH_REMATCH[2] >= 11 )) || skip "$nvim_version" neovim-too-old
else
  skip 'could not determine nvim version' neovim-version-unknown
fi

server=${server:-build/virgil-lsp}
server_dir=$(cd "$(dirname "$server")" && pwd -P)
[[ -x $server_dir/virgil-lsp && -x $server_dir/virgil-lsp-worker ]] || {
  echo 'FAIL: build virgil-lsp and virgil-lsp-worker before running the smoke test' >&2
  [[ -z $results ]] || results_py start --failure-code server-missing \
    --preflight-failure 'virgil-lsp and virgil-lsp-worker are not both executable in the server directory'
  exit 1
}
preflight=passed
# Keep edits and Neovim state out of the checkout's fixture and the user's home.
work=$(mktemp -d "$server_dir/nvim-smoke.XXXXXX")
[[ -n $results ]] || trap 'rm -rf "$work"' EXIT
cp -R "$repo/test/e2e/nvim/fixture" "$work/project"
export PATH="$server_dir:$PATH"
export XDG_CONFIG_HOME="$work/config" XDG_DATA_HOME="$work/data"
export XDG_STATE_HOME="$work/state" XDG_CACHE_HOME="$work/cache"
export NVIM_LOG_FILE="$work/nvim.log"
export VIRGIL_NVIM_CONFIG="$repo/editors/nvim/virgil_lsp.lua"
# The fixture project's virgilDependencies resolve against the pinned checkout.
export VIRGIL_LOC="$repo/vendor/virgil"
[[ -z $results ]] || results_py start --fixture "$work/project"

echo "Neovim smoke test: $nvim_version"
run_nvim() {
  if [[ -n $results ]]; then
    # The same deadline, and check.lua's observations written as they arrive.
    # A trapped signal ends the wait at once; finish then stops the wrapper.
    python3 "$repo/test/e2e/nvim/results.py" run --stream "$results" --work "$work" --deadline "$deadline" -- \
      nvim --headless --clean -n -u "$repo/test/e2e/nvim/check.lua" "$work/project/bad.v3" <&0 &
    runner=$!
    local status=0
    wait "$runner" || status=$?
    runner=''
    return "$status"
  fi
  # A portable outer deadline also covers init errors before the Lua checks run.
  python3 - "$repo/test/e2e/nvim/check.lua" "$work/project/bad.v3" "$deadline" <<'PY'
import os
import signal
import subprocess
import sys

deadline = int(sys.argv[3])
process = subprocess.Popen(
    ["nvim", "--headless", "--clean", "-n", "-u", sys.argv[1], sys.argv[2]],
    start_new_session=True,
)
try:
    sys.exit(process.wait(timeout=deadline))
except subprocess.TimeoutExpired:
    print(f"FAIL: Neovim smoke test exceeded {deadline} seconds", file=sys.stderr)
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    sys.exit(1)
PY
}
if ! run_nvim; then
  # Surface the protocol log before the temporary directory is removed.
  find "$work" -name lsp.log -exec tail -n 100 {} \;
  exit 1
fi
