#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0

# Prototype (#186): runs the Remote-SSH test with a Mac as the client and this
# Linux machine as the host, from this machine. host.cjs provisions the host
# here, on an address that the Mac reaches, such as this machine's Tailscale
# address; the Mac runs run.cjs --target in a study directory, which must
# already hold a copy of clients/vscode with its npm dependencies, the
# fixture, and the VSIX files; then host.cjs takes the host down. Usage, from
# clients/vscode:
#
#   test/e2e/remote-ssh/mac-client.sh --mac SSH-ALIAS --study DIR --listen ADDR --host-name NAME
#     --allow-from ADDR --vsix MAC-PATH [--client-vsix MAC-PATH] --out DIR [--work-dir DIR] [--repeat N] [--keep-work]
#
# SSH-ALIAS is how this machine reaches the Mac; DIR is the study directory on
# the Mac, which holds repo/, and gets w/ and results/; MAC-PATH is relative
# to DIR. See the README's "Remote-SSH test from a Mac".
# The results, with the Mac's manifest and the host's teardown, go in --out
# here. The exit status is run.cjs's, made 3 if the host's teardown could not
# confirm that nothing is left, or if the Mac could not be reached.

set -u
mac='' study='' listen='' host_name='' allow_from='' vsix='' client_vsix='' out='' work_dir=${TMPDIR:-/tmp} repeat=1 keep_work=''
while [ $# -gt 0 ]; do
  case $1 in
    --keep-work) keep_work=--keep-work; shift; continue ;;
    --mac) mac=$2 ;; --study) study=$2 ;; --listen) listen=$2 ;; --host-name) host_name=$2 ;;
    --allow-from) allow_from=$2 ;; --vsix) vsix=$2 ;; --client-vsix) client_vsix=$2 ;;
    --out) out=$2 ;; --work-dir) work_dir=$2 ;; --repeat) repeat=$2 ;;
    *) echo "mac-client.sh: unknown argument $1" >&2; exit 2 ;;
  esac
  shift 2
done
for name in mac study listen host_name allow_from vsix out; do
  if [ -z "${!name}" ]; then echo "mac-client.sh: --${name//_/-} is required" >&2; exit 2; fi
