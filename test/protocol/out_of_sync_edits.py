#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Code actions and rename refuse edits the server can't compute (#514).

A project with a.v3 calling B.g in b.v3, and an untitled document
with a missing semicolon, over the server's real pipes with its analysis
worker. A change rejected over the document store's capacity makes code
actions, prepareRename, and rename answer RequestFailed for that document; a
version-only change keeps the refusal, and an accepted full-text change ends
it. A rename refuses when the project contains a source whose change or
open was rejected, or that got a didChange or didSave while the server
didn't have it open. This includes c.v3, which has no reference to B.g in
the last accepted analysis. After a frame over the payload limit, or a didChange
over the JSON value limit, every open document refuses until it gets a
full-text change or is reopened. A valid ignored notification, $/setTrace,
changes nothing. Completion answers an empty list, incomplete, for a document
whose change the server rejected, rather than items whose edits come from the
server's copy: at once, at the cursor of a cached reply, and for a request
that waited on the worker when the change came; an accepted full-text change
restores it. Each session shuts down and exits with status 0, and every
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
import time

from code_actions import (
    DIAGNOSTIC as SEMI_DIAGNOSTIC, ERROR as SEMI_ERROR, POINT as SEMI_POINT, TEXT as SEMI_TEXT, URI as SEMI,
)
from fingerprints import worker_pid
from formatting import FormatSession, MAX_PAYLOAD, OUT_OF_SYNC, REQUEST_FAILED, STORE_BYTES, large_frame, value_limit
from worker import send_payload

CAPS = {"workspace": {"workspaceEdit": {"documentChanges": True}},
        "textDocument": {"rename": {"prepareSupport": True},
                         "codeAction": {"codeActionLiteralSupport": {"codeActionKind": {"valueSet": ["quickfix"]}}}}}
