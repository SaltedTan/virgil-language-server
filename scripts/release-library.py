#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Stage/check the pinned source bundle and write normalized release archives."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
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


def stage(source, destination, revision):
    inventory(source / 'lib')  # inspect before copying, without following links
    license = source / 'rt/LICENSE'  # named by Virgil's root README
    if not stat.S_ISREG(license.lstat().st_mode):
        raise ValueError(f"not a regular license file: {license}")
    destination.mkdir()
    shutil.copytree(source / 'lib', destination / 'lib')
    shutil.copyfile(license, destination / 'LICENSE')
    (destination / 'REVISION').write_text(revision + '\n', encoding='ascii')
    for path in [destination, *destination.rglob('*')]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def check_archive(archive, revision):
    name = archive.name.removesuffix('.tar.gz')
    with tarfile.open(archive, 'r:gz') as tar:
        entries = {}
        directories = set()
        for entry in tar.getmembers():
            path = PurePosixPath(entry.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or path.parts[0] != name or str(path) != entry.name.rstrip('/')):
                raise ValueError(f"unsafe archive member: {entry.name}")
            if entry.name.rstrip('/') in entries or entry.name.rstrip('/') in directories:
                raise ValueError(f"duplicate archive member: {entry.name}")
            if entry.isdir():
                directories.add(entry.name.rstrip('/'))
            elif entry.isfile():
                entries[entry.name] = tar.extractfile(entry).read()
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
        if any(p not in ('LICENSE', 'REVISION') and not p.startswith('lib/') for p in files):
            raise ValueError('bundle includes files outside lib, LICENSE, and REVISION')
        lines = ''.join(f'{digest}  {path}\n' for path, digest in sorted(files.items()))
        if sha(lines.encode()) != library['treeSha256']:
            raise ValueError('bundle tree digest does not match the manifest')
        expected = {f'{name}/{p}' for p in (
            'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'manifest.json',
            'virgil-lsp', 'virgil-lsp-worker')}
        expected.update(f'{name}/virgil/{p}' for p in files)
        if set(entries) != expected:
            raise ValueError('archive files do not match the expected release layout')
        allowed_dirs = {name}
        for path in expected:
            allowed_dirs.update(str(p) for p in PurePosixPath(path).parents if str(p) != '.')
        if not directories <= allowed_dirs:
            raise ValueError('archive has unexpected directories')
        for exe in ('virgil-lsp', 'virgil-lsp-worker'):
            if sha(entries[f'{name}/{exe}']) != manifest['sha256'][exe]:
                raise ValueError(f'{exe} checksum does not match the manifest')
    print(f'{archive.name}: bundle files, license, revision, and tree digest verified')


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
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
