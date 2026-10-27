#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Current-text repair over real stdio: cold opens with a syntax error in a
method body, with an error the next line continues, with errors in two
bodies, and with a lost "{" and a lost "}", beside method-body fallback in
another file."""
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
# The broken call's arity is wrong too; the blanked body holds every error.
BROKEN = A.replace('return target();', 'var bad = target(1, ;\n\t\t/* é😀\t */ return target();', 1)
# The parser reads the next line as the rest of the member access, so its
# first error is on that line.
CONTINUED = A.replace('return target();', 'var n = target().\n\t\tif (n > 0) return n;\n\t\treturn target();', 1)
# A second body that fails to parse once the first is blanked.
TWO = BROKEN.replace('/* é😀\t */ return target(); }\n\tdef wrong', '/* é😀\t */ return target(). ; }\n\tdef wrong')
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
# The brace rules (ADR-0010 rules 2 and 3) read this layout: an if that lost
# its "{", and a method that lost its own "}", before which a "}" is inserted,
# since no statement can begin the private method after it.
BRACES = '''/* é😀\t */ component A {
\tdef broken() -> int {
\t\tif (target() > 0) {
\t\t\treturn target();
\t\t}
\t\treturn 0;
\t}
\tprivate def unused() { }
\tdef target() -> int { return 1; }
\tdef elsewhere() -> int { /* é😀\t */ return target(); }
\tdef wrong() -> int { /* é😀\t */ return "bad"; }
}
'''
LOST_OPENER = BRACES.replace('(target() > 0) {', '(target() > 0)')
LOST_CLOSER = BRACES.replace('\t\treturn 0;\n\t}\n', '\t\treturn 0;\n\t\n')


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


def body(text, needle):
    """The UTF-8 byte range of the body whose "{" starts {needle}; these
    bodies hold no other brace."""
    at = text.index(needle)
    return len(text[:at].encode()), len(text[:text.index('}', at) + 1].encode())


def blanked(*bodies):
    """The development inspection's record of blanked {bodies}, which move no
    offset: the interval spans the blanked bytes."""
    return {'begin': bodies[0][0] + 1, 'effectiveEnd': bodies[-1][1] - 1, 'currentEnd': bodies[-1][1] - 1,
            'bodies': [{'begin': b, 'end': e} for b, e in bodies]}


def prepare(c, name, needle, start=0):
    return c.s.result(c.s.send('textDocument/prepareCallHierarchy', c.params(name, needle, start)))


def calls(c, direction, item):
    return c.s.result(c.s.send('callHierarchy/%sCalls' % direction, {'item': item}))


def inside(c, needles):
    """Nothing inside an excluded body, including its braces, answers."""
    for needle in needles:
        for method in ('definition', 'typeDefinition', 'hover', 'references', 'documentHighlight', 'implementation'):
            assert c.query(method, 'A.v3', needle) is None, (method, needle)
        assert prepare(c, 'A.v3', needle) is None, needle


def no_leaks(c):
    """No answer or publication names a sentinel or an emptied body's missing
    return. The development inspection lists the effective program's own
    diagnostics."""
    inspections = {m['id'] for direction, m in c.s.transcript
                   if direction == 'send' and m.get('method') == 'virgil-lsp/snapshot'}
    leaked = [m for direction, m in c.s.transcript if direction == 'recv' and m.get('id') not in inspections
              and (SENTINEL in json.dumps(m) or 'MissingReturn' in json.dumps(m))]
    assert not leaked, leaked


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
        broken = body(BROKEN, '{ var bad')
        assert sub['uri'] == c.uri('A.v3') and sub['substitution'] == blanked(broken), sub
        generation = snap['generation']
        # Definition, hover, references, and highlights outside the body, in
        # this file and in another, keep their offsets.
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
        inside(c, ('target(1', 'bad', '{ var bad', 'target(); }\n\t/*'))
        # Semantic diagnostics outside the body remain; the emptied body's
        # missing return is inside it.
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
        # substituted, and A is blanked again in the same single analysis.
        c.change('C.v3', C_BROKEN)
        snap = c.snapshot()
        assert snap['generation'] == generation + 1, snap
        subs = {v['uri']: v['substitution'] for v in substitutions(snap)}
        assert subs[c.uri('A.v3')] == blanked(broken), subs
        n = offset(C_BROKEN, '= ;') + 2
        assert subs[c.uri('C.v3')] == {'begin': n, 'effectiveEnd': n + 1, 'currentEnd': n,
                                        'bodies': [{'begin': offset(C_BROKEN, '{ var n'),
                                                    'end': offset(C_BROKEN, '}', C_BROKEN.index('{ var n')) + 1}]}, subs
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
        no_leaks(c)
        c.s.shutdown()
    finally:
        c.s.close()


def continued(exe, root):
    # The parser's first error is on the line after the unfinished member
    # access; the body that holds it is still the one blanked.
    c = Case(exe, root, {'A.v3': CONTINUED, 'B.v3': B, 'C.v3': C})
    try:
        c.open('A.v3')
        c.open('B.v3')
        snap = c.snapshot()
        sub, = substitutions(snap)
        assert sub['substitution'] == blanked(body(CONTINUED, '{ var n')), sub
        error, = [d for d in c.diagnostics('A.v3') if d['code'] == 'ParseError']
        assert error['range']['start']['line'] == CONTINUED[:CONTINUED.index('if (n')].count('\n'), error
        target = location(c, 'A.v3', 'target', CONTINUED.index('def target'))
        assert c.query('definition') == target
        assert c.query('hover')['contents']['value'] == 'def target() -> int'
        assert c.query('references') == [target, location(c, 'A.v3', 'target', CONTINUED.index('def elsewhere')),
                                         location(c, 'B.v3', 'target')]
        inside(c, ('{ var n', 'target().', 'n > 0', 'return n', 'target(); }\n\t/*'))
        for name in ('A.v3', 'B.v3'):
            assert [d['range'] for d in c.semantic(name)] == [span(c.texts[name], '"bad"')], c.semantic(name)
        c.change('A.v3', A)
        assert not substitutions(c.snapshot())
        assert c.query('definition', 'A.v3', 'target();') == location(c, 'A.v3', 'target', A.index('def target'))
        assert len(c.query('references')) == 4
        no_leaks(c)
        c.s.shutdown()
    finally:
        c.s.close()


def two_bodies(exe, root):
    # The first error is in broken; once that body is blanked, the next is
    # in elsewhere. Both bodies are excluded, and answers between and after
    # them keep their offsets.
    c = Case(exe, root, {'A.v3': TWO, 'B.v3': B, 'C.v3': C})
    try:
        c.open('A.v3')
        c.open('B.v3')
        snap = c.snapshot()
        sub, = substitutions(snap)
        first, second = body(TWO, '{ var bad'), body(TWO, '{ /* é😀\t */ return target(). ;')
        assert sub['substitution'] == blanked(first, second), sub
        target = location(c, 'A.v3', 'target', TWO.index('def target'))
        assert c.query('definition') == target
        assert c.query('hover')['contents']['value'] == 'def target() -> int'
        # The uses in both bodies are left out.
        assert c.query('references') == [target, location(c, 'B.v3', 'target')]
        assert c.query('documentHighlight', 'A.v3', 'target', TWO.index('def target')) == [
            {'range': span(TWO, 'target', TWO.index('def target')), 'kind': 1}]
        inside(c, ('target(1', 'bad', '{ var bad', 'target(); }\n\t/*', 'target(). ;', '{ /* é😀\t */ return target().'))
        # Calls in either body are in neither direction of call hierarchy.
        use, = prepare(c, 'B.v3', 'use')
        assert [call['to']['name'] for call in calls(c, 'outgoing', use)] == ['target', 'size']
        for name in ('broken', 'elsewhere'):
            item, = prepare(c, 'A.v3', name)
            assert calls(c, 'outgoing', item) == [], name
        callee, = prepare(c, 'A.v3', 'target', TWO.index('def target'))
        assert [call['from']['name'] for call in calls(c, 'incoming', callee)] == ['use']
        for name in ('A.v3', 'B.v3'):
            assert [d['range'] for d in c.semantic(name)] == [span(c.texts[name], '"bad"')], c.semantic(name)
        # Restoration: the parseable text has all four uses again.
        c.change('A.v3', A)
        assert not substitutions(c.snapshot())
        assert len(c.query('references')) == 4
        callee, = prepare(c, 'A.v3', 'target', A.index('def target'))
        assert sorted(call['from']['name'] for call in calls(c, 'incoming', callee)) == ['broken', 'elsewhere', 'use']
        no_leaks(c)
        c.s.shutdown()
    finally:
        c.s.close()


def braces(exe, root):
    # A lost "{" empties the body that layout delimits and moves no offset; a
    # lost "}" of the method's own is inserted before the next method, whose
    # header no statement can begin, and every answer after it maps back to
    # current text.
    for current in (LOST_OPENER, LOST_CLOSER):
        project = root / str(len(list(root.iterdir())))
        project.mkdir()
        c = Case(exe, project, {'A.v3': current, 'B.v3': B, 'C.v3': C})
        try:
            c.open('A.v3')
            c.open('B.v3')
            snap = c.snapshot()
            sub, = substitutions(snap)
            open_ = offset(current, '{\n\t\tif')
            if current == LOST_OPENER:
                close = offset(current, '}\n\tprivate def') + 1
                assert sub['substitution'] == blanked((open_, close)), sub
            else:
                at = offset(current, '\tprivate def')
                assert sub['substitution'] == {'begin': at, 'effectiveEnd': at + 1, 'currentEnd': at,
                                               'bodies': [{'begin': open_, 'end': at}]}, sub
            target = location(c, 'A.v3', 'target', current.index('def target'))
            assert c.query('definition') == target
            assert c.query('definition', 'A.v3', 'target(); }\n\tdef wrong') == target
            assert c.query('hover') == {'contents': {'kind': 'plaintext', 'value': 'def target() -> int'},
                                        'range': span(B, 'target')}
            assert c.query('hover', 'A.v3', 'target', current.index('def elsewhere'))['range'] == span(
                current, 'target', current.index('def elsewhere'))
            assert c.query('references') == [target, location(c, 'A.v3', 'target', current.index('def elsewhere')),
                                             location(c, 'B.v3', 'target')]
            assert c.query('documentHighlight', 'A.v3', 'target', current.index('def elsewhere')) == [
                {'range': span(current, 'target', current.index('def target')), 'kind': 1},
                {'range': span(current, 'target', current.index('def elsewhere')), 'kind': 2}]
            inside(c, ('{\n\t\tif', 'target() > 0', 'target();\n', 'return 0'))
            item, = prepare(c, 'A.v3', 'broken')
            assert calls(c, 'outgoing', item) == []
            callee, = prepare(c, 'A.v3', 'target', current.index('def target'))
            assert sorted(call['from']['name'] for call in calls(c, 'incoming', callee)) == ['elsewhere', 'use']
            for name in ('A.v3', 'B.v3'):
                assert [d['range'] for d in c.semantic(name)] == [span(c.texts[name], '"bad"')], c.semantic(name)
            # Restoration: the parseable text has every use again.
            c.change('A.v3', BRACES)
            assert not substitutions(c.snapshot())
            assert len(c.query('references')) == 5
            no_leaks(c)
            c.s.shutdown()
        finally:
            c.s.close()


def refused(exe, root):
    # Braces that don't balance where layout doesn't show the lost brace or
    # where the method that lost its "}" could go on, as before a header that
    # a local function's could be, an error in a header or a field
    # initializer, errors in three bodies, and an unterminated string leave
    # the program unanalyzed, as before.
    for current in (A.replace('return target(); }', 'return target();', 1),
                    LOST_CLOSER.replace('\tprivate def unused() { }\n', ''),
                    A.replace('broken()', 'broken(x: )'),
                    A.replace('\tdef broken', '\tdef field: int = ;\n\tdef broken'),
                    TWO.replace('return "bad";', 'return ;.'),
                    A.replace('return target();', 'var s = "open;\n\t\treturn target();', 1)):
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
        for name, run in (('cold', cold), ('continued', continued), ('two-bodies', two_bodies), ('braces', braces),
                          ('refused', refused)):
            root = Path(work) / name
            root.mkdir()
            run(exe, root)
    print('current-text repair: cold open, next-line continuation, two bodies, a lost "{" and "}", blanking '
          'beside fallback, suppression, call hierarchy, rename refusal, restoration, and refusals passed')


if __name__ == '__main__':
    main()
