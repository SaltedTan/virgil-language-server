#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Documents too dense for the server's own parse are refused before it (#372).

Outlines, folding ranges, and selection ranges parse an open document in the
server process. Before #372, a document just under the 1 MiB overlay limit of
"var a,a,a,...;" made selectionRange and documentSymbol end the server with
HeapOverflow. Each method now gets one response for the densest texts with the
default heap: a reply, or RequestFailed for a document of more than 350,000
tokens, including dense bodies after malformed representation hints.
The semicolon quick fix, whose parse keeps the same syntax index but reads the
text without normalizing its line terminators, offers no action for such a
document instead of ending the server, including malformed representation
hints and mixed line endings. Each case is followed
by the same request on a small document, a ping, shutdown, and exit with
status 0.

The same budget bounds the server's other single-file parses (#428).
Repairing the current text of a dense project file whose method body is
broken, or whose braces are one closer short, ended the server with
HeapOverflow at rest; now the file is analyzed as it is. Parser diagnostics of a dense document ended it
with two Aeneas-sized analysis snapshots retained; now the document has none.
Each case is followed by a small document's outline, a ping, shutdown, and
exit with status 0. The pressure case copies the pinned Aeneas sources, as
evicted_snapshot.py does, checks that both projects' analyses completed and
are retained before it opens the dense text, and takes about 15 seconds.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import evicted_snapshot
from worker import Session

DENSE_URI = 'untitled:dense.v3'
SMALL_URI = 'untitled:s'
SMALL = 'component C {\n\tdef f() {}\n}\n'
MAX_TOKENS = 350000
REFUSED = 'the document has more than %d tokens' % MAX_TOKENS
# Just under the 1 MiB overlay limit, beside the small document: each is
# charged its text, URI, and language ID, and 256 bytes for its record.
SIZE = 1024 * 1024 - 2 * 256 - len(SMALL) - len(SMALL_URI) - len(DENSE_URI) - 2 * len('virgil')
# A small document missing a semicolon, which has one quick fix.
SMALL_FIX = 'component C { def f() { return 1\n} }'
CODE_ACTIONS = {'workspace': {'workspaceEdit': {'documentChanges': True}},
                'textDocument': {'codeAction': {'codeActionLiteralSupport': {
                    'codeActionKind': {'valueSet': ['quickfix']}}}}}
METHODS = ('textDocument/documentSymbol', 'textDocument/foldingRange', 'textDocument/selectionRange')


