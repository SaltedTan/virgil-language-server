#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Exercise an installed server's root selection through stdio (no environment root)."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time


class Session:
    def __init__(self, server, directory):
        self.log = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([str(server), '--stdio'], cwd=directory,
                                     env={'PATH': '/usr/bin:/bin'}, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.log)
        self.buffer = b''
        self.messages = []
        self.number = 0

    def send(self, method, params=None, request=True):
        message = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        if request:
            self.number += 1
            message['id'] = self.number
        data = json.dumps(message).encode()
        self.proc.stdin.write(f'Content-Length: {len(data)}\r\n\r\n'.encode() + data)
        self.proc.stdin.flush()
        return message.get('id')

    def until(self, predicate):
        deadline = time.monotonic() + 30
        while True:
            for message in self.messages:
                if predicate(message):
                    return message
            header, sep, body = self.buffer.partition(b'\r\n\r\n')
            if sep:
                size = int(header.removeprefix(b'Content-Length: '))
                if len(body) >= size:
                    self.messages.append(json.loads(body[:size]))
                    self.buffer = body[size:]
                    continue
            remaining = deadline - time.monotonic()
            assert remaining > 0, ('response timeout', self.messages)
            assert select.select([self.proc.stdout], [], [], remaining)[0], self.messages
            data = os.read(self.proc.stdout.fileno(), 65536)
            assert data, ('server closed stdout', self.messages)
            self.buffer += data

    def request(self, method, params=None):
        number = self.send(method, params)
        message = self.until(lambda m: m.get('id') == number)
        assert 'result' in message, message
        return message['result']

    def logs(self):
        self.log.seek(0)
        return self.log.read().decode()

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc.stdout.close()
        self.log.close()


def exercise(server, expected_root=None, options=None, error=None, log_contains=None):
    with tempfile.TemporaryDirectory(prefix='library-project-') as temporary:
        project = Path(temporary).resolve()
        config = project / '.virgil-lsp.json'
        config.write_text(json.dumps({'version': 1, 'projects': [{'name': 'app',
            'sources': ['main.v3'], 'virgilDependencies': ['lib/util/*.v3']}]}))
        source = 'component Main {\n def main() -> int {\n  var b = StringBuilder.new();\n  var bad: int = true;\n  return b.length;\n }\n}\n'
        main = project / 'main.v3'
        main.write_text(source)
        session = Session(server, project)
        try:
            params = {'rootUri': project.as_uri(), 'capabilities': {}}
            if options is not None:
                params['initializationOptions'] = options
            session.request('initialize', params)
            session.send('initialized', {}, request=False)
            session.send('textDocument/didOpen', {'textDocument': {'uri': config.as_uri(),
                'languageId': 'json', 'version': 1, 'text': config.read_text()}}, request=False)
            if error:
                result = session.until(lambda m: m.get('method') == 'textDocument/publishDiagnostics'
                    and m['params']['uri'] == config.as_uri() and any(
                        d.get('code') == error for d in m['params']['diagnostics']))
                print(json.dumps(result))
            else:
                configured = session.until(lambda m: m.get('method') == 'textDocument/publishDiagnostics'
                    and m['params']['uri'] == config.as_uri())
                assert configured['params']['diagnostics'] == [], configured
                session.send('textDocument/didOpen', {'textDocument': {'uri': main.as_uri(),
                    'languageId': 'virgil', 'version': 1, 'text': source}}, request=False)
                result = session.until(lambda m: m.get('method') == 'textDocument/publishDiagnostics'
                    and m['params']['uri'] == main.as_uri() and any(
                        d.get('code') == 'TypeError' for d in m['params']['diagnostics']))
                print(json.dumps(result))
                position = {'textDocument': {'uri': main.as_uri()},
                            'position': {'line': 2, 'character': 12}}
                hover = session.request('textDocument/hover', position)
                assert hover and 'StringBuilder' in json.dumps(hover), hover
                definition = session.request('textDocument/definition', position)
                expected = (expected_root / 'lib/util/StringBuilder.v3').as_uri()
                assert definition and definition['uri'] == expected, (definition, expected)
                print('hover: ' + json.dumps(hover))
                print('definition: ' + json.dumps(definition))
            session.request('shutdown')
            session.send('exit', request=False)
            session.proc.stdin.close()
            assert session.proc.wait(timeout=5) == 0
            logs = [line for line in session.logs().splitlines() if 'Virgil root:' in line]
            assert len(logs) == 1, logs
            if log_contains:
                assert log_contains in logs[0], logs
            print(logs[0])
        finally:
            session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('server', type=Path)
    args = parser.parse_args()
    root = args.server.resolve().parent / 'virgil'
    exercise(args.server.absolute(), root, log_contains='(bundle, revision ')
