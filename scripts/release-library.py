#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Stage/check the pinned source bundle and write normalized release archives."""

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    """Regular files only; reject links, devices, and ambiguous digest paths."""
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"not a physical directory: {root}")
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        relative = path.relative_to(root).as_posix()
        if '\n' in relative or '\r' in relative or '\\' in relative:
            raise ValueError(f"unsupported bundle path: {relative!r}")
        if stat.S_ISREG(mode):
            result[relative] = sha(path.read_bytes())
        elif not stat.S_ISDIR(mode):
            raise ValueError(f"unexpected bundle entry (regular files/directories only): {path}")
    return result


def metadata(root, revision):
    files = inventory(root)
    lines = ''.join(f'{digest}  {path}\n' for path, digest in sorted(files.items()))
    return {'revision': revision, 'treeSha256': sha(lines.encode()), 'files': files}


ROOT = Path(__file__).resolve().parents[1]
# The supported-target table, whose runtime patterns select the rt/ sources
# that the bundle holds beside lib/ (ADR-0018, decision 7).
TARGETS_SOURCE = ROOT / 'src/analysis/AnalysisTargets.v3'


def strip_comments(text, source):
    """text with each // and /* */ comment replaced by a space, leaving string
    and character literals, which may hold "//" or "/*", unchanged."""
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in '"\'':
            j = i + 1
            while j < len(text) and text[j] != c and text[j] != '\n':
                j += 2 if text[j] == '\\' else 1
            if j >= len(text) or text[j] != c:
                raise ValueError(f'unterminated literal in {source}')
            result.append(text[i:j + 1])
            i = j + 1
        elif text.startswith('//', i):
            j = text.find('\n', i)
            i = len(text) if j < 0 else j
            result.append(' ')
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            if j < 0:
                raise ValueError(f'unterminated comment in {source}')
            i = j + 2
            result.append(' ')
        else:
            result.append(c)
            i += 1
    return ''.join(result)


STRING = r'"([^"\\\n]*)"'
# The two declaration shapes of the table, outside comments.
TARGET_DEF = re.compile(r'\bdef\s+([A-Z][A-Z0-9_]*)\s*=\s*AnalysisTarget\.new\(\s*' + STRING + r'\s*,\s*'
                        + STRING + r'\s*,\s*\[\s*("[^"\\\n]*"(?:\s*,\s*"[^"\\\n]*")*)\s*\]\s*,\s*'
                        + STRING + r'\s*\)\s*;')
ALL_DEF = re.compile(r'\bdef\s+all\s*:\s*Array<AnalysisTarget>\s*=\s*\[\s*([A-Z][A-Z0-9_]*(?:\s*,\s*[A-Z][A-Z0-9_]*)*)'
                     r'\s*\]\s*;')


def runtime_patterns(source=TARGETS_SOURCE):
    """The runtime patterns of the targets in AnalysisTargets.all, in order and
    without duplicates, read from the Virgil table itself. Comments don't
    count, and a declaration of another shape is an error rather than skipped."""
    text = strip_comments(Path(source).read_text(encoding='utf-8'), source)
    entries = {}
    for match in TARGET_DEF.finditer(text):
        if match.group(1) in entries:
            raise ValueError(f'target {match.group(1)} is declared twice in {source}')
        entries[match.group(1)] = re.findall(STRING, match.group(4))
    if len(entries) != len(re.findall(r'\bAnalysisTarget\.new\b', text)):
        raise ValueError(f'an AnalysisTarget.new entry in {source} is not `def NAME = AnalysisTarget.new('
                         f'"name", "aeneas", ["pattern", ...], "stack");`')
    supported = ALL_DEF.findall(text)
    if len(supported) != 1 or len(re.findall(r'\bdef\s+all\b', text)) != 1:
        raise ValueError(f'{source} needs exactly one `def all: Array<AnalysisTarget> = [NAME, ...];`')
    patterns = []
    for name in re.findall(r'\w+', supported[0]):
        if name not in entries:
            raise ValueError(f'supported target {name} has no AnalysisTarget.new entry in {source}')
        for pattern in entries[name]:
            parts = pattern.split('/')
            if (len(parts) != 3 or parts[0] != 'rt' or parts[1] in ('', '.', '..')
                    or any(c in parts[1] for c in '*?[') or parts[2] != '*.v3'):
                raise ValueError(f'unsupported runtime pattern in {source}: {pattern!r}')
            if pattern not in patterns:
                patterns.append(pattern)
    if not patterns:
        raise ValueError(f'the supported targets in {source} have no runtime patterns')
    return patterns