def fill(head, unit, tail, size):
    """{head}, then as many {unit}s as fit in {size} bytes with {tail}."""
    return head + unit * ((size - len(head) - len(tail)) // len(unit)) + tail


def nested(depth, size):
    """Statements of {depth} nested parentheses in one method body."""
    one = '(' * depth + 'a' + ')' * depth + ';'
    return fill('def f(){', one, '}', size)


def params(method, uri):
    p = {'textDocument': {'uri': uri}}
    if method == 'textDocument/selectionRange':
        p['positions'] = [{'line': 0, 'character': 5}]
    return p


def run(exe, name, text, method, refused):
    """Opens {text} and asks {method} for it: RequestFailed if {refused},
    otherwise a complete reply."""
    s = Session(exe)
    try:
        s.result(s.send('initialize', {'capabilities': {}}), 5)
        for uri, t in ((DENSE_URI, text), (SMALL_URI, SMALL)):
            s.send('textDocument/didOpen', {'textDocument': {
                'uri': uri, 'languageId': 'virgil', 'version': 1, 'text': t}}, request=False)
        s.ping()
        reply = s.reply(s.send(method, params(method, DENSE_URI)), 20)
        if refused:
            assert 'result' not in reply, (name, method, str(reply)[:200])
            assert reply['error'] == {'code': -32803, 'message': REFUSED}, (name, method, reply)
        else:
            assert isinstance(reply.get('result'), list), (name, method, str(reply)[:200])
        small = s.result(s.send(method, params(method, SMALL_URI)), 5)
        # The flat outline of C and f, C's folding range, and one chain.
        assert len(small) == (2 if method == 'textDocument/documentSymbol' else 1), (name, method, small)
        s.ping()
        s.shutdown()
    except BaseException:
        print('%s, %s:' % (name, method), file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def diagnostic(s, uri):
    """The first diagnostic published for {uri}."""
    published = [m['params'] for d, m in s.transcript
                 if d == 'recv' and m.get('method') == 'textDocument/publishDiagnostics' and m['params']['uri'] == uri]
    return published[-1]['diagnostics'][0]


def run_code_action(exe, text):
    """Opens {text} and asks for a quick fix without client diagnostics:
    no action, followed by a small document's missing-semicolon action."""
    s = Session(exe)
    try:
        s.result(s.send('initialize', {'capabilities': CODE_ACTIONS}), 5)
        for uri, t in ((DENSE_URI, text), (SMALL_URI, SMALL_FIX)):
            s.send('textDocument/didOpen', {'textDocument': {
                'uri': uri, 'languageId': 'virgil', 'version': 1, 'text': t}}, request=False)
        s.ping()
        for uri, count in ((DENSE_URI, 0), (SMALL_URI, 1)):
            d = diagnostic(s, uri) if uri == SMALL_URI else None
            context = {'diagnostics': [d]} if d else {}
            span = d['range'] if d else {'start': {'line': 0, 'character': 0},
                                        'end': {'line': 0, 'character': 0}}
            actions = s.result(s.send('textDocument/codeAction', {
                'textDocument': {'uri': uri}, 'range': span, 'context': context}), 20)
            assert len(actions) == count, (uri, str(actions)[:200])
        s.ping()
        s.shutdown()
    except BaseException:
        print('code action:', file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def outline_small(s, uri):
    """The small document's flat outline: C and f."""
    s.send('textDocument/didOpen', {'textDocument': {
        'uri': uri, 'languageId': 'virgil', 'version': 1, 'text': SMALL}}, request=False)
    small = s.result(s.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}), 5)
    assert len(small) == 2, small


# A method whose body is broken, and one whose if lost its "}", after a dense
# expression list: repair parses the whole text with a syntax index.
BROKEN = '\ndef g(a: int, b: int) -> int {\n\tvar y = g(a, ;\n\treturn a;\n}\n'
CLOSER = '\ndef g(a: int, b: int) -> int {\n\tif (a > 0) {\n\t\ta = 1;\n\tvar y = ;\n\treturn a;\n}\n'


def run_repair(exe, name, tail):
    """Opens a dense project file that fails to parse, which an analysis
    would repair, and waits for that analysis with a hover; then a small
    document's outline."""
    work = Path(tempfile.mkdtemp(prefix='parse-budget-'))
    s = Session(exe)
    try:
        (work / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [{'name': 'p', 'sources': ['*.v3']}]}))
        path, small = work / 'd.v3', work / 's.v3'
        uris = path.as_uri(), small.as_uri()
        size = 1024 * 1024 - 2 * 256 - len(SMALL) - sum(len(u) for u in uris) - 2 * len('virgil')
        text = fill('var x=(', 'a,', 'a);', size - len(tail)) + tail
        assert len(text) <= size
        path.write_text(text)
        small.write_text(SMALL)
        s.result(s.send('initialize', {'rootUri': work.as_uri(), 'capabilities': {}}), 10)
        s.send('initialized', {}, request=False)
        s.send('textDocument/didOpen', {'textDocument': {
            'uri': uris[0], 'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)
        line = text.count('\n', 0, text.index('def g('))
        s.reply(s.send('textDocument/hover', {'textDocument': {'uri': uris[0]},
                                              'position': {'line': line, 'character': 5}}), 60)
        outline_small(s, uris[1])
        s.ping()
        s.shutdown()
    except BaseException:
        print('%s:' % name, file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()
        shutil.rmtree(work, ignore_errors=True)


def run_pressure(exe):
    """With the compiler and demangle projects of evicted_snapshot.py
    analyzed, and each project's snapshot retained, opens and changes a dense
    document; then a small document's outline."""
    ev = evicted_snapshot
    work = Path(tempfile.mkdtemp(prefix='parse-budget-'))
    s = ev.Client(exe)
    try:
        for rel in ('aeneas/src', 'apps/Demangle'):
            shutil.copytree(ev.VIRGIL / rel, work / rel)
        (work / '.virgil-lsp.json').write_text(json.dumps(ev.CONFIG))
        s.result(s.send('initialize', {
            'rootUri': work.as_uri(), 'workspaceFolders': [{'uri': work.as_uri(), 'name': 'p'}],
            'initializationOptions': {'virgilRoot': str(ev.VIRGIL.resolve())}, 'capabilities': {}}), 10)
        s.send('initialized', {}, request=False)
        # Small documents, so that the dense one fits beside them in the
        # overlay budget: each one's opening schedules its project's analysis.
        opened = [(work / 'aeneas/src/main/Version.v3', 'component Version'),
                  (work / 'apps/Demangle/Demangle.v3', 'def doStdin')]
        for path, _ in opened:
            s.send('textDocument/didOpen', {'textDocument': {
                'uri': path.as_uri(), 'languageId': 'virgil', 'version': 1, 'text': path.read_text()}}, request=False)
        ev.settle(s)
        # Hover answers from the project's retained snapshot, and at once with
        # null after a failed analysis. Nothing is analyzed once the analyses
        # have settled, so neither snapshot is evicted before the dense text.
        for path, needle in opened:
            text = path.read_text()
            at = text.index(needle) + len(needle) - 2
            line = text.count('\n', 0, at)
            hover = s.result(s.send('textDocument/hover', {'textDocument': {'uri': path.as_uri()}, 'position': {
                'line': line, 'character': at - text.rfind('\n', 0, at) - 1}}), 60)
            assert hover is not None, 'no retained analysis of %s: the pressure is missing' % path.name
        assert ev.generation(s) == ev.settle(s), 'an analysis ran after the hovers'
        uris = [p.as_uri() for p, _ in opened] + [DENSE_URI, SMALL_URI]
        size = 1024 * 1024 - 4 * 256 - sum(len(p.read_text()) for p, _ in opened) - len(SMALL) \
            - sum(len(u) for u in uris) - 4 * len('virgil')
        text = fill('var x=(', 'a,', 'a);', size)
        s.send('textDocument/didOpen', {'textDocument': {
            'uri': DENSE_URI, 'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)
        s.ping()
        s.send('textDocument/didChange', {'textDocument': {'uri': DENSE_URI, 'version': 2},
                                          'contentChanges': [{'text': text[:-1] + ' '}]}, request=False)
        s.ping()
        assert diagnostics(s, DENSE_URI) == [], 'a dense document has no parser diagnostics'
        outline_small(s, SMALL_URI)
        s.ping()
        s.shutdown()
    except BaseException:
        print('parser diagnostics under pressure:', file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()
        shutil.rmtree(work, ignore_errors=True)


def diagnostics(s, uri):
    """The diagnostics last published for {uri}."""
    published = [m['params'] for d, m in s.transcript
                 if d == 'recv' and m.get('method') == 'textDocument/publishDiagnostics' and m['params']['uri'] == uri]
    return published[-1]['diagnostics']


def main():
    exe = Path(sys.argv[1]).resolve()
    lists = fill('var x=(', 'a,', 'a);', SIZE)
    hints = [('hint slash', 'class C #//* */ {var x=(' + 'a,' * 520000 + 'a);}'),
             ('hint quote', 'class C #" {var x=(' + 'a,' * 520000 + 'a);}')]
    cases = [
        # The reproduction of #372, a token in each byte.
        ('var a,a,a,...', fill('var ', 'a,', 'a;', SIZE), True),
        # The densest text measured, a long expression list.
        ('(a,a,a,...)', lists, True),
        # Nesting as deep as the parser's stack limit allows, repeated, which
        # gives the longest selection chains.
        ('nested parentheses', nested(400, SIZE), True),
        # A line comment that a lone carriage return ends, as it ends the
        # line that the parser reads, and one that CRLF ends.
        ('comment ended by CR', '//\rvar x=(' + 'a,' * 523990 + 'a);', True),
        ('comment ended by CRLF', '//\r\nvar x=(' + 'a,' * 523990 + 'a);', True),
        # The most tokens the bound admits, and one more: "var", "x", "=",
        # "(", "-", two for each "a,", and "a", ")", ";", or "-", "a", ")", ";".
        ('350,000 tokens', 'var x=(-' + 'a,' * 174996 + 'a);', False),
        ('350,001 tokens', 'var x=(-' + 'a,' * 174996 + '-a);', True),
    ] + [(name, text, True) for name, text in hints]
    for name, text, refused in cases:
        assert len(text.encode()) <= SIZE, name
        for method in METHODS:
            run(exe, name, text, method, refused)
    dense_fix = 'def f(){var x=(' + 'a,' * 523980 + 'a)\n}'
    # Its parse reads the text raw, where a carriage return doesn't end a line
    # comment, so the comment hides "/*" from it, but not from normalized text.
    mixed_fix = '//\r/*\n' + dense_fix
    for text in (dense_fix, mixed_fix, *(text for _, text in hints)):
        assert len(text) + len(SMALL_FIX) - len(SMALL) <= SIZE
        run_code_action(exe, text)
    for name, tail in (('repair of a broken body', BROKEN), ('repair of a lost closer', CLOSER)):
        run_repair(exe, name, tail)
    run_pressure(exe)
    print('PASS: outline parse budget (%d cases of %d methods: refused and admitted documents, and a quick fix '
          'refused; dense repairs and parser diagnostics under pressure; then a small document, a ping, shutdown, '
          'and exit)' % (len(cases), len(METHODS)))


if __name__ == '__main__':
    main()
