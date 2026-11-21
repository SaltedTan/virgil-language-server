#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Run test suites, several at once, each printed whole once it finishes.

Usage: test/suites.py [--jobs=N] [--shard=I/N [--times=FILE] [--list]]
                      [--report=FILE [--record-digest=PATH ...]] COMMAND ...
       test/suites.py --times-from LOG ...
       test/suites.py --verify=N --expected=FILE [--digest=PATH=SHA256 ...] REPORT ...

Each COMMAND is a shell command that runs one suite. They start in the order
given, so the longest should come first, and up to N run at once (default:
one for each CPU this process may use, at most DEFAULT_JOBS). With more than
one at once, a suite's output, stdout and stderr together, goes to a
temporary file that is printed whole, after a line with the suite's result
and time, once it finishes; with one at a time it streams as it is written.
Every suite runs, even after one fails. Each runs in a session of its own,
with no input. SIGTERM, SIGHUP, and SIGINT are forwarded to each running
suite's process group and stop new suites from starting. A second signal or
ten seconds after the first signal kills the remaining groups. Suites own
cleanup of processes they move into separate sessions. Afterwards it lists
each suite's time, slowest first.

--shard=I/N runs only the I-th of N shards, which together hold every
COMMAND exactly once. The shards are balanced by the seconds that FILE
records for each command, in the format of the "suite times:" list that a run
prints; a command it doesn't name counts as the median of those it does, or
as one second each without FILE. Each shard keeps the order of COMMAND. A
sharded run first prints the shard's commands, one per line after "shard I/N:",
and --list prints only those lines and exits.

--report=FILE writes a JSON report once the suites finish, whether or not
they passed, but not after a signal stopped the run: "shard", the --shard
value or null; "suites", each suite that ran with its "command", exit
"status", and "seconds"; and "digests", the SHA-256 digest of each
--record-digest PATH, read before any suite starts, as lowercase hex.

--times-from reads the "suite times:" lists of one or more runs' logs, such
as CI job logs, and prints each command's mean time in FILE's format.

--verify=N checks the reports of a run split into N shards, such as CI's
shard jobs: one report for each shard 1/N to N/N, every suite passed, the
shards together ran each command in --expected FILE, one per line, exactly
once, and every report records the same digests, which also match each
--digest PATH=SHA256 given, such as those of another job's build. It lists
every problem it finds and exits with status 1 if there is any.
"""
import hashlib
import json
import math
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time

# Suites check timing (exits within seconds, workers stopped at their limits),
# so they don't run more at once than CI runners have CPUs.
DEFAULT_JOBS = 4


class Process(subprocess.Popen):
    killing = False
    killed = False


def send(process, signum):
    """Signals an owned process group, returning whether it still exists."""
    if process.killing:
        # A nested handler can reach this group before killpg's result is recorded.
        return True
    killing = signum == signal.SIGKILL
    try:
        if killing:
            process.killing = True
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        if process.killed or process.poll() is not None:
            return False
        # On macOS getpgid cannot see a zombie leader, even while wait()
        # holds the lock that prevents poll() from observing its exit.
        try:
            os.getpgid(process.pid)
        except ProcessLookupError:
            return False
        raise
    else:
        if killing:
            process.killed = True
        return True
    finally:
        if killing:
            process.killing = False


class Suite:
    def __init__(self, command):
        self.command = command
        self.seconds = 0.0
        self.status = None


class Suites:
    """Hands the suites to the threads in turn and owns their processes. A
    signal is recorded and forwarded to every running suite's process group,
    rather than raising during a launch. Launch and registration share the
    signal handler's lock, so cancellation prevents further launches."""

    def __init__(self, commands, live):
        self.waiting = [Suite(command) for command in commands]
        self.ran = []
        self.live = live  # stream each suite's output, with one at a time
        self.lock = threading.RLock()
        self.signals = []
        self.deadline = None
        self.processes = []

    def stop(self, signum, _frame):
        self.signals.append(signum)
        if len(self.signals) == 1:
            self.deadline = time.monotonic() + 10
        if len(self.signals) > 1:
            self.deadline = None
        # Only workers hold this lock outside handlers; nested signals can reenter it.
        with self.lock:
            for process in self.processes:
                send(process, signal.SIGKILL if len(self.signals) > 1 else self.signals[0])

    def thread(self):
        while self.run():
            pass

    def run(self):
        try:
            with self.lock:
                if self.signals or not self.waiting:
                    return False
                suite = self.waiting.pop(0)
                self.ran.append(suite)
                output = None if self.live else tempfile.TemporaryFile()
                started = time.monotonic()
                process = Process(["/bin/sh", "-c", suite.command], stdin=subprocess.DEVNULL,
                                  stdout=output, stderr=subprocess.STDOUT if output else None,
                                  start_new_session=True)
                self.processes.append(process)
        except OSError as error:
            suite.status = 127
            self.write(suite, f"cannot run it: {error}\n".encode())
            return True
        suite.status = process.wait()
        suite.seconds = time.monotonic() - started
        with self.lock:
            if not self.signals:
                self.processes.remove(process)
        if output is None:
            self.write(suite, b"")
        else:
            output.seek(0)
            self.write(suite, output.read())
            output.close()
        return True

    def write(self, suite, output):
        result = "passed" if suite.status == 0 else f"FAILED (exit status {suite.status})"
        with self.lock:
            sys.stdout.buffer.write(f"== {suite.command}: {result} in {suite.seconds:.1f} s\n".encode())
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()


