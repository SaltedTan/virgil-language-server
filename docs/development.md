# Development guide

## Prerequisites

- Linux x86-64 (including WSL 2), or an Apple Silicon Mac with Rosetta 2 (`softwareupdate --install-rosetta --agree-to-license`)
- `git`, `make`, `bash`
- No separate Virgil install: the build uses the pinned submodule at `vendor/virgil`.

## Build and test

```sh
git submodule update --init --recursive   # if you cloned without --recurse-submodules
make            # builds build/virgil-lsp
make test       # builds and runs unit tests and command-line smoke tests
make clean
```

The build is a single Aeneas invocation (see `Makefile` and [architecture.md](architecture.md#build-model)). `scripts/v3c.sh` selects the host target and the pinned `bin/stable` compiler. It accepts these overrides:

| Variable | Purpose |
| --- | --- |
| `VIRGIL` | Use a different Virgil checkout, e.g. `make VIRGIL=~/src/virgil` |
| `VIRGIL_V3C` | Use a specific Aeneas binary, e.g. a bootstrapped `bin/current/x86-64-linux/Aeneas` |
| `V3C_TARGET` | Force a target such as `x86-64-darwin` |

## Running

```sh
build/virgil-lsp --version
build/virgil-lsp parse path/to/file.v3    # development command: syntax diagnostics
build/virgil-lsp analyze a.v3 b.v3        # development command: parse and verify as one program
build/virgil-lsp --stdio                  # LSP over stdio
```

`analyze` runs the compiler spike: the files are parsed and verified together, from in-memory copies, and semantic diagnostics are printed. `--bindings` also prints each use and the declaration it resolves to. `--stats` prints timing and compiler global-state counters to stderr. `--repeat=<n>` runs the analysis n times in one process and fails if any run differs. For example:

```sh
build/virgil-lsp analyze --bindings test/fixtures/analysis/two-file/*.v3
scripts/bench-analysis.sh        # parse and verify timings: small fixture and the Aeneas sources
```

## Tests

| Location | What | How it runs |
| --- | --- | --- |
| `test/unit/` | Virgil unit tests using Virgil's `lib/test` (`UnitTests.register`) | `build/unit-tests [glob]` |
| `test/cli/run.sh` | Command-line behaviour, exit codes, stdout cleanliness | `test/cli/run.sh build/virgil-lsp` |
| `test/protocol/` | Golden transcripts: `--stdio` input bytes, expected output bytes and exit status | `test/protocol/run.sh build/virgil-lsp [case...]` |
| `test/fixtures/` | Source files used by tests. Bytes are preserved exactly (`-text` in `.gitattributes`). | — |

Each directory in `test/protocol/` is one transcript: `input` (or `input.1`, `input.2`, ... to split the input across reads), the expected `output` and `status`, and optionally `args` and `stderr`. The header of `test/protocol/run.sh` describes the format. The files are raw bytes with CR LF header lines (`-text` in `.gitattributes`), and `Content-Length` must count the payload's bytes exactly, so write them with a tool rather than an editor, for example:

```sh
frame() { printf 'Content-Length: %d\r\n\r\n%s' $(( $(printf %s "$1" | wc -c) )) "$1"; }
frame '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' > input
```

In `output`, write `@VERSION@` for the server version in the `initialize` result.

Planned additions: `test/features/` and `test/e2e/` (headless Neovim and the VS Code Extension Host).

To add a unit test, create `test/unit/<Thing>Test.v3` and register test functions:

```virgil
def T = UnitTests.register;
def X = [
	T("Thing:does_something", test_does_something),
	()
];

def test_does_something(t: Tester) {
	t.asserti(2, 1 + 1);
}
```

Run a subset with `build/unit-tests 'Thing:*'`.

## Learning the compiler front end

The [roadmap's learning path](../ROADMAP.md#learning-path-for-the-compiler-front-end) lists which parts of Aeneas to read, and in what order. Useful entry points in `vendor/virgil`:

- `aeneas/src/vst/Parser.v3` — `Parser.parseFile`, `parseToplevelDecl`
- `aeneas/src/vst/Vst.v3` — syntax tree, `VarBinding`, visitors
- `aeneas/src/vst/Verifier.v3` — name resolution and type checking
- `aeneas/src/main/Compiler.v3` — `Compilation.parse()` / `verify()`
- `aeneas/src/main/Error.v3` — `Error`, `ErrorGen`
- `apps/vctags/` — a small standalone tool built on the parser
