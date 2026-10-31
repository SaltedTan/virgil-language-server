#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Bounded checks of successful LSP replies against the pinned 3.17 model.

Each result type, and each server notification's params type, is read from the
model's request or notification entry, never from a copy here. Structures are
open: a property the model does not name is allowed and not checked, as the
protocol README explains. LSPAny, LSPObject and LSPArray accept any value of
their JSON kind. Anything outside the supported model kinds or bounds raises
UnsupportedModelPath instead of passing unchecked.
"""

import json
import math

from shape_model import Model

# The bounded set that shapes.py checks on every successful reply.
RESULTS = ("initialize", "textDocument/hover", "textDocument/completion",
           "textDocument/documentSymbol", "workspace/symbol")
NOTIFICATIONS = ("textDocument/publishDiagnostics",)
OPEN = {"LSPAny": None, "LSPObject": dict, "LSPArray": list}
LITERALS = ("stringLiteral", "integerLiteral", "booleanLiteral")
MAX_DEPTH = 64      # JSON nesting below the checked value
MAX_WORK = 500000   # type checks per value, including failed union alternatives
INT_MIN, INT_MAX = -2 ** 31, 2 ** 31 - 1


# A path is a tuple of (segment, label) pairs: an array index or property name,
# and the "Structure.property" a property belongs to, for failure messages.
def path_text(path):
    return "$" + "".join("[%d]" % seg if type(seg) is int else "." + seg if seg.isidentifier()
                         else "[%s]" % json.dumps(seg, ensure_ascii=False) for seg, _ in path)


def show(value):
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean " + json.dumps(value)
    if type(value) in (int, float):
        return "number " + json.dumps(value)
    if type(value) is str:
        text = json.dumps(value, ensure_ascii=False)
        return "string " + (text if len(text) <= 40 else text[:37] + '..."')
    if type(value) is list:
        return "array of %d" % len(value)
    return "object"


def describe(t):
    kind = t["kind"]
    if kind in ("reference", "base"):
        return t["name"]
    if kind in LITERALS:
        return json.dumps(t["value"])
    if kind == "array":
        inner = describe(t["element"])
        return ("(%s)[]" if t["element"]["kind"] == "or" else "%s[]") % inner
    if kind == "or":
        return " | ".join(describe(item) for item in t["items"])
    if kind == "tuple":
        return "[" + ", ".join(describe(item) for item in t["items"]) + "]"
    if kind == "map":
        return "{[%s]: %s}" % (describe(t["key"]), describe(t["value"]))
    return kind


class ShapeError(AssertionError):
    """A value that the model's type rejects."""

    def __init__(self, where, path, owner, detail):
        trail = [label for _, label in path if label]
        if trail and trail[-1] == owner:
            trail = trail[:-1]
        context = " (in %s)" % " > ".join(trail) if trail else ""
        message = "%s %s: %s: %s%s" % (where, path_text(path), owner, detail, context)
        super().__init__(message if len(message) <= 1000 else message[:997] + "...")
        self.path, self.owner, self.detail = path, owner, detail


class UnsupportedModelPath(Exception):
    """A model type or bound this validator does not handle; never a pass."""

    def __init__(self, where, path, owner, reason):
        super().__init__("unsupported model path: %s %s: %s: %s" % (where, path_text(path), owner, reason))


class _Run:
    """One value's check: where it came from, and its work count."""

    def __init__(self, where):
        self.where = where
        self.work = 0

    def error(self, path, owner, detail):
        return ShapeError(self.where, path, owner, detail)

    def unsupported(self, path, owner, reason):
        return UnsupportedModelPath(self.where, path, owner, reason)


