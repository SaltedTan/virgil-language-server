#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check that the pinned Virgil revision agrees everywhere it is recorded.

The pin is the `vendor/virgil` gitlink. Every other record of it must name the
same commit:

- the `Pinned revision` row of `THIRD_PARTY_NOTICES.md`;
- the `main` row of the Virgil revision table in `docs/compatibility.md`;
- the `virgil_source_revision` of `test/benchmarks/corpus.json`.

A pin bump that misses one of these is caught here, in `make test`, rather than
at release. The files write the SHA in different forms: `THIRD_PARTY_NOTICES.md`
and the corpus use the full SHA, and the compatibility table writes an
abbreviated SHA in the link text and the full SHA in its URL. A source agrees
when its SHA is a prefix of the gitlink, which covers both forms.
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


def extract_notices(text):
    """The pinned revision from `THIRD_PARTY_NOTICES.md`, a full SHA in backticks."""
    match = re.search(r'Pinned revision\s*\|\s*`(' + HEX + r'{7,40})`', text)
    return match.group(1) if match else None


def extract_compatibility_main(text):
    """The pinned revision from the `main` row of the compatibility table.

    The row writes an abbreviated SHA in the link text and the full SHA in its
    commit URL. The full SHA is authoritative, so prefer it.
    """
    for line in text.splitlines():
        if not re.match(r'\|\s*`main`', line):
            continue
        full = re.search(r'commit/(' + HEX + r'{40})\)', line)
        if full:
            return full.group(1)
        abbreviated = re.search(r'\[`(' + HEX + r'{7,40})`\]', line)
        return abbreviated.group(1) if abbreviated else None
    return None


def extract_corpus(text):
    """The `virgil_source_revision` of the benchmark corpus, a full SHA."""
    try:
        return json.loads(text).get('virgil_source_revision')
    except ValueError:
        return None


def matches(gitlink, value):
    """True when a recorded SHA names the same commit as the gitlink.

    The gitlink is a full SHA; a recorded value agrees when it is a prefix of
    it, which matches both the full SHA and an abbreviated one.
    """
    return value is not None and re.fullmatch(HEX + r'{7,40}', value) and gitlink.startswith(value)


def check(gitlink, sources):
    """The problems, one per source: a missing value, or a value that disagrees."""
    problems = []
    for name, value in sources.items():
        if value is None:
            problems.append(f'{name}: no pinned revision found')
        elif not matches(gitlink, value):
            problems.append(f'{name}: {value} does not match the gitlink {gitlink}')
    return problems


def read_gitlink(root):
    """The committed `vendor/virgil` gitlink, or the submodule's HEAD as a fallback."""
    root = Path(root)
    try:
        gitlink = subprocess.run(
            ['git', '-C', str(root), 'rev-parse', 'HEAD:vendor/virgil'],
            capture_output=True, text=True, check=True).stdout.strip()
        if re.fullmatch(HEX + r'{40}', gitlink):
            return gitlink
    except (subprocess.CalledProcessError, OSError):
        pass
    try:
        return subprocess.run(
            ['git', '-C', str(root / 'vendor' / 'virgil'), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as error:
        raise RuntimeError(f'cannot read the vendor/virgil gitlink: {error}') from None


def main(argv=None):
    root = ROOT
    gitlink = read_gitlink(root)
    sources = {
        'THIRD_PARTY_NOTICES.md': extract_notices(NOTICES.read_text(encoding='utf-8')),
        'docs/compatibility.md (main row)': extract_compatibility_main(
            COMPATIBILITY.read_text(encoding='utf-8')),
        'test/benchmarks/corpus.json': extract_corpus(CORPUS.read_text(encoding='utf-8')),
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
