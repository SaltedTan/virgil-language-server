#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""textDocument/formatting over the server's real pipes (ADR-0019 decision 5).

Each case applies the returned edits to the document's text and checks the
exact result: UTF-16 positions after non-ASCII text, CR LF and mixed line
ends, the end of the text mirrored, a comment between two edits, the merged
edit above 10,000 edits, and the same text as `virgil-lsp format` for files
that end with a line end. It also checks the refusals: no edits for a syntax
error, a lone CR, a project file, or a document that isn't Virgil;
InvalidParams for malformed options; RequestFailed for the parse budget,
growth past the document store, a reply over 4 MiB, a merged edit around
U+FFFD, which stands for an unpaired surrogate, and a document marked out
of sync by a rejected change, a skipped frame, or an undecodable payload,
until an accepted full-text change or a reopen. Each session shuts down and
exits with status 0, and every receive has a deadline.

Run from the repository root:

  python3 test/protocol/formatting.py build/virgil-lsp
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from worker import Session, send_payload

ROOT = Path(__file__).resolve().parents[2]
INVALID_PARAMS, REQUEST_FAILED = -32602, -32803
OPTIONS = {"tabSize": 4, "insertSpaces": True}
OUT_OF_SYNC = "the server's copy of this document is out of date"
MAX_TOKENS = 350000
MAX_PAYLOAD = 4 * 1024 * 1024
STORE_BYTES = 1024 * 1024


class FormatSession(Session):
    # A rejected change over the store's capacity also shows a warning.
    NOTIFICATIONS = Session.NOTIFICATIONS + ("window/showMessage",)

    def __init__(self, exe, *options, **popen):
        super().__init__(exe, *options, **popen)
        self.versions = {}

    def start(self, capabilities=None, **params):
        self.result(self.send("initialize", dict(params, capabilities=capabilities or {})))
        self.send("initialized", {}, request=False)

    def open(self, uri, text, language="virgil"):
        self.versions[uri] = 1
        self.send("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": language, "version": 1, "text": text}}, request=False)

    def change(self, uri, text=None):
        """A full-text change, or a version-only one if {text} is None."""
        self.versions[uri] += 1
        changes = [] if text is None else [{"text": text}]
        self.send("textDocument/didChange", {"textDocument": {"uri": uri, "version": self.versions[uri]},
                                             "contentChanges": changes}, request=False)

    def close_document(self, uri):
        self.send("textDocument/didClose", {"textDocument": {"uri": uri}}, request=False)

    def format(self, uri, options=OPTIONS, timeout=60):
        """The reply to a formatting request for {uri}."""
        return self.reply(self.send("textDocument/formatting", {"textDocument": {"uri": uri}, "options": options}),
                          timeout)

    def edits(self, uri, timeout=60):
        msg = self.format(uri, timeout=timeout)
        assert "result" in msg, msg
        return msg["result"]

    def refused(self, uri, code=REQUEST_FAILED, text=OUT_OF_SYNC):
        msg = self.format(uri)
        assert msg.get("error", {}).get("code") == code, msg
        assert text in msg["error"]["message"], msg
        return msg["error"]["message"]

    def null_reply(self, timeout=30):
        """The error response, with a null ID, to a payload that can't be decoded."""
        deadline = time.monotonic() + timeout
        while None not in self.waiting:
            msg = self.receive(deadline)
            if "id" in msg:
                self.waiting[msg["id"]] = msg
        self.answered.discard((type(None), None))
        return self.waiting.pop(None)

    def take_request(self, timeout=10):
        """The next request the server sent."""
        deadline = time.monotonic() + timeout
        while True:
            for key, msg in list(self.waiting.items()):
                if "method" in msg:
                    return self.waiting.pop(key)
            msg = self.receive(deadline)
            if "id" in msg:
                self.waiting[msg["id"]] = msg

    def respond(self, request, **member):
        payload = json.dumps(dict({"jsonrpc": "2.0", "id": request["id"]}, **member),
                             separators=(",", ":")).encode()
        send_payload(self, payload)


