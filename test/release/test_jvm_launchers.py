#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Runs the JVM launchers that scripts/v3c.sh generates, without a JDK.

scripts/v3c.sh writes the launchers, as `make jvm` and
scripts/package-release.sh have it do, with a fake Virgil checkout, a fake
Aeneas that writes an empty jar, and a fake JDK whose java reports its home.
The packaged launchers then run from a moved directory whose path has
spaces, directly, through relative, absolute, and chained symlinks, and from
PATH, against a fake bundled runtime/bin/java that records what it was given.
With no bundled java, a launcher must fail rather than run a java from PATH.
"""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
V3C = ROOT / 'scripts/v3c.sh'
HEAP = '384m'
LOCALE = 'en_US.UTF-8'

# A fake Aeneas: it must not be given -heap-size, which v3c.sh turns into the
# launcher's -Xmx, and writes <output>/<program-name>.jar as an empty jar.
FAKE_AENEAS = '''#!/usr/bin/env python3
import sys, zipfile
args = dict(a.split("=", 1) for a in sys.argv[1:] if a.startswith("-") and "=" in a)
if "-heap-size" in args or args.get("-target") != "jar":
    sys.exit("fake Aeneas: unexpected arguments %r" % sys.argv[1:])
with zipfile.ZipFile(args["-output"] + "/" + args["-program-name"] + ".jar", "w"):
    pass
'''


def write_script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def generate(work, bundled=True):
    """Runs scripts/v3c.sh for the server and the worker into work/out and
    returns that directory. bundled selects the packaged launchers
    (JVM_BUNDLED_RUNTIME=runtime) rather than the development ones."""
    virgil = work / 'virgil'
    write_script(virgil / 'bin/dev/sense_host', '#!/bin/sh\necho none\n')
    aeneas = work / 'aeneas'
    write_script(aeneas, FAKE_AENEAS)
    rt = work / 'rt'
    rt.mkdir(exist_ok=True)
    (rt / 'V3S_System.class').write_bytes(b'\xca\xfe\xba\xbe')
    (rt / 'V3S_System$Pipe.class').write_bytes(b'\xca\xfe\xba\xbe')
    jdk = work / 'jdk'
    write_script(jdk / 'bin/java', '#!/bin/sh\necho "    java.home = %s" >&2\n' % jdk)
    out = work / 'out'
    out.mkdir(exist_ok=True)
    env = {'PATH': os.environ['PATH'], 'V3C_TARGET': 'jvm', 'VIRGIL': str(virgil),
           'VIRGIL_V3C': str(aeneas), 'JVM_RT': str(rt), 'JAVA': str(jdk / 'bin/java')}
    if bundled:
        env.update(JVM_BUNDLED_RUNTIME='runtime', JVM_LOCALE=LOCALE)
    for name, extra in (('virgil-lsp', []), ('virgil-lsp-worker', ['-heap-size=' + HEAP])):
        subprocess.run(['bash', str(V3C), *extra, '-output=' + str(out), '-program-name=' + name,
                        'src/main.v3'], env=env, check=True, capture_output=True, text=True)
    return out


def generated_launchers():
    """{name: bytes} of the packaged launchers that scripts/v3c.sh writes."""
    with tempfile.TemporaryDirectory() as temp:
        out = generate(Path(temp))
        return {name: (out / name).read_bytes() for name in ('virgil-lsp', 'virgil-lsp-worker')}


def development_launcher():
    """The development launcher of the server, which names its JDK and jar."""
    with tempfile.TemporaryDirectory() as temp:
        return (generate(Path(temp), bundled=False) / 'virgil-lsp').read_bytes()


class LauncherTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        out = generate(self.root / 'generate')
        # Install as a package would, in a directory other than the build's,
        # whose path has spaces.
        self.package = self.root / 'moved install' / 'virgil lsp'
        self.package.mkdir(parents=True)
        for name in ('virgil-lsp', 'virgil-lsp-worker', 'virgil-lsp.jar', 'virgil-lsp-worker.jar'):
            shutil.copy2(out / name, self.package / name)
        shutil.rmtree(self.root / 'generate')
        self.record = self.root / 'record'
        write_script(self.package / 'runtime/bin/java', self.recorder(self.record, 'bundled java'))
        self.sentinel = self.root / 'sentinel'
        write_script(self.root / 'system bin/java', self.recorder(self.sentinel, 'system java'))

    @staticmethod
    def recorder(record, label):
        """A java that records its path, environment, and arguments."""
        return ('#!/bin/sh\n'
                '{ printf "%%s\\n" "$0" "LC_ALL=$LC_ALL" "PATH=$PATH"; printf "arg:%%s\\n" "$@"; } > \'%s\'\n'
                'echo "%s ran"\n' % (record, label))

    def run_launcher(self, command, *args, env=None):
        return subprocess.run([str(command), *args], capture_output=True, text=True, timeout=30,
                              env={'PATH': '/usr/bin:/bin'} if env is None else env)

    def recorded(self):
        lines = self.record.read_text().splitlines()
        return {'java': lines[0], 'LC_ALL': lines[1].split('=', 1)[1], 'PATH': lines[2].split('=', 1)[1],
                'args': [line[4:] for line in lines[3:]]}

    def expect(self, result, name='virgil-lsp', forwarded=('--stdio',)):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'bundled java ran\n')
        record = self.recorded()
        self.assertEqual(record['java'], str(self.package / 'runtime/bin/java'))
        self.assertEqual(record['LC_ALL'], LOCALE)
        options = ['-Xss16m'] + (['-Xmx' + HEAP] if name == 'virgil-lsp-worker' else [])
        self.assertEqual(record['args'], options + [
            '-Dvirgil.lsp.executable=' + str(self.package / name),
            '-jar', str(self.package / (name + '.jar')), *forwarded])
        self.assertFalse(self.sentinel.exists(), 'a java on PATH ran')
        return record

    def test_generated_launchers_are_relocatable(self):
        for name in ('virgil-lsp', 'virgil-lsp-worker'):
            text = (self.package / name).read_text()
            self.assertIn('exec "$dir/runtime/bin/java" ', text)
            self.assertNotIn(str(self.root), text)
            self.assertNotIn("'/", text)
        self.assertNotIn('-Xmx', (self.package / 'virgil-lsp').read_text())

    def test_moved_directory_with_spaces(self):
        self.expect(self.run_launcher(self.package / 'virgil-lsp', '--stdio'))

    def test_forwarded_arguments(self):
        args = ('analyze', 'a file.v3', '', '--flag=x y', '*', '$HOME')
        self.expect(self.run_launcher(self.package / 'virgil-lsp', *args), forwarded=args)

    def test_worker_path_and_heap(self):
        self.expect(self.run_launcher(self.package / 'virgil-lsp-worker', 'x'), 'virgil-lsp-worker', ('x',))

    def test_empty_environment(self):
        # The server starts its worker with an empty environment.
        record = self.expect(self.run_launcher(self.package / 'virgil-lsp-worker', env={}),
                             'virgil-lsp-worker', ())
        # The launcher's fallback, or the shell's own default (dash has one).
        self.assertTrue({'/usr/bin', '/bin'} <= set(record['PATH'].split(':')), record['PATH'])

    def test_relative_symlink(self):
        bin_dir = self.root / 'bin dir'
        bin_dir.mkdir()
        (bin_dir / 'virgil-lsp').symlink_to(os.path.relpath(self.package / 'virgil-lsp', bin_dir))
        self.expect(self.run_launcher(bin_dir / 'virgil-lsp', '--stdio'))

    def test_absolute_symlink(self):
        bin_dir = self.root / 'bin dir'
        bin_dir.mkdir()
        (bin_dir / 'virgil-lsp').symlink_to(self.package / 'virgil-lsp')
        self.expect(self.run_launcher(bin_dir / 'virgil-lsp', '--stdio'))

    def test_chained_symlinks(self):
        # A relative symlink to an absolute one, in different directories.
        first = self.root / 'first'
        second = self.root / 'second dir'
        first.mkdir()
        second.mkdir()
        (second / 'lsp').symlink_to(self.package / 'virgil-lsp')
        (first / 'virgil-lsp').symlink_to('../second dir/lsp')
        self.expect(self.run_launcher(first / 'virgil-lsp', '--stdio'))

    def test_symlink_on_path(self):
        bin_dir = self.root / 'bin dir'
        bin_dir.mkdir()
        (bin_dir / 'virgil-lsp').symlink_to(os.path.relpath(self.package / 'virgil-lsp', bin_dir))
        self.expect(self.run_launcher('virgil-lsp', '--stdio',
                                      env={'PATH': '%s:/usr/bin:/bin' % bin_dir}))

    def test_relative_invocation(self):
        result = subprocess.run(['./virgil-lsp', '--stdio'], cwd=self.package, capture_output=True,
                                text=True, timeout=30, env={'PATH': '/usr/bin:/bin'})
        self.expect(result)

    def test_missing_bundled_java_does_not_use_path(self):
        (self.package / 'runtime/bin/java').unlink()
        for name in ('virgil-lsp', 'virgil-lsp-worker'):
            with self.subTest(name=name):
                result = self.run_launcher(self.package / name, '--stdio',
                                           env={'PATH': '%s:/usr/bin:/bin' % (self.root / 'system bin')})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertIn('runtime/bin/java', result.stderr)
                self.assertFalse(self.sentinel.exists(), 'the java on PATH ran')
                self.assertFalse(self.record.exists())

    def test_development_launcher_names_its_jdk_and_jar(self):
        text = development_launcher().decode()
        self.assertRegex(text, r"exec '/[^']*/jdk/bin/java' -Xss16m ")
        self.assertRegex(text, r"-jar '/[^']*/out/virgil-lsp.jar' ")

    def test_jar_holds_the_runtime_classes_with_a_fixed_date(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temp:
            out = generate(Path(temp))
            with zipfile.ZipFile(out / 'virgil-lsp.jar') as jar:
                infos = {i.filename: i for i in jar.infolist()}
        self.assertEqual(sorted(infos), ['V3S_System$Pipe.class', 'V3S_System.class'])
        for info in infos.values():
            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
            self.assertEqual(stat.S_IMODE(info.external_attr >> 16), 0o644)


if __name__ == '__main__':
    unittest.main()
