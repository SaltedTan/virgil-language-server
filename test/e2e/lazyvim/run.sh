#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Tests the shipped LazyVim configuration (editors/nvim/lazyvim/virgil.lua)
# under the pinned LazyVim starter and plugins, in a real terminal UI: each run
# starts Neovim in a detached tmux session, and driver.lua drives it through
# LazyVim's own keys. See README.md for the checks, outputs, and pins.
#
#   run.sh [--out DIR] [--keep-work] [--update-lock | --cache CACHE]
#     [--results FILE [--release VERSION] [--purpose PURPOSE]] (--archive ARCHIVE | SERVER)
#   run.sh [--out DIR] [--keep-work] --seed-cache CACHE
#
# ARCHIVE is a release archive, virgil-lsp-<version>-x86-64-<os>.tar.gz or the
# JVM build's virgil-lsp-<version>-jvm-arm64-darwin.tar.gz;
# SERVER is a built virgil-lsp with virgil-lsp-worker beside it. With
# --results, results.py also writes the run's result stream to FILE, as
# test/e2e/RESULTS.md describes. --seed-cache installs the starter, plugins,
# and parsers into CACHE, a new or empty directory, and runs nothing else;
# --cache restores them from CACHE instead of installing them, if CACHE holds
# a finished seed for the same starter, Neovim, system, lock, parsers.lua, and
# virgil.lua; stops if CACHE holds a seed for anything else; and otherwise
# installs them as without it, leaving CACHE alone.
set -euo pipefail

# The LazyVim starter's commit. The plugins' commits, including lazy.nvim's,
# are in lazy-lock.json.
starter_commit=803bc181d7c0d6d5eeba9274d9be49b287294d99
starter_url=https://github.com/LazyVim/starter
lazy_url=https://github.com/folke/lazy.nvim.git
lazyvim_url=https://github.com/LazyVim/LazyVim.git
# Deadlines in seconds: for installing the plugins and parsers, and for each run.
install_timeout=${VIRGIL_LAZYVIM_INSTALL_TIMEOUT:-600}
run_timeout=${VIRGIL_LAZYVIM_RUN_TIMEOUT:-240}

usage() {
  echo "usage: $0 [--out DIR] [--keep-work] [--update-lock | --cache CACHE]" \
    "[--results FILE [--release VERSION] [--purpose PURPOSE]] (--archive ARCHIVE | SERVER)" >&2
  echo "       $0 [--out DIR] [--keep-work] --seed-cache CACHE" >&2
  stop_check "the arguments are not valid: $*" usage
  exit 2
}

# die MESSAGE: fails the check in progress with MESSAGE, and stops. Its
# failure code is $failure_code, or error.
die() {
  echo "run.sh: $*" >&2
  stop_check "$*" "${failure_code:-error}"
  exit 1
}

# With --results, run.sh records each of its own checks, and driver.lua each
# of its steps, in the observations file, from which results.py writes the
# stream at the end. Without it, these do nothing.
results='' release='' purpose='' observations='' interrupted=''
check_id='' check_profile='' check_started=''
clock() { echo "${EPOCHREALTIME:-$(date -u +%s)}"; }
results_py() { python3 "$here/results.py" "$@"; }
# begin_check ID [PROFILE]: the check in progress, in the wrapper profile by
# default. A failure of the script belongs to it until it ends.
begin_check() {
  [[ -n $results ]] || return 0
  check_id=$1 check_profile=${2:-wrapper} check_started=$(clock)
}
# end_check STATUS OBSERVATION [REASON CODE [stopped]]: ends the check in
# progress.
end_check() {
  [[ -n $results && -n $check_id ]] || return 0
  local extra=()
  [[ -z ${3:-} ]] || extra=(--reason "$3" --failure-code "$4")
  [[ -z ${5:-} ]] || extra+=(--stopped)
  results_py check "$observations" --id "$check_id" --profile "$check_profile" --started "$check_started" \
    --status "$1" --observation "$2" ${extra[@]+"${extra[@]}"} || true
  check_id=''
}
pass_check() { end_check pass "$*"; }
fail_check() { end_check fail "$1" "$1" "$2"; }
# stop_check REASON CODE: fails the check in progress, at which run.sh stops.
stop_check() { end_check fail "$1" "$1" "$2" stopped; }
# record_digest NAME DIR: the tree digest of a profile's configuration or the
# fixture.
record_digest() {
  [[ -n $results ]] || return 0
  results_py digest "$observations" --name "$1" --tree "$2" || true
}
# results_end STATUS: writes the stream, and exits with results.py's status.
# It runs from the directory that run.sh started in, whose relative paths the
# command names; the work directory may be gone.
results_end() {
  local args=()
  cd "$launch_dir" 2>/dev/null || cd /
  [[ -z ${work:-} ]] || args+=(--work "$work")
  [[ -z ${out:-} ]] || args+=(--out "$out")
  (( ! keep_work )) || args+=(--keep-work)
  [[ -z $interrupted ]] || args+=(--interrupted)
  [[ -z $release ]] || args+=(--release "$release")
  results_py end "$observations" --stream "$results" --purpose "$purpose" --exit-code "$1" \
    --nvim-version "${nvim_version:-}" --starter-commit "$starter_commit" --launched-at "$launched_at" \
    --runtime "bash=$BASH_VERSION" ${args[@]+"${args[@]}"} -- "${command[@]}"
  exit $?
}

command=("$0" "$@")
launched_at=$(clock) launch_dir=$PWD
# An earlier run's stream must not outlive a run that stops at its
# arguments, so --results FILE is emptied before they are checked.
previous=''
for argument in "$@"; do
  [[ $previous != --results || -z $argument ]] || { : > "$argument"; } 2>/dev/null || true
  previous=$argument
done
out='' keep_work=0 update_lock=0 archive='' server='' cache='' seed=''
while (( $# > 0 )); do
  case $1 in
    --out) (( $# > 1 )) || usage; out=$2; shift 2 ;;
    --results|--release|--purpose|--cache|--seed-cache)
      (( $# > 1 )) && [[ -n $2 ]] || usage
      case $1 in
        --results) results=$2 ;;
        --release) release=$2 ;;
        --purpose) purpose=$2 ;;
        --cache) cache=$2 ;;
        --seed-cache) seed=$2 ;;
      esac
      shift 2
      ;;
    --keep-work) keep_work=1; shift ;;
    --update-lock) update_lock=1; shift ;;
    --archive) (( $# > 1 )) || usage; archive=$2; shift 2 ;;
    -*) usage ;;
    *) [[ -z $server ]] || usage; server=$1; shift ;;
  esac
