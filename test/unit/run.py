#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Run the unit tests, each group in a process of its own.

Usage: test/unit/run.py BUILD UNIT_TESTS [PATTERN ...]

UNIT_TESTS is the unit-test executable, and BUILD the directory whose
project-fixtures it uses. With patterns, the executable runs once with them,
under `prepare.py BUILD --run`. Without, each group of tests (the part of a
name before the colon, such as LspRename) runs in a process of its own.
Analyses in one process leave compiler state behind that makes every later one
slower, so in a single process the late groups ran several times slower than
on their own.

The executable has no option to list its tests, so the groups come from the
names that the .v3 files next to this script register with T(...) or
UnitTests.register(...). Their counts must add up to the executable's own count
of all its tests; a group the scan misses fails the run. The two fixture
directories that must be unreadable are restricted for the whole run and
restored however it ends, as `prepare.py BUILD --run` does. Afterwards it lists
the slowest tests.
"""
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

DIR = Path(__file__).resolve().parent
PREPARE = DIR.parent / "fixtures" / "projects" / "prepare.py"
REGISTRATION = re.compile(r'\b(?:T|register)\(\s*"([A-Za-z0-9_]+):')
HEADER = re.compile(rb"##>(\d+) unit tests")
SLOWEST = 25


class Children:
    def __init__(self):
        self.signum = 0
        self.processes = []

    def stop(self, signum, _frame):
        if not self.signum:
            self.signum = signum
        for process in self.processes:
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
    the first one. A link to it runs in an empty directory and is stopped once
    the count arrives, so a test that has started finds neither the fixtures
    nor the worker, which the tests look for next to the executable."""
    scratch = Path(tempfile.mkdtemp(prefix="unit-count-", dir=build)).absolute()
    try:
        link = scratch / Path(unit_tests).name
        try:
            os.link(unit_tests, link)
        except OSError:
            # Covers executables that cannot be hard-linked into BUILD, e.g. across filesystems.
            shutil.copy2(unit_tests, link)
        process = subprocess.Popen(
            [link, "*"], cwd=scratch, stdin=subprocess.DEVNULL,
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


class Group:
    def __init__(self, name):
        self.name = name
        self.count = None
        self.durations = []  # (seconds, test name)
        self.failed = []
        self.status = None


def run(unit_tests, group, children):
    """Runs {group}, copying its output, and times each test."""
    process = subprocess.Popen([unit_tests, f"{group.name}:*"], stdout=subprocess.PIPE)
    children.add(process)
    test, started = None, 0.0
    for line in process.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
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
    children.processes.remove(process)


def main(build, unit_tests, patterns):
    if patterns:
        os.execv(sys.executable, [sys.executable, str(PREPARE), build, "--run", unit_tests, *patterns])
    children = Children()
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, children.stop)
    unit_tests = os.path.abspath(unit_tests)
    expected = total(build, unit_tests, children)
    if children.signum:
        return 128 + children.signum
    ran = []
    started = time.monotonic()
    try:
        subprocess.run([sys.executable, str(PREPARE), build, "--restrict"],
                       check=True, start_new_session=True)
        if children.signum:
            return 128 + children.signum
        for name in groups():
            if children.signum:
                break
            group = Group(name)
            ran.append(group)
            run(unit_tests, group, children)
    finally:
        for process in children.processes:
            process.kill()
            process.wait()
            process.stdout.close()
        subprocess.run([sys.executable, str(PREPARE), build, "--cleanup"],
                       check=False, start_new_session=True)
    if children.signum:
        return 128 + children.signum
    elapsed = time.monotonic() - started

    errors = []
    for group in ran:
        finished = len(group.durations)
        if group.count is None:
            errors.append(f"{group.name}: no test count (exit status {group.status})")
        elif finished != group.count:
            errors.append(f"{group.name}: {group.count - finished} of {group.count} tests did not finish "
                          f"(exit status {group.status})")
        elif group.status != 0 and not group.failed:
            errors.append(f"{group.name}: exit status {group.status}")
    counted = sum(group.count or 0 for group in ran)
    if counted != expected:
        errors.append(f"the groups hold {counted} tests, but {unit_tests} has {expected}; "
                      f"register each test with T(\"Group:name\", ...) in {DIR.name}")

    durations = sorted((d for group in ran for d in group.durations), reverse=True)
    print(f"\nslowest {min(SLOWEST, len(durations))} unit tests:")
    for seconds, test in durations[:SLOWEST]:
        print(f"{seconds:8.2f} s  {test}")
    failed = [test for group in ran for test in group.failed]
    passed = sum(len(group.durations) for group in ran) - len(failed)
    print(f"unit tests: {passed} passed, {len(failed)} failed, in {len(ran)} groups, {elapsed:.1f} s")
    for test in failed:
        print(f"FAIL: {test}")
    for error in errors:
        print(f"error: {error}")
    return 128 + children.signum if children.signum else int(bool(failed or errors))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__.split("\n\n")[1])
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