def offsets(text):
    """The start of each LSP line of {text}: LF, CR LF, and CR end a line."""
    starts, i = [0], 0
    while i < len(text):
        if text[i] == "\r" and text[i + 1:i + 2] == "\n":
            i += 1
        if text[i] in "\r\n":
            starts.append(i + 1)
        i += 1
    return starts


def index(text, starts, position):
    """The string index of an LSP {position}, in UTF-16 code units."""
    i, units = starts[position["line"]], 0
    while units < position["character"]:
        assert text[i] not in "\r\n", ("character past the end of its line", position)
        units += 2 if ord(text[i]) > 0xFFFF else 1
        i += 1
    assert units == position["character"], ("position inside a surrogate pair", position)
    return i


def apply(text, edits):
    """{text} with {edits} applied, which must be sorted and not overlap."""
    starts, spans = offsets(text), []
    for e in edits:
        begin, end = index(text, starts, e["range"]["start"]), index(text, starts, e["range"]["end"])
        assert begin <= end, e
        assert not spans or spans[-1][1] <= begin, ("edits out of order or overlapping", edits)
        spans.append((begin, end, e["newText"]))
    for begin, end, new in reversed(spans):
        text = text[:begin] + new + text[end:]
    return text


def edit(sl, sc, el, ec, text):
    return {"range": {"start": {"line": sl, "character": sc}, "end": {"line": el, "character": ec}},
            "newText": text}


def formats(s, uri, text, expected):
    """Opens {text} as {uri}, checks that formatting gives {expected}, and closes it."""
    s.open(uri, text)
    edits = s.edits(uri)
    assert apply(text, edits) == expected, (text, edits)
    s.close_document(uri)
    return edits


def positions(exe):
    s = FormatSession(exe)
    try:
        s.start()
        # Edits after non-ASCII text count UTF-16 code units: "é" is one and
        # the emoji two.
        text = 'class C {\n  def s = "é😀";def t=1;\n}\n'
        edits = formats(s, "untitled:unicode.v3", text, 'class C {\n\tdef s = "é😀";def t = 1;\n}\n')
        assert edits == [edit(1, 0, 1, 2, "\t"), edit(1, 21, 1, 21, " "), edit(1, 22, 1, 22, " ")], edits
        # CR LF keeps each line end, and no edit splits one.
        text = 'class C {\r\n  def s="é";  \r\n\r\n}\r\n'
        edits = formats(s, "untitled:crlf.v3", text, 'class C {\r\n\tdef s = "é";\r\n\r\n}\r\n')
        assert edits == [edit(1, 0, 1, 2, "\t"), edit(1, 7, 1, 7, " "), edit(1, 8, 1, 8, " "),
                         edit(1, 12, 1, 14, "")], edits
        # LF and CR LF mixed.
        formats(s, "untitled:mixed.v3", "class C {\n  def x = 1;\r\n  def y = 2;\n}\r\n",
                "class C {\n\tdef x = 1;\r\n\tdef y = 2;\n}\r\n")
        # One gap with a comment between two changed runs: two edits, neither
        # covering the comment on line 1, characters 2 to 9. Each edit is the
        # changed part of its run, so the first keeps the run's last tab.
        edits = formats(s, "untitled:comment.v3", "def f() {   \n\t\t// note   \n   var x = 1;\n}\n",
                        "def f() {\n\t// note\n\tvar x = 1;\n}\n")
        assert edits == [edit(0, 9, 1, 1, "\n"), edit(1, 9, 2, 3, "\n\t")], edits
        assert edits[0]["range"]["end"]["character"] <= 2 and edits[1]["range"]["start"]["character"] >= 9
        # The text's end is mirrored: no line end is added to a text without
        # one, and the whitespace after the last token or comment goes.
        for text, expected in (("def f() { }", "def f() { }"), ("def f() {}", "def f() { }"),
                               ("def f() { }\n  ", "def f() { }"), ("def f() { }\n\n", "def f() { }\n"),
                               ("def f() { }\r\n\r\n\n", "def f() { }\r\n"), ("// c\n\n\n", "// c\n"),
                               ("\n\n", ""), ("  ", "")):
            formats(s, "untitled:end.v3", text, expected)
        # Ending inside an open block comment, and an already formatted text.
        for text in ("/* note\n\n", "def f() { }\n", "def f() { }"):
            s.open("untitled:same.v3", text)
            assert s.edits("untitled:same.v3") == [], text
            s.close_document("untitled:same.v3")
        # A cancelled request is answered once, as if it hadn't been.
        s.open("untitled:cancel.v3", "def f() {}\n")
        ident = s.send("textDocument/formatting", {"textDocument": {"uri": "untitled:cancel.v3"}, "options": OPTIONS})
        s.send("$/cancelRequest", {"id": ident}, request=False)
        assert s.result(ident) == [edit(0, 9, 0, 9, " ")]
        s.shutdown()
    finally:
        s.close()


