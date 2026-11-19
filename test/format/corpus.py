#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check the formatter against the record of the pinned Virgil sources.

Usage: test/format/corpus.py [--write] build/virgil-lsp

ADR-0019 decision 8. `test/format/virgil-corpus.txt` records the Virgil pin
and, for every `.v3` file of `aeneas/src`, `lib`, and `apps` in the pinned
`vendor/virgil`, in sorted order, whether it formatted or its first
diagnostic, a hash of its formatted text, and the lines of the input that
formatting changes. This suite formats those files again and fails, naming
the file and lines, when anything differs from the record or when the
record's pin isn't the submodule's checkout. With `--write`, it regenerates
the record instead; review the diff.

The record applies only when the submodule is checked out at the
superproject's gitlink (from the index, or `HEAD` without an index entry, as
`test/release/check_pin.py` reads it). When the submodule is at another
revision, as the advisory Virgil master workflow checks it out, the suite
says so in one line and skips the comparison with the record, but runs every
other check.

It also requires, for every file of `aeneas/src`, `lib`, `apps`, and `test`
that parses, that formatting the output again changes nothing and that the
output keeps the input's parser tokens and comments. The files are those
that the submodule's Git index tracks, and a tracked file missing from disk,
as in a sparse checkout, or an untracked `.v3` file there fails the suite.

The files are formatted in batches by `format-corpus-probe`, built beside
the server from `test/format/FormatCorpusProbe.v3`, which calls the
formatter as the `format` command does and compares the tokens that the
parser consumed, so no second lexer can disagree with the parser. The suite
also runs `virgil-lsp format --check` once over the recorded files and
requires it to report exactly the files and lines that the probe does.
"""
import collections
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIRGIL = 'vendor/virgil'
RECORD = ROOT / 'test' / 'format' / 'virgil-corpus.txt'
RECORDED = ('aeneas/src', 'lib', 'apps')
CHECKED = RECORDED + ('test',)
# Files for each probe process.
BATCH = 1000
# Paths that a failure lists before it counts the rest.
LISTED = 10
HEADER = """\
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# The formatter's record of the pinned Virgil sources (ADR-0019 decision 8),
# written by `python3 test/format/corpus.py --write build/virgil-lsp`, which
# `make test` checks. Review its diff after a formatter change or a pin update.
#
# A total line gives a directory's files, those that format, those that
# formatting changes, and their changed lines. Each file of aeneas/src, lib,
# and apps then has a line of three fields separated by tabs: its path in
# vendor/virgil, then either "refused" and its first diagnostic, or the first
# 16 hex digits of the SHA-256 of its formatted text, with each CR LF taken as
# LF so that the checkout's line ends don't matter, and "unchanged" or the
# lines of the file that an edit touches. A brace or else join touches two
# lines, and each blank line removed at the end of a file is a line of its own.
"""

failures = 0


def fail(message):
    """Reports a failure; the suite then exits with 1."""
    global failures
    failures += 1
    print(f'FAIL {message}')


def plural(n, word):
    return f'{n} {word}' + ('' if n == 1 else 's')


def listed(paths):
    """The first few of `paths`, and how many more there are."""
    paths = sorted(paths)
    text = ', '.join(paths[:LISTED])
    return text + (f', and {len(paths) - LISTED} more' if len(paths) > LISTED else '')


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT / VIRGIL), *args], capture_output=True)


def gitlink():
    """The superproject's `vendor/virgil` gitlink, read as check_pin.py reads it, or None."""
    spec = importlib.util.spec_from_file_location('check_pin', ROOT / 'test' / 'release' / 'check_pin.py')
    check_pin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_pin)
    try:
        return check_pin.read_gitlink(ROOT)
    except check_pin.PinError as error:
        fail(f'cannot read the vendor/virgil gitlink: {error}')
        return None


