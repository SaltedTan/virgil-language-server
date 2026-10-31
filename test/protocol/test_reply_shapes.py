#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The reply-shape validator on hand-written JSON, independent of the generator."""
import json
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reply_shapes  # noqa: E402
from reply_shapes import ReplyValidator, ShapeError, UnsupportedModelPath  # noqa: E402
import shapes  # noqa: E402
from shape_model import Model  # noqa: E402

MODEL = Model()
URI = "file:///work/Main.v3"


def pos(line, character):
    return {"line": line, "character": character}


def span(a=0, b=0, c=0, d=1):
    return {"start": pos(a, b), "end": pos(c, d)}


SYMBOL_INFORMATION = {"name": "C", "kind": 5, "location": {"uri": URI, "range": span()}}
DOCUMENT_SYMBOL = {"name": "C", "kind": 5, "range": span(), "selectionRange": span(),
                   "children": [{"name": "x", "kind": 8, "range": span(), "selectionRange": span()}]}
DIAGNOSTIC = {"range": span(), "message": "invalid start of expression",
              "severity": 1, "code": "ParseError", "source": "aeneas"}
ITEM = {"label": "a", "kind": 6, "textEdit": {"newText": "a", "range": span()}}
INSERT_REPLACE = {"label": "foo", "textEdit": {"newText": "foo", "insert": span(0, 4, 0, 4), "replace": span(0, 4, 0, 6)}}
CAPABILITIES = {"textDocumentSync": {"openClose": True, "change": 1}, "hoverProvider": True,
                "completionProvider": {"triggerCharacters": ["."]}, "documentSymbolProvider": True,
                "workspaceSymbolProvider": True,
                "semanticTokensProvider": {"legend": {"tokenTypes": ["type"], "tokenModifiers": []}, "full": True}}


