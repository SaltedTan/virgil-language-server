#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Bundle integrity, entry types, and archive reproducibility without compiling."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release_library', Path(__file__).resolve().parents[2] / 'scripts/release-library.py')
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        (self.source / 'lib/util').mkdir(parents=True)
        (self.source / 'rt').mkdir()
        (self.source / 'lib/util/B.v3').write_bytes(b'// upstream attribution\nclass B {}\n')
        (self.source / 'lib/util/A.v3').write_bytes(b'class A {}\n')
        (self.source / 'rt/LICENSE').write_bytes(b'Apache License, Version 2.0\n')
        self.revision = '7' * 40
        self.stage = self.root / 'virgil-lsp-test'
        self.stage.mkdir()
        self.bundle = self.stage / 'virgil'
        lib.stage(self.source, self.bundle, self.revision)
        for name in ('virgil-lsp', 'virgil-lsp-worker', 'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md'):
            (self.stage / name).write_text(name)
        self.manifest = {'virgilRevision': self.revision,
                         'library': lib.metadata(self.bundle, self.revision),
                         'sha256': {name: lib.sha((self.stage / name).read_bytes())
                                    for name in ('virgil-lsp', 'virgil-lsp-worker')}}
        self.archive = self.root / (self.stage.name + '.tar.gz')

    def pack(self):
        (self.stage / 'manifest.json').write_text(json.dumps(self.manifest))
        lib.archive(self.stage, self.archive, 1234)

    def check(self):
        self.pack()
        with contextlib.redirect_stdout(io.StringIO()):
            lib.check_archive(self.archive, self.revision)

    def test_unchanged_sources_and_license(self):
        self.check()
        for path in self.source.joinpath('lib').rglob('*'):
            if path.is_file():
                copied = self.bundle / 'lib' / path.relative_to(self.source / 'lib')
                self.assertEqual(path.read_bytes(), copied.read_bytes())
                self.assertEqual(copied.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.source / 'rt/LICENSE').read_bytes(), (self.bundle / 'LICENSE').read_bytes())

    def test_reproducible_archive(self):
        self.pack()
        original = self.archive.read_bytes()
        for path in self.stage.rglob('*'):
            import os
            os.utime(path, (99999, 99999))
        self.pack()
        self.assertEqual(original, self.archive.read_bytes())

    def test_digest_definition(self):
        files = self.manifest['library']['files']
        expected = lib.sha(''.join(f'{files[p]}  {p}\n' for p in sorted(files)).encode())
        self.assertEqual(expected, self.manifest['library']['treeSha256'])

    def test_changed_source(self):
        (self.bundle / 'lib/util/A.v3').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'files/checksums'):
            self.check()

    def test_missing_source(self):
        (self.bundle / 'lib/util/A.v3').unlink()
        with self.assertRaisesRegex(ValueError, 'files/checksums'):
            self.check()

    def test_extra_source(self):
        (self.bundle / 'lib/util/Extra.v3').write_text('extra')
        with self.assertRaisesRegex(ValueError, 'files/checksums'):
            self.check()

    def test_license_checksum(self):
        (self.bundle / 'LICENSE').write_text('wrong license')
        with self.assertRaisesRegex(ValueError, 'files/checksums'):
            self.check()

    def test_pinned_revision(self):
        self.pack()
        with self.assertRaisesRegex(ValueError, 'pinned Virgil revision'):
            lib.check_archive(self.archive, '0' * 40)

    def test_revision_file(self):
        (self.bundle / 'REVISION').write_text('0' * 40 + '\n')
        with self.assertRaisesRegex(ValueError, 'pinned Virgil revision'):
            self.check()

    def test_tree_digest(self):
        self.manifest['library']['treeSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'tree digest'):
            self.check()

    def test_no_other_upstream_directories(self):
        (self.bundle / 'rt').mkdir()
        (self.bundle / 'rt/foo').write_text('extra')
        self.manifest['library'] = lib.metadata(self.bundle, self.revision)
        with self.assertRaisesRegex(ValueError, 'outside lib'):
            self.check()

    def test_source_symlink(self):
        (self.source / 'lib/link').symlink_to('util/A.v3')
        with self.assertRaisesRegex(ValueError, 'unexpected bundle entry'):
            lib.stage(self.source, self.root / 'second', self.revision)

    def test_directory_symlink(self):
        (self.source / 'lib/link').symlink_to('util', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'unexpected bundle entry'):
            lib.stage(self.source, self.root / 'second', self.revision)

    def test_source_fifo(self):
        import os
        os.mkfifo(self.source / 'lib/pipe')
        with self.assertRaisesRegex(ValueError, 'unexpected bundle entry'):
            lib.stage(self.source, self.root / 'second', self.revision)

    def test_archive_symlink(self):
        (self.bundle / 'lib/util/A.v3').unlink()
        (self.bundle / 'lib/util/A.v3').symlink_to('B.v3')
        with self.assertRaisesRegex(ValueError, 'unexpected archive entry'):
            self.check()

    def test_archive_extra_directory(self):
        (self.bundle / 'bin').mkdir()
        with self.assertRaisesRegex(ValueError, 'unexpected directories'):
            self.check()


if __name__ == '__main__':
    unittest.main()
