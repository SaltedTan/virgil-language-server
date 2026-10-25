#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Run the unit tests, each group in a process of its own, several at once.

Usage: test/unit/run.py [--jobs=N] BUILD UNIT_TESTS [PATTERN ...]

UNIT_TESTS is the unit-test executable, and BUILD the directory whose
project-fixtures it uses. With patterns, the executable runs once with them,
under `prepare.py BUILD --run`. Without, each group of tests (the part of a
name before the colon, such as LspRename) runs in a process of its own.
Analyses in one process leave compiler state behind that makes every later one
slower, so in a single process the late groups ran several times slower than
on their own.

Up to N groups (default: one for each CPU this process may use, at most
DEFAULT_JOBS) run at once,
each in a slot directory of its own, because tests edit and remove files in
the fixtures next to the executable. The first slot is BUILD itself; each
other is BUILD/unit-slots/<n>, with copies of the executable and the worker next
to it and fixtures of its own from prepare.py, and is removed afterwards. With
more than one slot, each group's output is printed whole once the group
finishes.

The executable has no option to list its tests, so the groups come from the
names that the .v3 files next to this script register with T(...) or
UnitTests.register(...). Their counts must add up to the executable's own count
of all its tests; a group the scan misses fails the run. The two fixture
directories that must be unreadable are restricted in every slot for the whole
run and restored however it ends, as `prepare.py BUILD --run` does. Afterwards
it lists the slowest tests.
"""
from contextlib import nullcontext
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

DIR = Path(__file__).resolve().parent
PREPARE = DIR.parent / "fixtures" / "projects" / "prepare.py"
REGISTRATION = re.compile(r'\b(?:T|register)\(\s*"([A-Za-z0-9_]+):')
HEADER = re.compile(rb"##>(\d+) unit tests")
SLOTS = "unit-slots"
WORKER = "virgil-lsp-worker"
SLOWEST = 25
# Each slot's fixtures hold about 23,000 files, and the groups' work is short.
DEFAULT_JOBS = 8


class Children:
    """The running child processes. A signal is recorded and stops them all,
    and any started later, rather than raising in the middle of a launch or
    a cleanup. Signal handlers run on the main thread, between the slots'
    threads' list operations, so the handler kills a copy of the list."""

    def __init__(self):
        self.signum = 0
        self.processes = []

    def stop(self, signum, _frame):
        if not self.signum:
            self.signum = signum
        for process in list(self.processes):
            process.kill()

    def add(self, process):
        self.processes.append(process)
        if self.signum:
            process.kill()


def groups():
    found = []
    for source in sorted(DIR.glob("*.v3")):
        for match in REGISTRATION.finditer(source.read_text(encoding="utf-8")):
            if match.group(1) not in found:
                found.append(match.group(1))
    return found


def total(build, unit_tests, children):
    """The executable's count of all its tests, which it prints before running
    the first one. A copy of it runs in an empty directory and is stopped once
    the count arrives, so a test that has started finds neither the fixtures
    nor the worker, which the tests look for next to the executable."""
    scratch = Path(tempfile.mkdtemp(prefix="unit-count-", dir=build)).absolute()
    try:
        copy = scratch / Path(unit_tests).name
        shutil.copy2(unit_tests, copy)
        process = subprocess.Popen(
            [copy, "*"], cwd=scratch, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        children.add(process)
        try:
            line = process.stdout.readline()
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # macOS can report EPERM for an exited process group.
                if process.poll() is None:
                    raise
            process.wait()
            children.processes.remove(process)
            process.stdout.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if children.signum:
        return None
    match = HEADER.fullmatch(line.rstrip(b"\n"))
    if match is None:
        sys.exit(f"error: {unit_tests} printed {line!r} instead of its test count")
    return int(match.group(1))


def prepare(directory, *mode):
    """Runs prepare.py on {directory} in a session of its own, so that a
    terminal's Ctrl-C can't stop it midway."""
    return subprocess.run([sys.executable, str(PREPARE), str(directory), *mode], check=False,
                          start_new_session=True, stdout=subprocess.DEVNULL if not mode else None).returncode


def slots(build, unit_tests, jobs):
    """The executables of {jobs} slots: BUILD's own, and copies in new
    BUILD/unit-slots/<n> directories that get fixtures of their own."""
    executables = [unit_tests]
    if jobs > 1:
        parent = Path(build) / SLOTS
        parent.mkdir()
        directories = []
        for n in range(1, jobs):
            slot = parent / str(n)
            slot.mkdir()
            for name in [Path(unit_tests).name, WORKER]:
                if (Path(unit_tests).parent / name).exists():
                    shutil.copy2(Path(unit_tests).parent / name, slot / name)
            directories.append(slot)
            executables.append(str(slot / Path(unit_tests).name))
        preparing = []
        launch_failed = False
        try:
            for slot in directories:
                preparing.append(subprocess.Popen([sys.executable, str(PREPARE), str(slot)],
                                                  start_new_session=True, stdout=subprocess.DEVNULL))
        except OSError:
            launch_failed = True
        finally:
            statuses = [process.wait() for process in preparing]
        if launch_failed or any(statuses):
            sys.exit("error: preparing the slots' fixtures failed")
    return executables


def remove_slots(build):
    """Restores and removes BUILD/unit-slots, and nothing outside it."""
    parent = Path(build) / SLOTS
    if parent.is_symlink() or not parent.is_dir():
        return
    for slot in parent.iterdir():
        fixtures = slot / "project-fixtures"
        if slot.is_dir() and not slot.is_symlink() and fixtures.is_dir() and not fixtures.is_symlink():
            prepare(slot, "--cleanup")
    shutil.rmtree(parent)


class Group:
    def __init__(self, name):
        self.name = name
        self.count = None
        self.durations = []  # (seconds, test name)
        self.failed = []
        self.status = None


class Slots:
    """Hands the groups to the slots' threads in turn, one at a time."""

    def __init__(self, names, children, live):
        self.waiting = [Group(name) for name in names]
        self.ran = []
        self.children = children
        self.live = live  # print each line at once, with only one slot
        self.lock = threading.Lock()

    def next(self):
        with self.lock:
            if self.children.signum or not self.waiting:
                return None
            group = self.waiting.pop(0)
            self.ran.append(group)
            return group

    def slot(self, executable):
        while (group := self.next()) is not None:
            self.run(executable, group)

    def run(self, executable, group):
        """Runs {group}, copying its output, and times each test."""
        with (nullcontext() if self.live else tempfile.TemporaryFile()) as error:
            process = subprocess.Popen([executable, f"{group.name}:*"], stdout=subprocess.PIPE, stderr=error)
            self.children.add(process)
            output, test, started = bytearray(), None, 0.0
            for line in process.stdout:
                if self.live:
                    self.write(line)
                else:
                    output += line
                now = time.monotonic()
                header = HEADER.fullmatch(line.rstrip(b"\n"))
                if header is not None:
                    group.count = int(header.group(1))
                elif line.startswith(b"##+"):
                    test, started = line[3:].decode(errors="replace").rstrip("\n"), now
                elif line.startswith(b"##-") and test is not None:
                    group.durations.append((now - started, test))
                    if line.startswith(b"##-fail"):
                        group.failed.append(test)
                    test = None
            process.stdout.close()
            group.status = process.wait()
            with self.lock:
                self.children.processes.remove(process)
            self.write(output, error)

    def write(self, output, error=None):
        with self.lock:
            sys.stdout.buffer.write(output)
            if error is not None:
                error.seek(0)
                shutil.copyfileobj(error, sys.stdout.buffer)
            sys.stdout.buffer.flush()


def cpus():
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def main(jobs, build, unit_tests, patterns):
    if patterns:
        os.execv(sys.executable, [sys.executable, str(PREPARE), build, "--run", unit_tests, *patterns])
    children = Children()
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, children.stop)
    try:
        status = run(jobs, build, unit_tests, children)
    except BaseException:
        if not children.signum:
            raise
        status = 1
    return 128 + children.signum if children.signum else status


