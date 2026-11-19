#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check the formatter against the record of the pinned Virgil sources.

Usage: test/format/corpus.py [--write] build/virgil-lsp

ADR-0019 decision 8. `test/format/virgil-corpus.txt` records the Virgil pin
and, for every `.v3` file of `aeneas/src`, `lib`, and `apps` in the pinned
`vendor/virgil` in path order, whether it formatted or its first diagnostic,
a hash of its formatted text, and the lines of the input that formatting changes.
This suite formats those files again and fails, naming
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
import hashlib
import importlib.util
import itertools
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
PROBE_SHAPES = {'formatted': 4, 'fail': 3, 'syntax': 4, 'cr': 4, 'refused': 4}
PROBE_ESCAPE = re.compile(r'\\([\\trn])')
PROBE_MESSAGE = re.compile(r'(?:[^\\\r\t]|\\[\\trn])+')
UNESCAPED = {'\\': '\\', 't': '\t', 'r': '\r', 'n': '\n'}
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
# formatting changes, and their changed lines. The file rows are sorted by path.
# Each file has a line of three fields separated by tabs: its path in
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


def source_paths(*options):
    run = git('ls-files', '-z', *options, '--', *CHECKED)
    if run.returncode != 0:
        fail(f'cannot list the files of {VIRGIL}: {run.stderr.decode(errors="replace").strip()}')
        return set()
    return {path.decode() for path in set(run.stdout.split(b'\0'))
            if path.endswith(b'.v3') and usable(path)}


def inventory():
    """The tracked `.v3` files of each checked directory, by directory.

    Fails, naming them, for tracked files missing from disk, untracked files
    found on disk, and paths that the probe and the record can't carry: not
    UTF-8, or with a tab or line end. Those are left out of the checks.
    """
    tracked = source_paths('--cached')
    untracked = source_paths('--others')
    base = ROOT / VIRGIL
    missing = {p for p in tracked if not (base / p).is_file()}
    if missing:
        fail(f'{plural(len(missing), "tracked file")} missing from {VIRGIL}, as in a sparse checkout: '
             f'{listed(missing)}')
    if untracked:
        fail(f'{plural(len(untracked), "file")} in {VIRGIL} not tracked: {listed(untracked)}')
    present = sorted(tracked - missing)
    return {d: [p for p in present if p.startswith(d + '/')] for d in CHECKED}


def usable(raw):
    """Whether the path `raw` is UTF-8 without a tab or line end; reports it if not."""
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        fail(f'{raw!r}: the path is not UTF-8, so the record can\'t hold it')
        return False
    if any(c in text for c in '\t\n\r'):
        fail(f'{text!r}: the path has a tab or line end, so the record can\'t hold it')
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
    for i in range(0, len(paths), BATCH):
        batch = paths[i:i + BATCH]
        args = [str(exe)] + (['--emit'] if emit else []) + [f'{VIRGIL}/{p}' for p in batch]
        run = subprocess.run(args, cwd=ROOT, capture_output=True)
        if run.returncode not in (0, 1) or run.stderr:
            raise ValueError(f'the probe exited with {run.returncode}: {run.stderr!r}')
        parsed = probe_frames(run.stdout, batch, emit)
        expected_status = int(any(r.get('fails') or r['kind'] == 'refused' for r in parsed.values()))
        if run.returncode != expected_status:
            raise ValueError(f'{batch[0]}: the probe exited with {run.returncode}, '
                             f'but its reports imply {expected_status}')
        results.update(parsed)
    return results


