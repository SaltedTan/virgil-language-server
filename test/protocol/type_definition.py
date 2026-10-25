#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Type navigation over stdio, including retained targets after worker replacement."""
import json
from pathlib import Path
import sys
import tempfile

from worker import Session

A = '/* é𝄞\t */ class Foo { }\nclass Other { }\n'
B = ('component C {\n'
     '  def make() -> Foo { return Foo.new(); }\n'
     '  def run() { /* é𝄞\t */ var inferred = make(); void(inferred); }\n'
     '}\n')


def position(text, needle, skip=0):
    offset = text.index(needle) + skip
    prefix = text[:offset]
    return {'line': prefix.count('\n'),
            'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def location(path, text, name):
    begin = position(text, name)
    return {'uri': path.as_uri(), 'range': {'start': begin, 'end': {
        'line': begin['line'], 'character': begin['character'] + len(name)}}}


def open_file(s, path, text):
    s.send('textDocument/didOpen', {'textDocument': {
        'uri': path.as_uri(), 'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)


def change(s, path, text, version):
    s.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': version},
                                    'contentChanges': [{'text': text}]}, request=False)


def query(s, path, text, needle, skip=0):
    return s.result(s.send('textDocument/typeDefinition', {
        'textDocument': {'uri': path.as_uri()}, 'position': position(text, needle, skip)}))


def run(exe, root):
    a, b, extra, loose = (root / name for name in ('A.v3', 'B.v3', 'Extra.v3', 'Loose.v3'))
    for path, text in ((a, A), (b, B), (extra, 'class Extra { }\n'), (loose, B)):
        path.write_text(text)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'types', 'sources': ['A.v3', 'B.v3']}]}))
    # Retire every completed worker. A second, unrelated analysis launches
    # and replaces another worker while the context keeps its first snapshot.
    s = Session(exe, '--worker-max-analyses=1')
    try:
        caps = s.result(s.send('initialize', {'rootUri': root.as_uri()}))['capabilities']
        assert caps['typeDefinitionProvider'] is True
        open_file(s, b, B)
        expected = location(a, A, 'Foo')
        assert query(s, b, B, 'void(inferred)', 5) == expected
        assert 'after 1 analyses (limit 1)' in s.logs(), s.logs()
        s.result(s.analyze([extra.as_uri()]))
        assert s.logs().count('after 1 analyses (limit 1)') >= 2, s.logs()
        assert query(s, b, B, 'var inferred', 4) == expected
        assert query(s, b, B, 'make();') == expected
        # A method's name without a call is a function, with no type location.
        assert query(s, b, B, 'def make', 4) is None
        assert query(s, b, B, '-> Foo', 3) == expected
        # Request immediately after a document edit: wait for the new snapshot
        # and follow Other, never the old Foo target at identical positions.
        changed = B.replace('Foo', 'Other')
        change(s, b, changed, 2)
        assert query(s, b, changed, 'void(inferred)', 5) == location(a, A, 'Other')
        # An edit before an open target invalidates its old bytes as well.
        # Conversion uses the declaration's current UTF-16 name range.
        moved = '\n' + A
        open_file(s, a, moved)
        assert query(s, b, changed, 'void(inferred)', 5) == location(a, moved, 'Other')
        # One broken project file suppresses all semantic navigation, even
        # for an otherwise intact source and a previously retained target.
        change(s, a, 'class Foo {', 2)
        assert query(s, b, changed, 'void(inferred)', 5) is None
        change(s, a, A, 3)
        assert query(s, b, changed, 'void(inferred)', 5) == location(a, A, 'Other')
        open_file(s, loose, B)
        assert query(s, loose, B, 'void(inferred)', 5) is None
        # Invalid params go through the same deferred method validation.
        reply = s.reply(s.send('textDocument/typeDefinition', {'position': {'line': 0, 'character': 0}}))
        assert reply['error']['code'] == -32602, reply
        s.shutdown()
    except BaseException:
        print(json.dumps(s.transcript, indent=2), file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='type-definition-', dir=exe.parent) as work:
        run(exe, Path(work))
    print('protocol type definition: UTF-16, stale edits, no project, parse errors, worker replacement passed')


if __name__ == '__main__':
    main()
