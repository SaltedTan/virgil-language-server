#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Check .githooks/pre-commit and .githooks/pre-push in temporary Git repositories.

Each pre-commit test uses a stand-in `build/virgil-lsp` and a stand-in `Makefile`
with the real exclusion list, and the pre-push tests use a stand-in `make` on
PATH that logs its invocations. The last tests check the real hooks' executable
bit and that `make print-format-exclude` reports the real `FORMAT_EXCLUDE`.
"""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".githooks"
PRE_COMMIT = HOOKS / "pre-commit"
PRE_PUSH = HOOKS / "pre-push"
MAKEFILE = ROOT / "Makefile"

FAKE_MAKEFILE = (
    "FORMAT_EXCLUDE = \\\n"
    "\tvendor/ \\\n"
    "\ttest/fixtures/ \\\n"
    "\ttest/e2e/nvim/fixture/ \\\n"
    "\ttest/e2e/lazyvim/fixture/ \\\n"
    "\tdocs/examples/\n"
    "\n"
    ".PHONY: print-format-exclude\n"
    "print-format-exclude:\n"
    "\t@printf '%s\\n' $(FORMAT_EXCLUDE)\n"
    "\n"
    "build/virgil-lsp:\n"
    "\t@true\n"
)

FAKE_SERVER = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Stand-in virgil-lsp for pre-commit tests. Treats 'UNFORMATTED' as a marker.
    set -u
    if [ "${1:-}" != "format" ] || [ "${2:-}" != "--check" ]; then
        echo "stand-in: unexpected arguments: $*" >&2
        exit 2
    fi
    shift 2
    rc=0
    for f in "$@"; do
        if grep -q 'UNFORMATTED' -- "$f" 2>/dev/null; then
            echo "$f:1: not formatted" >&2
            rc=1
        fi
    done
    exit $rc
""")


def git(*args, cwd, env=None, check=True, input=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, input=input)