TIME_LINE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?) s  (.+)")
# A GitHub Actions log line starts with its time, such as 2026-11-21T06:32:10.1234567Z.
LOG_PREFIX = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z ")


def read_times(path):
    """Reads a times file: lines of seconds and a command, as "suite times:"
    lists them, and comments that start with #."""
    seconds = {}
    with open(path, encoding="utf-8") as file:
        for number, line in enumerate(file, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = TIME_LINE.fullmatch(line)
            if not match:
                sys.exit(f"{path}:{number}: not a suite time: {line}")
            seconds[match.group(2)] = float(match.group(1))
    return seconds


def times_from(logs):
    """Prints each command's mean time over the "suite times:" lists in the logs."""
    runs = {}
    for path in logs:
        listing = False
        with open(path, encoding="utf-8", errors="replace") as file:
            for line in file:
                line = line.rstrip("\n")
                prefix = LOG_PREFIX.match(line)
                if prefix:
                    line = line[prefix.end():]
                match = TIME_LINE.fullmatch(line) if listing else None
                if match:
                    runs.setdefault(match.group(2), []).append(float(match.group(1)))
                listing = line == "suite times:" or bool(match)
    if not runs:
        sys.exit("no suite times in " + ", ".join(logs))
    print(f"# Mean suite times from {len(logs)} log(s), written by test/suites.py --times-from.")
    for command, seconds in sorted(runs.items(), key=lambda item: (-statistics.mean(item[1]), item[0])):
        print(f"{statistics.mean(seconds):8.1f} s  {command}")
    return 0


def shards(commands, count, seconds):
    """Splits the commands into count shards of about equal recorded time,
    each in the commands' order. Returns them, each shard's recorded time, the
    time counted for a command without one, and how many had none."""
    known = [seconds[command] for command in commands if command in seconds]
    default = statistics.median(known) if known else 1.0
    weights = [seconds.get(command, default) for command in commands]
    totals = [0.0] * count
    members = [[] for _ in range(count)]
    # Longest first, each to the shard with the least time so far.
    for index in sorted(range(len(commands)), key=lambda index: (-weights[index], index)):
        shard = min(range(count), key=lambda shard: (totals[shard], shard))
        totals[shard] += weights[index]
        members[shard].append(index)
    return ([[commands[index] for index in sorted(shard)] for shard in members], totals, default,
            len(commands) - len(known))


def cpus():
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def main(jobs, commands, report=None, shard=None, digests=None):
    jobs = max(1, min(jobs or min(cpus(), DEFAULT_JOBS), len(commands)))
    suites = Suites(commands, jobs == 1)
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, suites.stop)
    started = time.monotonic()
    threads = []
    try:
        for _ in range(jobs):
            if suites.signals:
                break
            thread = threading.Thread(target=suites.thread)
            thread.start()
            threads.append(thread)
        # A timed join lets the main thread run signal handlers.
        for thread in threads:
            while thread.is_alive():
                thread.join(0.1)
                # A nested signal's handler can clear the deadline meanwhile.
                deadline = suites.deadline
                if deadline is not None and time.monotonic() >= deadline:
                    suites.stop(signal.SIGKILL, None)
        while len(suites.signals) == 1:
            suites.processes[:] = [process for process in suites.processes if send(process, 0)]
            if not suites.processes:
                break
            deadline = suites.deadline
            if deadline is not None and time.monotonic() >= deadline:
                suites.stop(signal.SIGKILL, None)
            else:
                time.sleep(0.1)
    finally:
        for process in list(suites.processes):
            send(process, signal.SIGKILL)
        for thread in threads:
            thread.join()
    if suites.signals:
        return 128 + suites.signals[0]
    elapsed = time.monotonic() - started

    print("\nsuite times:")
    for suite in sorted(suites.ran, key=lambda suite: suite.seconds, reverse=True):
        print(f"{suite.seconds:8.1f} s  {suite.command}")
    failed = [suite for suite in suites.ran if suite.status != 0]
    print(f"suites: {len(suites.ran) - len(failed)} passed, {len(failed)} failed, "
          f"{jobs} at once, {elapsed:.1f} s")
    for suite in failed:
        print(f"FAIL: {suite.command}")
    sys.stdout.flush()
    # A signal during the report is recorded too, and still decides the status.
    if suites.signals:
        return 128 + suites.signals[0]
    if report is not None:
        write_report(report, shard, suites.ran, digests or {})
    return 128 + suites.signals[0] if suites.signals else int(bool(failed))


