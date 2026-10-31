#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Checks that scripts/release-assets.sh selects exactly the release assets."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/release-assets.sh'
VERSION = '0.1.0-alpha.6'
EXT = '0.1.5'

EXPECTED = sorted([
    f'virgil-lsp-{VERSION}-x86-64-linux.tar.gz',
    f'virgil-lsp-{VERSION}-x86-64-linux.manifest.json',
    f'virgil-lsp-{VERSION}-x86-64-darwin.tar.gz',
    f'virgil-lsp-{VERSION}-x86-64-darwin.manifest.json',
    f'virgil-lsp-vscode-{EXT}-linux-x64.vsix',
    f'virgil-lsp-vscode-{EXT}-darwin-arm64.vsix',
    f'virgil-lsp-vscode-{EXT}-darwin-x64.vsix',
    f'virgil-lsp-vscode-{EXT}.vsix',
    'SHA256SUMS',
])


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

    def test_rejects_bad_arguments(self):
        result = subprocess.run(
            ['bash', str(SCRIPT), VERSION], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Usage', result.stderr)


if __name__ == '__main__':
    unittest.main()
