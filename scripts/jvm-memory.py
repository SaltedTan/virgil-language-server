#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Memory high-water marks and the worker restart boundary of the JVM build.

Measures the JVM target that `make jvm` builds (#463). For each configuration
of JVM flags, it writes scratch copies of the server and worker launchers that
run the same jars with those flags, then drives one server per configuration
and project through `virgil-lsp/analyze` past the worker's analysis limit, so
the analysis after the limit lands on a fresh worker. It reports:

- each analysis's latency, and the first, warm, and post-restart ones;
- the server's worker replacements and their reasons, from its log;
- each JVM process's peak resident set size as a high-water mark, not a
  sample: a waiter process collects it when the JVM ends. On macOS the waiter
  is `/usr/bin/time -l`, which also reports the peak memory footprint; on
  Linux it is wait4's ru_maxrss. The server kills a retired worker's
  launcher, which is the waiter's parent, so the worker itself ends on its
  closed request pipe and the waiter still reports;
- the heap, stack, and collector flags in effect, from -XX:+PrintFlagsFinal;
- on macOS, whether any JVM process ran translated by Rosetta;
- with --nmt, a native memory breakdown of both processes once warm, from
  `jcmd <pid> VM.native_memory summary`, and the worker's GC.heap_info;
- with --startup N, the time to the initialize reply, N runs per configuration;
- with --cold N, N fresh process pairs per block, configuration, and project,
  timing each one's first (cold) and second analysis.

Not part of `make test` or CI. Run from the repository root after `make jvm`:

  python3 scripts/jvm-memory.py --smoke
  python3 scripts/jvm-memory.py --project util --project aeneas \\
      --config 'today|-Xss16m|-Xss16m -Xmx384m' \\
      --config 'serial|-Xss16m -Xmx256m -XX:+UseSerialGC|-Xss16m -Xmx384m -XX:+UseSerialGC' \\
      --output build/jvm-memory.json

A configuration is NAME|SERVER FLAGS|WORKER FLAGS, optionally followed by
|JAVA; without one, the flags of the launchers themselves are used. --java
runs the jars on another runtime.
See docs/testing-notes/jvm-memory.md.
"""
import argparse
import ctypes
import ctypes.util
import datetime
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "test/protocol"))
from worker import JVM_LAUNCHER_MARKER, Session  # noqa: E402

spec = importlib.util.spec_from_file_location("corpus", ROOT / "scripts/bench-corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)

DEFAULT_MAX_ANALYSES = 100  # AnalysisWorkerLimits.maxAnalyses
TIMEOUT = 120
FLAGS = ("MaxHeapSize", "InitialHeapSize", "ThreadStackSize", "UseSerialGC", "UseG1GC", "UseParallelGC",
         "ReservedCodeCacheSize", "MaxMetaspaceSize", "CompressedClassSpaceSize", "TieredStopAtLevel",
         "CICompilerCount", "ParallelGCThreads", "ConcGCThreads", "UseSharedSpaces")
REPLACED = re.compile(r"replacing analysis worker (\d+) (.*)")
# Each launched server starts a session and process group of its own, which
# its waiters, workers, and retired workers' waiters share; see launch().
GROUPS = []


def fail(message):
    raise SystemExit(f"jvm-memory: {message}")


def exit_code(status):
    return status >> 8 if status & 0x7f == 0 else 128 + (status & 0x7f)


def wrap(out, command):
    """The launcher's measurement waiter; see the module comment.

    This process is the one the server starts and may kill. Its child waits
    for the JVM and writes <out>-<pid>.time, so the report survives that kill.
    """
    out = f"{out}-{os.getpid()}"
    Path(f"{out}.start").write_text(f"{time.time()}\n")
    child = os.fork()
    if child == 0:
        if sys.platform == "darwin":
            os.execv("/usr/bin/time", ["/usr/bin/time", "-l", "-o", f"{out}.time", *command])
        jvm = os.fork()
        if jvm == 0:
            os.execv(command[0], command)
        _, status, usage = os.wait4(jvm, 0)
        # Linux reports ru_maxrss in KiB; write it as macOS's time does, in bytes.
        Path(f"{out}.time").write_text(f"{usage.ru_maxrss * 1024}  maximum resident set size\n")
        os._exit(exit_code(status))
    _, status = os.waitpid(child, 0)
    os._exit(exit_code(status))


def launch(exe, *options):
    """A Session in a new process group, recorded so that stop_group() can end all of it.

    A waiter (see wrap) outlives a retired worker's launcher on purpose, so
    killing Session.proc, the launcher, isn't enough; nothing in the chain
    leaves the group.
    """
    s = Session(exe, *options, start_new_session=True)
    GROUPS.append(s.proc.pid)
    return s


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap(pgid):
    """Reap this script's own exited children in the group, which would otherwise look alive."""
    try:
        while os.waitpid(-pgid, os.WNOHANG)[0] > 0:
            pass
    except ChildProcessError:
        pass


def stop_group(pgid, settle=0.0, grace=5.0):
    """Ends process group {pgid}: SIGTERM (with SIGCONT, for stopped processes), then SIGKILL.

    First waits up to {settle} seconds for the group to empty by itself, then
    up to {grace} seconds after each signal. Returns None if no signal was
    needed, else the last one sent.
    """
    deadline = time.monotonic() + settle
    reap(pgid)
    while group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
        reap(pgid)
    needed = None
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not group_alive(pgid):
            return needed
        needed = signal.Signals(sig).name
        try:
            os.killpg(pgid, sig)
            if sig == signal.SIGTERM:
                os.killpg(pgid, signal.SIGCONT)
        except ProcessLookupError:
            return needed
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            reap(pgid)
            if not group_alive(pgid):
                return needed
            time.sleep(0.05)
    reap(pgid)
    if group_alive(pgid):
        print(f"jvm-memory: process group {pgid} survived SIGKILL", file=sys.stderr)
    return needed


def read_time(path):
    text = Path(path).read_text() if Path(path).exists() else ""
    found = {}
    for key, label in (("max_rss_bytes", "maximum resident set size"),
                       ("peak_footprint_bytes", "peak memory footprint")):
        m = re.search(rf"^\s*(\d+)\s+{label}$", text, re.M)
        found[key] = int(m[1]) if m else None
    return found


def parse_launcher(exe):
    """The launcher's java and options, and the jar beside it."""
    text = exe.read_text()
    if text.splitlines()[1:2] != [JVM_LAUNCHER_MARKER.decode()]:
        fail(f"{exe} is not a JVM launcher; run make jvm")
    m = re.search(r"^exec '([^']+)' (.*?) ?-Dvirgil\.lsp\.executable=", text, re.M)
    jar = exe.with_name(exe.name + ".jar")
    if not m or not jar.is_file():
        fail(f"cannot read the java command of {exe}, or {jar} is missing")
    return {"java": m[1], "options": m[2].split(), "jar": jar.resolve()}


def write_launcher(path, java, options, jar, wrapper=None):
    """A copy of scripts/v3c.sh's launcher with other flags, optionally under the waiter.

    The jars may have been built on another host, so the locale is this
    host's, as scripts/v3c.sh chooses it.
    """
    locale = "en_US.UTF-8" if sys.platform == "darwin" else "C.UTF-8"
    prefix = ""
    if wrapper:
        prefix = " ".join(shlex.quote(a) for a in (sys.executable, "-I", str(Path(__file__).resolve()),
                                                   "--wrap", str(wrapper), "--")) + " "
    path.write_text(f"""#!/bin/sh
{JVM_LAUNCHER_MARKER.decode()}
# Scratch copy written by scripts/jvm-memory.py.
PATH=${{PATH:-/usr/bin:/bin}}
export LC_ALL={shlex.quote(locale)}
self=$0
case $self in */*) ;; *) self=./$self ;; esac
while [ -h "$self" ]; do
    link=$(readlink "$self") || exit 1
    case $link in /*) self=$link ;; *) self=${{self%/*}}/$link ;; esac
done
dir=$(cd -P "${{self%/*}}" && pwd -P) || exit 1
exec {prefix}{shlex.quote(java)} {" ".join(shlex.quote(o) for o in options)} \
-Dvirgil.lsp.executable="$dir/${{self##*/}}" -jar {shlex.quote(str(jar))} "$@"
""")
    path.chmod(0o755)


def resolve(command, what):
    """{command}'s absolute path, searching PATH once, so that the waiters' execv and
    the server's empty-environment worker run the same runtime."""
    found = shutil.which(command)
    if not found:
        fail(f"cannot find {what} {command!r}")
    return os.path.abspath(found)


def flags_in_effect(java, options):
    out = subprocess.run([java, *options, "-XX:+PrintFlagsFinal", "-version"], capture_output=True,
                         text=True, check=True).stdout
    found = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] in FLAGS:
            found[fields[1]] = fields[3]
    return found