def refusals(exe):
    s = FormatSession(exe)
    try:
        s.start()
        uri = "untitled:refusals.v3"
        # Not open.
        assert s.edits(uri) == []
        for bad in (None, [], {}, {"textDocument": {"uri": 1}, "options": OPTIONS},
                    {"textDocument": {"uri": ""}, "options": OPTIONS},
                    {"textDocument": {"uri": "file://remote/a.v3"}, "options": OPTIONS},
                    {"textDocument": {"uri": uri}}, {"textDocument": {"uri": uri}, "options": None},
                    {"textDocument": {"uri": uri}, "options": {}},
                    {"textDocument": {"uri": uri}, "options": {"tabSize": 4}},
                    {"textDocument": {"uri": uri}, "options": {"insertSpaces": True}},
                    {"textDocument": {"uri": uri}, "options": {"tabSize": -1, "insertSpaces": "false"}},
                    {"textDocument": {"uri": uri}, "options": {"tabSize": -1, "insertSpaces": False}},
                    {"textDocument": {"uri": uri}, "options": {"tabSize": 4, "insertSpaces": "false"}},
                    {"textDocument": {"uri": uri}, "options": {"tabSize": "4", "insertSpaces": False}},
                    {"textDocument": {"uri": uri}, "options": {"tabSize": 4.5, "insertSpaces": False}},
                    {"textDocument": {"uri": uri}, "options": {"tabSize": 2 ** 31, "insertSpaces": False}}):
            msg = s.reply(s.send("textDocument/formatting", bad))
            assert msg.get("error", {}).get("code") == INVALID_PARAMS, (bad, msg)
        # The option values are ignored, and other members are allowed.
        s.open(uri, "def f() {\n    return;\n}\n")
        for options in (OPTIONS, {"tabSize": 0, "insertSpaces": False, "trimFinalNewlines": True, "x": "y"}):
            assert s.format(uri, options)["result"] == [edit(1, 0, 1, 4, "\t")]
        s.close_document(uri)
        # A syntax error, or a CR that no line feed follows, gets no edits and
        # no error: the folding parse and the compiler's lexer disagree about
        # where the comment before "-> int" ends.
        for text in ("def f( {\n  var x = 1;\n}\n", "def f() // note\r -> int\n{\n\treturn;\n}\n"):
            s.open(uri, text)
            assert s.edits(uri) == [], text
            s.close_document(uri)
        # A project file, and a document whose language isn't Virgil.
        for name, language in ((".virgil-lsp.json", "json"), ("a.v3", "plaintext")):
            other = "file:///tmp/virgil-format-" + name
            s.open(other, "def f() {\n    return;\n}\n", language)
            assert s.edits(other) == [], (name, language)
            s.close_document(other)
        # A valid notification that the server ignores marks nothing.
        s.open(uri, "def f() {}\n")
        s.send("$/setTrace", {"value": "verbose"}, request=False)
        s.send("textDocument/didChangeNothing", {}, request=False)
        assert s.edits(uri) == [edit(0, 9, 0, 9, " ")]
        s.close_document(uri)
        s.shutdown()
    finally:
        s.close()


