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
"""
import sys
from pathlib import Path

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
    print('PASS: outline parse budget (%d cases of %d methods: refused and admitted documents, and a quick fix '
          'refused; then a small document, a ping, shutdown, and exit)' % (len(cases), len(METHODS)))


if __name__ == '__main__':
    main()
