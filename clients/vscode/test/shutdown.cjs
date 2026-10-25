// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The Extension Host test's shutdown order check (e2e/shutdown.cjs).

const assert = require('node:assert/strict');
const { test } = require('node:test');

const { fromShutdown } = require('./e2e/shutdown.cjs');

const stopped = (methods) => fromShutdown(['initialize', 'initialized', ...methods]).join() === 'shutdown,exit';

test('a stop may have didOpen and $/cancelRequest notifications between shutdown and exit', () => {
  assert.ok(stopped(['shutdown', 'exit']));
  assert.ok(stopped(['textDocument/inlayHint', 'shutdown', '$/cancelRequest', 'exit']));
  assert.ok(stopped(['shutdown', 'textDocument/didOpen', '$/cancelRequest', '$/cancelRequest', 'exit']));
});

test('a stop with a request or another notification after shutdown fails', () => {
  assert.ok(!stopped(['shutdown', 'textDocument/inlayHint', 'exit']));
  assert.ok(!stopped(['shutdown', 'textDocument/documentSymbol', '$/cancelRequest', 'exit']));
  assert.ok(!stopped(['shutdown', 'textDocument/didChange', 'exit']));
  assert.ok(!stopped(['shutdown', '$/setTrace', 'exit']));
});

test('a stop must end with exit, after shutdown', () => {
  assert.ok(!stopped(['shutdown', 'exit', '$/cancelRequest']));
  assert.ok(!stopped(['shutdown', '$/cancelRequest']));
  assert.ok(!stopped(['$/cancelRequest', 'exit']));
  assert.ok(!stopped(['exit']));
});