def inventory():
    """The tracked `.v3` files of each checked directory, by directory.

    Fails, naming them, for tracked files missing from disk, untracked files
    found on disk, and paths that the probe and the record can't carry: not
    UTF-8, or with a tab or line end. Those are left out of the checks.
    """
    run = git('ls-files', '-z', '--', *CHECKED)
    if run.returncode != 0:
        fail(f'cannot list the files of {VIRGIL}: {run.stderr.decode(errors="replace").strip()}')
        return {d: [] for d in CHECKED}
    tracked = set()
    for raw in run.stdout.split(b'\0'):
        if raw.endswith(b'.v3') and usable(raw):
            tracked.add(raw.decode())
    base = ROOT / VIRGIL
    found = set()
    for directory in CHECKED:
        for p in (base / directory).rglob('*.v3'):
            if p.is_file():
                raw = os.fsencode(p.relative_to(base).as_posix())
                if usable(raw):
                    found.add(raw.decode())
    missing = sorted(p for p in tracked if not (base / p).is_file())
    untracked = sorted(found - tracked)
    if missing:
        fail(f'{plural(len(missing), "tracked file")} missing from {VIRGIL}, as in a sparse checkout: '
             f'{listed(missing)}')
    if untracked:
        fail(f'{plural(len(untracked), "file")} in {VIRGIL} not tracked: {listed(untracked)}')
    present = sorted(tracked - set(missing))
    return {d: [p for p in present if p.startswith(d + '/')] for d in CHECKED}


_reported = set()


def usable(raw):
    """Whether the path `raw` is UTF-8 without a tab or line end; reports it once if not."""
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        if raw not in _reported:
            fail(f'{raw!r}: the path is not UTF-8, so the record can\'t hold it')
        _reported.add(raw)
        return False
    if any(c in text for c in '\t\n\r'):
        if raw not in _reported:
            fail(f'{text!r}: the path has a tab or line end, so the record can\'t hold it')
        _reported.add(raw)
        return False
    return True


def lines_text(lines):
    """`12,40-42`, or "unchanged"."""
    if not lines:
        return 'unchanged'
    parts, start = [], lines[0]
    for prev, n in zip(lines, lines[1:] + [None]):
        if n != prev + 1:
            parts.append(str(start) if start == prev else f'{start}-{prev}')
            start = n
    return ','.join(parts)


def lines_phrase(lines):
    return ('line ' if len(lines) == 1 else 'lines ') + lines_text(lines)


def parse_lines(text):
    if text == 'unchanged':
        return []
    result = []
    for part in text.split(','):
        a, _, b = part.partition('-')
        result.extend(range(int(a), int(b or a) + 1))
    return result


def probe(exe, paths, emit):
    """Runs the probe over `paths` in batches: a dict of path to result."""
    results = {}
    expected = set(paths)
    for i in range(0, len(paths), BATCH):
        batch = paths[i:i + BATCH]
        args = [str(exe)] + (['--emit'] if emit else []) + [f'{VIRGIL}/{p}' for p in batch]
        run = subprocess.run(args, cwd=ROOT, capture_output=True)
        if run.returncode not in (0, 1) or run.stderr:
            fail(f'the probe exited with {run.returncode}: {run.stderr.decode(errors="replace").strip()}')
        out, at = run.stdout, 0
        while at < len(out):
            end = out.find(b'\n', at)
            if end < 0:
                fail('the probe ended its output mid-line')
                break
            line = out[at:end].decode(errors='replace')
            at = end + 1
            fields = line.split('\t')
            path = fields[0][len(VIRGIL) + 1:] if fields[0].startswith(VIRGIL + '/') else None
            kind = fields[1] if len(fields) > 1 else None
            shapes = {'formatted': 4, 'fail': 3, 'syntax': 3, 'cr': 3, 'refused': 3}
            if path not in expected or shapes.get(kind) != len(fields):
                fail(f'the probe printed an unexpected line: {line!r}')
                break
            r = results.setdefault(path, {'kind': None})
            if kind == 'fail':
                r.setdefault('fails', []).append(fields[2])
            elif kind == 'formatted':
                r['kind'] = kind
                r['lines'] = [] if fields[3] == '-' else [int(n) for n in fields[3].split(',')]
                if emit:
                    size = int(fields[2])
                    text = out[at:at + size].replace(b'\r\n', b'\n')
                    r['hash'] = hashlib.sha256(text).hexdigest()[:16]
                    at += size
            else:
                r['kind'], r['message'] = kind, fields[2]
        if run.returncode == 1 and not any(results.get(p, {}).get('fails') or
                                           results.get(p, {}).get('kind') == 'refused' for p in batch):
            fail('the probe exited with 1 but reported no failure')
    for p in paths:
        if results.get(p, {}).get('kind') is None:
            fail(f'{p}: the probe reported no result')
            results.setdefault(p, {}).update(kind='missing', message='no result')
    return results