def runtime_source(name):
    return name.endswith('.v3') and not name.startswith('.')


def check_runtime_checkout(source, patterns):
    """Reject nonregular runtime files and symlinked runtime directories, as for lib/."""
    root = source / 'rt'
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError(f'not a physical directory: {root}')
    for pattern in patterns:
        path = source / pattern.rsplit('/', 1)[0]
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError(f'not a physical directory: {path}')
        matched = sorted(p for p in path.iterdir() if runtime_source(p.name))
        if not matched:
            raise ValueError(f'runtime pattern {pattern} selects no files in {source}')
        for entry in matched:
            if not stat.S_ISREG(entry.lstat().st_mode):
                raise ValueError(f'unexpected bundle entry (regular files only): {entry}')


def pinned_revision():
    """The Virgil revision that this checkout pins, its vendor/virgil gitlink."""
    result = subprocess.run(['git', '--no-replace-objects', '-C', str(ROOT), 'rev-parse', 'HEAD:vendor/virgil'],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f'cannot read the vendor/virgil gitlink: {result.stderr.strip()}')
    return result.stdout.strip()


def runtime_blobs(virgil, revision, patterns):
    """Read the runtime sources selected by patterns from the given Git revision."""
    def git(*args, data=None):
        result = subprocess.run(['git', '--no-replace-objects', '-C', str(virgil), *args],
                                input=data, capture_output=True)
        if result.returncode != 0:
            raise ValueError(f'cannot read the runtime sources of Virgil {revision} from {virgil}; '
                             f'initialize the vendor/virgil submodule or set VIRGIL to a checkout with the revision: '
                             f'{result.stderr.decode(errors="replace").strip()}')
        return result.stdout

    # An uninitialized submodule directory belongs to the server's repository.
    top = git('rev-parse', '--show-toplevel').decode().strip()
    if Path(top).resolve() != Path(virgil).resolve():
        raise ValueError(f'{virgil} is not an initialized Virgil checkout; '
                         f'initialize the vendor/virgil submodule or set VIRGIL to a Virgil checkout')
    objects = {}
    selected = set()
    for record in git('ls-tree', '-r', '-t', '-z', '--full-tree', revision + '^{commit}', '--', 'rt').split(b'\0'):
        if not record:
            continue
        meta, path = record.split(b'\t', 1)
        mode, _, oid = meta.decode().split()
        path = path.decode()
        directory, _, name = path.rpartition('/')
        pattern = directory + '/*.v3'
        if pattern in patterns and runtime_source(name):
            if mode not in ('100644', '100755'):
                raise ValueError(f'pinned runtime entry is not a regular file: {path}')
            objects[path] = oid
            selected.add(pattern)
    for pattern in patterns:
        if pattern not in selected:
            raise ValueError(f'runtime pattern {pattern} selects no files in Virgil {revision}')
    out = git('cat-file', '--batch', data=''.join(oid + '\n' for oid in objects.values()).encode())
    result = {}
    for path, oid in objects.items():
        header, out = out.split(b'\n', 1)
        size = int(header.split()[2])
        if header.split()[0].decode() != oid:
            raise ValueError(f'unexpected git cat-file output for {path}')
        result[path] = out[:size]
        out = out[size + 1:]
    return result


def virgil_checkout():
    return ROOT / (os.environ.get('VIRGIL') or 'vendor/virgil')


def pinned_runtime(revision):
    return {path: sha(data) for path, data in runtime_blobs(virgil_checkout(), revision, runtime_patterns()).items()}


def compare_runtime(actual, expected, what):
    """Refuse a runtime map, path to SHA-256, that isn't exactly the expected one."""
    problems = [f'missing {p}' for p in sorted(expected.keys() - actual.keys())]
    problems += [f'changed {p}' for p in sorted(expected.keys() & actual.keys()) if actual[p] != expected[p]]
    problems += [f'unexpected {p}' for p in sorted(actual.keys() - expected.keys())]
    if problems:
        raise ValueError(f'{what} runtime sources differ from the pinned Virgil runtime: ' + ', '.join(problems))


