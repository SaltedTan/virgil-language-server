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
INITIAL_CONFIGS = [
    (None, b""),
    ("init.lua", b"vim.g.keep = 1"),
    ("init.lua", b'return require("myconfig")'),
    ("init.vim", b"let g:keep = 1"),
    ("init.vim", b"let g:keep = 1\nfinish"),
]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    # Paths whose response declares more bytes than it sends, as an
    # interrupted transfer does.
    truncated = set()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path not in self.truncated:
            return super().do_GET()
        body = b"local broken = "
        self.send_response(200)
        self.send_header("Content-Length", str(len(body) + 100))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


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
        plugin = self.public / "editors/nvim/virgil_lsp.lua"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        plugin.write_bytes(b"-- downloaded plugin\n")
        for xdg in [None, "", "custom config"]:
            for initial, original in INITIAL_CONFIGS:
                with self.subTest(xdg=xdg, initial=initial, original=original):
                    home, env = self.home()
                    if xdg is not None:
                        env["XDG_CONFIG_HOME"] = str(home / xdg) if xdg else ""
                    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "nvim"
                    if initial:
                        config.mkdir(parents=True)
                        (config / initial).write_bytes(original)
                    for _ in range(2):
                        result = self.paste(self.steps["plain-nvim"], home, env)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        for name in ["init.lua", "init.vim"]:
                            if name == initial:
                                self.assertEqual((config / name).read_bytes(), original)
                            else:
                                self.assertFalse((config / name).exists(), f"created {name}")
                        self.assertEqual((config / "plugin/virgil_lsp.lua").read_bytes(),
                                         plugin.read_bytes())
                    if xdg:
                        self.assertFalse((home / ".config/nvim").exists())

    def test_nvim_failed_and_interrupted_reruns_keep_installation(self):
        for step, served, installed in [
            ("plain-nvim", "editors/nvim/virgil_lsp.lua", "plugin/virgil_lsp.lua"),
            ("lazyvim", "editors/nvim/lazyvim/virgil.lua", "lua/plugins/virgil.lua"),
        ]:
            for failure in ["failed", "interrupted"]:
                for xdg in [None, "", "custom config"]:
                    for initial, original in INITIAL_CONFIGS:
                        with self.subTest(step=step, failure=failure, xdg=xdg,
                                          initial=initial, original=original):
                            source = self.public / served
                            source.parent.mkdir(parents=True, exist_ok=True)
                            source.write_bytes(b"-- downloaded plugin\n")
                            home, env = self.home()
                            if xdg is not None:
                                env["XDG_CONFIG_HOME"] = str(home / xdg) if xdg else ""
                            config = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "nvim"
                            if initial:
                                config.mkdir(parents=True)
                                (config / initial).write_bytes(original)
                            result = self.paste(self.steps[step], home, env)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual((config / installed).read_bytes(), source.read_bytes())
                            before = {path: path.read_bytes() for path in config.rglob("*")
                                      if path.is_file()}
                            if failure == "failed":
                                source.unlink()
                            else:
                                QuietHandler.truncated.add("/" + served)
                            try:
                                result = self.paste(self.steps[step], home, env)
                            finally:
                                QuietHandler.truncated.discard("/" + served)
                            self.assertNotEqual(result.returncode, 0)
                            for path, data in before.items():
                                self.assertEqual(path.read_bytes(), data, f"changed {path}")
                            for name in ["init.lua", "init.vim"]:
                                if name == initial:
                                    self.assertEqual((config / name).read_bytes(), original)
                                else:
                                    self.assertFalse((config / name).exists(), f"created {name}")
                            if xdg:
                                self.assertFalse((home / ".config/nvim").exists())

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
        vsix = f'virgil-lsp-vscode-{self.values["extension_version"]}-linux-x64.vsix'
        for step, filename in [("server", archive), ("vsix-download", vsix)]:
            for checksum in ["wrong", "missing", "near-match", "valid"]:
                with self.subTest(step=step, checksum=checksum):
                    home, env = self.home()
                    (self.public / filename).write_bytes(b"corrupted download\n")
                    expected = (b"original download\n" if checksum in ["wrong", "near-match"]
                                else b"corrupted download\n")
                    listed_file = filename if checksum != "missing" else "other-file"
                    if checksum == "near-match":
                        # A different file must not authorize installing the download,
                        # even when its name matches the requested name as a regex.
                        listed_file = filename.replace(".", "x")
                        download_dir = home / "Downloads/virgil-lsp" if step == "server" else home
                        download_dir.mkdir(parents=True, exist_ok=True)
                        (download_dir / listed_file).write_bytes(expected)
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
