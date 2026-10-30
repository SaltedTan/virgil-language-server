#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The checked-in first-project walkthrough, end to end over stdio.

docs/examples/first-project/ is copied to a temporary directory and opened as
a workspace. Its deliberate type error is published with the exact message,
range, and severity the walkthrough quotes; the walkthrough's one-word fix
clears it; hover and definition at the call site give the walkthrough's
answers.
"""

import shutil
from pathlib import Path
import sys
import tempfile

from worker import Session

EXAMPLE = Path(__file__).resolve().parents[2] / 'docs/examples/first-project'
# The deliberate type error, at the call, as the walkthrough quotes it.
DIAGNOSTIC = {'code': 'TypeError',
              'message': 'expected int in var initialization, got Array<byte>',
              'range': {'start': {'line': 4, 'character': 22},
                        'end': {'line': 4, 'character': 39}},
              'severity': 1,
              'source': 'aeneas'}
# The call site, 'message' in 'Greeter.message()'.
CALL = 'Greeter.message'
# The declaration the walkthrough names, 'message' in Greeter.v3.
DECLARATION = {'line': 3, 'start': 5, 'end': 12}


def call_site(text):
    offset = text.index(CALL) + len('Greeter.')
    return {'line': text[:offset].count('\n'),
            'character': len(text[:offset].rsplit('\n', 1)[-1])}


def published(s, uri):
    return [m['params'] for direction, m in s.transcript
            if direction == 'recv' and m.get('method') == 'textDocument/publishDiagnostics'
            and m['params']['uri'] == uri]


def run(exe):
    with tempfile.TemporaryDirectory(prefix='first-project-') as temporary:
        root = Path(temporary).resolve() / 'first-project'
        shutil.copytree(EXAMPLE, root)
        s = Session(exe)
        try:
            s.result(s.send('initialize', {'rootUri': root.as_uri()}))
            main = root / 'Main.v3'
            text = main.read_text()
            s.send('textDocument/didOpen', {'textDocument': {
                'uri': main.as_uri(), 'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)
            # A hover request waits for the first analysis to finish.
            query = {'textDocument': {'uri': main.as_uri()}, 'position': call_site(text)}
            s.result(s.send('textDocument/hover', query))
            diagnostics = published(s, main.as_uri())
            assert diagnostics and diagnostics[-1]['diagnostics'] == [DIAGNOSTIC], diagnostics
            # The walkthrough's fix: the one word 'int' becomes 'string'.
            fixed = text.replace('var greeting: int', 'var greeting: string')
            assert fixed != text
            s.send('textDocument/didChange', {'textDocument': {'uri': main.as_uri(), 'version': 2},
                                             'contentChanges': [{'text': fixed}]}, request=False)
            # The fix lengthens the line by one, so the call site moves with it.
            query = {'textDocument': {'uri': main.as_uri()}, 'position': call_site(fixed)}
            hover = s.result(s.send('textDocument/hover', query))
            diagnostics = published(s, main.as_uri())
            assert diagnostics and diagnostics[-1]['diagnostics'] == [], diagnostics
            assert hover['contents']['value'] == '```virgil\ndef message() -> Array<byte>\n```', hover
            definition = s.result(s.send('textDocument/definition', query))
            expected = {'uri': (root / 'Greeter.v3').as_uri(), 'range': {
                'start': {'line': DECLARATION['line'], 'character': DECLARATION['start']},
                'end': {'line': DECLARATION['line'], 'character': DECLARATION['end']}}}
            assert definition == expected, definition
            s.shutdown()
            print('first project: diagnostic, fix, hover, and definition passed')
        finally:
            s.close()


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
