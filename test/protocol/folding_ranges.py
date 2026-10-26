#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Current-overlay folding, capability negotiation, edits, and parse failures."""
import sys
from pathlib import Path
from worker import Session

URI = 'untitled:folding-protocol.v3'
TEXT = '// first\n// second\ncomponent C {\n  def f() {\n    return;\n  }\n}\n'
LINES = [{'startLine': 0, 'endLine': 1, 'kind': 'comment'},
         {'startLine': 2, 'endLine': 5}, {'startLine': 3, 'endLine': 4}]


def run(exe, capabilities):
    s = Session(exe)
    params = {'textDocument': {'uri': URI}}
    def query():
        return s.result(s.send('textDocument/foldingRange', params))
    def change(text, version):
        s.send('textDocument/didChange', {'textDocument': {'uri': URI, 'version': version},
               'contentChanges': [{'text': text}]}, request=False)
    try:
        caps = s.result(s.send('initialize', {'capabilities': {
            'textDocument': {'foldingRange': capabilities}}}))['capabilities']
        assert caps['foldingRangeProvider'] is True
        assert query() == []
        s.send('textDocument/didOpen', {'textDocument': {'uri': URI,
               'languageId': 'virgil', 'version': 1, 'text': TEXT}}, request=False)
        want = [dict(r) for r in LINES]
        if not capabilities.get('lineFoldingOnly'):
            for r, start, end in zip(want, (0, 13, 11), (9, 3, 11)):
                r.update(startCharacter=start, endCharacter=end)
        if capabilities.get('foldingRangeKind', {}).get('valueSet') == []:
            want[0].pop('kind')
        if 'rangeLimit' in capabilities:
            want = want[:capabilities['rangeLimit']]
        assert query() == want, s.transcript
        change('\n' + TEXT, 2)
        assert query() == [dict(r, startLine=r['startLine'] + 1,
                               endLine=r['endLine'] + 1) for r in want]
        # A complete method before the error survives; the enclosing component
        # crosses the error, and the later method must not appear.
        change(TEXT.replace('\n}\n', '\n  def bad() { var x = ; }\n  def later() {\n    return;\n  }\n}\n'), 3)
        error_want = [dict(r) for r in LINES if r['startLine'] != 2]
        if capabilities.get('foldingRangeKind', {}).get('valueSet') == []:
            error_want[0].pop('kind')
        if not capabilities.get('lineFoldingOnly'):
            for r, start, end in zip(error_want, (0, 11), (9, 11)):
                r.update(startCharacter=start, endCharacter=end)
        if 'rangeLimit' in capabilities:
            error_want = error_want[:capabilities['rangeLimit']]
        assert query() == error_want, s.transcript
        s.send('textDocument/didClose', params, request=False)
        assert query() == []
        for bad in (None, [], {}, {'textDocument': None}, {'textDocument': {'uri': 1}},
                    {'textDocument': {'uri': ''}}, {'textDocument': {'uri': 'file://remote/a'}}):
            assert s.reply(s.send('textDocument/foldingRange', bad))['error']['code'] == -32602
        s.shutdown()
    finally:
        s.close()


if __name__ == '__main__':
    exe = Path(sys.argv[1]).resolve()
    for capabilities in ({}, {'lineFoldingOnly': True},
                         {'lineFoldingOnly': True, 'foldingRangeKind': {'valueSet': []}},
                         {'lineFoldingOnly': True, 'rangeLimit': 1},
                         {'lineFoldingOnly': True, 'rangeLimit': 0}):
        run(exe, capabilities)
    print('PASS: folding ranges (overlay, edits, parse error, close, params, capabilities)')
