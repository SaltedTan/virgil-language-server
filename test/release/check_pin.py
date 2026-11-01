#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check that the pinned Virgil revision agrees everywhere it is recorded.

The pin is the `vendor/virgil` gitlink, read from the index so a staged pin
bump can be validated before it is committed (as `scripts/bench-corpus.py`
does), falling back to `HEAD` only where there is no index entry. Every other
record of it must name the same commit:

- the `Pinned revision` row of `THIRD_PARTY_NOTICES.md`;
- the `main` row of the Virgil revision table in `docs/compatibility.md`, in
  both its link-text SHA and its commit-URL SHA;
- the `virgil_source_revision` of `test/benchmarks/corpus.json`.

A pin bump that misses one of these is caught here, in `make test`, rather than
at release. The files write the SHA in different forms: `THIRD_PARTY_NOTICES.md`
and the corpus use the full SHA, and the compatibility table writes an
abbreviated SHA in the link text and the full SHA in its URL. A source agrees
when its SHA is a prefix of the gitlink, which covers both forms.

The check runs only from a checkout: it verifies the given root is itself the
top of a repository, and skips with a message (exit 0) when there is no Git
metadata, as in an exported tree. The release workflow runs from a checkout, so
skipping there is correct.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NOTICES = ROOT / 'THIRD_PARTY_NOTICES.md'
COMPATIBILITY = ROOT / 'docs' / 'compatibility.md'
CORPUS = ROOT / 'test' / 'benchmarks' / 'corpus.json'

HEX = r'[0-9a-f]'


class PinError(Exception):
    """A hard failure reading the pin, reported as a failure rather than a skip."""


def extract_notices(text):
    """The pinned revision from `THIRD_PARTY_NOTICES.md`, a full SHA in backticks."""
    match = re.search(r'Pinned revision\s*\|\s*`(' + HEX + r'{7,40})`', text)
    return match.group(1) if match else None


def extract_compatibility_main(text):
    """The two SHAs of the `main` row of the compatibility table.

    The row writes an abbreviated SHA in the link text and the full SHA in its
    commit URL. Both are checked, so a stale displayed SHA is caught even when
    the URL is correct. Each is `None` when the row (or that part of it) is
    missing.
    """
    for line in text.splitlines():
        if not re.match(r'\|\s*`main`', line):
            continue
        link = re.search(r'\[`(' + HEX + r'{7,40})`\]', line)
        url = re.search(r'commit/(' + HEX + r'{40})\)', line)
        return (link.group(1) if link else None,
                url.group(1) if url else None)
    return (None, None)


def extract_corpus(text):
    """The `virgil_source_revision` of the benchmark corpus.

    Returns the value when the JSON is an object and the value is present, so a
    non-string value is reported as invalid rather than crashing. Returns
    `None` when the file is not valid JSON, is not an object, or has no key.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data.get('virgil_source_revision')


def matches(gitlink, value):
    """True when a recorded SHA names the same commit as the gitlink.

    The gitlink is a full SHA; a recorded value agrees when it is a prefix of
    it, which matches both the full SHA and an abbreviated one. Empty values
    and SHAs shorter than seven characters are rejected.
    """
    return isinstance(value, str) and re.fullmatch(HEX + r'{7,40}', value) and gitlink.startswith(value)


def check(gitlink, sources):
    """The problems, one per source: a missing value, an invalid value, or a value that disagrees."""
    problems = []
    for name, value in sources.items():
        if value is None:
            problems.append(f'{name}: no pinned revision found')
        elif not isinstance(value, str):
            problems.append(f'{name}: invalid revision value {value!r}')
        elif not matches(gitlink, value):
            problems.append(f'{name}: {value} does not match the gitlink {gitlink}')
    return problems


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args],
                          capture_output=True, text=True)


def read_gitlink(root):
    """The `vendor/virgil` gitlink from the index, `None` to skip, or a `PinError`.

    Reads the gitlink from the index, not `HEAD`, so a staged pin bump can be
    validated before it is committed; falls back to `HEAD` only where there is
    no index entry at all. Returns `None` when `root` is not itself the top of a
    repository (a standalone export, or a tree inside another repository), so
    the caller skips rather than reading a parent repository's pin. Raises
    `PinError` for an unmerged or malformed gitlink, which is a failure, not an
    agreement.
    """
    root = Path(root).resolve()
    toplevel = _git(root, 'rev-parse', '--show-toplevel')
    if toplevel.returncode != 0:
        return None
    if Path(toplevel.stdout.strip()).resolve() != root:
        return None
    tree = _git(root, 'ls-files', '-s', 'vendor/virgil')
    if tree.returncode != 0:
        raise PinError(f'cannot read the vendor/virgil index: {tree.stderr.strip()}')
    lines = [line for line in tree.stdout.splitlines() if line.strip()]
    if not lines:
        head = _git(root, 'rev-parse', 'HEAD:vendor/virgil')
        if head.returncode == 0 and re.fullmatch(HEX + r'{40}', head.stdout.strip()):
            return head.stdout.strip()
        return None
    if len(lines) > 1:
        raise PinError('vendor/virgil has more than one index entry (unmerged)')
    match = re.fullmatch(r'160000 (' + HEX + r'{40}) (0|[1-9])\tvendor/virgil', lines[0])
    if not match:
        raise PinError(f'malformed vendor/virgil index entry: {lines[0]!r}')
    if match.group(2) != '0':
        raise PinError(f'vendor/virgil index entry is not stage 0 (unmerged, stage {match.group(2)})')
    return match.group(1)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else ROOT
    try:
        gitlink = read_gitlink(root)
    except PinError as error:
        print(f'Virgil pin check failed: {error}')
        return 1
    if gitlink is None:
        print(f'skipped: no Git metadata for {root}')
        return 0
    sources = {
        'THIRD_PARTY_NOTICES.md': extract_notices((root / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')),
        'docs/compatibility.md (main row, link text)': extract_compatibility_main(
            (root / 'docs' / 'compatibility.md').read_text(encoding='utf-8'))[0],
        'docs/compatibility.md (main row, URL)': extract_compatibility_main(
            (root / 'docs' / 'compatibility.md').read_text(encoding='utf-8'))[1],
        'test/benchmarks/corpus.json': extract_corpus(
            (root / 'test' / 'benchmarks' / 'corpus.json').read_text(encoding='utf-8')),
    }
    problems = check(gitlink, sources)
    if problems:
        print(f'Virgil pin disagrees with the gitlink {gitlink}:')
        for problem in problems:
            print(f'  {problem}')
        return 1
    print(f'Virgil pin agrees everywhere: {gitlink}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
