#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""An exhausted open-document capacity warns once, not once per rejection.

Five distinct documents over the aggregate 1 MiB overlay budget are opened in
turn. Each didOpen is rejected and logged to stderr, but only the first one
reaches the user, so a request that drains the notifications sees exactly one
Warning window/showMessage (#332). The generated text is one declaration and a
long comment; it is too large to keep as a golden transcript.
"""

from pathlib import Path
import sys

from worker import Session


class WarningSession(Session):
    # Capacity warnings are the notifications this case counts.
    NOTIFICATIONS = Session.NOTIFICATIONS + ('window/showMessage',)


def run(exe):
    s = WarningSession(exe)
    try:
        s.initialize()
        text = 'component C { }\n//' + 'x' * (1024 * 1024)
        for index in range(5):
            s.send('textDocument/didOpen', {'textDocument': {
                'uri': f'file:///tmp/virgil-capacity-{index}.v3',
                'languageId': 'virgil', 'version': 1, 'text': text}}, request=False)
        # The reply drains the notifications the server wrote before it.
        assert s.result(s.send('workspace/symbol', {'query': ''})) == [], s.transcript
        warnings = [m['params'] for direction, m in s.transcript
                    if direction == 'recv' and m.get('method') == 'window/showMessage']
        assert len(warnings) == 1, warnings
        assert warnings[0]['type'] == 2, warnings
        assert 'aggregate open-document capacity exceeded' in warnings[0]['message'], warnings
        s.shutdown()
        logs = s.logs()
        prefix = 'virgil-lsp: warning: ignored textDocument/didOpen: aggregate open-document capacity exceeded'
        assert logs.count(prefix) == 5, logs
        print('capacity warning: five rejected opens, one warning: passed')
    finally:
        s.close()


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
