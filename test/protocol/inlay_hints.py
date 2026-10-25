#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Inlay hints through real workers: ranges, settings, freshness, refresh."""
import json
from pathlib import Path
import sys
import tempfile
from worker import Session
from type_definition import position, location, open_file, change

TEXT = ('class Box { }\ncomponent Hints {\n'
        '  def take(count: int) -> Box { return Box.new(); }\n'
        '  def run() { /* é𝄞 */ var box = take(3); }\n}\n')

class HintsSession(Session):
    def __init__(self, *args):
        super().__init__(*args)
        self.refreshes = []

    def receive(self, deadline):
        msg = super().receive(deadline)
        if msg.get('method') == 'workspace/inlayHint/refresh':
            assert 'params' not in msg or msg['params'] is None, msg
            self.refreshes.append(msg)
            payload = json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': None}).encode()
            self.raw(f'Content-Length: {len(payload)}\r\n\r\n'.encode() + payload)
            return self.receive(deadline)
        return msg


def query(s, path, start=None, end=None):
    return s.result(s.send('textDocument/inlayHint', {'textDocument': {'uri': path.as_uri()},
        'range': {'start': start or {'line': 0, 'character': 0},
                  'end': end or {'line': 100, 'character': 0}}}))


def expected(path, types=True, names=True, text=TEXT):
    result = []
    if types:
        result.append({'position': position(text, 'box =', 3), 'kind': 1,
            'label': [{'value': ': Box', 'location': location(path, text, 'Box')}]})
    if names:
        result.append({'position': position(text, '3);'), 'kind': 2,
                       'label': 'count:', 'paddingRight': True})
    return result


def run(exe, root, types, names, support):
    path, loose = root / 'Main.v3', root / 'Loose.v3'
    path.write_text(TEXT)
    loose.write_text(TEXT)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'hints', 'sources': ['Main.v3']}]}))
    s = HintsSession(exe, '--worker-max-analyses=1')
    try:
        options = {} if types and names else {'inlayHints': {'types': types, 'parameterNames': names}}
        caps = s.result(s.send('initialize', {'rootUri': root.as_uri(), 'initializationOptions': options,
            'capabilities': {'workspace': {'inlayHint': {'refreshSupport': support}}}}))['capabilities']
        assert caps['inlayHintProvider'] is True, caps
        assert query(s, loose) == []
        open_file(s, path, TEXT)
        want = expected(path, types, names)
        assert query(s, path) == want, s.transcript
        assert not s.refreshes, (s.transcript, s.logs())
        count = len(s.refreshes)
        assert 'after 1 analyses (limit 1)' in s.logs(), s.logs()
        s.result(s.analyze([loose.as_uri()]))
        assert query(s, path) == want
        assert query(s, loose) == []
        start, end = position(TEXT, 'box =', 3), position(TEXT, '3);')
        assert query(s, path, start, end) == ([want[0]] if types else [])
        assert query(s, path, start, start) == []
        s.send('textDocument/didSave', {'textDocument': {'uri': path.as_uri()}}, request=False)
        assert query(s, path) == want
        assert len(s.refreshes) == count, s.transcript
        unchanged = TEXT + '// unrelated trailing comment\n'
        change(s, path, unchanged, 2)
        assert query(s, path) == want
        assert len(s.refreshes) == count, s.transcript
        change(s, path, unchanged, 3)
        assert query(s, path) == want
        assert len(s.refreshes) == count, s.transcript
        changed = '\n' + TEXT
        change(s, path, changed, 4)
        assert query(s, path) == expected(path, types, names, changed)
        assert len(s.refreshes) == count + int(support and (types or names)), s.transcript
        change(s, path, 'component Broken {', 5)
        assert query(s, path) == []
        for params in [None, {}, {'textDocument': {'uri': path.as_uri()},
            'range': {'start': {'line': 2, 'character': 0}, 'end': {'line': 1, 'character': 0}}}]:
            assert s.reply(s.send('textDocument/inlayHint', params))['error']['code'] == -32602
        s.shutdown()
    finally:
        s.close()


