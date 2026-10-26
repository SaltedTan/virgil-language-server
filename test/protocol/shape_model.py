#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Small LSP metaModel walker. No downloads, dependencies, or random globals."""

import copy
import hashlib
import json
from pathlib import Path

MODEL_PATH = Path(__file__).parent / "schema/3.17/metaModel.json"
MODEL_SHA256 = "c207890d9e0f54d8f9e462a924670f563423ec9af90a0a369645ff55384c8d6d"
URI = "untitled:protocol-shapes"
TEXT = "component C { def x = 1; }\n"
REQUESTS = (
    "initialize", "shutdown", "textDocument/completion",
    "textDocument/definition", "textDocument/typeDefinition", "textDocument/documentHighlight", "textDocument/documentSymbol",
    "textDocument/hover", "textDocument/implementation", "textDocument/inlayHint",
    "textDocument/prepareRename", "textDocument/references", "textDocument/rename",
    "textDocument/signatureHelp", "textDocument/prepareTypeHierarchy", "typeHierarchy/supertypes",
    "typeHierarchy/subtypes", "workspace/symbol",
)
NOTIFICATIONS = (
    "initialized", "exit", "textDocument/didOpen", "textDocument/didChange",
    "textDocument/didSave", "textDocument/didClose", "workspace/didChangeWatchedFiles",
    "workspace/didChangeWorkspaceFolders", "$/cancelRequest",
)
EXTENSIONS = ("virgil-lsp/analyze", "virgil-lsp/snapshot")