def probe_frames(out, batch, emit):
    results = {}
    at = 0
    last_formatted = None
    while at < len(out):
        end = out.find(b'\n', at)
        if end < 0:
            raise ValueError(f'the probe ended its output mid-line: {out[at:]!r}')
        line = out[at:end].decode(errors='surrogateescape')
        at = end + 1
        fields = line.split('\t')
        kind = fields[1] if len(fields) > 1 else None
        if not fields[0].startswith(VIRGIL + '/') or PROBE_SHAPES.get(kind) != len(fields):
            raise ValueError(f'the probe printed an unexpected line: {line!r}')
        path = fields[0][len(VIRGIL) + 1:]
        if kind != 'formatted' and not PROBE_MESSAGE.fullmatch(fields[-1]):
            raise ValueError(f'{path}: the probe printed a malformed {kind} message: {fields[-1]!r}')
        if kind == 'fail' and path == last_formatted:
            results[path].setdefault('fails', []).append(fields[2])
            continue
        if len(results) == len(batch) or path != batch[len(results)]:
            raise ValueError(f'the probe reported an unexpected result: {line!r}')
        last_formatted = None
        if kind == 'formatted':
            if not re.fullmatch(r'[0-9]+', fields[2]):
                raise ValueError(f'{path}: the probe reported an invalid size: {fields[2]!r}')
            size = int(fields[2])
            if not re.fullmatch(r'-|[1-9][0-9]*(?:,[1-9][0-9]*)*', fields[3]):
                raise ValueError(f'{path}: the probe reported invalid changed lines: {fields[3]!r}')
            lines = [] if fields[3] == '-' else [int(n) for n in fields[3].split(',')]
            if any(a >= b for a, b in zip(lines, lines[1:])):
                raise ValueError(f'{path}: the probe reported unordered changed lines: {fields[3]!r}')
            result = {'kind': kind, 'lines': lines}
            if emit:
                if size > len(out) - at:
                    raise ValueError(f'{path}: the probe ended its output mid-payload')
                formatted = out[at:at + size].replace(b'\r\n', b'\n')
                result['hash'] = hashlib.sha256(formatted).hexdigest()[:16]
                at += size
            last_formatted = path
        elif kind == 'fail':
            result = {'kind': 'failed', 'fails': [fields[2]]}
        else:
            form, message = fields[2:]
            if form != 'refusal' and (form != 'diagnostic' or
                                      not re.fullmatch(r'[0-9]+:[0-9]+: .+', message)):
                raise ValueError(f'{path}: the probe printed a malformed {kind} report: {line!r}')
            result = {'kind': kind, 'form': form, 'message': message}
        results[path] = result
    if len(results) != len(batch):
        raise ValueError(f'{batch[len(results)]}: the probe reported no result')
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
            message = r['message'].encode(errors='surrogateescape').decode(errors='backslashreplace')
            out.append(f'{p}\trefused\t{message}\n')
    return ''.join(out)


def read_record(text):
    """The record's pin, its total lines, and a dict of path to its other two fields."""
    pin, totals, entries = None, [], {}
    previous = ''
    for line in text.split('\n'):
        if not line or line.startswith('#'):
            continue
        if line.startswith('pin '):
            pin = line[4:]
        elif line.startswith('total '):
            totals.append(line)
        else:
            fields = line.split('\t')
            if len(fields) != 3:
                raise ValueError(f'the record has a malformed line: {line!r}')
            path, status, detail = fields
            if path in entries:
                raise ValueError(f'{path}: duplicate path in the record')
            if not any(path.startswith(d + '/') for d in RECORDED):
                raise ValueError(f'{path}: path outside the recorded directories')
            if path <= previous:
                raise ValueError(f'{path}: out of order in the record after {previous}')
            previous = path
            entries[path] = (status, detail)
    return pin, totals, entries


def compare(old_text, new_text):
    """Reports each difference of the new record from the old; returns their count."""
    try:
        old_pin, old_totals, old = read_record(old_text)
        new_pin, new_totals, new = read_record(new_text)
    except ValueError as error:
        fail(error)
        return 1
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
    stdout, stderr = [], []
    for p in paths:
        r = results[p]
        path = f'{VIRGIL}/{p}'
        if r['kind'] == 'formatted':
            if r['lines']:
                stdout.append(f'{path}:{r["lines"][0]}: not formatted\n')
        else:
            message = PROBE_ESCAPE.sub(lambda m: UNESCAPED[m[1]], r['message'])
            location = f'{path}:' if r['form'] == 'diagnostic' else f"cannot format '{path}': "
            stderr.append(f'virgil-lsp: error: {location}{message}\n')
    problems = 0
    for name, actual, reports in (('stdout', run.stdout, stdout), ('stderr', run.stderr, stderr)):
        expected = ''.join(reports).encode(errors='surrogateescape')
        if actual != expected:
            lines = itertools.zip_longest(actual.splitlines(keepends=True),
                                          expected.splitlines(keepends=True), fillvalue=b'')
            got, want = next((got, want) for got, want in lines if got != want)
            fail(f'format --check {name}: expected {want!r}, got {got!r}')
            problems += 1
    want_status = int(bool(stdout or stderr))
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
    recorded = sorted(p for d in RECORDED for p in files[d])
    try:
        results = probe(probe_exe, recorded, True)
        recorded_time = time.monotonic() - start
        results.update(probe(probe_exe, files['test'], False))
    except ValueError as error:
        fail(error)
        return 1
    broken = invariants(results)
    if any(results[p]['kind'] == 'failed' for p in recorded):
        return 1
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
