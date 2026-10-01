#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Invoke the pinned Virgil compiler, targeting the host platform.
#
# Environment:
#   VIRGIL       Virgil checkout to use (default: vendor/virgil submodule).
#   VIRGIL_V3C   Aeneas binary to compile with (default: the checkout's
#                prebuilt bin/stable compiler for the host).
#   V3C_TARGET   Override the detected host target, e.g. x86-64-darwin.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VIRGIL=${VIRGIL:-$ROOT/vendor/virgil}

if [ ! -x "$VIRGIL/bin/dev/sense_host" ]; then
    echo "error: Virgil sources not found at $VIRGIL" >&2
    echo "       run: git submodule update --init --recursive" >&2
    exit 1
fi

if [ -z "${V3C_TARGET:-}" ]; then
    HOSTS=" $("$VIRGIL/bin/dev/sense_host") "
    # Prefer 64-bit targets; sense_host lists 32-bit targets first.
    for t in x86-64-linux x86-64-darwin x86-linux x86-darwin; do
        case "$HOSTS" in *" $t "*) V3C_TARGET=$t; break ;; esac
    done
fi
if [ -z "${V3C_TARGET:-}" ] || [ ! -x "$VIRGIL/bin/v3c-$V3C_TARGET" ]; then
    echo "error: no supported Virgil target for this host (detected:${HOSTS:- none})" >&2
    if [ "$(uname -sm)" = "Darwin arm64" ]; then
        echo "       Apple Silicon needs Rosetta 2 until Virgil supports arm64-darwin:" >&2
        echo "       softwareupdate --install-rosetta --agree-to-license" >&2
    fi
    echo "       see docs/compatibility.md for supported platforms" >&2
    exit 1
fi

if [ -z "${VIRGIL_V3C:-}" ]; then
    VIRGIL_V3C=$VIRGIL/bin/stable/$V3C_TARGET/Aeneas
fi
if [ ! -x "$VIRGIL_V3C" ]; then
    echo "error: Aeneas compiler not found at $VIRGIL_V3C" >&2
    exit 1
fi

V3C=$VIRGIL_V3C exec "$VIRGIL/bin/v3c-$V3C_TARGET" "$@"
