#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Seeded source mutations over worker.Session's pipes: liveness and worker recovery.

Each case starts its own server on a temporary project, opens a clean observer
and the files of one reviewed repair seed, applies bounded token-aware UTF-8
edits in one to three steps, and sends every advertised request kind with
params from the current text. It then restores clean sources, checks that the
project analyzes again, and shuts the server down. The oracles, bounds, replay
format, and known compiler traps are described in test/protocol/README.md.
"""

import argparse
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import reply_shapes
from reply_shapes import ReplyValidator, ShapeError, UnsupportedModelPath
from shape_model import MODEL_SHA256
from worker import Session

ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / "test/fixtures/repair/cases"
FORMAT = 1
KIB, MIB = 1024, 1024 * 1024
DEFAULT_SEED = 20261028
ROOT_TOKEN = "${ROOT}"
WORKER_TIMEOUT_MS = 5000

# Each limit's default and hard cap, then the same with --stress. Deadlines are
# seconds and are the same in both modes. With --stress, source_bytes bounds the
# aggregate document charge (DocumentLimits: text plus 256 bytes of metadata
# plus the URI and language ID, the URI estimated at MAX_URI) instead of the
# total source text.
LIMITS = {
    "cases": (100, 1000, 10, 10),
    "source_bytes": (32 * KIB, 64 * KIB, MIB, MIB),
    "requests": (24, 64, 24, 64),
    "request_timeout": (10, 30, 10, 30),
    "case_timeout": (30, 120, 30, 120),
    "run_timeout": (180, 900, 180, 900),
    "artifact_bytes": (64 * MIB, 256 * MIB, 64 * MIB, 256 * MIB),
}
DEADLINES = ("request_timeout", "case_timeout", "run_timeout")
RECORD_BYTES = 256
MAX_URI = 1024
LANGUAGE_ID = "virgil"

OBSERVER_NAME = "Observer.v3"
OBSERVER_LINES = (
    "class MutationBase {",
    "\tdef size() -> int { return 0; }",
    "}",
    "class MutationLeaf extends MutationBase {",
    "\tdef size() -> int { return 1; }",
    "}",
    "component MutationObserver {",
    "\tdef stable(x: int) -> int { return x; }",
    "\tdef check() -> int { return stable(1) + MutationLeaf.new().size(); }",
    "}",
)
OBSERVER = "\n".join(OBSERVER_LINES) + "\n"


def observer_position(line, name):
    return {"line": line, "character": OBSERVER_LINES[line].index(name)}


# The observer's use of stable(), the declaration it resolves to, and the class
# whose type hierarchy the restoration oracle asks for.
OBSERVER_USE = observer_position(8, "stable")
OBSERVER_DECLARATION = {"start": observer_position(7, "stable"),
                        "end": {"line": 7, "character": observer_position(7, "stable")["character"] + 6}}
OBSERVER_LEAF = observer_position(3, "MutationLeaf")
CONFIG = json.dumps({"version": 1, "projects": [{"name": "mutations", "sources": ["*.v3"]}]}) + "\n"
CLIENT_CAPABILITIES = {
    "workspace": {"workspaceEdit": {"documentChanges": True}},
    "textDocument": {
        "rename": {"prepareSupport": True},
        "codeAction": {"codeActionLiteralSupport": {"codeActionKind": {"valueSet": ["quickfix"]}}},
        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
    },
}


def restored(index):
    return "component MutationRestored%d {\n\tdef value() -> int { return %d; }\n}\n" % (index, index)


# The coverage manifest: every request method the server may advertise, in
# round-robin order, then the hierarchy follow-ups, which take items that the
# prepare requests returned. A method advertised but not listed here, or listed
# but not advertised, fails the run, so that a new capability cannot go
# unexercised.
MAIN_METHODS = (
    "textDocument/hover", "textDocument/definition", "textDocument/typeDefinition",
    "textDocument/implementation", "textDocument/references", "textDocument/documentHighlight",
    "textDocument/completion", "textDocument/signatureHelp", "textDocument/prepareRename",
    "textDocument/rename", "textDocument/documentSymbol", "textDocument/foldingRange",
    "textDocument/selectionRange", "textDocument/semanticTokens/full", "textDocument/inlayHint",
    "textDocument/codeAction", "textDocument/prepareTypeHierarchy",
    "textDocument/prepareCallHierarchy", "workspace/symbol",
)
FOLLOW_UPS = {
    "textDocument/prepareTypeHierarchy": ("typeHierarchy/supertypes", "typeHierarchy/subtypes"),
    "textDocument/prepareCallHierarchy": ("callHierarchy/incomingCalls", "callHierarchy/outgoingCalls"),
}
MANIFEST = MAIN_METHODS + tuple(m for ms in FOLLOW_UPS.values() for m in ms)
FOLLOW_UP_RESERVE = 4


def _resolve(value, method):
    return (method,) if isinstance(value, dict) and value.get("resolveProvider") else ()


def _semantic(value):
    full, methods = value.get("full"), []
    if full:
        methods.append("textDocument/semanticTokens/full")
    if isinstance(full, dict) and full.get("delta"):
        methods.append("textDocument/semanticTokens/full/delta")
    if value.get("range"):
        methods.append("textDocument/semanticTokens/range")
    return tuple(methods)


def _sync(value):
    return ("textDocument/willSaveWaitUntil",) if isinstance(value, dict) and value.get("willSaveWaitUntil") else ()


# Each server capability and the request methods it advertises.
CAPABILITIES = {
    "callHierarchyProvider": lambda v: FOLLOW_UPS["textDocument/prepareCallHierarchy"] + ("textDocument/prepareCallHierarchy",),
    "codeActionProvider": lambda v: ("textDocument/codeAction",) + _resolve(v, "codeAction/resolve"),
    "completionProvider": lambda v: ("textDocument/completion",) + _resolve(v, "completionItem/resolve"),
    "definitionProvider": lambda v: ("textDocument/definition",),
    "documentHighlightProvider": lambda v: ("textDocument/documentHighlight",),
    "documentSymbolProvider": lambda v: ("textDocument/documentSymbol",),
    "foldingRangeProvider": lambda v: ("textDocument/foldingRange",),
    "hoverProvider": lambda v: ("textDocument/hover",),
    "implementationProvider": lambda v: ("textDocument/implementation",),
    "inlayHintProvider": lambda v: ("textDocument/inlayHint",) + _resolve(v, "inlayHint/resolve"),
    "referencesProvider": lambda v: ("textDocument/references",),
    "renameProvider": lambda v: ("textDocument/rename",) + (
        ("textDocument/prepareRename",) if isinstance(v, dict) and v.get("prepareProvider") else ()),
    "selectionRangeProvider": lambda v: ("textDocument/selectionRange",),
    "semanticTokensProvider": _semantic,
    "signatureHelpProvider": lambda v: ("textDocument/signatureHelp",),
    "textDocumentSync": _sync,
    "typeDefinitionProvider": lambda v: ("textDocument/typeDefinition",),
    "typeHierarchyProvider": lambda v: FOLLOW_UPS["textDocument/prepareTypeHierarchy"] + ("textDocument/prepareTypeHierarchy",),
    # Workspace folders are notifications; any other member is unknown here.
    "workspace": lambda v: tuple("workspace." + k for k in sorted(v) if k != "workspaceFolders"),
    "workspaceSymbolProvider": lambda v: ("workspace/symbol",) + _resolve(v, "workspaceSymbol/resolve"),
}


def coverage_drift(capabilities):
    """The differences between the advertised request methods and MANIFEST."""
    advertised, problems = set(), []
    for name, value in sorted(capabilities.items()):
        if value is None or value is False:
            continue
        if name not in CAPABILITIES:
            problems.append("capability %s is not in the coverage manifest" % name)
            continue
        advertised.update(CAPABILITIES[name](value))
    problems += ["%s is advertised but not in the coverage manifest" % m for m in sorted(advertised - set(MANIFEST))]
    problems += ["%s is in the coverage manifest but not advertised" % m for m in sorted(set(MANIFEST) - advertised)]
    return problems


# The error codes each method documents (docs/architecture.md): RequestFailed
# for bounded replies and rename refusals, ContentModified for semantic tokens.
# Valid params never get InvalidParams, and no request is cancelled.
REQUEST_FAILED, CONTENT_MODIFIED = -32803, -32801
DOCUMENTED_ERRORS = {
    "textDocument/rename": {REQUEST_FAILED},
    "textDocument/prepareRename": {REQUEST_FAILED},
    "textDocument/documentSymbol": {REQUEST_FAILED},
    "textDocument/references": {REQUEST_FAILED},
    "textDocument/documentHighlight": {REQUEST_FAILED},
    "textDocument/implementation": {REQUEST_FAILED},
    "typeHierarchy/supertypes": {REQUEST_FAILED},
    "typeHierarchy/subtypes": {REQUEST_FAILED},
    "callHierarchy/incomingCalls": {REQUEST_FAILED},
    "callHierarchy/outgoingCalls": {REQUEST_FAILED},
    "textDocument/semanticTokens/full": {CONTENT_MODIFIED},
}

# The documented window/showMessage for a failed automatic analysis
# (LspSemanticDiagnostics.failed), with the supervisor's failure reasons and
# the worker's exit status (AnalysisSupervisor.describe).
WORKER_FAILURE_MESSAGE = re.compile(
    r"Semantic diagnostics for project mutations could not be updated: (?P<reason>the analysis worker "
    r"(?:crashed|timed out|sent a malformed result)|no analysis worker could be started|the program is too "
    r"large to analyze: result exceeds the allocation budget of \d+ bytes; reduce the program's source set) "
    r"\([^()\n]*\)\. The last published diagnostics are kept; see the server log\.")
FAILED_ANALYSIS = "virgil-lsp: warning: automatic analysis of project mutations failed: "
DEATH = re.compile(r"^virgil-lsp: warning: analysis worker (?P<pid>\d+) (?P<what>.+?); it ended with "
                   r"(?P<status>.+?)(?:; snapshot (?P<kept>\d+) is kept)?$", re.M)
TRAP = re.compile(r"^(?P<trap>![A-Za-z]+)\n(?P<frames>(?:\tin [^\n]*\n)*)", re.M)
FRAME = re.compile(r"^\tin (?P<function>\S+) \[(?P<file>.+?) @ (?P<line>\d+):(?P<column>\d+)\]$", re.M)


def _duplicate_types(text):
    names = re.findall(r"\btype\s+([A-Za-z_]\w*)", text)
    return len(names) != len(set(names))


def _nesting(text):
    depth = deepest = 0
    for c in text:
        if c in "([{":
            depth += 1
            deepest = max(deepest, depth)
        elif c in ")]}":
            depth = max(0, depth - 1)
    return deepest


# The shortest run that counts as a deep nest or a long chain: far beyond the
# deepest real code (docs/aeneas-crash-paths.md finds at most about 360), and
# below the thousands at which the verifier's recursion overflows.
DEEP = 1000
CRASHED = "crashed during an analysis"

# Contained worker failures that the documentation records, each by a narrow
# signature: the supervisor's log text, the trap and its innermost frame, and a
# condition that some text the case sent must meet. A worker's stack overflow
# prints no frames. Any other worker failure fails the case as a worker failure.
KNOWN_WORKER_FAILURES = (
    {"known": "crash-path row 1", "what": CRASHED, "trap": "!NullCheckException",
     "function": "TypeChecker.TryRepresentationChangeOfLiteral()", "file": "aeneas/src/vst/Verifier.v3",
     "source": lambda t: re.search(r"\b[0-9]+i\b", t)},
    {"known": "crash-path row 1", "what": CRASHED, "trap": "!NullCheckException",
     "function": "MethodEnv.lookupInfix()", "file": "aeneas/src/vst/MethodEnv.v3",
     "source": lambda t: re.search(r"\b[0-9]+i\b", t)},
    {"known": "crash-path row 2", "what": CRASHED, "trap": "!NullCheckException",
     "function": "Verifier.typeCheckEnumCase()", "file": "aeneas/src/vst/Verifier.v3",
     "source": lambda t: re.search(r"\benum\b[^{}]*\{[^{}]*\(", t)},
    {"known": "crash-path row 7", "what": CRASHED, "trap": "!BoundsCheckException",
     "function": "MethodEnv.lookupVariantCaseMember()", "file": "aeneas/src/vst/MethodEnv.v3",
     "source": _duplicate_types},
    {"known": "crash-path row 17", "what": CRASHED, "trap": "!NullCheckException",
     "function": "SourceCodeError.TypeExprError()", "file": "aeneas/src/main/Error.v3",
     "source": lambda t: re.search(r"\.set\b", t)},
    {"known": "crash-path row 13", "what": CRASHED, "trap": "!StackOverflow", "function": None,
     "source": lambda t: _nesting(t) >= DEEP},
    {"known": "crash-path row 25", "what": CRASHED, "trap": "!StackOverflow", "function": None,
     "source": lambda t: re.search(r"(?:(?:\.|[-+*/])\s*\w+\s*){%d}" % DEEP, t)},
    # ADR-0004's decoded result budget, which a long chain's analysis can exceed.
    {"known": "ADR-0004 result budget", "what": "sent a result exceeding the allocation budget and was replaced",
     "trap": None, "function": None, "source": lambda t: re.search(r"(?:(?:\.|[-+*/])\s*\w+\s*){%d}" % DEEP, t)},
)

PASSING = ("pass", "known-worker-trap")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def resolve_limits(requested, stress=False):
    """The limits, with defaults for those not requested; ValueError past a hard cap."""
    limits = {}
    for name, (default, cap, stress_default, stress_cap) in LIMITS.items():
        value = requested.get(name)
        top = stress_cap if stress else cap
        if value is None:
            value = stress_default if stress else default
        low = 0 if name in DEADLINES else 1
        if not low < value <= top if name in DEADLINES else not low <= value <= top:
            raise ValueError("--%s must be %s and at most %s%s" % (
                name.replace("_", "-"), "positive" if name in DEADLINES else "at least 1", top,
                " with --stress" if stress else ""))
        limits[name] = value
    if not limits["request_timeout"] <= limits["case_timeout"] <= limits["run_timeout"]:
        raise ValueError("deadlines must satisfy --request-timeout <= --case-timeout <= --run-timeout")
    smallest = 16 * KIB if stress else 4 * KIB
    if limits["source_bytes"] < smallest:
        raise ValueError("--source-bytes must be at least %d%s" % (smallest, " with --stress" if stress else ""))
    return limits


def load_seeds():
    """The pinned repair seeds in name order, and each input file's SHA-256.

    Each seed has two variants: the reviewed text without its cursor marker,
    and the same text without the marker's line, which mostly parses, so that
    mutations of it reach semantic analysis too."""
    seeds, inputs = [], {}
    for path in sorted(SEEDS.iterdir(), key=lambda p: p.name):
        if path.is_dir():
            paths = sorted(path.glob("*.v3"), key=lambda p: p.name)
        else:
            paths = [path] if path.suffix == ".v3" else []
        files, trimmed = {}, {}
        for p in paths:
            data = p.read_bytes()
            inputs[p.relative_to(ROOT).as_posix()] = sha256(data)
            name = p.name if path.is_dir() else "Mutant.v3"
            text = data.decode()
            files[name] = text.replace("<|>", "")
            trimmed[name] = "".join(line for line in text.splitlines(True) if "<|>" not in line)
        if files:
            seeds.append({"name": path.name.removesuffix(".v3"),
                          "variants": {"reviewed": files, "cursor line removed": trimmed}})
    return seeds, inputs


# ---------------------------------------------------------------------------
# The mutator: a lexer for token boundaries, not a grammar.

TOKEN = re.compile(r"""\r\n|\r|\n|[ \t]+|//[^\r\n]*|/\*.*?(?:\*/|\Z)|"(?:[^"\\\r\n]|\\.)*"?|'(?:[^'\\\r\n]|\\.)*'?"""
                   r"""|[0-9][0-9A-Za-z_]*(?:\.[0-9][0-9A-Za-z_]*)?|[A-Za-z_][A-Za-z0-9_]*"""
                   r"""|\.\.\.|\.\.\+|->|=>|==|!=|<=|>=|&&|\|\||<<|>>|\+\+|--|.""", re.S)
