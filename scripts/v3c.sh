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
#   V3C_TARGET   Override the detected host target, e.g. x86-64-darwin, or
#                select jvm, which detection never does (see below).
#
# With the single argument -print-target, print the selected target instead of
# compiling. The Makefile uses it to choose the host files under src/os/.
# With -print-compiler, print the path of the Aeneas binary that would compile;
# scripts/package-release.sh records its checksum.
#
# V3C_TARGET=jvm compiles <output>/<program-name>.jar with Aeneas's jar target
# and writes <output>/<program-name>, a launcher that runs it. The launcher
# names the jar and the JDK by absolute path, so a copy of it elsewhere runs
# the same jar but finds its worker beside the copy, as a copied native
# executable does. It also reads:
#   JVM_RT       The directory of the compiled src/os/jvm runtime classes
#                (required; the Makefile's jvm-runtime target builds it). Each
#                class goes into the jar.
#   JAVA         The java command whose JDK the launcher runs (default: java).
#                The launcher records that JDK's absolute path, since the
#                server starts its worker with an empty environment.
#   JVM_BUNDLED_RUNTIME
#                Write a relocatable launcher for a package instead: it runs
#                the jar beside its own resolved path on the java of the
#                runtime directory that this names, relative to the same
#                directory (such as runtime). scripts/package-release.sh sets it.
#   JVM_LOCALE   The launcher's LC_ALL (default: the build host's UTF-8 locale).
# -heap-size=<size> becomes the launcher's -Xmx; other arguments go to Aeneas.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VIRGIL=${VIRGIL:-$ROOT/vendor/virgil}

if [ ! -x "$VIRGIL/bin/dev/sense_host" ]; then
    echo "error: Virgil sources not found at $VIRGIL" >&2
    echo "       run: git submodule update --init --recursive" >&2
    exit 1
fi

# The JVM stack size for both programs, and the worker's heap from the
# Makefile's WORKER_HEAP, are provisional; see docs/testing-notes/jvm-memory.md
# for the measurements and proposal awaiting the maintainer's decision. The
# development launchers and the packaged ones (JVM_BUNDLED_RUNTIME) both take
# their options from here.
JVM_STACK=16m

