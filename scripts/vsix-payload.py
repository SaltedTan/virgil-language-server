#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Read the runtime payload of a tested release archive without extracting it."""
import hashlib
import json
import tarfile
from pathlib import PurePosixPath

TARGETS = {"linux-x64": "x86-64-linux", "darwin-arm64": "x86-64-darwin",
           "darwin-x64": "x86-64-darwin"}
REQUIRED = {"virgil-lsp", "virgil-lsp-worker", "LICENSE", "THIRD_PARTY_NOTICES.md",
            "manifest.json", "virgil/LICENSE", "virgil/REVISION"}


def payload(archive, target):
    """Return canonical file names mapped to (bytes, permissions). Refuse links."""
    files = {}
    root = None
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            parts = PurePosixPath(member.name).parts
            if (not parts or member.name.startswith("/") or "\\" in member.name
                    or any(p in ("", ".", "..") for p in member.name.rstrip("/").split("/"))):
                raise ValueError("noncanonical archive entry: " + member.name)
            if root is None:
                root = parts[0]
            if parts[0] != root:
                raise ValueError("archive has multiple roots")
            if member.isdir():
                continue
            if not member.isfile() or len(parts) < 2:
                raise ValueError("archive entry is not a regular file: " + member.name)
            name = "/".join(parts[1:])
            if name in files:
                raise ValueError("duplicate archive file: " + name)
            if name != "README.md" and name not in REQUIRED and not name.startswith("virgil/lib/"):
                raise ValueError("unexpected archive file: " + name)
            files[name] = (tar.extractfile(member).read(), member.mode & 0o777)
    if not REQUIRED <= files.keys() or not any(n.startswith("virgil/lib/") for n in files):
        raise ValueError("archive lacks executables, library, metadata, or notices")
    manifest = json.loads(files["manifest.json"][0])
    if manifest["target"] != TARGETS[target]:
        raise ValueError("archive target does not match " + target)
    for name in ("virgil-lsp", "virgil-lsp-worker"):
        data, mode = files[name]
        if mode & 0o111 != 0o111:
            raise ValueError("archive executable lacks execute permissions: " + name)
        if hashlib.sha256(data).hexdigest() != manifest["sha256"][name]:
            raise ValueError("archive executable differs from its manifest: " + name)
    files.pop("README.md", None)
    return files
