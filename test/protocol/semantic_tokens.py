#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Semantic tokens through real workers: exact tokens, the option, a file
that doesn't parse, method-body fallback, current-text repair, and refresh."""
import json
from pathlib import Path
import sys
import tempfile
from worker import Session
from type_definition import open_file, change

TYPES = ['namespace', 'class', 'enum', 'enumMember', 'struct', 'type',
         'typeParameter', 'parameter', 'variable', 'property', 'function', 'method']
MODIFIERS = ['declaration', 'readonly']
PROVIDER = {'full': True, 'legend': {'tokenModifiers': MODIFIERS, 'tokenTypes': TYPES}}
CONTENT_MODIFIED = -32801
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# Every token type and modifier. Non-ASCII text (U+00E9, one UTF-16 unit, and
# U+1D11E, two) comes before tokens on line 0, and lines 0 to 2 end in CRLF
# after a comment, where the compiler accepts a carriage return.
MAIN = ('/* é\U0001d11e */ component Util { def K = 1; var n = 2; '
        'def f(p: int) -> int { var v = p; def w = v; return w + K; } } //\r\n'
        'class Box<T>(item: T, var size: int) { def get() -> T { return this.item; } } //\r\n'
        'type Shape { case Dot; } enum Color { RED } //\r\n'
        'layout L { +0 f: u8; =1; } packing P(a: 4, b: 4): 8 = #concat(a, b);\n'
        'def top(s: Shape, c: Color) -> Box<int> { var r: Ref<L>; return Box<int>.new(1, 2); }\n')
# Line, UTF-16 character, the word there, its type, and its modifiers.
EXPECTED = [
    (0, 20, 'Util', 'namespace', ['declaration']),
    (0, 31, 'K', 'property', ['declaration', 'readonly']),
    (0, 42, 'n', 'property', ['declaration']),
    (0, 53, 'f', 'function', ['declaration']),
    (0, 55, 'p', 'parameter', ['declaration']),
    (0, 76, 'v', 'variable', ['declaration']),
    (0, 80, 'p', 'parameter', []),
    (0, 87, 'w', 'variable', ['declaration', 'readonly']),
    (0, 91, 'v', 'variable', []),
    (0, 101, 'w', 'variable', ['readonly']),
    (0, 105, 'K', 'property', ['readonly']),
    (1, 6, 'Box', 'class', ['declaration']),
    (1, 10, 'T', 'typeParameter', ['declaration']),
    (1, 13, 'item', 'property', ['declaration', 'readonly']),
    (1, 19, 'T', 'typeParameter', []),
    (1, 26, 'size', 'property', ['declaration']),
    (1, 43, 'get', 'method', ['declaration']),
    (1, 52, 'T', 'typeParameter', []),
    (1, 68, 'item', 'property', ['readonly']),
    (2, 5, 'Shape', 'enum', ['declaration']),
    (2, 18, 'Dot', 'enumMember', ['declaration']),
    (2, 30, 'Color', 'enum', ['declaration']),
    (2, 38, 'RED', 'enumMember', ['declaration']),
    (3, 7, 'L', 'struct', ['declaration']),
    (3, 14, 'f', 'property', ['declaration']),
    (3, 35, 'P', 'type', ['declaration']),
    (3, 37, 'a', 'parameter', ['declaration']),
    (3, 43, 'b', 'parameter', ['declaration']),
    (3, 62, 'a', 'parameter', []),
    (3, 65, 'b', 'parameter', []),
    (4, 4, 'top', 'function', ['declaration']),
    (4, 8, 's', 'parameter', ['declaration']),
    (4, 11, 'Shape', 'enum', []),
    (4, 18, 'c', 'parameter', ['declaration']),
    (4, 21, 'Color', 'enum', []),
    (4, 31, 'Box', 'class', []),
    (4, 46, 'r', 'variable', ['declaration']),
    (4, 53, 'L', 'struct', []),
    (4, 64, 'Box', 'class', []),
]


class TokensSession(Session):
    NOTIFICATIONS = Session.NOTIFICATIONS + ('window/showMessage',)

    def __init__(self, *args):
        super().__init__(*args)
        self.refreshes = []

    def receive(self, deadline):
        msg = super().receive(deadline)
        if msg.get('method') == 'workspace/semanticTokens/refresh':
            assert 'params' not in msg or msg['params'] is None, msg
            self.refreshes.append(msg)
            payload = json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': None}).encode()
            self.raw(f'Content-Length: {len(payload)}\r\n\r\n'.encode() + payload)
            return self.receive(deadline)
        return msg