class Model:
    def __init__(self):
        data = MODEL_PATH.read_bytes()
        assert hashlib.sha256(data).hexdigest() == MODEL_SHA256, "unreviewed metaModel change"
        model = json.loads(data)
        assert model["metaData"]["version"] == "3.17.0"
        self.requests = {s["method"]: s.get("params") for s in model["requests"]}
        self.structures = {s["name"]: s for s in model["structures"]}
        self.aliases = {s["name"]: s["type"] for s in model["typeAliases"]}
        self.enums = {s["name"]: s for s in model["enumerations"]}
        entries = {s["method"]: s for s in model["requests"] + model["notifications"]}
        self.params = {m: entries[m].get("params") for m in REQUESTS + NOTIFICATIONS}
        # These development requests are not LSP. Their narrow local schemas
        # keep them in method coverage without pretending the model defines them.
        self.params["virgil-lsp/analyze"] = {
            "kind": "literal", "value": {"properties": [{"name": "uris", "type": {
                "kind": "array", "element": {"kind": "base", "name": "DocumentUri"}}}]}}
        self.params["virgil-lsp/snapshot"] = None

    def properties(self, structure):
        result = {}
        for parent in structure.get("extends", []) + structure.get("mixins", []):
            for prop in self.properties(self.structures[parent["name"]]):
                result[prop["name"]] = prop
        for prop in structure.get("properties", []):
            result[prop["name"]] = prop
        return list(result.values())

    def base(self, t, name="", trail=()):
        if t is None:
            return None
        kind = t["kind"]
        if kind == "reference":
            ref = t["name"]
            if ref in trail:
                raise ValueError("recursive required type: " + ref)
            if ref in self.structures:
                return self.object_base(self.properties(self.structures[ref]), trail + (ref,))
            if ref in self.enums:
                return self.enums[ref]["values"][0]["value"]
            return self.base(self.aliases[ref], name, trail + (ref,))
        if kind == "base":
            base = t["name"]
            if base == "null":
                return None
            if base == "boolean":
                return False
            if base in ("integer", "uinteger", "decimal"):
                return 2 if name == "version" else 0
            if base in ("URI", "DocumentUri"):
                return URI
            return {"uri": URI, "languageId": "virgil", "text": TEXT,
                    "newName": "renamed", "name": "shapes"}.get(name, "")
        if kind in ("stringLiteral", "integerLiteral", "booleanLiteral"):
            return t["value"]
        if kind == "or":
            return self.base(t["items"][0], name, trail)
        if kind == "and":
            result = {}
            for item in t["items"]:
                result.update(self.base(item, name, trail))
            return result
        if kind == "array":
            return []
        if kind == "map":
            return {}
        if kind == "tuple":
            return [self.base(item, trail=trail) for item in t["items"]]
        if kind == "literal":
            return self.object_base(t["value"]["properties"], trail)
        raise ValueError(t)

    def object_base(self, properties, trail):
        return {p["name"]: self.base(p["type"], p["name"], trail)
                for p in properties if not p.get("optional")}

    def valid(self, t, value, depth=0):
        if t is None:
            return value is None
        if depth > 60:
            raise ValueError("validation recursion budget")
        kind = t["kind"]
        recur = lambda typ, val: self.valid(typ, val, depth + 1)
        if kind == "reference":
            name = t["name"]
            if name in self.structures:
                return self.object_valid(self.properties(self.structures[name]), value, depth)
            if name in self.enums:
                enum = self.enums[name]
                return recur(enum["type"], value) and (
                    enum.get("supportsCustomValues", False)
                    or any(type(value) is type(v["value"]) and value == v["value"] for v in enum["values"]))
            return recur(self.aliases[name], value)
        if kind == "base":
            name = t["name"]
            if name == "null":
                return value is None
            if name == "boolean":
                return type(value) is bool
            if name in ("integer", "uinteger"):
                return type(value) is int and (-2147483648 if name == "integer" else 0) <= value <= 2147483647
            if name == "decimal":
                return type(value) in (int, float)
            return type(value) is str
        if kind in ("stringLiteral", "integerLiteral", "booleanLiteral"):
            return type(value) is type(t["value"]) and value == t["value"]
        if kind == "or":
            return any(recur(item, value) for item in t["items"])
        if kind == "and":
            return all(recur(item, value) for item in t["items"])
        if kind == "array":
            return type(value) is list and all(recur(t["element"], item) for item in value)
        if kind == "map":
            return type(value) is dict and all(recur(t["key"], key) and recur(t["value"], val)
                                              for key, val in value.items())
        if kind == "tuple":
            return type(value) is list and len(value) == len(t["items"]) and all(
                recur(typ, val) for typ, val in zip(t["items"], value))
        if kind == "literal":
            return self.object_valid(t["value"]["properties"], value, depth)
        raise ValueError(t)

    def object_valid(self, properties, value, depth):
        return type(value) is dict and all(
            (p["name"] not in value and p.get("optional", False))
            or (p["name"] in value and self.valid(p["type"], value[p["name"]], depth + 1))
            for p in properties)

    def object_variants(self, properties, trail):
        minimal = self.object_base(properties, trail)
        # Populate direct optional siblings, then omit each in turn. Descendant
        # calls construct required ancestors, so nested omissions are exercised.
        populated = {p["name"]: self.base(p["type"], p["name"], trail) for p in properties}
        for prop in properties:
            key = prop["name"]
            if prop.get("optional"):
                omitted = copy.deepcopy(populated)
                del omitted[key]
                yield key + ".omit", omitted
            for label, item in self.variants(prop["type"], key, trail):
                value = copy.deepcopy(minimal)
                value[key] = item
                yield key + "." + label, value

    def variants(self, t, name="", trail=()):
        """One leaf/branch at a time, never an unbounded cross product.

        Recursive LSPAny retains scalars, {} and [] but stops repeated references.
        Integer mutations may be schema-invalid; the caller labels validity.
        """
        yield "minimal", self.base(t, name)
        if t is None:
            return
        kind = t["kind"]
        if kind == "reference":
            ref = t["name"]
            if ref in trail:
                return
            if ref in self.structures:
                yield "empty-object", {}
                yield from self.object_variants(self.properties(self.structures[ref]), trail + (ref,))
            elif ref in self.enums:
                for entry in self.enums[ref]["values"]:
                    yield "enum-" + str(entry["value"]), entry["value"]
                for label, value in self.variants(self.enums[ref]["type"], name, trail + (ref,)):
                    yield "enum-base." + label, value
            else:
                yield from self.variants(self.aliases[ref], name, trail + (ref,))
        elif kind == "literal":
            yield "empty-object", {}
            yield from self.object_variants(t["value"]["properties"], trail)
        elif kind in ("or", "and"):
            for i, item in enumerate(t["items"]):
                for label, value in self.variants(item, name, trail):
                    if kind == "and":
                        value = dict(self.base(t, name), **value)
                    yield "branch%d.%s" % (i, label), value
        elif kind == "array":
            for label, value in self.variants(t["element"], name, trail):
                yield "item." + label, [value]
        elif kind == "map":
            key = self.base(t["key"], "key")
            for label, value in self.variants(t["value"], name, trail):
                yield "entry." + label, {key: value}
        elif kind == "tuple":
            for i, item in enumerate(t["items"]):
                for label, value in self.variants(item, name, trail):
                    values = self.base(t, name)
                    values[i] = value
                    yield "item%d.%s" % (i, label), values
        elif kind == "base":
            base = t["name"]
            if base in ("integer", "uinteger", "decimal"):
                for value in (0, -1, 2147483647, 2147483648):
                    yield "number-" + str(value), value
                if base == "decimal":
                    yield "decimal", 0.5
            elif base == "boolean":
                yield "true", True
            elif base in ("string", "URI", "DocumentUri"):
                for label, value in (("empty", ""), ("unicode", "é😀"), ("long", "x" * 1024)):
                    yield "string-" + label, value


