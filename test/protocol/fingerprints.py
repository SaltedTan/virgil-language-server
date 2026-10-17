#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Live snapshot/publication guards with a real stdio server and worker.

Pause this session's worker after a warmup, submit a project, use a snapshot
request as a capture barrier, change its disposable disk inputs, and resume.
No fake worker or timing-dependent race is used. Optional JSON evidence records
the actual protocol messages and server logs. Run from the repository root:

  python3 test/protocol/fingerprints.py build/virgil-lsp --evidence-dir PATH
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from worker import Session


A = 'component A { def a() -> int { return B.b(); } }\n'
B = '// café 😀\ncomponent B {\n\tdef b() -> int { /* 😀 */ return missing; }\n}\n'
C = 'def c = 1;\n'


def notify(s, method, params):
    s.send(method, params, request=False)


def open_file(s, uri, text, version=1):
    notify(s, 'textDocument/didOpen', {'textDocument': {
        'uri': uri, 'languageId': 'virgil', 'version': version, 'text': text}})


def changed(s, uri):
    notify(s, 'workspace/didChangeWatchedFiles', {'changes': [{'uri': uri, 'type': 2}]})


def inspect(s):
    result = s.result(s.send('virgil-lsp/snapshot'))
    if result is not None:
        assert set(result) in ({'generation', 'diagnostics', 'documentVersions'},
                               {'generation', 'diagnostics', 'documentVersions', 'projectConfiguration'}), result
        for stamp in result['documentVersions']:
            assert set(stamp) == {'uri', 'version', 'current'}, stamp
    return result


def completed(s, generation):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = inspect(s)
        if result is not None and result['generation'] >= generation:
            return result
        time.sleep(0.01)
    raise AssertionError('analysis did not finish')


def worker_pid(s):
    # Restrict signals to a direct child of the disposable server we launched.
    rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,comm='], text=True)
    children = [int(row.split()[0]) for row in rows.splitlines()
                if len(row.split()) >= 3 and int(row.split()[1]) == s.proc.pid]
    assert len(children) == 1, children
    return children[0]


def b_diagnostics(s, uri):
    return [msg['params'] for direction, msg in s.transcript
            if direction == 'recv' and msg.get('method') == 'textDocument/publishDiagnostics'
            and msg['params']['uri'] == uri and msg['params']['diagnostics']]


def assert_b_diagnostic(s, uri):
    lists = b_diagnostics(s, uri)
    assert len(lists) == 1, lists
    assert len(lists[0]['diagnostics']) == 1, lists
    d, = lists[0]['diagnostics']
    assert d['message'] == 'identifier "missing" cannot be found', d
    assert d['range'] == {'start': {'line': 2, 'character': 34},
                          'end': {'line': 2, 'character': 41}}, d


