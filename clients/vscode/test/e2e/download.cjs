// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// Downloads VS Code into cache with @vscode/test-electron, unless it is
// there, and prints its executable's path. Both test runners run it in a
// process group of its own, with the tar that extracts the download, so that
// an interrupt can stop both. Usage:
//
//   node test/e2e/download.cjs VERSION CACHE

const { downloadAndUnzipVSCode } = require('@vscode/test-electron');

const [version, cachePath] = process.argv.slice(2);
downloadAndUnzipVSCode({ version, cachePath }).then((executable) => {
  process.stdout.write(`${executable}\n`);
}, (error) => {
  console.error(error?.message ?? String(error));
  process.exitCode = 1;
});
