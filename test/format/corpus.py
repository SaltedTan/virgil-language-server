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

It also requires, for every file of `aeneas/src`, `lib`, `apps`, and `test`
that parses, that formatting the output again changes nothing and that the
output keeps the input's parser tokens and comments.

The files are formatted in batches by `format-corpus-probe`, built beside
the server from `test/format/FormatCorpusProbe.v3`, which calls the
formatter as the `format` command does and compares the tokens that the
parser consumed, so no second lexer can disagree with the parser. The suite
also runs `virgil-lsp format --check` once over the recorded files and
requires its report to agree with the record.
"""
import hashlib
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
# and apps then has a line: its path in vendor/virgil, then either "refused"
# and its first diagnostic, or the first 16 hex digits of the SHA-256 of its
# formatted text and "unchanged" or the lines of the file that an edit
# touches. A brace or else join touches two lines, and each blank line
# removed at the end of a file is a line of its own.
"""


failures = 0


def fail(message):
    """Reports a failure; the suite then exits with 1."""
    global failures
    failures += 1
    print(f'FAIL {message}')


def files(directory):
    """The `.v3` files under `vendor/virgil/<directory>`, relative to it, sorted."""
    base = ROOT / VIRGIL
    return sorted(p.relative_to(base).as_posix() for p in (base / directory).rglob('*.v3') if p.is_file())


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
            fail(f'the probe exited with {run.returncode}: {run.stderr.decode(errors="replace").strip()}')
        out, at = run.stdout, 0
        while at < len(out):
            end = out.find(b'\n', at)
            if end < 0:
                fail('the probe ended its output mid-line')
                break
            fields = out[at:end].decode().split('\t', 2)
            at = end + 1
            path = fields[0][len(VIRGIL) + 1:]
            if fields[1] == 'fail':
                results.setdefault(path, {'kind': None}).setdefault('fails', []).append(fields[2])
                continue
            r = results.setdefault(path, {})
            r['kind'] = fields[1]
            if fields[1] == 'formatted':
                size, lines = fields[2].split('\t')
                r['lines'] = [] if lines == '-' else [int(n) for n in lines.split(',')]
                if emit:
                    r['hash'] = hashlib.sha256(out[at:at + int(size)]).hexdigest()[:16]
                    at += int(size)
            else:
                r['message'] = fields[2] if len(fields) > 2 else 'a carriage return without a line feed'
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
            fail(f'{path}: it parses but is not formatted: {r["message"]}')
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
            out.append(f'{p} {r["hash"]} {lines_text(r["lines"])}\n')
        else:
            out.append(f'{p} refused {r["message"]}\n')
    return ''.join(out)


def read_record(text):
    """The record's pin, its total lines, and a dict of path to the rest of its line."""
    pin, totals, entries = None, [], {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        if line.startswith('pin '):
            pin = line[4:]
        elif line.startswith('total '):
            totals.append(line)
        else:
            path, _, rest = line.partition(' ')
            entries[path] = rest
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
            fail(f'{path}: not in the record (now: {b})')
        elif b is None:
            fail(f'{path}: in the record but no longer in vendor/virgil')
        elif a.startswith('refused ') or b.startswith('refused '):
            fail(f'{path}: the record says "{a}", now "{b}"')
        else:
            old_hash, old_lines = a.split(' ', 1)
            new_hash, new_lines = b.split(' ', 1)
            was, now = parse_lines(old_lines), parse_lines(new_lines)
            added = [n for n in now if n not in was]
            dropped = [n for n in was if n not in now]
            parts = []
            if added:
                parts.append(f'{lines_phrase(added)} now change{"s" if len(added) == 1 else ""}')
            if dropped:
                parts.append(f'{lines_phrase(dropped)} no longer change{"s" if len(dropped) == 1 else ""}')
            if not parts:
                parts.append(f'the formatted text differs ({old_hash} in the record, now {new_hash}) '
                             f'on its changed {lines_phrase(now)}')
            fail(f'{path}: ' + '; '.join(parts))
    if not problems and old_totals != new_totals:
        fail('the record\'s totals are out of date: ' + '; '.join(new_totals))
        problems += 1
    return problems


def check_command(exe, paths, results):
    """`virgil-lsp format --check` over the recorded files must agree with the probe."""
    run = subprocess.run([str(exe), 'format', '--check'] + [f'{VIRGIL}/{p}' for p in paths],
                         cwd=ROOT, capture_output=True, text=True)
    reported = {}
    for line in run.stdout.splitlines():
        m = re.fullmatch(re.escape(VIRGIL) + r'/(.+):(\d+): not formatted', line)
        if not m:
            fail(f'format --check printed an unexpected line: {line}')
            continue
        reported[m.group(1)] = f'line {m.group(2)}'
    for line in run.stderr.splitlines():
        m = re.fullmatch(r'\S+: error: (?:cannot format \'' + re.escape(VIRGIL) + r'/(.+?)\': (.*)|'
                         + re.escape(VIRGIL) + r'/(.+?\.v3):(.*))', line)
        if not m:
            fail(f'format --check reported an unexpected error: {line}')
            continue
        reported[m.group(1) or m.group(3)] = 'refused ' + (m.group(2) or m.group(4).strip())
    problems = 0
    for p in paths:
        r = results[p]
        if r['kind'] == 'formatted':
            want = f'line {r["lines"][0]}' if r['lines'] else None
        elif r['kind'] == 'syntax':
            want = f'refused {r["message"]}'
        else:
            want = reported.get(p, 'refused')
        if reported.get(p) != want:
            fail(f'{p}: format --check reports {reported.get(p) or "nothing"}, the probe {want or "nothing"}')
            problems += 1
    want_status = 1 if reported else 0
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
    revision = subprocess.run(['git', '-C', str(ROOT / VIRGIL), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True)
    if revision.returncode != 0:
        fail(f'cannot read the revision of {VIRGIL}: {revision.stderr.strip()}')
        return 1
    pin = revision.stdout.strip()

    recorded = [p for d in RECORDED for p in files(d)]
    others = files('test')
    results = probe(probe_exe, recorded, True)
    recorded_time = time.monotonic() - start
    results.update(probe(probe_exe, others, False))
    broken = invariants(results)
    problems = check_command(exe, recorded, results)
    text = record_text(pin, recorded, results)
    if write:
        if failures:
            fail('the record was not written')
            return 1
        RECORD.write_text(text, encoding='utf-8')
    elif not RECORD.exists():
        fail(f'{RECORD.relative_to(ROOT)} is missing; write it with --write')
        problems += 1
    else:
        problems += compare(RECORD.read_text(encoding='utf-8'), text)

    elapsed = time.monotonic() - start
    counts = {d: sum(1 for p in results if p.startswith(d + '/')) for d in CHECKED}
    parsed = sum(1 for r in results.values() if r['kind'] == 'formatted')
    print(f'{len(results)} files ({", ".join(f"{counts[d]} in {d}" for d in CHECKED)}), {parsed} formatted; '
          f'{recorded_time:.1f} s for the recorded directories, {elapsed:.1f} s in all')
    if failures:
        print(f'{broken} files broke an invariant; {problems} '
              f'difference{"" if problems == 1 else "s"} from the record or the command')
        return 1
    print('wrote ' + str(RECORD.relative_to(ROOT)) if write else 'ok')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