done
[[ -n $results || -z $release$purpose ]] || usage
# A seed installs and runs nothing else, so it takes no server and writes no
# stream; a restored install has no lock to update.
[[ -z $seed || ( -z $archive$server$results$cache && $update_lock == 0 ) ]] || usage
[[ -z $cache || $update_lock == 0 ]] || usage
case ${purpose:=development} in
  release|development|compatibility|canary|demo) ;;
  *) usage ;;
esac
# Every input path is resolved before the script changes directory.
here=$(cd "$(dirname "$0")" && pwd -P)
repo=$(cd "$here/../../.." && pwd -P)
# The stream is emptied before any other command, so that an earlier run's
# stream can't outlive a run that stops early. Until the teardown's trap is
# set, this one writes it.
if [[ -n $results ]]; then
  { : > "$results"; } 2>/dev/null || { echo "$0: cannot write $results" >&2; usage; }
  results=$(cd "$(dirname "$results")" && pwd -P)/$(basename "$results")
  observations=$(mktemp "${TMPDIR:-/tmp}/virgil-lazyvim-observations.XXXXXX") || { echo "$0: cannot create a temporary file" >&2; usage; }
  trap 'rc=$?; stop_check "run.sh exited with status $rc" exit-status; results_end "$rc"' EXIT
fi
begin_check lazyvim.infrastructure.preflight

[[ -n $seed || ( -n $archive$server && ( -z $archive || -z $server ) ) ]] || usage "give either --archive or a server"
for value in "$install_timeout" "$run_timeout"; do
  [[ $value =~ ^[1-9][0-9]*$ ]] || die "deadlines must be whole seconds: $value"
done

if [[ -n $seed ]]; then
  mkdir -p "$seed" || die "cannot create $seed"
  seed=$(cd "$seed" && pwd -P)
  [[ -z $(ls -A "$seed") ]] || die "the cache directory is not empty: $seed"
elif [[ -n $archive ]]; then
  [[ -f $archive ]] || die "archive not found: $archive"
  archive=$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")
  archive_dir=$(basename "$archive" .tar.gz)
  [[ $archive_dir != "$(basename "$archive")" ]] || die "not a .tar.gz archive: $archive"
else
  [[ -f $server ]] || die "server not found: $server"
  server_dir=$(cd "$(dirname "$server")" && pwd -P)
  [[ -x $server_dir/virgil-lsp && -x $server_dir/virgil-lsp-worker ]] ||
    die "$server_dir must hold the executables virgil-lsp and virgil-lsp-worker"
fi
if [[ -n $out ]]; then
  mkdir -p "$out" || die "cannot create $out"
  out=$(cd "$out" && pwd -P)
  [[ -z $(ls -A "$out") ]] || die "the output directory is not empty: $out"
fi

# A seed runs no profile, so it needs no tmux.
tools=(nvim git python3)
[[ -n $seed ]] || tools+=(tmux)
for tool in "${tools[@]}"; do
  command -v "$tool" >/dev/null 2>&1 ||
    die "$tool not found on PATH; the LazyVim test needs Neovim 0.11 or later, tmux, git, and python3"
done
nvim_version=$(nvim --version)
nvim_version=${nvim_version%%$'\n'*}
re='^NVIM v([0-9]+)\.([0-9]+)'
[[ $nvim_version =~ $re ]] || die "could not determine Neovim's version: $nvim_version"
(( BASH_REMATCH[1] > 0 || BASH_REMATCH[2] >= 11 )) || die "requires Neovim 0.11 or later, not $nvim_version"
# The fixture project's virgilDependencies resolve against the pinned checkout.
virgil_loc=$repo/vendor/virgil
[[ -n $seed || -f $virgil_loc/lib/util/StringBuilder.v3 ]] ||
  die "Virgil sources not found at $virgil_loc; run: git submodule update --init vendor/virgil"