def digest(path):
    """Returns the SHA-256 digest of the file at path as lowercase hex."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            sha256.update(block)
    return sha256.hexdigest()


def record_digests(paths):
    """Returns each path's digest, or exits naming each path it cannot read."""
    digests, problems = {}, []
    for path in paths:
        try:
            digests[path] = digest(path)
        except OSError as error:
            problems.append(f"--record-digest={path}: cannot read it: {error.strerror or error}")
    if problems:
        sys.exit("\n".join(problems))
    return digests


def write_report(path, shard, ran, digests):
    report = {
        "shard": shard,
        "suites": [{"command": suite.command, "status": suite.status, "seconds": round(suite.seconds, 3)}
                   for suite in ran],
        "digests": digests,
    }
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    os.replace(temporary, path)
    print(f"report: {path}")


SHA256 = re.compile(r"[0-9a-f]{64}")


def verify(count, expected_path, given, paths):
    """Checks the shard reports at paths against the expected commands and the
    given {path: digest}; prints every problem and returns the exit status."""
    problems = []
    try:
        with open(expected_path, encoding="utf-8") as file:
            expected = [line.rstrip("\n") for line in file if line.strip()]
    except OSError as error:
        problems.append(f"--expected={expected_path}: cannot read it: {error.strerror or error}")
        expected = None
    if expected is not None:
        if not expected:
            problems.append(f"--expected={expected_path}: lists no suites")
        for command in sorted({command for command in expected if expected.count(command) > 1}):
            problems.append(f"--expected={expected_path}: lists {command!r} more than once")
    for path, value in given.items():
        if not SHA256.fullmatch(value):
            problems.append(f"--digest={path}={value}: not a lowercase hex SHA-256 digest")

    reports = {}  # shard index: (path, report)
    for path in paths:
        try:
            with open(path, encoding="utf-8") as file:
                report = json.load(file)
        except (OSError, ValueError) as error:
            problems.append(f"{path}: cannot read the report: {error}")
            continue
        malformed = report_problems(report)
        if malformed:
            problems.extend(f"{path}: {problem}" for problem in malformed)
            continue
        match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", report["shard"] or "")
        if not match or int(match.group(2)) != count or not 1 <= int(match.group(1)) <= count:
            problems.append(f"{path}: shard {report['shard']!r} is not one of 1/{count} to {count}/{count}")
            continue
        index = int(match.group(1))
        if index in reports:
            problems.append(f"{path}: shard {report['shard']} is also in {reports[index][0]}")
            continue
        reports[index] = (path, report)
    for index in range(1, count + 1):
        if index not in reports:
            problems.append(f"no report for shard {index}/{count}")

    ran = {}  # command: the reports that ran it
    for index in sorted(reports):
        path, report = reports[index]
        for suite in report["suites"]:
            ran.setdefault(suite["command"], []).append(path)
            if suite["status"] != 0:
                problems.append(f"{path}: {suite['command']!r} failed with exit status {suite['status']}")
    for command, where in ran.items():
        if len(where) > 1:
            problems.append(f"{command!r} ran {len(where)} times, in {', '.join(where)}")
    if expected is not None:
        for command in expected:
            if command not in ran:
                problems.append(f"{command!r} is expected but no shard ran it")
        for command in ran:
            if command not in expected:
                problems.append(f"{command!r} ran but is not expected")

    if reports:
        first_path, first = reports[min(reports)]
        for index in sorted(reports):
            path, report = reports[index]
            for executable, value in sorted(report["digests"].items()):
                if not SHA256.fullmatch(value):
                    problems.append(f"{path}: digest {value!r} of {executable} is not lowercase hex SHA-256")
            if index == min(reports):
                continue
            for executable in sorted(set(first["digests"]) | set(report["digests"])):
                mine, theirs = report["digests"].get(executable), first["digests"].get(executable)
                if mine is None or theirs is None:
                    absent, present = (path, first_path) if mine is None else (first_path, path)
                    problems.append(f"{absent}: records no digest of {executable}, which {present} records")
                elif mine != theirs:
                    problems.append(f"{executable}: {path} records {mine}, but {first_path} records {theirs}")
        for executable, value in sorted(given.items()):
            recorded = first["digests"].get(executable)
            if recorded is None:
                problems.append(f"--digest={executable}: no report records a digest of {executable}")
            elif SHA256.fullmatch(value) and value != recorded:
                problems.append(f"--digest={executable}: {value} differs from {recorded}, which the reports record")

    if problems:
        print(f"verify: {len(problems)} problem(s) in {len(paths)} report(s):", file=sys.stderr)
        for problem in problems:
            print(f"verify: {problem}", file=sys.stderr)
        return 1
    digests = reports[1][1]["digests"]
    seconds = [sum(suite["seconds"] for suite in reports[index][1]["suites"]) for index in sorted(reports)]
    print(f"verify: {count} shard(s) ran all {len(expected)} expected suites once each, and every one passed; "
          f"suite seconds by shard: {', '.join(f'{total:.1f}' for total in seconds)}")
    for executable, value in sorted(digests.items()):
        also = " (also --digest)" if executable in given else ""
        print(f"verify: {value}  {executable}, the same in every shard{also}")
    return 0


