#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Code actions and rename refuse edits the server can't compute (#514).

A project of two files, a.v3 calling B.g in b.v3, and an untitled document
with a missing semicolon, over the server's real pipes with its analysis
worker. A change rejected over the document store's capacity makes code
actions, prepareRename, and rename answer RequestFailed for that document; a
version-only change keeps the refusal, and an accepted full-text change ends
it. A rename from another document refuses when it would edit a document
whose change was rejected, a file whose didOpen was rejected, or a file that
got a didChange or didSave while the server didn't have it open, until a
didClose of that file. After a frame over the payload limit, or a didChange
over the JSON value limit, every open document refuses until it gets a
full-text change or is reopened. A valid ignored notification, $/setTrace,
changes nothing. Each session shuts down and exits with status 0, and every
receive has a deadline.

Run from the repository root:

  python3 test/protocol/out_of_sync_edits.py build/virgil-lsp
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from fingerprints import worker_pid
from formatting import FormatSession, OUT_OF_SYNC, REQUEST_FAILED, STORE_BYTES, large_frame, value_limit

CAPS = {"workspace": {"workspaceEdit": {"documentChanges": True}},
        "textDocument": {"rename": {"prepareSupport": True},
                         "codeAction": {"codeActionLiteralSupport": {"codeActionKind": {"valueSet": ["quickfix"]}}}}}
A_TEXT = "component A {\n\tdef f() -> int {\n\t\treturn B.g();\n\t}\n}\n"
B_TEXT = "component B {\n\tdef g() -> int {\n\t\treturn 1;\n\t}\n}\n"
# The ";" goes after "return 1", at line 2, character 10; the parser reports
# its absence at the "}" on the next line. A_TEXT and B_TEXT have no error
# there.
SEMI = "untitled:semicolon.v3"
SEMI_TEXT = "component C {\n\tdef f() {\n\t\treturn 1 // trailing\n\t}\n}\n"
SEMI_ERROR = {"line": 3, "character": 1}
SEMI_POINT = {"line": 2, "character": 10}
# Over the store's capacity whatever else is open.
TOO_LARGE = "//" + "x" * STORE_BYTES
# The use of B.g in a.v3, and its declaration in b.v3.
A_USE = {"line": 2, "character": 11}
B_DECL = {"line": 1, "character": 5}
HELD = "the client may have %s open with text the server doesn't have"
TARGET = "the server's copy of %s is out of date"


@contextmanager
def paused_worker(session):
    pid = worker_pid(session)
    os.kill(pid, signal.SIGSTOP)
    try:
        yield
    finally:
        os.kill(pid, signal.SIGCONT)


