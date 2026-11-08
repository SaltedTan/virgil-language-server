#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Checks that the JVM build runs natively on Apple Silicon, without Rosetta 2.

  native.py host
      Checks that this Mac is Apple Silicon with no Rosetta 2: the machine is
      arm64, this process isn't translated (sysctl.proc_translated is 0), the
      Rosetta runtime that its installer puts down is absent, and
      `arch -x86_64 /usr/bin/true` fails, as an x86-64 program must without
      Rosetta. Prints what it found.

  native.py run --java GLOB [--min-java N] [--report FILE] -- COMMAND...
      Runs COMMAND, and while it runs records every Java process that starts:
      its process ID, parent, executable path (proc_pidpath), command line,
      whether macOS runs it translated by Rosetta (P_TRANSLATED), and the CPU
      type of its executable's Mach-O header. It fails, after COMMAND ends, if
      COMMAND failed, if fewer than N Java processes were seen (default 1), or
      if any was translated, had an executable that doesn't match GLOB (an
      fnmatch pattern, such as '*/runtime/bin/java' for a release archive's
      bundled runtime), or had one that isn't an arm64 Mach-O file. --report
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
import time

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


def pids(libproc):
    count = libproc.proc_listallpids(None, 0)
    if count <= 0:
        return []
    buf = (ctypes.c_int * (count + 64))()
    count = libproc.proc_listallpids(buf, ctypes.sizeof(buf))
    return [pid for pid in buf[:max(count, 0)] if pid > 0]


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


def parent(pid):
    out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout
    return int(out) if out.strip().isdigit() else None


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
    if present:
        problems.append("Rosetta 2 is installed: %s exists" % ROSETTA_RUNTIME)
    x86 = subprocess.run(["arch", "-x86_64", "/usr/bin/true"], capture_output=True, text=True)
    print("arch -x86_64 /usr/bin/true: status %d%s" % (
        x86.returncode, (": " + x86.stderr.strip()) if x86.stderr.strip() else ""))
    if x86.returncode == 0:
        problems.append("an x86-64 program ran: arch -x86_64 /usr/bin/true succeeded")
    if problems:
        fail("this host is not Apple Silicon without Rosetta 2: " + "; ".join(problems))
    print("Apple Silicon without Rosetta 2: x86-64 programs can't run here")


def run(args):
    require_macos()
    if not args.command:
        fail("no command to run after --")
    libc, libproc = libraries()
    before = set(pids(libproc))
    seen = {}
    vanished = set()
    done = threading.Event()

    def sample():
        while not done.is_set():
            for pid in pids(libproc):
                if pid in before or pid in seen:
                    continue
                path = pidpath(libproc, pid)
                if path is None or os.path.basename(path) != "java":
                    continue
                record = {"pid": pid, "ppid": parent(pid), "path": path, "args": process_args(libc, pid),
                          "translated": translated(libc, pid), "cpu": [cpu_name(cpu) for cpu in cpu_types(path)]}
                if record["translated"] is None and not alive(pid):
                    # It ended between the two reads, so it can't be inspected.
                    vanished.add(pid)
                    continue
                seen[pid] = record
            done.wait(INTERVAL)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        status = subprocess.run(args.command).returncode
    finally:
        done.set()
        thread.join()
    records = sorted(seen.values(), key=lambda record: record["pid"])
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"command": args.command, "status": status, "java": args.java,
                       "selfTranslated": translated(libc, os.getpid()), "processes": records}, f, indent=1)
            f.write("\n")
    problems = []
    for record in records:
        print("java process %d (parent %s): %s, %s, translated %s" % (
            record["pid"], record["ppid"], record["path"], "/".join(record["cpu"]) or "not Mach-O",
            record["translated"]))
        if record["translated"] is not False:
            problems.append("process %d runs translated, or its translation is unknown" % record["pid"])
        if not fnmatch.fnmatchcase(record["path"], args.java):
            problems.append("process %d runs %s, which doesn't match %s" % (
                record["pid"], record["path"], args.java))
        if record["cpu"] != ["arm64"]:
            problems.append("process %d runs %s, which isn't an arm64 Mach-O file" % (
                record["pid"], record["path"]))
    print("%d Java processes, every one matching %s: %s%s" % (
        len(records), args.java, "yes" if not problems else "no",
        "; %d more ended before they could be inspected" % len(vanished) if vanished else ""))
    if len(records) < args.min_java:
        problems.append("expected at least %d Java processes, saw %d" % (args.min_java, len(records)))
    if status != 0:
        problems.append("the command exited with status %d" % status)
    if problems:
        fail("; ".join(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("host", help="check that this Mac is Apple Silicon without Rosetta 2")
    runner = commands.add_parser("run", help="run a command and check its Java processes")
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