def parse_budget(exe):
    s = FormatSession(exe)
    try:
        s.start()
        s.open("untitled:dense.v3", "def f(){" + ";" * MAX_TOKENS + "}\n")
        s.refused("untitled:dense.v3", text="the document has more than %d tokens" % MAX_TOKENS)
        s.open("untitled:small.v3", "def f() {}\n")
        assert s.edits("untitled:small.v3") == [edit(0, 9, 0, 9, " ")]
        s.ping()
        s.shutdown()
    finally:
        s.close()


def lines(n, indent="  "):
    return "def f() {\n" + (indent + ";\n") * n + "}\n"


def commented(note, indent="  "):
    """A method of 10,000 statements with the comment "// {note} note" between
    the 5,000th and the next, so that formatting changes 10,001 runs."""
    half = (indent + ";\n") * 5000
    return "def f() {\n" + half + indent + "// " + note + " note\n" + half + "}\n"


def nested(depth, body):
    """{body} in {depth} blocks in a method, each on lines of its own."""
    return "def f() {\n" + "{\n" * depth + body + "}\n" * depth + "}\n"


def bounds(exe, work):
    s = FormatSession(exe)
    try:
        s.start()
        # Exactly 10,000 changed runs are separate edits. More, here 10,001
        # with a comment line among them, make one edit from the first
        # change's start to the last change's end, with the comment.
        uri = "untitled:many.v3"
        s.open(uri, lines(10000))
        edits = s.edits(uri)
        assert len(edits) == 10000 and edits[0] == edit(1, 0, 1, 2, "\t"), edits[:2]
        assert apply(lines(10000), edits) == lines(10000, "\t")
        text = lines(5000) + "  // é note  \n" + lines(5000)[len("def f() {\n"):]
        text = text.replace("}\n  // é", "  // é", 1)
        expected = lines(5000, "\t") + "\t// é note\n" + lines(5000, "\t")[len("def f() {\n"):]
        expected = expected.replace("}\n\t// é", "\t// é", 1)
        s.change(uri, text)
        edits = s.edits(uri)
        assert len(edits) == 1, len(edits)
        assert edits[0]["range"] == {"start": {"line": 1, "character": 0}, "end": {"line": 10001, "character": 2}}, edits
        assert "// é note\n" in edits[0]["newText"], edits[0]["newText"][:80]
        assert apply(text, edits) == expected
        command(exe, work, text, expected)
        # The server holds an unpaired surrogate as U+FFFD, which a merged
        # edit would write back in its place, so a merged edit whose span
        # holds U+FFFD is refused, also for a genuine one. Without it, the
        # same text gets one edit; and fewer edits never cover it.
        for note in ("\ud800", "�"):
            s.change(uri, commented(note))
            s.refused(uri, text="includes a character the server can't reproduce exactly: U+FFFD")
        text = commented("")
        s.change(uri, text)
        edits = s.edits(uri)
        assert len(edits) == 1 and apply(text, edits) == commented("", "\t"), len(edits)
        text = "def f() {\n  // \ud800 note\n  ;\n}\n"
        s.change(uri, text)
        assert apply(text, s.edits(uri)) == "def f() {\n\t// \ud800 note\n\t;\n}\n"
        s.close_document(uri)
        # A document of about 100 KB whose indentation would grow past the
        # document store's 1 MiB: 50,000 empty statements 21 blocks deep gain
        # 21 tabs each. It is refused before any edit is built. The blocks
        # nest within the JVM build's parse depth too.
        uri = "untitled:growth.v3"
        text = nested(20, ";\n" * 50000)
        assert len(text) < 101000
        s.open(uri, text)
        s.refused(uri, text="would not fit in the open-document capacity")
        s.close_document(uri)
        # A reply over 4 MiB: 10,001 lines 21 blocks deep gain 210,021 tabs,
        # two bytes each in JSON, and 650,000 vertical tabs in a comment in
        # the merged edit take six bytes each. The parse budget counts them
        # as whitespace. The formatted text fits in the store, and the
        # didOpen in a frame.
        uri = "untitled:reply.v3"
        comment = "/*" + "\x0b" * 650000 + "*/\n"
        text = nested(20, ";\n" * 5000 + comment + ";\n" * 5001)
        s.open(uri, text)
        s.refused(uri, text="the formatting edits would need more than 4194304 bytes")
        s.close_document(uri)
        s.ping()
        s.open("untitled:small.v3", "def f() {}\n")
        assert s.edits("untitled:small.v3") == [edit(0, 9, 0, 9, " ")]
        s.shutdown()
    finally:
        s.close()


