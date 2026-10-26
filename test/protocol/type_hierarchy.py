#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Type hierarchy over stdio: definition's snapshot rules, items that later
requests resolve against the current snapshot, method-body fallback, and the
retained hierarchy after worker replacement."""
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
     '\tdef total(s: Shape, q: Square, t: Tree) -> int { /* é𝄞\t */ return s.area() + q.area(); }\n'
     '}\n'
     '/* é𝄞\t */ class Circle extends Shape {\n'
     '\tdef area() -> int { return 2; }\n'
     '}\n'
     '/* é𝄞\t */ type Tree {\n'
     '\tcase Leaf;\n'
     '\tcase Node(left: Tree) { }\n'
     '}\n')


def at(text, offset):
    prefix = text[:offset]
    return {'line': prefix.count('\n'),
            'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def position(text, needle, skip=0):
    return at(text, text.index(needle) + skip)


def span(text, begin, end):
    return {'start': at(text, begin), 'end': at(text, end)}


# The item of {name} in {text} at {path}: its declaration's range begins
# with {begin}, which contains its name, and ends with the first {end} after
# it. A client that declares no SymbolKinds gets a case as Constant (14).
def item(path, text, begin, name, end, detail, variant=None):
    b = text.index(begin)
    n = b + begin.index(name)
    e = text.index(end, b) + len(end)
    data = {'uri': path.as_uri(), 'position': at(text, n), 'name': name, 'kind': 'case' if variant else 'variant' if begin.startswith('type') else 'class'}
    if variant:
        data['variant'] = variant
    result = {'name': name, 'kind': 14 if variant else 5, 'uri': path.as_uri(), 'range': span(text, b, e),
              'selectionRange': span(text, n, n + len(name)), 'data': data}
    if detail is not None:
        result['detail'] = detail
    return result


def open_file(s, path, text):
    s.send('textDocument/didOpen', {'textDocument': {
        'uri': path.as_uri(), 'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)


def change(s, path, text, version):
    s.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': version},
                                    'contentChanges': [{'text': text}]}, request=False)


def prepare(s, path, text, needle, skip=0):
    return s.result(s.send('textDocument/prepareTypeHierarchy', {
        'textDocument': {'uri': path.as_uri()}, 'position': position(text, needle, skip)}))


def supertypes(s, it):
    return s.result(s.send('typeHierarchy/supertypes', {'item': it}))


def subtypes(s, it):
    return s.result(s.send('typeHierarchy/subtypes', {'item': it}))


def substitutions(s):
    value = s.result(s.send('virgil-lsp/snapshot'))
    return [v for v in value['documentVersions'] if 'substitution' in v]


def invalid(s, method, params):
    reply = s.reply(s.send(method, params))
    assert reply['error']['code'] == -32602, reply


def run(exe, root):
    a, b, extra, loose = (root / name for name in ('A.v3', 'B.v3', 'Extra.v3', 'Loose.v3'))
    for path, text in ((a, A), (b, B), (extra, 'class Extra { }\n'), (loose, B)):
        path.write_text(text)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'shapes', 'sources': ['A.v3', 'B.v3']}]}))
    # Retire every completed worker. A second, unrelated analysis launches
    # and replaces another worker while the context keeps its first snapshot.
    s = worker.Session(exe, '--worker-max-analyses=1')
    try:
        caps = s.result(s.send('initialize', {'rootUri': root.as_uri()}))['capabilities']
        assert caps['typeHierarchyProvider'] is True
        open_file(s, a, A)
        shape = item(a, A, 'class Shape', 'Shape', '\n}', 'class Shape')
        square = item(a, A, 'class Square', 'Square', '\n}', 'class Square extends Shape')
        circle = item(b, B, 'class Circle', 'Circle', '\n}', 'class Circle extends Shape')
        tree = item(b, B, 'type Tree', 'Tree', '\n}', 'type Tree')
        # UTF-16 ranges after non-ASCII text, from a use in a closed file, of
        # a declaration in an open one, and the subtypes in both, in byte
        # order of path.
        assert prepare(s, b, B, 's: Shape', 3) == [shape]
        assert subtypes(s, shape) == [square, circle]
        assert 'after 1 analyses (limit 1)' in s.logs(), s.logs()
        s.result(s.analyze([extra.as_uri()]))
        assert s.logs().count('after 1 analyses (limit 1)') >= 2, s.logs()
        assert supertypes(s, circle) == [shape]
        assert supertypes(s, shape) == []
        assert prepare(s, a, A, 'class Square', 7) == [square]
        assert subtypes(s, square) == []
        # A variant's cases, and a case's variant.
        leaf = item(b, B, 'case Leaf', 'Leaf', ';', 'case Leaf', 'Tree')
        node = item(b, B, 'case Node', 'Node', '{ }', 'case Node(left: Tree)', 'Tree')
        assert prepare(s, b, B, 't: Tree', 3) == [tree]
        assert subtypes(s, tree) == [leaf, node]
        assert supertypes(s, node) == [tree]
        # Other names, a builtin, and nothing at all.
        for needle, skip in (('component C', 10), ('def total', 4), ('s.area', 2), ('return', 1), ('é𝄞\t */ class Circle', 1)):
            assert prepare(s, b, B, needle, skip) is None, needle
        # A stale snapshot with an analysis pending waits for the new one.
        # Square and Shape have moved, so their earlier items name nothing,
        # and items prepared again name them.
        added = 'class Kite extends Square { }\n' + A
        change(s, a, added, 2)
        assert subtypes(s, square) is None
        assert supertypes(s, shape) is None
        moved = item(a, added, 'class Square', 'Square', '\n}', 'class Square extends Shape')
        shape2 = item(a, added, 'class Shape', 'Shape', '\n}', 'class Shape')
        assert prepare(s, a, added, 'class Square', 6) == [moved]
        assert subtypes(s, moved) == [item(a, added, 'class Kite', 'Kite', '{ }', 'class Kite extends Square')]
        assert supertypes(s, moved) == [shape2]
        assert subtypes(s, shape2) == [moved, circle]
        # Method-body fallback: the open file's declarations have no details,
        # since its analysis substituted a body; Square's range ends past the
        # excluded body, mapped to current text; and a position inside the
        # body gets null.
        broken = added.replace('return 1;', 'var bad = ;\n\t\treturn 1;')
        change(s, a, broken, 3)
        assert subtypes(s, shape2) == [item(a, broken, 'class Square', 'Square', '\n}', None), circle]
        assert len(substitutions(s)) == 1
        assert prepare(s, a, broken, 'class Square', 6) == [item(a, broken, 'class Square', 'Square', '\n}', None)]
        assert prepare(s, a, broken, 'Kite extends Square', 15) == [item(a, broken, 'class Square', 'Square', '\n}', None)]
        assert prepare(s, a, broken, 'var bad', 4) is None
        # A parse error outside any method body suppresses every answer.
        change(s, a, added + 'class Broken {\n', 4)
        assert prepare(s, a, added, 'class Shape', 6) is None
        assert subtypes(s, shape2) is None
        assert not substitutions(s)
        # Once the declaration is deleted, its item names nothing; another
        # kind of declaration of the same name is not it.
        change(s, a, A.replace('class Square extends Shape', 'component Square'), 5)
        assert supertypes(s, square) is None
        assert subtypes(s, shape) == [circle]
        # A document in no project.
        open_file(s, loose, B)
        assert prepare(s, loose, B, 's: Shape', 3) is None
        assert subtypes(s, dict(circle, data=dict(circle['data'], uri=loose.as_uri()))) is None
        # Invalid params go through the same deferred method validation:
        # data that names no valid document, position, name, or kind.
        invalid(s, 'textDocument/prepareTypeHierarchy', {'position': {'line': 0, 'character': 0}})
        invalid(s, 'typeHierarchy/supertypes', {})
        for bad in (None, 'Circle', {}, dict(circle['data'], uri='file://host/B.v3'), dict(circle['data'], uri=''), dict(circle['data'], position={'line': -1, 'character': 0}),
                    dict(circle['data'], name=None), dict(circle['data'], kind='enum'), dict(circle['data'], variant=1)):
            invalid(s, 'typeHierarchy/subtypes', {'item': dict(circle, data=bad)})
        s.shutdown()
    except BaseException:
        print(json.dumps(s.transcript, indent=2), file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='type-hierarchy-', dir=exe.parent) as work:
        run(exe, Path(work))
    print('protocol type hierarchy: UTF-16, open and closed files, stale edits, moved and deleted declarations, fallback, parse errors, no project, worker replacement, invalid params passed')


if __name__ == '__main__':
    main()
