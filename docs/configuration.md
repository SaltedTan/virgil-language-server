# Configuration

> **Draft.** The project file is specified in milestone M3. Nothing on this page is implemented yet, and the format may change before v0.1.

## Why a project file

Virgil compiles whole programs: all participating `.v3` files are passed together. A repository can contain several programs, test files with duplicate declarations, and several targets, so analyzing every `.v3` file in a workspace would be wrong. A project file states which files form each program.

## `.virgil-lsp.json`

Proposed shape:

```json
{
  "version": 1,
  "projects": [
    {
      "name": "app",
      "sources": ["src/**/*.v3"],
      "dependencies": ["vendor/lib/**/*.v3"],
      "exclude": ["build/**"],
      "compilerArgs": ["-fun-exprs"],
      "target": null
    }
  ]
}
```

Planned rules:

- Globs are resolved relative to the directory containing the config file.
- Open editor buffers override the file contents on disk.
- Unknown compiler flags produce a configuration diagnostic. They are never silently ignored.
- A file that belongs to more than one project gets one analysis context per project, with a deterministic active context.
- The server never downloads or executes code from the workspace.

The final fields will be chosen after studying real Virgil projects and their `DEPS`/`TARGETS` files.

## Without a project file

The server falls back to single-file mode: parsing, syntax diagnostics, and document symbols, plus a notice that semantic features are limited.
