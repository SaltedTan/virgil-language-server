#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Checks of the JVM release archive and its bundled runtime, without a JDK.

The fixtures stand in for the jlinked Temurin runtime with files that have the
real layout and binary headers, so that a wrong architecture or version, a
missing launcher or notice, or a changed native layout fails the checks that
scripts/check-release.sh --archive and scripts/check-vsix.py run. The checks
require the runtime that scripts/jvm-runtime.json pins, so most tests pin the
fixture runtime in its place (pin_runtime), and pack() always writes a
manifest generated from the runtime as packed, as packaging does: a mutated
runtime must fail against the pin, not only against a stale manifest. Other
tests check the real pin, and the source archive. The launchers are the ones
that scripts/v3c.sh generates (test_jvm_launchers.py runs them).
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lib = load('release_library', 'scripts/release-library.py')
# The module that release-library.py and vsix-payload.py check with.
runtime = lib.jvm_runtime()
REAL_PIN = runtime.pin
PIN = runtime.pin()
launchers = load('jvm_launchers', 'test/release/test_jvm_launchers.py')
GENERATED = launchers.generated_launchers()
REVISION = '7' * 40
ARM64 = b'\xcf\xfa\xed\xfe' + (0x0100000C).to_bytes(4, 'little') + bytes(12)
X86_64_MACHO = b'\xcf\xfa\xed\xfe' + (0x01000007).to_bytes(4, 'little') + bytes(12)
X86_64_ELF = b'\x7fELF' + bytes(14) + (0x3E).to_bytes(2, 'little')


def release_file(**changes):
    values = {'IMPLEMENTOR': PIN['vendor'],
              'IMPLEMENTOR_VERSION': PIN['distribution'] + '-' + PIN['version'],
              'JAVA_RUNTIME_VERSION': PIN['runtimeVersion'],
              'JAVA_VERSION': PIN['javaVersion'], 'MODULES': ' '.join(PIN['modules']),
              'OS_ARCH': 'aarch64', 'OS_NAME': 'Darwin'}
    values.update(changes)
    return ''.join(f'{k}="{v}"\n' for k, v in sorted(values.items())).encode()


def runtime_fixture(binary=ARM64):
    """{path: (bytes, mode)} shaped like the jlinked darwin-arm64 runtime."""
    files = {name: (binary + name.encode(), 0o755) for name in sorted(runtime.EXECUTABLES)}
    for name in ('lib/server/libjvm.dylib', 'lib/libjli.dylib', 'lib/libjava.dylib'):
        files[name] = (binary + name.encode(), 0o644)
    for name in ('lib/modules', 'lib/jvm.cfg', 'conf/security/java.security',
                 'legal/java.base/LICENSE', 'legal/java.base/ASSEMBLY_EXCEPTION',
                 'legal/java.base/ADDITIONAL_LICENSE_INFO', 'legal/java.base/zlib.md'):
        files[name] = (name.encode(), 0o644)
    files['release'] = (release_file(), 0o644)
    return files


def launcher(name):
    """The packaged launcher that scripts/v3c.sh generates."""
    return GENERATED[name]


def pin_runtime(test, files, platform='arm64-darwin', **changes):
    """Makes the pin file's runtime for platform the given files, {path: (bytes,
    mode)}, for the rest of a test, with changes to that entry."""
    pinned = copy.deepcopy(PIN)
    pinned['runtimes'][platform] = {**runtime.inventory(files), **changes}
    runtime.pin = lambda: copy.deepcopy(pinned)
    test.addCleanup(setattr, runtime, 'pin', REAL_PIN)


