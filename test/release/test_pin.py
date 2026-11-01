#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Virgil pin agreement check in check_pin.py."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('check_pin', Path(__file__).resolve().parent / 'check_pin.py')
pin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pin)

FULL = '57318d37e9fcca8de6fda5f9cd82d669479f49ff'
ABBREV = '57318d37'
OTHER = '7cf038fc13a5474836bec8b14c7a3e796fd07fa5'


class ExtractionTest(unittest.TestCase):
    def test_notices_full_sha(self):
        text = ('| Pinned revision | `57318d37e9fcca8de6fda5f9cd82d669479f49ff` (2026-10-27) |\n')
        self.assertEqual(pin.extract_notices(text), FULL)

    def test_notices_missing(self):
        self.assertIsNone(pin.extract_notices('| License | Apache License 2.0 |\n'))

    def test_compatibility_main_full_in_url(self):
        text = (
            '| Server version | Pinned Virgil revision | Notes |\n'
            '| --- | --- | --- |\n'
            '| `main` (unreleased) | [`57318d37`](https://github.com/titzer/virgil/commit/'
            '57318d37e9fcca8de6fda5f9cd82d669479f49ff) (2026-10-27) | Required CI |\n'
            '| `0.1.0-alpha.5` | [`7cf038fc`](https://github.com/titzer/virgil/commit/'
            '7cf038fc13a5474836bec8b14c7a3e796fd07fa5) (2026-10-08) | Fifth prerelease |\n')
        self.assertEqual(pin.extract_compatibility_main(text), FULL)

    def test_compatibility_main_abbreviated_only(self):
        text = ('| `main` (unreleased) | [`57318d37`](https://github.com/titzer/virgil) | CI |\n')
        self.assertEqual(pin.extract_compatibility_main(text), ABBREV)

    def test_compatibility_main_missing(self):
        self.assertIsNone(pin.extract_compatibility_main('| `0.1.0-alpha.5` | [`x`] | y |\n'))

    def test_corpus(self):
        self.assertEqual(pin.extract_corpus('{"virgil_source_revision": "%s"}' % FULL), FULL)

    def test_corpus_missing(self):
        self.assertIsNone(pin.extract_corpus('{"schema": 1}'))

    def test_corpus_invalid_json(self):
        self.assertIsNone(pin.extract_corpus('not json'))


class MatchingTest(unittest.TestCase):
    def test_full_sha_agrees(self):
        self.assertTrue(pin.matches(FULL, FULL))

    def test_abbreviated_sha_agrees(self):
        self.assertTrue(pin.matches(FULL, ABBREV))

    def test_other_revision_disagrees(self):
        self.assertFalse(pin.matches(FULL, OTHER))

    def test_none_disagrees(self):
        self.assertFalse(pin.matches(FULL, None))


class CheckTest(unittest.TestCase):
    def sources(self, notices=FULL, compatibility=FULL, corpus=FULL):
        return {
            'THIRD_PARTY_NOTICES.md': notices,
            'docs/compatibility.md (main row)': compatibility,
            'test/benchmarks/corpus.json': corpus,
        }

    def test_all_agree(self):
        self.assertEqual(pin.check(FULL, self.sources()), [])

    def test_abbreviated_compatibility_agrees(self):
        self.assertEqual(pin.check(FULL, self.sources(compatibility=ABBREV)), [])

    def test_notices_disagree(self):
        problems = pin.check(FULL, self.sources(notices=OTHER))
        self.assertEqual(len(problems), 1)
        self.assertIn('THIRD_PARTY_NOTICES.md', problems[0])
        self.assertIn(OTHER, problems[0])

    def test_compatibility_disagree(self):
        problems = pin.check(FULL, self.sources(compatibility=OTHER))
        self.assertEqual(len(problems), 1)
        self.assertIn('docs/compatibility.md', problems[0])

    def test_corpus_disagree(self):
        problems = pin.check(FULL, self.sources(corpus=OTHER))
        self.assertEqual(len(problems), 1)
        self.assertIn('corpus.json', problems[0])

    def test_missing_row_reported(self):
        problems = pin.check(FULL, self.sources(notices=None))
        self.assertEqual(problems, ['THIRD_PARTY_NOTICES.md: no pinned revision found'])


if __name__ == '__main__':
    unittest.main()
