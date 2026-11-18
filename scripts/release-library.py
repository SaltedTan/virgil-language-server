#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Stage/check the pinned source bundle and write normalized release archives."""

import argparse
import gzip
import hashlib
import importlib.util
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
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


# The supported-target table, whose runtime patterns select the rt/ sources
# that the bundle holds beside lib/ (ADR-0018, decision 7).
TARGETS_SOURCE = Path(__file__).resolve().parents[1] / 'src/analysis/AnalysisTargets.v3'


def runtime_patterns(source=TARGETS_SOURCE):
    """The runtime patterns of the targets in AnalysisTargets.all, in order and
    without duplicates, read from the Virgil table itself."""
    text = Path(source).read_text(encoding='utf-8')
    entries = dict(re.findall(r'def\s+(\w+)\s*=\s*AnalysisTarget\.new\(\s*"[^"]*"\s*,'
                              r'\s*"[^"]*"\s*,\s*\[([^\]]*)\]', text))
    supported = re.search(r'def\s+all\s*:\s*Array<AnalysisTarget>\s*=\s*\[([^\]]*)\]', text)
    if not entries or supported is None:
        raise ValueError(f'no supported targets found in {source}')
    patterns = []
    for name in re.findall(r'\w+', supported.group(1)):
        if name not in entries:
            raise ValueError(f'supported target {name} has no AnalysisTarget.new entry in {source}')
        for pattern in re.findall(r'"([^"]*)"', entries[name]):
            parts = pattern.split('/')
            if (parts[0] != 'rt' or len(parts) < 3 or any(p in ('', '.', '..') for p in parts)
                    or any(c in p for p in parts[:-1] for c in '*?[')):
                raise ValueError(f'unsupported runtime pattern in {source}: {pattern!r}')
            if pattern not in patterns:
                patterns.append(pattern)
    if not patterns:
        raise ValueError(f'the supported targets in {source} have no runtime patterns')
    return patterns


def runtime_match(path, patterns):
    """Whether a bundle path is selected by one of the runtime patterns, whose
    wildcards, as in project patterns, never match a "/"."""
    parts = path.split('/')
    return any(len(parts) == len(p.split('/')) and
               all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, p.split('/')))
               for p in patterns)


def runtime_files(source, patterns):
    """The bundle paths of the regular files under source that the runtime
    patterns select. A selected entry that isn't a regular file, or a
    directory on the way that is a link, is an error, as for lib/."""
    files = []
    for pattern in patterns:
        directory, name = pattern.rsplit('/', 1)
        path = source
        for part in directory.split('/'):
            path = path / part
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise ValueError(f'not a physical directory: {path}')
        matched = sorted(p for p in path.iterdir() if fnmatch.fnmatchcase(p.name, name))
        if not matched:
            raise ValueError(f'runtime pattern {pattern} selects no files in {source}')
        for entry in matched:
            if not stat.S_ISREG(entry.lstat().st_mode):
                raise ValueError(f'unexpected bundle entry (regular files only): {entry}')
            relative = f'{directory}/{entry.name}'
            if relative not in files:
                files.append(relative)
    return sorted(files)


def stage(source, destination, revision, patterns=None):
    inventory(source / 'lib')  # inspect before copying, without following links
    runtime = runtime_files(source, runtime_patterns() if patterns is None else patterns)
    license = source / 'rt/LICENSE'  # named by Virgil's root README
    if not stat.S_ISREG(license.lstat().st_mode):
        raise ValueError(f"not a regular license file: {license}")
    destination.mkdir()
    shutil.copytree(source / 'lib', destination / 'lib')
    for relative in runtime:
        (destination / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination / relative)
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


def check_archive(archive, revision, patterns=None):
    patterns = runtime_patterns() if patterns is None else patterns
    name = archive.name.removesuffix('.tar.gz')
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
            if entry.isdir():
                directories[entry.name.rstrip('/')] = entry.mode & 0o777
            elif entry.isfile():
                entries[entry.name] = tar.extractfile(entry).read()
                modes[entry.name] = entry.mode & 0o777
            else:
                raise ValueError(f"unexpected archive entry: {entry.name}")
        manifest = json.loads(entries[f'{name}/manifest.json'])
        library = manifest['library']
        if (manifest['virgilRevision'] != revision or library['revision'] != revision
                or entries[f'{name}/virgil/REVISION'] != (revision + '\n').encode()):
            raise ValueError('bundle revision does not match the pinned Virgil revision')
        files = {key.removeprefix(f'{name}/virgil/'): sha(data)
                 for key, data in entries.items() if key.startswith(f'{name}/virgil/')}
        if files != library['files']:
            raise ValueError('bundle files/checksums do not match the manifest')
        if not files.get('LICENSE') or not any(p.startswith('lib/') for p in files):
            raise ValueError('bundle must include LICENSE and lib sources')
        if any(p not in ('LICENSE', 'REVISION') and not p.startswith('lib/')
               and not runtime_match(p, patterns) for p in files):
            raise ValueError('bundle includes files outside lib, the runtime patterns, LICENSE, and REVISION')
        for pattern in patterns:
            if not any(runtime_match(p, [pattern]) for p in files):
                raise ValueError(f'bundle has no runtime sources for {pattern}')
        lines = ''.join(f'{digest}  {path}\n' for path, digest in sorted(files.items()))
        if sha(lines.encode()) != library['treeSha256']:
            raise ValueError('bundle tree digest does not match the manifest')
        expected = {f'{name}/{p}' for p in (
            'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'manifest.json',
            'virgil-lsp', 'virgil-lsp-worker')}
        expected.update(f'{name}/virgil/{p}' for p in files)
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
