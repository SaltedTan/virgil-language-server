#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Drive exact diagnostic ranges through the real worker and CLI.

Usage: python3 test/protocol/coordinates.py build/virgil-lsp [--evidence-dir DIR]
The worker's v3 wire response is the executable interface for byte offsets;
the development CLI and stdio API intentionally retain compiler coordinates.
"""

import argparse
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import tempfile
import time

from worker import Session, expected_snapshot


def leb(value):
    result = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        done = (value == 0 and byte < 64) or (value == -1 and byte >= 64)
        result.append(byte if done else byte | 128)
        if done:
            return bytes(result)


def string(value):
    if value is None:
        return leb(-1)
    data = value.encode() if isinstance(value, str) else value
    return leb(len(data)) + data


class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        assert size >= 0 and self.pos + size <= len(self.data), "truncated response"
        data = self.data[self.pos:self.pos + size]
        self.pos += size
        return data

    def integer(self):
        value = shift = 0
        while True:
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            shift += 7
            if byte < 128:
                return value - (1 << shift) if byte & 64 else value
            assert shift < 70, "invalid LEB128"

    def string(self):
        size = self.integer()
        return None if size == -1 else self.take(size).decode()

    def finish(self):
        assert self.pos == len(self.data), "unexpected response fields"


class Worker:
    def __init__(self, exe):
        self.proc = subprocess.Popen(
            ["bash", "-c", 'exec 3<&0 4>&1; exec "$1" --serve', "worker", str(exe)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.buffer = b""
        self.next_id = 0
        hello = Reader(self.frame())
        assert hello.take(1) == b"\x01"
        self.hello = dict(protocol=hello.integer(), version=hello.string(),
                          revision=hello.string(), heapBytes=hello.integer())
        hello.finish()
        assert self.hello["protocol"] == 3, self.hello

    def frame(self, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            if len(self.buffer) >= 4:
                size = struct.unpack("<I", self.buffer[:4])[0]
                assert size <= 16 * 1024 * 1024
                if len(self.buffer) >= 4 + size:
                    data, self.buffer = self.buffer[4:4 + size], self.buffer[4 + size:]
                    return data
            remaining = deadline - time.monotonic()
            assert remaining > 0, "worker response timed out"
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            assert ready, "worker response timed out"
            data = os.read(self.proc.stdout.fileno(), 65536)
            assert data, ("worker closed its output", self.proc.poll())
            self.buffer += data

    def analyze(self, sources, flags=0, options=()):
        self.next_id += 1
        payload = b"\x02" + leb(self.next_id) + leb(flags) + leb(2000) + leb(len(options))
        for option in options:
            payload += string(option)
        payload += leb(len(sources))
        for path, text in sources:
            payload += string(path) + string(text)
        self.proc.stdin.write(struct.pack("<I", len(payload)) + payload)
        self.proc.stdin.flush()
        reader = Reader(self.frame())
        assert reader.take(1) == b"\x03"
        request, analyses, uid, live = struct.unpack("<IIIQ", reader.take(20))
        assert request == self.next_id
        flags = reader.take(1)[0]
        stats = [reader.integer() for _ in range(6)]
        diagnostics = []
        for _ in range(reader.integer()):
            diagnostics.append(dict(path=reader.string(),
                                    **dict(zip(("beginLine", "beginColumn", "endLine", "endColumn",
                                                "beginOffset", "endOffset"),
                                               [reader.integer() for _ in range(6)])),
                                    kind=reader.string(), message=reader.string()))
        declarations, occurrences = [], []
        if flags & 4:
            paths = [reader.string() for _ in range(reader.integer())]
            for _ in range(reader.integer()):
                kind, name, path = reader.take(1)[0], reader.string(), paths[reader.integer()]
                declarations.append(dict(kind=kind, name=name, path=path,
                                         range=[reader.integer() for _ in range(4)]))
            for _ in range(reader.integer()):
                path, count = paths[reader.integer()], reader.integer()
                for _ in range(count):
                    span = [reader.integer() for _ in range(4)]
                    occurrences.append(dict(path=path, range=span,
                                            declaration=declarations[reader.integer()]))
        reader.finish()
        return dict(flags=flags, parseUs=stats[0], verifyUs=stats[1],
                    diagnostics=diagnostics, occurrences=occurrences)

    def close(self):
        self.proc.stdin.close()
        try:
            status = self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise
        stderr = self.proc.stderr.read().decode()
        self.proc.stdout.close()
        self.proc.stderr.close()
        assert status == 0 and not stderr, (status, stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    exe = args.exe.resolve()
    evidence = []
    worker = Worker(exe.with_name("virgil-lsp-worker"))
    evidence.append(dict(hello=worker.hello))

    def check(name, text, spans, flags=0, path="ranges.v3", options=()):
        result = worker.analyze([(path, text)], flags, options)
        actual = [(d["beginOffset"], d["endOffset"]) for d in result["diagnostics"]]
        assert actual == spans, (name, actual, spans, result)
        raw = text.encode()
        selected = [raw[begin:end].decode() if begin >= 0 else None for begin, end in spans]
        evidence.append(dict(scenario=name, source=text, selectedBytes=selected, result=result))
        return result

    try:
        text = 'component C {\n/*\t*/ def x: int = "s";\n\tdef y: int = "t";\n}\n'
        spans = [(text.encode().index(b'"s"'), text.encode().index(b'"s"') + 3),
                 (text.encode().index(b'"t"'), text.encode().index(b'"t"') + 3)]
        check("block-comment tab and whitespace tab", text, spans)
        for prefix in ['/*\t😀é*/ ', '/* start\n\t😀é*/ ', '/*\t*/\t',
                       'def s = "/*\\\"//😀é"; ']:
            text = 'component C {\n' + prefix + 'def x: int = "s";\n}\n'
            begin = text.encode().rindex(b'"s"')
            check("UTF-8 and comment/literal boundaries: " + prefix, text, [(begin, begin + 3)])
        text = 'component C { // trailing\t😀'
        check("EOF after trailing line comment", text, [(len(text.encode()), len(text.encode()))])
        check("token ends before trailing line comment", '/*\t*/ def if// trailing\t😀', [(10, 12), (28, 28)])
        for op in ["[]", "[]="]:
            prefix = 'component C {\n/*\t*/ def f() => C.'
            text = prefix + op + ';\n}\n'
            check("unresolved retrospective " + op, text,
                  [(len(prefix.encode()), len(prefix.encode()) + (3 if op.endswith('=') else 2))])
        for op, spans in [("[ ]", [(33, 33)]), ("[ ]=", [(33, 33), (37, 37)])]:
            check("reject whitespace inside member operator " + op,
                  prefix + op + ';\n}\n', spans)
        cases = [
            ('class C { def [] -> int { return 1; }\n/*\t*/ def f(c: C) -> int => c.[];\n}\n', [(66, 70)]),
            ('class C { def [] = x: int {}\n/*\t*/ def f(c: C) -> int => c.[]=;\n}\n', [(57, 62)]),
            ('component C {\n/*\t*/ def f() => C.[]; def x: int = "s";\n}\n', [(50, 53), (33, 35)]),
        ]
        for text, spans in cases:
            check("expression enclosing retrospective token", text, spans)
        # Language options reach the worker's parser and verifier, and last
        # for one analysis: the same text is checked again without them.
        described = '/*\t😀*/ class D describes C { }\nclass C descriptor D { }\n'
        begin = described.encode().index(b'describes')
        check("descriptor clause without -lang:descriptors", described, [(begin, begin)])
        check("descriptor clause with -lang:descriptors", described, [], options=["-lang:descriptors"])
        check("descriptor clause after an analysis with options", described, [(begin, begin)])
        unnamed = check("source without a captured filename", 'component C { def x: int = "s"; }',
                        [(-1, -1)], path=None)
        assert unnamed['diagnostics'][0]['path'] == '<null>', unnamed
        empty = worker.analyze([])
        assert empty['diagnostics'] == [], empty
        evidence.append(dict(scenario='attempt program-level diagnostic with empty program', result=empty))

        with tempfile.TemporaryDirectory(prefix="coordinate-fixtures-", dir=exe.parent) as temporary:
            work = Path(temporary)
            command = [str(exe), 'analyze', str(work / 'missing.v3')]
            missing = subprocess.run(command, capture_output=True, text=True, timeout=5)
            assert missing.returncode == 1 and not missing.stdout, missing
            evidence.append(dict(scenario='attempt program-level diagnostic with missing file',
                                 command=command, stdout=missing.stdout, stderr=missing.stderr))
            for op, text, column in [
                ('[]', 'class C { def [x: int] -> int { return x; }\n/*\t*/ def f(c: C) -> int => c.[](1);\n}\n', 36),
                ('[]=', 'class C { def [x: int] = y: int {}\n/*\t*/ def f(c: C) => c.[]=(1, 2);\n}\n', 29),
            ]:
                path = work / ('get.v3' if op == '[]' else 'set.v3')
                path.write_text(text)
                result = check("binding coordinates " + op, text, [], flags=1, path=str(path))
                use = [o for o in result['occurrences'] if o['declaration']['name'] == op]
                assert len(use) == 1, use
                assert use[0]['range'] == [2, column, 2, column + len(op)], use
                assert use[0]['declaration']['range'] == [1, 15, 1, 15 + len(op)], use
                command = [str(exe), 'analyze', '--bindings', '--repeat=2', str(path)]
                cli = subprocess.run(command, capture_output=True, text=True, timeout=5)
                expected = f'{path}:2:{column}-2:{column + len(op)} -> METHOD {op} @ {path}:1:15-1:{15 + len(op)}'
                assert cli.returncode == 0 and expected in cli.stdout.splitlines(), cli
                evidence.append(dict(command=command, stdout=cli.stdout, stderr=cli.stderr))

            path = work / 'semantic.v3'
            text = 'component C {\n/*\t😀é*/ def x: int = "s";\n}\n'
            path.write_text(text)
            result = worker.analyze([(str(path), text)])
            command = [str(exe), 'analyze', str(path)]
            cli = subprocess.run(command, capture_output=True, text=True, timeout=5)
            d = result['diagnostics'][0]
            assert cli.returncode == 1 and cli.stdout.startswith(f'{path}:{d["beginLine"]}:{d["beginColumn"]}: '), cli
            evidence.append(dict(command=command, stdout=cli.stdout, stderr=cli.stderr))
            session = Session(exe)
            try:
                session.initialize()
                uri = path.as_uri()
                overlay = 'component C {\n/*\t*/ def x: int = "overlay";\n}\n'
                session.send('textDocument/didOpen', dict(textDocument=dict(uri=uri, languageId='virgil', version=1, text=overlay)), request=False)
                response = session.result(session.analyze([uri]))
                exact = worker.analyze([(str(path), overlay)])
                for expected in exact['diagnostics']:
                    expected.pop('beginOffset')
                    expected.pop('endOffset')
                assert response['diagnostics'] == exact['diagnostics'], response
                session.snapshot(response)
                # A real compiler diagnostic with a huge unresolved name is
                # under the wire-size limit but over the decoder's allocation
                # budget. Exercise admission through the running server,
                # retaining its previous snapshot and recovering afterward.
                oversized = work / 'large-diagnostic.v3'
                oversized.write_text('component C { def f() => C.' + 'x' * 600000 + '; }')
                rejected = session.reply(session.analyze([oversized.as_uri()]))
                assert rejected['error']['code'] == -32603, rejected
                message = rejected['error']['message']
                assert 'program is too large to analyze' in message, rejected
                assert 'allocation budget of 16777216 bytes' in message, rejected
                assert "reduce the program's source set" in message, rejected
                assert 'malformed' not in message, rejected
                session.snapshot(response)
                session.send('textDocument/didChange', dict(textDocument=dict(uri=uri, version=2),
                             contentChanges=[dict(text='component C {}')]), request=False)
                recovered = session.result(session.analyze([uri]))
                assert recovered == expected_snapshot(2, [uri], version=2), recovered
                session.snapshot(recovered)
                evidence.append(dict(scenario='oversized diagnostic allocation guard',
                                     unresolvedNameBytes=600000, rejected=rejected, recovered=recovered))
                session.shutdown()
                evidence.append(dict(scenario='stdio overlay and retained snapshot', transcript=session.transcript, stderr=session.logs()))
            finally:
                session.close()

        for count in [32768, 262144, 1048576]:
            text = 'def s = "' + 'a\\"' * count
            start = time.monotonic()
            result = worker.analyze([('malformed.v3', text)])
            elapsed = time.monotonic() - start
            assert len(result['diagnostics']) == 1 and result['diagnostics'][0]['message'] == 'invalid string literal', result
            assert (result['diagnostics'][0]['beginOffset'], result['diagnostics'][0]['endOffset']) == (8, 8), result
            assert result['parseUs'] < 1000000 and elapsed < 2, (count, elapsed, result)
            evidence.append(dict(scenario='bounded malformed string replay', escapedQuotes=count,
                                 sourceBytes=len(text), elapsedSeconds=elapsed, result=result))
        check('worker recovery after malformed strings', 'component C {}', [])
    finally:
        worker.close()
        if args.evidence_dir:
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            (args.evidence_dir / 'coordinates-live.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print('Real worker offsets, language options, CLI coordinates, binding exports, stdio overlays, allocation guard, and malformed-string bounds passed.')


if __name__ == '__main__':
    main()
