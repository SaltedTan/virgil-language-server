#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Tests the shipped LazyVim configuration (editors/nvim/lazyvim/virgil.lua)
# under the pinned LazyVim starter and plugins, in a real terminal UI: each run
# starts Neovim in a detached tmux session, and driver.lua drives it through
# LazyVim's own keys. See README.md for the checks, outputs, and pins.
#
#   run.sh [--out DIR] [--keep-work] [--update-lock] (--archive ARCHIVE | SERVER)
#
# ARCHIVE is a release archive, virgil-lsp-<version>-x86-64-<os>.tar.gz;
# SERVER is a built virgil-lsp with virgil-lsp-worker beside it.
set -euo pipefail

# The LazyVim starter's commit. The plugins' commits, including lazy.nvim's,
# are in lazy-lock.json.
starter_commit=803bc181d7c0d6d5eeba9274d9be49b287294d99
starter_url=https://github.com/LazyVim/starter
lazy_url=https://github.com/folke/lazy.nvim.git
lazyvim_url=https://github.com/LazyVim/LazyVim.git
# Deadlines in seconds: for installing the plugins, and for each run.
install_timeout=${VIRGIL_LAZYVIM_INSTALL_TIMEOUT:-600}
run_timeout=${VIRGIL_LAZYVIM_RUN_TIMEOUT:-240}

usage() {
  echo "usage: $0 [--out DIR] [--keep-work] [--update-lock] (--archive ARCHIVE | SERVER)" >&2
  exit 2
}

die() {
  echo "run.sh: $*" >&2
  exit 1
}

out='' keep_work=0 update_lock=0 archive='' server=''
while (( $# > 0 )); do
  case $1 in
    --out) (( $# > 1 )) || usage; out=$2; shift 2 ;;
    --keep-work) keep_work=1; shift ;;
    --update-lock) update_lock=1; shift ;;
    --archive) (( $# > 1 )) || usage; archive=$2; shift 2 ;;
    -*) usage ;;
    *) [[ -z $server ]] || usage; server=$1; shift ;;
  esac
done
[[ -n $archive$server && ( -z $archive || -z $server ) ]] || usage
for value in "$install_timeout" "$run_timeout"; do
  [[ $value =~ ^[1-9][0-9]*$ ]] || die "deadlines must be whole seconds: $value"
done

# Every input path is resolved before the script changes directory.
here=$(cd "$(dirname "$0")" && pwd -P)
repo=$(cd "$here/../../.." && pwd -P)
if [[ -n $archive ]]; then
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

for tool in nvim tmux git python3; do
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
[[ -f $virgil_loc/lib/util/StringBuilder.v3 ]] ||
  die "Virgil sources not found at $virgil_loc; run: git submodule update --init vendor/virgil"

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
  # The work directory is removed only once no process of the runs is left.
  if stop_processes && (( ! lost )); then
    echo "-- no process of the runs left running"
  else
    (( ! lost )) || echo "FAIL: an earlier teardown failed, so processes of the runs may be left running"
    (( rc != 0 )) || rc=1
    keep_work=1
  fi
  if (( keep_work )); then
    echo "work directory kept: $work"
  else
    rm -rf "$work"
  fi
  echo "results: $out"
  exit "$rc"
}

