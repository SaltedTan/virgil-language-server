#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Virgil pin agreement check in check_pin.py."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('check_pin', Path(__file__).resolve().parent / 'check_pin.py')
pin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pin)

SCRIPT = Path(__file__).resolve().parent / 'check_pin.py'

FULL = '57318d37e9fcca8de6fda5f9cd82d669479f49ff'
ABBREV = '57318d37'
OTHER = '7cf038fc13a5474836bec8b14c7a3e796fd07fa5'
OTHER_ABBREV = '7cf038fc'


def notices_text(sha):
    return ('## Virgil (Aeneas compiler and libraries)\n\n'
            f'| Pinned revision | `{sha}` (2026-10-27) |\n')


def compatibility_text(abbrev, full):
    return ('| Server version | Pinned Virgil revision | Notes |\n'
            '| --- | --- | --- |\n'
            f'| `main` (unreleased) | [`{abbrev}`]'
            f'(https://github.com/titzer/virgil/commit/{full}) (2026-10-27) | Required CI |\n')


def corpus_text(value):
    return json.dumps({'schema': 1, 'virgil_source_revision': value})


def write_tree(root, notices=FULL, abbrev=ABBREV, full=FULL, corpus=FULL):
    root = Path(root)
    (root / 'docs').mkdir(parents=True, exist_ok=True)
    (root / 'test' / 'benchmarks').mkdir(parents=True, exist_ok=True)
    (root / 'test' / 'release').mkdir(parents=True, exist_ok=True)
    (root / 'THIRD_PARTY_NOTICES.md').write_text(notices_text(notices))
    (root / 'docs' / 'compatibility.md').write_text(compatibility_text(abbrev, full))
    (root / 'test' / 'benchmarks' / 'corpus.json').write_text(corpus_text(corpus))


def init_repo(root, gitlink):
    root = Path(root)
    for args in (['init', '-q'], ['config', 'user.email', 't@example.com'],
                 ['config', 'user.name', 't'],
                 ['update-index', '--add', '--cacheinfo', f'160000,{gitlink},vendor/virgil'],
                 ['commit', '-qm', 'init']):
        subprocess.run(['git', '-C', str(root), *args], check=True,
                       capture_output=True, text=True)


def stage_bump(root, gitlink):
    """Stage a different gitlink in the index without committing it."""
    subprocess.run(['git', '-C', str(root), 'update-index', '--add',
                    '--cacheinfo', f'160000,{gitlink},vendor/virgil'],
                   check=True, capture_output=True, text=True)


def unmerged_gitlink(root):
    info = (f'160000 {FULL} 1\tvendor/virgil\n'
            f'160000 {OTHER} 2\tvendor/virgil\n'
            f'160000 {OTHER} 3\tvendor/virgil\n')
    subprocess.run(['git', '-C', str(root), 'update-index', '--index-info'],
                   input=info, check=True, capture_output=True, text=True)


def sources(notices=FULL, abbrev=ABBREV, full=FULL, corpus=FULL):
    return {
        'THIRD_PARTY_NOTICES.md': notices,
        'docs/compatibility.md (main row, link text)': abbrev,
        'docs/compatibility.md (main row, URL)': full,
        'test/benchmarks/corpus.json': corpus,
    }


def run_cli(root):
    return subprocess.run([sys.executable, str(SCRIPT), str(root)],
                          capture_output=True, text=True)


class ExtractionTest(unittest.TestCase):
    def test_notices_full_sha(self):
        self.assertEqual(pin.extract_notices(notices_text(FULL)), FULL)

    def test_notices_missing(self):
        self.assertIsNone(pin.extract_notices('| License | Apache License 2.0 |\n'))

    def test_compatibility_main_both(self):
        text = compatibility_text(ABBREV, FULL)
        self.assertEqual(pin.extract_compatibility_main(text), (ABBREV, FULL))

    def test_compatibility_main_missing(self):
        self.assertEqual(pin.extract_compatibility_main('| `0.1.0-alpha.5` | [`x`] | y |\n'), (None, None))

    def test_corpus(self):
        self.assertEqual(pin.extract_corpus(corpus_text(FULL)), FULL)

    def test_corpus_missing(self):
        self.assertIsNone(pin.extract_corpus('{"schema": 1}'))

    def test_corpus_invalid_json(self):
        self.assertIsNone(pin.extract_corpus('not json'))

    def test_corpus_not_an_object(self):
        self.assertIsNone(pin.extract_corpus('[1, 2, 3]'))

    def test_corpus_non_string_value(self):
        self.assertEqual(pin.extract_corpus('{"virgil_source_revision": 123}'), 123)