def command(exe, work, text, expected=None):
    """Checks that `virgil-lsp format` prints {expected}, if given, and
    returns what it prints for {text}, or None if it refuses the file."""
    path = Path(work) / "command.v3"
    path.write_bytes(text.encode())
    run = subprocess.run([str(exe), "format", str(path)], capture_output=True, timeout=60)
    if expected is not None:
        assert run.returncode == 0 and run.stdout == expected.encode(), (run.returncode, run.stderr)
    return run.stdout.decode() if run.returncode == 0 else None


def same_as_command(exe, work):
    """Formatting a file that ends with a line end gives the command's text.
    For one that doesn't, the request leaves the end alone and the command
    adds one, of the kind of the file's first line end."""
    files = sorted((ROOT / "vendor/virgil/lib/util").glob("*.v3"))
    assert files, "vendor/virgil is not initialized"
    texts = [(f.name, f.read_text(encoding="utf-8")) for f in files]
    texts += [(name + " (CR LF)", text.replace("\n", "\r\n")) for name, text in texts[:4]]
    texts += [("unformatted", "class C {\n    def f(x:int)->int{return x;}\n  // é 😀\n}\n\n\n"),
              ("mixed", "component K {\r\n  def x=1;\n}\r\n  \n"),
              ("no line end", "def f() {}"), ("no line end, CR LF", "class C {\r\n  def x = 1;\r\n}"),
              ("spaces after the last line end", "def f() { }\n  ")]
    s = FormatSession(exe)
    changed = ends = 0
    try:
        s.start()
        for name, text in texts:
            want = command(exe, work, text)
            assert want is not None, name
            s.open("untitled:compare.v3", text)
            got = apply(text, s.edits("untitled:compare.v3"))
            if text.endswith("\n"):
                assert got == want, name
            else:
                first = text.find("\n")
                assert got + ("\r\n" if first > 0 and text[first - 1] == "\r" else "\n") == want, name
                ends += 1
            changed += want != text
            s.close_document("untitled:compare.v3")
        s.shutdown()
    finally:
        s.close()
    assert changed >= 10 and ends >= 3, (changed, ends)
    return len(texts)


def unformatted(n):
    return "def f%d() {\n    return;\n}\n" % n


def formatted(n):
    return "def f%d() {\n\treturn;\n}\n" % n


def two_documents(s):
    a, b = "untitled:a.v3", "untitled:b.v3"
    s.open(a, unformatted(1))
    s.open(b, unformatted(2))
    for uri in (a, b):
        assert s.edits(uri) == [edit(1, 0, 1, 4, "\t")]
    return a, b


