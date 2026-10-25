#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Regression checks for target metadata and archive-to-VSIX provenance."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_vsix', ROOT / 'scripts/check-vsix.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


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


if __name__ == '__main__':
    unittest.main()
