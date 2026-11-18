# Your first Virgil project

This walkthrough takes you from an installed release to your first working semantic feature: a type error the server reports, its one-word fix, and a hover and a go-to-definition that show the server understands the program. The example needs no Virgil checkout and no bundled library.

## Prerequisites

- **VS Code:** install the platform VSIX for your workspace platform, as [Installing from a VSIX](../clients/vscode/README.md#installing-from-a-vsix) describes. The platform package includes the server, worker, and pinned library, so no separate server installation is needed.
- **LazyVim:** start from an existing [LazyVim starter installation](https://www.lazyvim.org/installation), install the [Virgil plugin configuration](../editors/nvim/README.md#lazyvim), and make sure `virgil-lsp` is on your `PATH`, as the [editor setup](../README.md#editor-setup) describes.

## The example project

Create a folder called `first-project` with these three files. `Main.v3` deliberately holds one type error. The example is also checked in at [`docs/examples/first-project/`](examples/first-project/); you can copy that folder instead of creating the files.

`.virgil-lsp.json`:

```json
{
  "version": 1,
  "projects": [
    {
      "name": "first-project",
      "sources": ["Greeter.v3", "Main.v3"]
    }
  ]
}
```

`Greeter.v3`:

```virgil
// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0
component Greeter {
	def message() -> string { return "hello, world"; }
}
```

`Main.v3`:

```virgil
// Copyright 2026 The Virgil Language Server Authors.
// SPDX-License-Identifier: Apache-2.0
component Main {
	def run() {
		var greeting: int = Greeter.message();
	}
}
```

## Open the folder and the file

- **VS Code:** open `first-project` as a folder, then open `Main.v3`. The extension runs only in [trusted workspaces](https://code.visualstudio.com/docs/editor/workspace-trust), so trust the folder if VS Code asks.
- **LazyVim:** start `nvim` in the folder, then open `Main.v3`.

## What to expect, and when

Syntax diagnostics appear for any open `.v3` file, with no project file needed. Semantic diagnostics, hover, and go-to-definition need the `.virgil-lsp.json` project file, and they arrive when the first analysis finishes, a moment after you open `Main.v3`.

1. **The type error.** `Main.v3` declares `greeting` as `int`, but `Greeter.message()` returns a `string`. The server publishes one semantic diagnostic, an error, on that line:

   > expected int in var initialization, got Array<byte>

   Virgil's `string` is an `Array<byte>`, which is what the compiler names it.

2. **The fix.** Change the one word `int` to `string`:

   ```virgil
   var greeting: string = Greeter.message();
   ```

   The diagnostic clears when the next analysis finishes.

3. **Hover.** Put the cursor on `message` in `Greeter.message()` — with the mouse in VS Code, or `K` in LazyVim. The hover shows the declaration's signature:

   ```virgil
   def message() -> Array<byte>
   ```

4. **Go to definition.** Jump from the same call to its declaration — `F12` in VS Code, `gd` in LazyVim. It lands on `message` in `Greeter.v3`:

   ```virgil
   def message() -> string { return "hello, world"; }
   ```

## The Virgil root and the bundled library

This project names no `virgilDependencies`, so it needs neither a [Virgil root](configuration.md#the-virgil-root) nor the bundled library. A project that uses Virgil's own libraries, such as `lib/util`, names them relative to a root, which release archives and development checkouts provide.

## Where to go next

- [Troubleshooting](troubleshooting.md): the server doesn't start, or a file gets no diagnostics or navigation.
- [Supported platforms and editors](compatibility.md#host-platforms).
- [Project configuration](configuration.md): the full `.virgil-lsp.json` format.
- [Known limitations](known-limitations.md): what the alpha doesn't do yet.