def rejected_changes(exe):
    """A rejected change to an open document marks only that document."""
    s = FormatSession(exe)
    try:
        s.start()
        a, b = two_documents(s)
        # Over the document store's capacity.
        s.change(a, "//" + "x" * STORE_BYTES)
        s.refused(a)
        assert s.edits(b) == [edit(1, 0, 1, 4, "\t")]
        # A version-only change keeps the mark; an accepted full change clears it.
        s.change(a)
        s.refused(a)
        s.change(a, unformatted(3))
        assert s.edits(a) == [edit(1, 0, 1, 4, "\t")]
        # An incremental change, invalid change params with a known URI, a
        # version that isn't newer, and a duplicate open.
        rejects = [
            {"textDocument": {"uri": a, "version": 100}, "contentChanges": [{"range": {
                "start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}}, "text": "x"}]},
            {"textDocument": {"uri": a, "version": 100}, "contentChanges": [{"text": 5}]},
            {"textDocument": {"uri": a}, "contentChanges": [{"text": formatted(4)}]},
            {"textDocument": {"uri": a, "version": 1}, "contentChanges": [{"text": formatted(4)}]},
        ]
        for params in rejects:
            s.send("textDocument/didChange", params, request=False)
            s.refused(a)
            assert s.edits(b) == [edit(1, 0, 1, 4, "\t")]
            s.change(a, unformatted(5))
            assert s.edits(a) == [edit(1, 0, 1, 4, "\t")]
        s.send("textDocument/didOpen", {"textDocument": {"uri": a, "languageId": "virgil", "version": 50,
                                                         "text": formatted(6)}}, request=False)
        s.refused(a)
        assert s.edits(b) == [edit(1, 0, 1, 4, "\t")]
        s.change(a, unformatted(7))
        assert s.edits(a) == [edit(1, 0, 1, 4, "\t")]
        # A version number that JsonValue can't represent: the URI is still known.
        s.send("textDocument/didChange", {"textDocument": {"uri": a, "version": 2 ** 40},
                                          "contentChanges": [{"text": formatted(7)}]}, request=False)
        s.refused(a)
        assert s.edits(b) == [edit(1, 0, 1, 4, "\t")]
        # A close and reopen clears the mark too.
        s.close_document(a)
        assert s.edits(a) == []
        s.open(a, unformatted(8))
        assert s.edits(a) == [edit(1, 0, 1, 4, "\t")]
        # A rejected change to a document that isn't open marks nothing.
        s.send("textDocument/didChange", {"textDocument": {"uri": "untitled:closed.v3", "version": 2},
                                          "contentChanges": [{"text": "x"}]}, request=False)
        for uri in (a, b):
            assert s.edits(uri) == [edit(1, 0, 1, 4, "\t")]
        s.shutdown()
        logs = s.logs()
        for reason in ("aggregate open-document capacity exceeded", "incremental changes are not supported",
                       "content change text must be a string", "invalid didChange params",
                       "document version is not newer", "document is already open",
                       "params contain a number that is not a 32-bit integer", "document is not open"):
            assert reason in logs, (reason, logs)
    finally:
        s.close()


def large_frame(s):
    # The payload's bytes are never read: the frame is skipped.
    s.raw(b"Content-Length: %d\r\n\r\n" % (MAX_PAYLOAD + 1) + b" " * (MAX_PAYLOAD + 1))


def charset_frame(s):
    payload = json.dumps({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {}}).encode()
    s.raw(b"Content-Length: %d\r\nContent-Type: application/vscode-jsonrpc; charset=latin1\r\n\r\n"
          % len(payload) + payload)


def malformed_json(s):
    send_payload(s, b'{"jsonrpc":"2.0","method":"textDocument/didChange","params":{"textDocument":')
    assert s.null_reply()["error"]["code"] == -32700


def value_limit(s):
    # A full-text change that fits in a frame, with more values than the
    # decoder's 50,000.
    msg = {"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {
        "textDocument": {"uri": "untitled:a.v3", "version": 1000},
        "contentChanges": [{"text": formatted(1)}], "padding": [0] * 50001}}
    payload = json.dumps(msg, separators=(",", ":")).encode()
    assert len(payload) < MAX_PAYLOAD
    send_payload(s, payload)
    reply = s.null_reply()
    assert reply["error"]["code"] == -32700 and "more than 50000 values" in reply["error"]["message"], reply


def lost_messages(exe):
    """A skipped frame or an undecodable payload marks every open document."""
    s = FormatSession(exe)
    try:
        s.start()
        for lose in (large_frame, charset_frame, malformed_json, value_limit):
            a, b = two_documents(s)
            lose(s)
            for uri in (a, b):
                s.refused(uri)
            s.change(a)
            s.refused(a)
            s.change(a, unformatted(3))
            assert s.edits(a) == [edit(1, 0, 1, 4, "\t")], lose.__name__
            s.refused(b)
            s.close_document(b)
            s.open(b, unformatted(4))
            assert s.edits(b) == [edit(1, 0, 1, 4, "\t")], lose.__name__
            # A document opened after the loss isn't marked.
            s.close_document(a)
            s.close_document(b)
        # A rejected payload with a valid request ID was a request, which
        # changes no document.
        a, b = two_documents(s)
        send_payload(s, b'{"jsonrpc":"2.0","id":"bad","method":"textDocument/didChange","params":5}')
        assert s.reply("bad")["error"]["code"] == -32600
        for uri in (a, b):
            assert s.edits(uri) == [edit(1, 0, 1, 4, "\t")]
        s.ping()
        s.shutdown()
        logs = s.logs()
        assert "skipped a message: payload of %d bytes is longer than the limit" % (MAX_PAYLOAD + 1) in logs, logs
        assert 'payload charset "latin1" is not utf-8' in logs, logs
    finally:
        s.close()


def projects(exe, work):
    """The active project's language options decide what parses: a file in a
    project without fun expressions, and in two projects, the first of which
    is active, is not formatted; in single-file mode it is."""
    root = Path(work) / "projects"
    root.mkdir()
    text = "def f = fun () {};\n"
    for name, projects in (("one", [{"name": "a", "sources": ["fun.v3"], "compilerArgs": ["-lang:fun-exprs=false"]}]),
                           ("two", [{"name": "a", "sources": ["fun.v3"], "compilerArgs": ["-lang:fun-exprs=false"]},
                                    {"name": "b", "sources": ["fun.v3"]}]),
                           ("default", [{"name": "b", "sources": ["fun.v3"]}])):
        folder = root / name
        folder.mkdir()
        (folder / "fun.v3").write_text(text)
        (folder / ".virgil-lsp.json").write_text(json.dumps({"version": 1, "projects": projects}))
        s = FormatSession(exe)
        try:
            s.start(rootUri=folder.as_uri())
            uri = (folder / "fun.v3").as_uri()
            s.open(uri, text)
            expected = [edit(0, 16, 0, 16, " ")] if name == "default" else []
            assert s.edits(uri) == expected, name
            s.open("untitled:fun.v3", text)
            assert s.edits("untitled:fun.v3") == [edit(0, 16, 0, 16, " ")]
            s.shutdown()
        finally:
            s.close()


def registration(exe):
    """A client that registers formatting dynamically gets its registration
    after initialized; a failure to register is logged."""
    for answer in ({"result": None}, {"error": {"code": -32601, "message": "unsupported"}}):
        s = FormatSession(exe)
        try:
            reply = s.result(s.send("initialize", {"capabilities": {
                "textDocument": {"formatting": {"dynamicRegistration": True}}}}))
            assert "documentFormattingProvider" not in reply["capabilities"], reply
            # The server numbers its own requests from 1.
            s.next_id = 1000
            s.send("initialized", {}, request=False)
            request = s.take_request()
            assert request["method"] == "client/registerCapability", request
            assert request["params"] == {"registrations": [{
                "id": "virgil-lsp/formatting", "method": "textDocument/formatting",
                "registerOptions": {"documentSelector": [{"language": "virgil"}]}}]}, request
            s.respond(request, **answer)
            s.open("untitled:r.v3", "def f() {}\n")
            assert s.edits("untitled:r.v3") == [edit(0, 9, 0, 9, " ")]
            s.shutdown()
            failure = "virgil-lsp: warning: client/registerCapability for formatting failed: -32601 unsupported"
            assert (failure in s.logs()) == ("error" in answer), s.logs()
        finally:
            s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="virgil-formatting-") as work:
        positions(exe)
        refusals(exe)
        parse_budget(exe)
        bounds(exe, work)
        compared = same_as_command(exe, work)
        rejected_changes(exe)
        lost_messages(exe)
        projects(exe, work)
        registration(exe)
    print("formatting: positions, refusals, bounds, synchronization, projects, registration, "
          "and %d files compared with the command: passed" % compared)


if __name__ == "__main__":
    main()
