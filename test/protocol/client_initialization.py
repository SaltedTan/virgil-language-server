#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Replay the initialization exchanges of real editor clients.

Each directory under clients/ holds a capture's normalized client messages
(messages.json) and its provenance (fixture.json). This test sends those
messages, in order and with correct framing, to the stdio server; answers the
server's client/registerCapability request as the capture did; then opens a
small file at the capture's project root, requests its symbols, and shuts down.
It compares JSON values, not bytes, and every receive has a deadline.

The six captures are VS Code 1.142.0 (single file and workspace folder) and
Neovim 0.11.6 and 0.12.4 (with a Virgil root and with empty options). They
differ in which members they send at all, not in the client capabilities that
change the initialize reply: all six declare
textDocument.documentSymbol.hierarchicalDocumentSymbolSupport and
textDocument.rename.prepareSupport, and list every document-symbol and
completion kind, so one expected reply serves all six. If a later capture
differs there, add its expected part per capture and document why.

Run from the repository root:

  python3 test/protocol/client_initialization.py build/virgil-lsp
"""

import json
from pathlib import Path
import sys
import time

from worker import Session


CLIENTS = Path(__file__).resolve().parent / "clients"
PROJECT = "/work/project"
URI = "file://" + PROJECT + "/main.v3"
SOURCE = (
    "class Shape {\n"
    "\tdef area() -> int { return 1; }\n"
    "}\n"
    "\n"
    "def main() -> int { return 0; }\n"
)
# The server numbers its own requests from 1. JSON-RPC matches a reply to a
# request by id within one direction, but Session records both directions, so
# this test's requests start above the ids the server can choose.
FIRST_ID = 1000
TIMEOUT = 20

CAPABILITIES = {
    "callHierarchyProvider": True,
    "completionProvider": {"resolveProvider": True, "triggerCharacters": ["."]},
    "codeActionProvider": {"codeActionKinds": ["quickfix"]},
    "definitionProvider": True,
    "typeDefinitionProvider": True,
    "inlayHintProvider": True,
    "documentHighlightProvider": True,
    "documentSymbolProvider": True,
    "foldingRangeProvider": True,
    "selectionRangeProvider": True,
    "semanticTokensProvider": {"full": True, "legend": {
        "tokenModifiers": ["declaration", "readonly"],
        "tokenTypes": ["namespace", "class", "enum", "enumMember", "struct", "type", "typeParameter",
                       "parameter", "variable", "property", "function", "method"]}},
    "hoverProvider": True,
    "implementationProvider": True,
    "referencesProvider": True,
    "renameProvider": {"prepareProvider": True},
    "signatureHelpProvider": {"retriggerCharacters": [")"], "triggerCharacters": ["(", ","]},
    "textDocumentSync": {"change": 1, "openClose": True, "save": {"includeText": False}},
    "typeHierarchyProvider": True,
    "workspace": {"workspaceFolders": {"changeNotifications": True, "supported": True}},
    "workspaceSymbolProvider": True,
}

SYMBOLS = [
    {
        "children": [{
            "kind": 6,
            "name": "area",
            "range": {"end": {"character": 32, "line": 1}, "start": {"character": 1, "line": 1}},
            "selectionRange": {"end": {"character": 9, "line": 1}, "start": {"character": 5, "line": 1}},
        }],
        "kind": 5,
        "name": "Shape",
        "range": {"end": {"character": 1, "line": 2}, "start": {"character": 0, "line": 0}},
        "selectionRange": {"end": {"character": 11, "line": 0}, "start": {"character": 6, "line": 0}},
    },
    {
        "kind": 6,
        "name": "main",
        "range": {"end": {"character": 31, "line": 4}, "start": {"character": 0, "line": 4}},
        "selectionRange": {"end": {"character": 8, "line": 4}, "start": {"character": 4, "line": 4}},
    },
]

# Opening a file outside a project logs this once.
SINGLE_FILE_WARNING = (
    "virgil-lsp: warning: Without a project file selecting this document, "
    "semantic features are limited; using single-file mode."
)
# The placeholder workspace root does not exist on the test machine, so a
# capture with a workspace folder also gets this line, once, before the other.
MISSING_ROOT_WARNING = (
    "virgil-lsp: warning: cannot enumerate workspace project discovery directory: "
    + PROJECT
)
# A capture that replies to client/registerCapability gets this confirmation.
REGISTERED_INFO = "virgil-lsp: the client watches project files and sources"


def expected_log(messages):
    params = messages[0]["params"]
    options = params.get("initializationOptions")
    root = options.get("virgilRoot") if isinstance(options, dict) else None
    root_log = (
        f"virgil-lsp: Virgil root: {root} (client)" if root else
        "virgil-lsp: Virgil root: none (bundle REVISION is missing or unreadable)"
    )
    replies = any("method" not in message for message in messages)
    return [root_log] + ([REGISTERED_INFO] if replies else []) + (
        [MISSING_ROOT_WARNING] if params.get("workspaceFolders") else []
    ) + [SINGLE_FILE_WARNING]


class Replay(Session):
    def take_request(self, deadline):
        """The next request the server sent, waited for until {deadline}."""
        while True:
            for key, message in list(self.waiting.items()):
                if "method" in message:
                    return self.waiting.pop(key)
            message = self.receive(deadline)
            if "id" in message:
                self.waiting[message["id"]] = message

    def answer(self, request, captured):
        reply = {"jsonrpc": "2.0", "id": request["id"]}
        for member in ("result", "error"):
            if member in captured:
                reply[member] = captured[member]
        payload = json.dumps(reply, separators=(",", ":")).encode()
        self.raw(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)


def load(directory):
    messages = json.loads((directory / "messages.json").read_text(encoding="utf-8"))
    metadata = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    initialize = messages[0]
    assert initialize["method"] == "initialize", initialize
    assert messages[1]["method"] == "initialized", messages[1]
    for message in messages:
        assert message.get("jsonrpc") == "2.0", message
    for message in messages[2:]:
        assert message.get("method") is None and "id" in message, message
    client = initialize["params"]["clientInfo"]
    assert metadata["editor"] == client["name"], (metadata, client)
    assert metadata["editorVersion"] == client["version"], (metadata, client)
    assert metadata["captured"] and metadata["serverCommit"], metadata
    text = json.dumps(messages)
    assert "/home/" not in text and "/.t3/" not in text, "a capture path was not normalized"
    return messages, metadata


def replay(exe, directory):
    messages, metadata = load(directory)
    s = Replay(exe)
    try:
        for message in messages:
            method = message.get("method")
            if method == "initialize":
                s.send("initialize", message["params"], id=message["id"])
                reply = s.reply(message["id"], TIMEOUT)
                assert "result" in reply, f"{directory.name}: initialize was rejected: {reply}"
                result = reply["result"]
                assert result["capabilities"] == CAPABILITIES, (
                    f"{directory.name}: initialize capabilities differ\n"
                    + json.dumps(result["capabilities"], indent=2, sort_keys=True))
                assert result["serverInfo"]["name"] == "virgil-lsp", result["serverInfo"]
                assert isinstance(result["serverInfo"]["version"], str), result["serverInfo"]
                s.next_id = FIRST_ID
            elif method == "initialized":
                s.send("initialized", message["params"], request=False)
            else:
                assert method is None and "id" in message, message
                request = s.take_request(time.monotonic() + TIMEOUT)
                assert request["method"] == "client/registerCapability", request
                s.answer(request, message)

        s.send("textDocument/didOpen", {"textDocument": {
            "uri": URI, "languageId": "virgil", "version": 1, "text": SOURCE,
        }}, request=False)
        symbols = s.result(s.send("textDocument/documentSymbol",
                                  {"textDocument": {"uri": URI}}), TIMEOUT)
        assert symbols == SYMBOLS, f"{directory.name}: document symbols differ\n" + json.dumps(
            symbols, indent=2)
        s.shutdown()
        log = s.logs().strip().splitlines()
        assert log == expected_log(messages), f"{directory.name}: unexpected stderr\n" + "\n".join(log)
        return metadata
    except BaseException:
        print(json.dumps(s.transcript, indent=2), file=sys.stderr)
        print(s.logs(), file=sys.stderr)
        raise
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    directories = sorted(path for path in CLIENTS.iterdir()
                         if path.is_dir() and (path / "messages.json").is_file())
    assert directories, f"{CLIENTS}: no client fixtures"
    for directory in directories:
        try:
            metadata = replay(exe, directory)
        except BaseException:
            print(f"client initialization: {directory.name} failed", file=sys.stderr)
            raise
        print(f"client initialization: {directory.name} ({metadata['editor']} "
              f"{metadata['editorVersion']}) replayed")


if __name__ == "__main__":
    main()