def check_runtime_mode(path, mode):
    if mode != 0o644:
        raise ValueError(f'{path} has mode {mode:o}, expected 644')


def check_bundle_revision(manifest, bundled_revision, revision):
    if (manifest['virgilRevision'] != revision or manifest['library']['revision'] != revision
            or bundled_revision != (revision + '\n').encode()):
        raise ValueError('bundle revision does not match the pinned Virgil revision')


def stage(source, destination, revision):
    inventory(source / 'lib')  # inspect before copying, without following links
    patterns = runtime_patterns()
    check_runtime_checkout(source, patterns)
    runtime = runtime_blobs(source, revision, patterns)
    license = source / 'rt/LICENSE'  # named by Virgil's root README
    if not stat.S_ISREG(license.lstat().st_mode):
        raise ValueError(f"not a regular license file: {license}")
    destination.mkdir()
    shutil.copytree(source / 'lib', destination / 'lib')
    for relative, data in runtime.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    shutil.copyfile(license, destination / 'LICENSE')
    (destination / 'REVISION').write_text(revision + '\n', encoding='ascii')
    for path in [destination, *destination.rglob('*')]:
        path.chmod(0o755 if path.is_dir() else 0o644)


# The JVM package's own files beside the launchers.
JVM_JARS = ('virgil-lsp.jar', 'virgil-lsp-worker.jar')


