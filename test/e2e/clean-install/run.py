#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Execute marked README commands from a standalone bundle, never a checkout.
See docs/releasing.md#clean-install-test for prerequisites and substitutions.
"""
import argparse
import functools
import http.server
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
STEPS = {
    "README.md": ["server"],
    "clients/vscode/README.md": ["vsix-download", "vsix-install", "vscode-root"],
    "editors/nvim/README.md": ["lazyvim", "plain-nvim", "nvim-root"],
}


def extract(bundle):
    steps = {}
    for name, expected in STEPS.items():
        markdown = (bundle / name).read_text()
        found = re.findall(
            r"<!-- clean-install: ([a-z-]+) -->\s*\n *```sh\n(.*?)\n *```",
            markdown, re.S)
        if [key for key, _ in found] != expected:
            raise ValueError(f"README steps in {name}: expected {expected}, found {[k for k, _ in found]}")
        for key, body in found:
            # Numbered Markdown steps indent their fences and body by 3 spaces.
            steps[key] = "\n".join(line[3:] if line.startswith("   ") else line for line in body.splitlines()) + "\n"
    return steps


def bundle(out):
    """The only operation that reads the source checkout; run outside the container."""
    repo = HERE.parents[2]
    out.mkdir(parents=True, exist_ok=False)
    for name in [*STEPS, "editors/nvim/virgil_lsp.lua", "editors/nvim/lazyvim/virgil.lua",
                 "test/e2e/lazyvim/lazy-lock.json"]:
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    for name in ["run.py", "prerequisites.sh", "nvim.lua", "suite.cjs"]:
        shutil.copyfile(HERE / name, out / name)
    (out / "package.json").write_text(json.dumps({
        "name": "clean-install-driver", "version": "0.0.0", "publisher": "test",
        "engines": {"vscode": "^1.90.0"}, "main": "suite.cjs",
    }) + "\n")
    extract(out)
    print(f"Self-contained bundle: {out}")


def fixture(home):
    project = home / "project"
    project.mkdir()
    (project / ".virgil-lsp.json").write_text(json.dumps({"version": 1, "projects": [{
        "name": "install", "sources": ["*.v3"], "virgilDependencies": ["lib/util/*.v3"]
    }]}) + "\n")
    (project / "Install.v3").write_text(
        "component Install {\n"
        "    def text() -> int {\n"
        "        var text: int = StringBuilder.new().toString();\n"
        "        return text;\n"
        "    }\n"
        "}\n")
    return project


def run(args):
    source = args.bundle.resolve()
    work = args.out.resolve()
    work.mkdir(parents=True, exist_ok=False)
    # Deliberately minimal environment; no inherited root, editor profile, or XDG paths.
    base_env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "TERM": "xterm-256color"}
    steps = extract(source)
    public = work / "public"
    public.mkdir()
    for file in args.assets.resolve().iterdir():
        if file.is_file():
            shutil.copyfile(file, public / file.name)
    # This is an expectation from the producer, never a digest of downloaded bytes.
    digest = args.archive_sha256.read_text().split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not re.fullmatch(r"[0-9a-f]{64}", args.vsix_sha256):
        raise ValueError("producer SHA-256 must be 64 lowercase hex digits")
    archive = f"virgil-lsp-{args.version}-x86-64-linux.tar.gz"
    (public / "SHA256SUMS").write_text(f"{digest}  {archive}\n{args.vsix_sha256}  {args.vsix}\n")
    shutil.copytree(source / "editors", public / "editors")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(public))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    extension_version = re.fullmatch(r"virgil-lsp-vscode-(.+)\.vsix", args.vsix)[1]
    # Only the versions and the two README-documented download locations change.
    replacements = {"0.1.0-alpha.3": args.version,
                    "extension_version=0.1.2": f"extension_version={extension_version}",
                    "https://github.com/SaltedTan/virgil-language-server/releases/download/v$server_version": url,
                    "https://raw.githubusercontent.com/SaltedTan/virgil-language-server/v0.1.0-alpha.3/editors/nvim": url + "/editors/nvim"}
    scripts = work / "steps"
    scripts.mkdir()
    for key, body in steps.items():
        # Download URLs are replaced first because one contains the example version.
        for old, new in reversed(list(replacements.items())):
            body = body.replace(old, new)
        (scripts / (key + ".sh")).write_text(body)
    q = shlex.quote

    def step(name):
        return f'step={q(name)}; echo "README step: $step"; source {q(str(scripts / (name + ".sh")))}\n'

    try:
        for editor in ["plain-nvim", "lazyvim", "vscode"]:
            home = work / editor
            home.mkdir()
            project = fixture(home)
            (home / "tmp").mkdir()
            env = dict(base_env, HOME=str(home), TMPDIR=str(home / "tmp"),
                       CLEAN_BUNDLE=str(source), CLEAN_PROJECT=str(project), CLEAN_EDITOR=editor)
            if editor == "vscode":
                code = shutil.which("code", path=base_env["PATH"])
                if code is None:
                    raise ValueError("missing README prerequisite: code on PATH")
                cli = Path(code).resolve()
                env["CLEAN_CODE_EXECUTABLE"] = str(cli.parent.parent / "code")
            script = 'set -Eeuo pipefail\ntrap \'echo "FAIL step: $step (line $LINENO)" >&2\' ERR\n'
            script += step("server")
            if editor == "vscode":
                script += step("vsix-download") + step("vsix-install")
                command = (
                    'xvfb-run -a "$CLEAN_CODE_EXECUTABLE" --no-sandbox --disable-gpu-sandbox --disable-workspace-trust '
                    '--skip-welcome --skip-release-notes --disable-updates '
                    '--extensionDevelopmentPath="$CLEAN_BUNDLE" --extensionTestsPath="$CLEAN_BUNDLE/suite.cjs" '
                    '"$CLEAN_PROJECT"')
            else:
                if editor == "lazyvim":
                    script += f'step=lazyvim-prerequisite; bash {q(str(source / "prerequisites.sh"))} lazyvim\n'
                script += step(editor)
                command = 'nvim --headless -n -c \'lua dofile(vim.env.CLEAN_BUNDLE .. "/nvim.lua")\' "$CLEAN_PROJECT/Install.v3"'
            script += f'step={editor}-bundled-library; timeout --kill-after=10 120 {command}\n'
            script += step("vscode-root" if editor == "vscode" else "nvim-root")
            script += f'step={editor}-explicit-root; timeout --kill-after=10 120 {command}\n'
            (home / "run.sh").write_text(script)
            with (home / "run.log").open("w") as log:
                print(f"Running {editor}: {home / 'run.log'}", flush=True)
                subprocess.run(["bash", str(home / "run.sh")], cwd=home, env=env,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            for mode in ["bundled", "explicit"]:
                receipt = home / f"{editor}-{mode}.json"
                evidence = json.loads(receipt.read_text())
                if evidence["checks"] != ["diagnostic", "definition", "hover"]:
                    raise ValueError(f"incomplete editor evidence: {receipt}")
            print(f"PASS {editor}: bundled library and explicit root", flush=True)
    finally:
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pack = sub.add_parser("bundle")
    pack.add_argument("out", type=Path)
    test = sub.add_parser("run")
    test.add_argument("--bundle", type=Path, required=True)
    test.add_argument("--assets", type=Path, required=True)
    test.add_argument("--archive-sha256", type=Path, required=True)
    test.add_argument("--vsix-sha256", required=True)
    test.add_argument("--version", required=True)
    test.add_argument("--vsix", required=True)
    test.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "bundle":
        bundle(args.out)
    else:
        run(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"FAIL clean install: {error}")