class Rejections(unittest.TestCase):
    def setUp(self):
        self.validator = ReplyValidator(MODEL)

    def rejects(self, method, value, path, *fragments, params=False):
        check = self.validator.params if params else self.validator.result
        with self.assertRaises(ShapeError) as caught:
            check(method, value)
        message = str(caught.exception)
        role = " params " if params else " result "
        self.assertTrue(message.startswith(method + role + path + ": "), message)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    def test_missing_required_member(self):
        self.rejects("textDocument/hover", {"range": span()}, "$", "Hover",
                     'missing required property "contents"')
        self.rejects("textDocument/completion", [{"kind": 6}], "$[0]", "CompletionItem",
                     'missing required property "label"')
        self.rejects("initialize", {"serverInfo": {"name": "virgil-lsp"}}, "$", "InitializeResult",
                     'missing required property "capabilities"')
        self.rejects("textDocument/documentSymbol", [{"name": "C", "kind": 5, "location": {"uri": URI}}],
                     "$[0].location", "Location", 'missing required property "range"')

    def test_wrong_primitive(self):
        self.rejects("textDocument/documentSymbol", [dict(SYMBOL_INFORMATION, name=5)], "$[0].name",
                     "SymbolInformation.name", "expected string, got number 5")
        self.rejects("textDocument/completion", {"isIncomplete": "no", "items": []}, "$.isIncomplete",
                     "CompletionList.isIncomplete", "expected boolean")

    def test_booleans_are_not_integers_and_numbers_keep_their_domains(self):
        for line, got in ((True, "boolean true"), (-1, "number -1"), (2 ** 31, "number 2147483648"),
                          (1.5, "number 1.5"), (1.0, "number 1.0")):
            value = [{"name": "C", "kind": 5, "location": {"uri": URI, "range": span(line)}}]
            self.rejects("textDocument/documentSymbol", value, "$[0].location.range.start.line", "Position.line",
                         "uinteger in [0, 2^31-1], got " + got, "Location.range > Range.start")
        self.rejects("textDocument/completion", [dict(ITEM, kind=True)], "$[0].kind",
                     "CompletionItemKind", "got boolean true")
        self.rejects("initialize", {"capabilities": {"textDocumentSync": True}},
                     "$.capabilities.textDocumentSync", "TextDocumentSyncKind", "got boolean true")

    def test_wrong_array_member(self):
        self.rejects("textDocument/completion", {"isIncomplete": False, "items": [ITEM, "b"]},
                     "$.items[1]", "CompletionItem", "expected object, got string")
        self.rejects("textDocument/documentSymbol", [DOCUMENT_SYMBOL, SYMBOL_INFORMATION], "$",
                     "SymbolInformation at $[0]", "DocumentSymbol at $[1]", 'missing required property "range"')
        self.rejects("textDocument/completion", [dict(ITEM, tags=[1, 2])], "$[0].tags[1]",
                     "CompletionItemTag", "expected one of 1, got number 2")

    def test_malformed_insert_replace_edit_range(self):
        edit = dict(INSERT_REPLACE["textEdit"], insert={"start": pos(0, 4), "end": {"line": 0}})
        self.rejects("textDocument/completion", {"isIncomplete": False, "items": [dict(INSERT_REPLACE, textEdit=edit)]},
                     "$.items[0].textEdit.insert.end", "Position", 'missing required property "character"',
                     "CompletionItem.textEdit > InsertReplaceEdit.insert > Range.end")
        edit = dict(INSERT_REPLACE["textEdit"], replace=[pos(0, 4), pos(0, 6)])
        self.rejects("textDocument/completion", [dict(INSERT_REPLACE, textEdit=edit)],
                     "$[0].textEdit.replace", "Range", "expected object, got array of 2",
                     "InsertReplaceEdit.replace")

    def test_invalid_markup_kind(self):
        self.rejects("textDocument/hover", {"contents": {"kind": "html", "value": "x"}}, "$.contents.kind",
                     "MarkupKind", 'expected one of "plaintext", "markdown", got string "html"')
        self.rejects("textDocument/completion", [dict(ITEM, documentation={"kind": "Markdown", "value": "x"})],
                     "$[0].documentation.kind", "MarkupKind")

    def test_ill_typed_diagnostic(self):
        method = "textDocument/publishDiagnostics"
        for change, path, fragment in (
                ({"message": 3}, "$.diagnostics[0].message", "Diagnostic.message"),
                ({"severity": 0}, "$.diagnostics[0].severity", "DiagnosticSeverity"),
                ({"severity": "error"}, "$.diagnostics[0].severity", "DiagnosticSeverity"),
                ({"range": {"start": pos(0, 0)}}, "$.diagnostics[0].range", 'missing required property "end"'),
                ({"tags": [3]}, "$.diagnostics[0].tags[0]", "DiagnosticTag"),
                ({"relatedInformation": [{"message": "m"}]}, "$.diagnostics[0].relatedInformation[0]",
                 'missing required property "location"')):
            self.rejects(method, {"uri": URI, "diagnostics": [dict(DIAGNOSTIC, **change)]}, path, fragment,
                         params=True)
        message = self.rejects(method, {"uri": URI, "diagnostics": [dict(DIAGNOSTIC, code=True)]},
                               "$.diagnostics[0].code", "Diagnostic.code", params=True)
        self.assertIn("expected integer in [-2^31, 2^31-1], got boolean true", message)
        self.assertIn("expected string, got boolean true", message)
        self.rejects(method, {"uri": URI, "diagnostics": [DIAGNOSTIC], "version": 2.5}, "$.version",
                     "PublishDiagnosticsParams.version", params=True)
        self.rejects(method, {"diagnostics": []}, "$", 'missing required property "uri"', params=True)

    def test_unsupported_model_paths_fail_explicitly(self):
        with self.assertRaisesRegex(UnsupportedModelPath, r"^unsupported model path: value \$: and: type kind 'and'$"):
            self.validator.check({"kind": "and", "items": []}, {})
        with self.assertRaisesRegex(UnsupportedModelPath, r"value \$: NoSuchType: no structure"):
            self.validator.check("NoSuchType", {})
        with self.assertRaisesRegex(UnsupportedModelPath, r"textDocument/didOpen result \$: .*no result type"):
            self.validator.result("textDocument/didOpen", None)
        with self.assertRaisesRegex(UnsupportedModelPath, r"textDocument/hover params \$: .*not a model notification"):
            self.validator.params("textDocument/hover", {})

    def test_reference_cycles_fail_explicitly(self):
        model = Model()
        model.aliases["CycleA"] = {"kind": "or", "items": [{"kind": "reference", "name": "CycleB"}]}
        model.aliases["CycleB"] = {"kind": "reference", "name": "CycleA"}
        with self.assertRaisesRegex(UnsupportedModelPath, r"value \$: CycleA: reference cycle CycleA -> CycleB -> CycleA"):
            ReplyValidator(model).check("CycleA", 1)
        model.structures["Loop"] = {"name": "Loop", "extends": [{"kind": "reference", "name": "Loop"}], "properties": []}
        with self.assertRaisesRegex(UnsupportedModelPath, r"Loop: structure base that is missing or inherits from itself"):
            ReplyValidator(model).check("Loop", {})

    def test_depth_and_work_are_bounded(self):
        symbol = {"name": "leaf", "kind": 12, "range": span(), "selectionRange": span()}
        for _ in range(40):
            symbol = {"name": "n", "kind": 12, "range": span(), "selectionRange": span(), "children": [symbol]}
        with self.assertRaisesRegex(UnsupportedModelPath,
                                    r"^unsupported model path: textDocument/documentSymbol result "
                                    r"\$\[0\](\.children\[0\]){31}\.range: Range: JSON nesting deeper than 64$"):
            self.validator.result("textDocument/documentSymbol", [symbol])
        with mock.patch.object(reply_shapes, "MAX_WORK", 50):
            with self.assertRaisesRegex(UnsupportedModelPath,
                                        r"workspace/symbol result \$\[\d+\]\S*: .*more than 50 type checks"):
                self.validator.result("workspace/symbol", [SYMBOL_INFORMATION] * 20)
        started = time.monotonic()
        with self.assertRaisesRegex(UnsupportedModelPath, "more than 500000 type checks"):
            self.validator.check({"kind": "array", "element": {"kind": "reference", "name": "LSPAny"}}, [0] * 600000)
        self.assertLess(time.monotonic() - started, 30)

    def test_open_values_keep_the_bounds_and_finite_numbers(self):
        nested = 0
        for _ in range(100):
            nested = [nested]
        with self.assertRaisesRegex(UnsupportedModelPath,
                                    r"^unsupported model path: textDocument/completion result "
                                    r"\$\[0\]\.data(\[0\]){62}: LSPAny: JSON nesting deeper than 64$"):
            self.validator.result("textDocument/completion", [dict(ITEM, data=nested)])
        with self.assertRaisesRegex(UnsupportedModelPath, r"\$\[0\]\.data\[\d+\]: LSPAny: more than 500000 type checks"):
            self.validator.result("textDocument/completion", [dict(ITEM, data=[0] * 600000)])
        for bad in (float("inf"), float("nan")):
            self.rejects("textDocument/completion", [dict(ITEM, data={"x": [bad]})], "$[0].data.x[0]",
                         "LSPAny: expected a finite number")
            self.rejects("textDocument/publishDiagnostics", {"uri": URI, "diagnostics": [dict(DIAGNOSTIC, data=bad)]},
                         "$.diagnostics[0].data", "LSPAny: expected a finite number", params=True)


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.validator = ReplyValidator(MODEL)

    def test_null_and_empty_alternatives(self):
        for method in ("textDocument/hover", "textDocument/completion",
                       "textDocument/documentSymbol", "workspace/symbol"):
            self.validator.result(method, None)
        for method in ("textDocument/completion", "textDocument/documentSymbol", "workspace/symbol"):
            self.validator.result(method, [])
        self.validator.result("textDocument/completion", {"isIncomplete": False, "items": []})
        self.validator.params("textDocument/publishDiagnostics", {"uri": URI, "diagnostics": []})
        self.validator.result("initialize", {"capabilities": {}})

    def test_union_alternatives(self):
        hover = self.validator.result
        hover("textDocument/hover", {"contents": {"kind": "markdown", "value": "```virgil\ndef foo\n```"}, "range": span()})
        hover("textDocument/hover", {"contents": "plain MarkedString"})
        hover("textDocument/hover", {"contents": {"language": "virgil", "value": "def foo"}})
        hover("textDocument/hover", {"contents": ["a", {"language": "virgil", "value": "b"}]})
        self.validator.result("textDocument/completion", [ITEM, INSERT_REPLACE])
        self.validator.result("textDocument/completion", {"isIncomplete": True, "items": [ITEM]})
        self.validator.result("textDocument/documentSymbol", [SYMBOL_INFORMATION])
        self.validator.result("textDocument/documentSymbol", [DOCUMENT_SYMBOL])
        self.validator.result("workspace/symbol", [SYMBOL_INFORMATION])
        self.validator.result("workspace/symbol", [{"name": "C", "kind": 5, "location": {"uri": URI}}])
        self.validator.params("textDocument/publishDiagnostics", {"uri": URI, "version": 3, "diagnostics": [
            DIAGNOSTIC, dict(DIAGNOSTIC, code=7, tags=[1, 2], codeDescription={"href": "https://example.com"},
                             relatedInformation=[{"location": {"uri": URI, "range": span()}, "message": "m"}])]})

    def test_capability_alternatives(self):
        for capabilities in (
                CAPABILITIES,
                dict(CAPABILITIES, textDocumentSync=1, hoverProvider={"workDoneProgress": False},
                     documentSymbolProvider={"label": "Virgil"}, workspaceSymbolProvider={"resolveProvider": False}),
                dict(CAPABILITIES, semanticTokensProvider={"legend": {"tokenTypes": [], "tokenModifiers": []},
                                                           "full": {"delta": False}, "range": True}),
                dict(CAPABILITIES, positionEncoding="utf-32-custom-value")):
            self.validator.result("initialize", {"capabilities": capabilities,
                                                 "serverInfo": {"name": "virgil-lsp", "version": "0.1.0"}})
        self.validator.result("textDocument/completion", [dict(ITEM, insertTextFormat=2, insertTextMode=1)])

    def test_extensible_data_and_lawful_extra_properties(self):
        data = {"nested": [1, -1, 0.5, "x", None, True, {"deep": []}]}
        self.validator.result("textDocument/completion", [dict(ITEM, data=data)])
        self.validator.result("textDocument/completion", [dict(ITEM, data="any JSON value")])
        self.validator.params("textDocument/publishDiagnostics",
                              {"uri": URI, "diagnostics": [dict(DIAGNOSTIC, data=data)]})
        self.validator.result("initialize", {"capabilities": dict(CAPABILITIES, experimental=data)})
        # Structures are open: a later protocol's property is neither an error nor checked.
        self.validator.result("textDocument/completion", {"isIncomplete": False, "items": [ITEM], "applyKind": {}})
        self.validator.result("textDocument/hover", {"contents": "x", "futureProperty": [1]})
        # The documented cost: an unchecked extra cannot rule out the { uri } alternative.
        self.validator.result("workspace/symbol", [{"name": "C", "kind": 5, "location": {"uri": URI, "range": 1}}])

    def test_lsp_any_containers_keep_their_json_kind(self):
        self.validator.check("LSPObject", {"a": [1]})
        self.validator.check("LSPArray", [{"a": 1}])
        with self.assertRaisesRegex(ShapeError, r"^value \$: LSPObject: expected object, got array of 1$"):
            self.validator.check("LSPObject", [1])
        with self.assertRaisesRegex(ShapeError, r"^value \$: LSPArray: expected array, got object$"):
            self.validator.check("LSPArray", {})

    def test_model_kinds_beyond_the_bounded_results(self):
        check = self.validator.check
        check("ParameterInformation", {"label": [0, 3]})
        check("ParameterInformation", {"label": "a: int"})
        with self.assertRaisesRegex(ShapeError, r"^value \$\.label: ParameterInformation.label: no alternative"):
            check("ParameterInformation", {"label": [0]})
        for label in ([0, True], [0, -1]):
            with self.assertRaisesRegex(ShapeError, r"^value \$\.label\[1\]: ParameterInformation.label: expected uinteger"):
                check("ParameterInformation", {"label": label})
        check("WorkspaceEdit", {"changes": {URI: [{"range": span(), "newText": "x"}]}})
        with self.assertRaisesRegex(ShapeError, r'^value \$\.changes\["file:///work/Main.v3"\]\[0\]: TextEdit: '
                                                r'missing required property "newText" \(in WorkspaceEdit.changes\)$'):
            check("WorkspaceEdit", {"changes": {URI: [{"range": span()}]}})
        check("CreateFile", {"kind": "create", "uri": URI})
        with self.assertRaisesRegex(ShapeError, r'^value \$\.kind: CreateFile.kind: expected "create", got string "rename"$'):
            check("CreateFile", {"kind": "rename", "uri": URI})
        check("Color", {"red": 0.5, "green": 1, "blue": 0, "alpha": 1.0})
        for bad in (True, float("nan"), "1"):
            with self.assertRaisesRegex(ShapeError, r"^value \$\.red: Color.red: expected finite decimal"):
                check("Color", {"red": bad, "green": 1, "blue": 0, "alpha": 1})
        check({"kind": "integerLiteral", "value": 1}, 1)
        check({"kind": "booleanLiteral", "value": True}, True)
        with self.assertRaises(ShapeError):
            check({"kind": "integerLiteral", "value": 1}, True)
        with self.assertRaises(ShapeError):
            check({"kind": "booleanLiteral", "value": True}, 1)