def invariants(results):
    """Reports broken invariants; returns how many files broke one."""
    broken = 0
    for path, r in results.items():
        if r['kind'] == 'refused':
            fail(f'{path}: it parses but the formatter refuses it: {r["message"]}')
        for message in r.get('fails', []):
            fail(f'{path}: {message}')
        broken += r['kind'] == 'refused' or bool(r.get('fails'))
    return broken


def record_text(pin, paths, results):
    out = [HEADER, f'pin {pin}\n']
    for directory in RECORDED:
        mine = [p for p in paths if p.startswith(directory + '/')]
        formatted = [p for p in mine if results[p]['kind'] == 'formatted']
        changed = [p for p in formatted if results[p]['lines']]
        lines = sum(len(results[p]['lines']) for p in changed)
        out.append(f'total {directory}: {len(mine)} files, {len(formatted)} formatted, '
                   f'{len(changed)} changed, {lines} lines\n')
    for p in paths:
        r = results[p]
        if r['kind'] == 'formatted':
            out.append(f'{p}\t{r["hash"]}\t{lines_text(r["lines"])}\n')
        else:
            out.append(f'{p}\trefused\t{r["message"]}\n')
    return ''.join(out)


def read_record(text):
    """The record's pin, its total lines, and a dict of path to its other two fields."""
    pin, totals, entries = None, [], {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        if line.startswith('pin '):
            pin = line[4:]
        elif line.startswith('total '):
            totals.append(line)
        else:
            fields = line.split('\t')
            if len(fields) != 3:
                fail(f'the record has a malformed line: {line!r}')
                continue
            entries[fields[0]] = (fields[1], fields[2])
    return pin, totals, entries


def compare(old_text, new_text):
    """Reports each difference of the new record from the old; returns their count."""
    old_pin, old_totals, old = read_record(old_text)
    new_pin, new_totals, new = read_record(new_text)
    problems = 0
    if old_pin != new_pin:
        fail(f'the record is for Virgil {old_pin}, but vendor/virgil is at {new_pin}; '
             'regenerate it with --write and review the diff')
        problems += 1
    for path in sorted(old.keys() | new.keys()):
        a, b = old.get(path), new.get(path)
        if a == b:
            continue
        problems += 1
        if a is None:
            fail(f'{path}: not in the record (now: {" ".join(b)})')
        elif b is None:
            fail(f'{path}: in the record but no longer in vendor/virgil')
        elif 'refused' in (a[0], b[0]):
            fail(f'{path}: the record says "{" ".join(a)}", now "{" ".join(b)}"')
        else:
            try:
                was, now = parse_lines(a[1]), parse_lines(b[1])
            except ValueError:
                fail(f'{path}: the record\'s changed lines are malformed: {a[1]!r}')
                continue
            added = [n for n in now if n not in was]
            dropped = [n for n in was if n not in now]
            parts = []
            if added:
                parts.append(f'{lines_phrase(added)} now change{"s" if len(added) == 1 else ""}')
            if dropped:
                parts.append(f'{lines_phrase(dropped)} no longer change{"s" if len(dropped) == 1 else ""}')
            if not parts:
                parts.append(f'the formatted text differs ({a[0]} in the record, now {b[0]}) '
                             f'on its changed {lines_phrase(now)}')
            fail(f'{path}: ' + '; '.join(parts))
    if not problems and old_totals != new_totals:
        fail('the record\'s totals are out of date: ' + '; '.join(new_totals))
        problems += 1
    return problems


def check_command(exe, paths, results):
    """`virgil-lsp format --check` over the recorded files must report exactly what the probe does.

    Each file that changes gets one `<path>:<line>: not formatted` line on
    standard output, with its first changed line; each refused file gets one
    error on standard error, with the probe's message; other files get
    nothing, and nothing else is printed.
    """
    run = subprocess.run([str(exe), 'format', '--check'] + [f'{VIRGIL}/{p}' for p in paths],
                         cwd=ROOT, capture_output=True)
    known = set(paths)
    reports = collections.defaultdict(list)
    problems = 0
    prefix = re.escape(VIRGIL + '/')
    for line in run.stdout.decode(errors='replace').splitlines():
        m = re.fullmatch(prefix + r'(.+):(\d+): not formatted', line)
        if not m or m.group(1) not in known:
            fail(f'format --check printed an unexpected line: {line!r}')
            problems += 1
            continue
        reports[m.group(1)].append(f'line {m.group(2)}')
    for line in run.stderr.decode(errors='replace').splitlines():
        m = (re.fullmatch(r'\S+: error: cannot format \'' + prefix + r'(.+?)\': (.*)', line)
             or re.fullmatch(r'\S+: error: ' + prefix + r'(.+?\.v3):(\d+:\d+: .*)', line))
        if not m or m.group(1) not in known:
            fail(f'format --check reported an unexpected error: {line!r}')
            problems += 1
            continue
        reports[m.group(1)].append('refused ' + m.group(2))
    for p in paths:
        r = results[p]
        if r['kind'] == 'formatted':
            want = [f'line {r["lines"][0]}'] if r['lines'] else []
        else:
            # The probe escapes a message; the command's is on one line.
            want = [f'refused {r["message"]}']
        if reports.get(p, []) != want:
            def said(items):
                return ' and '.join(f'"{item}"' for item in items) or 'nothing'
            fail(f'{p}: format --check reports {said(reports.get(p, []))}; the probe expects {said(want)}')
            problems += 1
    want_status = 1 if reports else 0
    if run.returncode != want_status:
        fail(f'format --check exited with {run.returncode}, not {want_status}')
        problems += 1
    return problems


def main(argv):
    write = '--write' in argv
    args = [a for a in argv if a != '--write']
    if len(args) != 1:
        print(__doc__.split('\n\n')[1], file=sys.stderr)
        return 2
    exe = Path(args[0]).resolve()
    probe_exe = exe.parent / 'format-corpus-probe'
    start = time.monotonic()
    revision = git('rev-parse', 'HEAD')
    if revision.returncode != 0:
        fail(f'cannot read the revision of {VIRGIL}: {revision.stderr.decode(errors="replace").strip()}')
        return 1
    pin = revision.stdout.decode().strip()
    link = gitlink()

    files = inventory()
    recorded = [p for d in RECORDED for p in files[d]]
    results = probe(probe_exe, recorded, True)
    recorded_time = time.monotonic() - start
    results.update(probe(probe_exe, files['test'], False))
    broken = invariants(results)
    problems = check_command(exe, recorded, results)
    text = record_text(pin, recorded, results)
    if write:
        if failures:
            fail('the record was not written')
            return 1
        RECORD.write_text(text, encoding='utf-8')
    elif link is not None and link != pin:
        print(f'skipped the comparison with the record: {VIRGIL} is checked out at {pin}, '
              f'not at the gitlink {link}, and the record describes the gitlink')
    elif not RECORD.exists():
        fail(f'{RECORD.relative_to(ROOT)} is missing; write it with --write')
        problems += 1
    else:
        problems += compare(RECORD.read_text(encoding='utf-8'), text)

    elapsed = time.monotonic() - start
    counts = {d: len(files[d]) for d in CHECKED}
    parsed = sum(1 for r in results.values() if r['kind'] == 'formatted')
    print(f'{len(results)} files ({", ".join(f"{counts[d]} in {d}" for d in CHECKED)}), {parsed} formatted; '
          f'{recorded_time:.1f} s for the recorded directories, {elapsed:.1f} s in all')
    if failures:
        print(f'{plural(failures, "failure")}: {plural(broken, "file")} with a broken invariant; '
              f'{plural(problems, "difference")} from the record or the command')
        return 1
    print('wrote ' + str(RECORD.relative_to(ROOT)) if write else 'ok')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
