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


# The parser reads `return if (false)` as a conditional expression. A ";" after
# `if (false)` parses but ends the wrong statement; the one after `return`
# starts on another line, so no action is offered.
SWALLOWED = 'component C {\n    def f() {\n        if (true) return\n        if (false) {}\n    }\n}\n'
SWALLOWED_URI = 'untitled:swallowed.v3'
SWALLOWED_ERROR = {'line': 3, 'character': 19}
NESTED = 'component C {\n    def f() {\n        if (true) return\n        if (p(fun() {})) {}\n    }\n}\n'
NESTED_URI = 'untitled:nested.v3'
NESTED_ERROR = {'line': 3, 'character': 25}


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
                s.send('textDocument/didOpen', {'textDocument': {
                    'uri': SWALLOWED_URI, 'version': 1, 'languageId': 'virgil', 'text': SWALLOWED}}, request=False)
                swallowed = dict(params(), textDocument={'uri': SWALLOWED_URI})
                assert action(swallowed) == []
                published = [m['params'] for direction, m in s.transcript
                             if direction == 'recv' and m.get('method') == 'textDocument/publishDiagnostics'
                             and m['params']['uri'] == SWALLOWED_URI][-1]['diagnostics']
                assert [(d['message'], d['range']['start']) for d in published] == [
                    ('";" expected', SWALLOWED_ERROR)], published
                assert action(dict(swallowed, context={'diagnostics': published})) == []
                s.send('textDocument/didOpen', {'textDocument': {
                    'uri': NESTED_URI, 'version': 1, 'languageId': 'virgil',
                    'text': NESTED.replace('if (p(fun() {})) {}', 'if (p(fun() {})); {}')}}, request=False)
                nested = dict(params(), textDocument={'uri': NESTED_URI},
                              range={'start': NESTED_ERROR, 'end': NESTED_ERROR})
                assert action(nested) == []
                published = [m['params'] for direction, m in s.transcript
                             if direction == 'recv' and m.get('method') == 'textDocument/publishDiagnostics'
                             and m['params']['uri'] == NESTED_URI][-1]['diagnostics']
                assert published == [], published
                s.send('textDocument/didChange', {'textDocument': {'uri': NESTED_URI, 'version': 2},
                       'contentChanges': [{'text': NESTED}]}, request=False)
                assert action(nested) == []
                published = [m['params'] for direction, m in s.transcript
                             if direction == 'recv' and m.get('method') == 'textDocument/publishDiagnostics'
                             and m['params']['uri'] == NESTED_URI][-1]['diagnostics']
                assert [(d['message'], d['range']['start']) for d in published] == [
                    ('";" expected', NESTED_ERROR)], published
                assert action(dict(nested, context={'diagnostics': published})) == []
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
