#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Bundle integrity, entry types, and archive reproducibility without compiling."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('release_library', ROOT / 'scripts/release-library.py')
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)
PATTERNS = lib.runtime_patterns()
# One fixture runtime source for each of the supported targets' patterns, and
# a second one for the last pattern, so that a pattern still selects a file
# when one of its files is missing.
RUNTIME = [p.replace('*', 'Fixture') for p in PATTERNS] + [PATTERNS[-1].replace('*', 'Second')]


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
        for path in RUNTIME:
            (self.source / path).parent.mkdir(parents=True, exist_ok=True)
            (self.source / path).write_text(f'// {path}\ncomponent C {{}}\n')
        # Upstream rt/ files that no pattern selects.
        directory = self.source / RUNTIME[0].rsplit('/', 1)[0]
        for name in ('DEPS', 'Native.c', 'nested/Deep.v3', '.Hidden.v3'):
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_text(name)
        (self.source / 'rt/other-target').mkdir()
        (self.source / 'rt/other-target/Other.v3').write_text('component Other {}\n')
        self.revision = '7' * 40
        self.stage = self.root / 'virgil-lsp-test'
        self.stage.mkdir()
        self.bundle = self.stage / 'virgil'
        lib.stage(self.source, self.bundle, self.revision)
        # The runtime that the archive check expects, in place of the pin's.
        self.runtime = lib.runtime_digests(self.source, PATTERNS)
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
            lib.check_archive(self.archive, self.revision, self.runtime)

    def test_unchanged_sources_and_license(self):
        self.check()
        for path in self.source.joinpath('lib').rglob('*'):
            if path.is_file():
                copied = self.bundle / 'lib' / path.relative_to(self.source / 'lib')
                self.assertEqual(path.read_bytes(), copied.read_bytes())
                self.assertEqual(copied.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.source / 'rt/LICENSE').read_bytes(), (self.bundle / 'LICENSE').read_bytes())

    def test_runtime_sources(self):
        # Exactly the selected regular files, unchanged, at their rt/ paths.
        self.check()
        staged = sorted(p.relative_to(self.bundle).as_posix() for p in (self.bundle / 'rt').rglob('*')
                        if p.is_file())
        self.assertEqual(sorted(RUNTIME), staged)
        self.assertEqual(sorted(RUNTIME), sorted(self.runtime))
        for path in RUNTIME:
            self.assertEqual((self.source / path).read_bytes(), (self.bundle / path).read_bytes())
            self.assertEqual((self.bundle / path).stat().st_mode & 0o777, 0o644)
            self.assertEqual(lib.sha((self.source / path).read_bytes()),
                             self.manifest['library']['files'][path])

    def test_pinned_runtime_sources(self):
        # At the pin, the bundle holds the runtime files that the supported
        # targets select, found here with Path.glob, and no other rt/ file.
        virgil = ROOT / 'vendor/virgil'
        bundle = self.root / 'pinned'
        lib.stage(virgil, bundle, self.revision)
        expected = sorted({p.relative_to(virgil).as_posix() for pattern in PATTERNS
                           for p in virgil.glob(pattern) if p.is_file()})
        staged = sorted(p.relative_to(bundle).as_posix() for p in (bundle / 'rt').rglob('*') if p.is_file())
        self.assertEqual(expected, staged)
        files = lib.metadata(bundle, self.revision)['files']
        for path in staged:
            self.assertEqual(lib.sha((virgil / path).read_bytes()), files[path], path)
        self.assertEqual(['LICENSE', 'REVISION'],
                         sorted(p for p in files if not p.startswith(('lib/', 'rt/'))))
        if PATTERNS == ['rt/x86-64-linux/*.v3', 'rt/native/*.v3', 'rt/gc/*.v3']:
            # x86-64-linux alone: rt/x86-64-linux, rt/native, and rt/gc.
            self.assertEqual(12, len(staged), staged)

    def test_pinned_runtime(self):
        # The pin's runtime, read from the submodule's objects, is the
        # runtime staged from its working tree at the pin.
        virgil = ROOT / 'vendor/virgil'
        pin = lib.pinned_revision()
        head = subprocess.run(['git', '-C', str(virgil), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        if head.stdout.strip() != pin:
            self.skipTest(f'vendor/virgil is not at the pinned {pin}')
        pinned = lib.pinned_runtime(pin)
        self.assertEqual(lib.runtime_digests(virgil), pinned)
        bundle = self.root / 'pinned'
        lib.stage(virgil, bundle, pin)
        lib.compare_runtime(lib.runtime_digests(bundle), pinned, 'bundle')

    def git_virgil(self, files, links=()):
        """A Virgil repository with one commit of files and symlinks; its SHA."""
        virgil = self.root / 'git-virgil'
        for name, data in files.items():
            (virgil / name).parent.mkdir(parents=True, exist_ok=True)
            (virgil / name).write_text(data)
        for name, target in links:
            (virgil / name).parent.mkdir(parents=True, exist_ok=True)
            (virgil / name).symlink_to(target)
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
        for command in (['init', '-q'], ['add', '-A'],
                        ['-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                         'commit', '-q', '-m', 'fixture']):
            subprocess.run(['git', '-C', str(virgil), *command], check=True, env=env)
        return virgil, subprocess.run(['git', '-C', str(virgil), 'rev-parse', 'HEAD'], check=True,
                                      capture_output=True, text=True, env=env).stdout.strip()

    def test_pinned_runtime_from_objects(self):
        # Only the selected regular files of the commit, without hidden files,
        # whatever the working tree holds now.
        files = {'rt/a/A.v3': 'a', 'rt/a/B.v3': 'b', 'rt/a/DEPS': 'deps', 'rt/a/.Hidden.v3': 'hidden',
                 'rt/a/nested/C.v3': 'c', 'rt/b/D.v3': 'd', 'rt/other/E.v3': 'e'}
        virgil, revision = self.git_virgil(files)
        (virgil / 'rt/a/A.v3').write_text('changed in the working tree')
        (virgil / 'rt/b/D.v3').unlink()
        self.assertEqual({'rt/a/A.v3': lib.sha(b'a'), 'rt/a/B.v3': lib.sha(b'b'), 'rt/b/D.v3': lib.sha(b'd')},
                         lib.pinned_runtime(revision, ['rt/a/*.v3', 'rt/b/*.v3'], virgil))
        with self.assertRaisesRegex(ValueError, 'selects no files'):
            lib.pinned_runtime(revision, ['rt/a/*.v3', 'rt/c/*.v3'], virgil)

    def test_pinned_runtime_symlink(self):
        virgil, revision = self.git_virgil({'rt/a/A.v3': 'a'}, [('rt/a/Link.v3', 'A.v3')])
        with self.assertRaisesRegex(ValueError, 'not a regular file: rt/a/Link.v3'):
            lib.pinned_runtime(revision, ['rt/a/*.v3'], virgil)

    def test_pinned_runtime_unavailable(self):
        # A missing commit, a directory that isn't a repository, and an
        # uninitialized submodule directory inside the server's repository
        # are errors, never an empty runtime.
        virgil, _ = self.git_virgil({'rt/a/A.v3': 'a'})
        for directory, revision in ((virgil, '0' * 40), (self.root / 'source', '0' * 40),
                                    (ROOT / 'test/release', lib.pinned_revision())):
            with self.subTest(directory=directory):
                with self.assertRaisesRegex(ValueError, 'initialize the vendor/virgil submodule'):
                    lib.pinned_runtime(revision, ['rt/a/*.v3'], directory)

    def test_runtime_patterns(self):
        # Only the targets in AnalysisTargets.all count, each pattern once.
        table = self.root / 'AnalysisTargets.v3'
        table.write_text('component AnalysisTargets {\n'
                         '\tdef A = AnalysisTarget.new("a", "a",\n\t\t["rt/a/*.v3", "rt/native/*.v3"], "2m");\n'
                         '\tdef B = AnalysisTarget.new("b", "b", ["rt/b/*.v3", "rt/native/*.v3"], "1m");\n'
                         '\tdef C = AnalysisTarget.new("c", "c", ["rt/c/*.v3"], "1m");\n'
                         '\tdef all: Array<AnalysisTarget> = [A, B];\n}\n')
        self.assertEqual(['rt/a/*.v3', 'rt/native/*.v3', 'rt/b/*.v3'], lib.runtime_patterns(table))
        for bad in ('lib/util/*.v3', 'rt/*/X.v3', 'rt/../lib/*.v3', 'rt/X.v3'):
            with self.subTest(pattern=bad):
                table.write_text(f'def A = AnalysisTarget.new("a", "a", ["{bad}"], "2m");\n'
                                 'def all: Array<AnalysisTarget> = [A];\n')
                with self.assertRaisesRegex(ValueError, 'unsupported runtime pattern'):
                    lib.runtime_patterns(table)
        table.write_text('def all: Array<AnalysisTarget> = [A];\n')
        with self.assertRaisesRegex(ValueError, 'A has no AnalysisTarget.new entry'):
            lib.runtime_patterns(table)

    def test_runtime_pattern_comments(self):
        # Comments can't add a pattern or a target, nor replace the real
        # list of supported targets; strings may hold "/*" and "//".
        table = self.root / 'AnalysisTargets.v3'
        table.write_text('// def all: Array<AnalysisTarget> = [B];\n'
                         '/* def all: Array<AnalysisTarget> = [B]; */\n'
                         'def A = AnalysisTarget.new("a//b", "a", ["rt/a/*.v3" /*, "rt/x86-64-darwin/*.v3" */,\n'
                         '\t"rt/native/*.v3" // , "rt/gc/*.v3"\n\t], "2m");\n'
                         '// def B = AnalysisTarget.new("b", "b", ["rt/b/*.v3"], "1m");\n'
                         'def B = AnalysisTarget.new("b", "b", ["rt/b/*.v3"], "1m");\n'
                         'def all: Array<AnalysisTarget> = [A];\n')
        self.assertEqual(['rt/a/*.v3', 'rt/native/*.v3'], lib.runtime_patterns(table))

    def test_runtime_pattern_shapes(self):
        # Any other shape of a target or of the supported list is an error.
        good = 'def A = AnalysisTarget.new("a", "a", ["rt/a/*.v3"], "2m");\n'
        for text, error in (
                (good.replace('["rt/a/*.v3"]', 'PATTERNS'), 'is not `def NAME'),
                (good.replace('"2m");', '"2m")'), 'is not `def NAME'),
                (good + 'def C = AnalysisTarget.new("c", "c", ["rt/" + "c/*.v3"], "1m");\n', 'is not `def NAME'),
                (good, 'exactly one `def all'),
                (good + 'def all: Array<AnalysisTarget> = [A];\ndef all: Array<AnalysisTarget> = [A];\n',
                 'exactly one `def all'),
                (good + 'def all: Array<AnalysisTarget> = targets();\n', 'exactly one `def all'),
                (good + good + 'def all: Array<AnalysisTarget> = [A];\n', 'declared twice'),
                (good + 'def all: Array<AnalysisTarget> = [A]; /* unterminated\n', 'unterminated comment'),
                (good + 'def all: Array<AnalysisTarget> = [A];\ndef s = "unterminated\n', 'unterminated literal')):
            with self.subTest(text=text):
                table = self.root / 'AnalysisTargets.v3'
                table.write_text(text)
                with self.assertRaisesRegex(ValueError, error):
                    lib.runtime_patterns(table)

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

    def runtime_rejected(self, mutate):
        """A runtime mutation is refused both with the original manifest and
        with one regenerated from the mutated bundle."""
        mutate()
        with self.subTest(manifest='original'):
            with self.assertRaisesRegex(ValueError, 'files/checksums'):
                self.check()
        self.manifest['library'] = lib.metadata(self.bundle, self.revision)
        with self.subTest(manifest='regenerated'):
            with self.assertRaisesRegex(ValueError, 'differ from the pinned Virgil runtime'):
                self.check()

    def test_changed_runtime_source(self):
        self.runtime_rejected(lambda: (self.bundle / RUNTIME[0]).write_text('changed'))

    def test_missing_runtime_source(self):
        # The last pattern still selects its second file.
        self.runtime_rejected(lambda: (self.bundle / RUNTIME[-1]).unlink())

    def test_missing_pattern_sources(self):
        def remove():
            (self.bundle / RUNTIME[-1]).unlink()
            (self.bundle / RUNTIME[-2]).unlink()
        self.runtime_rejected(remove)

    def test_extra_runtime_source(self):
        # A file that the pattern would select, but that isn't the pin's.
        self.runtime_rejected(lambda: (self.bundle / RUNTIME[0]).with_name('Extra.v3').write_text('extra'))

    def test_unselected_runtime_file(self):
        # No rt/ file that the runtime patterns don't select, hidden ones
        # included, as the wrapper's shell glob skips them.
        directory = RUNTIME[0].rsplit('/', 1)[0]
        for path in (directory + '/DEPS', directory + '/nested/Deep.v3', directory + '/.Hidden.v3',
                     'rt/other-target/Other.v3'):
            with self.subTest(path=path):
                self.setUp()
                def add():
                    (self.bundle / path).parent.mkdir(parents=True, exist_ok=True)
                    (self.bundle / path).write_text('extra')
                self.runtime_rejected(add)

    def test_runtime_source_symlink(self):
        source = self.source / RUNTIME[0]
        source.unlink()
        source.symlink_to('DEPS')
        with self.assertRaisesRegex(ValueError, 'unexpected bundle entry'):
            lib.stage(self.source, self.root / 'second', self.revision)

    def test_runtime_directory_symlink(self):
        directory = self.source / RUNTIME[0].rsplit('/', 1)[0]
        directory.rename(self.source / 'moved')
        directory.symlink_to(self.source / 'moved', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'not a physical directory'):
            lib.stage(self.source, self.root / 'second', self.revision)

    def test_runtime_source_fifo(self):
        import os
        os.mkfifo((self.source / RUNTIME[0]).with_name('Pipe.v3'))
        with self.assertRaisesRegex(ValueError, 'unexpected bundle entry'):
            lib.stage(self.source, self.root / 'second', self.revision)

    def test_missing_runtime_directory(self):
        import shutil
        shutil.rmtree(self.source / RUNTIME[-1].rsplit('/', 1)[0])
        with self.assertRaises(FileNotFoundError):
            lib.stage(self.source, self.root / 'second', self.revision)

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
        (self.bundle / 'aeneas').mkdir()
        (self.bundle / 'aeneas/foo').write_text('extra')
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

    def test_archive_runtime_symlink(self):
        (self.bundle / RUNTIME[0]).unlink()
        (self.bundle / RUNTIME[0]).symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'unexpected archive entry'):
            self.check()

    def test_archive_extra_directory(self):
        (self.bundle / 'bin').mkdir()
        with self.assertRaisesRegex(ValueError, 'unexpected directories'):
            self.check()


if __name__ == '__main__':
    unittest.main()