on_signal() {
  local rc
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

echo "results: $out"
echo "work directory: $work"
cd "$work"

# Neovim's state stays in the work directory: XDG directories, plugins, and
# logs. The tmux server, and so each pane, inherits this environment.
export XDG_CONFIG_HOME=$work/config XDG_DATA_HOME=$work/data
export XDG_STATE_HOME=$work/state XDG_CACHE_HOME=$work/cache
export NVIM_APPNAME=nvim VIRGIL_HARNESS=$here
unset VIMINIT MYVIMRC NVIM NVIM_LOG_FILE NVIM_LISTEN_ADDRESS TMUX TMUX_PANE VIRGIL_LOC
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME" "$work/bin"

# The server, installed as its archive's README says: extracted into one
# directory, with a symlink to virgil-lsp on PATH; or a build's directory.
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
server_version=$(virgil-lsp --version 2>&1) || die "virgil-lsp does not run here: $server_version"

# The provenance header, printed and kept in the output directory.
{
  echo "date: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    dirty=$(git --no-optional-locks -C "$repo" status --porcelain 2>/dev/null || true)
    echo "repository: $repo at $(git -C "$repo" rev-parse HEAD)${dirty:+ (modified)}"
  else
    echo "repository: $repo (not a Git checkout)"
  fi
  if git -C "$virgil_loc" rev-parse --git-dir >/dev/null 2>&1; then
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
  echo "virgil-lsp on PATH: $(command -v virgil-lsp) ($(file -bL "$(command -v virgil-lsp)" 2>/dev/null))"
  echo "$server_version" | sed 's/^/  /'
  echo "LazyVim starter: $starter_url at $starter_commit"
} > "$out/provenance.txt"
cat "$out/provenance.txt"

# The starter at its commit, with the repository's plugin spec.
git clone -q "$starter_url" "$XDG_CONFIG_HOME/nvim"
git -C "$XDG_CONFIG_HOME/nvim" checkout -q "$starter_commit"
[[ $(git -C "$XDG_CONFIG_HOME/nvim" rev-parse HEAD) == "$starter_commit" ]] || die "starter checkout failed"
rm -rf "$XDG_CONFIG_HOME/nvim/.git"
cp "$repo/editors/nvim/lazyvim/virgil.lua" "$XDG_CONFIG_HOME/nvim/lua/plugins/virgil.lua"

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
# commit; --update-lock instead takes the latest of each.
lazy_dir=$XDG_DATA_HOME/nvim/lazy
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
install_started=$SECONDS started=$SECONDS
rc=0
deadline "$install_timeout" nvim --headless "+Lazy! $action" +qa > "$out/lazy-install.log" 2>&1 || rc=$?
(( rc != 124 )) || die "installing the plugins exceeded $install_timeout seconds; see $out/lazy-install.log"
(( rc == 0 )) || die "installing the plugins failed with status $rc; see $out/lazy-install.log"
echo "   done in $((SECONDS - started)) s"
if (( update_lock )); then
  cp "$XDG_CONFIG_HOME/nvim/lazy-lock.json" "$here/lazy-lock.json"
  echo "-- wrote $here/lazy-lock.json; review it with git diff"
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
if ! verify_plugins > "$work/plugins.txt"; then
  cat "$work/plugins.txt"
  die "the plugins are not at the locked commits"
fi
cat "$work/plugins.txt" >> "$out/provenance.txt"
cat "$work/plugins.txt"

# nvim-treesitter's parsers, installed by parsers.lua as LazyVim installs them
# at a first start, under what is left of the deadline for installing the
# plugins. Otherwise LazyVim would download and build them in the background
# during main. Meanwhile, a plugin spec leaves LazyVim's configuration of
# nvim-treesitter only its setup, so that LazyVim starts no install of its own.
cat > "$XDG_CONFIG_HOME/nvim/lua/plugins/zz-install-parsers.lua" <<'LUA'
return { { "nvim-treesitter/nvim-treesitter", config = function(_, opts) require("nvim-treesitter").setup(opts) end } }
LUA
echo "-- installing nvim-treesitter's parsers; log in $out/parsers-install.log"
started=$SECONDS
left=$((install_timeout - (SECONDS - install_started)))
(( left > 0 )) || die "installing the plugins and parsers exceeded $install_timeout seconds"
rc=0
VIRGIL_PARSERS_OUT=$work/parsers.txt deadline "$left" nvim --headless \
  -c 'lua dofile(vim.env.VIRGIL_HARNESS .. "/parsers.lua")' > "$out/parsers-install.log" 2>&1 || rc=$?
(( rc != 124 )) ||
  die "installing the plugins and parsers exceeded $install_timeout seconds; see $out/parsers-install.log"
(( rc == 0 )) || die "installing the parsers failed with status $rc; see $out/parsers-install.log"
rm "$XDG_CONFIG_HOME/nvim/lua/plugins/zz-install-parsers.lua"
echo "   done in $((SECONDS - started)) s"
cat "$work/parsers.txt" >> "$out/provenance.txt"
cat "$work/parsers.txt"

cp -R "$repo/test/e2e/nvim/fixture" "$work/project"
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
  local results=$out/$name.results sock=$work/$1.sock started=$SECONDS rc=0
  local exit_status complete=0 passed='?' failed='?' xfail='?' verdict=PASS left
  local tmux_pid='' pane_pid='' anchor_pid=''
  current=$name
  rm -f "$XDG_STATE_HOME/nvim/lsp.log"
  echo "== $name"
  (
    if [[ $mode == main ]]; then export VIRGIL_LOC=$virgil_loc; fi
    export VIRGIL_DRIVER_MODE=$mode VIRGIL_DRIVER_FILE=$file
    export VIRGIL_DRIVER_NOICE_WORKAROUND=$workaround VIRGIL_DRIVER_OUT=$results
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
  if [[ -f $results ]] && (( $(grep -c '^DONE' "$results" || true) == 1 )) &&
    [[ -z $(tail -c 1 "$results") && $(tail -n 1 "$results") =~ $re ]]; then
    complete=1 passed=${BASH_REMATCH[1]} failed=${BASH_REMATCH[2]} xfail=${BASH_REMATCH[3]}
  fi
  echo "-- Neovim exit status $exit_status, after $((SECONDS - started)) s"
  if [[ -f $results ]] && (( ! complete )); then echo "-- $results does not end with one complete DONE line"; fi
  if (( rc != 124 )) && [[ $exit_status != 0 || $complete == 0 ]]; then
    echo "-- the screen after Neovim exited is in $out/$name.screen.txt"
    save_screen "$name" "after Neovim exited"
    verdict=FAIL
  fi
  current=''
  cat "$results" 2>/dev/null || echo "FAIL: no results"
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
  if [[ -n $left ]]; then
    echo "FAIL: processes left running after $name:"
    echo "$left" | sed 's/^/   /'
    verdict=FAIL
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
  stop_processes || verdict=FAIL
  if [[ $complete == 0 || $exit_status != 0 || $failed != 0 || $xfail != "$expected_xfail" ]]; then
    verdict=FAIL
  fi
  [[ $verdict == PASS ]] || status=1
  summary="$summary$name: $passed passed, $failed failed, $xfail expected failure(s) (expected $expected_xfail); Neovim exit status $exit_status; $((SECONDS - started)) s: $verdict"$'\n'
  summary="$summary$(grep '^XFAIL ' "$results" 2>/dev/null | sed 's/^/  /' || true)"
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
if ! verify_plugins > "$work/plugins.txt"; then
  cat "$work/plugins.txt"
  echo "FAIL: the plugins changed during the runs"
  status=1
fi

if (( status == 0 )); then result=PASS; else result=FAIL; fi
printf '%s' "$summary" > "$out/summary.txt"
echo "LazyVim test: $result" >> "$out/summary.txt"
echo "== summary"
cat "$out/summary.txt"
exit "$status"