def unsupported_number(value):
    if type(value) is float or (type(value) is int and not -2147483648 <= value <= 2147483647):
        return True
    if isinstance(value, dict):
        return any(unsupported_number(v) for v in value.values())
    if isinstance(value, list):
        return any(unsupported_number(v) for v in value)
    return False


def session_validity(method, params):
    """Schema validity alone says nothing about these extra session constraints."""
    if unsupported_number(params):
        return False, "documented 32-bit JSON number limitation"
    if not isinstance(params, dict):
        return True, "no additional negotiated constraint"
    if method == "textDocument/didChange":
        if any("range" in c or "rangeLength" in c for c in params.get("contentChanges", [])):
            return False, "ranged change under negotiated full synchronization"
        if params.get("textDocument", {}).get("version", 1) <= 1:
            return False, "version must increase beyond the open version 1"
    if method in ("textDocument/completion", "textDocument/signatureHelp") and "context" in params:
        return False, "trigger context support was not negotiated"
    if method == "textDocument/inlayHint":
        span = params.get("range", {})
        start, end = span.get("start", {}), span.get("end", {})
        if all(k in start and k in end for k in ("line", "character")) and (start["line"], start["character"]) > (end["line"], end["character"]):
            return False, "range end precedes start"
    if method == "virgil-lsp/analyze":
        return False, "analysis requires nonempty local-file URIs; generated URIs are untitled"
    if method == "textDocument/rename":
        return False, "versioned workspace edits were not negotiated"
    if method in ("typeHierarchy/supertypes", "typeHierarchy/subtypes"):
        return False, "an item's data must be the server's own, from prepareTypeHierarchy"
    doc = params.get("textDocument", {})
    if "uri" in doc and not doc["uri"]:
        return False, "empty document URI"
    if method != "textDocument/didOpen" and (doc.get("uri", URI) != URI or params.get("uri", URI) != URI):
        return False, "URI differs from the session's open overlay"
    return True, "full-sync session with overlay version 1"


def generated_cases(model):
    for method, schema in model.params.items():
        # shutdown/exit are exercised with null/absent params as session epilogues.
        if method in ("shutdown", "exit"):
            continue
        seen = set()
        for label, params in model.variants(schema):
            # Preserve different omission paths even if their JSON is identical.
            key = (label, json.dumps(params, sort_keys=True, ensure_ascii=False))
            if key in seen:
                continue
            seen.add(key)
            digest = hashlib.sha256((label + "\n" + key[1]).encode()).hexdigest()[:12]
            valid = model.valid(schema, params)
            session_valid, reason = session_validity(method, params)
            yield {"id": method.replace("/", "-").replace("$", "cancel") + "-" + digest,
                   "method": method, "label": label, "params": params,
                   "schema_valid": valid, "session_valid": valid and session_valid,
                   "lifecycle_valid": True, "reason": reason if valid else "schema boundary mutation"}
