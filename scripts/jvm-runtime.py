#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Fetch the pinned Temurin JDKs, link the bundled Java runtime, and check it.

scripts/jvm-runtime.json is the one place that pins the runtime: the Temurin
release, its version strings, the modules, each JDK archive's URL and
SHA-256, Adoptium's source archive for that build, and, for each platform,
the linked runtime itself: the SHA-256 of every file, the file count, and the
tree digest. The JVM release archive bundles a runtime that jlink links from
the target platform's JDK modules (its jmods/) with the host's JDK of the same
release, so a Linux host can link the macOS arm64 runtime; the result is the
same, byte for byte, on either host. jlink copies the native files from the
modules unchanged, so Temurin's own signatures on them are kept. Packaging
and the archive and VSIX checks require exactly the pinned runtime, so a
change to the Temurin release or the jlink options must update the pinned
runtimes too (pin-runtime prints them). See docs/releasing.md.

Usage:
  jvm-runtime.py prepare <platform> <work> [--cache <dir>]
      Fetch and verify the JDK archives (into --cache, default
      $JVM_JDK_CACHE or build/jdk-cache), extract them in <work>, link the
      runtime into <work>/runtime, check it against the pin, and print the
      host JDK's java.
  jvm-runtime.py source <output-dir> [--cache <dir>]
      Fetch and verify the runtime's source archive, copy it into
      <output-dir>, and print its path.
  jvm-runtime.py check <platform> <runtime-dir> <manifest.json>
      Check an extracted runtime against the pin and the manifest's runtime.
  jvm-runtime.py check-source <file>
      Check that a file is the pinned source archive, by name and SHA-256.
  jvm-runtime.py pin-runtime <platform> <work> [--cache <dir>]
      Link the runtime as prepare does, without comparing it with the pinned
      runtime, and print the JSON for its "runtimes" entry in the pin file.

Platforms: arm64-darwin, the release target, and x86-64-linux, which links a
runtime for this Linux host so that the packaged launchers and layout can be
smoke-tested here; it is not a release asset.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform as host_platform
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PIN_FILE = ROOT / 'scripts/jvm-runtime.json'

# The jlink options. --strip-debug would also run the strip-native-debug-symbols
# plugin, which only handles the host's own binary format, so only the Java
# debug attributes are stripped. A class-data sharing archive
# (--generate-cds-archive) needs the target's java to run, so it isn't made.
JLINK_OPTIONS = ['--strip-java-debug-attributes', '--no-header-files', '--no-man-pages',
                 '--compress=zip-9']
# Keys copied from the target JDK's release file into the runtime's, which
# jlink otherwise reduces to JAVA_VERSION and MODULES. jlink writes each value
# as given, so they are quoted as in the JDK's file.
RELEASE_KEYS = ('IMPLEMENTOR', 'IMPLEMENTOR_VERSION', 'JAVA_RUNTIME_VERSION', 'OS_NAME', 'OS_ARCH')

# Each platform: the JDK whose modules it links, the binary format and CPU of
# its native files, the release file's OS_NAME and OS_ARCH, and its JVM library.
PLATFORMS = {
    'arm64-darwin': {'jdk': 'mac-aarch64', 'binary': ('Mach-O', 0x0100000C),
                     'os': 'Darwin', 'arch': 'aarch64', 'libjvm': 'lib/server/libjvm.dylib'},
    'x86-64-linux': {'jdk': 'linux-x64', 'binary': ('ELF', 0x3E),
                     'os': 'Linux', 'arch': 'x86_64', 'libjvm': 'lib/server/libjvm.so'},
}
# The host JDK that runs jlink, javac, and Aeneas's jar output, by uname.
HOSTS = {('Linux', 'x86_64'): 'linux-x64', ('Darwin', 'arm64'): 'mac-aarch64'}
# The only runtime files that are executable.
EXECUTABLES = {'bin/java', 'bin/keytool', 'lib/jspawnhelper'}
# Files that every runtime must hold, besides its JVM library.
REQUIRED = {'release', 'bin/java', 'lib/jspawnhelper', 'lib/modules', 'lib/jvm.cfg',
            'conf/security/java.security', 'legal/java.base/LICENSE',
            'legal/java.base/ASSEMBLY_EXCEPTION', 'legal/java.base/ADDITIONAL_LICENSE_INFO'}