PUNCTUATION = ("{", "}", "(", ")", "[", "]", ";", ",", ".", ":", "=", "=>", "->", "<", ">", "+", "-",
               "*", "...", "..+", "#", "?", "!", "&&", "==")
# The keyword table of the pinned Aeneas parser (vst/Parser.v3).
KEYWORDS = ("break", "case", "class", "component", "continue", "def", "descriptor", "describes", "else",
            "enum", "export", "extends", "false", "for", "fun", "if", "import", "in", "layout", "match",
            "new", "null", "packing", "private", "return", "struct", "super", "thread", "true", "type",
            "var", "while")
MALFORMED = ("0i", "0x", "0b", "0b2", "1e", "1.", "0xFFFFFFFFFFFFFFFFFFFF", "99999999999999999999",
             "0u", "1i64x", "'ab'", "''", "'\\q'", '"\\q"', '"\\x"', "0o7", "1_", "0x_")
# Literals that the pinned parser accepts but whose values or suffixes are odd.
ODD_LITERALS = ("0i", "0u", "0i64", "0u8", "0i33", "0x0i", "1.", "1_", "0xFFFFFFFFu32", "0.0e400",
                "1e999", "2147483648", "-2147483649")
UNICODE = ("\u00e9", "\u4e2d", "\u00a0", "\U0001F600", "\U0001D518", "\U0010FFFF", "\u200b", "\ufeff")
BRACKETS = "{}()[]"
# Syntax damage, and edits that often keep the text parseable, so that
# semantic analysis and the worker run on it too; each half is chosen equally.
SYNTAX_FAMILIES = ("delete", "insert", "duplicate", "truncate", "replace", "literal", "bracket",
                   "comment", "string", "crlf", "unicode")