def report_problems(report):
    """Returns how a report doesn't have the shape that --report writes."""
    if not isinstance(report, dict):
        return ["the report is not a JSON object"]
    problems = []
    if "shard" not in report or not (report["shard"] is None or isinstance(report["shard"], str)):
        problems.append('"shard" is missing or not a string or null')
    suites = report.get("suites")
    if not isinstance(suites, list) or not all(
            isinstance(suite, dict) and isinstance(suite.get("command"), str)
            and isinstance(suite.get("status"), int) and not isinstance(suite.get("status"), bool)
            and isinstance(suite.get("seconds"), (int, float))
            and not isinstance(suite.get("seconds"), bool) and math.isfinite(suite.get("seconds"))
            for suite in suites):
        problems.append('"suites" is not a list of objects with a string "command", '
                        'an integer "status", and a finite number "seconds"')
    digests = report.get("digests")
    if not isinstance(digests, dict) or not all(isinstance(value, str) for value in digests.values()):
        problems.append('"digests" is not an object of strings')
    return problems


def select(shard, times, listing, commands):
    """Returns the commands of the shard I/N that shard names, after printing them."""
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", shard)
    if not match or int(match.group(1)) > int(match.group(2)):
        sys.exit(f"--shard={shard}: expected I/N with 1 <= I <= N")
    index, count = int(match.group(1)), int(match.group(2))
    groups, totals, default, missing = shards(commands, count, read_times(times) if times else {})
    selected = groups[index - 1]
    if not listing:
        note = (f" ({missing} of the {len(commands)} have no recorded time and count as {default:.1f} s each)"
                if missing else "")
        print(f"shard {shard} runs {len(selected)} of {len(commands)} suites, "
              f"{totals[index - 1]:.1f} s by recorded times{note}:")
    for command in selected:
        print(f"shard {shard}: {command}")
    sys.stdout.flush()
    return selected


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["--times-from"] and arguments[1:]:
        sys.exit(times_from(arguments[1:]))
    jobs, shard, times, listing, report, recorded = 0, None, None, False, None, []
    count, expected, given = None, None, {}
    while arguments and arguments[0].startswith("--"):
        option = arguments.pop(0)
        if option.startswith("--jobs="):
            jobs = int(option[len("--jobs="):])
        elif option.startswith("--shard="):
            shard = option[len("--shard="):]
        elif option.startswith("--times="):
            times = option[len("--times="):]
        elif option == "--list":
            listing = True
        elif option.startswith("--report="):
            report = option[len("--report="):]
        elif option.startswith("--record-digest="):
            recorded.append(option[len("--record-digest="):])
        elif option.startswith("--verify="):
            if not re.fullmatch(r"[1-9][0-9]*", option[len("--verify="):]):
                sys.exit(f"{option}: expected a shard count of at least 1")
            count = int(option[len("--verify="):])
        elif option.startswith("--expected="):
            expected = option[len("--expected="):]
        elif option.startswith("--digest="):
            path, equals, value = option[len("--digest="):].rpartition("=")
            if not equals or not path:
                sys.exit(f"{option}: expected --digest=PATH=SHA256")
            if path in given:
                sys.exit(f"{option}: {path} has a --digest already")
            given[path] = value
        else:
            sys.exit(f"unknown option {option}")
    usage = __doc__.split("\n\n")[1]
    if count is not None or expected is not None or given:
        if count is None or expected is None or not arguments or any(
                [jobs, shard, times, listing, report, recorded]):
            sys.exit(usage)
        sys.exit(verify(count, expected, given, arguments))
    if not arguments or (shard is None and (times or listing)) or (recorded and report is None):
        sys.exit(usage)
    if shard is not None:
        arguments = select(shard, times, listing, arguments)
        if listing:
            sys.exit(0)
    digests = record_digests(recorded) if report is not None else None
    sys.exit(main(jobs, arguments, report, shard, digests))