def make_executable(path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def init_repo(path):
    git("init", "-q", "-b", "main", cwd=path)
    git("config", "user.email", "t@example.invalid", cwd=path)
    git("config", "user.name", "Test User", cwd=path)
    git("config", "commit.gpgsign", "false", cwd=path)


def write_script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    make_executable(path)


class PreCommitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pre-commit-")
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        init_repo(self.repo)
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        shutil.copy2(PRE_COMMIT, hooks / "pre-commit")
        make_executable(hooks / "pre-commit")
        git("config", "core.hooksPath", ".githooks", cwd=self.repo)
        (self.repo / "Makefile").write_text(FAKE_MAKEFILE)
        deps = self.repo / "vendor" / "virgil" / "aeneas" / "DEPS"
        deps.parent.mkdir(parents=True)
        deps.touch()
        write_script(self.repo / "build" / "virgil-lsp", FAKE_SERVER)

    def stage(self, path, content, disk=None):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git("add", "--", path, cwd=self.repo)
        if disk is not None:
            target.write_text(disk)

    def run_hook(self, env=None):
        return subprocess.run([str(self.repo / ".githooks" / "pre-commit")], cwd=self.repo,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=env)

    def test_no_staged_files_passes(self):
        result = self.run_hook()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_formatted_v3_passes(self):
        self.stage("a.v3", "ok\n")
        result = self.run_hook()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unformatted_v3_fails_with_hint(self):
        self.stage("a.v3", "UNFORMATTED\n")
        result = self.run_hook()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("a.v3:1: not formatted", result.stderr)
        self.assertIn("build/virgil-lsp format --write", result.stderr)

    def test_staged_formatted_overrides_unformatted_working_tree(self):
        self.stage("a.v3", "ok\n", disk="UNFORMATTED\n")
        result = self.run_hook()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_staged_unformatted_overrides_formatted_working_tree(self):
        self.stage("a.v3", "UNFORMATTED\n", disk="ok\n")
        result = self.run_hook()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("a.v3", result.stderr)

    def test_excluded_directories_are_skipped(self):
        for prefix in ("vendor/", "test/fixtures/", "test/e2e/nvim/fixture/",
                       "test/e2e/lazyvim/fixture/", "docs/examples/"):
            name = prefix + "x.v3"
            self.stage(name, "UNFORMATTED\n")
        result = self.run_hook()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_deleted_v3_file_does_not_fail(self):
        self.stage("a.v3", "ok\n")
        git("commit", "-q", "--no-verify", "-m", "add", cwd=self.repo)
        git("rm", "-q", "--", "a.v3", cwd=self.repo)
        result = self.run_hook()
        self.assertEqual(0, result.returncode, result.stderr)

    def test_filename_with_spaces(self):
        self.stage("a file.v3", "UNFORMATTED\n")
        result = self.run_hook()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("a file.v3", result.stderr)

    def test_filename_starting_with_dash(self):
        self.stage("-dash.v3", "UNFORMATTED\n")
        result = self.run_hook()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("-dash.v3", result.stderr)

    def test_non_v3_file_does_not_invoke_make(self):
        bin_dir = self.repo / "fake-bin"
        bin_dir.mkdir()
        write_script(bin_dir / "make", "#!/usr/bin/env bash\necho should-not-run >&2\nexit 1\n")
        self.stage("README.md", "hi\n")
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:" + env.get("PATH", "")
        result = self.run_hook(env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("should-not-run", result.stdout + result.stderr)

    def test_vscode_npm_run_check_with_node_modules(self):
        bin_dir = self.repo / "fake-bin"
        bin_dir.mkdir()
        log = self.repo / "npm.log"
        write_script(bin_dir / "npm", f"#!/usr/bin/env bash\necho \"$@\" >> {log}\nexit 0\n")
        (self.repo / "clients" / "vscode" / "node_modules").mkdir(parents=True)
        self.stage("clients/vscode/pkg.ts", "ok\n")
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:" + env.get("PATH", "")
        result = self.run_hook(env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("run check", log.read_text())

    def test_vscode_skipped_without_node_modules(self):
        self.stage("clients/vscode/pkg.ts", "ok\n")
        result = self.run_hook()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("skipping VS Code type-check", result.stderr)

    def test_actionlint_runs_when_available(self):
        bin_dir = self.repo / "fake-bin"
        bin_dir.mkdir()
        log = self.repo / "actionlint.log"
        write_script(bin_dir / "actionlint",
                     f"#!/usr/bin/env bash\necho \"[$PWD]\" >> {log}\nfor f in \"$@\"; do echo \"$f\" >> {log}; done\nexit 0\n")
        self.stage(".github/workflows/ci.yml",
                   "name: CI\non: push\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: true\n")
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:" + env.get("PATH", "")
        result = self.run_hook(env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        text = log.read_text()
        self.assertIn(".github/workflows/ci.yml", text)

    def test_actionlint_skipped_when_absent(self):
        self.stage(".github/workflows/ci.yml", "name: CI\non: push\njobs: {}\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
        result = self.run_hook(env=env)
        self.assertEqual(0, result.returncode, result.stderr)


class PrePushTests(unittest.TestCase):
    ZERO = "0" * 40

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pre-push-")
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        init_repo(self.repo)
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        shutil.copy2(PRE_PUSH, hooks / "pre-push")
        make_executable(hooks / "pre-push")
        git("config", "core.hooksPath", ".githooks", cwd=self.repo)
        self.bin_dir = self.repo / "fake-bin"
        self.bin_dir.mkdir()
        self.make_log = self.repo / "make.log"
        write_script(self.bin_dir / "make", textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "$@" >> {self.make_log}
            if [ -e {self.repo}/FAIL_make_$1 ]; then exit 1; fi
            exit 0
        """))

    def env(self):
        env = os.environ.copy()
        env["PATH"] = f"{self.bin_dir}:" + env.get("PATH", "")
        return env

    def run_hook(self, stdin, env=None):
        return subprocess.run([str(self.repo / ".githooks" / "pre-push"), "origin", "git@example"],
                              input=stdin, cwd=self.repo, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env or self.env())

    def commit(self, files, message="c"):
        for path, content in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            git("add", "--", path, cwd=self.repo)
        git("commit", "-q", "--no-verify", "-m", message, cwd=self.repo)
        return git("rev-parse", "HEAD", cwd=self.repo).stdout.strip()

    def test_runs_check_format_and_test_unit(self):
        sha = self.commit({"a.txt": "x"})
        stdin = f"refs/heads/main {sha} refs/heads/main {self.ZERO}\n"
        result = self.run_hook(stdin)
        self.assertEqual(0, result.returncode, result.stderr)
        log_lines = self.make_log.read_text().splitlines()
        self.assertIn("check-format", log_lines)
        self.assertIn("test-unit", log_lines)

    def test_fails_when_check_format_fails(self):
        sha = self.commit({"a.txt": "x"})
        (self.repo / "FAIL_make_check-format").touch()
        stdin = f"refs/heads/main {sha} refs/heads/main {self.ZERO}\n"
        result = self.run_hook(stdin)
        self.assertEqual(1, result.returncode, result.stderr)

    def test_fails_when_test_unit_fails(self):
        sha = self.commit({"a.txt": "x"})
        (self.repo / "FAIL_make_test-unit").touch()
        stdin = f"refs/heads/main {sha} refs/heads/main {self.ZERO}\n"
        result = self.run_hook(stdin)
        self.assertEqual(1, result.returncode, result.stderr)

    def test_deletion_only_push_skips_everything(self):
        stdin = f"(delete) {self.ZERO} refs/heads/old {'a'*40}\n"
        result = self.run_hook(stdin)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.make_log.exists(),
                         self.make_log.read_text() if self.make_log.exists() else "")

    def test_mixed_deletion_and_push_still_runs_checks(self):
        sha = self.commit({"a.txt": "x"})
        stdin = (f"refs/heads/main {sha} refs/heads/main {self.ZERO}\n"
                 f"(delete) {self.ZERO} refs/heads/old {'a'*40}\n")
        result = self.run_hook(stdin)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("check-format", self.make_log.read_text())

    def test_vscode_checks_run_when_touched(self):
        sha0 = self.commit({"a.txt": "x"})
        sha1 = self.commit({"clients/vscode/x.ts": "ok\n"})
        (self.repo / "clients" / "vscode" / "node_modules").mkdir(parents=True)
        npm_log = self.repo / "npm.log"
        write_script(self.bin_dir / "npm",
                     f"#!/usr/bin/env bash\necho \"$@\" >> {npm_log}\nexit 0\n")
        stdin = f"refs/heads/main {sha1} refs/heads/main {sha0}\n"
        result = self.run_hook(stdin)
        self.assertEqual(0, result.returncode, result.stderr)
        text = npm_log.read_text()
        self.assertIn("run check", text)
        self.assertIn("test", text.splitlines())

    def test_vscode_checks_skipped_when_untouched(self):
        sha0 = self.commit({"a.txt": "x"})
        sha1 = self.commit({"src/other.v3": "ok\n"})
        (self.repo / "clients" / "vscode" / "node_modules").mkdir(parents=True)
        npm_log = self.repo / "npm.log"
        write_script(self.bin_dir / "npm",
                     f"#!/usr/bin/env bash\necho \"$@\" >> {npm_log}\nexit 0\n")
        stdin = f"refs/heads/main {sha1} refs/heads/main {sha0}\n"
        result = self.run_hook(stdin)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(npm_log.exists(),
                         npm_log.read_text() if npm_log.exists() else "")

    def test_new_branch_push_checks_vscode_against_main(self):
        self.commit({"a.txt": "x"})
        git("checkout", "-q", "-b", "feat", cwd=self.repo)
        sha1 = self.commit({"clients/vscode/y.ts": "ok\n"})
        (self.repo / "clients" / "vscode" / "node_modules").mkdir(parents=True)
        npm_log = self.repo / "npm.log"
        write_script(self.bin_dir / "npm",
                     f"#!/usr/bin/env bash\necho \"$@\" >> {npm_log}\nexit 0\n")
        stdin = f"refs/heads/feat {sha1} refs/heads/feat {self.ZERO}\n"
        result = self.run_hook(stdin)
        self.assertEqual(0, result.returncode, result.stderr)
        text = npm_log.read_text()
        self.assertIn("run check", text)
        self.assertIn("test", text.splitlines())


class RepositoryTests(unittest.TestCase):
    def test_hooks_exist_and_are_executable(self):
        for path in (PRE_COMMIT, PRE_PUSH):
            self.assertTrue(path.is_file(), f"{path} missing")
            self.assertTrue(path.stat().st_mode & 0o111, f"{path} not executable")

    def test_print_format_exclude_matches_makefile(self):
        result = subprocess.run(["make", "-s", "--no-print-directory", "print-format-exclude"],
                                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, check=True)
        printed = [line for line in result.stdout.splitlines() if line.strip()]
        text = MAKEFILE.read_text()
        match = re.search(r"^FORMAT_EXCLUDE\s*=\s*((?:[^\n]*\\\n)*[^\n]*)",
                          text, re.MULTILINE)
        self.assertIsNotNone(match, "FORMAT_EXCLUDE not found in Makefile")
        expected = [token for token in match.group(1).replace("\\\n", " ").split()
                    if token]
        self.assertEqual(expected, printed)


if __name__ == "__main__":
    unittest.main()