A_TEXT = "component A {\n\tdef f() -> int {\n\t\treturn B.g();\n\t}\n}\n"
B_TEXT = "component B {\n\tdef g() -> int {\n\t\treturn 1;\n\t}\n}\n"
C_TEXT = "component C {\n\tdef other() -> int {\n\t\treturn 0;\n\t}\n}\n"
C_WITH_CALL = C_TEXT.replace("return 0;", "return B.g();")
# Over the store's capacity whatever else is open.
TOO_LARGE = "//" + "x" * STORE_BYTES
# The use of B.g in a.v3, and its declaration in b.v3.
A_USE = {"line": 2, "character": 11}
B_DECL = {"line": 1, "character": 5}
HELD = "the client may have %s open with text the server doesn't have"
# The issue's example: the server keeps "whi;" after rejecting the change that
# deletes the "i", so a completion at "wh|;" would replace the ";" too.
TYPED = "component A {\n\tdef f() {\n\t\twhi;\n\t}\n}\n"
EDITED = TYPED.replace("whi;", "wh;")
WH = {"line": 2, "character": 4}
DELETE_I = {"start": WH, "end": {"line": 2, "character": 5}}
COMPLETION_CAPS = {"textDocument": {"completion": {"completionItem": {"insertReplaceSupport": True}}}}
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

    def send_completion(self, name, position):
        return self.send("textDocument/completion", {"textDocument": {"uri": self.uri(name)}, "position": position})

    def rejected_edit(self, name, range, text):
        """An incremental change, which the server rejects: the client's text
        now differs from the server's."""
        uri = self.uri(name)
        self.versions[uri] += 1
        self.notify("didChange", name, textDocument={"version": self.versions[uri]},
                    contentChanges=[{"range": range, "text": text}])

    # Checks

    def while_replaces(self, reply, end):
        """{reply} offers "while", replacing "wh" and the identifier's rest up
        to {end} on line 2."""
        items = {i["label"]: i for i in reply.get("result", {}).get("items", [])}
        assert "while" in items, reply
        edit = items["while"]["textEdit"]
        assert edit["insert"] == {"start": {"line": 2, "character": 2}, "end": WH}, edit
        assert edit["replace"] == {"start": {"line": 2, "character": 2}, "end": {"line": 2, "character": end}}, edit

    def no_completion(self, reply):
        assert reply.get("result") == {"isIncomplete": True, "items": []}, reply

    def action_offered(self, name=SEMI, diagnostics=None):
        msg = self.code_action(name, diagnostics)
        assert [a["title"] for a in msg.get("result", [])] == ['Insert missing ";"'], msg
        edit = msg["result"][0]["edit"]["documentChanges"][0]["edits"][0]
        assert edit == {"range": {"start": SEMI_POINT, "end": SEMI_POINT}, "newText": ";"}, msg

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
    filler = "untitled:capacity.v3"
    replacement = A_TEXT + "//" + "x" * (3 * STORE_BYTES // 4)
    try:
        s.begin()
        s.open("a.v3", A_TEXT)
        s.open(filler, "//" + "x" * (STORE_BYTES // 2))
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        s.no_action("a.v3")
        s.change("a.v3", replacement)
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
        s.close_document(filler)
        s.change("a.v3", replacement)
        s.renames("a.v3", A_USE, {"a.v3": 6, "b.v3": None})
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
        for replace_while_checking, recovered_version in ((False, 3), (True, 6)):
            with paused_worker(s):
                pending = s.send_rename("a.v3", A_USE)
                s.refuses(s.rename("a.v3", A_USE), "another rename is being checked")
                assert pending not in s.waiting, s.waiting
                if replace_while_checking:
                    s.change("b.v3", B_TEXT + "\n")
                s.change("b.v3", TOO_LARGE)
                s.result(s.send("virgil-lsp/snapshot"))
            s.refuses(s.reply(pending, 60), TARGET % b_path)
            s.refuses(s.rename("a.v3", A_USE), TARGET % b_path)
            s.prepares("a.v3", A_USE)
            s.change("b.v3", B_TEXT)
            s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": recovered_version})
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


def unreferenced_source(exe, root, open_document):
    c_path = str(root / "c.v3")
    s = EditSession(exe, root)
    try:
        s.begin()
        s.open("a.v3", A_TEXT)
        if open_document:
            s.open("c.v3", C_TEXT)
        edited = {"a.v3": 1, "b.v3": None}
        s.renames("a.v3", A_USE, edited)
        with paused_worker(s):
            pending = s.send_rename("a.v3", A_USE)
            s.refuses(s.rename("a.v3", A_USE), "another rename is being checked")
            assert pending not in s.waiting, s.waiting
            if open_document:
                s.notify("didChange", "c.v3", textDocument={"version": 2}, contentChanges=[{
                    "range": {"start": {"line": 2, "character": 9}, "end": {"line": 2, "character": 10}},
                    "text": "B.g()"}])
            else:
                s.open("c.v3", C_WITH_CALL + TOO_LARGE)
            s.result(s.send("virgil-lsp/snapshot"))
        reason = (TARGET if open_document else HELD) % c_path
        s.refuses(s.reply(pending, 60), reason)
        s.refuses(s.rename("a.v3", A_USE), reason)
        s.prepares("a.v3", A_USE)
        if open_document:
            s.change("c.v3", C_WITH_CALL)
            edited["c.v3"] = 2
        else:
            s.close_document("c.v3")
        s.renames("a.v3", A_USE, edited)
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
            s.action_offered(diagnostics=[SEMI_DIAGNOSTIC])
            lose(s)
            s.all_refuse("a.v3", A_USE)
            s.all_refuse("b.v3", B_DECL)
            s.refuses(s.code_action(SEMI))
            empty = s.code_action(SEMI, [])
            assert empty.get("result") == [], empty
            s.refuses(s.code_action(SEMI, [SEMI_DIAGNOSTIC]))
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
        for replace_while_waiting in (False, True):
            with paused_worker(s):
                s.change("a.v3", broken)
                prepare = s.send_prepare("a.v3", A_USE)
                rename = s.send_rename("a.v3", A_USE)
                s.result(s.send("virgil-lsp/snapshot"))
                assert prepare not in s.waiting and rename not in s.waiting, s.waiting
                if replace_while_waiting:
                    s.change("a.v3", broken + "\n")
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


def incomplete_skip_during_preflight(exe, root):
    s = EditSession(exe, root)
    try:
        s.begin()
        s.open("a.v3", A_TEXT)
        s.open("b.v3", B_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": 1})
        length = MAX_PAYLOAD + 1
        with paused_worker(s):
            pending = s.send_rename("a.v3", A_USE)
            s.refuses(s.rename("a.v3", A_USE), "another rename is being checked")
            assert pending not in s.waiting, s.waiting
            s.raw(b"Content-Length: %d\r\n\r\n " % length)
            warning = b"skipped a message: payload of %d bytes is longer than the limit" % length
            deadline = time.monotonic() + 5
            while warning not in os.pread(s.err.fileno(), 65536, 0):
                assert time.monotonic() < deadline, "the server didn't report the skip at the header"
                time.sleep(0.01)
        s.refuses(s.reply(pending, 60))
        s.raw(b" " * (length - 1))
        s.refuses(s.prepare("b.v3", B_DECL))
        s.change("a.v3", A_TEXT)
        s.change("b.v3", B_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 2, "b.v3": 2})
        s.shutdown()
    finally:
        s.close()


def rejected_open_before_initialize(exe, root):
    s = EditSession(exe, root)
    try:
        early_open = {"jsonrpc": "2.0", "id": None, "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": s.uri("b.v3"), "languageId": "virgil", "version": 1, "text": B_TEXT}}}
        send_payload(s, json.dumps(early_open).encode())
        reply = s.null_reply()
        assert reply["id"] is None and reply["error"]["code"] == -32600, reply
        s.begin()
        s.open("a.v3", A_TEXT)
        s.renames("a.v3", A_USE, {"a.v3": 1, "b.v3": None})
        s.shutdown()
    finally:
        s.close()


def completion(exe, root):
    """Completion offers no edit computed from the server's out-of-date copy,
    even where a cached or a waiting reply would."""
    s = EditSession(exe, root)
    try:
        s.start(COMPLETION_CAPS, rootUri=root.as_uri())
        s.open("a.v3", TYPED)
        s.while_replaces(s.reply(s.send_completion("a.v3", WH), 60), 5)
        # The same cursor again would get the cached reply.
        s.while_replaces(s.reply(s.send_completion("a.v3", WH), 60), 5)
        s.rejected_edit("a.v3", DELETE_I, "")
        s.no_completion(s.reply(s.send_completion("a.v3", WH), 60))
        s.change("a.v3")
        s.no_completion(s.reply(s.send_completion("a.v3", WH), 60))
        s.change("a.v3", TYPED)
        s.while_replaces(s.reply(s.send_completion("a.v3", WH), 60), 5)
        # A completion waiting on the worker when the change is rejected.
        with paused_worker(s):
            s.change("a.v3", TYPED + "\n")
            pending = s.send_completion("a.v3", WH)
            s.result(s.send("virgil-lsp/snapshot"))
            assert pending not in s.waiting, s.waiting
            s.rejected_edit("a.v3", DELETE_I, "")
            s.no_completion(s.reply(s.send_completion("a.v3", WH), 10))
            s.change("a.v3", EDITED)
            s.result(s.send("virgil-lsp/snapshot"))
            assert pending not in s.waiting, s.waiting
        s.no_completion(s.reply(pending, 60))
        # The editor's text, accepted: "while" replaces "wh" and not the ";".
        s.while_replaces(s.reply(s.send_completion("a.v3", WH), 60), 4)
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
        rejected_open_before_initialize(exe, root)
        rejected_change(exe, root)
        other_documents(exe, root)
        refusal_before_analysis(exe, root)
        incomplete_skip_during_preflight(exe, root)
        lost_messages(exe, root)
        ignored_notification(exe, root)
        completion(exe, root)
        (root / "c.v3").write_text(C_TEXT)
        unreferenced_source(exe, root, open_document=True)
        unreferenced_source(exe, root, open_document=False)
    print("out-of-sync edits: rejected changes, rename preflight, rejected opens, unopened changes, "
          "lost messages, ignored notifications, and completion: passed")


if __name__ == "__main__":
    main()
