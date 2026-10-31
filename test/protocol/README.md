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

## Evicted-snapshot reproduction

`evicted_snapshot.py` repeats the case of
[#330](https://github.com/SaltedTan/virgil-language-server/issues/330) with a
real worker. Its two projects' analyses take about 13 MB each under the
32 MiB [snapshot budget](../../docs/architecture.md#analysis-snapshots-partly-present).
Before #330, an edit to the compiler left the compiler's snapshot evicted, and
its requests answered null or ContentModified until the next edit; #330
analyzes an evicted snapshot again for the first request that needs it. Since
[#373](https://github.com/SaltedTan/virgil-language-server/issues/373), the
edit's own analyses are admitted, by freeing each project's stale snapshot, so
the compiler's requests are answered without a demand analysis. It is opt-in, and `make test` doesn't
run it, since its fixture is a copy of the pinned Aeneas sources and
`apps/Demangle`, whose two analyses take about 13 MB each. Run it by hand
from the repository root after `make all`, with `TMPDIR` pointing at a scratch
directory that has room for the copy (about 6 MB):

```sh
TMPDIR=<scratch> python3 test/protocol/evicted_snapshot.py [build/virgil-lsp]
```

The `demangle` project depends on the `compiler` project's sources, so an
edit to a compiler source schedules both projects. It opens
`aeneas/src/vst/Verifier.v3` and `apps/Demangle/Demangle.v3`, edits the first,
and then requests hover, definition, and semantic tokens in both,
printing each step's replies, their times, and the latest snapshot as JSON
lines. It passes when the compiler's requests are answered after the edit, no
refresh request arrives during the requests, since the compiler's tokens and
hints after the edit are those the client already has, and no analysis runs
afterwards.
Automatic analyses have no completion signal, so it waits until the latest
snapshot's generation hasn't changed for four seconds
(`EVICTED_SNAPSHOT_QUIET` changes that); every receive has a deadline. On
`ced30385`, before the fix, the compiler's hover and definition answer null
and its semantic tokens fail with `ContentModified`.

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

`make test` runs the offline shape tests, with the same command as the
`test-protocol-shapes` target. They need Python 3.9 or newer and only its
standard library. Build and run the target alone:

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
- `session_valid`: schema validity plus validity under the negotiated full synchronization, available overlay, version, and capabilities, including the documented JSON number limit. Supertypes and subtypes cases are never session-valid: the model allows any `data` in a type hierarchy item, but the server accepts only the [data](../../docs/architecture.md#type-hierarchy) that its own prepareTypeHierarchy gave, and refuses other data with `InvalidParams`. Incoming and outgoing calls cases are never session-valid for the same reason: the server accepts only the [data](../../docs/architecture.md#call-hierarchy) that its own prepareCallHierarchy gave.
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

### Successful reply shapes

`reply_shapes.py` checks successful replies against the same pinned model.
Every session checks each successful `initialize`, `textDocument/hover`,
`textDocument/completion`, `textDocument/documentSymbol`, and `workspace/symbol`
result as it arrives, and the params of each `textDocument/publishDiagnostics`
notification. Each type comes from the method's request or notification entry
in the model, not from a copy in the tests. Error replies keep their envelope
and error-code checks and are never checked as results.

The validator resolves references to structures, enumerations, and type
aliases, `extends` and `mixins`, unions, arrays, tuples, maps, literal
structures, string, integer, and boolean literal types, and `null`
alternatives. `integer` and `uinteger` must be JSON integers in their 32-bit
ranges and `decimal` any finite number; a JSON boolean is never a number. An
enumeration without custom values accepts only its listed values. `LSPAny`,
`LSPObject`, and `LSPArray` accept any value of their JSON kind, so `data` and
`experimental` stay open, but the validator still walks them within the same
bounds below, and their numbers must be finite.

Structures are open: a property the model does not name is allowed and not
checked, while every property it names must be present when required and have
its type when present. The model marks no structure as closed, and its `since`
annotations show protocol versions adding optional properties to existing
structures, so rejecting unknown properties would fail lawful replies. The cost
is that an unknown property cannot rule out a union alternative: a
`WorkspaceSymbol.location` with a malformed `range` still matches the
`{ uri }` alternative. Since the server never sends `WorkspaceSymbol`, the
shapes runner also checks every non-null `workspace/symbol` result as the
model's `SymbolInformation[]`, so a malformed `range` there fails.

The validator stops at 64 levels of JSON nesting, at 500,000 type checks for one
value (failed union alternatives included), and at a reference cycle that
consumes no JSON. Those bounds, a model kind it does not handle (intersections,
which these methods don't reach), and an unknown type name each fail with an
`unsupported model path` error naming the method, the type, and the JSON path;
nothing is skipped. A shape failure names the method, the JSON path, the
innermost type, and the properties that led to it:

```text
textDocument/completion result $.items[0].textEdit.insert.end: Position: missing required property "character" (in CompletionList.items > CompletionItem.textEdit > InsertReplaceEdit.insert > Range.end)
```

Generated requests use an untitled document, which gets no semantic analysis,
so their hovers are null and their workspace symbols empty. The
`full-change-version-control` case's project therefore also requests a hover,
a completion under negotiated `insertReplaceSupport` (whose items must use
`InsertReplaceEdit`), and workspace symbols, each with a null or empty
alternative, and opens a document with a syntax error for populated
diagnostics. A whole run fails unless every method had a populated reply and a
null or empty one (the model gives `initialize` neither); the runner prints the
count of each kind.

The model checks structure only. It cannot prove that a range's start precedes
its end, that a reply is current for the document version, that a result uses
the alternative the client negotiated, or that candidates are correct. The
semantic oracles keep those checks: a shape-valid `DocumentSymbol[]` still fails
where flat symbols were negotiated, and an empty but complete completion list
can still be a regression that the model accepts.

`test_reply_shapes.py` checks the validator on hand-written JSON, independent
of the generator, and runs in `make test`:

```sh
python3 test/protocol/test_reply_shapes.py
```

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

`code_actions.py` checks literal-support advertisement, exact versioned semicolon edits, client diagnostics, staleness after edits, statements that swallowed a later line, and invalid params over stdio.

## Seeded source mutations

`source_mutations.py` edits reviewed source and checks that every request
still gets a lawful reply, that worker failures stay contained, and that the
project analyzes again once its source is clean. It is opt-in: neither
`make test` nor CI runs it. It needs Python 3.9 or newer and only its standard
library. From the repository root:

```sh
make test-source-mutations
make test-source-mutations SOURCE_MUTATIONS_SEED=20261029 SOURCE_MUTATIONS_CASES=500
make test-source-mutations SOURCE_MUTATIONS_CASES=10 SOURCE_MUTATIONS_ARGS=--stress
python3 test/protocol/source_mutations.py build/virgil-lsp --replay CASE.json
```

The default is seed 20261028 and 100 cases. `--plan-only` writes the
manifests and case records without starting a server.

### What a case does

Each case starts a server of its own, with `--worker-timeout-ms=5000`, on a
temporary project: a clean observer file and the files of one
[repair seed](../fixtures/repair/cases/), either as reviewed without its
cursor marker or without the marker's line, which mostly parses. After opening
them, it applies one to three steps of mutations and sends each changed file's
full text with `didChange`. The mutator finds token boundaries with a lexer; it
has no grammar. It deletes, inserts, duplicates, replaces, and truncates
tokens, writes malformed literals, damages brackets, comments, and strings,
changes line ends to CRLF or a lone CR, and inserts non-ASCII and non-BMP
characters. Other edits usually keep the text parseable, so that analysis and
the worker run on it too: odd literals that still parse, such as `0i`,
renaming a name everywhere, swapping two names or numbers, and duplicating or
deleting a line. Half the cases use only those. About one mutation in 16, in
any case, is a bounded stress edit instead: deep nesting, thousands of
declarations, or a long member or operator chain.

After each step, the case sends its requests at once and waits for every
reply. They go round robin through the 19 request methods of the coverage
manifest (`MAIN_METHODS`), with UTF-16 positions in the current text, mostly
at names, sometimes at the end or inside a surrogate pair, and params for each
method. Rename edits are never applied. A prepare request that returns items
gets supertypes and subtypes, or incoming and outgoing calls, for the first
item. `run.json` lists every prepare request whose follow-ups weren't sent,
with the reason: no items, an error, the request cap, or an item larger than
16 KiB. Mutated source rarely analyzes to items, so the restoration step
covers that branch on clean source.

After opening the files, and after each step's replies, a barrier hover on
the observer waits for any analysis that is due or running (the
[freshness rules](../../docs/architecture.md#definition)). Every analysis
that ends between two barriers therefore had the source of the step between
them, and a worker failure is matched against that source alone.

Finally the case restores every seed file to clean text and checks that the
observer's hover and definition, the incoming calls of its method, and the
supertypes of its class are exact, which needs a completed analysis, on a
replacement worker if one died, and that no file has diagnostics. Then it
shuts the server down. The server runs in a session of its own, so its
workers share its process group; cleanup kills what is left of that group by
its ID, before the server is reaped, so a stopped or hung server leaves no
worker behind. Nothing is killed by name.

### Oracles

A case fails if:

- a frame or message is malformed, the server's output ends, the server exits
  with a status other than 0 after `exit`, or it writes after `exit`;
- a request gets no reply or two, or a reply with a changed ID type; a
  notification gets a reply; or the server sends a request, which no client
  capability here asks for;
- an error is not a JSON-RPC error object (an integer `code`, a string
  `message`, and optional `data` of any type, and nothing else), or not one
  that its method documents: `RequestFailed` for bounded replies and rename
  refusals, and `ContentModified` for semantic tokens. Valid params never get
  `InvalidParams`;
- a successful `initialize`, hover, completion, document-symbol, or
  workspace-symbol result, or diagnostics params, fail the
  [reply-shape validator](#successful-reply-shapes); a value past the
  validator's bounds is recorded as unchecked rather than failed;
- a `window/showMessage` is not the documented warning that an automatic
  analysis failed, or there are more of them than failures in the server log;
- a request, the case, or the run passes its deadline; every read, write,
  wait for exit, and cleanup checks all three, and a write also stops at the
  deadline of any request still waiting for its reply, which starts before
  the request is written;
- restoration does not give the clean answers above;
- a worker failure matches no documented signature.

If `initialize` advertises a request method that the coverage manifest
doesn't list, or doesn't advertise one that it lists, the case fails as
`coverage-manifest`, so that a new capability can't go unexercised: add the
method to `MAIN_METHODS` or `FOLLOW_UPS`, with its params and documented
errors.

Worker failures are recorded apart from server and protocol failures, from
the server's log: the worker's process ID, what happened, its exit status, its
trap, and its innermost frames. One that matches a narrow signature in
`KNOWN_WORKER_FAILURES` leaves the case passing as `known-worker-trap`; the
signature names the trap, its innermost frame, and a condition that the
source of the failed analysis must meet, from a row of the
[Aeneas crash paths](../../docs/aeneas-crash-paths.md): row 1's `0i` literal at
either trap site, rows 2, 7, and 17, and a stack overflow, which prints no
frames, after nesting deeper than 1,000 (row 13) or a chain longer than 1,000
(row 25). The conditions count only executable tokens: a lexer pass drops
comments, strings, and characters first, so 1,000 `(` in a comment are no
nesting. A long chain may also exceed the worker's decoded
[result budget](../../docs/decisions/0004-analysis-worker-process.md#restart-policy).
Any other worker failure, including a timeout, fails the case as
`worker-failure`. Each records whether restoration then succeeded on a
replacement worker. To accept a newly found compiler trap, add its row to the
crash paths and a signature for it; never accept worker deaths in general.

### Bounds

| Option | Default | Hard cap | With `--stress` |
| --- | --- | --- | --- |
| `--cases` | 100 | 1,000 | 10, at most 10 |
| `--source-bytes` | 32 KiB of source per case | 64 KiB | 1 MiB of document charge, at most 1 MiB |
| `--requests` per case, everything included | 24, at least 15 | 64 | the same |
| `--request-timeout` | 10 s | 30 s | the same |
| `--case-timeout` | 30 s | 120 s | the same |
| `--run-timeout` | 180 s | 900 s | the same |
| `--artifact-bytes` | 64 MiB | 256 MiB | the same |

A limit past its cap, or deadlines that don't satisfy request ≤ case ≤ run,
are rejected with exit status 2 before anything runs. The request cap counts
every feature request a case sends: its planned requests, the follow-ups, a
barrier after opening and after each of up to three steps, and the six
restoration requests. A plan leaves four requests for follow-ups, so at
least 15 are needed, and the 24 by default leave 9 to 12 for a case's
planned requests. The source bound counts the observer too. With `--stress`, it counts the
[document store's](../../docs/architecture.md#document-store) charge instead:
each open document's text plus 256 bytes of metadata, its language ID, and its
URI, estimated at 1,024 bytes, so it stays below the server's 1 MiB capacity.
Each stress case starts with a deep, dense, or chain edit of up to all the
room left.

### Artifacts and replay

A run replaces `build/source-mutations/seed-<seed>/`
(`stress-seed-<seed>/` with `--stress`):

- `mutations.json` and `requests.json`: each case's mutations, with the
  SHA-256 and size of each resulting text, and its planned requests, after a
  header with the seed, the bounds, and the SHA-256 of the harness and every
  seed file. They hold no paths or times, so the same seed and inputs give
  identical bytes; compare two runs with `cmp`.
- `run.json`: the server and worker SHA-256 and `--version`, the outcome and
  request counts, worker failures, follow-ups not sent, unchecked shapes,
  failures with replay commands, the elapsed time, the bytes written, and
  whether the run is incomplete.
- `cases/<id>/`: `case.json`, the replay record; `result.json`, the
  classification; `transcript.jsonl`, the decoded messages with their times;
  and `stderr.txt`.

A replay record holds the exact text of every file opened and every change,
the project configuration, the `initialize` params, and each request's params
as sent, including the items of follow-ups, with the project's URI as
`${ROOT}`. `--replay CASE.json` runs that case in a new temporary project and
sends the same messages; it needs neither the seed nor the seed files, and
writes to `build/source-mutations/replay-<id>/`. Before starting a server,
replay rejects, with exit status 2, a record that this runner could not have
written: limits past the hard caps, which are the `--stress` ones only for a
stress record; any source state, after opening, after each step, or after
restoration, past the source bound; more feature requests in all than the
request cap; or a change to a file that the case does not open.

Everything written counts against `--artifact-bytes`: manifests, records,
results, transcripts, logs, and `run.json`. A run is rejected with exit status
2 unless the budget holds the manifests, the complete replay record and
result of the plan's largest failing case, with room for the 16 KiB items of
as many follow-ups as its cap allows, and 64 KiB for `run.json`; those last two
stay reserved. Passing cases may fill the rest, and their records and results
are written whole or not at all; `run.json` lists the ones left out, which
`--plan-only` recreates without follow-ups. A failing case's record and result
are always written whole, from the reserve. A cut transcript ends with a
`{"truncated": true, ...}` line, a cut log with a `[truncated: ...]` line, and
the case's `result.json` has `"evidence_complete": false`: that evidence is
partial, and replaying the record recreates it whole. Lists in `run.json` and
`result.json` keep their first 32 entries and count the rest.

### Reading a failure

A failing run stops at its first failing case, exits 1, and prints the case,
its class, the error, any worker failures, the evidence directory, and the
replay command. The classes are `server-failure`, `protocol-failure`,
`deadline`, `recovery-failure`, `coverage-manifest`, `worker-failure`,
`run-deadline`, and `harness-error`, a bug in the harness. When the run
deadline passes, the current case's evidence is saved too, and `run.json` is
marked incomplete. Replay the record to reproduce the failure alone, and read
`transcript.jsonl` from the end: the last requests sent without replies are
the ones that a deadline names.

### Runtime and limits

On the Linux development machine, excluding compilation, the default run
takes about 2 seconds, 1,000 cases about 21 seconds, and 10 stress cases 4 to
6 seconds; a request makes the analysis that it waits for urgent, so the
debounce adds nothing. These are local measurements, not a CI budget, and
macOS has not been timed.

The harness checks liveness, recovery, and lawful replies, not that answers
are correct for mutated source; there is no coverage guidance, and no
comparison of fresh and warm servers. It uses one client capability set, full
synchronization only, one project, and no cancellation, saves, closes,
configuration changes, or refresh requests. The pinned parser rejects `\r`,
so a file with CR line ends never parses. In the default run, about a quarter
of the mutated steps parse, and few semantic requests get populated answers.

`test_source_mutations.py` checks the harness itself, runs in `make test`, and
needs the server only for its replay, known-trap, and stalled-worker tests:

```sh
python3 test/protocol/test_source_mutations.py
```