# Compiles a jar and writes its launcher; see the header.
jvm_compile() {
    local heap="" output="" name="" arg
    local args=()
    for arg in "$@"; do
        case $arg in
            -heap-size=*) heap=${arg#-heap-size=}; continue ;;
            -output=*) output=${arg#-output=} ;;
            -program-name=*) name=${arg#-program-name=} ;;
        esac
        args+=("$arg")
    done
    if [ -z "$output" ] || [ -z "$name" ]; then
        echo "error: the jvm target needs -output= and -program-name=" >&2
        exit 1
    fi
    if [ -z "${JVM_RT:-}" ] || [ ! -f "$JVM_RT/V3S_System.class" ]; then
        echo "error: no JVM runtime classes at ${JVM_RT:-(JVM_RT unset)}; run make jvm-runtime" >&2
        exit 1
    fi
    local java_home
    java_home=$(${JAVA:-java} -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java\.home = //p')
    if [ -z "$java_home" ] || [ ! -x "$java_home/bin/java" ]; then
        echo "error: cannot find the JDK of '${JAVA:-java}'" >&2
        exit 1
    fi
    local jar=$output/$name.jar
    rm -f "$jar" "$output/$name"
    local absolute
    absolute=$(cd "$output" && pwd -P)/$name.jar
    case $java_home$absolute${JVM_BUNDLED_RUNTIME:-}${JVM_LOCALE:-} in *"'"*)
        echo "error: the JDK, output path, runtime, or locale contains a single quote" >&2
        exit 1 ;;
    esac
    if [ "$VIRGIL_V3C" = "$VIRGIL/bin/stable/jar/Aeneas" ]; then
        "$java_home/bin/java" -client -Xms900m -Xmx900m -XX:+UseSerialGC \
            -jar "$VIRGIL_V3C.jar" -target=jar -jvm.rt-path="$JVM_RT" "${args[@]}"
    else
        "$VIRGIL_V3C" -target=jar -jvm.rt-path="$JVM_RT" "${args[@]}"
    fi
    # Aeneas copies only V3S_System.class, and the runtime's nested classes
    # are needed too. Their entries get a fixed date, as Aeneas's do, so the
    # jar is reproducible.
    python3 -I - "$jar" "$JVM_RT" <<'PY'
import os, sys, zipfile
jar, rt = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(jar, 'a') as z:
    have = set(z.namelist())
    for f in sorted(os.listdir(rt)):
        if f.endswith('.class') and f not in have:
            info = zipfile.ZipInfo(f, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            with open(os.path.join(rt, f), 'rb') as data:
                z.writestr(info, data.read())
PY
    local options="-Xss$JVM_STACK"
    if [ -n "$heap" ]; then options="$options -Xmx$heap"; fi
    # Java decodes native paths using the locale before opening the jar.
    # Workers have no inherited locale; use a UTF-8 locale on each host.
    local jvm_locale=C.UTF-8
    if [ "$(uname -s)" = Darwin ]; then jvm_locale=en_US.UTF-8; fi
    jvm_locale=${JVM_LOCALE:-$jvm_locale}
    # A development launcher names the JDK and jar by absolute path; a
    # packaged one finds them from its own directory, so the package can move.
    local java="'$java_home/bin/java'" jar_path="'$absolute'"
    if [ -n "${JVM_BUNDLED_RUNTIME:-}" ]; then
        java="\"\$dir/$JVM_BUNDLED_RUNTIME/bin/java\""
        jar_path="\"\$dir/$name.jar\""
    fi
    # The server starts its worker with an empty environment, so the launcher
    # sets PATH only for its own commands. As /proc/self/exe does natively, the
    # executable path that it passes resolves symlinks, so the worker and
    # library beside the real file are found.
    cat > "$output/$name" <<EOF
#!/bin/sh
# Generated by scripts/v3c.sh for the JVM target; do not edit.
PATH=\${PATH:-/usr/bin:/bin}
export LC_ALL='$jvm_locale'
self=\$0
case \$self in */*) ;; *) self=./\$self ;; esac
while [ -h "\$self" ]; do
    link=\$(readlink "\$self") || exit 1
    case \$link in /*) self=\$link ;; *) self=\${self%/*}/\$link ;; esac
done
dir=\$(cd -P "\${self%/*}" && pwd -P) || exit 1
exec $java $options -Dvirgil.lsp.executable="\$dir/\${self##*/}" -jar $jar_path "\$@"
EOF
    chmod +x "$output/$name"
}

if [ "${V3C_TARGET:-}" = jvm ]; then
    if [ "$#" -eq 1 ] && [ "$1" = "-print-target" ]; then
        echo jvm
        exit 0
    fi
    # Aeneas runs natively where the host has a Virgil target, and otherwise
    # on the JVM itself, from the pinned checkout's bin/stable/jar.
    if [ -z "${VIRGIL_V3C:-}" ]; then
        HOSTS=" $("$VIRGIL/bin/dev/sense_host") "
        VIRGIL_V3C=$VIRGIL/bin/stable/jar/Aeneas
        for t in x86-64-linux x86-64-darwin x86-linux x86-darwin; do
            case "$HOSTS" in *" $t "*) VIRGIL_V3C=$VIRGIL/bin/stable/$t/Aeneas; break ;; esac
        done
    fi
    if [ ! -x "$VIRGIL_V3C" ]; then
        echo "error: Aeneas compiler not found at $VIRGIL_V3C" >&2
        exit 1
    fi
    if [ "$#" -eq 1 ] && [ "$1" = "-print-compiler" ]; then
        echo "$VIRGIL_V3C"
        exit 0
    fi
    jvm_compile "$@"
    exit 0
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

if [ "$#" -eq 1 ] && [ "$1" = "-print-target" ]; then
    echo "$V3C_TARGET"
    exit 0
fi

if [ -z "${VIRGIL_V3C:-}" ]; then
    VIRGIL_V3C=$VIRGIL/bin/stable/$V3C_TARGET/Aeneas
fi
if [ ! -x "$VIRGIL_V3C" ]; then
    echo "error: Aeneas compiler not found at $VIRGIL_V3C" >&2
    exit 1
fi

if [ "$#" -eq 1 ] && [ "$1" = "-print-compiler" ]; then
    echo "$VIRGIL_V3C"
    exit 0
fi

V3C=$VIRGIL_V3C exec "$VIRGIL/bin/v3c-$V3C_TARGET" "$@"
