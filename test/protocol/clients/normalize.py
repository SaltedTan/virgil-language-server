#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Normalize captured client initialization messages into replay fixtures.

Reads the capture directories that research produced (each with a decoded
client.json) and writes test/protocol/clients/<name>/messages.json plus
fixture.json. Not part of `make test`: it only runs when a client is captured
again, so the fixtures stay reviewable diffs instead of machine-specific bytes.

Usage, from the repository root:

  python3 test/protocol/clients/normalize.py CAPTURES_DIR

CAPTURES_DIR holds one directory per capture, each with client.json, and the
output goes to the directory that holds this script. Replace a capture's
directory to update its fixture.

The span kept is the client's initialize request, its initialized
notification, and its replies to the server's requests (client/registerCapability)
that the capture holds before the client's first later request. Client
notifications in between, such as textDocument/didOpen, are outside the span
and are skipped by the replay test, which opens its own file.

Normalization replaces machine-specific values and nothing else:

  * the captured project root, in rootPath, rootUri, workspace folder names
    and URIs, and anywhere else it appears, with /work/project; a workspace
    folder named after the project directory becomes "project";
  * initializationOptions.virgilRoot, a checkout outside the project, with
    /work/virgil;
  * processId with a fixed number (or null, which stays null);
  * Content-Length is recomputed on replay, since messages are stored as JSON.

Every other member, and every absent, null, [] and {} distinction, is kept as
sent. Absolute paths that no rule covers stop the script, so a new capture
cannot leak a user path silently.
"""

import argparse
import json
from pathlib import Path
import re
import sys


PROJECT = "/work/project"
VIRGIL = "/work/virgil"
PROCESS_ID = 4242
MACHINE_PATH = re.compile(r"/(?:home|Users|root|private|tmp)/")
CAPTURES = {
    "vscode-single-file": {
        "editor": "Visual Studio Code",
        "editorVersion": "1.142.0",
        "clientLibrary": "vscode-languageclient 10.1.2",
    },
    "vscode-workspace": {
        "editor": "Visual Studio Code",
        "editorVersion": "1.142.0",
        "clientLibrary": "vscode-languageclient 10.1.2",
    },
    "nvim-0.11.6-root": {
        "editor": "Neovim",
        "editorVersion": "0.11.6",
        "clientLibrary": None,
    },
    "nvim-0.11.6-empty-options": {
        "editor": "Neovim",
        "editorVersion": "0.11.6",
        "clientLibrary": None,
    },
    "nvim-0.12.4-root": {
        "editor": "Neovim",
        "editorVersion": "0.12.4+v0.12.4",
        "clientLibrary": None,
    },
    "nvim-0.12.4-empty-options": {
        "editor": "Neovim",
        "editorVersion": "0.12.4+v0.12.4",
        "clientLibrary": None,
    },
}


def span(messages):
    """initialize, initialized, and replies to the server's requests."""
    assert messages[0].get("method") == "initialize", messages[0]
    initialized = next(i for i, m in enumerate(messages) if m.get("method") == "initialized")
    kept = messages[:initialized + 1]
    for message in messages[initialized + 1:]:
        if "method" not in message:
            kept.append(message)
        elif "id" in message:
            # The client's first later request ends the initialization exchange.
            break
        # Another notification, such as didOpen, is outside the span.
    return kept


def replacements(initialize):
    """Machine paths in this capture, longest first, with their placeholders."""
    params = initialize["params"]
    roots = set()
    for path in (params.get("rootPath"), params.get("rootUri")):
        if isinstance(path, str):
            roots.add(path.removeprefix("file://"))
    for folder in params.get("workspaceFolders") or []:
        uri = folder.get("uri") if isinstance(folder, dict) else None
        if isinstance(uri, str):
            roots.add(uri.removeprefix("file://"))
    roots.discard(PROJECT)
    mapping = [(root.rstrip("/"), PROJECT) for root in roots]
    options = params.get("initializationOptions")
    virgil = options.get("virgilRoot") if isinstance(options, dict) else None
    if isinstance(virgil, str):
        mapping.append((virgil.rstrip("/"), VIRGIL))
    mapping.sort(key=lambda pair: -len(pair[0]))
    return mapping, {root.rsplit("/", 1)[-1]: "project" for root in roots}


def rewrite(value, mapping, names):
    if isinstance(value, str):
        for old, new in mapping:
            value = value.replace(old, new)
        value = names.get(value, value)
        if MACHINE_PATH.search(value):
            raise ValueError(f"uncovered machine path {value!r}")
        return value
    if isinstance(value, list):
        return [rewrite(item, mapping, names) for item in value]
    if isinstance(value, dict):
        return {key: rewrite(item, mapping, names) for key, item in value.items()}
    return value


def normalize(capture, out):
    messages = json.loads((capture / "client.json").read_text(encoding="utf-8"))
    kept = span(messages)
    initialize = kept[0]
    mapping, names = replacements(initialize)
    normalized = rewrite(kept, mapping, names)
    normalized[0]["params"]["processId"] = (
        PROCESS_ID if normalized[0]["params"].get("processId") is not None else None)
    if capture.name not in CAPTURES:
        sys.exit(f"{capture.name}: add its editor and client library to CAPTURES")
    metadata = {
        **CAPTURES[capture.name],
        "captured": "2026-10-23",
        "serverCommit": "025082d",
        "projectRoot": PROJECT,
        "normalized": [
            "the captured project root to /work/project",
            "initializationOptions.virgilRoot, when present, to /work/virgil",
            f"processId to {PROCESS_ID}, or kept null",
            "a workspace folder named after the project directory, when present, to project",
        ],
        "notes": "Replies to the server's requests keep the captured id; the test answers the id of the server's pending request.",
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "messages.json").write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "fixture.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", type=Path, help="directory of capture directories")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    captures = sorted(path for path in args.captures.iterdir()
                      if path.is_dir() and (path / "client.json").is_file())
    if not captures:
        sys.exit(f"{args.captures}: no capture with client.json")
    for capture in captures:
        metadata = normalize(capture, here / capture.name)
        print(f"{capture.name}: {metadata.get('editor', '?')} {metadata.get('editorVersion', '')}".rstrip())


if __name__ == "__main__":
    main()
