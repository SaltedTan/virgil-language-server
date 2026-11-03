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
# the Mac, which holds repo/, w/, and results/; MAC-PATH is relative to DIR.
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
  exec 7>&-
  wait "$host_pid"
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
  grep -q '^READY ' "$out/host.log" && break
  kill -0 "$host_pid" 2>/dev/null || break
  sleep 0.2
done
if ! grep -q '^READY ' "$out/host.log"; then
  cat "$out/host.log"
  stop_host
  echo "RESULT: INFRA (the host was not provisioned)"
  exit 3
fi
cat "$out/host.log"

status=0
mac_bundle="$study/bundles/$stamp"
if ! ssh -o BatchMode=yes "$mac" "mkdir -p '$mac_bundle' && chmod 700 '$mac_bundle'" \
  || ! scp -q "$bundle/target.json" "$bundle/client" "$mac:$mac_bundle/"; then
  echo "INFRA: could not copy the target's bundle to $mac"
  status=3
fi
rm -f "$bundle/client"

if [ "$status" -eq 0 ]; then
  client_args=
  [ -n "$client_vsix" ] && client_args="--client-vsix '$study/$client_vsix'"
  # The Mac's PATH for a non-interactive SSH session lacks Homebrew's node.
  # SSH runs in the background, so that a trap runs while it does: bash
  # runs traps only between commands, and wait is interruptible.
  ssh -o BatchMode=yes -o ServerAliveInterval=30 "$mac" "export PATH=/opt/homebrew/bin:\$PATH TMPDIR='$study/tmp' npm_config_cache='$study/npm-cache'
    mkdir -p \"\$TMPDIR\" && cd '$study/repo/clients/vscode' && echo \$\$ > '$mac_pidfile' && exec node test/e2e/remote-ssh/run.cjs \
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
  scp -q -r "$mac:$study/results/$stamp" "$out/mac" || echo "INFRA: could not copy the results from $mac"
fi

stop_host
host_status=$?
tail -n +2 "$out/host.log" | grep -v '^READY ' || true
cp "$bundle"/host-teardown.json "$bundle"/sshd.log "$out/" 2>/dev/null
rm -rf "$bundle"
if [ "$host_status" -ne 0 ] && { [ "$status" -eq 0 ] || [ "$status" -eq 1 ]; }; then status=3; fi
[ -n "$interrupted" ] && status=$interrupted

# The combined manifest: the Mac's, with the host's teardown and the result
# that it makes.
node - "$out" "$status" "$started" <<'EOF'
const fs = require('node:fs');
const path = require('node:path');
const [out, status, started] = process.argv.slice(2);
const file = path.join(out, 'mac', 'manifest.json');
const manifest = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : { result: null, reasons: ['the Mac wrote no manifest'] };
const teardown = path.join(out, 'host-teardown.json');
manifest.hostTeardown = fs.existsSync(teardown) ? JSON.parse(fs.readFileSync(teardown, 'utf8')) : null;
manifest.macResult = manifest.result ?? null;
manifest.exitStatus = Number(status);
manifest.result = { 0: 'PASS', 1: 'FAIL', 2: 'USAGE', 3: 'INFRA', 129: 'INTERRUPTED', 130: 'INTERRUPTED', 143: 'INTERRUPTED' }[status] ?? 'INFRA';
if (manifest.hostTeardown?.ok !== true) (manifest.reasons ??= []).push('the host teardown could not confirm that nothing of the run is left');
manifest.wallSeconds = Math.round(Date.now() / 1000 - Number(started));
fs.writeFileSync(path.join(out, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`results: ${out}\nRESULT: ${manifest.result} in ${manifest.wallSeconds} s`);
EOF
exit "$status"
