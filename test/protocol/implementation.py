#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Implementation navigation over stdio: definition's snapshot rules, method-body
fallback, and the retained hierarchy after worker replacement."""
import json
from pathlib import Path
import sys
import tempfile

import worker

A = ('/* é𝄞\t */ class Shape {\n'
     '\tdef area() -> int { return 0; }\n'
     '}\n'
     '/* é𝄞\t */ class Square extends Shape {\n'
     '\tdef area() -> int { return 1; }\n'
     '}\n')
B = ('component C {\n'
     '\tvar count: int;\n'
     '\tdef total(s: Shape, q: Square) -> int { /* é𝄞\t */ return s.area() + q.area() + "abc".length; }\n'
     '}\n'
     '/* é𝄞\t */ class Circle extends Shape {\n'
     '\tdef area() -> int { return 2; }\n'
     '}\n')


def at(text, offset):
    prefix = text[:offset]
    return {'line': prefix.count('\n'),
            'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def position(text, needle, skip=0):
    return at(text, text.index(needle) + skip)


# The Location of {name}, the first in {needle}, in {text} at {path}.
def location(path, text, needle, name):
    begin = text.index(needle) + needle.index(name)
    return {'uri': path.as_uri(), 'range': {'start': at(text, begin), 'end': at(text, begin + len(name))}}


def open_file(s, path, text):
    s.send('textDocument/didOpen', {'textDocument': {
        'uri': path.as_uri(), 'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)


def change(s, path, text, version):
    s.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': version},
                                    'contentChanges': [{'text': text}]}, request=False)


def query(s, path, text, needle, skip=0):
    return s.result(s.send('textDocument/implementation', {
        'textDocument': {'uri': path.as_uri()}, 'position': position(text, needle, skip)}))


def substitutions(s):
    value = s.result(s.send('virgil-lsp/snapshot'))
    return [v for v in value['documentVersions'] if 'substitution' in v]


class Session(worker.Session):
    # A failed automatic analysis warns the client once.
    NOTIFICATIONS = ("textDocument/publishDiagnostics", "window/showMessage")


def run(exe, root):
    a, b, extra, loose = (root / name for name in ('A.v3', 'B.v3', 'Extra.v3', 'Loose.v3'))
    for path, text in ((a, A), (b, B), (extra, 'class Extra { }\n'), (loose, B)):
        path.write_text(text)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'shapes', 'sources': ['A.v3', 'B.v3']}]}))
    # Retire every completed worker. A second, unrelated analysis launches
    # and replaces another worker while the context keeps its first snapshot.
    s = Session(exe, '--worker-max-analyses=1')
    try:
        caps = s.result(s.send('initialize', {'rootUri': root.as_uri()}))['capabilities']
        assert caps['implementationProvider'] is True
        open_file(s, b, B)
        square = location(a, A, 'class Square', 'Square')
        circle = location(b, B, 'class Circle', 'Circle')
        # In byte order of path and then by offset, after non-ASCII text, in
        # an open file and a closed one.
        assert query(s, b, B, 's: Shape', 3) == [square, circle]
        assert 'after 1 analyses (limit 1)' in s.logs(), s.logs()
        s.result(s.analyze([extra.as_uri()]))
        assert s.logs().count('after 1 analyses (limit 1)') >= 2, s.logs()
        assert query(s, a, A, 'class Shape', 6) == [square, circle]
        areas = [location(a, A, 'def area() -> int { return 1', 'area'), location(b, B, 'def area() -> int { return 2', 'area')]
        assert query(s, b, B, 's.area()', 2) == areas
        assert query(s, a, A, 'def area() -> int { return 0', 4) == areas
        # Never the declaration itself; an overriding method that nothing
        # overrides, other names, and a builtin give an empty list.
        assert query(s, a, A, 'class Square', 6) == []
        assert query(s, b, B, 'q.area()', 2) == []
        for needle, skip in (('var count', 4), ('s: Shape', 0), ('component C', 10), ('.length', 1)):
            assert query(s, b, B, needle, skip) == [], needle
        # Nothing selected: a comment.
        assert query(s, b, B, 'é𝄞\t */ class Circle', 1) is None
        # A stale snapshot with an analysis pending waits for the new one.
        added = B + 'class Hexagon extends Square { }\n'
        change(s, b, added, 2)
        assert query(s, a, A, 'class Square', 6) == [location(b, added, 'class Hexagon', 'Hexagon')]
        # Method-body fallback: a query outside the excluded body maps every
        # location to the current text, and one inside the body gets null.
        broken = added.replace('/* é𝄞\t */ return s.area()', 'var bad = ;\n\t\t/* é𝄞\t */ return s.area()')
        change(s, b, broken, 3)
        moved = [square, location(b, broken, 'class Circle', 'Circle'), location(b, broken, 'class Hexagon', 'Hexagon')]
        moved.sort(key=lambda l: (l['uri'], l['range']['start']['line'], l['range']['start']['character']))
        assert query(s, a, A, 'class Shape', 6) == moved
        assert len(substitutions(s)) == 1
        assert query(s, b, broken, 'extends Shape', 8) == moved
        assert query(s, b, broken, 's.area()', 2) is None
        # A parse error outside any method body suppresses the answer.
        change(s, b, added + 'class Broken {\n', 4)
        assert query(s, a, A, 'class Shape', 6) is None
        assert not substitutions(s)
        change(s, b, added, 5)
        assert query(s, a, A, 'class Square', 6) == [location(b, added, 'class Hexagon', 'Hexagon')]
        # A failed analysis leaves the snapshot stale with nothing pending:
        # null at once, until a later edit analyzes again.
        crash = added + 'def x: i32 = 0i;\n'
        change(s, b, crash, 6)
        assert query(s, a, A, 'class Shape', 6) is None
        assert 'the analysis worker crashed' in s.logs(), s.logs()
        assert query(s, a, A, 'class Square', 6) is None
        change(s, b, added, 7)
        assert query(s, a, A, 'class Square', 6) == [location(b, added, 'class Hexagon', 'Hexagon')]
        # A document in no project.
        open_file(s, loose, B)
        assert query(s, loose, B, 's: Shape', 3) is None
        # Invalid params go through the same deferred method validation.
        reply = s.reply(s.send('textDocument/implementation', {'position': {'line': 0, 'character': 0}}))
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
    with tempfile.TemporaryDirectory(prefix='implementation-', dir=exe.parent) as work:
        run(exe, Path(work))
    print('protocol implementation: UTF-16, stale edits, fallback, parse errors, no project, worker replacement passed')


if __name__ == '__main__':
    main()