class EditSession(FormatSession):
    def __init__(self, exe, root):
        super().__init__(exe)
        self.root = root

    def begin(self):
        self.start(CAPS, rootUri=self.root.as_uri())

    def uri(self, name):
        return name if name.startswith("untitled:") else (self.root / name).as_uri()

    def open(self, name, text, language="virgil"):
        super().open(self.uri(name), text, language)

    def change(self, name, text=None):
        super().change(self.uri(name), text)

    def close_document(self, name):
        super().close_document(self.uri(name))

    def notify(self, method, name, **params):
        self.send("textDocument/" + method, dict(params, textDocument=dict(
            params.get("textDocument", {}), uri=self.uri(name))), request=False)

    def code_action(self, name, diagnostics=None):
        """The code actions at the missing semicolon's error in SEMI_TEXT."""
        params = {"textDocument": {"uri": self.uri(name)}, "range": {"start": SEMI_ERROR, "end": SEMI_ERROR}}
        if diagnostics is not None:
            params["context"] = {"diagnostics": diagnostics}
        return self.reply(self.send("textDocument/codeAction", params), 60)

    def send_prepare(self, name, position):
        return self.send("textDocument/prepareRename", {
            "textDocument": {"uri": self.uri(name)}, "position": position})

    def prepare(self, name, position):
        return self.reply(self.send_prepare(name, position), 60)

    def send_rename(self, name, position):
        return self.send("textDocument/rename", {
            "textDocument": {"uri": self.uri(name)}, "position": position, "newName": "h"})

    def rename(self, name, position):
        return self.reply(self.send_rename(name, position), 60)

    # Checks

    def action_offered(self, name=SEMI):
        msg = self.code_action(name)
        assert [a["title"] for a in msg.get("result", [])] == ['Insert missing ";"'], msg
        edit = msg["result"][0]["edit"]["documentChanges"][0]["edits"][0]
        assert edit["range"]["start"] == SEMI_POINT, msg
        return msg["result"][0]["diagnostics"][0]

    def no_action(self, name):
        msg = self.code_action(name)
        assert msg.get("result") == [], msg

    def renames(self, name, position, edited):
        """A rename that edits {edited}: a map of file names to the version
        each edit must carry, null for a closed file."""
        msg = self.rename(name, position)
        assert "result" in msg, msg
        changes = {c["textDocument"]["uri"]: c["textDocument"]["version"] for c in msg["result"]["documentChanges"]}
        assert changes == {self.uri(n): v for n, v in edited.items()}, msg

    def prepares(self, name, position):
        msg = self.prepare(name, position)
        assert msg.get("result", {}).get("placeholder") == "g", msg

    def refuses(self, reply, text=OUT_OF_SYNC):
        assert reply.get("error", {}).get("code") == REQUEST_FAILED, reply
        assert text in reply["error"]["message"], (text, reply)

    def all_refuse(self, name, position):
        """Code actions, prepareRename, and rename all refuse for {name}."""
        self.refuses(self.code_action(name))
        self.refuses(self.prepare(name, position))
        self.refuses(self.rename(name, position))