def start(exe, root, sources, options=None, support=True):
    (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
        {'name': 'tokens', 'sources': sources}]}))
    s = TokensSession(exe)
    caps = s.result(s.send('initialize', {'rootUri': root.as_uri(), 'initializationOptions': options or {},
        'capabilities': {'workspace': {'semanticTokens': {'refreshSupport': support}}}}))['capabilities']
    return s, caps


def request(s, path):
    return s.reply(s.send('textDocument/semanticTokens/full', {'textDocument': {'uri': path.as_uri()}}))


def lines(text):
    """LSP lines: CR, LF, and CRLF each end one."""
    return text.replace('\r\n', '\n').replace('\r', '\n').split('\n')


def decode(text, data):
    """The tokens of {data} as (line, character, word, type, modifiers)."""
    assert len(data) % 5 == 0, data
    result, line, character, rows = [], 0, 0, lines(text)
    for i in range(0, len(data), 5):
        delta, start, length, kind, mods = data[i:i + 5]
        line += delta
        character = character + start if delta == 0 else start
        units = rows[line].encode('utf-16-le')
        word = units[2 * character:2 * (character + length)].decode('utf-16-le')
        result.append((line, character, word, TYPES[kind], [m for b, m in enumerate(MODIFIERS) if mods >> b & 1]))
    return result


def tokens(s, path, text):
    reply = request(s, path)
    assert 'result' in reply, (reply, s.transcript)
    return decode(text, reply['result']['data'])


def exact(exe, root):
    main, loose = root / 'Main.v3', root / 'Loose.v3'
    main.write_bytes(MAIN.encode())
    loose.write_bytes(MAIN.encode())
    s, caps = start(exe, root, ['Main.v3'])
    try:
        assert caps['semanticTokensProvider'] == PROVIDER, caps
        open_file(s, main, MAIN)
        reply = request(s, main)
        assert decode(MAIN, reply['result']['data']) == EXPECTED, (decode(MAIN, reply['result']['data']), s.transcript)
        # The relative encoding, exactly: the first tokens and the line change.
        assert reply['result']['data'][:15] == [0, 20, 4, 0, 1, 0, 11, 1, 9, 3, 0, 11, 1, 9, 1], reply
        i = [t[:2] for t in EXPECTED].index((1, 6))
        assert reply['result']['data'][5 * i:5 * i + 5] == [1, 6, 3, 1, 1], reply
        assert list(reply['result']) == ['data'], reply
        # A document in no project has no tokens.
        open_file(s, loose, MAIN)
        assert request(s, loose)['result'] == {'data': []}
        for params in [None, {}, {'textDocument': {}}, {'textDocument': {'uri': 7}}]:
            assert s.reply(s.send('textDocument/semanticTokens/full', params))['error']['code'] == INVALID_PARAMS
        for method in ['textDocument/semanticTokens/full/delta', 'textDocument/semanticTokens/range']:
            assert s.reply(s.send(method, {'textDocument': {'uri': main.as_uri()}}))['error']['code'] == METHOD_NOT_FOUND
        assert not s.refreshes, s.transcript
        s.shutdown()
    finally:
        s.close()


def option_off(exe, root):
    main = root / 'Main.v3'
    main.write_bytes(MAIN.encode())
    s, caps = start(exe, root, ['Main.v3'], {'semanticTokens': False})
    try:
        assert 'semanticTokensProvider' not in caps, caps
        open_file(s, main, MAIN)
        error = request(s, main)['error']
        assert error == {'code': METHOD_NOT_FOUND, 'message': 'method not found: textDocument/semanticTokens/full'}, error
        change(s, main, MAIN + '// edited\n', 2)
        assert request(s, main)['error']['code'] == METHOD_NOT_FOUND
        assert not s.refreshes, s.transcript
        s.shutdown()
    finally:
        s.close()


# A file the server can't analyze, here one closing brace short, gets
# ContentModified, so that the client keeps the tokens it has.
UNBALANCED = 'component A {\n\tdef f() -> int { return 1; }\n'
SIMPLE = 'component A {\n\tdef f() -> int { return 1; }\n}\n'


def unanalyzable(exe, root):
    path, broken = root / 'A.v3', root / 'Broken.v3'
    path.write_text(SIMPLE)
    broken.write_text(UNBALANCED)
    s, _ = start(exe, root, ['A.v3', 'Broken.v3'])
    try:
        open_file(s, path, SIMPLE)
        error = request(s, path)['error']
        assert error['code'] == CONTENT_MODIFIED, error
        assert 'parse errors' in error['message'], error
        broken.write_text(SIMPLE.replace('A', 'B'))
        s.send('workspace/didChangeWatchedFiles', {'changes': [{'uri': broken.as_uri(), 'type': 2}]}, request=False)
        assert tokens(s, path, SIMPLE) == [(0, 10, 'A', 'namespace', ['declaration']),
                                           (1, 5, 'f', 'function', ['declaration'])], s.transcript
        change(s, path, UNBALANCED, 2)
        assert request(s, path)['error']['code'] == CONTENT_MODIFIED
        change(s, path, SIMPLE, 3)
        assert len(tokens(s, path, SIMPLE)) == 2
        s.shutdown()
    finally:
        s.close()