done
if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then echo "mac-client.sh: $out is not empty" >&2; exit 2; fi
mkdir -p "$out"
out=$(cd "$out" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
bundle=$(mktemp -d "$work_dir/vrsb-XXXXXX")
rmdir "$bundle"
hold=$(mktemp -u "$work_dir/vrsf-XXXXXX")
mkfifo -m 600 "$hold"
started=$(date +%s)

# host.cjs runs until its standard input, the FIFO that fd 7 holds open, closes.
node "$here/host.cjs" --listen "$listen" --host-name "$host_name" --allow-from "$allow_from" \
  --bundle "$bundle" --work-dir "$work_dir" < "$hold" > "$out/host.log" 2>&1 &
host_pid=$!
exec 7> "$hold"
rm -f "$hold"
mac_pidfile="$study/results/$stamp.pid"
stop_host() {
  local host_wait_status
  exec 7>&-
  while :; do
    wait "$host_pid"
    host_wait_status=$?
    kill -0 "$host_pid" 2>/dev/null || return "$host_wait_status"
  done
}
interrupted=
# shellcheck disable=SC2329 # Invoked by the traps below.
on_signal() {
  interrupted=$1
  # Stop run.cjs on the Mac by the PID that it recorded, which runs its own
  # teardown, then the host.
  ssh -o BatchMode=yes "$mac" "pid=\$(cat '$mac_pidfile' 2>/dev/null) && kill -TERM \"\$pid\"" || true
}
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

for _ in $(seq 100); do
  [ -n "$interrupted" ] && break
  grep -q '^READY ' "$out/host.log" && break
  kill -0 "$host_pid" 2>/dev/null || break
  sleep 0.2
done
if ! grep -q '^READY ' "$out/host.log"; then
  cat "$out/host.log"
  stop_host
  if [ -n "$interrupted" ]; then
    echo "RESULT: INTERRUPTED (the host was not provisioned)"
    exit "$interrupted"
  fi
  echo "RESULT: INFRA (the host was not provisioned)"
  exit 3
fi
cat "$out/host.log"

status=0
evidence_reasons=()
evidence_failure() {
  echo "INFRA: $1" >&2
  evidence_reasons+=("$1")
  if [ "$status" -eq 0 ]; then status=3; fi
}
mac_bundle="$study/bundles/$stamp"
if [ -z "$interrupted" ]; then
  if ! ssh -o BatchMode=yes "$mac" "mkdir -p '$mac_bundle' && chmod 700 '$mac_bundle'"; then
    evidence_failure 'could not prepare the Mac bundle'
  elif [ -z "$interrupted" ]; then
    scp -q "$bundle/target.json" "$bundle/client" "$mac:$mac_bundle/" || evidence_failure 'could not copy the target bundle to the Mac'
  fi
fi
rm -f "$bundle/client"

client_args=
[ -n "$client_vsix" ] && client_args="--client-vsix '$study/$client_vsix'"
if [ "$status" -eq 0 ] && [ -z "$interrupted" ]; then
  # The Mac's PATH for a non-interactive SSH session lacks Homebrew's node.
  # SSH runs in the background, so that a trap runs while it does: bash
  # runs traps only between commands, and wait is interruptible.
  ssh -o BatchMode=yes -o ServerAliveInterval=30 "$mac" "export PATH=/opt/homebrew/bin:\$PATH TMPDIR='$study/tmp' npm_config_cache='$study/npm-cache'
    mkdir -p \"\$TMPDIR\" '$study/w' '$study/results' && cd '$study/repo/clients/vscode' && echo \$\$ > '$mac_pidfile' && exec node test/e2e/remote-ssh/run.cjs \
      --target '$mac_bundle/target.json' --vsix '$study/$vsix' --bundled $client_args $keep_work --repeat '$repeat' \
      --out '$study/results/$stamp' --work-dir '$study/w'" > "$out/mac-run.log" 2>&1 < /dev/null &
  ssh_pid=$!
  tail -n +1 -f --pid="$ssh_pid" "$out/mac-run.log" &
  tail_pid=$!
  while :; do
    wait "$ssh_pid"
    status=$?
    kill -0 "$ssh_pid" 2>/dev/null || break
  done
  wait "$tail_pid"
  if [ "$status" -eq 255 ]; then
    echo "INFRA: SSH to $mac failed"
    status=3
  fi
  ssh -o BatchMode=yes "$mac" "rm -rf '$mac_bundle' '$mac_pidfile'" || true
  scp -q -r "$mac:$study/results/$stamp" "$out/mac" || evidence_failure 'could not copy the Mac results'
fi

stop_host
host_status=$?
tail -n +2 "$out/host.log" | grep -v '^READY ' || true
cp "$bundle"/host-teardown.json "$bundle"/sshd.log "$out/" 2>/dev/null || evidence_failure 'could not copy the host evidence'
rm -rf "$bundle"
if [ "$host_status" -ne 0 ]; then evidence_failure 'the host teardown failed'; fi
[ -f "$out/mac/manifest.json" ] || evidence_failure 'the Mac wrote no manifest'
[ -f "$out/host-teardown.json" ] || evidence_failure 'the host wrote no teardown evidence'
[ -n "$interrupted" ] && status=$interrupted

# The combined manifest: the Mac's, with the host's teardown and the result
# that it makes.
if assembled_status=$(node - "$out" "$status" "$started" "$here" "${evidence_reasons[@]}" <<'EOF'
const fs = require('node:fs');
const path = require('node:path');
const [out, status, started, here, ...reasons] = process.argv.slice(2);
const file = path.join(out, 'mac', 'manifest.json');
const manifest = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : { result: null, reasons: [] };
const teardown = path.join(out, 'host-teardown.json');
manifest.hostTeardown = fs.existsSync(teardown) ? JSON.parse(fs.readFileSync(teardown, 'utf8')) : null;
manifest.macResult = manifest.result ?? null;
manifest.reasons ??= [];
manifest.reasons.push(...reasons);
if (manifest.hostTeardown?.ok !== true) manifest.reasons.push('the host teardown could not confirm that nothing of the run is left');
const preferenceReason = require(path.join(here, 'prefs.cjs')).notRestoredReason(manifest.teardown?.preferences);
if (preferenceReason !== undefined) {
  if (!manifest.reasons.includes(preferenceReason)) manifest.reasons.push(preferenceReason);
  console.error(`PREFERENCES NOT RESTORED: ${preferenceReason}`);
}
manifest.exitStatus = Number(status) === 0 && (reasons.length > 0 || manifest.hostTeardown?.ok !== true || manifest.teardown?.preferences?.outcome === 'failed') ? 3 : Number(status);
manifest.result = { 0: 'PASS', 1: 'FAIL', 2: 'USAGE', 3: 'INFRA', 129: 'INTERRUPTED', 130: 'INTERRUPTED', 143: 'INTERRUPTED' }[manifest.exitStatus] ?? 'INFRA';
manifest.wallSeconds = Math.round(Date.now() / 1000 - Number(started));
fs.writeFileSync(path.join(out, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
console.log(manifest.exitStatus);
EOF
); then
  status=$assembled_status
else
  evidence_failure 'could not assemble the combined manifest'
  result=INFRA
  case $status in 1) result=FAIL ;; 2) result=USAGE ;; 129|130|143) result=INTERRUPTED ;; esac
  {
    printf '{"result":"%s","exitStatus":%s,"reasons":[' "$result" "$status"
    separator=''
    for reason in "${evidence_reasons[@]}"; do
      printf '%s"%s"' "$separator" "$reason"
      separator=,
    done
    printf ']}\n'
  } > "$out/manifest.json" || echo 'INFRA: could not write the combined manifest' >&2
fi
result=INFRA
case $status in 0) result=PASS ;; 1) result=FAIL ;; 2) result=USAGE ;; 129|130|143) result=INTERRUPTED ;; esac
echo "results: $out"
echo "RESULT: $result in $(($(date +%s) - started)) s"
exit "$status"
