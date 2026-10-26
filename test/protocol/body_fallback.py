#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Method-body fallback over real stdio, including current UTF-16 coordinates."""
import json
from pathlib import Path
import sys
import tempfile

from worker import Session


A = '''/* é😀\t */ component A {
\tvar field = 1;
\tdef broken() -> int { return target(); }
\t/* é😀\t */ def target() -> int { return 1; }
\tdef elsewhere() -> int { /* é😀\t */ return target(); }
\tdef wrong() -> int { /* é😀\t */ return "bad"; }
}
'''
B = '''component B {
\tdef use() -> int { return A.target(); }
\tdef wrong() -> int { return "bad"; }
}
'''
BROKEN = A.replace('return target();', 'var bad = ;\n\t\t/* é😀\t */ return target();', 1)


def position(text, offset):
    prefix = text[:offset]
    return {'line': prefix.count('\n'),
            'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def span(text, needle, start=0):
    at = text.index(needle, start)
    return {'start': position(text, at), 'end': position(text, at + len(needle))}


class Case:
    def __init__(self, exe, root, a=A):
        self.root = root
        self.texts = {'A.v3': a, 'B.v3': B}
        self.versions = {name: 1 for name in self.texts}
        for name, text in self.texts.items():
            (root / name).write_text(text)
        (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
            {'name': 'p', 'sources': ['A.v3', 'B.v3']}]}))
        self.s = Session(exe)
        self.s.result(self.s.send('initialize', {'rootUri': root.as_uri(), 'capabilities': {
            'workspace': {'workspaceEdit': {'documentChanges': True}},
            'textDocument': {'rename': {'prepareSupport': True},
                             'hover': {'contentFormat': ['plaintext']}}}}))
        self.notify('initialized', {})

    def uri(self, name):
        return (self.root / name).as_uri()

    def notify(self, method, params):
        self.s.send(method, params, request=False)

    def open(self, name):
        self.notify('textDocument/didOpen', {'textDocument': {
            'uri': self.uri(name), 'languageId': 'virgil', 'version': 1, 'text': self.texts[name]}})

    def change(self, text):
        self.versions['A.v3'] += 1
        self.texts['A.v3'] = text
        self.notify('textDocument/didChange', {'textDocument': {
            'uri': self.uri('A.v3'), 'version': self.versions['A.v3']}, 'contentChanges': [{'text': text}]})

    def params(self, name, needle, **extra):
        return {'textDocument': {'uri': self.uri(name)},
                'position': span(self.texts[name], needle)['start'], **extra}

    def query(self, method, name='B.v3', needle='target', **extra):
        if method == 'references':
            extra['context'] = {'includeDeclaration': True}
        return self.s.result(self.s.send('textDocument/' + method, self.params(name, needle, **extra)))

    def snapshot(self):
        # A navigation reply waits for its generation's analysis and publications.
        self.query('definition')
        value = self.s.result(self.s.send('virgil-lsp/snapshot'))
        assert value['projectConfiguration']['current'], value
        assert all(v['current'] for v in value['documentVersions']), value
        return value

    def symbols(self, query):
        return self.s.result(self.s.send('workspace/symbol', {'query': query}))

    def diagnostics(self, name):
        publications = [m['params'] for direction, m in self.s.transcript if direction == 'recv'
                        and m.get('method') == 'textDocument/publishDiagnostics'
                        and m['params']['uri'] == self.uri(name)]
        return publications[-1]['diagnostics']

    def close(self):
        self.s.shutdown()


def substitutions(snapshot):
    return [v for v in snapshot['documentVersions'] if 'substitution' in v]


