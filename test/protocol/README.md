# Protocol tests

These tests drive `virgil-lsp --stdio` over its real pipes. Every receive has a
deadline, and failures print the decoded transcript and the server's stderr.

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
   (`git rev-parse --short HEAD`); the existing fixtures were captured at
   `025082d`.
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
   `<name>/client.json`, an array of the messages in order. The research
   scripts that produced the current captures (`capture_clients.py`,
   `capture_nvim.lua`, `capture_vscode.cjs`, and `stdio_proxy.py`) show the
   shape.
6. Normalize them, from the repository root:

   ```sh
   python3 test/protocol/clients/normalize.py CAPTURES_DIR
   ```

   This writes `test/protocol/clients/<name>/messages.json` and
   `fixture.json` for every capture directory. Add the new client's name,
   editor version, and client library to `CAPTURES` in `normalize.py` first.
   The normalizer replaces the captured project root with `/work/project`, a
   `virgilRoot` outside it with `/work/virgil`, and `processId` with a fixed
   number; it leaves every other member and every absent, `null`, `[]`, and
   `{}` distinction as sent, and stops on a machine path that no rule covers.
   `Content-Length` is written when the test frames the messages. It is not
   run by `make test`, so rerun it only to add or refresh a capture.
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
