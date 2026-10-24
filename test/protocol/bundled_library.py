#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Root precedence, failure, and no-symlink traversal with a real adjacent bundle."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bundled_library', ROOT / 'test/release/bundled-library.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
server = Path(sys.argv[1]).resolve()
revision = subprocess.check_output([str(server), '--version'], text=True).split('virgil revision: ')[1].splitlines()[0]
with tempfile.TemporaryDirectory(prefix='bundled-library-') as temporary:
    install = Path(temporary).resolve()
    for name in ('virgil-lsp', 'virgil-lsp-worker'):
        shutil.copy2(server.parent / name, install / name)
    exe = install / 'virgil-lsp'
    bundle = install / 'virgil'
    bundle.mkdir()
    shutil.copytree(ROOT / 'vendor/virgil/lib', bundle / 'lib')
    (bundle / 'REVISION').write_text(revision + '\n')
    for name, options in [('absent', None), ('empty options', {}), ('null', {'virgilRoot': None})]:
        module.exercise(exe, bundle, options, log_contains='(bundle, revision ')
        print(f'PASS: bundle found ({name})')
    client = install / 'client'
    shutil.copytree(bundle, client)
    module.exercise(exe, client, {'virgilRoot': str(client)}, log_contains='(client)')
    print('PASS: client overrides present bundle')
    for value in (42, False, '', 'relative', [], {}):
        module.exercise(exe, options={'virgilRoot': value}, error='MissingVirgilRoot',
                        log_contains='invalid client root; bundle fallback disabled')
        print(f'PASS: invalid override {value!r} blocks fallback')
    module.exercise(exe, options={'virgilRoot': str(install / 'absent')}, error='SourceIO',
                    log_contains='(client)')
    print('PASS: unlistable absolute client root blocks fallback')
    (bundle / 'REVISION').write_text('0' * 40 + '\n')
    module.exercise(exe, error='MissingVirgilRoot', log_contains='REVISION does not match')
    print('PASS: revision mismatch')
    (bundle / 'REVISION').unlink()
    module.exercise(exe, error='MissingVirgilRoot', log_contains='REVISION is missing')
    print('PASS: bundle missing revision')
    shutil.rmtree(bundle)
    module.exercise(exe, error='MissingVirgilRoot', log_contains='REVISION is missing')
    print('PASS: bundle missing')
    bundle.symlink_to(client, target_is_directory=True)
    module.exercise(exe, error='MissingVirgilRoot', log_contains='REVISION is missing')
    print('PASS: symlinked bundle rejected')
