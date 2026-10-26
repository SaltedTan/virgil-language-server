#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Semicolon quick fixes over stdio, without a project or semantic worker."""
import copy
from pathlib import Path
import sys

from worker import Session

CAPS = {'workspace': {'workspaceEdit': {'documentChanges': True}},
        'textDocument': {'codeAction': {'codeActionLiteralSupport': {
            'codeActionKind': {'valueSet': ['quickfix']}}}}}
URI = 'untitled:semicolon.v3'
TEXT = 'component C {\n\tdef f() {\n\t\treturn 1 // trailing\n\t}\n}\n'
POINT = {'line': 2, 'character': 10}
ERROR = {'line': 3, 'character': 1}
DIAGNOSTIC = {'range': {'start': ERROR, 'end': ERROR}, 'severity': 1,
              'source': 'aeneas', 'code': 'ParseError', 'message': '";" expected'}


def params(diagnostics=None):
    result = {'textDocument': {'uri': URI}, 'range': {'start': ERROR, 'end': ERROR}}
    if diagnostics is not None:
        result['context'] = {'diagnostics': diagnostics}
    return result


def expected(diagnostic=DIAGNOSTIC, version=1):
    return [{'title': 'Insert missing ";"', 'kind': 'quickfix',
             'diagnostics': [diagnostic], 'isPreferred': True,
             'edit': {'documentChanges': [{'textDocument': {'uri': URI, 'version': version},
                                          'edits': [{'range': {'start': POINT, 'end': POINT}, 'newText': ';'}]}]}}]


def run(exe):
    for literal, versioned in ((True, True), (False, True), (True, False)):
        s = Session(exe)
        try:
            caps = copy.deepcopy(CAPS)
            if not literal:
                del caps['textDocument']['codeAction']
            caps['workspace']['workspaceEdit']['documentChanges'] = versioned
            result = s.result(s.send('initialize', {'capabilities': caps}))['capabilities']
            if literal:
                assert result['codeActionProvider'] == {'codeActionKinds': ['quickfix']}, result
            else:
                assert 'codeActionProvider' not in result, result
            s.send('textDocument/didOpen', {'textDocument': {
                'uri': URI, 'version': 1, 'languageId': 'virgil', 'text': TEXT}}, request=False)
            action = lambda p: s.result(s.send('textDocument/codeAction', p))
            assert action(params()) == (expected() if literal and versioned else [])
            if literal and versioned:
                publication = [m['params'] for direction, m in s.transcript
                               if direction == 'recv' and m.get('method') == 'textDocument/publishDiagnostics'][-1]
                assert publication['diagnostics'] == [DIAGNOSTIC], publication
                client = dict(DIAGNOSTIC, data={'fromClient': True})
                assert action(params([client])) == expected(client)
                stale = copy.deepcopy(client)
                stale['range']['start']['line'] += 1
                assert action(params([stale])) == []
                assert action(params([])) == []
                # A version-only update uses the current document version.
                s.send('textDocument/didChange', {'textDocument': {'uri': URI, 'version': 2},
                       'contentChanges': []}, request=False)
                assert action(params([client])) == expected(client, 2)
                s.send('textDocument/didChange', {'textDocument': {'uri': URI, 'version': 3},
                       'contentChanges': [{'text': TEXT.replace('1 //', '1; //')}]}, request=False)
                assert action(params([client])) == []
                assert action(params()) == []
            for bad in ({}, [], dict(params(), range={'start': ERROR}),
                        dict(params(), context={'only': [1]}), dict(params(), context={'diagnostics': 1})):
                reply = s.reply(s.send('textDocument/codeAction', bad))
                assert reply['error']['code'] == -32602, reply
            s.shutdown()
            print(f'code actions: literal={literal}, documentChanges={versioned}: passed')
        finally:
            s.close()


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