# A seeded cache holds the starter, the plugins with what mason.nvim installed
# for LazyVim, and the parsers, as a run without one has them once its parsers
# are installed. Its ready file, written last, names what they were installed
# for, which a restore must match line for line, and when.
sha256() { python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"; }
cache_identity() {
  echo "starter $starter_commit"
  echo "neovim $nvim_version"
  echo "system $(uname -sm)"
  echo "lazy-lock.json $(sha256 "$here/lazy-lock.json")"
  echo "parsers.lua $(sha256 "$here/parsers.lua")"
  echo "virgil.lua $(sha256 "$repo/editors/nvim/lazyvim/virgil.lua")"
}
# relink DIR FROM TO: points each symbolic link under DIR whose target starts
# with FROM to the same place under TO instead. nvim-treesitter links its
# queries into the data directory by absolute paths, which a seed keeps under
# @DATA@ and a restore points into its own data directory.
relink() {
  local link target
  while IFS= read -r -d '' link; do
    target=$(readlink "$link")
    [[ $target == "$2"* ]] || continue
    ln -sfn "$3${target#"$2"}" "$link"
  done < <(find "$1" -type l -print0)
}
cache_hit=0
if [[ -n $cache ]]; then
  if [[ -f $cache/ready ]]; then
    cache=$(cd "$cache" && pwd -P)
    [[ -d $cache/config/nvim && -d $cache/data/nvim && -f $cache/parsers.txt ]] ||
      die "the cache $cache is ready but lacks config/nvim, data/nvim, or parsers.txt"
    identity=$(cache_identity) || die "cannot compute the cache's identity"
    if [[ $(grep -v '^seeded ' "$cache/ready") != "$identity" ]]; then
      echo "run.sh: the cache's ready file and this run differ:" >&2
      diff <(grep -v '^seeded ' "$cache/ready") <(printf '%s\n' "$identity") | sed 's/^/   /' >&2 || true
      die "the cache $cache was seeded for another installation; seed it again with --seed-cache"
    fi
    cache_hit=1
    seeded=$(sed -n 's/^seeded //p' "$cache/ready")
  fi
fi

if [[ -z $out ]]; then
  out=$(mktemp -d "${TMPDIR:-/tmp}/virgil-lazyvim-results.XXXXXX")
  out=$(cd "$out" && pwd -P)
fi
# tmux's socket path, like any Unix socket's, is limited to about 104
# characters, which macOS's $TMPDIR under /var/folders nearly fills.
if [[ $(uname -s) == Darwin ]]; then tmp=/tmp; else tmp=${TMPDIR:-/tmp}; fi
helper=''    # PID of a running deadline helper
current=''   # name of the run in progress
status=0     # 1 once anything fails

# deadline SECONDS COMMAND...: runs COMMAND in a new process group, as
# test/e2e/nvim/run.sh does, and on expiry kills its process tree and group
# and exits 124. SIGTERM or SIGHUP kills them the same way.
read -r -d '' deadline_py <<'PY' || true
import os, signal, subprocess, sys

def tree(root):
    listing = subprocess.run(["ps", "-ax", "-o", "pid=,ppid="], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout
    children = {}
    for line in listing.splitlines():
        pid, ppid = (int(field) for field in line.split())
        children.setdefault(ppid, []).append(pid)
    found, todo = [], [root]
    while todo:
        pid = todo.pop()
        found.append(pid)
        todo.extend(children.get(pid, []))
    return found

def stop(code):
    for pid in tree(process.pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    process.wait()
    sys.exit(code)

process = subprocess.Popen(sys.argv[2:], start_new_session=True)
for sig in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, lambda signum, frame: stop(128 + signum))
try:
    sys.exit(process.wait(timeout=float(sys.argv[1])))
except subprocess.TimeoutExpired:
    stop(124)
PY

# Runs a deadline helper in the background, so that a signal interrupts the
# wait at once, and returns its status.
deadline() {
  local rc=0
  python3 -c "$deadline_py" "$@" &
  helper=$!
  wait "$helper" || rc=$?
  helper=''
  return "$rc"
}

# descendants PIDS: prints those of the PIDS, separated by spaces or
# newlines, that are running, and the PIDs of all their descendants. Zombies
# don't count. The PIDS go through the environment: macOS's awk rejects a
# newline in a -v value. Fails if the processes can't be listed.
descendants() {
  local table
  table=$(ps -ax -o pid=,ppid=,stat=) && [[ -n $table ]] || return 1
  printf '%s\n' "$table" | R=$1 awk '
    $3 !~ /^Z/ { parent[$1] = $2 }
    END {
      n = split(ENVIRON["R"], list, " ")
      for (i = 1; i <= n; i++) if (list[i] in parent) keep[list[i]] = 1
      do {
        added = 0
        for (pid in parent) if (!(pid in keep) && (parent[pid] in keep)) { keep[pid] = 1; added = 1 }
      } while (added)
      for (pid in keep) print pid
    }'
}

# Prints "RUN PID NAME [ANCHOR]" for each line that observer.lua wrote to
# RUN.pids.
records() {
  local file name
  for file in "$work"/*.pids; do
    [[ -f $file ]] || continue
    name=${file##*/}
    awk -v r="${name%.pids}" '{ print r, $0 }' "$file"
  done
}

# run_processes [groups]: prints "PID COMMAND" for each process of the runs
# still running: the server and worker processes that observer.lua recorded;
# the processes in the process group of each Neovim that it recorded, which
# keeps what Neovim started, such as a parser build, also once Neovim has
# exited and they no longer descend from it; and any process whose command
# line holds the work directory. A Neovim's group counts only while the anchor
# that observer.lua recorded with it runs in it, under its name: once a group
# is empty, its number can be any new group's. With groups, it prints instead
# "+ RUN ANCHOR" for each run whose Neovim's group counts, and "- RUN" for each
# other, except the runs in checked. It reads the process table once, and
# fails if it can't; a recorded process that is gone is just not printed.
run_processes() {
  local table
  table=$(ps -ax -o pid=,pgid=,stat=,command=) && [[ -n $table ]] || return 1
  printf '%s\n' "$table" | M=${1:-} P=$(records) C=$checked W=$work/ awk '
    BEGIN {
      n = split(ENVIRON["P"], line, "\n")
      for (i = 1; i <= n; i++) {
        if (split(line[i], f, " ") < 3) continue
        if (f[3] != "nvim") server[f[2]] = 1
        else { nvim[f[1]] = f[2]; anchor[f[1]] = f[4]; if (f[4] != "") run[f[4]] = f[1] }
      }
    }
    {
      cmd = $0
      sub(/^ *[0-9]+ +[0-9]+ +[^ ]+ +/, "", cmd)
      pid[NR] = $1; pgid[NR] = $2; command[NR] = cmd
      if (($1 in run) && $2 == nvim[run[$1]] && $3 !~ /^Z/ && cmd == (ENVIRON["W"] run[$1] ".anchor 86400"))
        counts[run[$1]] = 1
    }
    END {
      if (ENVIRON["M"] == "groups") {
        for (r in nvim) {
          if (index(ENVIRON["C"], " " r " ")) continue
          if (r in counts) print "+", r, anchor[r]; else print "-", r
        }
        exit
      }
      for (r in counts) group[nvim[r]] = 1
      for (i = 1; i <= NR; i++)
        if ((pgid[i] in group) || ((pid[i] in server) && index(command[i], "virgil-lsp")) ||
          index(command[i], ENVIRON["W"]))
          print pid[i], command[i]
    }'
}

# run_trees PIDS: prints the running PIDS, the processes that run_processes
# finds, and all their descendants; fails if the processes can't be listed.
run_trees() {
  local roots
  roots=$(run_processes) || return 1
  descendants "$1 $(printf '%s\n' "$roots" | awk '{ print $1 }')"
}

# stopping is 1 while stop_processes runs, and pending then holds the exit
# status of a signal that came meanwhile. frozen holds the PIDs that it has
# frozen and not yet killed.
stopping=0 pending='' frozen=' '
# lost is 1 once a call failed: its processes may have outlived their
# parents unseen, so a later call that finds none doesn't make the teardown
# complete.
lost=0
# checked holds the runs whose Neovim's process group a call has checked.
checked=' '

# Stops each tmux server and every process of the runs: the servers' process
# trees, the processes that run_processes finds, and all their descendants.
# Processes start while others stop, such as the C compiler that builds a
# tree-sitter parser, and an orphan no longer descends from the process that
# started it. So they are first frozen with SIGSTOP, until a look finds no new
# one: a stopped process neither starts another nor exits. A tmux server
# would send SIGCONT to its pane's stopped processes, so it stops first. Then
# they get SIGKILL. SIGTERM would need SIGCONT, and on SIGTERM Neovim runs on
# for seconds, and starts processes. If a look can't list the processes, it
# tries again after 0.2 seconds, up to five times, with those found so far
# still frozen. Then every 0.2 seconds it looks again and kills any process
# it finds; if any is left after five seconds, or the processes couldn't be
# listed, it prints so and fails. Before the kills, it checks each Neovim's
# process group that no call has checked: if the group doesn't count, what
# Neovim left in it can't be found, and it fails; if it does, its anchor is
# killed last, once nothing else is left, so that the looks until then still
# find the group's processes. A signal meanwhile exits only after that,
# and a stopped tmux server would answer no query: so a query that gets no
# answer in two seconds is given up, and if a call didn't finish, the next
# kills what it froze without asking the servers.
stop_processes() {
  local list new pids sock pid groups state name anchors left tries=0 fails=0 listed=1 rc=0
  stopping=1
  if [[ $frozen == ' ' ]]; then
    for sock in "$work"/*.sock; do
      [[ -S $sock ]] || continue
      # A tmux client passes its output to the server, and a stopped server
      # would hold a pipe open; so the PID goes through a file.
      deadline 2 tmux -S "$sock" display-message -p '#{pid}' > "$work/tmux.pid" 2>/dev/null || true
      pid=$(cat "$work/tmux.pid" 2>/dev/null || true)
      if [[ $pid =~ ^[0-9]+$ ]]; then
        frozen="$frozen$pid "
        kill -STOP "$pid" 2>/dev/null || true
      fi
    done
  fi
  while (( tries < 25 )); do
    if ! list=$(run_trees "$frozen"); then
      fails=$((fails + 1))
      if (( fails == 5 )); then listed=0; break; fi
      sleep 0.2
      continue
    fi
    new=''
    for pid in $list; do
      [[ $frozen == *" $pid "* ]] || new="$new$pid "
    done
    [[ -n $new ]] || break
    frozen="$frozen$new"
    for pid in $new; do kill -STOP "$pid" 2>/dev/null || true; done
    tries=$((tries + 1))
  done
  anchors=' '
  if (( listed )); then
    if groups=$(run_processes groups); then
      while read -r state name pid; do
        [[ -n $state ]] || continue
        checked="$checked$name "
        if [[ $state == + ]]; then
          anchors="$anchors$pid "
        else
          echo "FAIL: $name's Neovim has no anchor in its process group, so what Neovim left there can't be found"
          rc=1
        fi
      done <<< "$groups"
    else
      listed=0
    fi
  fi
  pids=$frozen tries=0
  while :; do
    for pid in $pids; do
      [[ $anchors == *" $pid "* ]] || kill -KILL "$pid" 2>/dev/null || true
    done
    frozen=$anchors
    if ! pids=$(run_trees "$pids"); then
      listed=0
      break
    fi
    left=''
    for pid in $pids; do
      [[ $anchors == *" $pid "* ]] || left="$left$pid "
    done
    if [[ -z $left && $anchors != ' ' ]]; then
      anchors=' '
      continue
    fi
    [[ -n $pids ]] || break
    if (( tries == 25 )); then
      echo "FAIL: processes still running after SIGKILL:"
      for pid in $left; do ps -p "$pid" -o pid=,command= 2>/dev/null | sed 's/^/   /'; done
      rc=1
      break
    fi
    sleep 0.2
    tries=$((tries + 1))
  done
  for pid in $anchors; do kill -KILL "$pid" 2>/dev/null || true; done
  frozen=' '
  if (( ! listed )); then
    echo "FAIL: could not list the processes of the runs"
    rc=1
  fi
  (( rc == 0 )) || lost=1
  stopping=0
  if [[ -n $pending ]]; then
    rc=$pending pending=''
    exit "$rc"
  fi
  return "$rc"
}

# save_screen NAME WHEN: appends the screen of NAME's pane, with its
# scrollback, to its screen file, while its session is still open. The pane
# stays open after Neovim exits, so this also keeps what Neovim printed.
save_screen() {
  local name=$1 sock=$work/$1.sock
  if [[ -S $sock ]] && deadline 2 tmux -S "$sock" has-session -t "$name" >/dev/null 2>&1; then
    {
      echo "== screen $2"
      deadline 5 tmux -S "$sock" capture-pane -p -S - -t "$name" 2>&1 || true
    } >> "$out/$name.screen.txt"
  fi
}

cleanup() {
  local rc=$?
  set +eu
  trap '' INT TERM HUP
  exec 1>&3 2>&4
  if [[ -n $helper ]]; then
    kill -TERM "$helper" 2>/dev/null
    wait "$helper" 2>/dev/null
  fi
  if [[ -n $current ]]; then
    echo "FAIL: $current did not finish; its screen and LSP log are in $out"
    save_screen "$current" "when the run was stopped"
    if [[ -f ${XDG_STATE_HOME:-}/nvim/lsp.log ]]; then
      mv "$XDG_STATE_HOME/nvim/lsp.log" "$out/$current.lsp.log"
    fi
    (( rc != 0 )) || rc=1
  fi
  if [[ -n $interrupted ]]; then
    stop_check "run.sh was interrupted by SIG$interrupted" interrupted
  else
    stop_check "run.sh exited with status $rc" exit-status
  fi
  # The work directory is removed only once no process of the runs is left.
  begin_check lazyvim.infrastructure.cleanup
  if stop_processes && (( ! lost )); then
    echo "-- no process of the runs left running"
    pass_check "every process of the runs was stopped, and none was left running"
  else
    (( ! lost )) || echo "FAIL: an earlier teardown failed, so processes of the runs may be left running"
    fail_check "processes of the runs may be left running: stopping them failed, or an earlier teardown did" \
      processes
    (( rc != 0 )) || rc=1
    keep_work=1
  fi
  if (( keep_work )); then
    echo "work directory kept: $work"
  else
    rm -rf "$work"
  fi
  echo "results: $out"
  [[ -z $results ]] || results_end "$rc"
  exit "$rc"
}

on_signal() {
  local rc
  interrupted=$1
  echo "run.sh: interrupted by SIG$1" >&4
  case $1 in
    INT) rc=130 ;;
    TERM) rc=143 ;;
    *) rc=129 ;;
  esac
  # stop_processes exits once it has killed what it froze.
  if (( stopping )); then pending=${pending:-$rc}; else exit "$rc"; fi
}

work=$(mktemp -d "${tmp%/}/virgil-lazyvim.XXXXXX")
# The traps print to the script's own output, on 3 and 4, even when a signal
# interrupts a command whose output goes to a file, such as the plugins'
# install.
exec 3>&1 4>&2
trap cleanup EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP
work=$(cd "$work" && pwd -P)
(( ${#work} <= 70 )) || die "the work directory's path is too long for tmux's socket: $work; set TMPDIR"
if [[ -n $archive ]]; then given="the archive $(basename "$archive")"; else given="the server and its worker"; fi
if (( cache_hit )); then given="$given; the matching cache $cache"; fi
pass_check "$nvim_version; nvim, tmux, git, and python3 on PATH; $given; an empty output directory; the Virgil sources"

echo "results: $out"
echo "work directory: $work"
if [[ -n $seed ]]; then
  echo "seeding the cache: $seed"
elif (( cache_hit )); then
  echo "cache: $cache, seeded $seeded"
elif [[ -n $cache ]]; then
  echo "cache: none ready in $cache; installing from GitHub"
fi
cd "$work"

# Neovim's state stays in the work directory: XDG directories, plugins, and
# logs. The tmux server, and so each pane, inherits this environment.
export XDG_CONFIG_HOME=$work/config XDG_DATA_HOME=$work/data
export XDG_STATE_HOME=$work/state XDG_CACHE_HOME=$work/cache
export NVIM_APPNAME=nvim VIRGIL_HARNESS=$here
unset VIMINIT MYVIMRC NVIM NVIM_LOG_FILE NVIM_LISTEN_ADDRESS TMUX TMUX_PANE VIRGIL_LOC
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME" "$work/bin"

# The server, installed as its archive's README says: extracted into one
# directory, with a symlink to virgil-lsp on PATH; or a build's directory. A
# seed has none.
if [[ -z $seed ]]; then
  begin_check lazyvim.infrastructure.artifact-start
  if [[ -n $archive ]]; then
    mkdir "$work/server"
    tar -xzf "$archive" -C "$work/server"
    [[ $(ls -A "$work/server") == "$archive_dir" && -d $work/server/$archive_dir ]] ||
      die "the archive does not hold just the directory $archive_dir"
    ln -s "$work/server/$archive_dir/virgil-lsp" "$work/bin/virgil-lsp"
    export PATH="$work/bin:$PATH"
  else
    export PATH="$server_dir:$PATH"
  fi
  # The rootless profiles deliberately model an incomplete installation. Keep
  # their MissingVirgilRoot and noice warning checks meaningful for archives
  # that now carry a default library. The main profile uses the real install.
  # The JVM build's launchers also need their jars and Java runtime beside them;
  # the runtime can be a symlink, since the server finds its library from the
  # launcher's own directory, which has no virgil/.
  mkdir "$work/no-bundle"
  installed_server=$(python3 -c 'import os, shutil; print(os.path.realpath(shutil.which("virgil-lsp")))')
  installed_dir=$(dirname "$installed_server")
  cp "$installed_server" "$installed_dir/virgil-lsp-worker" "$work/no-bundle/"
  for jar in virgil-lsp.jar virgil-lsp-worker.jar; do
    if [[ -f $installed_dir/$jar ]]; then cp "$installed_dir/$jar" "$work/no-bundle/"; fi
  done
  if [[ -d $installed_dir/runtime ]]; then ln -s "$installed_dir/runtime" "$work/no-bundle/runtime"; fi
  if [[ -n $results ]]; then
    if [[ -n $archive ]]; then
      results_py server "$observations" --server "$(command -v virgil-lsp)" --archive "$archive" \
        --archive-root "$work/server/$archive_dir" || true
    else
      results_py server "$observations" --server "$(command -v virgil-lsp)" || true
    fi
  fi
  server_version=$(virgil-lsp --version 2>&1) || die "virgil-lsp does not run here: $server_version"
  pass_check "virgil-lsp --version: ${server_version%%$'\n'*}${archive:+; the archive holds just $archive_dir}"
fi

# The provenance header, printed and kept in the output directory.
{
  echo "date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    dirty=$(git --no-optional-locks -C "$repo" status --porcelain 2>/dev/null || true)
    echo "repository: $repo at $(git -C "$repo" rev-parse HEAD)${dirty:+ (modified)}"
  else
    echo "repository: $repo (not a Git checkout)"
  fi
  if [[ -n $seed ]]; then
    :
  elif git -C "$virgil_loc" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Virgil root: $virgil_loc at $(git -C "$virgil_loc" rev-parse HEAD)"
  else
    echo "Virgil root: $virgil_loc (not a Git checkout)"
  fi
  if [[ $(uname -s) == Darwin ]]; then
    echo "OS: $(sw_vers -productName) $(sw_vers -productVersion) ($(sw_vers -buildVersion)), $(uname -srm)"
    echo "CPU: $(sysctl -n machdep.cpu.brand_string)"
  else
    echo "OS: $(sed -n 's/^PRETTY_NAME="\(.*\)"$/\1/p' /etc/os-release 2>/dev/null), $(uname -srm)"
    echo "CPU: $(awk -F': ' '/^model name/ { print $2; exit }' /proc/cpuinfo 2>/dev/null)"
  fi
  echo "Neovim: $nvim_version ($(command -v nvim))"
  echo "tmux: $(tmux -V) ($(command -v tmux))"
  echo "git: $(git --version); $(python3 --version 2>&1); bash $BASH_VERSION"
  if [[ -n $archive ]]; then
    echo "archive: $archive"
    echo "archive SHA-256: $(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$archive")"
  fi
  if [[ -z $seed ]]; then
    echo "virgil-lsp on PATH: $(command -v virgil-lsp) ($(file -bL "$(command -v virgil-lsp)" 2>/dev/null))"
    echo "$server_version" | sed 's/^/  /'
  fi
  echo "LazyVim starter: $starter_url at $starter_commit"
  if (( cache_hit )); then
    echo "cache: $cache, seeded $seeded; the starter, plugins, and parsers restored from it"
  fi
} > "$out/provenance.txt"
cat "$out/provenance.txt"

# The starter at its commit, with the repository's plugin spec. A cache's
# starter was checked out at the commit that its ready file names, which
# matches.
begin_check lazyvim.infrastructure.starter
if (( cache_hit )); then
  cp -R "$cache/config/nvim" "$XDG_CONFIG_HOME/nvim"
  from=", restored from the cache"
else
  git clone -q "$starter_url" "$XDG_CONFIG_HOME/nvim"
  git -C "$XDG_CONFIG_HOME/nvim" checkout -q "$starter_commit"
  [[ $(git -C "$XDG_CONFIG_HOME/nvim" rev-parse HEAD) == "$starter_commit" ]] || die "starter checkout failed"
  rm -rf "$XDG_CONFIG_HOME/nvim/.git"
  from=''
fi
cp "$repo/editors/nvim/lazyvim/virgil.lua" "$XDG_CONFIG_HOME/nvim/lua/plugins/virgil.lua"
pass_check "the starter at $starter_commit$from, with editors/nvim/lazyvim/virgil.lua"

# Prints "NAME COMMIT" for each plugin of a lazy-lock.json, sorted by name.
lock_entries() {
  python3 -c 'import json, sys
for name, entry in sorted(json.load(open(sys.argv[1])).items()):
    print(name, entry["commit"])' "$1"
}

# preinstall NAME URL [COMMIT]: clones a plugin where lazy.nvim keeps it, at
# COMMIT if given.
preinstall() {
  git clone -q --filter=blob:none "$2" "$lazy_dir/$1"
  if [[ -n ${3:-} ]]; then git -C "$lazy_dir/$1" checkout -q "$3"; fi
}

# The starter's lua/config/lazy.lua clones lazy.nvim's stable branch if it
# isn't installed, so it is installed first, at the lock's commit. LazyVim is
# too: lazy.nvim would otherwise install it alone to import its plugin specs,
# then rewrite the lock from what is installed, and install the rest at their
# latest commits. Then :Lazy! restore checks out every plugin at the lock's
# commit; --update-lock instead takes the latest of each. With a cache, the
# plugins, and what mason.nvim installed, are copied from it instead: the seed
# installed them so, and pins-before checks their commits as without one.
# mason.nvim downloads its registry again during the runs once its update
# file, in Neovim's cache directory, is a day old or missing; a run without a
# cache has just downloaded it. So a restore keeps the seed's update file with the
# time of the restore, and mason.nvim uses the registry it installed from.
lazy_dir=$XDG_DATA_HOME/nvim/lazy
begin_check lazyvim.infrastructure.plugins-install
install_started=$SECONDS started=$SECONDS
if (( cache_hit )); then
  cp -R "$cache/data/nvim" "$XDG_DATA_HOME/nvim"
  relink "$XDG_DATA_HOME/nvim" @DATA@ "$XDG_DATA_HOME"
  if [[ -f $cache/cache/nvim/mason-registry-update ]]; then
    mkdir -p "$XDG_CACHE_HOME/nvim"
    printf '%s\n%s' "$(head -n 1 "$cache/cache/nvim/mason-registry-update")" "$(date +%s)" \
      > "$XDG_CACHE_HOME/nvim/mason-registry-update"
  fi
  cp "$here/lazy-lock.json" "$XDG_CONFIG_HOME/nvim/lazy-lock.json"
  echo "-- restored the plugins from the cache in $((SECONDS - started)) s"
  pass_check "restored from the cache that run.sh --seed-cache installed with :Lazy! restore, seeded $seeded," \
    "in $((SECONDS - started)) s"
else
  if (( update_lock )); then
    preinstall lazy.nvim "$lazy_url"
    action=sync
  else
    preinstall lazy.nvim "$lazy_url" "$(lock_entries "$here/lazy-lock.json" | awk '$1 == "lazy.nvim" { print $2 }')"
    preinstall LazyVim "$lazyvim_url" "$(lock_entries "$here/lazy-lock.json" | awk '$1 == "LazyVim" { print $2 }')"
    cp "$here/lazy-lock.json" "$XDG_CONFIG_HOME/nvim/lazy-lock.json"
    action=restore
  fi
  echo "-- installing the plugins (:Lazy! $action); log in $out/lazy-install.log"
  rc=0
  deadline "$install_timeout" nvim --headless "+Lazy! $action" +qa > "$out/lazy-install.log" 2>&1 || rc=$?
  (( rc != 124 )) ||
    failure_code=deadline die "installing the plugins exceeded $install_timeout seconds; see $out/lazy-install.log"
  (( rc == 0 )) || failure_code=exit-status die "installing the plugins failed with status $rc; see $out/lazy-install.log"
  echo "   done in $((SECONDS - started)) s"
  if (( update_lock )); then
    cp "$XDG_CONFIG_HOME/nvim/lazy-lock.json" "$here/lazy-lock.json"
    echo "-- wrote $here/lazy-lock.json; review it with git diff"
  fi
  pass_check ":Lazy! $action exited with status 0 after $((SECONDS - started)) s," \
    "within the $install_timeout-second deadline"
fi

# Every plugin in the lock must be installed at its commit, and the lock that
# lazy.nvim wrote back must list the same plugins and commits.
verify_plugins() {
  local name commit head ok=1
  lock_entries "$here/lazy-lock.json" > "$work/lock.txt"
  while read -r name commit; do
    head=$(git -C "$lazy_dir/$name" rev-parse HEAD 2>/dev/null || echo "not installed")
    if [[ $head == "$commit" ]]; then
      echo "plugin $name $commit ($(git -C "$lazy_dir/$name" describe --tags --always 2>/dev/null))"
    else
      echo "FAIL: plugin $name is at $head, not the locked $commit"
      ok=0
    fi
  done < "$work/lock.txt"
  if ! lock_entries "$XDG_CONFIG_HOME/nvim/lazy-lock.json" | diff "$work/lock.txt" - >/dev/null; then
    echo "FAIL: lazy.nvim's lock differs from lazy-lock.json:"
    lock_entries "$XDG_CONFIG_HOME/nvim/lazy-lock.json" | diff "$work/lock.txt" - | sed 's/^/   /'
    ok=0
  fi
  (( ok ))
}
begin_check lazyvim.infrastructure.pins-before
if ! verify_plugins > "$work/plugins.txt"; then
  cat "$work/plugins.txt"
  failure_code=pins die "the plugins are not at the locked commits"
fi
cat "$work/plugins.txt" >> "$out/provenance.txt"
cat "$work/plugins.txt"
pass_check "$(grep -c '^plugin ' "$work/plugins.txt") plugins at the commits of lazy-lock.json," \
  "which lazy.nvim's lock matches"

# nvim-treesitter's parsers, installed by parsers.lua as LazyVim installs them
# at a first start, under what is left of the deadline for installing the
# plugins. Otherwise LazyVim would download and build them in the background
# during main. Meanwhile, a plugin spec leaves LazyVim's configuration of
# nvim-treesitter only its setup, so that LazyVim starts no install of its own.
#
# A cache's parsers.txt is what parsers.lua wrote for the seed, with the data
# directory as @DATA@. Each parser it lists must be installed at its
# revision, and the tree-sitter CLI it names must be there, as parsers.lua
# left them.
begin_check lazyvim.infrastructure.parsers-ready
if (( cache_hit )); then
  sed "s|@DATA@|$XDG_DATA_HOME|g" "$cache/parsers.txt" > "$work/parsers.txt"
  ok=1 count=0
  parsers=$XDG_DATA_HOME/nvim/site
  while read -r kind lang revision; do
    case $kind in
      tree-sitter)
        cli=${revision##*(} cli=${cli%)}
        [[ -x $cli ]] || { echo "FAIL: the tree-sitter CLI is not at $cli"; ok=0; }
        ;;
      parser)
        count=$((count + 1))
        if [[ ! -f $parsers/parser/$lang.so ]]; then
          echo "FAIL: parser $lang is not installed"
          ok=0
        elif [[ $revision != '?' && $(head -n 1 "$parsers/parser-info/$lang.revision" 2>/dev/null) != "$revision" ]]; then
          echo "FAIL: parser $lang is not at revision $revision"
          ok=0
        fi
        ;;
    esac
  done < "$work/parsers.txt"
  (( ok && count > 0 )) || die "the cache's parsers are not installed as its parsers.txt lists"
  cat "$work/parsers.txt" >> "$out/provenance.txt"
  cat "$work/parsers.txt"
  pass_check "the $count parsers of the cache's parsers.txt are installed at their revisions"
else
  cat > "$XDG_CONFIG_HOME/nvim/lua/plugins/zz-install-parsers.lua" <<'LUA'
return { { "nvim-treesitter/nvim-treesitter", config = function(_, opts) require("nvim-treesitter").setup(opts) end } }
LUA
  echo "-- installing nvim-treesitter's parsers; log in $out/parsers-install.log"
  started=$SECONDS
  left=$((install_timeout - (SECONDS - install_started)))
  (( left > 0 )) || failure_code=deadline die "installing the plugins and parsers exceeded $install_timeout seconds"
  rc=0
  VIRGIL_PARSERS_OUT=$work/parsers.txt deadline "$left" nvim --headless \
    -c 'lua dofile(vim.env.VIRGIL_HARNESS .. "/parsers.lua")' > "$out/parsers-install.log" 2>&1 || rc=$?
  (( rc != 124 )) || failure_code=deadline \
    die "installing the plugins and parsers exceeded $install_timeout seconds; see $out/parsers-install.log"
  (( rc == 0 )) ||
    failure_code=exit-status die "installing the parsers failed with status $rc; see $out/parsers-install.log"
  rm "$XDG_CONFIG_HOME/nvim/lua/plugins/zz-install-parsers.lua"
  echo "   done in $((SECONDS - started)) s"
  cat "$work/parsers.txt" >> "$out/provenance.txt"
  cat "$work/parsers.txt"
  pass_check "the parsers installed with status 0 after $((SECONDS - started)) s, within the install deadline"
fi

# A seed keeps what is installed and stops: the starter without virgil.lua,
# which a restore copies from the checkout, the data directory, mason.nvim's
# registry update file, and parsers.txt, then the ready file, last, so that
# an interrupted seed leaves no cache that a restore would use.
if [[ -n $seed ]]; then
  echo "-- writing the cache to $seed"
  mkdir "$seed/config" "$seed/data" "$seed/cache"
  if [[ -f $XDG_CACHE_HOME/nvim/mason-registry-update ]]; then
    mkdir "$seed/cache/nvim"
    cp "$XDG_CACHE_HOME/nvim/mason-registry-update" "$seed/cache/nvim/"
  fi
  cp -R "$XDG_CONFIG_HOME/nvim" "$seed/config/nvim"
  rm "$seed/config/nvim/lua/plugins/virgil.lua"
  cp -R "$XDG_DATA_HOME/nvim" "$seed/data/nvim"
  relink "$seed/data/nvim" "$XDG_DATA_HOME" @DATA@
  # Any other link into the work directory would dangle once it is gone.
  while IFS= read -r -d '' link; do
    [[ $(readlink "$link") != "$work"* ]] || die "the cache would hold a link into the work directory: $link"
  done < <(find "$seed" -type l -print0)
  sed "s|$XDG_DATA_HOME|@DATA@|g" "$work/parsers.txt" > "$seed/parsers.txt"
  { cache_identity; echo "seeded $(date -u '+%Y-%m-%dT%H:%M:%SZ')"; } > "$seed/ready.tmp"
  mv "$seed/ready.tmp" "$seed/ready"
  cat "$seed/ready"
  echo "LazyVim cache: seeded" > "$out/summary.txt"
  exit 0
fi

cp -R "$repo/test/e2e/nvim/fixture" "$work/project"
cp "$here/fixture/words/CallPicker.v3" "$work/project/words/"
record_digest fixture "$work/project"
# One run: Neovim with the observer and the driver, then its exit status,
# which the pane also exits with. The status file appears whole, once.
cat > "$work/pane.sh" <<'SH'
TERM=xterm-256color nvim -n --cmd 'lua dofile(vim.env.VIRGIL_HARNESS .. "/observer.lua")' \
  -c 'lua dofile(vim.env.VIRGIL_HARNESS .. "/driver.lua")' "$VIRGIL_DRIVER_FILE"
status=$?
echo "$status" > "$VIRGIL_DRIVER_STATUS.tmp"
mv "$VIRGIL_DRIVER_STATUS.tmp" "$VIRGIL_DRIVER_STATUS"
exit "$status"
SH
# wait.sh STATUS_FILE PANE_PID: waits until pane.sh has written its status,
# or the pane's shell is gone without one.
cat > "$work/wait.sh" <<'SH'
while [ ! -e "$1" ] && kill -0 "$2" 2>/dev/null; do sleep 0.2; done
SH

summary=''
# run NAME MODE FILE NOICE_WORKAROUND EXPECTED_XFAIL: one Neovim session in a
# new tmux server, driven by driver.lua.
run() {
  local name=$1 mode=$2 file=$3 workaround=$4 expected_xfail=$5
  local driver_out=$out/$name.results sock=$work/$1.sock started=$SECONDS rc=0
  local exit_status complete=0 passed='?' failed='?' xfail='?' verdict=PASS left
  local tmux_pid='' pane_pid='' anchor_pid='' processes_ok=1 lingering
  current=$name
  record_digest "configuration:$name" "$XDG_CONFIG_HOME/nvim"
  begin_check lazyvim.infrastructure.profile-complete "$name"
  rm -f "$XDG_STATE_HOME/nvim/lsp.log"
  echo "== $name"
  (
    if [[ $mode == main ]]; then
      export VIRGIL_LOC=$virgil_loc
    else
      export PATH="$work/no-bundle:$PATH"
    fi
    if [[ -n $results ]]; then
      export VIRGIL_DRIVER_OBSERVATIONS=$observations VIRGIL_DRIVER_PROFILE=$name
    fi
    export VIRGIL_DRIVER_MODE=$mode VIRGIL_DRIVER_FILE=$file
    export VIRGIL_DRIVER_NOICE_WORKAROUND=$workaround VIRGIL_DRIVER_OUT=$driver_out
    export VIRGIL_DRIVER_SCREEN=$out/$name.screen.txt VIRGIL_DRIVER_PIDS=$work/$name.pids
    export VIRGIL_DRIVER_ANCHOR=$work/$name.anchor VIRGIL_DRIVER_STATUS=$work/$name.status
    # The pane stays open when Neovim exits, for its screen, and keeps the
    # tmux server running until stop_processes. The script's path is an
    # argument of its own, not split by a shell.
    tmux -S "$sock" -f /dev/null set-option -g remain-on-exit on \; \
      new-session -d -P -F '#{pid} #{pane_pid}' -s "$name" -x 200 -y 50 -c "$work/project" \
      /bin/sh "$work/pane.sh"
  ) > "$work/$name.tmux"
  read -r tmux_pid pane_pid < "$work/$name.tmux" || true
  # The run ends once Neovim has exited and pane.sh has written its status, or
  # the pane's shell is gone without one. It doesn't wait for tmux to find the
  # pane dead: Ubuntu 24.04's tmux 3.4, built with utempter, can miss the
  # shell's exit and then never does (tmux/tmux#4559).
  deadline "$run_timeout" /bin/sh "$work/wait.sh" "$work/$name.status" "$pane_pid" || rc=$?
  if (( rc == 124 )); then
    echo "FAIL: $name exceeded $run_timeout seconds; screen in $out/$name.screen.txt"
    save_screen "$name" "when the run timed out"
    stop_processes || true
    verdict=FAIL
  fi
  exit_status=$(cat "$work/$name.status" 2>/dev/null || echo '?')
  # Complete results end with their only DONE line, whole, as driver.lua
  # writes it.
  re='^DONE ([0-9]+) passed, ([0-9]+) failed, ([0-9]+) expected failure\(s\)$'
  if [[ -f $driver_out ]] && (( $(grep -c '^DONE' "$driver_out" || true) == 1 )) &&
    [[ -z $(tail -c 1 "$driver_out") && $(tail -n 1 "$driver_out") =~ $re ]]; then
    complete=1 passed=${BASH_REMATCH[1]} failed=${BASH_REMATCH[2]} xfail=${BASH_REMATCH[3]}
  fi
  echo "-- Neovim exit status $exit_status, after $((SECONDS - started)) s"
  if [[ -f $driver_out ]] && (( ! complete )); then echo "-- $driver_out does not end with one complete DONE line"; fi
  if (( rc != 124 )) && [[ $exit_status != 0 || $complete == 0 ]]; then
    echo "-- the screen after Neovim exited is in $out/$name.screen.txt"
    save_screen "$name" "after Neovim exited"
    verdict=FAIL
  fi
  current=''
  if (( rc == 124 )); then
    fail_check "Neovim was still running at its $run_timeout-second deadline" deadline
  elif [[ $exit_status != 0 ]]; then
    fail_check "Neovim exited with status $exit_status" exit-status
  elif (( ! complete )); then
    fail_check "$name.results does not end with one complete DONE line" incomplete-results
  elif [[ $failed != 0 ]]; then
    fail_check "$failed check(s) failed" check-failed
  elif [[ $xfail != "$expected_xfail" ]]; then
    fail_check "$xfail expected failure(s), not $expected_xfail" xfail-count
  else
    pass_check "Neovim exited with status 0 after $((SECONDS - started)) s;" \
      "$passed passed, 0 failed, $xfail expected failure(s), as expected"
  fi
  begin_check lazyvim.infrastructure.profile-processes "$name"
  cat "$driver_out" 2>/dev/null || echo "FAIL: no results"
  local log=$XDG_STATE_HOME/nvim/lsp.log
  if [[ -f $log ]]; then
    echo "-- server stderr in the LSP log:"
    grep '"stderr"' "$log" | sed -n '1,40s/^/   /p' || echo "   (none)"
    echo "-- LSP log lines at WARN or ERROR, other than stderr:"
    grep -E '^\[(WARN|ERROR)\]' "$log" | grep -v '"stderr"' | sed -n '1,40s/^/   /p' || echo "   (none)"
    echo "-- response errors:"
    grep -oE 'error = \{ code = -?[0-9]+, message = "[^"]*"' "$log" | sed -n '1,20s/^/   /p' || echo "   (none)"
    mv "$log" "$out/$name.lsp.log"
  fi
  # Neovim stopped the clients; their server and worker processes must be
  # gone. Anything else that the run started is stopped here.
  local tries=0
  while left=$(run_processes | grep virgil-lsp || true); [[ -n $left ]] && (( tries < 50 )); do
    sleep 0.2
    tries=$((tries + 1))
  done
  lingering=$left
  if [[ -n $left ]]; then
    echo "FAIL: processes left running after $name:"
    echo "$left" | sed 's/^/   /'
    verdict=FAIL
    processes_ok=0
  fi
  # What else Neovim left running, such as a plugin's download, is named but
  # doesn't fail the run: it isn't the server's. Its anchor isn't listed.
  anchor_pid=$(awk '$2 == "nvim" { print $3; exit }' "$work/$name.pids" 2>/dev/null || true)
  left=$(run_processes | awk -v t="$tmux_pid" -v p="$pane_pid" -v a="$anchor_pid" \
    '$1 != t && $1 != p && $1 != a { print "   " $0 }' || true)
  if [[ -n $left ]]; then
    echo "-- left running after Neovim exited, and stopped:"
    echo "$left"
  fi
  stop_processes || { verdict=FAIL; processes_ok=0; }
  if [[ -n $lingering ]]; then
    fail_check "virgil-lsp processes left running 10 s after Neovim exited: $(printf "%s" "$lingering" | tr "\n" ";")" \
      processes-left
  elif (( ! processes_ok )); then
    fail_check "stopping the run's processes failed" processes
  else
    pass_check "no virgil-lsp process was left after Neovim exited, and the run's other processes were stopped"
  fi
  if [[ $complete == 0 || $exit_status != 0 || $failed != 0 || $xfail != "$expected_xfail" ]]; then
    verdict=FAIL
  fi
  [[ $verdict == PASS ]] || status=1
  summary="$summary$name: $passed passed, $failed failed, $xfail expected failure(s) (expected $expected_xfail); Neovim exit status $exit_status; $((SECONDS - started)) s: $verdict"$'\n'
  summary="$summary$(grep '^XFAIL ' "$driver_out" 2>/dev/null | sed 's/^/  /' || true)"
  [[ $summary == *$'\n' ]] || summary="$summary"$'\n'
}

run main main "$work/project/bad.v3" 0 0
run noroot noroot "$work/project/.virgil-lsp.json" 0 1
# The workaround for noice.nvim dropping window/showMessage (folke/noice.nvim#1074).
cat > "$XDG_CONFIG_HOME/nvim/lua/plugins/zz-noice-showmessage.lua" <<'LUA'
return { { "folke/noice.nvim", opts = { lsp = { message = { enabled = false } } } } }
LUA
run noroot-noice-workaround noroot "$work/project/.virgil-lsp.json" 1 0
rm "$XDG_CONFIG_HOME/nvim/lua/plugins/zz-noice-showmessage.lua"

# The runs must not have changed the plugins.
begin_check lazyvim.infrastructure.pins-after
if ! verify_plugins > "$work/plugins.txt"; then
  cat "$work/plugins.txt"
  echo "FAIL: the plugins changed during the runs"
  status=1
  fail_check "the plugins changed during the runs" pins
else
  pass_check "the plugins are still at the commits of lazy-lock.json"
fi

if (( status == 0 )); then result=PASS; else result=FAIL; fi
printf '%s' "$summary" > "$out/summary.txt"
echo "LazyVim test: $result" >> "$out/summary.txt"
echo "== summary"
cat "$out/summary.txt"
exit "$status"