def run(jobs, build, unit_tests, children):
    remove_slots(build)
    unit_tests = os.path.abspath(unit_tests)
    expected = total(build, unit_tests, children)
    if children.signum:
        return 0
    names = groups()
    jobs = max(1, min(jobs or min(cpus(), DEFAULT_JOBS), len(names)))
    runner = Slots(names, children, jobs == 1)
    threads = []
    started = time.monotonic()
    try:
        executables = slots(build, unit_tests, jobs)
        for executable in executables:
            if prepare(Path(executable).parent, "--restrict") != 0:
                sys.exit("error: restricting the fixtures failed")
        for executable in executables:
            if children.signum:
                break
            thread = threading.Thread(target=runner.slot, args=(executable,))
            thread.start()
            threads.append(thread)
        # A timed join lets the main thread run signal handlers.
        for thread in threads:
            while thread.is_alive():
                thread.join(0.1)
    finally:
        for process in list(children.processes):
            process.kill()
        for thread in threads:
            thread.join()
        prepare(build, "--cleanup")
        remove_slots(build)
    if children.signum:
        return 0
    elapsed = time.monotonic() - started

    errors = []
    for group in runner.ran:
        finished = len(group.durations)
        if group.count is None:
            errors.append(f"{group.name}: no test count (exit status {group.status})")
        elif finished != group.count:
            errors.append(f"{group.name}: {group.count - finished} of {group.count} tests did not finish "
                          f"(exit status {group.status})")
        elif group.status != 0 and not group.failed:
            errors.append(f"{group.name}: exit status {group.status}")
    counted = sum(group.count or 0 for group in runner.ran)
    if counted != expected:
        errors.append(f"the groups hold {counted} tests, but {unit_tests} has {expected}; "
                      f"register each test with T(\"Group:name\", ...) in {DIR.name}")

    durations = sorted((d for group in runner.ran for d in group.durations), reverse=True)
    print(f"\nslowest {min(SLOWEST, len(durations))} unit tests:")
    for seconds, test in durations[:SLOWEST]:
        print(f"{seconds:8.2f} s  {test}")
    failed = [test for group in runner.ran for test in group.failed]
    passed = sum(len(group.durations) for group in runner.ran) - len(failed)
    print(f"unit tests: {passed} passed, {len(failed)} failed, in {len(runner.ran)} groups, "
          f"{jobs} at once, {elapsed:.1f} s")
    for test in failed:
        print(f"FAIL: {test}")
    for error in errors:
        print(f"error: {error}")
    return int(bool(failed or errors))


if __name__ == "__main__":
    arguments = sys.argv[1:]
    jobs = 0
    if arguments and arguments[0].startswith("--jobs="):
        jobs = int(arguments.pop(0)[len("--jobs="):])
    if len(arguments) < 2:
        sys.exit(__doc__.split("\n\n")[1])
    sys.exit(main(jobs, arguments[0], arguments[1], arguments[2:]))