# Method-body fallback: the broken body is excluded, and later tokens move by
# the length its edit added. Current-text repair: a file opened with the
# error empties that body, and nothing else moves.
FALLBACK = ('component A {\n'
            '\tdef broken() -> int { var x = 1; return x; }\n'
            '\t/* é\U0001d11e */ def after(y: int) -> int { return y; }\n'
            '}\n')
BROKEN = FALLBACK.replace('var x = 1;', 'var x = ;\n\t\t/* é */ var z = x;')
OUTSIDE = [(0, 10, 'A', 'namespace', ['declaration']),
           (1, 5, 'broken', 'function', ['declaration'])]
AFTER = [('after', 'function', ['declaration']), ('y', 'parameter', ['declaration']),
         ('y', 'parameter', [])]


def after(text):
    line = lines(text).index('\t/* é\U0001d11e */ def after(y: int) -> int { return y; }')
    return [(line, c, w, t, m) for c, (w, t, m) in zip([15, 21, 45], AFTER)]


def fallback(exe, root):
    path = root / 'A.v3'
    path.write_text(FALLBACK)
    s, _ = start(exe, root, ['A.v3'])
    try:
        open_file(s, path, FALLBACK)
        inside = [(1, 27, 'x', 'variable', ['declaration']), (1, 41, 'x', 'variable', [])]
        assert tokens(s, path, FALLBACK) == OUTSIDE + inside + after(FALLBACK), s.transcript
        change(s, path, BROKEN, 2)
        assert tokens(s, path, BROKEN) == OUTSIDE + after(BROKEN), s.transcript
        assert after(BROKEN)[0][0] == 3, BROKEN
        s.shutdown()
    finally:
        s.close()


def repair(exe, root):
    path = root / 'A.v3'
    path.write_text(BROKEN)
    s, _ = start(exe, root, ['A.v3'])
    try:
        open_file(s, path, BROKEN)
        assert tokens(s, path, BROKEN) == OUTSIDE + after(BROKEN), s.transcript
        s.shutdown()
    finally:
        s.close()


# Library decides whether Main's Kind is a class or a variant, which changes
# Main's tokens without an edit to Main.
USER = 'component Main { def f(k: Kind) { } }\n'
KIND_CLASS = 'class Kind { }\n'
KIND_VARIANT = 'type Kind { case A; }\n'


def refresh(exe, root, support):
    main, library = root / 'Main.v3', root / 'Library.v3'
    main.write_text(USER)
    library.write_text(KIND_CLASS)
    s, _ = start(exe, root, ['Main.v3', 'Library.v3'], support=support)
    try:
        open_file(s, main, USER)
        open_file(s, library, KIND_CLASS)
        kind = (0, 26, 'Kind', 'class', [])
        assert tokens(s, main, USER)[3] == kind
        tokens(s, library, KIND_CLASS)
        assert not s.refreshes, s.transcript
        change(s, library, KIND_VARIANT, 2)
        assert tokens(s, main, USER)[3] == kind[:3] + ('enum', [])
        assert len(s.refreshes) == int(support), s.transcript
        # The same tokens, after a comment: no refresh.
        change(s, library, KIND_VARIANT + '// trailing\n', 3)
        assert tokens(s, main, USER)[3] == kind[:3] + ('enum', [])
        assert len(s.refreshes) == int(support), s.transcript
        # Main's own edit moves its tokens; the client asks for them anyway.
        edited = '\n' + USER
        change(s, main, edited, 2)
        assert tokens(s, main, edited)[3] == (1,) + kind[1:3] + ('enum', [])
        assert len(s.refreshes) == int(support), s.transcript
        # Library stops parsing: Main keeps its tokens, and recovering to the
        # same tokens sends nothing.
        change(s, library, 'type Kind {', 4)
        assert request(s, main)['error']['code'] == CONTENT_MODIFIED
        change(s, library, KIND_VARIANT, 5)
        assert tokens(s, main, edited)[3] == (1,) + kind[1:3] + ('enum', [])
        assert len(s.refreshes) == int(support), s.transcript
        # Main leaves the project: its tokens are gone, which needs a refresh.
        (root / '.virgil-lsp.json').write_text(json.dumps({'version': 1, 'projects': [
            {'name': 'tokens', 'sources': ['Library.v3']}]}))
        s.send('workspace/didChangeWatchedFiles', {'changes': [
            {'uri': (root / '.virgil-lsp.json').as_uri(), 'type': 2}]}, request=False)
        assert request(s, main)['result'] == {'data': []}
        assert len(s.refreshes) == 2 * int(support), s.transcript
        s.shutdown()
    finally:
        s.close()