def warm(exe, root):
    c = Case(exe, root)
    try:
        c.open('A.v3')
        c.open('B.v3')
        initial = c.snapshot()
        assert not substitutions(initial)
        target, = c.symbols('target')
        assert target['name'] == 'target' and target['location'] == {
            'uri': c.uri('A.v3'), 'range': span(A, 'target', A.index('def target'))}, target
        c.change(BROKEN)
        snap = c.snapshot()
        assert snap['generation'] == initial['generation'] + 1, (initial, snap)
        sub, = substitutions(snap)
        assert sub['uri'] == c.uri('A.v3') and sub['version'] == 2, sub
        assert c.symbols('target') == []
        symbols = c.symbols('')
        assert symbols and all(s['location']['uri'] == c.uri('B.v3') for s in symbols), symbols
        use, = c.symbols('use')
        assert use['location'] == {'uri': c.uri('B.v3'), 'range': span(B, 'use')}, use
        before = c.query('hover', 'A.v3', 'broken')
        assert before == {'contents': {'kind': 'plaintext', 'value': 'def broken() -> int'},
                          'range': span(BROKEN, 'broken')}, before
        expected = {'uri': c.uri('A.v3'), 'range': span(BROKEN, 'target', BROKEN.index('def target'))}
        assert c.query('definition') == expected
        assert c.query('definition', 'A.v3', 'target(); }\n\tdef wrong') == expected
        hover = c.query('hover')
        assert hover == {'contents': {'kind': 'plaintext', 'value': 'def target() -> int'},
                         'range': span(B, 'target')}, hover
        hover = c.query('hover', 'A.v3', 'target(); }\n\tdef wrong')
        assert hover['range'] == span(BROKEN, 'target', BROKEN.index('def elsewhere'))
        refs = [expected, {'uri': c.uri('A.v3'), 'range': hover['range']},
                {'uri': c.uri('B.v3'), 'range': span(B, 'target')}]
        assert c.query('references') == refs
        highlights = c.query('documentHighlight', 'A.v3', 'target() ->')
        assert highlights == [{'range': loc['range'], 'kind': kind}
                              for loc, kind in zip(refs[:2], (1, 2))], highlights
        for method in ('definition', 'typeDefinition', 'hover', 'references', 'documentHighlight'):
            assert c.query(method, 'A.v3', 'target(); }\n\t/*') is None, method
        for name in ('A.v3', 'B.v3'):
            diagnostics = c.diagnostics(name)
            semantic = [d for d in diagnostics if d['code'] != 'ParseError']
            assert len(semantic) == 1 and semantic[0]['code'] == 'TypeError', diagnostics
            assert semantic[0]['range'] == span(c.texts[name], '"bad"'), semantic
        assert any(d['code'] == 'ParseError' for d in c.diagnostics('A.v3'))
        # Refuse all edits even at names outside the omitted body. No preflight
        # is submitted, and the degraded snapshot remains the retained result.
        for method in ('prepareRename', 'rename'):
            reply = c.s.reply(c.s.send('textDocument/' + method,
                                      c.params('B.v3', 'target', **({'newName': 'renamed'} if method == 'rename' else {}))))
            assert reply['error']['code'] == -32803 and 'substitutes a method body' in reply['error']['message'], reply
        assert c.query('signatureHelp') is None
        assert c.snapshot()['generation'] == snap['generation']
        # A second damaged generation still uses the original parseable text;
        # a header error then drops fallback and clears independent type errors.
        c.change(BROKEN.replace('var bad = ;', 'var bad = ; var more = ;'))
        assert len(substitutions(c.snapshot())) == 1
        c.change(A.replace('broken()', 'broken(x: )'))
        assert not substitutions(c.snapshot())
        assert c.query('definition') is None
        assert c.query('typeDefinition') is None
        assert c.query('hover') is None
        assert c.diagnostics('B.v3') == []
        # Repair restores the ordinary complete reference set without restart.
        c.change(A)
        assert not substitutions(c.snapshot())
        assert len(c.query('references')) == 4
        assert c.symbols('target') == [target]
        repaired = BROKEN.replace('var bad = ;', 'var bad = 1;')
        c.change(repaired)
        assert not substitutions(c.snapshot())
        moved, = c.symbols('target')
        assert moved['location'] == {
            'uri': c.uri('A.v3'), 'range': span(repaired, 'target', repaired.index('def target'))}, moved
        # Project development capture uses the same policy; explicit URIs keep
        # their exact-text path and must never acquire a substitution.
        c.change(BROKEN)
        c.snapshot()
        result = c.s.result(c.s.send('virgil-lsp/analyze', {'uri': c.uri('A.v3')}))
        assert len(substitutions(result)) == 1
        assert c.symbols('target') == []
        symbols = c.symbols('')
        assert symbols and all(s['location']['uri'] == c.uri('B.v3') for s in symbols), symbols
        result = c.s.result(c.s.analyze([c.uri('A.v3'), c.uri('B.v3')]))
        assert not substitutions(result) and any(d['kind'] == 'ParseError' for d in result['diagnostics'])
        c.close()
    finally:
        c.s.close()


def unterminated_regions(exe, root):
    old = A.replace('return target();', 'var x = 1; return target();', 1)
    c = Case(exe, root, old)
    try:
        c.open('A.v3')
        c.open('B.v3')
        assert c.query('definition') is not None
        for statement in ('var x = "oops\n;', "var x = 'a\n;", 'var x = /* oops\n;'):
            c.change(old.replace('var x = 1;', statement))
            assert not substitutions(c.snapshot()), statement
            assert c.query('definition') is None, statement
            assert c.query('typeDefinition') is None, statement
            assert c.diagnostics('B.v3') == [], statement
            c.change(old)
            assert c.query('definition') is not None
        c.close()
    finally:
        c.s.close()


