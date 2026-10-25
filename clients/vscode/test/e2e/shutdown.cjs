// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0

// The shutdown order check that suite.cjs and run.cjs share, for a server's
// input as the recovery script recorded it.

// The notifications that a client may still send after shutdown, on the
// connection, which stays open until exit:
// - textDocument/didOpen: the client sends one for each open document once it
//   is running, and one whose check that the client is running passed just
//   before a stop began can follow shutdown.
// - $/cancelRequest: the client cancels a request that is still pending on
//   its side, such as an automatic inlay hint request for a visible document,
//   and the cancellation can follow shutdown. The server answers every
//   pending request at shutdown, so it ignores the cancellation.
const AFTER_SHUTDOWN = new Set(['textDocument/didOpen', '$/cancelRequest']);

// The methods that a server received from shutdown on, without the
// notifications in AFTER_SHUTDOWN before the last one. A server stopped
// cleanly if they are exactly shutdown and exit; any request, or any other
// notification, after shutdown stays in, as does anything after exit.
function fromShutdown(received) {
  const rest = received.slice(received.indexOf('shutdown'));
  return [...rest.slice(0, -1).filter((method) => !AFTER_SHUTDOWN.has(method)), ...rest.slice(-1)];
}

module.exports = { fromShutdown };
