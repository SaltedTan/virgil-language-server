# VS Code extension

> **Not started.** The development client is part of milestone M2. See [ROADMAP.md](../../ROADMAP.md#visual-studio-code).

This will be a thin TypeScript extension built on `vscode-languageclient/node`. It will:

- contribute the `virgil` language ID for `.v3` files, with comment/bracket configuration and a TextMate grammar;
- launch `virgil-lsp --stdio`, from the `virgil.server.path` setting during development;
- expose `virgil.server.path`, `virgil.project.config`, and trace/log settings;
- report startup and configuration errors in a "Virgil Language Server" output channel.

It will be written from the official VS Code API documentation. Code from repositories without an explicit license must not be copied.