def newer_features(exe, root):
    old = ('/* é😀\t */ class Before { }\n'
           'component A {\n'
           '\tvar field: Before = Before.new();\n'
           '\tdef broken() { var local = After.new(); field = Before.new(); }\n'
           '\tdef target() -> After { return After.new(); }\n'
           '\tdef elsewhere() -> After { /* é😀\t */ field = Before.new(); void(field); return target(); }\n'
           '}\n'
           '/* é😀\t */ class After { }\n')
    c = Case(exe, root, old)
    c.texts['B.v3'] = 'component B { def use() -> After { return A.target(); } }\n'
    try:
        c.open('A.v3')
        c.open('B.v3')
        c.snapshot()
        complete = c.query('documentHighlight', 'A.v3', 'field:')
        assert [h['kind'] for h in complete] == [1, 3, 3, 2], complete
        # Exercise both signs of the translation, including moved lines and
        # non-ASCII bytes before the same-file query and type destination.
        for replacement in ('var bad = ;', '/* é😀\t */ var bad = ;\n' * 8):
            current = old.replace('var local = After.new(); field = Before.new();', replacement)
            c.change(current)
            assert len(substitutions(c.snapshot())) == 1
            before = {'uri': c.uri('A.v3'), 'range': span(current, 'Before')}
            after = {'uri': c.uri('A.v3'), 'range': span(current, 'After', current.index('class After'))}
            assert c.query('typeDefinition', 'A.v3', 'field:') == before
            assert c.query('typeDefinition', 'A.v3', 'field =') == before
            assert c.query('typeDefinition', 'A.v3', 'target();') == after
            assert c.query('typeDefinition') == after
            # At a type name, definition and type definition agree exactly.
            for name, needle in (('A.v3', 'After { return'), ('B.v3', 'After')):
                assert c.query('typeDefinition', name, needle) == after
                assert c.query('definition', name, needle) == after
            assert c.query('typeDefinition', 'A.v3', 'broken()') is None
            assert c.query('typeDefinition', 'A.v3', 'bad =') is None
            assert c.query('definition', 'A.v3', 'bad =') is None
            # The removed write is absent; retained declaration/read/write
            # kinds use effective offsets even when current offsets shift.
            highlights = c.query('documentHighlight', 'A.v3', 'field:')
            assert highlights == [
                {'range': span(current, 'field'), 'kind': 1},
                {'range': span(current, 'field', current.index('def elsewhere')), 'kind': 3},
                {'range': span(current, 'field', current.index('void(field)')), 'kind': 2},
            ], highlights
        # Keep old type-bearing tokens in the broken body. Their cached
        # targets remain available internally but must never be returned.
        c.change(old.replace('var local', 'var syntax = ; var local'))
        assert len(substitutions(c.snapshot())) == 1
        for needle in ('local =', 'After.new(); field', 'field = Before.new(); }'):
            for method in ('definition', 'typeDefinition', 'documentHighlight'):
                assert c.query(method, 'A.v3', needle) is None, (method, needle)
        c.change(old.replace('broken()', 'broken(x: )'))
        assert not substitutions(c.snapshot())
        assert c.query('definition') is None
        assert c.query('typeDefinition') is None
        c.change(old)
        assert c.query('typeDefinition') == {'uri': c.uri('A.v3'), 'range': span(old, 'After', old.index('class After'))}
        assert c.query('documentHighlight', 'A.v3', 'field:') == complete
        c.close()
    finally:
        c.s.close()


def unavailable(exe, root, closed):
    # Current-text repair restores a cold open of BROKEN (current_repair.py),
    # but not of a broken header.
    c = Case(exe, root, A if closed else A.replace('broken()', 'broken(x: )'))
    try:
        if not closed:
            c.open('A.v3')
        c.open('B.v3')
        c.snapshot()
        if closed:
            (root / 'A.v3').write_text(BROKEN)
            c.notify('workspace/didChangeWatchedFiles', {'changes': [{'uri': c.uri('A.v3'), 'type': 2}]})
        assert not substitutions(c.snapshot())
        assert c.query('definition') is None
        assert c.query('typeDefinition') is None
        assert c.diagnostics('B.v3') == []
        c.close()
    finally:
        c.s.close()


def error_limit(exe, root):
    errors = ''.join('var x%d: int = true; ' % n for n in range(20))
    old = ('component A { def target() -> int { return 1; } '
           'def outside() -> int { return "bad"; } def broken() { ' + errors + '} }\n')
    c = Case(exe, root, old)
    try:
        c.open('A.v3')
        c.open('B.v3')
        c.snapshot()
        assert any(d['code'] == 'ErrorLimit' for d in c.diagnostics('A.v3'))
        c.change(old.replace(errors, 'var syntax = ; ' + errors))
        assert len(substitutions(c.snapshot())) == 1
        semantic = [d for d in c.diagnostics('A.v3') if d['code'] != 'ParseError']
        assert len(semantic) == 1 and semantic[0]['code'] == 'TypeError', semantic
        assert semantic[0]['range'] == span(c.texts['A.v3'], '"bad"')
        c.close()
    finally:
        c.s.close()


