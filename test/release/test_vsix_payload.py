#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Regression checks for target metadata and archive-to-VSIX provenance."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_vsix', ROOT / 'scripts/check-vsix.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
# The JVM runtime fixtures of test_jvm_package.py.
spec = importlib.util.spec_from_file_location('jvm_package', Path(__file__).with_name('test_jvm_package.py'))
jvm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jvm)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, target='linux-x64'):
        files = {n: (n.encode(), 0o644) for n in (
            'LICENSE', 'THIRD_PARTY_NOTICES.md', 'virgil/LICENSE', 'virgil/REVISION',
            'virgil/lib/util/StringBuilder.v3')}
        files.update({n: (n.encode(), 0o755) for n in ('virgil-lsp', 'virgil-lsp-worker')})
        manifest = {'target': 'x86-64-linux' if target == 'linux-x64' else 'x86-64-darwin',
                    'sha256': {n: hashlib.sha256(files[n][0]).hexdigest()
                               for n in ('virgil-lsp', 'virgil-lsp-worker')}}
        files['manifest.json'] = (json.dumps(manifest).encode(), 0o644)
        archive = self.root / 'server.tar.gz'
        with tarfile.open(archive, 'w:gz') as t:
            for name, (data, mode) in files.items():
                member = tarfile.TarInfo('release/' + name)
                member.size, member.mode = len(data), mode
                t.addfile(member, io.BytesIO(data))
        entries = {'extension/server/' + n: (d, m) for n, (d, m) in files.items()}
        entries['extension/server-target.json'] = (json.dumps({'target': target}).encode(), 0o644)
        entries['extension.vsixmanifest'] = (self.xml(target), 0o644)
        return archive, entries

    def jvm_fixture(self, runtime=None):
        """A jvm-arm64-darwin archive and the entries of its darwin-arm64-jvm VSIX.
        The standard fixture runtime is pinned in place of the real one; the
        archive's manifest is generated from the runtime given."""
        jvm.pin_runtime(self, jvm.runtime_fixture())
        files = {n: (n.encode(), 0o644) for n in (
            'LICENSE', 'THIRD_PARTY_NOTICES.md', 'virgil/LICENSE', 'virgil/REVISION',
            'virgil/lib/util/StringBuilder.v3', 'virgil-lsp.jar', 'virgil-lsp-worker.jar')}
        files.update({n: (jvm.launcher(n), 0o755) for n in ('virgil-lsp', 'virgil-lsp-worker')})
        runtime = jvm.runtime_fixture() if runtime is None else runtime
        files.update({'runtime/' + n: v for n, v in runtime.items()})
        digests = {n: hashlib.sha256(d).hexdigest() for n, (d, _) in runtime.items()}
        host = jvm.PIN['jdks']['linux-x64']
        manifest = {'target': 'jvm', 'platform': 'arm64-darwin',
                    'runtime': {**jvm.runtime.metadata_from_pin('arm64-darwin'),
                                'jlink': {'name': host['name'], 'sha256': host['sha256']},
                                'files': digests, 'treeSha256': jvm.runtime.tree_digest(digests)},
                    'sha256': {n: hashlib.sha256(files[n][0]).hexdigest() for n in (
                        'virgil-lsp', 'virgil-lsp-worker', 'virgil-lsp.jar', 'virgil-lsp-worker.jar')}}
        files['manifest.json'] = (json.dumps(manifest).encode(), 0o644)
        archive = self.root / 'jvm.tar.gz'
        with tarfile.open(archive, 'w:gz') as t:
            for name, (data, mode) in files.items():
                member = tarfile.TarInfo('release/' + name)
                member.size, member.mode = len(data), mode
                t.addfile(member, io.BytesIO(data))
        entries = {'extension/server/' + n: (d, m) for n, (d, m) in files.items()}
        entries['extension/server-target.json'] = (
            json.dumps({'target': 'darwin-arm64', 'backend': 'jvm'}).encode(), 0o644)
        entries['extension.vsixmanifest'] = (self.xml('darwin-arm64'), 0o644)
        return archive, entries

    @staticmethod
    def xml(target):
        attribute = '' if target is None else f' TargetPlatform="{target}"'
        return (f'<PackageManifest><Metadata><Identity{attribute}/><Properties>'
                '<Property Id="Microsoft.VisualStudio.Code.PreRelease" Value="true"/>'
                '</Properties></Metadata></PackageManifest>').encode()

    def check(self, entries, target, archive):
        vsix = self.root / 'extension.vsix'
        with zipfile.ZipFile(vsix, 'w') as z:
            for name, (data, mode) in entries.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFREG | mode) << 16
                z.writestr(info, data)
        check.check_payload(vsix, target, archive)

    def test_each_target_and_universal(self):
        for target in ('linux-x64', 'darwin-arm64', 'darwin-x64'):
            with self.subTest(target=target):
                archive, entries = self.fixture(target)
                self.check(entries, target, archive)
        self.check({'extension.vsixmanifest': (self.xml(None), 0o644)}, 'universal', None)

    def test_mutated_payload_and_metadata_are_rejected(self):
        for mutation in ('server', 'worker', 'library', 'notice', 'manifest', 'mode',
                         'missing', 'extra', 'target', 'marker', 'prerelease', 'universal'):
            with self.subTest(mutation=mutation):
                archive, entries = self.fixture()
                changed = {'server': 'virgil-lsp', 'worker': 'virgil-lsp-worker',
                           'library': 'virgil/lib/util/StringBuilder.v3',
                           'notice': 'THIRD_PARTY_NOTICES.md', 'manifest': 'manifest.json'}
                if mutation in changed:
                    name = 'extension/server/' + changed[mutation]
                    entries[name] = (b'changed', entries[name][1])
                elif mutation == 'mode':
                    entries['extension/server/virgil-lsp'] = (b'virgil-lsp', 0o644)
                elif mutation == 'missing':
                    del entries['extension/server/virgil/LICENSE']
                elif mutation == 'extra':
                    entries['extension/server/unexpected'] = (b'', 0o644)
                elif mutation == 'target':
                    entries['extension.vsixmanifest'] = (self.xml('darwin-x64'), 0o644)
                elif mutation == 'marker':
                    entries['extension/server-target.json'] = (b'{"target":"darwin-arm64"}', 0o644)
                elif mutation == 'prerelease':
                    entries['extension.vsixmanifest'] = (self.xml('linux-x64').replace(b'true', b'false'), 0o644)
                elif mutation == 'universal':
                    entries['extension.vsixmanifest'] = (self.xml(None), 0o644)
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    self.check(entries, 'universal' if mutation == 'universal' else 'linux-x64', archive)

    def test_jvm_target(self):
        archive, entries = self.jvm_fixture()
        self.check(entries, 'darwin-arm64-jvm', archive)
        self.assertEqual(check.ROOT, str(ROOT))

    def test_jvm_payload_and_metadata_mutations_are_rejected(self):
        java = 'extension/server/runtime/bin/java'
        for mutation in ('java', 'java-mode', 'legal', 'jar', 'extra', 'marker', 'marker-target',
                         'vsix-target', 'default-target'):
            with self.subTest(mutation=mutation):
                archive, entries = self.jvm_fixture()
                target = 'darwin-arm64-jvm'
                if mutation == 'java':
                    entries[java] = (jvm.X86_64_MACHO, 0o755)
                elif mutation == 'java-mode':
                    entries[java] = (entries[java][0], 0o644)
                elif mutation == 'legal':
                    del entries['extension/server/runtime/legal/java.base/LICENSE']
                elif mutation == 'jar':
                    entries['extension/server/virgil-lsp.jar'] = (b'changed', 0o644)
                elif mutation == 'extra':
                    entries['extension/server/runtime/lib/extra.dylib'] = (b'', 0o644)
                elif mutation == 'marker':
                    entries['extension/server-target.json'] = (b'{"target": "darwin-arm64"}', 0o644)
                elif mutation == 'marker-target':
                    entries['extension/server-target.json'] = (
                        b'{"target": "darwin-arm64-jvm", "backend": "jvm"}', 0o644)
                elif mutation == 'vsix-target':
                    entries['extension.vsixmanifest'] = (self.xml('darwin-arm64-jvm'), 0o644)
                elif mutation == 'default-target':
                    # The JVM payload can't pass as the default darwin-arm64 VSIX.
                    target = 'darwin-arm64'
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    self.check(entries, target, archive)

    def test_jvm_archive_runtime_is_checked(self):
        # A wrong-architecture or wrong-version runtime in the archive fails
        # before any VSIX bytes are compared.
        for runtime in ({**jvm.runtime_fixture(), 'bin/java': (jvm.X86_64_ELF, 0o755)},
                        {**jvm.runtime_fixture(), 'release': (jvm.release_file(JAVA_VERSION='21.0.12'), 0o644)}):
            archive, entries = self.jvm_fixture(runtime)
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.check(entries, 'darwin-arm64-jvm', archive)

    def test_incomplete_runtime_with_a_regenerated_manifest_is_rejected(self):
        # The review's case (#464): a runtime missing a library or notice, or
        # with a text file for a library, whose manifest was generated from it.
        for name, change in (('lib/libjli.dylib', None), ('legal/java.base/zlib.md', None),
                             ('lib/libjava.dylib', b'not a binary')):
            with self.subTest(name=name):
                runtime = jvm.runtime_fixture()
                if change is None:
                    del runtime[name]
                else:
                    runtime[name] = (change, 0o644)
                archive, entries = self.jvm_fixture(runtime)
                err = io.StringIO()
                with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(err):
                    self.check(entries, 'darwin-arm64-jvm', archive)
                self.assertIn(name, str(raised.exception))

    def test_native_archive_is_not_a_jvm_payload(self):
        archive, entries = self.fixture('darwin-arm64')
        entries['extension/server-target.json'] = (
            json.dumps({'target': 'darwin-arm64', 'backend': 'jvm'}).encode(), 0o644)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.check(entries, 'darwin-arm64-jvm', archive)

    def test_wrong_archive_target_and_symlink_are_rejected(self):
        archive, entries = self.fixture('darwin-arm64')
        entries['extension.vsixmanifest'] = (self.xml('linux-x64'), 0o644)
        entries['extension/server-target.json'] = (b'{"target":"linux-x64"}', 0o644)
        with self.assertRaises(SystemExit):
            self.check(entries, 'linux-x64', archive)
        with tarfile.open(archive, 'w:gz') as t:
            link = tarfile.TarInfo('release/virgil-lsp')
            link.type, link.linkname = tarfile.SYMTYPE, '/external/server'
            t.addfile(link)
        with self.assertRaises(SystemExit):
            self.check(entries, 'linux-x64', archive)

    def test_payload_failure_fails_release_runner(self):
        shim = self.root / 'python3'
        shim.write_text(
            '#!/bin/sh\n'
            f'if [ "$1" = {shlex.quote(str(Path(__file__).resolve()))} ]; then\n'
            '  echo "injected payload failure" >&2\n'
            '  exit 1\n'
            'fi\n'
            f'exec {shlex.quote(sys.executable)} "$@"\n')
        shim.chmod(0o755)
        result = subprocess.run(
            ['bash', str(ROOT / 'test/release/run.sh')],
            env={**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH']},
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL: VSIX payload integrity and provenance', result.stdout)
        self.assertIn('injected payload failure', result.stdout)
        self.assertRegex(result.stdout, r'release scripts: \d+ passed, 1 failed\n$')


if __name__ == '__main__':
    unittest.main()
