#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Current-text repair over real stdio: a cold open with a syntax error in a
method body, beside method-body fallback in another file."""
import json
from pathlib import Path
import sys
import tempfile

from body_fallback import position, span, substitutions
from worker import Session


SENTINEL = 'virgilLspCursor'
A = '''/* é😀\t */ component A {
\tdef broken() -> int { return target(); }
\t/* é😀\t */ def target() -> int { return 1; }
\tdef elsewhere() -> int { /* é😀\t */ return target(); }
\tdef wrong() -> int { /* é😀\t */ return "bad"; }
}
'''
# The repaired call has the wrong arity; its error stays inside the body.
BROKEN = A.replace('return target();', 'var bad = target(1, ;\n\t\t/* é😀\t */ return target();', 1)
REPAIRED = BROKEN.replace('target(1, ;', 'target(1,%s) ;' % SENTINEL, 1)
B = '''component B {
\tdef use() -> int { return A.target() + C.size(); }
\tdef wrong() -> int { return "bad"; }
}
'''
C = '''component C {
\tdef size() -> int { return 2; }
\tdef edit() -> int { var n = 1; return n + size(); }
}
'''
C_BROKEN = C.replace('var n = 1;', 'var n = ;')


class Case:
    def __init__(self, exe, root, texts):
        self.root, self.texts = root, dict(texts)
        self.versions = {name: 1 for name in texts}
        for name, text in texts.items():
            (root / name).write_text(text)
        (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
            {'name': 'p', 'sources': sorted(texts)}]}))
        self.s = Session(exe)
        self.s.result(self.s.send('initialize', {'rootUri': root.as_uri(), 'capabilities': {
            'workspace': {'workspaceEdit': {'documentChanges': True}},
            'textDocument': {'rename': {'prepareSupport': True},
                             'hover': {'contentFormat': ['plaintext']}}}}))
        self.s.send('initialized', {}, request=False)

    def uri(self, name):
        return (self.root / name).as_uri()

    def open(self, name, text=None):
        if text is not None:
            self.texts[name] = text
        self.s.send('textDocument/didOpen', {'textDocument': {
            'uri': self.uri(name), 'languageId': 'virgil', 'version': 1, 'text': self.texts[name]}}, request=False)

    def change(self, name, text):
        self.versions[name] += 1
        self.texts[name] = text
        self.s.send('textDocument/didChange', {'textDocument': {
            'uri': self.uri(name), 'version': self.versions[name]}, 'contentChanges': [{'text': text}]}, request=False)

    def params(self, name, needle, start=0, **extra):
        text = self.texts[name]
        return {'textDocument': {'uri': self.uri(name)},
                'position': position(text, text.index(needle, start)), **extra}

    def query(self, method, name='B.v3', needle='target', start=0, **extra):
        if method == 'references':
            extra['context'] = {'includeDeclaration': True}
        return self.s.result(self.s.send('textDocument/' + method, self.params(name, needle, start, **extra)))

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

    def semantic(self, name):
        return [d for d in self.diagnostics(name) if d['code'] != 'ParseError']


def location(c, name, needle, start=0):
    return {'uri': c.uri(name), 'range': span(c.texts[name], needle, start)}


def offset(text, needle, start=0):
    return len(text[:text.index(needle, start)].encode())


def cold(exe, root):
    c = Case(exe, root, {'A.v3': BROKEN, 'B.v3': B, 'C.v3': C})
    try:
        # Cold: the broken file is open from the start, so no analysis has
        # seen parseable text for it.
        c.open('A.v3')
        c.open('B.v3')
        c.open('C.v3')
        snap = c.snapshot()
        sub, = substitutions(snap)
        site = offset(BROKEN, 'target(1, ;') + len('target(1,')
        body = offset(BROKEN, '{ var bad')
        end = offset(BROKEN, '}', body) + 1
        assert sub['uri'] == c.uri('A.v3') and sub['substitution'] == {
            'begin': site, 'effectiveEnd': site + len(SENTINEL) + 1, 'currentEnd': site,
            'bodyBegin': body, 'bodyEnd': end}, sub
        generation = snap['generation']
        # Definition, hover, references, and highlights outside the body, in
        # this file and in another, map through the single interval.
        target = location(c, 'A.v3', 'target', BROKEN.index('def target'))
        assert c.query('definition') == target
        assert c.query('definition', 'A.v3', 'target(); }\n\tdef wrong') == target
        hover = c.query('hover')
        assert hover == {'contents': {'kind': 'plaintext', 'value': 'def target() -> int'},
                         'range': span(B, 'target')}, hover
        refs = [target, location(c, 'A.v3', 'target', BROKEN.index('def elsewhere')),
                location(c, 'B.v3', 'target')]
        assert c.query('references') == refs
        assert c.query('definition', 'B.v3', 'size') == location(c, 'C.v3', 'size')
        # Nothing inside the excluded body, including the repaired call and
        # its braces, answers.
        for needle in ('target(1', 'bad', '{ var bad', 'target(); }\n\t/*'):
            for method in ('definition', 'typeDefinition', 'hover', 'references', 'documentHighlight'):
                assert c.query(method, 'A.v3', needle) is None, (method, needle)
        # Semantic diagnostics outside the body remain; the repaired call's
        # arity error and the sentinel's are inside it.
        for name in ('A.v3', 'B.v3'):
            semantic = c.semantic(name)
            assert len(semantic) == 1 and semantic[0]['code'] == 'TypeError', semantic
            assert semantic[0]['range'] == span(c.texts[name], '"bad"'), semantic
        assert c.semantic('C.v3') == []
        assert any(d['code'] == 'ParseError' for d in c.diagnostics('A.v3'))
        # Rename and prepare rename refuse the repaired snapshot; workspace
        # symbols skip the repaired file.
        for method in ('prepareRename', 'rename'):
            reply = c.s.reply(c.s.send('textDocument/' + method,
                                      c.params('B.v3', 'target', **({'newName': 'renamed'} if method == 'rename' else {}))))
            assert reply['error']['code'] == -32803 and 'substitutes a method body' in reply['error']['message'], reply
        assert c.s.result(c.s.send('workspace/symbol', {'query': 'target'})) == []
        assert c.snapshot()['generation'] == generation
        # Fallback in C, beside the repair in A: C's cached parseable text is
        # substituted, and A is repaired again in the same single analysis.
        c.change('C.v3', C_BROKEN)
        snap = c.snapshot()
        assert snap['generation'] == generation + 1, snap
        subs = {v['uri']: v['substitution'] for v in substitutions(snap)}
        assert subs[c.uri('A.v3')]['currentEnd'] == site, subs
        n = offset(C_BROKEN, '= ;') + 2
        assert subs[c.uri('C.v3')] == {'begin': n, 'effectiveEnd': n + 1, 'currentEnd': n,
                                        'bodyBegin': offset(C_BROKEN, '{ var n'),
                                        'bodyEnd': offset(C_BROKEN, '}', offset(C_BROKEN, '{ var n')) + 1}, subs
        assert c.query('definition') == target
        assert c.query('definition', 'B.v3', 'size') == location(c, 'C.v3', 'size')
        assert c.query('definition', 'C.v3', 'size(); }') is None
        assert c.semantic('B.v3')[0]['range'] == span(B, '"bad"')
        # A later parseable change restores A's full answers; C keeps fallback.
        c.change('A.v3', A)
        snap = c.snapshot()
        assert [v['uri'] for v in substitutions(snap)] == [c.uri('C.v3')], snap
        assert c.query('definition', 'A.v3', 'target();') == location(c, 'A.v3', 'target', A.index('def target'))
        c.change('C.v3', C)
        assert not substitutions(c.snapshot())
        assert len(c.query('references')) == 4
        assert c.s.result(c.s.send('workspace/symbol', {'query': 'target'}))[0]['location'] == location(
            c, 'A.v3', 'target', A.index('def target'))
        # No answer or publication names the sentinel or its locations. The
        # development inspection lists the effective program's own diagnostics.
        inspections = {m['id'] for direction, m in c.s.transcript
                       if direction == 'send' and m.get('method') == 'virgil-lsp/snapshot'}
        leaked = [m for direction, m in c.s.transcript if direction == 'recv'
                  and m.get('id') not in inspections and SENTINEL in json.dumps(m)]
        assert not leaked, leaked
        c.s.shutdown()
    finally:
        c.s.close()


def refused(exe, root):
    # Repairs that would need a brace, or that lie in a header, a field
    # initializer, or two bodies, leave the program unanalyzed, as before.
    for current in (A.replace('return target(); }', 'return target();', 1),
                    A.replace('broken()', 'broken(x: )'),
                    A.replace('\tdef broken', '\tdef field: int = ;\n\tdef broken'),
                    BROKEN.replace('return "bad";', 'return ;.')):
        project = root / str(len(list(root.iterdir())))
        project.mkdir()
        c = Case(exe, project, {'A.v3': current, 'B.v3': B, 'C.v3': C})
        try:
            c.open('A.v3')
            c.open('B.v3')
            assert not substitutions(c.snapshot()), current
            assert c.query('definition') is None, current
            assert c.semantic('B.v3') == [], current
            c.s.shutdown()
        finally:
            c.s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    # Keep disposable projects beside the build, away from the shared /tmp.
    with tempfile.TemporaryDirectory(prefix='current-repair-', dir=exe.parent) as work:
        for name, run in (('cold', cold), ('refused', refused)):
            root = Path(work) / name
            root.mkdir()
            run(exe, root)
    print('current-text repair: cold open, repair beside fallback, suppression, rename refusal, '
          'restoration, and refusals passed')


if __name__ == '__main__':
    main()
