#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Package a universal VSIX, or a platform VSIX from a tested archive's bytes."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("payload", ROOT / "scripts/vsix-payload.py")
payload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["universal", *payload.TARGETS])
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if (args.target == "universal") != (args.archive is None):
        parser.error("platform targets require --archive; universal must omit it")
    source = ROOT / "clients/vscode"
    server = source / "server"
    marker = source / "server-target.json"
    if server.exists() or marker.exists():
        parser.error("remove stale server/ and server-target.json staging files first")
    files = {} if args.archive is None else payload.payload(args.archive, args.target)
    package = json.loads((source / "package.json").read_text())
    suffix = "" if args.target == "universal" else "-" + args.target
    args.out.mkdir(parents=True, exist_ok=True)
    destination = args.out.resolve() / (package["name"] + "-" + package["version"] + suffix + ".vsix")
    try:
        for name, (data, mode) in files.items():
            file = server / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            file.chmod(mode)
        if files:
            marker.write_text(json.dumps({"target": args.target}) + "\n")
        command = ["npm", "run", "package", "--", "--out", str(destination)]
        if files:
            command += ["--target", args.target]
        subprocess.run(command, cwd=source, check=True)
        check = [str(ROOT / "scripts/check-vsix.py"), str(destination), "--target", args.target]
        if args.archive:
            check += ["--archive", str(args.archive.resolve())]
        subprocess.run(check, check=True)
    finally:
        if server.exists():
            shutil.rmtree(server)
        marker.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
