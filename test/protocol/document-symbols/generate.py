#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Regenerate this transcript's independently specified expected symbols."""
import json
from pathlib import Path

HERE = Path(__file__).parent
URI = "file:///not-on-disk/outline.v3"
TEXT = """component C {
  def field = "{};";
  def method() { if (true) { return; } }
}
class K { var field: int; }
enum E { A, B }
layout L { +0 field: int; =4; }
packing P(x: 1): 2 = 0b0x;"""


def position(line, character):
    return dict(line=line, character=character)


def span(bl, bc, el, ec):
    return dict(start=position(bl, bc), end=position(el, ec))


def symbol(name, kind, bl, bc, el, ec, nl, nc, children=None):
    result = dict(name=name, kind=kind, range=span(bl, bc, el, ec),
                  selectionRange=span(nl, nc, nl, nc + len(name)))
    if children:
        result["children"] = children
    return result


OUTLINE = [
    symbol("C", 3, 0, 0, 3, 1, 0, 10, [
        symbol("field", 8, 1, 2, 1, 20, 1, 6),
        symbol("method", 6, 2, 2, 2, 40, 2, 6),
    ]),
    symbol("K", 5, 4, 0, 4, 27, 4, 6, [
        symbol("field", 8, 4, 10, 4, 25, 4, 14),
    ]),
    symbol("E", 10, 5, 0, 5, 15, 5, 5, [
        symbol("A", 22, 5, 9, 5, 10, 5, 9),
        symbol("B", 22, 5, 12, 5, 13, 5, 12),
    ]),
    symbol("L", 23, 6, 0, 6, 31, 6, 7, [
        symbol("field", 8, 6, 11, 6, 25, 6, 14),
    ]),
    symbol("P", 23, 7, 0, 7, 26, 7, 8),
]


def request(id, method, params=None):
    msg = dict(jsonrpc="2.0", id=id, method=method)
    if params is not None:
        msg["params"] = params
    return msg


def notify(method, params):
    return dict(jsonrpc="2.0", method=method, params=params)


def reply(id, result):
    # Envelopes have the server's explicit order; result members are sorted.
    return dict(jsonrpc="2.0", id=id, result=result)


def frame(msg, result_sorted=False):
    if result_sorted:
        payload = ('{"jsonrpc":"2.0","id":' + str(msg["id"]) + ',"result":' +
                   json.dumps(msg["result"], separators=(",", ":"), sort_keys=True) + '}').encode()
    else:
        payload = json.dumps(msg, separators=(",", ":")).encode()
    return f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload


inputs = [
    request(1, "initialize", {}),
    notify("textDocument/didOpen", dict(textDocument=dict(uri=URI, languageId="virgil", version=1, text=TEXT))),
    request(2, "textDocument/documentSymbol", dict(textDocument=dict(uri="file:/not-on-disk/outline.v3"))),
    notify("textDocument/didChange", dict(textDocument=dict(uri=URI, version=2), contentChanges=[dict(text="class Edited {}")])),
    request(3, "textDocument/documentSymbol", dict(textDocument=dict(uri=URI))),
    notify("textDocument/didChange", dict(textDocument=dict(uri=URI, version=3), contentChanges=[dict(text="class Edited { def bad = ; }")])),
    request(4, "textDocument/documentSymbol", dict(textDocument=dict(uri=URI))),
    notify("textDocument/didClose", dict(textDocument=dict(uri=URI))),
    request(5, "textDocument/documentSymbol", dict(textDocument=dict(uri=URI))),
    request(6, "shutdown"),
    notify("exit", {}),
]
outputs = [
    reply(1, dict(capabilities=dict(documentSymbolProvider=True,
                                   textDocumentSync=dict(change=1, openClose=True, save=dict(includeText=False))),
                  serverInfo=dict(name="virgil-lsp", version="@VERSION@"))),
    reply(2, OUTLINE),
    reply(3, [symbol("Edited", 5, 0, 0, 0, 15, 0, 6)]),
    reply(4, []), reply(5, []), reply(6, None),
]
(HERE / "input").write_bytes(b"".join(frame(m) for m in inputs))
(HERE / "output").write_bytes(b"".join(frame(m, True) for m in outputs))
(HERE / "status").write_text("0\n")
