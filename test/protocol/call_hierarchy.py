#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Call hierarchy over stdio: definition's snapshot rules, items that later
requests resolve against the current snapshot, method-body fallback, and the
retained calls after worker replacement."""
import json
from pathlib import Path
import sys
import tempfile

import worker

A = ('/* é𝄞\t */ component Math {\n'
     '\tdef square(x: int) -> int { return x * x; }\n'
     '\tdef sumSquares(a: int, b: int) -> int { /* é𝄞\t */ return square(a) + square(b); }\n'
     '}\n')
B = ('component Use {\n'
     '\tdef run() -> int { /* é𝄞\t */ return Math.sumSquares(1, 2) + Math.square(3); }\n'
     '}\n'
     '/* é𝄞\t */ class Counter {\n'
     '\tvar n: int;\n'
     '\tnew(start: int) { n = start; }\n'
     '\tdef bump() -> int { return n++; }\n'
     '}\n'
     'component Make {\n'
     '\tdef counter = Counter.new(4);\n'
     '\tdef go() -> int { return counter.bump(); }\n'
     '}\n'
     'component Values {\n'
     '\tdef make() -> (int -> int) { return fun (x: int) => x; }\n'
     '\tdef f = make();\n'
     '\tdef use() { f(1); }\n'
     '}\n')
KINDS = {'method': 6, 'field': 8, 'constructor': 9}


def at(text, offset):
    prefix = text[:offset]
    return {'line': prefix.count('\n'),
            'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def position(text, needle, skip=0):
    return at(text, text.index(needle) + skip)


def span(text, begin, end):
    return {'start': at(text, begin), 'end': at(text, end)}


# The item of {name} in {container} in {text} at {path}: its declaration's
# range begins with {begin}, which contains its name, and ends with the first
# {end} after it. A client that declares no SymbolKinds gets each kind as it
# is.
def item(path, text, container, begin, name, end, detail, kind='method'):
    b = text.index(begin)
    n = b + begin.rindex(name)
    e = text.index(end, b) + len(end)
    data = {'uri': path.as_uri(), 'position': at(text, n), 'name': name, 'kind': kind, 'container': container}
    result = {'name': name, 'kind': KINDS[kind], 'uri': path.as_uri(), 'range': span(text, b, e),
              'selectionRange': span(text, n, n + len(name)), 'data': data}
    if detail is not None:
        result['detail'] = detail
    return result


# A call from or to {other}, whose callee names begin at each of {needles},
# (text, skip) pairs in {text}, and are {length} long.
def call(end, other, text, length, *needles):
    ranges = []
    for needle, skip in needles:
        b = text.index(needle) + skip
        ranges.append(span(text, b, b + length))
    return {end: other, 'fromRanges': ranges}


def open_file(s, path, text):
    s.send('textDocument/didOpen', {'textDocument': {
        'uri': path.as_uri(), 'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)


def change(s, path, text, version):
    s.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': version},
                                    'contentChanges': [{'text': text}]}, request=False)


def prepare(s, path, text, needle, skip=0):
    return s.result(s.send('textDocument/prepareCallHierarchy', {
        'textDocument': {'uri': path.as_uri()}, 'position': position(text, needle, skip)}))


def incoming(s, it):
    return s.result(s.send('callHierarchy/incomingCalls', {'item': it}))


def outgoing(s, it):
    return s.result(s.send('callHierarchy/outgoingCalls', {'item': it}))


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
        {'name': 'math', 'sources': ['A.v3', 'B.v3']}]}))
    # Retire every completed worker. A second, unrelated analysis launches
    # and replaces another worker while the context keeps its first snapshot.
    s = worker.Session(exe, '--worker-max-analyses=1')
    try:
        caps = s.result(s.send('initialize', {'rootUri': root.as_uri()}))['capabilities']
        assert caps['callHierarchyProvider'] is True
        open_file(s, a, A)
        square = item(a, A, 'Math', 'def square', 'square', '; }', 'def square(x: int) -> int')
        sum_squares = item(a, A, 'Math', 'def sumSquares', 'sumSquares', '; }', 'def sumSquares(a: int, b: int) -> int')
        use_run = item(b, B, 'Use', 'def run', 'run', '; }', 'def run() -> int')
        # UTF-16 ranges after non-ASCII text, from a use in the open file, and
        # the callers in it and in a closed one, in byte order of path.
        assert prepare(s, a, A, 'square(a)', 0) == [square]
        assert incoming(s, square) == [call('from', sum_squares, A, 6, ('square(a)', 0), ('square(b)', 0)),
                                       call('from', use_run, B, 6, ('Math.square', 5))]
        assert 'after 1 analyses (limit 1)' in s.logs(), s.logs()
        s.result(s.analyze([extra.as_uri()]))
        assert s.logs().count('after 1 analyses (limit 1)') >= 2, s.logs()
        # From a closed file's item, after the worker was replaced: callees in
        # byte order of path and then by offset.
        assert prepare(s, b, B, 'def run', 4) == [use_run]
        assert outgoing(s, use_run) == [call('to', square, B, 6, ('Math.square', 5)),
                                        call('to', sum_squares, B, 10, ('Math.sumSquares', 5))]
        assert outgoing(s, square) == []
        assert incoming(s, use_run) == []
        # A construction calls an explicit constructor, and a field's
        # initializer is its caller, which expands in turn.
        new = item(b, B, 'Counter', 'new(start', 'new', '; }', 'new(start: int)', 'constructor')
        counter = item(b, B, 'Make', 'def counter', 'counter', ';', 'def counter: Counter', 'field')
        assert prepare(s, b, B, 'Counter.new', 8) == [new]
        assert incoming(s, new) == [call('from', counter, B, 3, ('Counter.new', 8))]
        assert outgoing(s, counter) == [call('to', new, B, 3, ('Counter.new', 8))]
        maker = item(b, B, 'Values', 'def make', 'make', '; }', 'def make() -> int -> int')
        f = item(b, B, 'Values', 'def f', 'f', ';', 'def f: int -> int', 'field')
        use = item(b, B, 'Values', 'def use', 'use', '; }', 'def use()')
        assert incoming(s, maker) == [call('from', f, B, 4, ('= make()', 2))]
        assert incoming(s, f) == []
        assert outgoing(s, f) == [call('to', maker, B, 4, ('= make()', 2))]
        assert outgoing(s, use) == []
        assert prepare(s, b, B, 'f(1)') is None
        # Other names, a function's field, and nothing at all.
        for needle, skip in (('component Use', 10), ('class Counter', 6), ('def counter', 4), ('counter.bump', 0),
                             ('var n', 4), ('return', 1), ('é𝄞\t */ class Counter', 1)):
            assert prepare(s, b, B, needle, skip) is None, needle
        # A stale snapshot with an analysis pending waits for the new one.
        # square and sumSquares have moved, so their earlier items name
        # nothing, and items prepared again name them.
        added = '// é𝄞 moved\n' + A
        change(s, a, added, 2)
        assert incoming(s, square) is None
        assert outgoing(s, sum_squares) is None
        square2 = item(a, added, 'Math', 'def square', 'square', '; }', 'def square(x: int) -> int')
        sum2 = item(a, added, 'Math', 'def sumSquares', 'sumSquares', '; }', 'def sumSquares(a: int, b: int) -> int')
        assert prepare(s, a, added, 'def square', 4) == [square2]
        assert incoming(s, square2) == [call('from', sum2, added, 6, ('square(a)', 0), ('square(b)', 0)),
                                        call('from', use_run, B, 6, ('Math.square', 5))]
        # Method-body fallback: calls in the excluded body are in neither
        # direction, the open file's items have no details, since its
        # analysis substituted a body, and a position inside the body gets
        # null.
        broken = added.replace('/* é𝄞\t */ return square(a)', '/* é𝄞\t */ var bad = ;\n\t\treturn square(a)')
        change(s, a, broken, 3)
        square3 = item(a, broken, 'Math', 'def square', 'square', '; }', None)
        assert incoming(s, square2) == [call('from', use_run, B, 6, ('Math.square', 5))]
        assert len(substitutions(s)) == 1
        assert outgoing(s, sum2) == []
        assert outgoing(s, use_run) == [call('to', square3, B, 6, ('Math.square', 5)),
                                        call('to', item(a, broken, 'Math', 'def sumSquares', 'sumSquares', '; }', None), B, 10,
                                             ('Math.sumSquares', 5))]
        assert prepare(s, a, broken, 'var bad', 4) is None
        assert prepare(s, a, broken, 'square(a)', 0) is None
        # A parse error outside any method body suppresses every answer.
        change(s, a, added + 'class Broken {\n', 4)
        assert prepare(s, a, added, 'def square', 4) is None
        assert incoming(s, square2) is None
        assert not substitutions(s)
        # Once the declaration is replaced by a field of the same name, its
        # item names nothing, and a call of the field, which verification
        # rejects, is no edge.
        replaced = (added.replace('def square(x: int) -> int { return x * x; }', 'def square = 4;')
                    .replace('square(a) + square(b)', 'square + a + b'))
        change(s, a, replaced, 5)
        assert incoming(s, square2) is None
        assert outgoing(s, square2) is None
        assert outgoing(s, use_run) == [call('to', item(a, replaced, 'Math', 'def sumSquares', 'sumSquares', '; }',
                                                        'def sumSquares(a: int, b: int) -> int'), B, 10, ('Math.sumSquares', 5))]
        # A document in no project.
        open_file(s, loose, B)
        assert prepare(s, loose, B, 'def run', 4) is None
        assert incoming(s, dict(use_run, data=dict(use_run['data'], uri=loose.as_uri()))) is None
        # Another container names nothing.
        assert outgoing(s, dict(use_run, data=dict(use_run['data'], container='Make'))) is None
        # Invalid params go through the same deferred method validation:
        # data that names no valid document, position, name, kind, or
        # container.
        invalid(s, 'textDocument/prepareCallHierarchy', {'position': {'line': 0, 'character': 0}})
        invalid(s, 'callHierarchy/incomingCalls', {})
        for bad in (None, 'run', {}, dict(use_run['data'], uri='file://host/B.v3'), dict(use_run['data'], uri=''),
                    dict(use_run['data'], position={'line': -1, 'character': 0}), dict(use_run['data'], name=None),
                    dict(use_run['data'], kind='class'), dict(use_run['data'], kind=None),
                    dict(use_run['data'], container=None), dict(use_run['data'], container=1),
                    {k: v for k, v in use_run['data'].items() if k != 'container'}):
            invalid(s, 'callHierarchy/outgoingCalls', {'item': dict(use_run, data=bad)})
        s.shutdown()
    except BaseException:
        print(json.dumps(s.transcript, indent=2), file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='call-hierarchy-', dir=exe.parent) as work:
        run(exe, Path(work))
    print('protocol call hierarchy: UTF-16, open and closed files, constructors, field initializers, stale edits, moved and replaced declarations, fallback, parse errors, no project, worker replacement, invalid params passed')


if __name__ == '__main__':
    main()
