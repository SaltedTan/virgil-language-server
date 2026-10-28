#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Document-symbol replies too large for the server's default heap are refused.

Before #331, an outline of 64,000 declarations, or of 1,000 in a flat reply
that repeats a 100,000-character URI, killed the server. Each case gets exactly
one response, a complete outline or RequestFailed, and the server then answers
a small outline, a ping, shutdown, and exit with status 0.
"""
import sys
from pathlib import Path

from worker import Session

HIERARCHY = {'textDocument': {'documentSymbol': {'hierarchicalDocumentSymbolSupport': True}}}
SMALL_URI = 'untitled:small.v3'
SMALL = 'component C {\n\tdef f() {}\n}\n'
TOO_MANY = 'the document has more than 10000 symbols'
TOO_LARGE = "the document's outline needs more than 4194304 bytes"


def names(symbols):
    """The names of {symbols} and their children, in pre-order."""
    return [n for symbol in symbols for n in [symbol['name'], *names(symbol.get('children', []))]]


def declarations(count):
    return ' '.join('def a%d=0;' % i for i in range(count))


def run(exe, name, hierarchy, uri, text, expected):
    """Asks for the outline of {text} at {uri}: {expected} is the number of
    symbols of a complete reply, or the message of a RequestFailed."""
    s = Session(exe)
    try:
        s.result(s.send('initialize', {'capabilities': HIERARCHY if hierarchy else {}}), 5)
        for u, t in ((uri, text), (SMALL_URI, SMALL)):
            s.send('textDocument/didOpen', {'textDocument': {
                'uri': u, 'languageId': 'virgil', 'version': 1, 'text': t}}, request=False)
        s.ping()
        reply = s.reply(s.send('textDocument/documentSymbol', {'textDocument': {'uri': uri}}), 20)
        if isinstance(expected, int):
            assert 'result' in reply and len(reply['result']) == expected, (name, str(reply)[:200])
        else:
            assert 'result' not in reply, (name, str(reply)[:200])
            assert reply['error'] == {'code': -32803, 'message': expected}, (name, reply)
        small = s.result(s.send('textDocument/documentSymbol', {'textDocument': {'uri': SMALL_URI}}), 5)
        assert names(small) == ['C', 'f'], small
        s.ping()
        s.shutdown()
    except BaseException:
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    dense = declarations(64000)
    assert len(dense.encode()) == 884889
    long_uri = 'untitled:' + 'u' * 100000
    cases = [
        # The three cases of #331.
        ('64,000 flat', False, 'untitled:dense.v3', dense, TOO_MANY),
        ('64,000 hierarchical', True, 'untitled:dense.v3', dense, TOO_MANY),
        ('1,000 flat with a long URI', False, long_uri, declarations(1000), TOO_LARGE),
        # Its 32,000 control, which the default heap could answer only with
        # most of it, beyond the snapshot budget's share.
        ('32,000 flat', False, 'untitled:dense.v3', declarations(32000), TOO_MANY),
        # The largest replies that each bound admits.
        ('10,000 flat', False, 'untitled:dense.v3', declarations(10000), 10000),
        ('10,000 hierarchical', True, 'untitled:dense.v3', declarations(10000), 10000),
        ('41 flat with a long URI', False, long_uri, declarations(41), 41),
        ('42 flat with a long URI', False, long_uri, declarations(42), TOO_LARGE),
        ('1,000 hierarchical with a long URI', True, long_uri, declarations(1000), 1000),
    ]
    for case in cases:
        run(exe, *case)
    print('PASS: document-symbol budget (%d cases: refused and admitted outlines, then a small outline, '
          'a ping, shutdown, and exit)' % len(cases))


if __name__ == '__main__':
    main()