# Top-level runtime directories; a JDK's include/, jmods/, and man/ are not.
TOP = {'bin', 'conf', 'legal', 'lib', 'release'}
# The runtime paths that hold native code, for the platform's library suffix:
# each one in the pinned runtime must be a binary of the platform's format.
NATIVE = ('bin/*', 'lib/jspawnhelper', 'lib/jexec', 'lib/*.{so}', 'lib/server/*.{so}')
SUFFIXES = {'arm64-darwin': 'dylib', 'x86-64-linux': 'so'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def pin():
    with open(PIN_FILE, encoding='utf-8') as f:
        return json.load(f)


def host_jdk():
    key = HOSTS.get((host_platform.system(), host_platform.machine()))
    if key is None:
        raise ValueError('no pinned JDK runs on this host (%s %s); see scripts/jvm-runtime.json'
                         % (host_platform.system(), host_platform.machine()))
    return key


def fetch(cache, key):
    """Returns the path of a pinned JDK archive in cache, downloading it if
    needed, after checking its SHA-256."""
    return fetch_entry(cache, pin()['jdks'][key])


def fetch_entry(cache, entry):
    """Returns the path of a pinned download ({name, url, sha256}) in cache,
    downloading it if needed, after checking its SHA-256. A wrong file fails."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / entry['name']
    if not path.exists():
        part = cache / (entry['name'] + '.part')
        print('downloading ' + entry['url'], file=sys.stderr)
        subprocess.run(['curl', '-fsSL', '--retry', '3', '-o', str(part), entry['url']], check=True)
        part.rename(path)
    digest = file_sha(path)
    if digest != entry['sha256']:
        raise ValueError('%s has SHA-256 %s, not the pinned %s' % (path, digest, entry['sha256']))
    return path


def copy_source(cache, output):
    """Copies the verified source archive into output and returns its path."""
    entry = pin()['source']
    source = fetch_entry(cache, entry)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / entry['name']
    shutil.copyfile(source, destination)
    destination.chmod(0o644)
    check_source(destination)
    return destination


def check_source(path):
    """Checks that a file is the pinned source archive, by name and SHA-256."""
    entry = pin()['source']
    if Path(path).name != entry['name']:
        raise ValueError('%s is not the pinned source archive %s' % (path, entry['name']))
    digest = file_sha(path)
    if digest != entry['sha256']:
        raise ValueError('%s has SHA-256 %s, not the pinned %s' % (path, digest, entry['sha256']))


def extract(cache, key, work):
    """Extracts a verified JDK archive into work/<key> and returns its home."""
    jdk = pin()['jdks'][key]
    destination = work / key
    if not destination.exists():
        archive = fetch(cache, key)
        destination.mkdir(parents=True)
        subprocess.run(['tar', '-xzf', str(archive), '-C', str(destination)], check=True)
    return destination / jdk['home']


def read_release(text):
    """Parses a release file's KEY="value" lines."""
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        if sep and len(value) >= 2 and value[0] == value[-1] == '"':
            values[key] = value[1:-1]
    return values


def link(platform, work, cache, check_pinned=True):
    """Links the runtime for platform into work/runtime and returns the host
    JDK's home. Unless check_pinned is false, the runtime must be the pinned one."""
    spec = PLATFORMS[platform]
    host = extract(cache, host_jdk(), work)
    target = extract(cache, spec['jdk'], work)
    values = read_release((target / 'release').read_text(encoding='utf-8'))
    for key in RELEASE_KEYS:
        if key not in values or ':' in values[key] or '"' in values[key]:
            raise ValueError('%s/release has no usable %s' % (target, key))
    output = work / 'runtime'
    if output.exists():
        shutil.rmtree(output)
    temp = work / 'jlink-tmp'
    temp.mkdir(exist_ok=True)
    pinned = pin()
    subprocess.run([str(host / 'bin/jlink'), '-J-Djava.io.tmpdir=' + str(temp),
                    '--module-path', str(target / 'jmods'),
                    '--add-modules', ','.join(pinned['modules']), *JLINK_OPTIONS,
                    '--release-info', 'add:' + ':'.join('%s="%s"' % (k, values[k]) for k in RELEASE_KEYS),
                    '--output', str(output)], check=True)
    # jlink copies the native files from the modules; they must be Temurin's bytes.
    for path in sorted(output.rglob('*')):
        relative = path.relative_to(output).as_posix()
        if path.is_file() and binary_format(path.read_bytes()[:20]) is not None:
            original = target / relative
            if not original.is_file() or file_sha(original) != file_sha(path):
                raise ValueError('linked native file differs from the JDK: ' + relative)
    normalize(output)
    files = runtime_files(output)
    check_files(platform, files, check_pinned)
    return host


def normalize(runtime):
    """Sets 0755 on directories and the executables, and 0644 on other files."""
    for path in [runtime, *runtime.rglob('*')]:
        relative = path.relative_to(runtime).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            path.chmod(0o755)
        elif stat.S_ISREG(mode):
            path.chmod(0o755 if relative in EXECUTABLES else 0o644)
        else:
            raise ValueError('linked runtime holds a non-regular file: ' + relative)


def runtime_files(runtime):
    """Returns {relative path: (bytes, mode)} for a runtime directory's files."""
    files = {}
    for path in sorted(runtime.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            files[path.relative_to(runtime).as_posix()] = (path.read_bytes(), mode & 0o777)
        elif not stat.S_ISDIR(mode):
            raise ValueError('runtime holds a non-regular file: ' + path.relative_to(runtime).as_posix())
    return files


def binary_format(head):
    """Returns (format, CPU type) of a native binary's header, or None."""
    if head[:4] == b'\xcf\xfa\xed\xfe' and len(head) >= 8:
        return 'Mach-O', int.from_bytes(head[4:8], 'little')
    if head[:4] in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xce',
                    b'\xfe\xed\xfa\xcf'):
        return 'Mach-O', None  # universal, 32-bit, or big-endian: never the pinned target
    if head[:4] == b'\x7fELF' and len(head) >= 20:
        return 'ELF', int.from_bytes(head[18:20], 'little')
    return None


def tree_digest(files):
    lines = ''.join('%s  %s\n' % (files[p], p) for p in sorted(files))
    return sha(lines.encode())


def native_paths(platform, names):
    """The paths among names that hold native code on platform."""
    patterns = [p.replace('{so}', SUFFIXES[platform]) for p in NATIVE]
    return {n for n in names if any(PurePosixPath(n).match(p) and n.count('/') == p.count('/')
                                    for p in patterns)}


def pinned_runtime(platform):
    """The pinned runtime of platform: {treeSha256, fileCount, files}."""
    runtime = pin().get('runtimes', {}).get(platform)
    if not runtime:
        raise ValueError('scripts/jvm-runtime.json pins no runtime for ' + platform)
    return runtime


def inventory(files):
    """The pin file's runtimes entry for a runtime's files."""
    digests = {name: sha(data) for name, (data, _) in sorted(files.items())}
    return {'treeSha256': tree_digest(digests), 'fileCount': len(digests), 'files': digests}


def metadata(platform, runtime):
    """The manifest's runtime object for a linked runtime directory."""
    host = pin()['jdks'][host_jdk()]
    files = {name: sha(data) for name, (data, _) in runtime_files(runtime).items()}
    return {**metadata_from_pin(platform),
            'jlink': {'name': host['name'], 'sha256': host['sha256'], 'options': JLINK_OPTIONS},
            'files': files, 'treeSha256': tree_digest(files)}


def check_files(platform, files, check_pinned=True):
    """Checks a runtime's files, {path: (bytes, mode)}, against the pin and platform:
    layout, modes, native binary formats, release file, legal notices, and,
    unless check_pinned is false, the pinned runtime, file by file."""
    pinned = pin()
    spec = PLATFORMS[platform]
    expected = pinned_runtime(platform)['files'] if check_pinned else {}
    names = set(files)
    for name in names:
        parts = PurePosixPath(name).parts
        if parts[0] not in TOP or (parts[0] == 'release') != (len(parts) == 1):
            raise ValueError('unexpected runtime file: ' + name)
        if parts[0] == 'legal' and (len(parts) < 3 or parts[1] not in pinned['modules']):
            raise ValueError('runtime has notices for another module: ' + name)
    missing = sorted((REQUIRED | {spec['libjvm']}) - names)
    if missing:
        raise ValueError('runtime lacks ' + ', '.join(missing))
    for name, (data, mode) in files.items():
        want = 0o755 if name in EXECUTABLES else 0o644
        if mode != want:
            raise ValueError('runtime file %s has mode %o, not %o' % (name, mode, want))
        found = binary_format(data[:20])
        if found is not None and found != spec['binary']:
            raise ValueError('runtime file %s is not a %s binary for %s' % (name, spec['binary'][0], platform))
    # Every native path of the pinned runtime, and of this one, must hold a
    # binary, whatever its contents: a missing header is not a pass.
    for name in sorted(native_paths(platform, names)):
        if binary_format(files[name][0][:20]) is None:
            raise ValueError('runtime file %s is not a native binary' % name)
    values = read_release(files['release'][0].decode('utf-8'))
    release = {'JAVA_VERSION': pinned['javaVersion'], 'MODULES': ' '.join(pinned['modules']),
                'IMPLEMENTOR': pinned['vendor'],
                'IMPLEMENTOR_VERSION': pinned['distribution'] + '-' + pinned['version'],
                'JAVA_RUNTIME_VERSION': pinned['runtimeVersion'],
                'OS_NAME': spec['os'], 'OS_ARCH': spec['arch']}
    for key, value in release.items():
        if values.get(key) != value:
            raise ValueError('runtime release file has %s=%r, not %r' % (key, values.get(key), value))
    if not check_pinned:
        return
    # The runtime is reproducible, so it must be exactly the pinned one,
    # whatever the manifest says.
    missing = sorted(set(expected) - names)
    if missing:
        raise ValueError('runtime lacks ' + ', '.join(missing))
    extra = sorted(names - set(expected))
    if extra:
        raise ValueError('unexpected runtime file: ' + ', '.join(extra))
    changed = sorted(n for n in names if sha(files[n][0]) != expected[n])
    if changed:
        raise ValueError('runtime file differs from the pinned runtime: ' + ', '.join(changed))
    runtime = pinned_runtime(platform)
    digests = {n: sha(d) for n, (d, _) in files.items()}
    if tree_digest(digests) != runtime['treeSha256'] or len(digests) != runtime['fileCount']:
        raise ValueError('runtime tree digest or file count differs from the pinned runtime')


def check(platform, files, runtime_manifest):
    """Checks a runtime's files and the manifest's runtime object against the pin."""
    check_files(platform, files)
    pinned = pin()
    expected = metadata_from_pin(platform)
    for key, value in expected.items():
        if runtime_manifest.get(key) != value:
            raise ValueError('manifest runtime %s is %r, not the pinned %r'
                             % (key, runtime_manifest.get(key), value))
    if runtime_manifest.get('jlink', {}).get('sha256') not in {j['sha256'] for j in pinned['jdks'].values()}:
        raise ValueError('manifest runtime was linked by an unpinned JDK')
    digests = {name: sha(data) for name, (data, _) in files.items()}
    if digests != runtime_manifest.get('files'):
        raise ValueError('runtime files/checksums do not match the manifest')
    if tree_digest(digests) != runtime_manifest.get('treeSha256'):
        raise ValueError('runtime tree digest does not match the manifest')


def metadata_from_pin(platform):
    """The manifest runtime fields that follow from the pin alone."""
    pinned = pin()
    spec = PLATFORMS[platform]
    jdk = pinned['jdks'][spec['jdk']]
    return {'backend': 'jvm', 'vendor': pinned['vendor'], 'distribution': pinned['distribution'],
            'version': pinned['version'], 'javaVersion': pinned['javaVersion'],
            'os': spec['os'], 'arch': spec['arch'], 'modules': pinned['modules'],
            'jdk': {'name': jdk['name'], 'sha256': jdk['sha256']},
            'source': {'name': pinned['source']['name'], 'sha256': pinned['source']['sha256']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    cache = Path(os.environ.get('JVM_JDK_CACHE') or ROOT / 'build/jdk-cache')
    for command in ('prepare', 'pin-runtime'):
        p = sub.add_parser(command)
        p.add_argument('platform', choices=sorted(PLATFORMS))
        p.add_argument('work', type=Path)
        p.add_argument('--cache', type=Path, default=cache)
    p = sub.add_parser('source')
    p.add_argument('output', type=Path)
    p.add_argument('--cache', type=Path, default=cache)
    p = sub.add_parser('check-source')
    p.add_argument('file', type=Path)
    p = sub.add_parser('check')
    p.add_argument('platform', choices=sorted(PLATFORMS))
    p.add_argument('runtime', type=Path)
    p.add_argument('manifest', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            # The source archive is fetched first, so packaging fails before
            # building anything if it can't be had.
            fetch_entry(args.cache.resolve(), pin()['source'])
            host = link(args.platform, args.work.resolve(), args.cache.resolve())
            print(host / 'bin/java')
        elif args.command == 'pin-runtime':
            work = args.work.resolve()
            link(args.platform, work, args.cache.resolve(), check_pinned=False)
            print(json.dumps(inventory(runtime_files(work / 'runtime')), indent=2))
        elif args.command == 'source':
            print(copy_source(args.cache.resolve(), args.output.resolve()))
        elif args.command == 'check-source':
            check_source(args.file)
            print('%s: the pinned %s source archive' % (args.file.name, pin()['version']))
        else:
            manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
            check(args.platform, runtime_files(args.runtime), manifest['runtime'])
            print('%s: %s runtime verified' % (args.runtime, args.platform))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print('jvm-runtime.py: error: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
