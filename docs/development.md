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
build/virgil-lsp --stdio                  # LSP transport (not implemented until M1)
```

## Tests

| Location | What | How it runs |
| --- | --- | --- |
| `test/unit/` | Virgil unit tests using Virgil's `lib/test` (`UnitTests.register`) | `build/unit-tests [glob]` |
| `test/cli/run.sh` | Command-line behaviour, exit codes, stdout cleanliness | `test/cli/run.sh build/virgil-lsp` |
| `test/fixtures/` | Source files used by tests. Bytes are preserved exactly (`-text` in `.gitattributes`). | — |

Planned additions: `test/protocol/` (golden JSON-RPC transcripts), `test/features/`, and `test/e2e/` (headless Neovim and the VS Code Extension Host).

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