def cross_file(exe, root):
    path, library = root / 'Main.v3', root / 'Library.v3'
    text = 'component Main { def run() { var a = Library.make(1); var b = Library.make(2); } }\n'
    lib = 'class Box {}\ncomponent Library { def make(count: int) -> Box { return Box.new(); } }\n'
    path.write_text(text)
    library.write_text(lib)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'cross', 'sources': ['Main.v3', 'Library.v3']}]}))
    s = HintsSession(exe)
    try:
        s.result(s.send('initialize', {'rootUri': root.as_uri(),
            'capabilities': {'workspace': {'inlayHint': {'refreshSupport': True}}}}))
        open_file(s, path, text)
        first = query(s, path)
        assert len(first) == 4
        for h in first[::2]:
            assert h['label'][0]['location'] == location(library, lib, 'Box'), h
        # Repeated links share conversion only within this request; a later
        # request must notice an unreported disk edit in the target file.
        library.write_text('// shifted on disk\n' + lib)
        stale = query(s, path)
        assert all('location' not in h['label'][0] for h in stale[::2]), stale
        library.write_text(lib)
        assert query(s, path) == first
        before_open = len(s.refreshes)
        open_file(s, library, lib)
        assert query(s, path) == first
        assert len(s.refreshes) == before_open, s.transcript
        before = len(s.refreshes)
        renamed = lib.replace('count:', 'amount:')
        change(s, library, renamed, 2)
        second = query(s, path)
        assert [h['label'] for h in second[1::2]] == ['amount:', 'amount:'], second
        assert second[::2] == first[::2]
        assert len(s.refreshes) == before + 1, s.transcript
        before = len(s.refreshes)
        change(s, library, renamed + '// trailing comment\n', 3)
        assert query(s, path) == second
        assert len(s.refreshes) == before, s.transcript
        shifted = '\n' + renamed
        change(s, library, shifted, 4)
        third = query(s, path)
        assert len(s.refreshes) == before + 1, s.transcript
        assert third[1::2] == second[1::2]
        for h in third[::2]:
            assert h['label'][0]['location'] == location(library, shifted, 'Box'), h
        # Link ranges compare actual UTF-16 positions, not compiler columns,
        # byte offsets, or a source hash. Library itself has no hints here.
        fourth_text = '/* é */ ' + renamed
        change(s, library, fourth_text, 5)
        fourth = query(s, path)
        before = len(s.refreshes)
        change(s, library, '/* a */ ' + renamed, 6)
        assert query(s, path) == fourth
        assert len(s.refreshes) == before, s.transcript
        sixth_text = '/* aa */ ' + renamed
        change(s, library, sixth_text, 7)
        sixth = query(s, path)
        assert len(s.refreshes) == before + 1, s.transcript
        assert sixth != fourth
        before = len(s.refreshes)
        change(s, library, '/* aa */\t' + renamed, 8)
        assert query(s, path) == sixth
        assert len(s.refreshes) == before, s.transcript
        # Closed source files cannot have displayed hints. Changes confined to
        # one must not refresh another open file whose hints remain identical.
        s.send('textDocument/didClose', {'textDocument': {'uri': path.as_uri()}}, request=False)
        query(s, library)
        before = len(s.refreshes)
        change(s, library, lib.replace('count:', 'closedOnly:'), 9)
        assert query(s, library) == []
        assert len(s.refreshes) == before, s.transcript
        open_file(s, path, text)
        reopened = query(s, path)
        assert reopened[1]['label'] == 'closedOnly:'
        assert len(s.refreshes) == before, s.transcript
        s.shutdown()
    finally:
        s.close()


def fallback(exe, root):
    path, library = root / 'Main.v3', root / 'Library.v3'
    text = ('component Main {\n'
            '  def before() { var before = Library.make(1); }\n'
            '  def broken() { var inside = Library.make(2); }\n'
            '  def after() { var after = Library.make(3); }\n}\n')
    lib = ('component Library { def broken() { var inside = 1; }\n'
           '  def make(count: int) -> Box { return Box.new(); } }\nclass Box {}\n')
    path.write_text(text)
    library.write_text(lib)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'fallback', 'sources': ['Main.v3', 'Library.v3']}]}))
    s = HintsSession(exe)
    try:
        s.result(s.send('initialize', {'rootUri': root.as_uri(),
            'capabilities': {'workspace': {'inlayHint': {'refreshSupport': True}}}}))
        open_file(s, path, text)
        open_file(s, library, lib)
        first = query(s, path)
        assert len(first) == 6, first
        broken = text.replace('var inside =', 'var bad = ;\n    var inside =')
        change(s, path, broken, 2)
        hints = query(s, path)
        assert len(hints) == 4, hints
        assert hints[:2] == first[:2], hints
        assert hints[2]['position'] == position(broken, 'after =', 5), hints
        assert hints[3]['position'] == position(broken, '3);'), hints
        assert query(s, path, position(broken, 'bad =')) == hints[2:]
        assert query(s, path, position(broken, 'bad ='), position(broken, 'inside =')) == []
        # Definition's target mapping also applies when the linked file is degraded.
        broken_lib = lib.replace('var inside =', 'var bad = ;\n    var inside =')
        change(s, library, broken_lib, 2)
        linked = query(s, path)
        for h in linked[::2]:
            assert h['label'][0]['location'] == {'uri': library.as_uri(), 'range': {'start': position(broken_lib, 'class Box', 6), 'end': position(broken_lib, 'class Box', 9)}}, h
        before = len(s.refreshes)
        change(s, library, broken_lib, 3)
        assert query(s, path) == linked
        assert len(s.refreshes) == before, s.transcript
        s.shutdown()
    finally:
        s.close()