def request_baselines(exe, root, support):
    main, library = root / 'Main.v3', root / 'Library.v3'
    main.write_text(USER)
    library.write_text(KIND_CLASS)
    s, _ = start(exe, root, ['Library.v3'], support=support)
    try:
        # Load the project before opening its excluded file. Its empty reply
        # must remain the baseline when a later configuration includes it.
        open_file(s, library, KIND_CLASS)
        tokens(s, library, KIND_CLASS)
        open_file(s, main, USER)
        assert request(s, main)['result'] == {'data': []}
        assert len(s.refreshes) == 0, s.transcript
        config = root / '.virgil-lsp.json'
        config.write_text(json.dumps({'version': 1, 'projects': [
            {'name': 'tokens', 'sources': ['Main.v3', 'Library.v3']}]}))
        s.send('workspace/didChangeWatchedFiles', {'changes': [
            {'uri': config.as_uri(), 'type': 2}]}, request=False)
        tokens(s, library, KIND_CLASS)
        assert len(s.refreshes) == int(support), s.transcript
        assert tokens(s, main, USER)[3] == (0, 26, 'Kind', 'class', [])

        # This real compiler crash yields no snapshot. A request for edited
        # Main receives ContentModified, then repairing Library must refresh
        # Main without a further edit or token request for it.
        crash = KIND_CLASS + 'def x: i32 = 0i;\n'
        change(s, library, crash, 2)
        edited = '\n' + USER
        change(s, main, edited, 2)
        assert request(s, main)['error']['code'] == CONTENT_MODIFIED
        assert len(s.refreshes) == int(support), s.transcript
        change(s, library, KIND_CLASS, 3)
        tokens(s, library, KIND_CLASS)
        assert len(s.refreshes) == 2 * int(support), s.transcript
        assert tokens(s, main, edited)[3] == (1, 26, 'Kind', 'class', [])

        # A normal open/edit reply and a same-identity ContentModified reply
        # preserve the displayed tokens and need no extra refresh on recovery.
        change(s, main, USER, 3)
        tokens(s, main, USER)
        change(s, library, crash, 4)
        assert request(s, main)['error']['code'] == CONTENT_MODIFIED
        change(s, library, KIND_CLASS, 5)
        tokens(s, library, KIND_CLASS)
        tokens(s, main, USER)
        assert len(s.refreshes) == 2 * int(support), s.transcript
        s.shutdown()
    finally:
        s.close()


def failed_refresh(exe, root, support):
    main, library = root / 'Main.v3', root / 'Library.v3'
    main.write_text(USER)
    library.write_text(KIND_CLASS)
    s, _ = start(exe, root, ['Main.v3', 'Library.v3'], support=support)
    try:
        open_file(s, main, USER)
        open_file(s, library, KIND_CLASS)
        assert tokens(s, main, USER)[3] == (0, 26, 'Kind', 'class', [])
        tokens(s, library, KIND_CLASS)
        assert len(s.refreshes) == 0, s.transcript
        change(s, library, KIND_VARIANT, 2)
        tokens(s, library, KIND_VARIANT)
        assert len(s.refreshes) == int(support), s.transcript
        change(s, library, KIND_VARIANT + 'def x: i32 = 0i;\n', 3)
        assert request(s, main)['error']['code'] == CONTENT_MODIFIED
        assert len(s.refreshes) == int(support), s.transcript
        change(s, library, KIND_VARIANT, 4)
        tokens(s, library, KIND_VARIANT)
        assert len(s.refreshes) == 2 * int(support), s.transcript
        assert tokens(s, main, USER)[3] == (0, 26, 'Kind', 'enum', [])
        assert len(s.refreshes) == 2 * int(support), s.transcript
        s.shutdown()
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    for case in [exact, option_off, unanalyzable, fallback, repair,
                 lambda e, r: refresh(e, r, True), lambda e, r: refresh(e, r, False),
                 lambda e, r: request_baselines(e, r, True),
                 lambda e, r: request_baselines(e, r, False),
                 lambda e, r: failed_refresh(e, r, True),
                 lambda e, r: failed_refresh(e, r, False)]:
        with tempfile.TemporaryDirectory() as tmp:
            case(exe, Path(tmp).resolve())
    print('semantic tokens: all checks passed')


if __name__ == '__main__':
    main()
