# Protocol tests

These tests drive `virgil-lsp --stdio` over its real pipes. The interactive
Python sessions give every receive a deadline and print the decoded transcript
and the server's stderr on failure.

- `run.sh` replays golden input bytes and compares output bytes and the exit
  status; its header owns the transcript format.
- `worker.py`, `coordinates.py`, and `fingerprints.py` hold interactive
  sessions that a byte comparison cannot express, such as replies while the
  analysis worker is busy or paused.
- `client_initialization.py` replays the initialization exchange that real
  editors captured, from `clients/<name>/messages.json`, then opens a file,
  requests its document symbols, and shuts down.

## Capturing a new client's initialization messages

The fixtures in `clients/` are the client's own words, so they catch members
that hand-written requests miss, such as the absent `initializationOptions` of
[#176](https://github.com/SaltedTan/virgil-language-server/issues/176). Follow
these steps to add a client:

1. Build the server from a clean checkout and record its commit
   (`git rev-parse --short HEAD`) and the capture date. Each fixture's
   `fixture.json` records its provenance.
2. Isolate the editor's state from yours and use a scratch project outside the
   repository. For VS Code, pass `--user-data-dir` and `--extensions-dir`; for
   Neovim, set `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_STATE_HOME`.
3. Record the bytes in both directions unchanged, either with the editor's own
   trace logging (`virgil.trace.server` in VS Code, `vim.lsp.set_log_level`
   in Neovim) or with a stdio proxy that sits between the editor and the
   server. Prefer the proxy: the existing fixtures came from one, and decoded
   logs can lose the difference between an absent member and `null`. Point the
   editor's server command at the proxy, for example `virgil.server.path` in
   VS Code or the `cmd` of the Neovim configuration.
4. Keep the session minimal: open one file with the configuration under test,
   let `initialize` and `initialized` finish, then close the editor so the
   server gets a `shutdown`, `exit`, and exit status 0.
5. Decode the client's frames into one capture directory per client,
   `<name>/client.json`, an array of decoded JSON-RPC objects in order,
   starting with `initialize`. Preserve notifications and replies to the
   server's requests, including any reply to `client/registerCapability`
   after `didOpen`; the normalizer selects the initialization exchange.
6. Normalize them, from the repository root:

   ```sh
   python3 test/protocol/clients/normalize.py CAPTURES_DIR
   ```

   This writes `test/protocol/clients/<name>/messages.json` and
   `fixture.json` for every capture directory. Add the new client's name,
   editor version, and client library to `CAPTURES` in `normalize.py` first,
   and set the metadata's `captured` and `serverCommit` fields there to the
   recorded date and commit. These values are hard-coded for the batch, so
   include only the new or refreshed captures in `CAPTURES_DIR`.
   The [normalizer's docstring](clients/normalize.py) owns the selection and
   normalization rules. Review the output for machine paths even when the
   path guard passes. The normalizer is not run by `make test`, so rerun it
   only to add or refresh a capture.
7. Keep raw logs out of the repository: they contain source text and machine
   paths. Commit only the normalized `messages.json` and `fixture.json`.

Then run the replay, which `make test` also runs:

```sh
python3 test/protocol/client_initialization.py build/virgil-lsp
```

If the new client gets a different `initialize` reply or document symbols, for
example because it does not declare
`textDocument.documentSymbol.hierarchicalDocumentSymbolSupport`, add its
expected values per capture and document why they differ.

## Bounded LSP 3.17 shape tests

`make test` includes the offline `test-protocol-shapes` target. It needs Python
3.9 or newer and only its standard library. Build and run the target alone:

```sh
make test-protocol-shapes
make test-protocol-shapes PROTOCOL_SHAPES_SEED=42 PROTOCOL_SHAPES_CASES=5000
```

The default is seed 0 and a total budget of 2,000 cases, including mandatory
semantic and lifecycle oracles. It covers the entire current model corpus;
the runner prints the corpus size. Remaining slots combine two seeded
initialization shapes. Smaller budgets keep every method's minimum and every
semantic oracle, then select other shapes using the seed. Budgets below that
mandatory set are rejected.
The seed controls selection, order, and pairwise combinations; it is not a
coverage claim. Wider runs have no fixed wall-time guarantee.

`shape_model.py` reads the pinned, unchanged [3.17.0 model](schema/3.17/README.md)
and verifies its SHA-256. It resolves structures, inheritance, mixins, aliases,
unions, intersections, literals, enums, arrays, maps, and tuples. It stops
recursive references instead of recursively expanding `LSPAny` without a bound.
It creates minimal objects, optional omissions with populated direct siblings,
nullable union branches, empty containers, integer boundaries (0, -1,
2^31-1, 2^31), and empty, non-ASCII, and 1,024-character strings. Descendant
variants construct their required ancestors. This includes the full initialize
capability tree, each workspace/textDocument capability absent, and both values
of every boolean capability. Empty objects and integer boundaries that violate
the schema remain labeled negative probes.

A behavioral oracle sends every request method in the pinned model with minimal
valid params after initialization: covered methods must be handled, and uncovered
methods must return `MethodNotFound`. Shutdown is checked by the session epilogue.
A new request handler therefore requires adding generator coverage. Notifications
have no replies, so their list is explicit: a new notification handler must be
added by hand. The two development requests have small explicit local schemas
because the LSP model does not define them.

Each case has three validity labels, available with `--list`:

- `schema_valid`: structural validity in the pinned model (or the extension's local schema).
- `session_valid`: schema validity plus validity under the negotiated full synchronization, available overlay, version, and capabilities, including the documented JSON number limit.
- `lifecycle_valid`: whether initialize/shutdown ordering is legal. Illegal sequences have exact expected lifecycle errors.

URI normalization, rename eligibility, and other semantic checks are server
policies beyond structural validation. The runner allows only the documented
handler errors (`InvalidParams`, and `RequestFailed` for rename/prepareRename).
It asserts the [number and full-sync limitations](../../docs/known-limitations.md#protocol-numbers-and-synchronization)
as expected behavior. There are currently no known server failures excluded
from the oracles.

`shapes.py` reuses `worker.Session` for child processes, framing and deadline-based
receives. Ordinary cases share bounded groups of at most 32 cases; initialization
and lifecycle cases get isolated sessions. Requests are awaited before shutdown;
notification cases use synchronous requests as barriers and observe overlay
text through document symbols. Same-version and next-version full-text changes,
followed by symbol requests and stderr checks, bound each surviving overlay
version exactly; closed overlays must reject a change as unopened. This checks
each generated synchronization case, including rejection warnings, without a
worker analysis per case. Reference outlines are cached within each session.
Every group checks framing, process survival, exactly one reply per request with the original ID
type, no notification replies, documented error codes, and a clean shutdown/exit.
Dynamic watcher registration requests are answered as a real client would.

Mandatory semantic cases assert that absent/false hierarchy support returns flat
`SymbolInformation[]` and true returns `DocumentSymbol[]` (#196). A worker-backed
project checks that an empty change at version 2 produces a rename edit at
version 2 (#200), with full-text and rejected ranged-change controls. The runner
waits for that rename reply before sending shutdown. Typed IDs, decimal options,
out-of-range numbers, and invalid lifecycle ordering also have fixed oracles.

### Reading and rerunning a failure

```sh
python3 test/protocol/shapes.py build/virgil-lsp --seed 0 --cases 2000 --list
python3 test/protocol/shapes.py build/virgil-lsp --case empty-change-version
```

A failure exits nonzero and prints the seed, stable case ID, validity labels,
exact framed bytes sent, replies, stderr, server/worker/model SHA-256 hashes,
and a command that runs just that case with its setup and epilogue. The same
evidence is saved under `build/protocol-shapes/seed-<seed>/<case-id>/`:
`failure.json`, `input.bin`, `transcript.json`, `replies.json`, and `stderr.txt`.
Checkpoints identify each case's first frame in shared-session traces. Temporary
project fixtures live under the artifact directory and are removed after the
run; a single-case rerun recreates them. A failed shared-session case can be
rerun alone; preserve the original seed/budget to reproduce the complete group
if earlier state is relevant. Timeouts close or kill only the runner's own
`Popen` child, never processes selected by name.

The initial default run took about 2 seconds on the Linux development machine,
excluding compilation. macOS CI (`macos-15`, including Rosetta when applicable)
has not been timed: allow roughly 5–20 seconds as a planning estimate, not a
measured result. Receives have individual deadlines rather than a machine-speed
assertion; the default budget is intended to stay below about one minute.

### Updating the model

Follow the [model update procedure](schema/3.17/README.md).
