#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Checks that the JVM build runs on Apple Silicon without Rosetta translation.

  native.py host
      Requires an arm64 machine and an untranslated runner process. Reports
      whether the Rosetta runtime is installed and the status of
      `arch -x86_64 /usr/bin/true`; Rosetta's presence is allowed.

  native.py run --java GLOB [--min-java N] [--report FILE] -- COMMAND...
      Runs COMMAND, and while it runs records its observed descendants:
      its process ID, parent, executable path (proc_pidpath), command line,
      whether macOS runs it translated by Rosetta (P_TRANSLATED), and the CPU
      type of its executable's Mach-O header. It fails, after COMMAND ends, if
      COMMAND failed, if fewer than N Java processes were seen (default 1), or
      if any descendant was translated or its translation was unknown. Every
      Java process must also have an executable that matches GLOB (an
      fnmatch pattern, such as '*/runtime/bin/java' for a release archive's
      bundled runtime), and is an arm64 Mach-O file. --report
      writes the records as JSON. Processes are sampled every 50 ms, so one
      that ends sooner may be missed, and one that ends while it is read is
      counted apart; the server and its worker live for the whole of a
      session.

macOS only. CI runs it in the JVM jobs on Apple Silicon; see
docs/compatibility.md#host-platforms.
"""
import argparse
import ctypes
import ctypes.util
import fnmatch
import json
import os
import platform
import subprocess
import sys
import threading

ROSETTA_RUNTIME = "/Library/Apple/usr/libexec/oah/libRosettaRuntime"
CPU_TYPE_ARM64 = 0x0100000C
CPU_TYPE_X86_64 = 0x01000007
INTERVAL = 0.05


def fail(message):
    sys.exit("native.py: error: " + message)


def libraries():
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    return libc, libproc


def translated(libc, pid):
    """Whether macOS runs pid under Rosetta (P_TRANSLATED), or None if unknown,
    as scripts/jvm-memory.py reads it."""
    mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
    buf = ctypes.create_string_buffer(1024)
    size = ctypes.c_size_t(len(buf))
    if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or size.value < 36:
        return None
    # kinfo_proc.kp_proc.p_flag follows a 16-byte union and two pointers.
    return bool(int.from_bytes(buf.raw[32:36], "little") & 0x00020000)


def pidpath(libproc, pid):
    buf = ctypes.create_string_buffer(4096)
    n = libproc.proc_pidpath(pid, buf, ctypes.c_uint32(4096))
    return os.fsdecode(buf.value) if n > 0 else None


def process_args(libc, pid):
    """The command line of pid, from KERN_PROCARGS2, or [] if unreadable."""
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t()
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return []
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return []
    argc_size = ctypes.sizeof(ctypes.c_int)
    argc = ctypes.c_int.from_buffer_copy(buf.raw[:argc_size]).value
    args = buf.raw[argc_size:size.value].split(b"\0", 1)[1].lstrip(b"\0")
    return [os.fsdecode(arg) for arg in args.split(b"\0")[:argc]]


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def process_parents():
    out = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
    return {int(pid): int(ppid) for pid, ppid in (line.split() for line in out.splitlines())}


def cpu_types(path):
    """The CPU types of a Mach-O file, thin or universal, or [] if it isn't one."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return []
    magic = head[:4]
    if magic == b"\xcf\xfa\xed\xfe":  # MH_MAGIC_64, little-endian
        return [int.from_bytes(head[4:8], "little")]
    if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):  # FAT_MAGIC, FAT_MAGIC_64
        count = int.from_bytes(head[4:8], "big")
        width = 20 if magic == b"\xca\xfe\xba\xbe" else 32
        return [int.from_bytes(head[8 + i * width:12 + i * width], "big") for i in range(min(count, 32))]
    return []


def cpu_name(cpu):
    return {CPU_TYPE_ARM64: "arm64", CPU_TYPE_X86_64: "x86_64"}.get(cpu, hex(cpu))


def require_macos():
    if sys.platform != "darwin":
        fail("this check runs only on macOS")