def excluded_error_limit(exe, root):
    # The excluded errors precede a visible last error in B. That last
    # location alone cannot prove the limit belongs to the current program.
    errors = ''.join('var x%d: int = true; ' % n for n in range(14))
    old = 'component A { def target() -> int { return 1; } def broken() { ' + errors + '} }\n'
    results = {}
    for name, text in (('fallback', old), ('oracle', old.replace(errors, 'var syntax = 1; '))):
        project = root / name
        project.mkdir()
        c = Case(exe, project, text)
        try:
            c.open('A.v3')
            c.open('B.v3')
            c.snapshot()
            if name == 'fallback':
                assert any(d['code'] == 'ErrorLimit' for d in c.diagnostics('B.v3'))
                c.change(old.replace(errors, 'var syntax = ; '))
                assert len(substitutions(c.snapshot())) == 1
            results[name] = c.diagnostics('B.v3')
            c.close()
        finally:
            c.s.close()
    assert [d['code'] for d in results['oracle']] == ['TypeError'], results
    assert results['fallback'] == results['oracle'], results


def current_error_limit(exe, root):
    # With no errors in the excluded body, a real current-program limit stays.
    old = 'component A { def target() -> int { return 1; } def broken() { var syntax = 1; } }\n'
    c = Case(exe, root, old)
    errors = ''.join('var x%d: int = true; ' % n for n in range(15))
    c.texts['B.v3'] = B.replace('return "bad";', errors + 'return 1;')
    try:
        c.open('A.v3')
        c.open('B.v3')
        c.snapshot()
        c.change(old.replace('var syntax = 1;', 'var syntax = ;'))
        assert len(substitutions(c.snapshot())) == 1
        assert [d['code'] for d in c.diagnostics('B.v3')] == ['TypeError'] * 15 + ['ErrorLimit']
        c.close()
    finally:
        c.s.close()


def large_cache(exe, root):
    # 4,000,063 source bytes, below the program cap. A reaches the parser's
    # error limit before B is read. Parsing B in cacheCompleted used to exhaust
    # the main server's heap (2755676), defeating worker isolation.
    a = '\r' * 16 + 'component A { }\n'
    b = 'component B { def huge() { ' + 'f();' * 1000000 + '} }\n'
    assert len(a) + len(b) == 4000063
    (root / 'A.v3').write_bytes(a.encode())
    (root / 'B.v3').write_text(b)
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'large', 'sources': ['A.v3', 'B.v3']}]}))
    s = Session(exe)
    try:
        s.result(s.send('initialize', {'rootUri': root.as_uri(), 'capabilities': {}}))
        s.send('initialized', {}, request=False)
        s.send('textDocument/didOpen', {'textDocument': {
            'uri': (root / 'A.v3').as_uri(), 'languageId': 'virgil', 'version': 1, 'text': a}}, request=False)
        assert s.result(s.send('textDocument/definition', {
            'textDocument': {'uri': (root / 'A.v3').as_uri()},
            'position': {'line': 0, 'character': 14}})) is None
        snapshot = s.result(s.send('virgil-lsp/snapshot'))
        assert not substitutions(snapshot), snapshot
        assert len(snapshot['diagnostics']) >= 15, snapshot
        assert all(d['path'] == str(root / 'A.v3') and d['kind'] == 'ParseError'
                   for d in snapshot['diagnostics']), snapshot
        s.ping()
        s.shutdown()
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    # Keep disposable projects beside the build, away from the shared /tmp.
    with tempfile.TemporaryDirectory(prefix='body-fallback-', dir=exe.parent) as work:
        for name, run in (('warm', warm), ('unterminated-regions', unterminated_regions),
                          ('newer-features', newer_features), ('error-limit', error_limit),
                          ('excluded-error-limit', excluded_error_limit), ('current-error-limit', current_error_limit),
                          ('large-cache', large_cache), ('cold', lambda e, p: unavailable(e, p, False)),
                          ('closed', lambda e, p: unavailable(e, p, True))):
            root = Path(work).resolve() / name
            root.mkdir()
            run(exe, root)
    print('body fallback: warm, cold, closed-file, mapped type definitions, highlight kinds, lexical rejection, error limits, large cache, and edit refusals passed')


if __name__ == '__main__':
    main()