SEMANTIC_FAMILIES = ("odd-literal", "rename", "swap", "line")
FAMILIES = SYNTAX_FAMILIES + SEMANTIC_FAMILIES
STRESS_FAMILIES = ("deep", "dense", "chain")
LINE_END = re.compile(r"\r\n|\r|\n")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def kind(token):
    if token in ("\n", "\r\n", "\r"):
        return "newline"
    if token.isspace():
        return "space"
    if token.startswith(("//", "/*")):
        return "comment"
    if token[0] in "\"'":
        return "string"
    if token[0].isdigit():
        return "number"
    if token[0].isalpha() or token[0] == "_":
        return "identifier"
    return "punctuation"


def summary(text):
    """A manifest entry for inserted text: itself, or its length and hash."""
    if len(text) <= 64:
        return text
    return {"length": len(text), "sha256": sha256(text.encode()), "prefix": text[:32]}


def mutate(rng, text, family, room):
    """Apply one mutation of {family}; {room} bounds the UTF-8 bytes it may add."""
    tokens = TOKEN.findall(text)
    starts = [0]
    for token in tokens:
        starts.append(starts[-1] + len(token))
    assert starts[-1] == len(text)

    def pick(test=lambda t: kind(t) not in ("space", "newline")):
        found = [i for i, t in enumerate(tokens) if test(t)]
        return rng.choice(found) if found else None

    def spot():
        return starts[rng.randrange(len(tokens) + 1)]

    def edit(start, end, insert, detail):
        return text[:start] + insert + text[end:], {
            "family": family, "detail": detail, "offset": start, "removed": end - start, "inserted": summary(insert)}

    names = sorted({t for t in tokens if kind(t) == "identifier" and t not in KEYWORDS})
    pool = PUNCTUATION + KEYWORDS + tuple(names[:32])
    i = pick()
    if family == "delete" and i is not None:
        n = rng.randint(1, 3)
        return edit(starts[i], starts[min(i + n, len(tokens))], "", "tokens")
    if family == "duplicate" and i is not None:
        end = starts[min(i + rng.randint(1, 4), len(tokens))]
        return edit(end, end, text[starts[i]:end], "tokens")
    if family == "truncate":
        return edit(rng.randrange(len(text) + 1), len(text), "", "rest")
    if family == "replace" and i is not None:
        return edit(starts[i], starts[i + 1], rng.choice(pool), "token")
    if family == "literal":
        j = pick(lambda t: kind(t) in ("number", "string"))
        if j is not None and rng.random() < 0.7:
            return edit(starts[j], starts[j + 1], rng.choice(MALFORMED), "replace literal")
        at = spot()
        return edit(at, at, " " + rng.choice(MALFORMED) + " ", "insert literal")
    if family == "odd-literal":
        j = pick(lambda t: kind(t) in ("number", "string"))
        if j is not None:
            return edit(starts[j], starts[j + 1], rng.choice(ODD_LITERALS), "replace literal")
    if family == "bracket":
        j = pick(lambda t: t in BRACKETS)
        choice = rng.randrange(3)
        if j is not None and choice == 0:
            return edit(starts[j], starts[j + 1], "", "delete bracket")
        if j is not None and choice == 1:
            return edit(starts[j], starts[j], tokens[j], "duplicate bracket")
        at = spot()
        return edit(at, at, rng.choice(BRACKETS), "insert bracket")
    if family == "comment":
        j = pick(lambda t: kind(t) == "comment")
        if j is not None and rng.random() < 0.3:
            body = tokens[j][2:].removesuffix("*/")
            return edit(starts[j], starts[j + 1], "/*" + body, "unterminate comment")
        at = spot()
        return edit(at, at, rng.choice(("/*", "*/", "// ", "/* */", "/**/")), "insert comment")
    if family == "string":
        j = pick(lambda t: kind(t) == "string" and len(t) > 1)
        if j is not None and rng.random() < 0.4:
            return edit(starts[j + 1] - 1, starts[j + 1], "", "unterminate string")
        at = spot()
        return edit(at, at, rng.choice(('"', "'", '"\\', '"abc', "'\\", '"\\u{110000}"')), "insert quote")
    if family == "crlf":
        choice = rng.randrange(4)
        if choice == 0:
            return edit(0, len(text), text.replace("\r\n", "\n").replace("\n", "\r\n"), "all lines CRLF")
        if choice == 1:
            return edit(0, len(text), text.replace("\r\n", "\n").replace("\n", "\r"), "all lines CR")
        ends = [m.start() for m in LINE_END.finditer(text)]
        if choice == 2 and ends:
            at = rng.choice(ends)
            return edit(at, at + (2 if text.startswith("\r\n", at) else 1), "\r\n", "one line CRLF")
        at = spot()
        return edit(at, at, "\r", "lone CR")
    if family == "unicode":
        char = rng.choice(UNICODE)
        j = pick(lambda t: kind(t) in ("identifier", "comment", "string") and len(t) > 1)
        if j is not None and rng.random() < 0.5:
            at = starts[j] + rng.randrange(1, len(tokens[j]))
            return edit(at, at, char, "inside token")
        at = spot()
        return edit(at, at, char, "at boundary")
    if family == "rename" and names:
        old = rng.choice(names)
        new = rng.choice(names + ["renamed", "x", "Point", "int"])
        replaced = "".join(new if t == old else t for t in tokens)
        return edit(0, len(text), replaced, "every %s to %s" % (old, new))
    i = pick(lambda t: kind(t) in ("identifier", "number") and t not in KEYWORDS) if family == "swap" else i
    if family == "swap" and i is not None:
        same = [j for j, t in enumerate(tokens) if kind(t) == kind(tokens[i]) and t != tokens[i] and t not in KEYWORDS]
        if same:
            a, b = sorted((i, rng.choice(same)))
            swapped = text[:starts[a]] + tokens[b] + text[starts[a + 1]:starts[b]] + tokens[a] + text[starts[b + 1]:]
            return edit(0, len(text), swapped, "%s tokens %d and %d" % (kind(tokens[i]), a, b))
    if family == "line":
        lines = text.splitlines(True)
        if lines:
            n = rng.randrange(len(lines))
            start = sum(len(line) for line in lines[:n])
            end = start + len(lines[n])
            if rng.random() < 0.5:
                return edit(end, end, lines[n] if lines[n].endswith(("\n", "\r")) else "\n" + lines[n], "duplicate line %d" % n)
            return edit(start, end, "", "delete line %d" % n)
    if family in STRESS_FAMILIES:
        size = max(0, int(room * rng.choice((1 / 32, 1 / 8, 1 / 2, 1))) - 64)
        at = spot() if rng.random() < 0.5 else len(text)
        if family == "deep":
            opener, closer = rng.choice((("(", ")"), ("[", "]"), ("{", "}"), ("if (true) {", "}")))
            balanced = rng.random() < 0.5
            depth = size // (len(opener) + (len(closer) if balanced else 0))
            insert = opener * depth + ("0" + closer * depth if balanced else "")
            return edit(at, at, insert, "%s %s x%d" % ("balanced" if balanced else "open", opener, depth))
        if family == "dense":
            count = size // 16
            body = "".join("def m%d = %d;\n" % (n, n) for n in range(count))
            while len(body) > size and count:
                count = count * 9 // 10
                body = "".join("def m%d = %d;\n" % (n, n) for n in range(count))
            return edit(at, at, "\n" + body, "declarations x%d" % count)
        count = max(1, (size - 32) // 2)
        operator = rng.choice(("+1", ".a"))
        return edit(at, at, "\ndef chain = a%s;\n" % (operator * count), "chain %s x%d" % (operator, count))
    # The family found nothing to change, such as a literal in text without one.
    at = spot()
    return edit(at, at, rng.choice(pool), "token instead")


def measure(texts, stress):
    """The bytes a case's open documents count against --source-bytes."""
    if stress:
        return sum(RECORD_BYTES + MAX_URI + len(LANGUAGE_ID) + len(t.encode()) for t in texts)
    return sum(len(t.encode()) for t in texts)


# ---------------------------------------------------------------------------
# Request planning: positions and params from the current text.

def utf16(text):
    return len(text.encode("utf-16-le")) // 2


def position(text, offset):
    """The LSP position of a code point offset: \\n, \\r\\n and \\r end lines."""
    if 0 < offset < len(text) and text[offset - 1] == "\r" and text[offset] == "\n":
        offset -= 1
    line, start = 0, 0
    for m in LINE_END.finditer(text, 0, offset):
        line, start = line + 1, m.end()
    return {"line": line, "character": utf16(text[start:offset])}


def choose_offset(rng, text, identifiers, names_only=False):
    roll = 0 if names_only else rng.random()
    if identifiers and roll < 0.5:
        m = rng.choice(identifiers)
        return m[0] + rng.randrange(m[1] - m[0] + 1)
    if roll < 0.6:
        return len(text)
    return rng.randrange(len(text) + 1)


def choose_position(rng, text, identifiers, names_only=False):
    offset = choose_offset(rng, text, identifiers, names_only)
    p = position(text, offset)
    # Sometimes the middle of a surrogate pair, which maps to the character's start.
    if offset < len(text) and ord(text[offset]) > 0xFFFF and rng.random() < 0.5:
        p["character"] += 1
    return p


def request_params(rng, method, uri, text):
    spans = [(m.start(), m.end()) for m in IDENTIFIER.finditer(text)]
    doc = {"uri": uri}
    if method in ("textDocument/documentSymbol", "textDocument/foldingRange", "textDocument/semanticTokens/full"):
        return {"textDocument": doc}
    if method == "workspace/symbol":
        name = text[slice(*rng.choice(spans))] if spans else ""
        return {"query": rng.choice(("", name, name[:2], "Mutation"))}
    if method == "textDocument/selectionRange":
        return {"textDocument": doc, "positions": [choose_position(rng, text, spans) for _ in range(rng.randint(1, 3))]}
    if method in ("textDocument/inlayHint", "textDocument/codeAction"):
        a, b = sorted((choose_offset(rng, text, spans), choose_offset(rng, text, spans)))
        params = {"textDocument": doc, "range": {"start": position(text, a), "end": position(text, b)}}
        if method == "textDocument/codeAction":
            params["context"] = {"diagnostics": []}
        return params
    params = {"textDocument": doc, "position": choose_position(rng, text, spans, method in FOLLOW_UPS)}
    if method == "textDocument/references":
        params["context"] = {"includeDeclaration": rng.random() < 0.5}
    elif method == "textDocument/rename":
        params["newName"] = rng.choice(("renamed", "x", "Renamed2", "\u00e9t\u00e9", "", "1bad"))
    elif method == "textDocument/completion":
        params["context"] = rng.choice(({"triggerKind": 1}, {"triggerKind": 2, "triggerCharacter": "."}))
    return params


def plan_case(seed, index, seeds, limits, stress, cursor):
    """One case: its seed files, mutation steps, and requests. {cursor} is the
    round-robin position in MAIN_METHODS, shared by the run's cases."""
    rng = random.Random("%d:%d" % (seed, index))
    source = rng.choice(seeds)
    # Semantic cases keep mostly parseable text, so that analysis and the
    # worker run on it; the others damage syntax too.
    mode = rng.choice(("semantic", "syntax"))
    variant = "cursor line removed" if mode == "semantic" else rng.choice(sorted(source["variants"]))
    initial = source["variants"][variant]
    files = dict(initial)
    names = sorted(files)
    budget = limits["source_bytes"]
    reserve = min(FOLLOW_UP_RESERVE, limits["requests"] - 1)
    main = limits["requests"] - reserve
    total = rng.randint(max(1, main // 2), main)
    steps = []
    count = rng.choice((1, 1, 2, 3))
    shares = [total // count + (1 if k < total % count else 0) for k in range(count)]
    next_id = 2
    for k in range(count):
        mutations, changed = [], set()
        for m in range(rng.choice((1, 1, 2, 3))):
            if stress and k == 0 and m == 0:
                family = rng.choice(STRESS_FAMILIES)
            elif rng.random() < 1 / 16:
                family = rng.choice(STRESS_FAMILIES)
            else:
                family = rng.choice(SEMANTIC_FAMILIES if mode == "semantic" or rng.random() < 0.5 else SYNTAX_FAMILIES)
            name = rng.choice(names)
            for attempt in range(8):
                others = [files[n] for n in names if n != name] + [OBSERVER]
                room = budget - measure(others + [files[name]], stress)
                text, record = mutate(rng, files[name], family, room)
                if measure(others + [text], stress) <= budget:
                    break
                family = "truncate" if attempt >= 6 else rng.choice(FAMILIES)
            assert measure(others + [text], stress) <= budget, "seed files exceed --source-bytes"
            record["file"] = name
            files[name] = text
            changed.add(name)
            mutations.append(record)
        requests = []
        for _ in range(shares[k]):
            method = MAIN_METHODS[cursor[0] % len(MAIN_METHODS)]
            cursor[0] += 1
            target = OBSERVER_NAME if rng.random() < 0.125 else rng.choice(names)
            text = OBSERVER if target == OBSERVER_NAME else files[target]
            ident = next_id if rng.random() < 0.5 else "r%d" % next_id
            next_id += 1
            requests.append({"id": ident, "method": method,
                             "params": request_params(rng, method, ROOT_TOKEN + "/" + target, text)})
        steps.append({"mutations": mutations, "texts": {n: files[n] for n in sorted(changed)},
                      "bytes": measure([files[n] for n in names] + [OBSERVER], stress), "requests": requests})
    record = case_record("%s%d-%04d" % ("stress-" if stress else "", seed, index), {n: initial[n] for n in names},
                         steps, limits, stress, next_id)
    record.update(seed=seed, index=index, seed_source=source["name"], mode=mode, variant=variant)
    return record


def case_record(ident, files, steps, limits, stress=False, next_id=None):
    """A replayable case: the observer and {files} as opened, {steps} of full
    texts and requests, and clean restorations of {files}."""
    names = sorted(files)
    if next_id is None:
        next_id = 2 + sum(len(step["requests"]) for step in steps)
    return {
        "format": FORMAT, "id": ident, "stress": stress, "limits": dict(limits), "config": CONFIG,
        "files": {OBSERVER_NAME: OBSERVER, **{n: files[n] for n in names}},
        "initialize": {"processId": None, "rootUri": ROOT_TOKEN, "capabilities": CLIENT_CAPABILITIES},
        "steps": steps, "restore": {n: restored(i) for i, n in enumerate(names)}, "next_id": next_id,
    }


def plan_run(seed, limits, stress, seeds):
    cursor = [0]
    return [plan_case(seed, i, seeds, limits, stress, cursor) for i in range(limits["cases"])]


def manifests(seed, limits, stress, inputs, plan):
    """The mutation and request manifests; identical inputs give identical bytes."""
    header = {"format": FORMAT, "seed": seed, "stress": stress, "inputs": inputs,
              "harness_sha256": sha256(Path(__file__).read_bytes()),
              "limits": {k: limits[k] for k in ("cases", "source_bytes", "requests")}}
    mutations = dict(header, cases=[{
        "id": c["id"], "seed_source": c["seed_source"], "mode": c["mode"], "variant": c["variant"],
        "steps": [{"mutations": s["mutations"], "bytes": s["bytes"],
                   "sha256": {n: sha256(t.encode()) for n, t in s["texts"].items()}} for s in c["steps"]]} for c in plan])
    requests = dict(header, cases=[{"id": c["id"], "steps": [s["requests"] for s in c["steps"]]} for c in plan])
    return mutations, requests


def templated(value, root_uri, reverse=False):
    """{value} with the case's root URI replaced by ROOT_TOKEN, or the reverse."""
    old, new = (ROOT_TOKEN, root_uri) if reverse else (root_uri, ROOT_TOKEN)
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [templated(v, root_uri, reverse) for v in value]
    if isinstance(value, dict):
        return {k: templated(v, root_uri, reverse) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# One case over real pipes.

class CaseFailure(Exception):
    """A classified failure: server-failure, protocol-failure, deadline,
    recovery-failure, coverage-manifest, or run-deadline."""

    def __init__(self, classification, message):
        super().__init__(message)
        self.classification = classification


class Deadlines:
    def __init__(self, run, case, limits):
        self.run, self.case, self.limits = run, case, limits

    def bound(self, request):
        """The earliest deadline and its name; the run's wins a tie, then the case's."""
        deadline, _, which = min((self.run, 0, "run"), (self.case, 1, "case"), (request, 2, "request"))
        return deadline, which

    def expired(self, which, what):
        if which == "run":
            return CaseFailure("run-deadline", "the run deadline of %ss expired %s" % (self.limits["run_timeout"], what))
        if which == "case":
            return CaseFailure("deadline", "the case deadline of %ss expired %s" % (self.limits["case_timeout"], what))
        return CaseFailure("deadline", "no reply within the request deadline of %ss %s" % (self.limits["request_timeout"], what))


class MutationSession(Session):
    """Session's framing, select deadlines, process ownership and cleanup, with
    every read, write, and wait bounded by the request, case, and run deadlines.

    IDs match by type and value, so a reply with a changed ID type, or a reply
    to a notification, is an unexpected response. Successful replies of the
    bounded reply_shapes set, and diagnostics params, are checked against the
    pinned model as they arrive.
    """
    NOTIFICATIONS = ("textDocument/publishDiagnostics", "window/showMessage")

    def __init__(self, exe, deadlines, validator, *options):
        super().__init__(exe, *options)
        self.deadlines = deadlines
        self.validator = validator
        self.expected = {}
        self.replies = {}
        self.diagnostics = {}
        self.messages = []
        self.unchecked = []
        self.stamps = []
        self.started = time.monotonic()
        self.eof = False
        self.final_log = None

    def stamp(self):
        self.stamps.extend([round(time.monotonic() - self.started, 4)] * (len(self.transcript) - len(self.stamps)))

    def raw(self, data):
        out, fd = self.proc.stdout.fileno(), self.proc.stdin.fileno()
        view = memoryview(data)
        while view:
            limit, which = self.deadlines.bound(float("inf"))
            remaining = limit - time.monotonic()
            if remaining <= 0:
                raise self.deadlines.expired(which, "while writing to the server")
            readable, writable, _ = select.select([] if self.eof else [out], [fd], [], remaining)
            if readable:
                # Read while writing, so that a server writing replies cannot block us both.
                chunk = os.read(out, 65536)
                self.eof = not chunk
                self.buffer += chunk
            if writable:
                try:
                    view = view[os.write(fd, view[:4096]):]
                except OSError as exc:
                    raise CaseFailure("server-failure", "cannot write to the server (%s); exit status %s" % (
                        exc, self.proc.poll())) from None

    def send(self, method, params=None, request=True, id=None):
        ident = super().send(method, params, request=request, id=id)
        self.stamp()
        if request:
            key = (type(ident), ident)
            assert key not in self.expected, ("the harness reused an ID", key)
            self.expected[key] = method
        return ident

    def receive(self, deadline):
        limit, which = self.deadlines.bound(deadline)
        try:
            msg = super().receive(limit)
        except CaseFailure:
            raise
        except Exception as exc:
            if time.monotonic() >= limit - 0.01:
                raise self.deadlines.expired(which, "waiting for %s" % self.waiting_for()) from None
            if self.proc.poll() is not None or "EOF" in str(exc):
                raise CaseFailure("server-failure", "the server closed its output; exit status %s" % (
                    self.proc.poll(),)) from None
            raise CaseFailure("protocol-failure", "invalid message from the server: %r" % (exc,)) from None
        finally:
            self.stamp()
        try:
            self.check(msg)
        except (ShapeError, AssertionError) as exc:
            raise CaseFailure("protocol-failure", str(exc)) from None
        return msg

    def check(self, msg):
        if "method" in msg and "id" in msg:
            raise CaseFailure("protocol-failure", "unexpected server request (no client capability asks for it): %s" % (
                json.dumps(msg)[:400],))
        if "id" in msg:
            key = (type(msg["id"]), msg["id"])
            if key not in self.expected:
                raise CaseFailure("protocol-failure", "reply to no outstanding request: a changed ID type, or a reply "
                                  "to a notification: %s" % json.dumps(msg)[:400])
            method = self.expected[key]
            if ("result" in msg) == ("error" in msg):
                raise CaseFailure("protocol-failure", "invalid reply envelope: %s" % json.dumps(msg)[:400])
            if "error" in msg:
                code = msg["error"].get("code") if isinstance(msg["error"], dict) else None
                if type(code) is not int or code not in DOCUMENTED_ERRORS.get(method, set()):
                    raise CaseFailure("protocol-failure", "undocumented error for %s: %s" % (method, json.dumps(msg)[:400]))
            elif method in reply_shapes.RESULTS:
                self.shape(lambda: self.validator.result(method, msg["result"]))
            self.replies[key] = msg
            return
        if msg["method"] == "textDocument/publishDiagnostics":
            self.shape(lambda: self.validator.params(msg["method"], msg.get("params")))
            self.diagnostics[msg["params"]["uri"]] = msg["params"]["diagnostics"]
        else:
            params = msg.get("params") or {}
            if params.get("type") != 2 or not WORKER_FAILURE_MESSAGE.fullmatch(str(params.get("message"))):
                raise CaseFailure("protocol-failure", "undocumented window/showMessage: %s" % json.dumps(msg)[:400])
            self.messages.append(params["message"])

    def shape(self, check):
        try:
            check()
        except UnsupportedModelPath as exc:
            # A validator bound, such as a very large outline; recorded, not a pass.
            self.unchecked.append(str(exc)[:400])

    def waiting_for(self):
        missing = [(m, k[1]) for k, m in self.expected.items() if k not in self.replies]
        return ", ".join("%s id %s" % (m, json.dumps(i)) for m, i in missing[:4]) or "a message"

    def collect(self, keys):
        """Wait for a reply to each request; each has its own deadline from when it was sent."""
        pending = {k: d for k, d in keys.items() if k not in self.replies}
        while pending:
            key = min(pending, key=pending.get)
            self.receive(pending[key])
            for k in [k for k in pending if k in self.replies]:
                del pending[k]
        return [self.replies[k] for k in keys]

    def request(self, method, params, ident):
        self.send(method, params, id=ident)
        return (type(ident), ident), time.monotonic() + self.deadlines.limits["request_timeout"]

    def call(self, method, params, ident):
        key, deadline = self.request(method, params, ident)
        return self.collect({key: deadline})[0]

    def finish(self):
        reply = self.call("shutdown", None, "shutdown")
        if reply.get("result", 0) is not None:
            raise CaseFailure("protocol-failure", "shutdown did not answer null: %s" % json.dumps(reply)[:400])
        self.send("exit", request=False)
        self.close_input()
        limit, which = self.deadlines.bound(time.monotonic() + 5)
        while self.proc.poll() is None:
            if time.monotonic() >= limit:
                if which == "request":
                    raise CaseFailure("server-failure", "the server did not exit within 5 s of exit")
                raise self.deadlines.expired(which, "waiting for the server to exit")
            time.sleep(0.01)
        if self.proc.returncode != 0:
            raise CaseFailure("server-failure", "the server exited with status %d after exit" % self.proc.returncode)
        rest = self.buffer
        while not self.eof:
            remaining = limit - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise CaseFailure("server-failure", "the server's output stayed open after it exited")
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            self.eof = not chunk
            rest += chunk
        if rest:
            raise CaseFailure("protocol-failure", "unsolicited output after exit: %r" % rest[:400])
        missing = set(self.expected) - set(self.replies)
        if missing:
            raise CaseFailure("protocol-failure", "requests without replies: %s" % sorted(missing, key=str))

    def close(self):
        # A server exits at the end of its input. Never wait past the run
        # deadline; only this session's own child is killed.
        self.close_input()
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=max(0.1, min(2, self.deadlines.run - time.monotonic())))
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        # The server and any worker it started have written their last words.
        self.final_log = self.logs()
        super().close()


def worker_deaths(log, texts):
    """Each worker death the server logged, its trap, and the known row it matches."""
    deaths, previous = [], 0
    for m in DEATH.finditer(log):
        traps = list(TRAP.finditer(log, previous, m.start()))
        previous = m.end()
        trap = traps[-1] if traps else None
        frames = [f.groupdict() for f in FRAME.finditer(trap["frames"])] if trap else []
        death = {"pid": int(m["pid"]), "what": m["what"], "status": m["status"],
                 "trap": trap["trap"] if trap else None, "frames": frames[:6], "known": None}
        for known in KNOWN_WORKER_FAILURES:
            top = frames[0] if frames else None
            if (m["what"] == known["what"] and death["trap"] == known["trap"]
                    and (top is None if known["function"] is None else
                         top is not None and top["function"] == known["function"] and top["file"].endswith(known["file"]))
                    and any(known["source"](t) for t in texts)):
                death["known"] = known["known"]
                break
        deaths.append(death)
    return deaths


def run_case(record, exe, run_deadline, work, validator, worker_timeout_ms=WORKER_TIMEOUT_MS, replay=False):
    """Run one case record; return its result and its session's transcript.

    A live run resolves hierarchy follow-ups from the prepare replies and adds
    them to the record; a replay sends the recorded ones.
    """
    limits = record["limits"]
    root = Path(tempfile.mkdtemp(prefix="case-", dir=work))
    root_uri = root.as_uri()
    assert len(root_uri) + 64 <= MAX_URI, ("project path too long for the charge estimate", root)
    for name, text in record["files"].items():
        (root / name).write_bytes(text.encode())
    (root / ".virgil-lsp.json").write_bytes(record["config"].encode())
    started = time.monotonic()
    deadlines = Deadlines(run_deadline, min(run_deadline, started + limits["case_timeout"]), limits)
    result = {"id": record["id"], "outcome": None, "error": None, "requests": Counter(), "unexercised": []}
    s = None
    try:
        s = MutationSession(exe, deadlines, validator, "--worker-timeout-ms=%d" % worker_timeout_ms)
        drive(s, record, root_uri, result, replay)
        result["outcome"] = "pass"
    except CaseFailure as failure:
        result["outcome"], result["error"] = failure.classification, str(failure)
    except Exception:
        result["outcome"], result["error"] = "harness-error", traceback.format_exc()
    finally:
        if s is not None:
            s.close()
        shutil.rmtree(root, ignore_errors=True)
    log = s.final_log if s else ""
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    texts = list(record["files"].values()) + [t for step in record["steps"] for t in step["texts"].values()]
    result["worker_deaths"] = worker_deaths(log, texts)
    result["messages"] = s.messages if s else []
    result["unchecked_shapes"] = s.unchecked if s else []
    failed = log.count(FAILED_ANALYSIS)
    if result["outcome"] == "pass":
        if len(result["messages"]) > failed:
            result["outcome"] = "protocol-failure"
            result["error"] = "window/showMessage reports a worker failure that the server did not log"
        elif any(d["known"] is None for d in result["worker_deaths"]):
            result["outcome"] = "worker-failure"
            result["error"] = "a worker failure that no documented signature matches"
        elif result["worker_deaths"]:
            result["outcome"] = "known-worker-trap"
    # Clean analysis after restoration ran on a replacement worker only if the case got that far.
    for death in result["worker_deaths"]:
        death["replacement"] = "verified" if result.get("restored") else "not verified"
    result["requests"] = dict(sorted(result["requests"].items()))
    return result, (s.transcript if s else []), (s.stamps if s else []), log


def drive(s, record, root_uri, result, replay):
    limits = record["limits"]
    init = s.call("initialize", templated(record["initialize"], root_uri, reverse=True), 1)
    if "result" not in init:
        raise CaseFailure("protocol-failure", "initialize failed: %s" % json.dumps(init)[:400])
    drift = coverage_drift(init["result"]["capabilities"])
    if drift:
        raise CaseFailure("coverage-manifest", "; ".join(drift))
    s.send("initialized", {}, request=False)
    versions = {}
    for name, text in record["files"].items():
        versions[name] = 1
        s.send("textDocument/didOpen", {"textDocument": {
            "uri": root_uri + "/" + name, "languageId": LANGUAGE_ID, "version": 1, "text": text}}, request=False)
    next_id = record["next_id"]
    used = 0
    for step in record["steps"]:
        for name, text in step["texts"].items():
            versions[name] += 1
            s.send("textDocument/didChange", {"textDocument": {"uri": root_uri + "/" + name, "version": versions[name]},
                                              "contentChanges": [{"text": text}]}, request=False)
        keys = {}
        for r in step["requests"]:
            key, deadline = s.request(r["method"], templated(r["params"], root_uri, reverse=True), r["id"])
            keys[key] = deadline
            result["requests"][r["method"]] += 1
        replies = s.collect(keys)
        used += len(step["requests"])
        if not replay:
            step["followups"] = []
            for r, reply in zip(step["requests"], replies):
                if r["method"] not in FOLLOW_UPS:
                    continue
                items = reply.get("result")
                reason = None
                if "error" in reply:
                    reason = "error %d" % reply["error"]["code"]
                elif not items:
                    reason = "no items"
                elif used + 2 > limits["requests"]:
                    reason = "request cap"
                if reason:
                    result["unexercised"].append({"request": r["id"], "method": r["method"], "reason": reason})
                    continue
                for method in FOLLOW_UPS[r["method"]]:
                    step["followups"].append({"id": next_id if next_id % 2 else "f%d" % next_id, "method": method,
                                              "from": r["id"], "params": {"item": templated(items[0], root_uri)}})
                    next_id += 1
                    used += 1
        keys = {}
        for f in step.get("followups", []):
            key, deadline = s.request(f["method"], templated(f["params"], root_uri, reverse=True), f["id"])
            keys[key] = deadline
            result["requests"][f["method"]] += 1
        s.collect(keys)
    # Restore clean sources: the project must analyze again, on a replacement
    # worker if one died, and answer from the observer.
    for name, text in record["restore"].items():
        versions[name] += 1
        s.send("textDocument/didChange", {"textDocument": {"uri": root_uri + "/" + name, "version": versions[name]},
                                          "contentChanges": [{"text": text}]}, request=False)
    def at(position):
        return {"textDocument": {"uri": root_uri + "/" + OBSERVER_NAME}, "position": position}
    hover, definition, calls, types = s.collect(dict([
        s.request("textDocument/hover", at(OBSERVER_USE), "restored-hover"),
        s.request("textDocument/definition", at(OBSERVER_USE), "restored-definition"),
        s.request("textDocument/prepareCallHierarchy", at(OBSERVER_DECLARATION["start"]), "restored-calls"),
        s.request("textDocument/prepareTypeHierarchy", at(OBSERVER_LEAF), "restored-types")]))
    value = ((hover.get("result") or {}).get("contents") or {}).get("value", "")
    if "stable(x: int) -> int" not in value:
        raise CaseFailure("recovery-failure", "no clean-source hover after restoration: %s" % json.dumps(hover)[:400])
    location = definition.get("result")
    location = location[0] if isinstance(location, list) and location else location
    expected = {"uri": root_uri + "/" + OBSERVER_NAME, "range": OBSERVER_DECLARATION}
    if location != expected:
        raise CaseFailure("recovery-failure", "wrong definition after restoration: %s" % json.dumps(definition)[:400])
    # The valid-item branches of the hierarchy follow-ups, on clean source.
    items = calls.get("result") or [{}]
    if items[0].get("name") != "stable":
        raise CaseFailure("recovery-failure", "no call hierarchy item after restoration: %s" % json.dumps(calls)[:400])
    incoming = s.call("callHierarchy/incomingCalls", {"item": items[0]}, "restored-incoming")
    if [c["from"]["name"] for c in incoming.get("result") or []] != ["check"]:
        raise CaseFailure("recovery-failure", "wrong incoming calls after restoration: %s" % json.dumps(incoming)[:400])
    items = types.get("result") or [{}]
    if items[0].get("name") != "MutationLeaf":
        raise CaseFailure("recovery-failure", "no type hierarchy item after restoration: %s" % json.dumps(types)[:400])
    supertypes = s.call("typeHierarchy/supertypes", {"item": items[0]}, "restored-supertypes")
    if [t["name"] for t in supertypes.get("result") or []] != ["MutationBase"]:
        raise CaseFailure("recovery-failure", "wrong supertypes after restoration: %s" % json.dumps(supertypes)[:400])
    dirty = {uri: d for uri, d in s.diagnostics.items() if d}
    if dirty:
        raise CaseFailure("recovery-failure", "restored sources still have diagnostics: %s" % json.dumps(dirty)[:400])
    result["restored"] = True
    s.finish()


# ---------------------------------------------------------------------------
# Artifacts: a bounded budget, with markers wherever anything is cut.

class Artifacts:
    """Write evidence within a byte budget. Replay records and results are
    always written in full; passing cases' transcripts and logs may use only
    three quarters of the budget, leaving the rest for failure evidence."""

    def __init__(self, directory, budget):
        self.directory, self.budget, self.used = directory, budget, 0
        self.truncated = []

    def write(self, rel, data, essential=False, failure=False):
        path = self.directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        limit = self.budget if failure else self.budget * 3 // 4
        if not essential and self.used + len(data) > limit:
            keep = max(0, limit - self.used - 200)
            omitted = len(data) - keep
            data = data[:keep] + ("\n[truncated: %d bytes omitted by the artifact budget]\n" % omitted).encode()
            self.truncated.append(rel)
        self.used += len(data)
        path.write_bytes(data)
        return rel not in self.truncated

    def transcript(self, rel, transcript, stamps, failure=False):
        lines = [json.dumps({"t": t, "dir": d, "msg": m}, ensure_ascii=False) for (d, m), t in zip(transcript, stamps)]
        limit = (self.budget if failure else self.budget * 3 // 4) - self.used
        out, size = [], 0
        for n, line in enumerate(lines):
            if size + len(line.encode()) + 1 > limit - 200:
                out.append(json.dumps({"truncated": True, "omitted_messages": len(lines) - n,
                                       "reason": "artifact budget"}))
                self.truncated.append(rel)
                break
            out.append(line)
            size += len(line.encode()) + 1
        return self.write(rel, ("\n".join(out) + "\n").encode(), essential=True)


def dump(value):
    return (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode()


def save_case(artifacts, record, result, transcript, stamps, log):
    failure = result["outcome"] not in PASSING or bool(result["worker_deaths"])
    base = "cases/%s/" % record["id"]
    complete = artifacts.transcript(base + "transcript.jsonl", transcript, stamps, failure)
    complete = artifacts.write(base + "stderr.txt", log.encode(), failure=failure) and complete
    result["evidence_complete"] = complete
    artifacts.write(base + "case.json", dump(record), essential=True)
    artifacts.write(base + "result.json", dump(result), essential=True)
    return artifacts.directory / base / "case.json"


def build_info(exe):
    worker = exe.with_name("virgil-lsp-worker")
    version = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=10).stdout
    return {"server_sha256": sha256(exe.read_bytes()),
            "worker_sha256": sha256(worker.read_bytes()) if worker.exists() else None,
            "version": version.strip().splitlines(), "harness_sha256": sha256(Path(__file__).read_bytes()),
            "model_sha256": MODEL_SHA256}


def check_record(record):
    if record.get("format") != FORMAT:
        raise ValueError("unsupported case record format %r" % record.get("format"))
    resolve_limits({k: v for k, v in record["limits"].items()}, record["stress"])
    texts = list(record["files"].values()) + [t for s in record["steps"] for t in s["texts"].values()]
    if measure(list(record["files"].values()), record["stress"]) > record["limits"]["source_bytes"]:
        raise ValueError("case files exceed the record's source limit")
    for t in texts:
        t.encode()


def replay_command(exe, case_path):
    return shlex.join([sys.executable, str(Path(__file__).resolve()), str(exe), "--replay", str(case_path)])


def report_failure(exe, record, result, case_path):
    print("FAIL %s (%s): %s" % (record["id"], result["outcome"], result["error"]), file=sys.stderr)
    for death in result["worker_deaths"]:
        print("worker death: %s" % json.dumps(death), file=sys.stderr)
    print("evidence: %s\nreplay: %s" % (case_path.parent, replay_command(exe, case_path)), file=sys.stderr)


def replay(args, exe, validator):
    record = json.loads(args.replay.read_text())
    try:
        check_record(record)
    except (ValueError, KeyError) as exc:
        print("invalid case record %s: %s" % (args.replay, exc), file=sys.stderr)
        return 2
    out = args.artifacts.resolve() / ("replay-" + record["id"])
    shutil.rmtree(out, ignore_errors=True)
    artifacts = Artifacts(out, record["limits"]["artifact_bytes"])
    work = Path(tempfile.mkdtemp(prefix="work-", dir=args.artifacts.resolve()))
    try:
        run_deadline = time.monotonic() + record["limits"]["run_timeout"]
        result, transcript, stamps, log = run_case(record, exe, run_deadline, work, validator, replay=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    case_path = save_case(artifacts, record, result, transcript, stamps, log)
    artifacts.write("run.json", dump({"replay": str(args.replay), **build_info(exe), "result": result}), essential=True)
    if result["outcome"] not in PASSING:
        report_failure(exe, record, result, case_path)
        return 1
    print("source mutations: replayed %s: %s; %d requests; worker deaths %s; %.2fs" % (
        record["id"], result["outcome"], sum(result["requests"].values()),
        [d["known"] for d in result["worker_deaths"]], result["elapsed_s"]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("exe", type=Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--cases", type=int)
    parser.add_argument("--source-bytes", type=int, help="total source per case; the aggregate document charge with --stress")
    parser.add_argument("--requests", type=int, help="feature requests per case, follow-ups included")
    parser.add_argument("--request-timeout", type=float)
    parser.add_argument("--case-timeout", type=float)
    parser.add_argument("--run-timeout", type=float)
    parser.add_argument("--artifact-bytes", type=int)
    parser.add_argument("--artifacts", type=Path, default=Path("build/source-mutations"))
    parser.add_argument("--replay", type=Path, metavar="CASE.json", help="run one saved case record exactly")
    parser.add_argument("--stress", action="store_true", help="large deep/dense cases, at most 10, up to a 1 MiB charge")
    parser.add_argument("--plan-only", action="store_true", help="write the manifests and case records without a server")
    args = parser.parse_args(argv)
    exe = args.exe.resolve()
    validator = ReplyValidator()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    if args.replay:
        return replay(args, exe, validator)
    try:
        limits = resolve_limits({k: getattr(args, k) for k in LIMITS}, args.stress)
    except ValueError as exc:
        parser.error(str(exc))
    started = time.monotonic()
    run_deadline = started + limits["run_timeout"]
    seeds, inputs = load_seeds()
    plan = plan_run(args.seed, limits, args.stress, seeds)
    out = args.artifacts.resolve() / ("%sseed-%d" % ("stress-" if args.stress else "", args.seed))
    shutil.rmtree(out, ignore_errors=True)
    artifacts = Artifacts(out, limits["artifact_bytes"])
    mutations, requests = manifests(args.seed, limits, args.stress, inputs, plan)
    artifacts.write("mutations.json", dump(mutations), essential=True)
    artifacts.write("requests.json", dump(requests), essential=True)
    if args.plan_only:
        for record in plan:
            artifacts.write("cases/%s/case.json" % record["id"], dump(record), essential=True)
        print("source mutations: planned %d cases; manifests in %s" % (len(plan), out))
        return 0
    run = {"seed": args.seed, "stress": args.stress, "limits": limits, "inputs": inputs, **build_info(exe),
           "cases_planned": len(plan), "outcomes": Counter(), "requests": Counter(), "worker_deaths": [],
           "unexercised": [], "unchecked_shapes": [], "failures": [], "incomplete": False}
    status = 0
    work = Path(tempfile.mkdtemp(prefix="work-", dir=args.artifacts.resolve()))
    try:
        for record in plan:
            if time.monotonic() >= run_deadline:
                run["incomplete"] = True
                break
            artifacts.write("cases/%s/case.json" % record["id"], dump(record), essential=True)
            result, transcript, stamps, log = run_case(record, exe, run_deadline, work, validator)
            case_path = save_case(artifacts, record, result, transcript, stamps, log)
            run["outcomes"][result["outcome"]] += 1
            run["requests"].update(result["requests"])
            run["worker_deaths"] += [dict(d, case=record["id"]) for d in result["worker_deaths"]]
            run["unexercised"] += [dict(u, case=record["id"]) for u in result["unexercised"]]
            run["unchecked_shapes"] += [{"case": record["id"], "error": e} for e in result["unchecked_shapes"]]
            if result["outcome"] not in PASSING:
                run["failures"].append({"case": record["id"], "outcome": result["outcome"], "error": result["error"],
                                        "replay": replay_command(exe, case_path)})
                run["incomplete"] = result["outcome"] == "run-deadline"
                report_failure(exe, record, result, case_path)
                status = 1
                break
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if run["incomplete"]:
        print("FAIL run deadline of %ss expired after %d of %d cases; evidence in %s" % (
            limits["run_timeout"], sum(run["outcomes"].values()), len(plan), out), file=sys.stderr)
        status = 1
    exercised = set(run["requests"])
    run["never_exercised"] = [m for m in MANIFEST if m not in exercised]
    run["elapsed_s"] = round(time.monotonic() - started, 2)
    run["artifact_bytes_used"], run["artifacts_truncated"] = artifacts.used, artifacts.truncated
    artifacts.write("run.json", dump(run), essential=True)
    known = Counter(d["known"] for d in run["worker_deaths"] if d["known"])
    print("source mutations: %s; seed=%d; cases=%d/%d; requests=%d; %.2fs" % (
        "passed" if status == 0 else "FAILED", args.seed, sum(run["outcomes"].values()), len(plan),
        sum(run["requests"].values()), run["elapsed_s"]))
    print("outcomes: " + "; ".join("%s=%d" % kv for kv in sorted(run["outcomes"].items())))
    print("worker deaths: %d known (%s), %d unclassified" % (
        sum(known.values()), ", ".join("%s=%d" % kv for kv in sorted(known.items())) or "none",
        sum(1 for d in run["worker_deaths"] if not d["known"])))
    print("requests (method=count): " + "; ".join("%s=%d" % kv for kv in sorted(run["requests"].items())))
    if run["never_exercised"]:
        print("never exercised in this run: " + ", ".join(run["never_exercised"]))
    print("valid-item follow-ups not sent: %d (see run.json); artifacts: %s (%d bytes%s)" % (
        len(run["unexercised"]), out, artifacts.used, ", truncated" if artifacts.truncated else ""))
    return status


if __name__ == "__main__":
    sys.exit(main())