def host(_args):
    require_macos()
    libc, _ = libraries()
    problems = []
    machine = platform.machine()
    print("machine: %s" % machine)
    if machine != "arm64":
        problems.append("the machine is %s, not arm64" % machine)
    self_translated = translated(libc, os.getpid())
    proc_translated = subprocess.run(["sysctl", "-n", "sysctl.proc_translated"], capture_output=True,
                                     text=True).stdout.strip()
    print("this process translated (P_TRANSLATED): %s; sysctl.proc_translated: %s"
          % (self_translated, proc_translated or "(none)"))
    if self_translated is not False or proc_translated != "0":
        problems.append("this process runs translated, or its translation is unknown")
    present = os.path.exists(ROSETTA_RUNTIME)
    print("Rosetta runtime %s: %s" % (ROSETTA_RUNTIME, "present" if present else "absent"))
    x86 = subprocess.run(["arch", "-x86_64", "/usr/bin/true"], capture_output=True, text=True)
    print("arch -x86_64 /usr/bin/true: status %d%s" % (
        x86.returncode, (": " + x86.stderr.strip()) if x86.stderr.strip() else ""))
    if problems:
        fail("this host is not untranslated Apple Silicon: " + "; ".join(problems))
    print("Apple Silicon: this runner process is untranslated")


def run(args):
    require_macos()
    if not args.command:
        fail("no command to run after --")
    libc, libproc = libraries()
    seen = {}
    vanished = set()
    done = threading.Event()
    problems = []
    command = subprocess.Popen(args.command)
    descendants = {command.pid}

    def sample():
        while not done.is_set():
            parents = process_parents()
            # Keep ancestry after a parent exits, and follow every generation,
            # including children that precede their parent in the ps snapshot.
            while True:
                children = {pid for pid, ppid in parents.items() if ppid in descendants}
                if children <= descendants:
                    break
                descendants.update(children)
            for pid in descendants.intersection(parents):
                path = pidpath(libproc, pid)
                is_translated = translated(libc, pid)
                if (path is None or is_translated is None) and not alive(pid):
                    # It ended between the two reads, so it can't be inspected.
                    vanished.add(pid)
                    continue
                # A shell may exec Java or a translated binary without changing
                # PID. Inspect it again rather than treating a PID as finished.
                key = (pid, path, is_translated)
                if key not in seen:
                    seen[key] = {"pid": pid, "ppid": parents[pid], "path": path,
                                 "args": process_args(libc, pid), "translated": is_translated,
                                 "cpu": [cpu_name(cpu) for cpu in cpu_types(path)] if path else []}
            done.wait(INTERVAL)

    def monitor():
        try:
            sample()
        except Exception as error:
            problems.append("could not inspect descendants: %s" % error)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    try:
        status = command.wait()
    finally:
        done.set()
        thread.join()
    records = sorted(seen.values(), key=lambda record: record["pid"])
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"command": args.command, "status": status, "java": args.java,
                       "selfTranslated": translated(libc, os.getpid()), "processes": records}, f, indent=1)
            f.write("\n")
    java_pids = set()
    for record in records:
        print("process %d (parent %s): %s, %s, translated %s" % (
            record["pid"], record["ppid"], record["path"], "/".join(record["cpu"]) or "not Mach-O",
            record["translated"]))
        if record["translated"] is not False:
            problems.append("process %d runs translated, or its translation is unknown" % record["pid"])
        if record["path"] is None or os.path.basename(record["path"]) != "java":
            continue
        java_pids.add(record["pid"])
        if not fnmatch.fnmatchcase(record["path"], args.java):
            problems.append("process %d runs %s, which doesn't match %s" % (
                record["pid"], record["path"], args.java))
        if record["cpu"] != ["arm64"]:
            problems.append("process %d runs %s, which isn't an arm64 Mach-O file" % (
                record["pid"], record["path"]))
    print("%d Java processes, every one matching %s: %s%s" % (
        len(java_pids), args.java, "yes" if not problems else "no",
        "; %d more ended before they could be inspected" % len(vanished) if vanished else ""))
    if len(java_pids) < args.min_java:
        problems.append("expected at least %d Java processes, saw %d" % (args.min_java, len(java_pids)))
    if status != 0:
        problems.append("the command exited with status %d" % status)
    if problems:
        fail("; ".join(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("host", help="check an untranslated Apple Silicon runner; report Rosetta's presence")
    runner = commands.add_parser("run", help="check command descendants for translation and the expected Java")
    runner.add_argument("--java", required=True, help="fnmatch pattern every Java executable must match")
    runner.add_argument("--min-java", type=int, default=1)
    runner.add_argument("--report")
    runner.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "run":
        if args.command[:1] == ["--"]:
            args.command = args.command[1:]
        run(args)
    else:
        host(args)


if __name__ == "__main__":
    main()
