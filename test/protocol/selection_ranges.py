#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Exact selection chains, current overlays, errors, and request bounds."""
import sys
from pathlib import Path
from worker import Session

URI = 'untitled:selection-protocol.v3'
TEXT = 'component C { def f() { g(42, 17); } }\n'


def chain(text, parts):
    parent = None
    for part in reversed(parts):
        begin = text.index(part)
        end = begin + len(part)
        def position(at):
            prefix = text[:at]
            return {'line': prefix.count('\n'),
                    'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}
        node = {'range': {'start': position(begin), 'end': position(end)}}
        if parent is not None:
            node['parent'] = parent
        parent = node
    return parent


def run(exe):
    s = Session(exe)
    base = {'textDocument': {'uri': URI}}
    positions = [{'line': 0, 'character': 26}, {'line': 0, 'character': 30}]
    def query(ps=positions):
        return s.result(s.send('textDocument/selectionRange', dict(base, positions=ps)))
    def change(text, version):
        s.send('textDocument/didChange', dict(base, textDocument={'uri': URI, 'version': version},
               contentChanges=[{'text': text}]), request=False)
    try:
        caps = s.result(s.send('initialize', {'capabilities': {}}))['capabilities']
        assert caps['selectionRangeProvider'] is True
        assert query() == []
        s.send('textDocument/didOpen', {'textDocument': {'uri': URI,
               'languageId': 'virgil', 'version': 1, 'text': TEXT}}, request=False)
        outer = ['42, 17', '(42, 17)', 'g(42, 17);', '{ g(42, 17); }',
                 'def f() { g(42, 17); }', '{ def f() { g(42, 17); } }', TEXT.rstrip(), TEXT]
        assert query() == [chain(TEXT, [token] + outer) for token in ('42', '17')], s.transcript
        change('\n' + TEXT, 2)
        assert query([dict(p, line=1) for p in positions]) == [
            chain('\n' + TEXT, [token] + outer[:-1] + ['\n' + TEXT]) for token in ('42', '17')]
        broken = 'component C { def f() { return 1; } def bad() { var x = ; } def later() { return 2; } }\n'
        change(broken, 3)
        assert query([{'line': 0, 'character': broken.index('1;')},
                      {'line': 0, 'character': broken.index('2;')}]) == [
            chain(broken, ['1', 'return 1;', '{ return 1; }', 'def f() { return 1; }', broken]),
            chain(broken, ['2', broken])]
        utf = 'component C {\r\n\tdef f() { /* é𝄞 */ return; }\r\n}\r\n'
        change(utf, 4)
        assert query([{'line': 1, 'character': 16}]) == [chain(utf, ['/* é𝄞 */',
            '/* é𝄞 */ return;', '{ /* é𝄞 */ return; }', 'def f() { /* é𝄞 */ return; }',
            '{\r\n\tdef f() { /* é𝄞 */ return; }\r\n}', utf.rstrip(), utf])]
        assert query([]) == []
        assert len(query([{'line': 0, 'character': 0}] * 100)) == 100
        assert s.reply(s.send('textDocument/selectionRange', dict(base,
            positions=[{'line': 0, 'character': 0}] * 101)))['error']['code'] == -32602
        s.send('textDocument/didClose', base, request=False)
        assert query() == []
        for bad in (None, [], {}, {'textDocument': None}, dict(base, positions=None),
                    dict(base, positions=[None]), dict(base, positions=[{}]),
                    dict(base, positions=[{'line': -1, 'character': 0}]),
                    dict(base, positions=[{'line': 0, 'character': '0'}]),
                    {'textDocument': {'uri': 'file://remote/a'}, 'positions': []}):
            assert s.reply(s.send('textDocument/selectionRange', bad))['error']['code'] == -32602
        s.shutdown()
    finally:
        s.close()


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
    print('PASS: selection ranges (exact chains, overlay, edits, parse error, UTF-16, batch, close, params, limits)')