def publication_case(exe, work, mode):
    root = work / mode
    root.mkdir()
    dep = root / 'dep'
    dep.mkdir()
    a, b, c = root / 'A.v3', dep / 'B.v3', root / 'C.v3'
    a.write_text(A)
    b.write_bytes(B.encode())
    c.write_text(C)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'p', 'sources': ['A.v3', 'dep/B.v3', 'C.v3']}]}))
    s = Session(exe)
    paused = None
    observed = {}
    try:
        s.result(s.send('initialize', {'rootUri': root.as_uri(), 'capabilities': {}}))
        s.result(s.analyze([a.as_uri()]))
        paused = worker_pid(s)
        os.kill(paused, signal.SIGSTOP)
        open_file(s, a.as_uri(), A)
        assert inspect(s)['generation'] == 1  # didOpen captured before this reply.
        if mode == 'same-length-edit':
            b.write_bytes(B.replace('missing', 'unknown').encode())
        elif mode == 'shorter':
            b.write_text('')
        elif mode == 'longer':
            with b.open('wb') as output:
                output.truncate(1024 * 1024)
        elif mode == 'deleted':
            b.unlink()
        elif mode == 'file-symlink':
            target = root / 'saved.txt'
            target.write_bytes(B.encode())
            b.unlink()
            b.symlink_to(target)
        elif mode == 'directory-symlink':
            saved = root / 'saved'
            dep.rename(saved)
            dep.symlink_to(saved, target_is_directory=True)
        elif mode == 'unrelated-source-edited':
            c.write_text(C.replace('c', 'd'))
        elif mode == 'reported-unchanged':
            changed(s, b.as_uri())
            assert inspect(s)['generation'] == 1
        else:
            assert mode == 'unchanged'
        observed = {'capturedDiskBytes': len(B.encode()),
                    'diskExists': b.exists(), 'fileIsSymlink': b.is_symlink(),
                    'parentIsSymlink': dep.is_symlink(),
                    'diskBytesAfterMutation': b.stat().st_size if b.exists() else None}
        os.kill(paused, signal.SIGCONT)
        paused = None
        snapshot = completed(s, 3 if mode == 'reported-unchanged' else 2)
        blocked = mode in {'same-length-edit', 'shorter', 'longer', 'deleted',
                           'file-symlink', 'directory-symlink'}
        if blocked:
            assert not b_diagnostics(s, b.as_uri()), s.transcript
            assert 'changed on disk after its sources were captured' in s.logs(), s.logs()
            # Unreported byte changes do not change the inspection stamp.
            assert all(v['current'] for v in snapshot['documentVersions']), snapshot
            # Restore a regular file, report it, and prove publication recovers.
            if mode == 'directory-symlink':
                dep.unlink()
                (root / 'saved').rename(dep)
            elif b.is_symlink():
                b.unlink()
            b.write_bytes(B.encode())
            changed(s, b.as_uri())
            completed(s, 3)
        if mode == 'reported-unchanged':
            assert 'discarded analysis 2' in s.logs(), s.logs()
            assert 'changed after its sources were captured' in s.logs(), s.logs()
        assert_b_diagnostic(s, b.as_uri())
        assert all(v['current'] for v in snapshot['documentVersions']), snapshot
        s.shutdown()
        return {'scenario': mode, 'observedMutation': observed, 'snapshot': snapshot,
                'transcript': s.transcript, 'stderr': s.logs()}
    finally:
        if paused is not None:
            os.kill(paused, signal.SIGCONT)
        s.close()


def overlay_case(exe, work):
    path = work / 'overlay.v3'
    path.write_text('def disk = 1;\n')
    uri = path.as_uri()
    text = 'def overlay = 1;\n'
    s = Session(exe)
    try:
        s.initialize()
        open_file(s, uri, text, version=-3)
        result = s.result(s.analyze([uri]))
        assert result['documentVersions'] == [{'uri': uri, 'version': -3, 'current': True}]
        # A duplicate/rejected version must leave the accepted record current.
        notify(s, 'textDocument/didChange', {'textDocument': {'uri': uri, 'version': -3},
                                           'contentChanges': [{'text': 'def bad = missing;'}]})
        assert inspect(s) == result
        notify(s, 'textDocument/didChange', {'textDocument': {'uri': uri, 'version': -2},
                                           'contentChanges': [{'text': text}]})
        assert inspect(s)['documentVersions'][0]['current'] is False
        result = s.result(s.analyze([uri]))
        assert result['documentVersions'][0]['current'] is True
        notify(s, 'textDocument/didClose', {'textDocument': {'uri': uri}})
        assert inspect(s)['documentVersions'][0]['current'] is False
        open_file(s, uri, text, version=-2)
        assert inspect(s)['documentVersions'][0]['current'] is False
        # Disk freshness remains stamp-based even if unreported bytes change.
        notify(s, 'textDocument/didClose', {'textDocument': {'uri': uri}})
        result = s.result(s.analyze([uri]))
        path.write_text('def changed = 2;\n')
        assert inspect(s) == result
        changed(s, uri)
        assert inspect(s)['documentVersions'][0]['current'] is False
        s.shutdown()
        return {'scenario': 'overlay-identity-and-disk-stamps',
                'transcript': s.transcript, 'stderr': s.logs()}
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path)
    parser.add_argument('--evidence-dir', type=Path)
    args = parser.parse_args()
    evidence = []
    try:
        with tempfile.TemporaryDirectory(prefix='fingerprints-', dir=args.exe.parent) as tmp:
            work = Path(tmp).absolute()
            for mode in ['unchanged', 'same-length-edit', 'shorter', 'longer', 'deleted',
                         'file-symlink', 'directory-symlink', 'unrelated-source-edited',
                         'reported-unchanged']:
                evidence.append(publication_case(args.exe.absolute(), work, mode))
            evidence.append(overlay_case(args.exe.absolute(), work))
    finally:
        if args.evidence_dir:
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            (args.evidence_dir / 'fingerprints-live.json').write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print('Live stdio: disk byte guards, no symlink traversal, per-source checks, '
          'recovery, overlay identity, stamp policy, UTF-16 diagnostics and inspection format passed.')


if __name__ == '__main__':
    main()