class Integration(unittest.TestCase):
    def test_types_come_from_the_model_entries(self):
        for method in reply_shapes.RESULTS:
            self.assertIn("result", MODEL.entries[method], method)
        for method in reply_shapes.NOTIFICATIONS:
            self.assertEqual(MODEL.entries[method]["messageDirection"], "serverToClient")
        model = Model()
        model.entries["textDocument/hover"] = dict(model.entries["textDocument/hover"],
                                                   result={"kind": "base", "name": "string"})
        ReplyValidator(model).result("textDocument/hover", "now a string")
        with self.assertRaises(ShapeError):
            ReplyValidator(model).result("textDocument/hover", None)

    def session(self, expected, *messages):
        s = shapes.ShapeSession.__new__(shapes.ShapeSession)
        s.buffer = b"".join(shapes.frame(m) for m in messages)
        s.transcript, s.answered, s.frames = [], set(), []
        s.expected = expected
        s.replies = shapes.Replies(MODEL)
        return s

    def test_session_checks_successes_but_never_error_replies(self):
        deadline = time.monotonic() + 1
        s = self.session({(int, 1): "textDocument/hover", (int, 2): "textDocument/hover"},
                         {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad", "data": 3}},
                         {"jsonrpc": "2.0", "id": 2, "result": {"contents": {"kind": "html", "value": ""}}})
        self.assertEqual(s.receive(deadline)["error"]["code"], -32602)
        self.assertFalse(s.replies.seen)
        with self.assertRaisesRegex(ShapeError, r"^textDocument/hover result \$\.contents\.kind: MarkupKind"):
            s.receive(deadline)

    def test_session_checks_diagnostics_params_and_counts_kinds(self):
        deadline = time.monotonic() + 1
        good = {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                "params": {"uri": URI, "diagnostics": [DIAGNOSTIC]}}
        bad = {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
               "params": {"uri": URI, "diagnostics": [dict(DIAGNOSTIC, range=None)]}}
        s = self.session({(str, "w"): "workspace/symbol"}, good,
                         {"jsonrpc": "2.0", "id": "w", "result": []}, bad)
        s.receive(deadline)
        s.receive(deadline)
        self.assertEqual(dict(s.replies.seen), {("textDocument/publishDiagnostics", "populated"): 1,
                                                ("workspace/symbol", "empty"): 1})
        self.assertIn("textDocument/hover populated", s.replies.missing())
        with self.assertRaisesRegex(ShapeError, r"^textDocument/publishDiagnostics params \$\.diagnostics\[0\]\.range: "):
            s.receive(deadline)

    def test_workspace_symbols_must_be_the_symbol_information_the_server_sends(self):
        replies = shapes.Replies(MODEL)
        replies.result("workspace/symbol", [SYMBOL_INFORMATION])
        replies.result("workspace/symbol", [])
        replies.result("workspace/symbol", None)
        # The model's union alone accepts both, through WorkspaceSymbol's { uri } location.
        prefix = r"^workspace/symbol result \(SymbolInformation\[\], as the server sends\) \$\[0\]\.location"
        with self.assertRaisesRegex(ShapeError, prefix + r'\.range: Range: expected object, got number 1'):
            replies.result("workspace/symbol", [{"name": "C", "kind": 5, "location": {"uri": URI, "range": 1}}])
        with self.assertRaisesRegex(ShapeError, prefix + r': Location: missing required property "range"'):
            replies.result("workspace/symbol", [{"name": "C", "kind": 5, "location": {"uri": URI}}])

    def test_reply_kinds(self):
        self.assertEqual([shapes.reply_kind(v) for v in (None, [], {"isIncomplete": False, "items": []},
                                                         {"uri": URI, "diagnostics": []}, [ITEM],
                                                         {"capabilities": {}}, {"contents": "x"})],
                         ["null", "empty", "empty", "empty", "populated", "populated", "populated"])


if __name__ == "__main__":
    unittest.main()