def rejected_change(exe, root):
    """A change over the store's capacity: a version-only change keeps the
    refusal, and an accepted full-text change ends it."""
    s = EditSession(exe, root)
    try:
        s.begin()
        s.open("a.v3", A_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        s.no_action("a.v3")
        s.change("a.v3", TOO_LARGE)
        s.all_refuse("a.v3", A_USE)
        s.change("a.v3")
        s.all_refuse("a.v3", A_USE)
        # The semicolon fix is offered again for the accepted text, and once
        # the text is valid again, prepareRename and rename answer.
        s.change("a.v3", SEMI_TEXT)
        s.action_offered("a.v3")
        s.change("a.v3", A_TEXT)
        s.prepares("a.v3", A_USE)
        s.renames("a.v3", A_USE, {"a.v3": 5, "b.v3": None})
        s.shutdown()
        assert "aggregate open-document capacity exceeded" in s.logs()
    finally:
        s.close()


def other_documents(exe, root):
    """A rename from a.v3 refuses to edit b.v3 while the server's copy of it
    is out of date, or while the client may hold it with other text."""
    b_path = str(root / "b.v3")
    s = EditSession(exe, root)
    try:
        s.begin()
        s.open("a.v3", A_TEXT)
        s.open("b.v3", B_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": 1})
        with paused_worker(s):
            pending = s.send_rename("a.v3", A_USE)
            s.refuses(s.rename("a.v3", A_USE), "another rename is being checked")
            assert pending not in s.waiting, s.waiting
            s.change("b.v3", TOO_LARGE)
            s.result(s.send("virgil-lsp/snapshot"))
        s.refuses(s.reply(pending, 60), TARGET % b_path)
        s.refuses(s.rename("a.v3", A_USE), TARGET % b_path)
        s.prepares("a.v3", A_USE)
        s.change("b.v3", B_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": 3})
        # A rejected open of b.v3: the rename would edit it from disk.
        s.close_document("b.v3")
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        s.open("b.v3", TOO_LARGE)
        s.refuses(s.rename("a.v3", A_USE), HELD % b_path)
        s.prepares("a.v3", A_USE)
        s.close_document("b.v3")
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        # A didChange or didSave of b.v3 while it isn't open shows that the
        # client holds it; its didClose ends that.
        s.notify("didChange", "b.v3", textDocument={"version": 5}, contentChanges=[{"text": B_TEXT}])
        s.refuses(s.rename("a.v3", A_USE), HELD % b_path)
        s.close_document("b.v3")
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        s.notify("didSave", "b.v3")
        s.refuses(s.rename("a.v3", A_USE), HELD % b_path)
        s.close_document("b.v3")
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        # An accepted open ends it too.
        s.notify("didSave", "b.v3")
        s.open("b.v3", B_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": 1})
        s.shutdown()
    finally:
        s.close()


def lost_messages(exe, root):
    """After a skipped frame or an undecodable didChange, every open document
    refuses until it gets a full-text change or is reopened."""
    b_path = str(root / "b.v3")
    s = EditSession(exe, root)
    try:
        s.begin()
        for lose in (large_frame, value_limit):
            s.open("a.v3", A_TEXT)
            s.open("b.v3", B_TEXT)
            s.open(SEMI, SEMI_TEXT)
            s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": 1})
            diagnostic = s.action_offered()
            lose(s)
            s.all_refuse("a.v3", A_USE)
            s.all_refuse("b.v3", B_DECL)
            s.refuses(s.code_action(SEMI))
            empty = s.code_action(SEMI, [])
            assert empty.get("result") == [], empty
            s.refuses(s.code_action(SEMI, [diagnostic]))
            # a.v3's change ends its own refusal, but a rename from it would
            # still edit b.v3.
            s.change("a.v3", A_TEXT)
            s.no_action("a.v3")
            s.prepares("a.v3", A_USE)
            s.refuses(s.rename("a.v3", A_USE), TARGET % b_path)
            s.refuses(s.prepare("b.v3", B_DECL))
            s.refuses(s.code_action(SEMI))
            s.close_document("b.v3")
            s.open("b.v3", B_TEXT)
            s.prepares("b.v3", B_DECL)
            s.renames("a.v3", A_USE, {"a.v3": 2, "b.v3": 1})
            s.refuses(s.code_action(SEMI))
            s.change(SEMI, SEMI_TEXT)
            s.action_offered()
            for name in ("a.v3", "b.v3", SEMI):
                s.close_document(name)
        s.shutdown()
    finally:
        s.close()


def ignored_notification(exe, root):
    """A valid notification that the server ignores marks nothing."""
    s = EditSession(exe, root)
    try:
        s.begin()
        s.open("a.v3", A_TEXT)
        s.open(SEMI, SEMI_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        s.send("$/setTrace", {"value": "verbose"}, request=False)
        s.action_offered()
        s.prepares("a.v3", A_USE)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        s.shutdown()
    finally:
        s.close()


def refusal_before_analysis(exe, root):
    s = EditSession(exe, root)
    try:
        s.begin()
        s.open("a.v3", A_TEXT)
        s.prepares("a.v3", A_USE)
        broken = "component A {\n"
        with paused_worker(s):
            s.change("a.v3", broken)
            prepare = s.send_prepare("a.v3", A_USE)
            rename = s.send_rename("a.v3", A_USE)
            s.result(s.send("virgil-lsp/snapshot"))
            assert prepare not in s.waiting and rename not in s.waiting, s.waiting
            s.change("a.v3", TOO_LARGE)
            s.result(s.send("virgil-lsp/snapshot"))
        s.refuses(s.reply(prepare, 60))
        s.refuses(s.reply(rename, 60))
        s.refuses(s.prepare("a.v3", A_USE))
        s.refuses(s.rename("a.v3", A_USE))
        s.change("a.v3", broken)
        s.refuses(s.prepare("a.v3", A_USE), "the project's analysis has parse errors")
        s.refuses(s.rename("a.v3", A_USE), "the project's analysis has parse errors")
        s.shutdown()
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="virgil-out-of-sync-") as work:
        root = Path(work).resolve()
        (root / ".virgil-lsp.json").write_text(json.dumps(
            {"version": 1, "projects": [{"name": "p", "sources": ["*.v3"]}]}))
        (root / "a.v3").write_text(A_TEXT)
        (root / "b.v3").write_text(B_TEXT)
        rejected_change(exe, root)
        other_documents(exe, root)
        refusal_before_analysis(exe, root)
        lost_messages(exe, root)
        ignored_notification(exe, root)
    print("out-of-sync edits: rejected changes, other documents, rejected opens, unopened changes, "
          "lost messages, and ignored notifications: passed")


if __name__ == "__main__":
    main()