class MatchingTest(unittest.TestCase):
    def test_full_sha_agrees(self):
        self.assertTrue(pin.matches(FULL, FULL))

    def test_abbreviated_sha_agrees(self):
        self.assertTrue(pin.matches(FULL, ABBREV))

    def test_other_revision_disagrees(self):
        self.assertFalse(pin.matches(FULL, OTHER))

    def test_none_disagrees(self):
        self.assertFalse(pin.matches(FULL, None))

    def test_empty_sha_rejected(self):
        self.assertFalse(pin.matches(FULL, ''))

    def test_short_sha_rejected(self):
        self.assertFalse(pin.matches(FULL, '57318'))


class CheckTest(unittest.TestCase):
    def test_all_agree(self):
        self.assertEqual(pin.check(FULL, sources()), [])

    def test_notices_disagree(self):
        problems = pin.check(FULL, sources(notices=OTHER))
        self.assertEqual(problems, [f'THIRD_PARTY_NOTICES.md: {OTHER} does not match the gitlink {FULL}'])

    def test_link_text_disagree(self):
        problems = pin.check(FULL, sources(abbrev=OTHER_ABBREV))
        self.assertEqual(problems,
                         [f'docs/compatibility.md (main row, link text): {OTHER_ABBREV} does not match the gitlink {FULL}'])

    def test_url_disagree(self):
        problems = pin.check(FULL, sources(full=OTHER))
        self.assertEqual(problems,
                         [f'docs/compatibility.md (main row, URL): {OTHER} does not match the gitlink {FULL}'])

    def test_corpus_disagree(self):
        problems = pin.check(FULL, sources(corpus=OTHER))
        self.assertEqual(problems,
                         [f'test/benchmarks/corpus.json: {OTHER} does not match the gitlink {FULL}'])

    def test_missing_row_reported(self):
        self.assertEqual(pin.check(FULL, sources(notices=None)),
                         ['THIRD_PARTY_NOTICES.md: no pinned revision found'])

    def test_non_string_corpus_reported(self):
        self.assertEqual(pin.check(FULL, sources(corpus=123)),
                         ["test/benchmarks/corpus.json: invalid revision value 123"])


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_all_agree(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        self.assertEqual(pin.read_gitlink(self.root), FULL)
        self.assertEqual(pin.check(FULL, sources()), [])

    def test_staged_bump_records_updated(self):
        write_tree(self.root, notices=OTHER, abbrev=OTHER_ABBREV, full=OTHER, corpus=OTHER)
        init_repo(self.root, FULL)
        stage_bump(self.root, OTHER)
        self.assertEqual(pin.read_gitlink(self.root), OTHER)
        self.assertEqual(pin.check(OTHER, sources(notices=OTHER, abbrev=OTHER_ABBREV,
                                                  full=OTHER, corpus=OTHER)), [])

    def test_staged_bump_stale_records(self):
        write_tree(self.root)  # records still name FULL, which equals HEAD
        init_repo(self.root, FULL)
        stage_bump(self.root, OTHER)  # staged gitlink is OTHER
        self.assertEqual(pin.read_gitlink(self.root), OTHER)
        problems = pin.check(OTHER, sources())
        self.assertEqual(len(problems), 4)
        for name in ('THIRD_PARTY_NOTICES.md', 'link text', 'URL', 'corpus.json'):
            self.assertTrue(any(name in p for p in problems), problems)

    def test_unmerged_gitlink_fails(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        unmerged_gitlink(self.root)
        with self.assertRaises(pin.PinError):
            pin.read_gitlink(self.root)

    def test_stale_link_text(self):
        write_tree(self.root, abbrev=OTHER_ABBREV, full=FULL)
        init_repo(self.root, FULL)
        problems = pin.check(FULL, sources(abbrev=OTHER_ABBREV))
        self.assertEqual(problems,
                         [f'docs/compatibility.md (main row, link text): {OTHER_ABBREV} does not match the gitlink {FULL}'])

    def test_no_git_inside_parent_skips(self):
        init_repo(self.root, FULL)  # outer repository
        inner = self.root / 'inner'
        write_tree(inner)
        self.assertIsNone(pin.read_gitlink(inner))

    def test_no_git_standalone_skips(self):
        write_tree(self.root)
        self.assertIsNone(pin.read_gitlink(self.root))


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_all_agree(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 0)
        self.assertIn(f'Virgil pin agrees everywhere: {FULL}', result.stdout)

    def test_stale_records_named(self):
        write_tree(self.root)
        init_repo(self.root, OTHER)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 1)
        for name in ('THIRD_PARTY_NOTICES.md', 'link text', 'URL', 'corpus.json'):
            self.assertIn(name, result.stdout)

    def test_no_git_skips(self):
        write_tree(self.root)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 0)
        self.assertIn('skipped: no Git metadata', result.stdout)

    def test_staged_bump_updated_records(self):
        write_tree(self.root, notices=OTHER, abbrev=OTHER_ABBREV, full=OTHER, corpus=OTHER)
        init_repo(self.root, FULL)
        stage_bump(self.root, OTHER)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 0)
        self.assertIn(f'Virgil pin agrees everywhere: {OTHER}', result.stdout)

    def test_staged_bump_stale_records(self):
        write_tree(self.root)  # records name FULL, which equals HEAD
        init_repo(self.root, FULL)
        stage_bump(self.root, OTHER)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 1)
        for name in ('THIRD_PARTY_NOTICES.md', 'link text', 'URL', 'corpus.json'):
            self.assertIn(name, result.stdout)

    def test_stale_link_text_alone(self):
        write_tree(self.root, abbrev=OTHER_ABBREV, full=FULL)
        init_repo(self.root, FULL)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn('link text', result.stdout)
        self.assertNotIn('URL', result.stdout)

    def test_unmerged_gitlink_fails(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        unmerged_gitlink(self.root)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn('unmerged', result.stdout)

    def test_missing_index_entry_falls_back_to_head(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        subprocess.run(['git', '-C', str(self.root), 'update-index',
                        '--force-remove', 'vendor/virgil'], check=True,
                       capture_output=True, text=True)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 0)
        self.assertIn(f'Virgil pin agrees everywhere: {FULL}', result.stdout)

    def test_head_fallback_rejects_regular_file(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        blob = subprocess.run(['git', '-C', str(self.root), 'hash-object',
                               '-w', '--stdin'], input='not a submodule\n',
                              check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(['git', '-C', str(self.root), 'update-index', '--add',
                        '--cacheinfo', f'100644,{blob},vendor/virgil'],
                       check=True, capture_output=True, text=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'regular file'],
                       check=True, capture_output=True, text=True)
        subprocess.run(['git', '-C', str(self.root), 'update-index',
                        '--force-remove', 'vendor/virgil'], check=True,
                       capture_output=True, text=True)
        write_tree(self.root, notices=blob, abbrev=blob[:8], full=blob, corpus=blob)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn('malformed', result.stdout)
        self.assertNotIn('skipped:', result.stdout)

    def test_head_fallback_rejects_missing_pin(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        subprocess.run(['git', '-C', str(self.root), 'update-index',
                        '--force-remove', 'vendor/virgil'], check=True,
                       capture_output=True, text=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'remove pin'],
                       check=True, capture_output=True, text=True)
        result = run_cli(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn('missing', result.stdout)
        self.assertNotIn('skipped:', result.stdout)

    def test_other_dependency_cannot_supply_missing_or_invalid_virgil_revision(self):
        write_tree(self.root)
        init_repo(self.root, FULL)
        for value in ('', FULL[:5], None):
            with self.subTest(virgil_revision=value):
                text = notices_text(value) if value is not None else ''
                text += f'\n## Another dependency\n\n| Pinned revision | `{FULL}` |\n'
                (self.root / 'THIRD_PARTY_NOTICES.md').write_text(text)
                result = run_cli(self.root)
                self.assertEqual(result.returncode, 1)
                self.assertIn('THIRD_PARTY_NOTICES.md: no pinned revision found', result.stdout)


if __name__ == '__main__':
    unittest.main()
