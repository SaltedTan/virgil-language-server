#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Checks that scripts/release-assets.sh selects exactly the release assets."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/release-assets.sh'
VERSION = '0.1.0-alpha.6'
EXT = '0.1.5'
# The JVM runtime's source archive, under the name that the pin file gives.
SOURCE = json.loads((ROOT / 'scripts/jvm-runtime.json').read_text())['source']['name']

# The assets of a release before the JVM build for Apple Silicon was added.
NATIVE = [
    f'virgil-lsp-{VERSION}-x86-64-linux.tar.gz',
    f'virgil-lsp-{VERSION}-x86-64-linux.manifest.json',
    f'virgil-lsp-{VERSION}-x86-64-darwin.tar.gz',
    f'virgil-lsp-{VERSION}-x86-64-darwin.manifest.json',
    f'virgil-lsp-vscode-{EXT}-linux-x64.vsix',
    f'virgil-lsp-vscode-{EXT}-darwin-arm64.vsix',
    f'virgil-lsp-vscode-{EXT}-darwin-x64.vsix',
    f'virgil-lsp-vscode-{EXT}.vsix',
    'SHA256SUMS',
]
# The JVM build's archive, manifest, and VSIX, and its runtime's source.
JVM = [
    f'virgil-lsp-{VERSION}-jvm-arm64-darwin.tar.gz',
    f'virgil-lsp-{VERSION}-jvm-arm64-darwin.manifest.json',
    f'virgil-lsp-vscode-{EXT}-darwin-arm64-jvm.vsix',
    SOURCE,
]
EXPECTED = sorted(NATIVE + JVM)


class ReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name) / 'assets'
        self.assets.mkdir()
        for name in EXPECTED:
            (self.assets / name).write_bytes(b'x')

    def run_script(self):
        return subprocess.run(
            ['bash', str(SCRIPT), VERSION, str(self.assets)],
            capture_output=True, text=True)

    def test_selects_every_expected_asset(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), EXPECTED)

    def test_rejects_a_missing_asset(self):
        (self.assets / 'SHA256SUMS').unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA256SUMS', result.stderr)

    def test_rejects_a_renamed_archive(self):
        (self.assets / f'virgil-lsp-{VERSION}-x86-64-linux.tar.gz').unlink()
        (self.assets / f'virgil-lsp-{VERSION}-x86-64-linux.tar.gz.old').write_bytes(b'x')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('x86-64-linux.tar.gz', result.stderr)

    def test_rejects_an_extra_asset(self):
        (self.assets / 'extra.txt').write_bytes(b'x')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('extra.txt', result.stderr)

    def test_rejects_a_hidden_file(self):
        (self.assets / '.extra').write_bytes(b'x')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('.extra', result.stderr)

    def test_rejects_a_subdirectory(self):
        (self.assets / 'nested').mkdir()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nested', result.stderr)

    def test_rejects_a_symlink(self):
        sums = self.assets / 'SHA256SUMS'
        sums.rename(self.assets.parent / 'SHA256SUMS')
        sums.symlink_to(self.assets.parent / 'SHA256SUMS')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA256SUMS', result.stderr)

    def test_rejects_a_name_joining_two_assets_with_a_newline(self):
        archive = f'virgil-lsp-{VERSION}-x86-64-linux.tar.gz'
        manifest = f'virgil-lsp-{VERSION}-x86-64-linux.manifest.json'
        (self.assets / archive).unlink()
        (self.assets / manifest).unlink()
        (self.assets / f'{archive}\n{manifest}').write_bytes(b'x')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_rejects_a_missing_vsix(self):
        (self.assets / f'virgil-lsp-vscode-{EXT}.vsix').unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('VSIX', result.stderr)

    def test_rejects_mismatched_extension_versions(self):
        (self.assets / f'virgil-lsp-vscode-{EXT}-linux-x64.vsix').rename(
            self.assets / f'virgil-lsp-vscode-{EXT}.1-linux-x64.vsix')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('extension version', result.stderr)

    def test_rejects_an_unexpected_vsix_target(self):
        (self.assets / f'virgil-lsp-vscode-{EXT}-darwin-x64.vsix').rename(
            self.assets / f'virgil-lsp-vscode-{EXT}-win32-x64.vsix')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_each_missing_asset(self):
        # Each asset is required: a JVM asset never stands in for a default
        # (Rosetta) one, nor a default one for a JVM one.
        for name in EXPECTED:
            with self.subTest(missing=name):
                data = (self.assets / name).read_bytes()
                (self.assets / name).unlink()
                result = self.run_script()
                (self.assets / name).write_bytes(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_rejects_a_release_without_the_jvm_assets(self):
        for name in JVM:
            (self.assets / name).unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_rejects_a_second_jvm_vsix(self):
        (self.assets / f'virgil-lsp-vscode-{EXT}-darwin-x64-jvm.vsix').write_bytes(b'x')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('expected 5 VSIX files', result.stderr)

    def test_rejects_a_jvm_archive_for_another_platform(self):
        for suffix in ('tar.gz', 'manifest.json'):
            (self.assets / f'virgil-lsp-{VERSION}-jvm-arm64-darwin.{suffix}').rename(
                self.assets / f'virgil-lsp-{VERSION}-jvm-x86-64-linux.{suffix}')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('jvm-x86-64-linux', result.stderr)

    def test_rejects_another_runtime_source_archive(self):
        (self.assets / SOURCE).rename(self.assets / 'OpenJDK21U-jdk-sources_21.0.12_7.tar.gz')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(SOURCE, result.stderr)

    def test_rejects_bad_arguments(self):
        result = subprocess.run(
            ['bash', str(SCRIPT), VERSION], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Usage', result.stderr)


if __name__ == '__main__':
    unittest.main()