def pin_source(test, path):
    """Makes the pin file's source archive the file at path, by name, URL, and SHA-256."""
    pinned = copy.deepcopy(PIN)
    pinned['source'].update(name=path.name, url=path.as_uri(),
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    runtime.pin = lambda: copy.deepcopy(pinned)
    test.addCleanup(setattr, runtime, 'pin', REAL_PIN)


class JvmArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        (source / 'lib/util').mkdir(parents=True)
        (source / 'rt').mkdir()
        (source / 'lib/util/A.v3').write_bytes(b'class A {}\n')
        (source / 'rt/LICENSE').write_bytes(b'Apache License, Version 2.0\n')
        self.stage = self.root / 'virgil-lsp-1.2.3-jvm-arm64-darwin'
        self.stage.mkdir()
        lib.stage(source, self.stage / 'virgil', REVISION)
        self.files = {'LICENSE': b'license', 'README.md': b'readme', 'THIRD_PARTY_NOTICES.md': b'notices',
                      'virgil-lsp': launcher('virgil-lsp'), 'virgil-lsp-worker': launcher('virgil-lsp-worker'),
                      'virgil-lsp.jar': b'server jar', 'virgil-lsp-worker.jar': b'worker jar'}
        self.modes = {}
        self.runtime = runtime_fixture()
        self.manifest = None
        pin_runtime(self, runtime_fixture())

    def metadata(self):
        digests = {n: runtime.sha(d) for n, (d, _) in self.runtime.items()}
        host = PIN['jdks']['linux-x64']
        return {**runtime.metadata_from_pin('arm64-darwin'),
                'jlink': {'name': host['name'], 'sha256': host['sha256'], 'options': runtime.JLINK_OPTIONS},
                'files': digests, 'treeSha256': runtime.tree_digest(digests)}

    def pack(self, target='jvm', change=None):
        # Each pack starts from the staged library alone, as packaging stages it.
        self.stage.chmod(0o755)
        for path in self.stage.iterdir():
            if path.name == 'runtime':
                shutil.rmtree(path)
            elif path.name != 'virgil':
                path.unlink()
        for name, data in self.files.items():
            path = self.stage / name
            path.write_bytes(data)
            path.chmod(self.modes.get(name, 0o755 if name in ('virgil-lsp', 'virgil-lsp-worker') else 0o644))
        if self.runtime is not None:
            for name, (data, mode) in self.runtime.items():
                path = self.stage / 'runtime' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(mode)
            for path in [self.stage / 'runtime', *(self.stage / 'runtime').rglob('*')]:
                if path.is_dir():
                    path.chmod(0o755)
        manifest = {'target': target, 'virgilRevision': REVISION,
                    'library': lib.metadata(self.stage / 'virgil', REVISION),
                    'sha256': {n: lib.sha(self.files[n]) for n in
                               ('virgil-lsp', 'virgil-lsp-worker', 'virgil-lsp.jar', 'virgil-lsp-worker.jar')
                               if n in self.files}}
        if target == 'jvm':
            manifest['platform'] = 'arm64-darwin'
            manifest['runtime'] = self.metadata()
        if change:
            change(manifest)
        (self.stage / 'manifest.json').write_text(json.dumps(manifest))
        (self.stage / 'manifest.json').chmod(0o644)
        archive = self.root / (self.stage.name + '.tar.gz')
        lib.archive(self.stage, archive, 1234)
        return archive

    def check(self, **options):
        archive = self.pack(**options)
        with contextlib.redirect_stdout(io.StringIO()):
            lib.check_archive(archive, REVISION)

    def rejects(self, pattern, **options):
        with self.assertRaisesRegex((ValueError, KeyError), pattern):
            self.check(**options)

    def test_good_archive(self):
        self.check()

    def test_pin_is_one_temurin_release(self):
        self.assertEqual(PIN['distribution'], 'Temurin')
        self.assertEqual(PIN['modules'], ['java.base'])
        self.assertTrue(PIN['version'].startswith(PIN['javaVersion'] + '+'))
        self.assertTrue(PIN['javaVersion'].startswith('21.'))
        for jdk in PIN['jdks'].values():
            self.assertRegex(jdk['sha256'], '^[0-9a-f]{64}$')
            self.assertIn(PIN['version'].replace('+', '%2B'), jdk['url'])
            self.assertIn(PIN['javaVersion'] + '_' + PIN['version'].split('+')[1], jdk['name'])

    def test_wrong_architecture(self):
        for binary in (X86_64_MACHO, X86_64_ELF, b'\xca\xfe\xba\xbe' + bytes(16)):
            with self.subTest(binary=binary[:8]):
                self.runtime = runtime_fixture(binary)
                self.rejects('not a Mach-O binary for arm64-darwin')

    def test_one_x86_64_library(self):
        self.runtime['lib/libjava.dylib'] = (X86_64_MACHO, 0o644)
        self.rejects('libjava.dylib is not a Mach-O binary')

    def test_wrong_version(self):
        for key, value in (('JAVA_VERSION', '21.0.12'), ('IMPLEMENTOR_VERSION', 'Temurin-21.0.12+7'),
                           ('IMPLEMENTOR', 'Oracle Corporation'), ('OS_ARCH', 'x86_64'),
                           ('MODULES', 'java.base java.logging')):
            with self.subTest(key=key):
                self.runtime = runtime_fixture()
                self.runtime['release'] = (release_file(**{key: value}), 0o644)
                self.rejects(f'release file has {key}=')

    def test_manifest_disagrees_with_the_pin(self):
        for key, value in (('version', '21.0.12+7'), ('vendor', 'Oracle'), ('arch', 'x86_64'),
                           ('jdk', {'name': 'other.tar.gz', 'sha256': '0' * 64}),
                           ('source', {'name': PIN['source']['name'], 'sha256': '0' * 64})):
            with self.subTest(key=key):
                self.rejects(f'manifest runtime {key} is', change=lambda m: m['runtime'].update({key: value}))
        self.rejects('unpinned JDK', change=lambda m: m['runtime']['jlink'].update(sha256='0' * 64))

    def test_missing_launcher(self):
        del self.files['virgil-lsp-worker']
        self.rejects('virgil-lsp-worker')

    def test_development_launcher(self):
        self.files['virgil-lsp'] = (b"#!/bin/sh\nexec '/usr/lib/jvm/bin/java' -Xss16m "
                                    b"-jar '/build/virgil-lsp.jar' \"$@\"\n")
        self.rejects('not a relocatable launcher')

    def test_generated_development_launcher(self):
        # The launcher that `make jvm` writes, from the same generator.
        self.files['virgil-lsp'] = launchers.development_launcher()
        self.rejects('not a relocatable launcher')

    def test_worker_launcher_in_place_of_the_server(self):
        self.files['virgil-lsp'] = launcher('virgil-lsp-worker')
        self.rejects('virgil-lsp is not a relocatable launcher')

    def test_missing_jar(self):
        del self.files['virgil-lsp.jar']
        self.rejects('virgil-lsp.jar checksum')

    def test_changed_jar(self):
        archive = self.pack()
        self.files['virgil-lsp-worker.jar'] = b'changed'
        manifest = json.loads((self.stage / 'manifest.json').read_text())
        (self.stage / 'virgil-lsp-worker.jar').write_bytes(b'changed')
        (self.stage / 'manifest.json').write_text(json.dumps(manifest))
        lib.archive(self.stage, archive, 1234)
        with self.assertRaisesRegex(ValueError, 'virgil-lsp-worker.jar checksum'):
            lib.check_archive(archive, REVISION)

    def test_missing_legal_file(self):
        for name in ('legal/java.base/LICENSE', 'legal/java.base/ASSEMBLY_EXCEPTION',
                     'legal/java.base/ADDITIONAL_LICENSE_INFO'):
            with self.subTest(name=name):
                self.runtime = runtime_fixture()
                del self.runtime[name]
                self.rejects('runtime lacks ' + name)

    def test_missing_runtime_executable(self):
        for name in ('bin/java', 'lib/jspawnhelper', 'lib/server/libjvm.dylib', 'release'):
            with self.subTest(name=name):
                self.runtime = runtime_fixture()
                del self.runtime[name]
                self.rejects('runtime lacks')

    def test_another_module(self):
        self.runtime['legal/java.logging/LICENSE'] = (b'license', 0o644)
        self.rejects('notices for another module')

    def test_jdk_directories(self):
        for name in ('include/jni.h', 'jmods/java.base.jmod', 'man/man1/java.1'):
            with self.subTest(name=name):
                self.runtime = runtime_fixture()
                self.runtime[name] = (b'x', 0o644)
                self.rejects('unexpected runtime file')

    def test_runtime_modes(self):
        for name, mode in (('bin/java', 0o644), ('lib/server/libjvm.dylib', 0o755),
                           ('legal/java.base/LICENSE', 0o600)):
            with self.subTest(name=name):
                self.runtime = runtime_fixture()
                self.runtime[name] = (self.runtime[name][0], mode)
                self.rejects(f'runtime file {name} has mode')

    def test_package_modes(self):
        for name, mode in (('virgil-lsp', 0o644), ('virgil-lsp.jar', 0o755), ('README.md', 0o600)):
            with self.subTest(name=name):
                self.modes = {name: mode}
                self.rejects(f'{name} has mode')

    def test_changed_runtime_file(self):
        # Bytes that the pin allows, but the manifest doesn't record.
        archive = self.pack()
        (self.stage / 'runtime/lib/modules').write_bytes(b'changed')
        changed = {**self.runtime, 'lib/modules': (b'changed', 0o644)}
        pin_runtime(self, changed)
        lib.archive(self.stage, archive, 1234)
        with self.assertRaisesRegex(ValueError, 'runtime files/checksums'):
            lib.check_archive(archive, REVISION)

    def test_changed_runtime_file_against_the_pin(self):
        archive = self.pack()
        (self.stage / 'runtime/lib/modules').write_bytes(b'changed')
        lib.archive(self.stage, archive, 1234)
        with self.assertRaisesRegex(ValueError, 'differs from the pinned runtime: lib/modules$'):
            lib.check_archive(archive, REVISION)

    # The review's cases (#464): each mutation is packed with a manifest
    # regenerated from the mutated runtime, as a packaging regression would
    # produce, and must still fail, naming the file.
    def test_omission_with_a_regenerated_manifest(self):
        for name in ('lib/libjli.dylib', 'lib/libjava.dylib', 'legal/java.base/zlib.md',
                     'lib/modules', 'conf/security/java.security'):
            with self.subTest(name=name):
                self.runtime = runtime_fixture()
                del self.runtime[name]
                self.rejects('runtime lacks ' + name + '$')

    def test_replacement_with_a_regenerated_manifest(self):
        for name, data, pattern in (
                ('lib/libjava.dylib', b'not a binary', 'lib/libjava.dylib is not a native binary'),
                ('lib/libjli.dylib', b'', 'lib/libjli.dylib is not a native binary'),
                ('bin/keytool', b'#!/bin/sh\n', 'bin/keytool is not a native binary'),
                ('lib/libjava.dylib', ARM64 + b'other', 'differs from the pinned runtime: lib/libjava.dylib$'),
                ('legal/java.base/zlib.md', b'changed', 'differs from the pinned runtime: legal/java.base/zlib.md$'),
                ('lib/modules', b'changed', 'differs from the pinned runtime: lib/modules$')):
            with self.subTest(name=name, data=data[:8]):
                self.runtime = runtime_fixture()
                self.runtime[name] = (data, self.runtime[name][1])
                self.rejects(pattern)

    def test_extra_file_with_a_regenerated_manifest(self):
        for name in ('lib/libextra.dylib', 'conf/extra.properties', 'legal/java.base/extra.md'):
            with self.subTest(name=name):
                self.runtime = runtime_fixture()
                data = ARM64 if name.endswith('.dylib') else b'extra'
                self.runtime[name] = (data, 0o644)
                self.rejects('unexpected runtime file: ' + name + '$')

    def test_unpinned_native_path_must_be_native(self):
        # A library that the pin also lists must hold a binary, before any
        # comparison with the pin.
        self.runtime['lib/libnew.dylib'] = (b'text', 0o644)
        pin_runtime(self, self.runtime)
        self.rejects('lib/libnew.dylib is not a native binary')

    def test_pinned_tree_digest_and_file_count(self):
        for change in ({'treeSha256': '0' * 64}, {'fileCount': len(self.runtime) + 1}):
            with self.subTest(change=change):
                pin_runtime(self, runtime_fixture(), **change)
                self.rejects('tree digest or file count differs from the pinned runtime')

    def test_platform_without_a_pinned_runtime(self):
        pinned = copy.deepcopy(PIN)
        del pinned['runtimes']['arm64-darwin']
        runtime.pin = lambda: copy.deepcopy(pinned)
        self.rejects('pins no runtime for arm64-darwin')

    def test_fixture_fails_against_the_real_pin(self):
        # The checks don't take the manifest's word for the runtime.
        runtime.pin = REAL_PIN
        self.rejects('runtime lacks ')

    def test_runtime_tree_digest(self):
        self.rejects('runtime tree digest', change=lambda m: m['runtime'].update(treeSha256='0' * 64))

    def test_wrong_platform_or_name(self):
        self.rejects('unknown JVM platform', change=lambda m: m.update(platform='arm64-linux'))
        self.rejects('does not end with -jvm-x86-64-linux', change=lambda m: m.update(platform='x86-64-linux'))

    def test_native_archive_layout_is_unchanged(self):
        # A native manifest must not admit the JVM package's files.
        self.runtime = None
        del self.files['virgil-lsp.jar'], self.files['virgil-lsp-worker.jar']
        self.check(target='x86-64-darwin')
        self.files['virgil-lsp.jar'] = b'server jar'
        self.rejects('expected release layout', target='x86-64-darwin')
        del self.files['virgil-lsp.jar']
        self.runtime = runtime_fixture()
        self.rejects('expected release layout', target='x86-64-darwin')


class RealPinTest(unittest.TestCase):
    """The pin file itself: complete, consistent runtimes and the source archive."""

    def test_runtimes_are_consistent(self):
        for platform in runtime.PLATFORMS:
            with self.subTest(platform=platform):
                pinned = PIN['runtimes'][platform]
                self.assertEqual(pinned['fileCount'], len(pinned['files']))
                self.assertEqual(pinned['treeSha256'], runtime.tree_digest(pinned['files']))
                for digest in pinned['files'].values():
                    self.assertRegex(digest, '^[0-9a-f]{64}$')

    def test_runtimes_are_complete(self):
        legal = {'legal/java.base/' + n for n in (
            'ADDITIONAL_LICENSE_INFO', 'ASSEMBLY_EXCEPTION', 'LICENSE', 'aes.md', 'asm.md',
            'c-libutl.md', 'cldr.md', 'icu.md', 'public_suffix.md', 'siphash.md', 'unicode.md', 'zlib.md')}
        libraries = {'java', 'jimage', 'jli', 'jsig', 'net', 'nio', 'syslookup', 'verify', 'zip'}
        for platform, suffix, extra in (('arm64-darwin', 'dylib', {'lib/libosxsecurity.dylib'}),
                                        ('x86-64-linux', 'so', {'legal/java.base/gcc.md', 'lib/jexec'})):
            with self.subTest(platform=platform):
                files = set(PIN['runtimes'][platform]['files'])
                want = legal | extra | runtime.REQUIRED | runtime.EXECUTABLES
                want |= {f'lib/lib{n}.{suffix}' for n in libraries}
                want |= {f'lib/server/libjvm.{suffix}', f'lib/server/libjsig.{suffix}'}
                self.assertEqual(want - files, set())
                self.assertEqual({n for n in files if n.startswith('legal/')}, legal | {
                    n for n in extra if n.startswith('legal/')})
                natives = runtime.native_paths(platform, files)
                self.assertTrue({f'lib/libjli.{suffix}', 'bin/java', 'bin/keytool', 'lib/jspawnhelper',
                                 f'lib/server/libjvm.{suffix}'} <= natives)
                self.assertFalse({n for n in natives if not n.startswith(('bin/', 'lib/'))})

    def test_source_archive_is_the_pinned_build(self):
        source = PIN['source']
        build = PIN['javaVersion'] + '_' + PIN['version'].split('+')[1]
        self.assertEqual(source['name'], f'OpenJDK21U-jdk-sources_{build}.tar.gz')
        self.assertRegex(source['sha256'], '^[0-9a-f]{64}$')
        self.assertEqual(source['scmRef'], 'jdk-' + PIN['version'] + '_adopt')
        release = 'https://github.com/adoptium/temurin21-binaries/releases/'
        self.assertEqual(source['url'], release + 'download/jdk-' + PIN['version'].replace('+', '%2B')
                         + '/' + source['name'])
        self.assertEqual(source['release'], release + 'tag/jdk-' + PIN['version'].replace('+', '%2B'))
        self.assertEqual(runtime.metadata_from_pin('arm64-darwin')['source'],
                         {'name': source['name'], 'sha256': source['sha256']})


class SourceArchiveTest(unittest.TestCase):
    """Fetching and checking the runtime's source archive, offline."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.upstream = self.root / 'upstream' / PIN['source']['name']
        self.upstream.parent.mkdir()
        self.upstream.write_bytes(b'corresponding source')
        self.cache = self.root / 'cache'

    def test_fetch_copy_and_check(self):
        pin_source(self, self.upstream)
        copied = runtime.copy_source(self.cache, self.root / 'assets')
        self.assertEqual(copied, self.root / 'assets' / PIN['source']['name'])
        self.assertEqual(copied.read_bytes(), b'corresponding source')
        self.assertEqual(copied.stat().st_mode & 0o777, 0o644)
        self.assertTrue((self.cache / PIN['source']['name']).is_file())
        runtime.check_source(copied)

    def test_wrong_cached_archive_fails(self):
        pin_source(self, self.upstream)
        self.cache.mkdir()
        (self.cache / PIN['source']['name']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'not the pinned'):
            runtime.copy_source(self.cache, self.root / 'assets')
        self.assertFalse((self.root / 'assets' / PIN['source']['name']).exists())

    def test_failed_download_fails(self):
        pin_source(self, self.upstream)
        self.upstream.unlink()
        with self.assertRaises(subprocess.CalledProcessError):
            runtime.copy_source(self.cache, self.root / 'assets')
        self.assertFalse((self.cache / PIN['source']['name']).exists())

    def test_downloaded_wrong_bytes_fail(self):
        pin_source(self, self.upstream)
        self.upstream.write_bytes(b'replaced upstream')
        with self.assertRaisesRegex(ValueError, 'not the pinned'):
            runtime.copy_source(self.cache, self.root / 'assets')

    def test_check_source_requires_name_and_hash(self):
        other = self.root / 'renamed.tar.gz'
        other.write_bytes(b'corresponding source')
        pin_source(self, self.upstream)
        with self.assertRaisesRegex(ValueError, 'not the pinned source archive'):
            runtime.check_source(other)
        self.upstream.write_bytes(b'other bytes')
        with self.assertRaisesRegex(ValueError, 'not the pinned'):
            runtime.check_source(self.upstream)

    def test_check_release_runtime_source(self):
        # Against the real pin: a file of the pinned name with other bytes fails.
        fake = self.root / PIN['source']['name']
        fake.write_bytes(b'not the source')
        result = subprocess.run([str(ROOT / 'scripts/check-release.sh'), '--runtime-source', str(fake)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('not the pinned ' + PIN['source']['sha256'], result.stderr)
        self.assertEqual(result.stdout, '')


class RuntimeFunctionsTest(unittest.TestCase):
    def test_binary_formats(self):
        self.assertEqual(runtime.binary_format(ARM64), ('Mach-O', 0x0100000C))
        self.assertEqual(runtime.binary_format(X86_64_ELF), ('ELF', 0x3E))
        self.assertEqual(runtime.binary_format(b'\xca\xfe\xba\xbe' + bytes(16)), ('Mach-O', None))
        self.assertIsNone(runtime.binary_format(b'PK\x03\x04' + bytes(16)))

    def test_release_values(self):
        self.assertEqual(runtime.read_release('A="1"\nB="two words"\nC=bare\n'),
                         {'A': '1', 'B': 'two words'})

    def test_jlink_avoids_native_stripping(self):
        # --strip-debug runs strip-native-debug-symbols, which fails on Mach-O
        # files when the host is Linux; CDS needs the target's java.
        self.assertNotIn('--strip-debug', runtime.JLINK_OPTIONS)
        self.assertNotIn('--generate-cds-archive', runtime.JLINK_OPTIONS)


if __name__ == '__main__':
    unittest.main()