def jvm_runtime():
    """scripts/jvm-runtime.py, loaded once per process and shared with
    vsix-payload.py, so that both check against the same pin."""
    module = sys.modules.get('virgil_lsp_jvm_runtime')
    if module is None:
        spec = importlib.util.spec_from_file_location('virgil_lsp_jvm_runtime',
                                                      Path(__file__).with_name('jvm-runtime.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules['virgil_lsp_jvm_runtime'] = module
    return module


def check_jvm(name, manifest, entries, modes, directories):
    """Checks a JVM archive's platform, launchers, jars, modes, and runtime.
    Returns the runtime's paths in the archive."""
    runtime = jvm_runtime()
    platform = manifest.get('platform')
    if platform not in runtime.PLATFORMS:
        raise ValueError(f'unknown JVM platform: {platform!r}')
    if not name.endswith(f'-jvm-{platform}'):
        raise ValueError(f'archive name does not end with -jvm-{platform}')
    prefix = f'{name}/runtime/'
    files = {key[len(prefix):]: (data, modes[key]) for key, data in entries.items()
             if key.startswith(prefix)}
    runtime.check(platform, files, manifest.get('runtime') or {})
    for jar in JVM_JARS:
        if sha(entries.get(f'{name}/{jar}', b'')) != manifest['sha256'].get(jar):
            raise ValueError(f'{jar} checksum does not match the manifest')
    for exe in ('virgil-lsp', 'virgil-lsp-worker'):
        text = entries[f'{name}/{exe}'].decode('utf-8', 'replace')
        # A development launcher names its JDK and jar by absolute path.
        if (f'exec "$dir/runtime/bin/java" ' not in text or f' -jar "$dir/{exe}.jar" ' not in text
                or "'/" in text):
            raise ValueError(f'{exe} is not a relocatable launcher for runtime/ and {exe}.jar')
    for key, mode in modes.items():
        relative = key[len(name) + 1:]
        if not relative.startswith('runtime/'):
            want = 0o755 if relative in ('virgil-lsp', 'virgil-lsp-worker') else 0o644
            if mode != want:
                raise ValueError(f'{relative} has mode {mode:o}, not {want:o}')
    for directory, mode in directories.items():
        if mode != 0o755:
            raise ValueError(f'directory {directory} has mode {mode:o}, not 755')
    return {prefix + path for path in files}


def check_archive(archive, revision):
    """Check a release archive against its manifest and the pinned Virgil runtime."""
    name = archive.name.removesuffix('.tar.gz')
    bundle_root = f'{name}/virgil'
    runtime_root = f'{bundle_root}/rt'
    with tarfile.open(archive, 'r:gz') as tar:
        entries = {}
        modes = {}
        directories = {}
        for entry in tar.getmembers():
            path = PurePosixPath(entry.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or path.parts[0] != name or str(path) != entry.name.rstrip('/')):
                raise ValueError(f"unsafe archive member: {entry.name}")
            if entry.name.rstrip('/') in entries or entry.name.rstrip('/') in directories:
                raise ValueError(f"duplicate archive member: {entry.name}")
            mode = entry.mode & 0o777
            if entry.isdir():
                directories[entry.name.rstrip('/')] = mode
            elif entry.isfile():
                if entry.name.startswith(runtime_root + '/'):
                    check_runtime_mode(entry.name, mode)
                entries[entry.name] = tar.extractfile(entry).read()
                modes[entry.name] = mode
            else:
                raise ValueError(f"unexpected archive entry: {entry.name}")
        manifest = json.loads(entries[f'{name}/manifest.json'])
        library = manifest['library']
        check_bundle_revision(manifest, entries[f'{bundle_root}/REVISION'], revision)
        files = {key.removeprefix(bundle_root + '/'): sha(data)
                 for key, data in entries.items() if key.startswith(bundle_root + '/')}
        if files != library['files']:
            raise ValueError('bundle files/checksums do not match the manifest')
        if not files.get('LICENSE') or not any(p.startswith('lib/') for p in files):
            raise ValueError('bundle must include LICENSE and lib sources')
        if any(p not in ('LICENSE', 'REVISION') and not p.startswith(('lib/', 'rt/')) for p in files):
            raise ValueError('bundle includes files outside lib, rt, LICENSE, and REVISION')
        compare_runtime({p: d for p, d in files.items() if p.startswith('rt/')},
                        pinned_runtime(revision), 'bundle')
        lines = ''.join(f'{digest}  {path}\n' for path, digest in sorted(files.items()))
        if sha(lines.encode()) != library['treeSha256']:
            raise ValueError('bundle tree digest does not match the manifest')
        expected = {f'{name}/{p}' for p in (
            'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'manifest.json',
            'virgil-lsp', 'virgil-lsp-worker')}
        expected.update(f'{bundle_root}/{p}' for p in files)
        if manifest.get('target') == 'jvm':
            expected.update(f'{name}/{jar}' for jar in JVM_JARS)
            expected.update(check_jvm(name, manifest, entries, modes, directories))
        if set(entries) != expected:
            raise ValueError('archive files do not match the expected release layout')
        allowed_dirs = {name}
        for path in expected:
            allowed_dirs.update(str(p) for p in PurePosixPath(path).parents if str(p) != '.')
        if not set(directories) <= allowed_dirs:
            raise ValueError('archive has unexpected directories')
        for exe in ('virgil-lsp', 'virgil-lsp-worker'):
            if sha(entries[f'{name}/{exe}']) != manifest['sha256'][exe]:
                raise ValueError(f'{exe} checksum does not match the manifest')
    print(f'{archive.name}: bundle files, runtime sources, license, revision, and tree digest verified')


def archive(stage, output, epoch):
    # Fixed gzip header, sorted members, and normalized tar metadata work with
    # GNU/BSD hosts alike. Executable/data modes were set when staging.
    with output.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as tar:
            for path in [stage, *sorted(stage.rglob('*'))]:
                info = tar.gettarinfo(str(path), arcname=path.relative_to(stage.parent).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mtime = epoch
                if path.is_file():
                    with path.open('rb') as f:
                        tar.addfile(info, f)
                else:
                    tar.addfile(info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('stage')
    p.add_argument('source', type=Path)
    p.add_argument('destination', type=Path)
    p.add_argument('revision')
    p = sub.add_parser('check')
    p.add_argument('archive', type=Path)
    p.add_argument('revision')
    p = sub.add_parser('archive')
    p.add_argument('stage', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('epoch', type=int)
    args = parser.parse_args()
    try:
        if args.command == 'stage':
            stage(args.source, args.destination, args.revision)
        elif args.command == 'check':
            check_archive(args.archive, args.revision)
        else:
            archive(args.stage, args.output, args.epoch)
    except (OSError, ValueError, KeyError, UnicodeDecodeError, tarfile.TarError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
