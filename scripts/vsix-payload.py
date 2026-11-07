#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Read the runtime payload of a tested release archive without extracting it."""
import hashlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

# Each VSIX target, and the archive that it bundles, by the archive's manifest
# target, or "jvm-<platform>" for a JVM archive.
TARGETS = {"linux-x64": "x86-64-linux", "darwin-arm64": "x86-64-darwin",
           "darwin-x64": "x86-64-darwin", "darwin-arm64-jvm": "jvm-arm64-darwin"}
# The VS Code target platform of the targets that aren't one themselves. The
# JVM VSIX is a darwin-arm64 package under a name of its own.
VSCODE_TARGETS = {"darwin-arm64-jvm": "darwin-arm64"}
REQUIRED = {"virgil-lsp", "virgil-lsp-worker", "LICENSE", "THIRD_PARTY_NOTICES.md",
            "manifest.json", "virgil/LICENSE", "virgil/REVISION"}
JARS = {"virgil-lsp.jar", "virgil-lsp-worker.jar"}


def vscode_target(target):
    """The VS Code target platform of a VSIX target."""
    return VSCODE_TARGETS.get(target, target)


def marker(target):
    """The contents of a platform VSIX's server-target.json."""
    result = {"target": vscode_target(target)}
    if TARGETS[target].startswith("jvm-"):
        result["backend"] = "jvm"
    return result


def jvm_runtime():
    """scripts/jvm-runtime.py, loaded once per process and shared with
    release-library.py, so that both check against the same pin."""
    module = sys.modules.get("virgil_lsp_jvm_runtime")
    if module is None:
        spec = importlib.util.spec_from_file_location(
            "virgil_lsp_jvm_runtime", Path(__file__).resolve().with_name("jvm-runtime.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["virgil_lsp_jvm_runtime"] = module
    return module


def payload(archive, target):
    """Return canonical file names mapped to (bytes, permissions). Refuse links."""
    files = {}
    root = None
    jvm = TARGETS[target].startswith("jvm-")
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
            if (name != "README.md" and name not in REQUIRED and not name.startswith("virgil/lib/")
                    and not (jvm and (name in JARS or name.startswith("runtime/")))):
                raise ValueError("unexpected archive file: " + name)
            files[name] = (tar.extractfile(member).read(), member.mode & 0o777)
    if not REQUIRED <= files.keys() or not any(n.startswith("virgil/lib/") for n in files):
        raise ValueError("archive lacks executables, library, metadata, or notices")
    manifest = json.loads(files["manifest.json"][0])
    label = manifest["target"]
    if label == "jvm":
        label += "-" + str(manifest.get("platform"))
    if label != TARGETS[target]:
        raise ValueError("archive target does not match " + target)
    for name in ("virgil-lsp", "virgil-lsp-worker"):
        data, mode = files[name]
        if mode & 0o111 != 0o111:
            raise ValueError("archive executable lacks execute permissions: " + name)
        if hashlib.sha256(data).hexdigest() != manifest["sha256"][name]:
            raise ValueError("archive executable differs from its manifest: " + name)
    if jvm:
        for name in sorted(JARS):
            if name not in files or hashlib.sha256(files[name][0]).hexdigest() != manifest["sha256"].get(name):
                raise ValueError("archive jar is missing or differs from its manifest: " + name)
        runtime = {n[len("runtime/"):]: v for n, v in files.items() if n.startswith("runtime/")}
        jvm_runtime().check(manifest["platform"], runtime, manifest.get("runtime") or {})
    files.pop("README.md", None)
    return files