class ReplyValidator:
    def __init__(self, model=None):
        self.model = model or Model()
        self.structure_members = {}

    def result(self, method, value):
        """Check a successful request result, typed by the model's request entry."""
        entry = self.model.entries.get(method)
        if entry is None or "result" not in entry:
            raise UnsupportedModelPath(method + " result", (), method, "the model has no result type for this method")
        self.check(entry["result"], value, method + " result")

    def params(self, method, value):
        """Check a notification's params, typed by the model's notification entry."""
        entry = self.model.entries.get(method)
        if entry is None or "result" in entry or not isinstance(entry.get("params"), dict):
            raise UnsupportedModelPath(method + " params", (), method,
                                       "not a model notification with a single params type")
        self.check(entry["params"], value, method + " params")

    def check(self, t, value, where="value"):
        """Raise ShapeError unless value has model type t, a type node or a type name."""
        if isinstance(t, str):
            t = {"kind": "reference", "name": t}
        self.value(t, value, (), (), "", _Run(where))

    def value(self, t, v, path, refs, owner, run):
        run.work += 1
        if run.work > MAX_WORK:
            raise run.unsupported(path, owner or describe(t), "more than %d type checks" % MAX_WORK)
        kind = t["kind"]
        if kind == "reference":
            self.reference(t["name"], v, path, refs, run)
        elif kind == "base":
            self.base(t["name"], v, path, owner, run)
        elif kind in LITERALS:
            if type(v) is not type(t["value"]) or v != t["value"]:
                raise run.error(path, owner or describe(t), "expected %s, got %s" % (json.dumps(t["value"]), show(v)))
        elif kind == "or":
            self.union(t, v, path, refs, owner, run)
        elif kind == "array":
            if type(v) is not list:
                raise run.error(path, owner or describe(t), "expected %s, got %s" % (describe(t), show(v)))
            for i, item in enumerate(v):
                self.value(t["element"], item, self.child(path, i, None, owner, run), (), owner, run)
        elif kind == "tuple":
            if type(v) is not list or len(v) != len(t["items"]):
                raise run.error(path, owner or describe(t), "expected %s, got %s" % (describe(t), show(v)))
            for i, (item_type, item) in enumerate(zip(t["items"], v)):
                self.value(item_type, item, self.child(path, i, None, owner, run), (), owner, run)
        elif kind == "map":
            if type(v) is not dict:
                raise run.error(path, owner or describe(t), "expected %s, got %s" % (describe(t), show(v)))
            for key, item in v.items():
                at = self.child(path, key, None, owner, run)
                self.value(t["key"], key, at, (), owner, run)
                self.value(t["value"], item, at, (), owner, run)
        elif kind == "literal":
            self.properties(owner or "literal", t["value"]["properties"], v, path, run)
        else:
            # The bounded result set reaches no intersection ("and") type.
            raise run.unsupported(path, owner or describe(t), "type kind %r" % kind)

    def child(self, path, segment, label, owner, run):
        if len(path) >= MAX_DEPTH:
            raise run.unsupported(path, owner or "value", "JSON nesting deeper than %d" % MAX_DEPTH)
        return path + ((segment, label),)

    def reference(self, name, v, path, refs, run):
        # References followed without descending into the JSON value can only
        # repeat through a cycle, which would otherwise never terminate.
        if name in refs:
            raise run.unsupported(path, name, "reference cycle " + " -> ".join(refs + (name,)))
        refs += (name,)
        model = self.model
        if name in OPEN:
            kind = OPEN[name]
            if kind is not None and type(v) is not kind:
                raise run.error(path, name, "expected %s, got %s" % ("object" if kind is dict else "array", show(v)))
        elif name in model.structures:
            self.properties(name, self.members(name, path, run), v, path, run)
        elif name in model.enums:
            enum = model.enums[name]
            self.value(enum["type"], v, path, refs, name, run)
            values = [e["value"] for e in enum["values"]]
            if not enum.get("supportsCustomValues") and not any(type(v) is type(e) and v == e for e in values):
                listed = ", ".join(json.dumps(e) for e in values[:8]) + (", ..." if len(values) > 8 else "")
                raise run.error(path, name, "expected one of %s, got %s" % (listed, show(v)))
        elif name in model.aliases:
            self.value(model.aliases[name], v, path, refs, name, run)
        else:
            raise run.unsupported(path, name, "no structure, enumeration, or type alias of this name")

    def members(self, name, path, run, chain=()):
        """A structure's properties with its extends and mixins, the structure's own last."""
        if name in self.structure_members:
            return self.structure_members[name]
        if name in chain or name not in self.model.structures:
            raise run.unsupported(path, name, "structure base that is missing or inherits from itself")
        structure = self.model.structures[name]
        result = {}
        for parent in structure.get("extends", []) + structure.get("mixins", []):
            if parent["kind"] != "reference":
                raise run.unsupported(path, name, "structure base of kind %r" % parent["kind"])
            for prop in self.members(parent["name"], path, run, chain + (name,)):
                result[prop["name"]] = prop
        for prop in structure.get("properties", []):
            result[prop["name"]] = prop
        self.structure_members[name] = list(result.values())
        return self.structure_members[name]

    def properties(self, owner, members, v, path, run):
        if type(v) is not dict:
            raise run.error(path, owner, "expected object, got " + show(v))
        for prop in members:
            if not prop.get("optional") and prop["name"] not in v:
                raise run.error(path, owner, "missing required property " + json.dumps(prop["name"]))
        # Properties the model does not name are allowed and not checked.
        for prop in members:
            key = prop["name"]
            if key in v:
                label = owner + "." + key
                self.value(prop["type"], v[key], self.child(path, key, label, owner, run), (), label, run)

    def base(self, name, v, path, owner, run):
        if name == "null":
            ok = v is None
        elif name == "boolean":
            ok = type(v) is bool
        elif name in ("string", "URI", "DocumentUri"):
            ok = type(v) is str
        elif name == "integer":
            ok = type(v) is int and INT_MIN <= v <= INT_MAX
            name = "integer in [-2^31, 2^31-1]"
        elif name == "uinteger":
            ok = type(v) is int and 0 <= v <= INT_MAX
            name = "uinteger in [0, 2^31-1]"
        elif name == "decimal":
            ok = type(v) in (int, float) and math.isfinite(v)
            name = "finite decimal"
        else:
            raise run.unsupported(path, owner or name, "base type %r" % name)
        if not ok:
            raise run.error(path, owner or name, "expected %s, got %s" % (name, show(v)))

    def union(self, t, v, path, refs, owner, run):
        errors = []
        for item in t["items"]:
            try:
                self.value(item, v, path, refs, owner, run)
                return
            except ShapeError as exc:
                errors.append(exc)
        # Report the one alternative that got furthest into the value, or all of them.
        deepest = max(len(e.path) for e in errors)
        furthest = [e for e in errors if len(e.path) == deepest]
        if deepest > len(path) and len(furthest) == 1:
            raise furthest[0]
        name = describe(t)
        raise run.error(path, owner or name, "no alternative%s matched %s: %s" % (
            " of " + name if owner else "", show(v),
            "; ".join("%s at %s: %s" % (e.owner, path_text(e.path), e.detail) for e in errors)))