def runtime_info(java):
    props = subprocess.run([java, "-XshowSettings:properties", "-version"], capture_output=True, text=True).stderr
    prop = lambda name: (re.search(rf"^\s*{re.escape(name)} = (.*)$", props, re.M) or [None, None])[1]
    info = {"java": java, "java_home": prop("java.home"), "vendor": prop("java.vendor"),
            "version": prop("java.runtime.version"), "vm": prop("java.vm.name"), "os_arch": prop("os.arch")}
    file_cmd = shutil.which("file")
    if file_cmd:
        info["file"] = subprocess.run([file_cmd, "-L", java], capture_output=True, text=True).stdout.strip()
    release = Path(info["java_home"] or "", "release")
    if release.is_file():
        info["release"] = release.read_text()
    return info


def translated(pid):
    """Whether macOS runs {pid} under Rosetta (P_TRANSLATED), or None elsewhere or if unknown."""
    if sys.platform != "darwin":
        return None
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
    buf = ctypes.create_string_buffer(1024)
    size = ctypes.c_size_t(len(buf))
    if libc.sysctl(mib, 4, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or size.value < 36:
        return None
    # kinfo_proc.kp_proc.p_flag follows a 16-byte union and two pointers.
    return bool(int.from_bytes(buf.raw[32:36], "little") & 0x00020000)


def jvm_processes(root):
    """{pid: role} for the server and worker JVMs below process {root}."""
    rows = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
    children, commands = {}, {}
    for row in rows.splitlines():
        fields = row.split(None, 2)
        if len(fields) == 3:
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
            commands[int(fields[0])] = fields[2]
    found, todo = {}, [root]
    while todo:
        pid = todo.pop()
        todo.extend(children.get(pid, []))
        command = commands.get(pid, "")
        if "-jar" in command and Path(command.split()[0]).name == "java":
            if "virgil-lsp-worker.jar" in command:
                found[pid] = "worker"
            elif "virgil-lsp.jar" in command:
                found[pid] = "server"
    return found


def jcmd(tool, pid, *command):
    if not tool:
        return None
    r = subprocess.run([tool, str(pid), *command], capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else f"jcmd failed ({r.returncode}): {r.stdout}{r.stderr}"


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def summary(values):
    if not values:
        return None
    return {"n": len(values), "median": round(statistics.median(values), 3),
            "p10": round(percentile(values, 0.1), 3), "p90": round(percentile(values, 0.9), 3)}


def startup_once(exe):
    start = time.perf_counter()
    s = launch(exe)
    try:
        s.initialize()
        elapsed = (time.perf_counter() - start) * 1000
        s.shutdown()
        return elapsed
    finally:
        s.close()


def cold_session(config, files):
    """A fresh process pair: its first analysis (a cold server and worker) and its second."""
    uris = [(ROOT / f["path"]).as_uri() for f in files]
    s = launch(config["dir"] / "plain" / "virgil-lsp")
    times = []
    try:
        s.initialize()
        s.send("initialized", {}, request=False)
        for i in (1, 2):
            start = time.perf_counter()
            result = s.result(s.analyze(uris), TIMEOUT)
            times.append(round((time.perf_counter() - start) * 1000, 3))
            if result["generation"] != i or result["diagnostics"]:
                fail(f"cold analysis {i} failed: {json.dumps(result)[:500]}")
        s.shutdown()
    finally:
        s.close()
    return times


def boundary_session(config, project, files, args):
    """One server driven past the worker's analysis limit; see the module comment."""
    limit = args.max_analyses or DEFAULT_MAX_ANALYSES
    options = [f"--worker-max-analyses={args.max_analyses}"] if args.max_analyses else []
    hwm = config["dir"] / "hwm-records"
    shutil.rmtree(hwm, ignore_errors=True)
    hwm.mkdir()
    exe = config["dir"] / ("hwm" if args.wrap else "plain") / "virgil-lsp"
    uris = [(ROOT / f["path"]).as_uri() for f in files]
    s = launch(exe, *options)
    record = {"config": config["name"], "project": project, "limit": limit, "wrapped": args.wrap,
              "analyses": [], "replacements": [], "processes": {}, "nmt": None}
    try:
        s.initialize()
        s.send("initialized", {}, request=False)
        for i in range(1, args.analyses + 1):
            start = time.perf_counter()
            result = s.result(s.analyze(uris), TIMEOUT)
            elapsed = (time.perf_counter() - start) * 1000
            if result["generation"] != i or result["diagnostics"]:
                fail(f"analysis {i} of {project} failed: {json.dumps(result)[:500]}")
            record["analyses"].append(round(elapsed, 3))
            for pid, role in jvm_processes(s.proc.pid).items():
                record["processes"].setdefault(str(pid), {"role": role, "translated": translated(pid)})
            if args.nmt and i == limit - 1:
                record["nmt"] = {role: {"pid": pid, "native_memory": jcmd(args.jcmd, pid, "VM.native_memory", "summary"),
                                        "heap_info": jcmd(args.jcmd, pid, "GC.heap_info")}
                                 for pid, role in jvm_processes(s.proc.pid).items()}
        s.shutdown()
        logs = s.logs()
    except BaseException:
        print("server stderr:", s.logs()[-4000:], file=sys.stderr)
        raise
    finally:
        s.close()
    record["replacements"] = [{"pid": int(m[1]), "why": m[2]} for m in REPLACED.finditer(logs)]
    if args.wrap:
        # A retired worker ends once it reads the end of its request pipe.
        # macOS's time creates its file at the start and fills it at the end.
        starts = sorted(hwm.glob("*.start"), key=lambda p: float(p.read_text()))
        deadline = time.monotonic() + 30
        while (any(read_time(p.with_suffix(".time"))["max_rss_bytes"] is None for p in starts)
               and time.monotonic() < deadline):
            time.sleep(0.1)
        record["hwm"] = [{"role": p.name.split("-", 1)[0], **read_time(p.with_suffix(".time"))} for p in starts]
    # Normally empty by now; anything left is stopped, and the record says how.
    record["leftover_signal"] = stop_group(s.proc.pid, settle=5.0)
    a, warm_from = record["analyses"], max(1, limit - 20)
    record["summary"] = {
        "first_ms": a[0],
        "warm_ms": summary(a[warm_from:limit]),
        "after_restart_ms": a[limit:limit + 5],
        "workers": 1 + len(record["replacements"]),
    }
    return record


def mib(value):
    return "n/a" if value is None else f"{value / 1048576:.1f}"


def report(result):
    for config in result["configs"]:
        print(f"{config['name']}: server {' '.join(config['server_options'])}; "
              f"worker {' '.join(config['worker_options'])}", file=sys.stderr)
    for s in result["sessions"]:
        line = (f"{s['config']} {s['project']}: first {s['summary']['first_ms']:.1f} ms, "
                f"warm median {s['summary']['warm_ms']['median'] if s['summary']['warm_ms'] else 'n/a'} ms, "
                f"after restart {s['summary']['after_restart_ms']}, workers {s['summary']['workers']}")
        if s.get("hwm"):
            for role in ("server", "worker"):
                peaks = [h for h in s["hwm"] if h["role"] == role]
                line += f"; {role} RSS HWM {', '.join(mib(h['max_rss_bytes']) for h in peaks)} MiB"
                if any(h["peak_footprint_bytes"] for h in peaks):
                    line += f" (footprint {', '.join(mib(h['peak_footprint_bytes']) for h in peaks)})"
        print(line, file=sys.stderr)
    for name, values in (result.get("startup") or {}).items():
        print(f"{name} startup: {summary(values)}", file=sys.stderr)
    for key in sorted({(c["config"], c["project"]) for c in result["cold"]}):
        rows = [c for c in result["cold"] if (c["config"], c["project"]) == key]
        print(f"{key[0]} {key[1]} cold first: {summary([r['first_ms'] for r in rows])}, "
              f"second: {summary([r['second_ms'] for r in rows])}", file=sys.stderr)


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "--wrap" and sys.argv[3] == "--":
        wrap(sys.argv[2], sys.argv[4:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=ROOT / "build/jvm-target/virgil-lsp")
    parser.add_argument("--java", help="run the jars on this java instead of the launcher's (searched on PATH)")
    parser.add_argument("--config", action="append", default=[],
                        help="NAME|SERVER FLAGS|WORKER FLAGS, optionally |JAVA (default: --java)")
    parser.add_argument("--project", action="append", choices=["small", "util", "aeneas"])
    parser.add_argument("--max-analyses", type=int, help="pass --worker-max-analyses (default: the server's own)")
    parser.add_argument("--analyses", type=int, help="analyses per session (default: the limit plus 5)")
    parser.add_argument("--blocks", type=int, default=1, help="alternating blocks of all configurations")
    parser.add_argument("--startup", type=int, default=0, help="also time N startups per configuration")
    parser.add_argument("--startup-only", action="store_true", help="time only the startups")
    parser.add_argument("--cold", type=int, default=0,
                        help="also time N fresh process pairs' first and second analyses per block")
    parser.add_argument("--no-boundary", dest="boundary", action="store_false",
                        help="skip the restart-boundary sessions")
    parser.add_argument("--nmt", action="store_true", help="native memory breakdown once warm (needs jcmd)")
    parser.add_argument("--jcmd", help="jcmd to use with --nmt (default: beside --java)")
    parser.add_argument("--no-wrap", dest="wrap", action="store_false", help="no high-water-mark waiters")
    parser.add_argument("--work", type=Path, help="directory for the scratch launchers (default: temporary)")
    parser.add_argument("--output", help="JSON file (default: none)")
    parser.add_argument("--prepare", action="store_true",
                        help="only write each configuration's launchers under --work, for other suites")
    parser.add_argument("--smoke", action="store_true",
                        help="small project, limit 3, 5 analyses, 2 startups, 1 cold pair, with checks; a self-test")
    args = parser.parse_args()
    if args.smoke:
        args.project, args.max_analyses, args.analyses, args.blocks = ["small"], 3, 5, 1
        args.startup, args.cold = args.startup or 2, args.cold or 1
    limit = args.max_analyses or DEFAULT_MAX_ANALYSES
    args.analyses = args.analyses or limit + 5
    if args.analyses <= limit:
        parser.error("--analyses must exceed the analysis limit to cross the restart boundary")
    os.chdir(ROOT)
    pinned, _ = corpus.validate(strict=not args.smoke)
    launcher = parse_launcher(args.exe.resolve())
    worker = parse_launcher(args.exe.resolve().with_name("virgil-lsp-worker"))
    java = resolve(args.java or launcher["java"], "java")
    if args.jcmd:
        args.jcmd = resolve(args.jcmd, "jcmd")
    if args.nmt and not args.jcmd:
        args.jcmd = str(Path(java).with_name("jcmd"))
        if not Path(args.jcmd).exists():
            parser.error("--nmt needs jcmd; give --jcmd")
    configs = []
    for spec_text in args.config or [f"launcher|{' '.join(launcher['options'])}|{' '.join(worker['options'])}"]:
        parts = spec_text.split("|")
        if len(parts) not in (3, 4) or not re.fullmatch(r"[A-Za-z0-9_.-]+", parts[0]):
            parser.error(f"bad --config {spec_text!r}")
        nmt = ["-XX:NativeMemoryTracking=summary"] if args.nmt else []
        configs.append({"name": parts[0], "server_options": parts[1].split() + nmt,
                        "worker_options": parts[2].split() + nmt, "java": resolve(parts[3], "java") if len(parts) == 4 else java})
    if args.prepare and not args.work:
        parser.error("--prepare needs --work")
    work = args.work or Path(tempfile.mkdtemp(prefix="jvm-memory-"))
    # SIGTERM, like Ctrl-C, unwinds through the cleanup below.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(128 + signal.SIGTERM))
    try:
        for c in configs:
            c["dir"] = work / c["name"]
            (c["dir"] / "hwm-records").mkdir(parents=True, exist_ok=True)
            for kind in ("plain", "hwm"):
                (c["dir"] / kind).mkdir(parents=True, exist_ok=True)
                for role, info, options in (("virgil-lsp", launcher, c["server_options"]),
                                            ("virgil-lsp-worker", worker, c["worker_options"])):
                    wrapper = c["dir"] / "hwm-records" / role.replace("virgil-lsp-worker", "worker").replace(
                        "virgil-lsp", "server") if kind == "hwm" else None
                    write_launcher(c["dir"] / kind / role, c["java"], options, info["jar"], wrapper)
                # So that scripts/bench-lsp.py accepts the copies.
                for extra in ("benchmark-build.json", "gen/BuildInfo.v3"):
                    source = args.exe.resolve().parent / extra
                    if source.is_file():
                        (c["dir"] / kind / extra).parent.mkdir(exist_ok=True)
                        shutil.copyfile(source, c["dir"] / kind / extra)
            c["flags"] = {"server": flags_in_effect(c["java"], c["server_options"]),
                          "worker": flags_in_effect(c["java"], c["worker_options"])}
            c["runtime"] = runtime_info(c["java"])
        if args.prepare:
            for c in configs:
                print(f"{c['name']}: {c['dir'] / 'plain' / 'virgil-lsp'} (with waiters: {c['dir'] / 'hwm' / 'virgil-lsp'})")
            return 0
        result = {"schema": 1, "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "command": [sys.executable, *sys.argv],
                  "server_revision": corpus.git_revision(ROOT, "rev-parse", "HEAD"),
                  "host": {"platform": platform.platform(), "machine": platform.machine(), "cpus": os.cpu_count(),
                           "load_before": list(os.getloadavg()), "python_translated": translated(os.getpid())},
                  "jars": {p.name: corpus.sha256(p.read_bytes()) for p in (launcher["jar"], worker["jar"])},
                  "build_info": (args.exe.parent / "gen/BuildInfo.v3").read_text()
                  if (args.exe.parent / "gen/BuildInfo.v3").exists() else None,
                  "configs": [{k: (str(v) if k == "dir" else v) for k, v in c.items()} for c in configs],
                  "startup": {}, "cold": [], "sessions": []}
        for block in range(args.blocks):
            order = configs if block % 2 == 0 else configs[::-1]
            for i in range(args.startup if block == 0 else 0):
                for c in (configs if i % 2 == 0 else configs[::-1]):
                    result["startup"].setdefault(c["name"], []).append(round(startup_once(c["dir"] / "plain" / "virgil-lsp"), 3))
            for project in [] if args.startup_only else args.project or ["util", "aeneas"]:
                files = pinned["projects"][project]["files"]
                for i in range(args.cold):
                    for c in (order if i % 2 == 0 else order[::-1]):
                        first, second = cold_session(c, files)
                        result["cold"].append({"config": c["name"], "project": project, "block": block,
                                               "first_ms": first, "second_ms": second})
                for c in order if args.boundary else []:
                    session = boundary_session(c, project, files, args)
                    session["block"] = block
                    result["sessions"].append(session)
                print(f"block {block + 1}, {project}: done", file=sys.stderr)
        result["host"]["load_after"] = list(os.getloadavg())
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=1) + "\n")
        report(result)
        if args.smoke:
            for s in result["sessions"]:
                why = [r["why"] for r in s["replacements"]]
                assert why == [f"after {limit} analyses (limit {limit})"], why
                assert s["summary"]["workers"] == 2, s["summary"]
                if args.wrap:
                    roles = [h["role"] for h in s["hwm"]]
                    assert roles.count("server") == 1 and roles.count("worker") == 2, s["hwm"]
                    assert all(h["max_rss_bytes"] and h["max_rss_bytes"] > 1 << 20 for h in s["hwm"]), s["hwm"]
                assert not any(p["translated"] for p in s["processes"].values()), s["processes"]
                assert sorted(p["role"] for p in s["processes"].values()) == ["server", "worker", "worker"], s["processes"]
                assert s["leftover_signal"] is None, s["leftover_signal"]
            assert len(result["cold"]) == args.cold * len(configs), result["cold"]
            print("jvm-memory smoke: startup, cold analyses, restart boundary, high-water marks, and flags passed",
                  file=sys.stderr)
    finally:
        for pgid in GROUPS:
            stop_group(pgid)
        if not args.work:
            shutil.rmtree(work, ignore_errors=True)
    return 0

if __name__ == "__main__":
    main()
