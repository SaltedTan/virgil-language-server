# LSP 3.17 metaModel

This is Microsoft's unchanged Language Server Protocol 3.17.0 metaModel.

| Field | Value |
| --- | --- |
| Upstream | https://github.com/microsoft/language-server-protocol |
| Source | https://github.com/microsoft/language-server-protocol/blob/baf2db31b5fb314a8f358f9a839d3c66e9d98734/_specifications/lsp/3.17/metaModel/metaModel.json |
| Commit | `baf2db31b5fb314a8f358f9a839d3c66e9d98734` |
| SHA-256 | `c207890d9e0f54d8f9e462a924670f563423ec9af90a0a369645ff55384c8d6d` |
| License | Creative Commons Attribution 4.0 International, [upstream license](https://github.com/microsoft/language-server-protocol/blob/baf2db31b5fb314a8f358f9a839d3c66e9d98734/License.txt), copied as [License.txt](License.txt) |
| Attribution | Microsoft, Language Server Protocol contributors |
| Modifications | None |

The specification copy is the canonical source here. We compared it with
`microsoft/vscode-languageserver-node` tag `release/protocol/3.17.5`'s
`protocol/metaModel.json` (MIT): both report 3.17.0; their differences are
in documentation strings, including glob semantics. The specification's
CC BY 4.0 license allows redistribution with attribution; it remains under
that license, separately from this repository's Apache-2.0 code.

Updates require a separate reviewed change. Select an immutable upstream
commit that still describes LSP 3.17, download the file without rewriting
it, inspect the structural and documentation diff and upstream license,
preserve its license and attribution, and update this table,
`THIRD_PARTY_NOTICES.md`, and `MODEL_SHA256` in
[shape_model.py](../../shape_model.py). Run the default target and a wider
budget that covers the new corpus using the [protocol README](../../README.md).
Tests never fetch the model or license, and must not follow a branch or a 3.18
model.
