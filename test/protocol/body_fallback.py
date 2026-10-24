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
        c.change(BROKEN)
        snap = c.snapshot()
        sub, = substitutions(snap)
        assert sub['uri'] == c.uri('A.v3') and sub['version'] == 2, sub
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
        assert highlights == [{'range': loc['range'], 'kind': 1} for loc in refs[:2]], highlights
        for method in ('definition', 'hover', 'references', 'documentHighlight'):
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
        assert c.query('hover') is None
        assert c.diagnostics('B.v3') == []
        # Repair restores the ordinary complete reference set without restart.
        c.change(A)
        assert not substitutions(c.snapshot())
        assert len(c.query('references')) == 4
        # Project development capture uses the same policy; explicit URIs keep
        # their exact-text path and must never acquire a substitution.
        c.change(BROKEN)
        c.snapshot()
        result = c.s.result(c.s.send('virgil-lsp/analyze', {'uri': c.uri('A.v3')}))
        assert len(substitutions(result)) == 1
        result = c.s.result(c.s.analyze([c.uri('A.v3'), c.uri('B.v3')]))
        assert not substitutions(result) and any(d['kind'] == 'ParseError' for d in result['diagnostics'])
        c.close()
    finally:
        c.s.close()


def unavailable(exe, root, closed):
    c = Case(exe, root, A if closed else BROKEN)
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


def main():
    exe = Path(sys.argv[1]).resolve()
    # Keep disposable projects beside the build, away from the shared /tmp.
    with tempfile.TemporaryDirectory(prefix='body-fallback-', dir=exe.parent) as work:
        for name, run in (('warm', warm), ('error-limit', error_limit), ('cold', lambda e, p: unavailable(e, p, False)),
                          ('closed', lambda e, p: unavailable(e, p, True))):
            root = Path(work).resolve() / name
            root.mkdir()
            run(exe, root)
    print('body fallback: warm, cold, closed-file, mapped queries, diagnostics, and edit refusals passed')


if __name__ == '__main__':
    main()