def refresh_scopes(exe, root):
    path, lib_a, lib_b = (root / name for name in ('Main.v3', 'LibA.v3', 'LibB.v3'))
    config = root / '.virgil-lsp.json'
    main = 'component Main { def run() { var x = Library.make(1); } }\n'
    a = 'class A {} component Library { def make(count:int) -> A { return A.new(); } }\n'
    b = 'class B {} component Library { def make(amount:int) -> B { return B.new(); } }\n'
    for file, text in [(path, main), (lib_a, a), (lib_b, b)]:
        file.write_text(text)

    def configuration(projects):
        return json.dumps({'version': 1, 'projects': [
            {'name': name, 'sources': sources} for name, sources in projects]})

    def initialize():
        s = HintsSession(exe)
        s.result(s.send('initialize', {'rootUri': root.as_uri(),
            'capabilities': {'workspace': {'inlayHint': {'refreshSupport': True}}}}))
        open_file(s, path, main)
        return s

    def expect(s, library, text, type_name, parameter):
        hints = query(s, path)
        assert len(hints) == 2, hints
        assert hints[0]['label'] == [{'value': ': ' + type_name,
                                      'location': location(library, text, type_name)}], hints
        assert hints[1]['label'] == parameter + ':', hints
        return hints

    config.write_text(configuration([('p', ['Main.v3', 'LibA.v3'])]))
    s = initialize()
    try:
        first = expect(s, lib_a, a, 'A', 'count')
        open_file(s, config, config.read_text())
        assert query(s, path) == first
        before = len(s.refreshes)
        replacement = configuration([('p', ['Main.v3', 'LibB.v3'])])
        change(s, config, replacement, 2)
        second = expect(s, lib_b, b, 'B', 'amount')
        assert len(s.refreshes) == before + 1, s.transcript
        # A new revision with identical hints must preserve the same baseline.
        change(s, config, replacement + '\n', 3)
        assert query(s, path) == second
        assert len(s.refreshes) == before + 1, s.transcript
        # No context will analyze Main after this edit, but displayed hints
        # must still be removed. Repeated empty results need no refresh.
        removed = configuration([('p', ['LibB.v3'])])
        change(s, config, removed, 4)
        assert query(s, path) == []
        assert len(s.refreshes) == before + 2, s.transcript
        change(s, config, removed + '\n', 5)
        assert query(s, path) == []
        assert len(s.refreshes) == before + 2, s.transcript
        change(s, config, replacement, 6)
        assert query(s, path) == second
        assert len(s.refreshes) == before + 3, s.transcript
        s.shutdown()
    finally:
        s.close()

    projects = [('a', ['Main.v3', 'LibA.v3']), ('b', ['Main.v3', 'LibB.v3'])]
    config.write_text(configuration(projects))
    s = initialize()
    try:
        first = expect(s, lib_a, a, 'A', 'count')
        query(s, lib_b)  # Establish the inactive context's snapshot.
        before = len(s.refreshes)
        changed_b = b.replace('amount:', 'inactive:')
        lib_b.write_text(changed_b)
        s.send('workspace/didChangeWatchedFiles', {'changes': [
            {'uri': lib_b.as_uri(), 'type': 2}]}, request=False)
        query(s, lib_b)  # Wait for b's analysis, not just a's cached reply.
        assert query(s, path) == first
        assert len(s.refreshes) == before, s.transcript
        changed_a = a.replace('count:', 'active:')
        lib_a.write_text(changed_a)
        s.send('workspace/didChangeWatchedFiles', {'changes': [
            {'uri': lib_a.as_uri(), 'type': 2}]}, request=False)
        expect(s, lib_a, changed_a, 'A', 'active')
        assert len(s.refreshes) == before + 1, s.transcript
        # Selecting a different active project also changes the same open
        # document's result, even though that document itself was not edited.
        open_file(s, config, config.read_text())
        expect(s, lib_a, changed_a, 'A', 'active')
        before = len(s.refreshes)
        change(s, config, configuration(list(reversed(projects))), 2)
        expect(s, lib_b, changed_b, 'B', 'inactive')
        assert len(s.refreshes) == before + 1, s.transcript
        s.shutdown()
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='inlay-hints-', dir=exe.parent) as work:
        for types, names, support in [(True, True, False), (True, True, True),
                                      (False, True, True), (True, False, True), (False, False, True)]:
            run(exe, Path(work), types, names, support)
        cross_file(exe, Path(work))
        fallback(exe, Path(work))
        refresh_scopes(exe, Path(work))
    print('protocol inlay hints: UTF-16 labels/links, ranges, settings, freshness, worker replacement, refresh passed')

if __name__ == '__main__':
    main()
