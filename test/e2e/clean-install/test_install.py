#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Run the pasted install blocks without errexit or an editor."""
import functools
import hashlib
import http.server
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from run import examples, extract, substitute

REPO = Path(__file__).resolve().parents[3]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="readme-install-")
        cls.root = Path(cls.temporary.name)
        cls.public = cls.root / "public"
        cls.public.mkdir()
        handler = functools.partial(QuietHandler, directory=str(cls.public))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        steps = extract(REPO)
        cls.values = examples(steps)
        cls.steps = substitute(steps, cls.values, cls.values["server_version"],
                               cls.values["extension_version"], cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temporary.cleanup()

    def home(self):
        home = Path(tempfile.mkdtemp(dir=self.root, prefix="home-"))
        return home, {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}

    def paste(self, body, home, env):
        # No -e, pipefail, or inherited SHELLOPTS: this is a terminal paste.
        return subprocess.run(["bash", "--noprofile", "--norc"], input=body, text=True,
                              cwd=home, env=env, capture_output=True, timeout=30)

    def test_plain_nvim_preserves_init_and_reruns(self):
        module = self.public / "editors/nvim/virgil_lsp.lua"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text("-- downloaded module\n")
        for xdg in [None, "", "custom config"]:
            for initial in [None, "init.lua", "init.vim"]:
                with self.subTest(xdg=xdg, initial=initial):
                    home, env = self.home()
                    if xdg is not None:
                        env["XDG_CONFIG_HOME"] = str(home / xdg) if xdg else ""
                    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "nvim"
                    original = 'let g:keep = 1' if initial == "init.vim" else '-- keep my settings'
                    if initial:
                        config.mkdir(parents=True)
                        (config / initial).write_text(original)  # No trailing newline.
                    init = config / (initial or "init.lua")
                    require = 'lua require("virgil_lsp")' if initial == "init.vim" else 'require("virgil_lsp")'
                    first = None
                    for _ in range(2):
                        result = self.paste(self.steps["plain-nvim"], home, env)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertTrue(init.exists(), f"missing {init}")
                        text = init.read_text()
                        if initial:
                            self.assertTrue(text.startswith(original + "\n"))
                        self.assertEqual(text.splitlines().count(require), 1)
                        self.assertEqual((config / "lua/virgil_lsp.lua").read_text(), module.read_text())
                        other = "init.lua" if initial == "init.vim" else "init.vim"
                        self.assertFalse((config / other).exists(), f"conflicting {other}")
                        if first is not None:
                            self.assertEqual(text, first, "rerun changed the init file")
                        first = text
                    if xdg:
                        self.assertFalse((home / ".config/nvim").exists())

    def test_plain_nvim_failed_download_leaves_init_alone(self):
        # A require of a module that didn't download would break every start.
        module = self.public / "editors/nvim/virgil_lsp.lua"
        if module.exists():
            module.unlink()
        for initial in [None, "init.lua", "init.vim"]:
            with self.subTest(initial=initial):
                home, env = self.home()
                config = home / ".config/nvim"
                if initial:
                    config.mkdir(parents=True)
                    (config / initial).write_text("-- keep my settings\n")
                result = self.paste(self.steps["plain-nvim"], home, env)
                self.assertNotEqual(result.returncode, 0)
                for name in ["init.lua", "init.vim"]:
                    if name == initial:
                        self.assertEqual((config / name).read_text(), "-- keep my settings\n")
                    else:
                        self.assertFalse((config / name).exists(), f"created {name}")

    def test_lazyvim_uses_xdg_config_home(self):
        plugin = self.public / "editors/nvim/lazyvim/virgil.lua"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        plugin.write_text("-- downloaded plugin\n")
        for xdg in [None, "custom config"]:
            with self.subTest(xdg=xdg):
                home, env = self.home()
                if xdg:
                    env["XDG_CONFIG_HOME"] = str(home / xdg)
                result = self.paste(self.steps["lazyvim"], home, env)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = Path(env.get("XDG_CONFIG_HOME", home / ".config")) / "nvim"
                self.assertEqual((config / "lua/plugins/virgil.lua").read_text(), plugin.read_text())
                if xdg:
                    self.assertFalse((home / ".config/nvim").exists())

    @unittest.skipUnless(sys.platform.startswith("linux"),
                         "the README checksum commands target Linux and require sha256sum")
    def test_checksum_gates_install_in_plain_bash(self):
        archive = f'virgil-lsp-{self.values["server_version"]}-x86-64-linux.tar.gz'
        vsix = f'virgil-lsp-vscode-{self.values["extension_version"]}.vsix'
        for step, filename in [("server", archive), ("vsix-download", vsix)]:
            for checksum in ["wrong", "missing", "valid"]:
                with self.subTest(step=step, checksum=checksum):
                    home, env = self.home()
                    (self.public / filename).write_bytes(b"corrupted download\n")
                    expected = b"original download\n" if checksum == "wrong" else b"corrupted download\n"
                    listed_file = filename if checksum != "missing" else "other-file"
                    sums = f"{hashlib.sha256(expected).hexdigest()}  {listed_file}\n"
                    (self.public / "SHA256SUMS").write_text(sums)
                    (home / "SHA256SUMS").write_text(sums)
                    bin_dir = home / "bin"
                    bin_dir.mkdir()
                    # Record every extraction, link, server execution, and VSIX install.
                    for command in ["tar", "ln", "virgil-lsp", "code"]:
                        stub = bin_dir / command
                        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "{command}" >> "$HOME/actions"\n')
                        stub.chmod(0o755)
                    env.update(PATH=f"{bin_dir}:/usr/bin:/bin", download_url=self.url)
                    body = self.steps[step]
                    # Also exercise the old split blocks when demonstrating the regression.
                    if step == "vsix-download":
                        body += self.steps.get("vsix-install", "")
                    result = self.paste(body, home, env)
                    recorded = home / "actions"
                    actions = recorded.read_text().splitlines() if recorded.exists() else []
                    if checksum == "valid":
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(actions, ["tar", "ln", "virgil-lsp"] if step == "server" else ["code"])
                    else:
                        self.assertEqual(actions, [], result.stdout + result.stderr)
                        self.assertFalse((home / ".local/opt").exists())
                        self.assertNotEqual(result.returncode, 0, "failed verification must fail the block")


if __name__ == "__main__":
    unittest.main()
