#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Measure completion and signature help through typing histories over stdio.

An opt-in companion to bench-tolerance.py: 17 fixed histories, each replayed in
warm, cold, and saved modes, and checked against reviewed facts about the
intended program. See docs/benchmarks.md#cursor-tolerance-histories-2026-10-31.
"""
import argparse
import collections
import datetime
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tolerance = load("bench-tolerance")
bench, states, sessions = tolerance.bench, tolerance.states, tolerance.sessions
check, sha256 = bench.check, bench.corpus.sha256
ROOT = bench.ROOT
INVENTORY = ROOT / "test/benchmarks/cursor-tolerance-histories.json"
VIRGIL = ROOT / "vendor/virgil"
MODES = ("warm", "cold", "saved")
BLOCKS = 6
SMOKE = ("member-with-prefix", "call-open-arguments")
VERDICTS = ("exact", "partial", "missing", "keywords-only", "wrong")
# Outcomes with no verdict: the step is before a cold process starts, or the
# request needs an overlay that the saved mode closed.
UNMEASURED = ("not-run", "unsupported", "inapplicable")
KEYWORD_KIND = 14

# The small fixture of the nine new histories, indented with tabs, as ADR-0010's
# brace rules read them.
UTIL = ("component Util {\n"
        "\tdef add(left: int, right: int) -> int {\n"
        "\t\treturn left + right;\n"
        "\t}\n"
        "}\n")
A = ("component A {\n"
     "\tdef typed: int = Util.add(1, 2);\n"
     "\tdef inferred = Util.add(1, 2);\n"
     "\tdef edit(n: int) -> int {\n"
     "\t\tvar r = Util.add(n, 2);\n"
     "\t\tvar s = \"text\"; /* note */\n"
     "\t\tif (r > 0) {\n"
     "\t\t\tr = r + s.length;\n"
     "\t\t}\n"
     "\t\treturn r;\n"
     "\t}\n"
     "\tdef sibling() -> int {\n"
     "\t\treturn Util.add(3, 4);\n"
     "\t}\n"
     "\tdef third() {\n"
     "\t\tvar x = 0;\n"
     "\t}\n"
     "}\n")
CONSUMER = ("component Consumer {\n"
            "\tdef use() -> int {\n"
            "\t\treturn A.sibling();\n"
            "\t}\n"
            "}\n")
FIXTURE = {"Util.v3": UTIL, "A.v3": A, "Consumer.v3": CONSUMER}


def locate(text, anchor, where):
    """The offset of the caret "|" in {anchor}, whose text without it must occur
    exactly once in {text}. A missing or repeated anchor is refused rather than
    resolved to another site."""
    check(anchor.count("|") == 1, (where, "an anchor marks exactly one caret", anchor))
    needle = anchor.replace("|", "")
    count = text.count(needle)
    check(count == 1, (where, "anchor is not unique" if count else "anchor is missing", anchor, count))
    return text.index(needle) + anchor.index("|")


def edit(text, find, replace, where):
    """{text} with the one occurrence of {find} replaced."""
    count = text.count(find)
    check(count == 1, (where, "edit site is not unique" if count else "edit site is missing", find, count))
    return text.replace(find, replace, 1)


def identifier(text, offset):
    """The range of the identifier that starts at {offset}."""
    end = offset
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    check(end > offset, ("no identifier at the target anchor", text[offset:offset + 20]))
    return {"start": bench.corpus.position(text, offset), "end": bench.corpus.position(text, end)}


# Oracle facts, reviewed against the intended program: the candidates that a
# completion must offer, with their kind and detail, the labels that only an
# older program has, and a signature's label, parameters, and active parameter.
def member(label, detail, kind, forbidden=()):
    return {"member": True, "required": [{"label": label, "kind": kind, "detail": detail}],
            "forbidden": list(forbidden)}


def names(*required, forbidden=()):
    return {"member": False, "required": [{"label": l, "kind": k, "detail": d} for l, k, d in required],
            "forbidden": list(forbidden)}


def signature(label, parameters, active):
    return {"label": label, "parameters": list(parameters), "active": active}


INT_TYPE = names(("int", 7, None))
ADD = "def add(left: int, right: int) -> int"
ADD_SIGNATURE = signature(ADD, ["left: int", "right: int"], 1)
ADD_MEMBER = member("add", ADD, 2)
SEED_ADD_SIGNATURE = signature("def add(a: int, b: int) -> int", ["a: int", "b: int"], 1)


def probe(name, kind, path, anchor, intended, facts, stale_sensitive=False):
    return {"id": name, "kind": kind, "path": path, "anchor": anchor, "intended_anchor": intended,
            "facts": facts, "stale_sensitive": stale_sensitive}


def step(name, operation, edits=(), probes=(), intended=None, parses=None, restore=False):
    """One step: its {edits}, each (path, find, replace), applied to the texts of
    the step before; or, with {restore}, the baseline texts of the files it
    names. {intended} names the step whose texts are the intended program, by
    default this step's own; {parses} lists the files whose current text the
    CLI parser must accept (by default those whose text is intended)."""
    return {"id": name, "operation": operation, "edits": [list(e) for e in edits], "restore": restore,
            "probes": list(probes), "intended": intended, "parses": parses}


def observer(name, role, path, anchor, target, target_anchor, steps=None, reason=None):
    """A definition request at {anchor} in {path}, whose reviewed binding is the
    identifier at {target_anchor} in {target}. Outside {steps} it is
    inapplicable for {reason}."""
    return {"id": name, "role": role, "path": path, "anchor": anchor, "target": target,
            "target_anchor": target_anchor, "steps": steps, "reason": reason}


def inapplicable(name, role, reason):
    return {"id": name, "role": role, "path": None, "reason": reason}


# The six existing seeds: the fixture's text with "<|>" removed is the damaged
# state. The intended program puts a reviewed insertion there: the seed's own
# where it is a real program, otherwise a member that the receiver has.
SEED_INTENDED = {
    "member-with-prefix": "rm()",
    "call-open-arguments": "2);",
    "call-trailing-comma": "2",
    "member-in-field-initializer": "x;",
    "member-in-component-field": "x;",
    "syntax-error-in-other-file": "x;",
}
POINT_X = member("x", "def x: int", 5)
POINT_X["required"].append({"label": "norm", "kind": 2, "detail": "def norm() -> int"})
SEED_SIBLING = ("return |Point.new(x * k", "class |Point(x: int")
# The seeds whose damaged text still parses: a member name that the receiver
# lacks, and a trailing comma, which the parser accepts.
SEED_PARSES = {"member-with-prefix": ["member-with-prefix.v3"], "call-trailing-comma": ["call-trailing-comma.v3"]}
NO_OTHER_FILE = "the fixture is one file"


def seed_histories(corpus):
    seeds = json.loads(states.SEEDS.read_text())
    cases = dict(states.repair.fixture_cases())
    table = {
        "member-with-prefix": ("member completion with a typed prefix in a local initializer",
                               "completion", "var n = p.no|;", "var n = p.no|rm();",
                               member("norm", "def norm() -> int", 2),
                               ("var n = |p.", "def run(|p: Point)"), ("p.|norm()", "def |norm()")),
        "call-open-arguments": ("call arguments left open at the end of a line",
                                "signature", "var n = Util.add(1, |\n", "var n = Util.add(1, |2);",
                                SEED_ADD_SIGNATURE, ("var n = |Util.", "component |Util {"),
                                ("var n = Util.|add(1", "def |add(a: int")),
        "call-trailing-comma": ("a call's trailing comma before its closer",
                                "signature", "var n = Util.add(1,|);", "var n = Util.add(1,|2);",
                                SEED_ADD_SIGNATURE, ("var n = |Util.", "component |Util {"),
                                ("var n = Util.|add(1", "def |add(a: int")),
        "member-in-field-initializer": ("member completion in a class field's initializer",
                                        "completion", "var n: int = origin.|\n", "var n: int = origin.|x;",
                                        POINT_X, ("var n: int = |origin.", "def |origin = Point.new(0, 0);"),
                                        ("origin.|x;", "class Point(|x: int")),
        "member-in-component-field": ("member completion in a component field's initializer",
                                      "completion", "def limit = start.|\n", "def limit = start.|x;",
                                      POINT_X, ("def limit = |start.", "def |start = Point.new(1, 2);"),
                                      ("start.|x;", "class Point(|x: int")),
        "syntax-error-in-other-file": ("member completion in a return while another file has a body error",
                                       "completion", "return p.|\n", "return p.|x;", POINT_X,
                                       ("return |p.", "def run(|p: Point)"), ("return p.|x;", "class Point(|x: int")),
    }
    check(tuple(table)[:2] == SMOKE, "the smoke histories are the first two seeds")
    for name in table:
        role, kind, anchor, intended_anchor, facts, edited, use = table[name]
        seed = seeds[name]
        current, intended, hashes = {}, {}, {}
        for filename in cases[name]:
            path = Path(filename)
            rel = str(path.relative_to(ROOT))
            raw = corpus.remember(path)
            check(corpus.inputs[rel] == seed["files"][rel], (name, rel, "fixture differs from its seed hash"))
            hashes[path.name] = {"fixture": rel, "sha256": corpus.inputs[rel]}
            current[path.name] = raw.replace("<|>", "", 1)
            intended[path.name] = raw.replace("<|>", SEED_INTENDED[name], 1).replace("var x = ;", "var x = 0;")
        main = next(p for p, t in current.items() if p != "other.v3")
        observers = [observer("edited", "edited-body", main, edited[0], main, edited[1]),
                     observer("sibling", "intact-sibling", main, SEED_SIBLING[0], main, SEED_SIBLING[1]),
                     inapplicable("other-file", "other-file", NO_OTHER_FILE if len(current) == 1 else
                                  "other.v3 has no name to navigate")]
        valid = ["baseline", "restored", "repeat"]
        observers.append(observer("callee" if kind == "signature" else "candidate",
                                  "callee" if kind == "signature" else "candidate", main, use[0], main, use[1],
                                  steps=None if kind == "signature" else valid,
                                  reason="the candidate's use is not typed yet"))
        p = lambda a: [probe("cursor", kind, main, a, intended_anchor, facts)]
        yield {"id": name, "origin": "seed", "role": role, "seed": {"policy": seed["policy"], "files": hashes},
               "initial": dict(intended), "open": sorted(current),
               "observers": observers, "steps": [
                   step("baseline", "valid intended program", probes=p(intended_anchor)),
                   step("damaged", "the seed's damaged fixture text", [(f, intended[f], current[f]) for f in current],
                        probes=p(anchor), intended="baseline", parses=SEED_PARSES.get(name, [])),
                   step("restored", "the intended program again", restore=True, probes=p(intended_anchor)),
                   step("repeat", "the restored query again", probes=p(intended_anchor))]}


def small_observers(edited_anchor="var r = |Util.", edited_target=("Util.v3", "component |Util {"),
                    sibling=("return Util.|add(3, 4)", "def |add("), extra=()):
    return [observer("edited", "edited-body", "A.v3", edited_anchor, *edited_target),
            observer("sibling", "intact-sibling", "A.v3", sibling[0], "Util.v3", sibling[1]),
            observer("other-file", "other-file", "Consumer.v3", "return A.|sibling();", "A.v3", "def |sibling()"),
            *extra]


def five(name, role, kind, removed, extra, anchors, facts, observers, parses=()):
    """A five-step small history: the baseline, a token removed or replaced, one
    more typed character or damage, the restored baseline, and its query again.
    {anchors} are the probe's anchors in the four distinct texts, then in the
    intended texts of the two damaged steps."""
    base, gone, more, back, gone_intended, more_intended = anchors
    p = lambda a, i: [probe("cursor", kind, "A.v3", a, i, facts)]
    return {"id": name, "origin": "small", "role": role, "initial": dict(FIXTURE), "open": sorted(FIXTURE),
            "observers": observers, "steps": [
                step("baseline", "valid fixture", probes=p(base, base)),
                step("removed", removed[0], [("A.v3",) + tuple(removed[1:])], probes=p(gone, gone_intended),
                     intended="baseline", parses=["A.v3"] if "removed" in parses else []),
                step("extra", extra[0], [("A.v3",) + tuple(extra[1:])], probes=p(more, more_intended),
                     intended="baseline", parses=["A.v3"] if "extra" in parses else []),
                step("restored", "the baseline restored", restore=True, probes=p(back, back)),
                step("repeat", "the restored query again", probes=p(back, back))]}


def small_histories():
    call = "var r = Util.add(n, |2);"
    typed_call = "var r = Util.add(n, 1|2);"
    dot = "var r = Util.|add(n, 2);"
    typed_dot = "var r = Util.a|add(n, 2);"
    callee = observer("callee", "callee", "A.v3", "var r = Util.|add(n", "Util.v3", "def |add(")
    candidate = observer("candidate", "candidate", "A.v3", "var r = Util.|add(n", "Util.v3", "def |add(",
                         steps=["baseline", "removed", "restored", "repeat"], reason="the typed prefix renames the use")
    builtin = inapplicable("candidate", "candidate", "int is a builtin type without a source declaration")
    yield five("parameter-type", "a parameter's type in a method header", "completion",
               ("the parameter type int removed", "def edit(n: int)", "def edit(n: )"),
               ("i typed as the type", "def edit(n: )", "def edit(n: i)"),
               ("def edit(n: |int)", "def edit(n: |)", "def edit(n: i|)", "def edit(n: i|nt)",
                "def edit(n: |int)", "def edit(n: i|nt)"),
               INT_TYPE, small_observers(extra=[builtin]), parses=("extra",))
    yield five("return-type", "a method's return type in its header", "completion",
               ("the return type int removed", "def edit(n: int) -> int {", "def edit(n: int) -> {"),
               ("i typed as the type", "def edit(n: int) -> {", "def edit(n: int) -> i {"),
               ("def edit(n: int) -> |int {", "def edit(n: int) -> |{", "def edit(n: int) -> i| {",
                "def edit(n: int) -> i|nt {", "def edit(n: int) -> |int {", "def edit(n: int) -> i|nt {"),
               INT_TYPE, small_observers(extra=[builtin]), parses=("extra",))
    typed = "def typed: int = Util."
    yield five("typed-initializer", "call arguments in a typed field's initializer", "signature",
               ("the argument 2 removed, leaving the paired parentheses", typed + "add(1, 2);", typed + "add(1, );"),
               ("the closing parenthesis removed", typed + "add(1, );", typed + "add(1, ;"),
               (typed + "add(1, |2);", typed + "add(1, |);", typed + "add(1, |;", typed + "add(1, |2);",
                typed + "add(1, |2);", typed + "add(1, |2);"),
               ADD_SIGNATURE, small_observers("def typed: int = |Util.", extra=[
                   observer("callee", "callee", "A.v3", typed + "|add(", "Util.v3", "def |add(")]),
               parses=("removed",))
    inferred = "def inferred = Util."
    yield five("inferred-initializer", "call arguments in an inferred field's initializer", "signature",
               ("the closing parenthesis removed", inferred + "add(1, 2);", inferred + "add(1, 2;"),
               ("3 typed before the argument", inferred + "add(1, 2;", inferred + "add(1, 32;"),
               (inferred + "add(1, |2);", inferred + "add(1, |2;", inferred + "add(1, 3|2;", inferred + "add(1, |2);",
                inferred + "add(1, |2);", inferred + "add(1, |2);"),
               ADD_SIGNATURE, small_observers("def inferred = |Util.", extra=[
                   observer("callee", "callee", "A.v3", inferred + "|add(", "Util.v3", "def |add(")]))
    yield five("open-string", "call arguments before a string left open in the same body", "signature",
               ("the string's closing quote removed", "var s = \"text\";", "var s = \"text;"),
               ("1 typed before the argument", "Util.add(n, 2", "Util.add(n, 12"),
               (call, call, typed_call, call, call, call), ADD_SIGNATURE, small_observers(extra=[callee]))
    yield five("open-block-comment", "member completion before a block comment left open", "completion",
               ("the comment's closing */ removed", "/* note */", "/* note "),
               ("a typed after the dot", "Util.add(n, 2", "Util.aadd(n, 2"),
               (dot, dot, typed_dot, dot, dot, "var r = Util.a|dd(n, 2);"), ADD_MEMBER,
               small_observers(extra=[candidate]))
    yield five("missing-inner-closer", "call arguments in a body whose inner if lost its }", "signature",
               ("the inner if's } removed", "s.length;\n\t\t}\n", "s.length;\n\t\t\n"),
               ("1 typed before the argument", "Util.add(n, 2", "Util.add(n, 12"),
               (call, call, typed_call, call, call, call), ADD_SIGNATURE, small_observers(extra=[callee]))
    yield five("missing-method-closer", "member completion in a method that lost its own }", "completion",
               ("edit's own } removed", "\t\treturn r;\n\t}\n", "\t\treturn r;\n\t\n"),
               ("a typed after the dot", "Util.add(n, 2", "Util.aadd(n, 2"),
               (dot, dot, typed_dot, dot, dot, "var r = Util.a|dd(n, 2);"), ADD_MEMBER,
               small_observers(extra=[candidate]))
    yield third_body("sum", "amount")


def third_body(name, parameter):
    """The third damaged body: Util.add renamed to {name} and its parameter
    right to {parameter}, with the valid call sites updated, then the calls in
    edit and sibling lose their ")" and a member access is typed in third. With
    add and right this is the stale-answer control's program."""
    renamed = name != "add"
    sig = "def %s(left: int, %s: int) -> int" % (name, parameter)
    call = "var r = Util.%s(n, " % name
    rename = [("Util.v3", "def add(left: int, right: int) -> int {\n\t\treturn left + right;",
               "def %s(left: int, %s: int) -> int {\n\t\treturn left + %s;" % (name, parameter, parameter))]
    rename += [("A.v3", site + "add(", site + name + "(") for site in (
        "typed: int = Util.", "inferred = Util.", "var r = Util.", "return Util.")]
    scope = names(("Util", 9, "component Util"), ("sibling", 2, "def sibling() -> int"))
    stale = ["add"] if renamed else []
    members = member(name, sig, 2, forbidden=stale)
    sums = signature(sig, ["left: int", parameter + ": int"], 1)
    def p(a, i, s, j):
        # Before third's member is typed, its completion lists the names in
        # scope, which the rename doesn't change.
        typed = "Util." in i
        return [probe("third", "completion", "A.v3", a, i, members if typed else scope, stale_sensitive=typed),
                probe("edit-call", "signature", "A.v3", s, j, sums, stale_sensitive=True)]
    restored = "var x = Util.|%s;" % name
    history = {
        "id": "third-damaged-body" if renamed else "third-damaged-body-stale",
        "origin": "small", "role": "member completion in a third body after damage in two others",
        "initial": dict(FIXTURE), "open": sorted(FIXTURE),
        "observers": small_observers(sibling=("typed: int = Util.|%s(" % name, "def |%s(" % name), extra=[
            observer("callee", "callee", "A.v3", call.replace("Util.", "Util.|"), "Util.v3", "def |%s(" % name),
            observer("candidate", "candidate", "A.v3", restored, "Util.v3", "def |%s(" % name,
                     steps=["restored", "repeat"], reason="the candidate's use is not typed yet")]),
        "steps": [
            step("baseline", "Util.add renamed to %s and right to %s, with every call site updated"
                 % (name, parameter), rename if renamed else (),
                 probes=p("var x = |0;", "var x = |0;", call + "|2);", call + "|2);")),
            step("removed", "the ) of the calls in edit and sibling removed",
                 [("A.v3", call + "2);", call + "2;"), ("A.v3", "return Util.%s(3, 4);" % name,
                                                       "return Util.%s(3, 4;" % name)],
                 probes=p("var x = |0;", "var x = |0;", call + "|2;", call + "|2);"), intended="baseline", parses=[]),
            step("extra", "Util. typed in third", [("A.v3", "var x = 0;", "var x = Util.;")],
                 probes=p("var x = Util.|;", restored, call + "|2;", call + "|2);"), intended="restored", parses=[]),
            step("restored", "edit and sibling restored and third's member completed",
                 [("A.v3", call + "2;", call + "2);"), ("A.v3", "return Util.%s(3, 4;" % name,
                                                       "return Util.%s(3, 4);" % name),
                  ("A.v3", "var x = Util.;", "var x = Util.%s;" % name)],
                 probes=p(restored, restored, call + "|2);", call + "|2);")),
            step("repeat", "the restored queries again", probes=p(restored, restored, call + "|2);", call + "|2);"))]}
    return history


def aeneas_history(name, rel, line, receiver, member_name, facts, observers):
    """A real member completion in the pinned compiler: the member after
    {receiver}. removed, one prefix character typed, then the original line."""
    path = "aeneas/src/" + rel
    dot = receiver + "."
    full = dot + member_name
    p = lambda a, i: [probe("cursor", "completion", path, a, i, facts)]
    q = lambda prefix: line.replace(full, dot + prefix + "|" + member_name[len(prefix):])
    typed = member_name[0]
    return {"id": name, "origin": "aeneas", "role": "member completion in a real method body",
            "initial": None, "open": [path], "aeneas_path": path, "observers": observers, "steps": [
                step("baseline", "the upstream file", probes=p(q(""), q(""))),
                step("dot", "the member after the dot removed", [(path, line, line.replace(full, dot))],
                     probes=p(line.replace(full, dot + "|"), q("")), intended="baseline", parses=[]),
                step("prefix", "its first character typed", [(path, line.replace(full, dot), line.replace(full, dot + typed))],
                     probes=p(line.replace(full, dot + typed + "|"), q(typed)), intended="baseline", parses=[path]),
                step("restored", "the upstream line restored", restore=True, probes=p(q(typed), q(typed))),
                step("repeat", "the restored query again", probes=p(q(typed), q(typed)))]}


def aeneas_histories():
    verifier = "vst/Verifier.v3"
    yield aeneas_history(
        "aeneas-verifier-member", verifier, "var vst = prog.vst;", "prog", "vst", member("vst", "var vst: VstModule", 5), [
            observer("edited", "edited-body", "aeneas/src/" + verifier, "for (f in vst.files) |buildFile(f);",
                     "aeneas/src/" + verifier, "def |buildFile(file: VstFile)"),
            observer("sibling", "intact-sibling", "aeneas/src/" + verifier, "if (!|ERROR.notTooMany) break;",
                     "aeneas/src/" + verifier, "def |ERROR = prog.ERROR;"),
            observer("other-file", "other-file", "aeneas/src/main/Compiler.v3", "Verifier.new(compiler, prog).|verify();",
                     "aeneas/src/" + verifier, "def |verify() {\n\t\tvar vst = prog."),
            observer("candidate", "candidate", "aeneas/src/" + verifier, "var vst = prog.|vst;",
                     "aeneas/src/core/Program.v3", "var |vst: VstModule;", steps=["baseline", "restored", "repeat"],
                     reason="the member is removed or partly typed")])
    patcher = "arm64/Arm64AddrPatcher.v3"
    yield aeneas_history(
        "aeneas-arm64-member", patcher, "def pos = mw.pos;", "mw", "pos", member("pos", "def var pos: int", 5), [
            observer("edited", "edited-body", "aeneas/src/" + patcher, "_ => |unknownPatchKind();",
                     "aeneas/src/" + patcher, "def |unknownPatchKind() {"),
            observer("sibling", "intact-sibling", "aeneas/src/" + patcher, "|mach.fail(\"Arm64AddrPatcher",
                     "aeneas/src/" + patcher, "def |mach: MachProgram;"),
            observer("other-file", "other-file", "aeneas/src/arm64/Arm64Backend.v3",
                     "patcher = |Arm64AddrPatcher.new(w, mach, asm);", "aeneas/src/" + patcher, "class |Arm64AddrPatcher {"),
            observer("candidate", "candidate", "aeneas/src/" + patcher, "def pos = mw.|pos;",
                     "lib/util/DataWriter.v3", "def var |pos: int;", steps=["baseline", "restored", "repeat"],
                     reason="the member is removed or partly typed")])


def resolve(history, sources):
    """{history} with each step's current and intended texts, by hash, and every
    probe and observer anchor resolved in them. {sources} holds every project
    file's original text."""
    texts = {}

    def keep(text):
        digest = sha256(text.encode())
        texts[digest] = text
        return digest

    steps, current = [], dict(sources)
    baseline = None
    for index, st in enumerate(history["steps"]):
        where = (history["id"], st["id"])
        changed = {}
        if st["restore"]:
            changed = {p: baseline[p] for p in history.get("restore_paths", baseline) if current[p] != baseline[p]}
        for path, find, replace in st["edits"]:
            changed[path] = edit(changed.get(path, current[path]), find, replace, where)
        current = {**current, **changed}
        if index == 0:
            baseline = dict(current)
        record = {"id": st["id"], "operation": st["operation"], "edits": st["edits"], "restore": st["restore"],
                  "changed": {p: sha256(t.encode()) for p, t in sorted(changed.items())}, "texts": current}
        steps.append(record)
    by_id = {st["id"]: st for st in steps}
    resolved = []
    for st, spec in zip(steps, history["steps"]):
        where = (history["id"], st["id"])
        intended = by_id[spec["intended"]]["texts"] if spec["intended"] else st["texts"]
        damaged = {p for p in sources if st["texts"][p] != intended[p]}
        files = tracked(history)
        parses = sorted(p for p in files if p not in damaged or p in (spec["parses"] or []))
        damaged &= files
        probes = []
        for pr in spec["probes"]:
            at = locate(st["texts"][pr["path"]], pr["anchor"], where + (pr["id"],))
            ia = locate(intended[pr["path"]], pr["intended_anchor"], where + (pr["id"], "intended"))
            probes.append({**pr, "position": bench.corpus.position(st["texts"][pr["path"]], at),
                           "intended_position": bench.corpus.position(intended[pr["path"]], ia)})
        observers = []
        for ob in history["observers"]:
            if ob["path"] is None or (ob["steps"] is not None and st["id"] not in ob["steps"]):
                observers.append({"id": ob["id"], "role": ob["role"], "status": "inapplicable", "reason": ob["reason"]})
                continue
            entry = {"id": ob["id"], "role": ob["role"], "path": ob["path"], "anchor": ob["anchor"],
                     "target": ob["target"], "target_anchor": ob["target_anchor"]}
            for key, source in (("current", st["texts"]), ("intended", intended)):
                at = locate(source[ob["path"]], ob["anchor"], where + (ob["id"], key))
                to = locate(source[ob["target"]], ob["target_anchor"], where + (ob["id"], key, "target"))
                entry[key] = {"position": bench.corpus.position(source[ob["path"]], at),
                              "expected": {"uri": "project:///" + ob["target"],
                                           "range": identifier(source[ob["target"]], to)}}
            observers.append(entry)
        resolved.append({"id": st["id"], "operation": st["operation"], "edits": st["edits"], "restore": st["restore"],
                         "changed": st["changed"], "intended_step": spec["intended"] or st["id"],
                         "damaged": sorted(damaged), "parses": parses,
                         "state": "damaged" if damaged else "valid",
                         "texts": {p: sha256(t.encode()) for p, t in sorted(st["texts"].items()) if p in tracked(history)},
                         "intended": {p: sha256(t.encode()) for p, t in sorted(intended.items()) if p in tracked(history)},
                         "probes": probes, "observers": observers})
        for p in tracked(history):
            keep(st["texts"][p])
            keep(intended[p])
    return resolved, texts


def tracked(history):
    """The files whose texts a history's steps record: its open files and any
    file an observer targets."""
    return set(history["open"]) | {o["target"] for o in history["observers"] if o.get("path")} | {
        o["path"] for o in history["observers"] if o.get("path")}


def aeneas_sources(corpus):
    return {str(Path(p).relative_to(VIRGIL)): corpus.remember(p) for p in states.repair.aeneas_files()}


def build(corpus):
    """The inventory: each history resolved, with the text of every small or
    seed file version and the hash of every Aeneas file it reads."""
    histories, texts = [], {}
    aeneas = None
    definitions = list(seed_histories(corpus)) + list(small_histories()) + list(aeneas_histories())
    stale = third_body("add", "right")
    for history in definitions + [stale]:
        if history["origin"] == "aeneas":
            aeneas = aeneas or aeneas_sources(corpus)
            sources = aeneas
        else:
            sources = history["initial"]
        steps, found = resolve(history, sources)
        entry = {k: v for k, v in history.items() if k != "steps"}
        if history["origin"] == "aeneas":
            entry["initial"] = None
            entry["sources"] = {p: sha256(t.encode()) for p, t in sorted(aeneas.items()) if p in tracked(history)}
            entry["project_sources"] = len(aeneas)
        else:
            entry["initial"] = {p: sha256(t.encode()) for p, t in sorted(sources.items())}
            texts.update(found)
            texts.update({sha256(t.encode()): t for t in sources.values()})
        entry["steps"] = steps
        histories.append(entry)
    check(len(histories) == 18 and histories[-1]["id"] == "third-damaged-body-stale", "history inventory changed")
    measured = histories[:-1]
    check(len(measured) == 17 and len({h["id"] for h in measured}) == 17, "the inventory must have 17 histories")
    check([h["origin"] for h in measured].count("small") == 9, "nine small histories")
    for h in measured:
        check(len(h["steps"]) == {"seed": 4, "small": 5, "aeneas": 5}[h["origin"]], (h["id"], "step count changed"))
    # Every Aeneas source the real histories' project reads, not only the ones
    # their steps track, so that a change to any of them is refused too.
    return {"schema": 1, "virgil_revision": bench.corpus.git_revision(VIRGIL, "rev-parse", "HEAD"),
            "aeneas_sources": {p: sha256(t.encode()) for p, t in sorted(aeneas.items())},
            "modes": list(MODES), "blocks": BLOCKS, "smoke": list(SMOKE),
            "histories": measured, "stale_control": histories[-1],
            "texts": dict(sorted(texts.items()))}


def pretty(value, indent=""):
    """{value} as sorted JSON, one line for each value that fits in 100
    columns, so that the reviewed inventory stays readable."""
    flat = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if len(indent) + len(flat) <= 100 or not isinstance(value, (dict, list)) or not value:
        return flat
    inner = indent + " "
    if isinstance(value, list):
        return "[\n" + ",\n".join(inner + pretty(v, inner) for v in value) + "\n" + indent + "]"
    return "{\n" + ",\n".join(inner + json.dumps(k) + ": " + pretty(v, inner) for k, v in sorted(value.items())) + "\n" + indent + "}"


def frozen(inventory):
    """Refuse an inventory that differs from the checked-in one."""
    recorded = json.loads(INVENTORY.read_text())
    check(recorded == json.loads(json.dumps(inventory)),
          "the cursor histories changed; review them and run --write-inventory")
    return recorded


# Verdicts. A nonempty reply is never correct by itself: each must hold the
# reviewed facts, and anything an older program has is wrong.
def completion_verdict(reply, facts):
    items = reply.get("items") if isinstance(reply, dict) else reply
    if not isinstance(items, list) or not items:
        return "missing", "no candidates"
    labels = collections.defaultdict(list)
    for item in items:
        labels[item.get("label")].append(item)
    stale = [label for label in facts["forbidden"] if label in labels]
    if stale:
        return "wrong", "a candidate that only an older program has: " + ", ".join(stale)
    found = 0
    for want in facts["required"]:
        got = labels.get(want["label"], [])
        if not got:
            continue
        # One stale duplicate is wrong, even beside the current candidate.
        if any(i.get("kind") != want["kind"] or i.get("detail") != want["detail"] for i in got):
            return "wrong", "%s with another kind or type: %s" % (want["label"], [(i.get("kind"), i.get("detail")) for i in got])
        found += 1
    if found == len(facts["required"]):
        return "exact", None
    if found:
        return "partial", "%d of %d required candidates" % (found, len(facts["required"]))
    if all(i.get("kind") == KEYWORD_KIND for i in items):
        return "keywords-only", "%d keywords" % len(items)
    if facts["member"]:
        return "wrong", "members without any of the receiver's required members"
    return "missing", "names without the required candidates"


def signature_verdict(reply, facts):
    signatures = reply.get("signatures") if isinstance(reply, dict) else None
    if not signatures:
        return "missing", "no signature"
    index = reply.get("activeSignature") or 0
    if not isinstance(index, int) or not 0 <= index < len(signatures):
        return "wrong", "active signature %r out of range" % (index,)
    sig = signatures[index]
    if sig.get("label") != facts["label"]:
        return "wrong", "another signature: %r" % sig.get("label")
    active = sig.get("activeParameter", reply.get("activeParameter", 0))
    if active != facts["active"]:
        return "wrong", "active parameter %r, expected %r" % (active, facts["active"])
    parameters = [p.get("label") for p in sig.get("parameters", [])]
    if parameters == facts["parameters"]:
        return "exact", None
    if parameters == facts["parameters"][:len(parameters)]:
        return "partial", "%d of %d parameters" % (len(parameters), len(facts["parameters"]))
    return "wrong", "parameters %r" % parameters


def locations(reply):
    found = reply if isinstance(reply, list) else [reply] if reply else []
    return [{"uri": f.get("targetUri", f.get("uri")), "range": f.get("targetSelectionRange", f.get("range"))}
            for f in found if isinstance(f, dict)]


def observer_verdict(reply, expected):
    found = locations(reply)
    if not found:
        return "missing", "no definition"
    if found == [expected]:
        return "exact", None
    return "wrong", "bound to %r" % found


def verdict(kind, reply, facts):
    return (completion_verdict if kind == "completion" else signature_verdict)(reply, facts)


class GroupSession(bench.Session):
    """A server that leads a process group of its own, which the workers it
    starts join, so that cleanup ends them all by the group's ID, as
    test/protocol/source_mutations.py does, and never selects processes by
    name. A sentinel joins the group and lives until that cleanup: while a
    member, or its unreaped zombie, is left, the ID can't be reused, even
    after the server is reaped, so killing the group never reaches another
    process. The sentinel reads a pipe from this driver, so it exits if the
    driver does."""

    def __init__(self, exe):
        self.proc = self.sentinel = None
        try:
            super().__init__(exe, preexec_fn=os.setpgrp)
            group = self.proc.pid
            self.sentinel = subprocess.Popen(["cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, preexec_fn=lambda: os.setpgid(0, group))
        except BaseException:
            if self.proc is not None:
                self.end_group()
            raise

    def end_group(self):
        """Kill what is left of the server's group, whether or not the server
        has exited, then reap the server and the sentinel."""
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for process in (self.proc, self.sentinel):
            if process is not None:
                process.wait()
        if self.sentinel is not None:
            try:
                self.sentinel.stdin.close()
            except OSError:
                pass

    def close(self):
        # Session.close ends the input, waits a bounded time for the server,
        # and kills only it; a hung worker, or one that a stopped server left,
        # goes with the group.
        try:
            super().close()
        finally:
            self.end_group()


class Process(tolerance.Run):
    """One server process replaying a history in one mode, or its intended
    program (mode "oracle"). It sets up tolerance.Run's state itself, with
    the server in a process group of its own (GroupSession)."""

    def __init__(self, exe, project, history, mode, texts):
        self.exe, self.p, self.row = exe, project, {"history": history["id"], "mode": mode}
        self.s = GroupSession(exe)
        self.versions, self.diagnostics, self.cursor = {}, {}, 0
        self.history, self.mode, self.texts = history, mode, texts
        self.closed = set()

    def query(self, method, path, position, extra=None):
        params = {"textDocument": {"uri": (self.p["base"] / path).as_uri()}, "position": position, **(extra or {})}
        start = time.perf_counter_ns()
        result = self.request("textDocument/" + method, params)
        return tolerance.portable(result, self.p["base"]), (time.perf_counter_ns() - start) / 1e6

    def inspect(self):
        """One snapshot after the step's definition requests, which wait for
        the current analysis: a barrier, never a polling loop."""
        snapshot = self.request("virgil-lsp/snapshot")
        check(snapshot is not None, (self.row, "project analysis did not complete"))
        check(snapshot["projectConfiguration"]["current"], snapshot)
        base = self.p["base"].as_uri() + "/"
        versions, substitutions = {}, {}
        for v in snapshot["documentVersions"]:
            path = v["uri"].removeprefix(base)
            check(v["current"], ("stale document in the step's snapshot", v))
            check(v.get("version") == self.versions.get(path), ("snapshot version differs", path, v, self.versions.get(path)))
            if v.get("version") is not None:
                versions[path] = v["version"]
            if "substitution" in v:
                substitutions[path] = v["substitution"]
        return {"generation": snapshot.get("generation"), "versions": versions, "substitutions": substitutions,
                "substitution_kinds": tolerance.substitution_kinds(snapshot, self.p["base"]),
                "excluded_bodies": tolerance.excluded_bodies(snapshot, self.p["base"]),
                "semantic_errors": len(snapshot.get("diagnostics", []))}


def replay(exe, project, history, mode, texts, transcripts=None, block=None):
    """Replay {history} in one process: warm opens the initial texts and runs
    every step; cold starts at the first damaged step's texts; saved runs as
    warm but saves each change and closes a saved file that no probe reads
    while it fails to parse; oracle replays the intended texts."""
    oracle = mode == "oracle"
    run = Process(exe, project, history, mode, texts)
    record = run.row
    record.update({"block": block, "steps": []})
    base = project["base"]
    disk = {p: t for p, t in project["sources"].items()}
    probe_paths = {pr["path"] for st in history["steps"] for pr in st["probes"]}
    try:
        for path, text in disk.items():
            (base / path).write_bytes(text.encode())
        steps = history["steps"]
        first = 1 if mode == "cold" else 0
        key = "intended" if oracle else "texts"
        opened = {p: texts[steps[1]["texts"][p]] if first else project["sources"][p] for p in history["open"]}
        run.start(opened)
        state = dict(opened)
        if not first and any(opened[p] != texts[steps[0][key][p]] for p in opened):
            # The initial program differs from the baseline (the stale-answer
            # control's rename): its analysis completes before the first step.
            record["predecessor"] = list(predecessor(run, history, opened))
        for index, st in enumerate(steps):
            row = {"id": st["id"], "state": st["state"], "mode": mode}
            record["steps"].append(row)
            if index < first:
                row["status"] = "not-run"
                row["reason"] = "the cold process starts at the first damaged step"
                continue
            row["status"] = "measured"
            log_offset = os.fstat(run.s.err.fileno()).st_size
            start = time.perf_counter_ns()
            actions = []
            for path in history["open"]:
                want = texts[st[key][path]]
                if state[path] == want:
                    continue
                if oracle:
                    run.change(path, want)
                    actions.append({"op": "change", "path": path, "version": run.versions[path]})
                else:
                    actions += apply(run, mode, path, want, path not in st["parses"] and path not in probe_paths)
                state[path] = want
            row["actions"] = actions
            row["probes"] = []
            for pr in st["probes"]:
                position = pr["intended_position"] if oracle else pr["position"]
                entry = {"id": pr["id"], "kind": pr["kind"], "path": pr["path"], "position": position}
                if pr["path"] in run.closed:
                    entry.update(status="unsupported", reason="the probe's overlay is closed")
                else:
                    method = "completion" if pr["kind"] == "completion" else "signatureHelp"
                    reply, elapsed = run.query(method, pr["path"], position)
                    v, why = verdict(pr["kind"], reply, pr["facts"])
                    entry.update(status="measured", reply=reply, latency_ms=elapsed, verdict=v, detail=why)
                row["probes"].append(entry)
            row["observers"] = []
            for ob in st["observers"]:
                entry = {"id": ob["id"], "role": ob["role"]}
                if ob.get("status") == "inapplicable":
                    entry.update(status="inapplicable", reason=ob["reason"])
                else:
                    site = ob["intended" if oracle else "current"]
                    reply, elapsed = run.query("definition", ob["path"], site["position"])
                    v, why = observer_verdict(reply, site["expected"])
                    entry.update(status="measured", reply=reply, latency_ms=elapsed, verdict=v, detail=why)
                row["observers"].append(entry)
            row["snapshot"] = run.inspect()
            row["closed"] = sorted(run.closed)
            row["step_latency_ms"] = (time.perf_counter_ns() - start) / 1e6
            log = bench.logs(run.s, log_offset)
            row["worker_replacements"] = log.count("replacing analysis worker")
            row["measured_probes"] = bench.probes(log)
        record["resources"] = bench.resources(run.s.proc.pid)
        return record
    except BaseException as error:
        record["transcript"] = run.s.transcript
        error.tolerance_record = record
        raise
    finally:
        try:
            with tolerance.cleanup(record):
                run.close()
        finally:
            if transcripts is not None:
                with tolerance.cleanup(record):
                    transcripts.write(json.dumps({"block": block, "history": history["id"], "mode": mode,
                                                  "transcript": run.s.transcript,
                                                  "stderr": record.pop("stderr", None)}) + "\n")
            for path, text in disk.items():
                with tolerance.cleanup(record):
                    (base / path).write_bytes(text.encode())


def predecessor(run, history, opened):
    """The observers that the initial program has, answered there."""
    for ob in history["observers"]:
        if not ob.get("path") or ob["path"] not in opened and ob["path"] not in run.p["sources"]:
            continue
        source = opened.get(ob["path"], run.p["sources"].get(ob["path"]))
        needle = ob["anchor"].replace("|", "")
        if source.count(needle) != 1:
            continue
        at = source.index(needle) + ob["anchor"].index("|")
        reply, _ = run.query("definition", ob["path"], bench.corpus.position(source, at))
        yield {"id": ob["id"], "reply": reply}


def apply(run, mode, path, text, closable):
    """Send one file's new text: a full-text change, and in saved mode a disk
    write and didSave, then a didClose for a file that no probe reads and that
    doesn't parse. A closed file changes on disk, with a watched-file event."""
    base = run.p["base"]
    if path in run.closed:
        (base / path).write_bytes(text.encode())
        bench.notify(run.s, "workspace/didChangeWatchedFiles", {"changes": [
            {"uri": (base / path).as_uri(), "type": 2}]})
        return [{"op": "write", "path": path}]
    run.change(path, text)
    actions = [{"op": "change", "path": path, "version": run.versions[path]}]
    if mode == "saved":
        run.apply({"op": "save", "path": path, "text": text})
        actions.append({"op": "save", "path": path})
        if closable:
            run.apply({"op": "close", "path": path})
            run.closed.add(path)
            actions.append({"op": "close", "path": path})
    return actions


def project_for(history, inventory, work, aeneas):
    base = work / history["id"]
    if history["origin"] == "aeneas":
        sources = dict(aeneas)
    else:
        sources = {p: inventory["texts"][h] for p, h in history["initial"].items()}
    for rel, text in sources.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    config = {"version": 1, "projects": [{"name": "cursor", "sources": sorted(sources)}]}
    (base / ".virgil-lsp.json").write_text(json.dumps(config))
    return {"base": base, "sources": sources, "config": config, "observer": history["open"][0]}


def texts_for(history, inventory, aeneas):
    """Every text of {history} by hash, including the Aeneas edits."""
    texts = dict(inventory["texts"])
    if history["origin"] == "aeneas":
        texts.update(aeneas_texts(history, aeneas))
    return texts


def aeneas_texts(history, aeneas):
    current, texts = dict(aeneas), {}
    for st in history["steps"]:
        if st["restore"]:
            current.update({p: aeneas[p] for p in history["open"]})
        for path, find, replace in st["edits"]:
            current[path] = edit(current[path], find, replace, (history["id"], st["id"]))
        for path in tracked(history):
            texts[sha256(current[path].encode())] = current[path]
        for path, digest in st["texts"].items():
            check(sha256(current[path].encode()) == digest, (history["id"], st["id"], path, "text hash changed"))
    for path in tracked(history):
        texts[sha256(aeneas[path].encode())] = aeneas[path]
    return texts


def check_parses(corpus, history, texts):
    """Every intended text parses, and each step's current files parse exactly
    as the inventory declares."""
    for st in history["steps"]:
        for path in history["open"]:
            check(corpus.parseable(texts[st["intended"][path]]), (history["id"], st["id"], path, "intended text fails to parse"))
            expected = path in st["parses"]
            check(corpus.parseable(texts[st["texts"][path]]) == expected,
                  (history["id"], st["id"], path, "parses" if not expected else "fails to parse", "unlike its declaration"))


def check_oracle(history, record):
    """The intended program must hold every reviewed fact exactly."""
    for st in record["steps"]:
        for entry in st["probes"] + st["observers"]:
            if entry["status"] == "measured":
                check(entry["verdict"] == "exact", (history["id"], st["id"], entry["id"], "oracle fact failed",
                                                    entry.get("detail"), entry.get("reply")))


def stale_control(history, stale_record):
    """Score the stale program's intended answers against the renamed
    history's facts: each stale-sensitive probe must be wrong."""
    result = []
    for st, old in zip(history["steps"], stale_record["steps"]):
        for pr, answer in zip(st["probes"], old["probes"]):
            check(pr["id"] == answer["id"], (st["id"], pr["id"], answer["id"]))
            if not pr["stale_sensitive"]:
                continue
            v, why = verdict(pr["kind"], answer["reply"], pr["facts"])
            check(v == "wrong", (history["id"], st["id"], pr["id"], "stale answer was not rejected", v, answer["reply"]))
            result.append({"step": st["id"], "probe": pr["id"], "reply": answer["reply"], "verdict": v, "detail": why})
    check(result, "the stale-answer control rejected nothing")
    return result


def check_records(inventory, records, blocks, histories):
    """Refuse a capture that lacks a block, history, mode, or step, or whose
    steps are out of order or unmeasured where they must be measured."""
    seen = collections.Counter()
    by_id = {h["id"]: h for h in inventory["histories"]}
    for record in records:
        history = by_id[record["history"]]
        seen[(record["block"], record["history"], record["mode"])] += 1
        check([s["id"] for s in record["steps"]] == [s["id"] for s in history["steps"]],
              (record["history"], record["mode"], record["block"], "missing or reordered history steps"))
        for index, (row, st) in enumerate(zip(record["steps"], history["steps"])):
            if record["mode"] == "cold" and index == 0:
                check(row.get("status") == "not-run", (record["history"], "cold baseline must be not-run"))
                continue
            check(row.get("status") == "measured" and len(row.get("probes", [])) == len(st["probes"]) and
                  len(row.get("observers", [])) == len(st["observers"]),
                  (record["history"], record["mode"], record["block"], st["id"], "missing step evidence"))
            for entry in row["probes"] + row["observers"]:
                check(entry["status"] in ("measured",) + UNMEASURED, entry)
                if entry["status"] == "measured":
                    check(entry["verdict"] in VERDICTS, entry)
    wanted = {(b, h, m) for b in range(blocks) for h in histories for m in MODES}
    check(set(seen) == wanted and all(n == 1 for n in seen.values()), ("missing or duplicate history records",
                                                                      sorted(wanted - set(seen))))


def summarize(records):
    """Verdict counts per probe kind, state, and mode; per observer role and
    mode; and, per history step and mode, the verdicts across blocks with p50
    and p95 latencies."""
    kinds, roles = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    rows = collections.defaultdict(lambda: {"verdicts": collections.Counter(), "probe_ms": collections.defaultdict(list),
                                            "step_ms": [], "worker_replacements": 0})
    replacements = collections.Counter()
    for record in records:
        for row in record["steps"]:
            group = rows[(record["history"], row["id"], record["mode"])]
            if row.get("status") == "not-run":
                group["verdicts"]["not-run"] += 1
                continue
            group["step_ms"].append(row["step_latency_ms"])
            group["worker_replacements"] += row["worker_replacements"]
            replacements[record["mode"]] += row["worker_replacements"]
            for pr in row["probes"]:
                outcome = pr.get("verdict", pr["status"])
                kinds[(pr["kind"], row["state"], record["mode"])][outcome] += 1
                group["verdicts"][pr["id"] + ":" + outcome] += 1
                if "latency_ms" in pr:
                    group["probe_ms"][pr["id"]].append(pr["latency_ms"])
            for ob in row["observers"]:
                roles[(ob["role"], row["state"], record["mode"])][ob.get("verdict", ob["status"])] += 1

    def stats(values):
        values = sorted(values)
        return {"n": len(values), "p50": bench.percentile(values, .5), "p95": bench.percentile(values, .95)} if values else {
            "n": 0, "p50": None, "p95": None}

    return {
        "probes": [{"kind": k, "state": s, "mode": m, **counts} for (k, s, m), counts in sorted(kinds.items())],
        "observers": [{"role": r, "state": s, "mode": m, **counts} for (r, s, m), counts in sorted(roles.items())],
        "steps": [{"history": h, "step": s, "mode": m, "verdicts": dict(sorted(g["verdicts"].items())),
                   "probe_latency_ms": {p: stats(v) for p, v in sorted(g["probe_ms"].items())},
                   "step_latency_ms": stats(g["step_ms"]), "worker_replacements": g["worker_replacements"]}
                  for (h, s, m), g in rows.items()],
        "worker_replacements": dict(replacements)}


def consistency(records):
    """The probe and observer outcomes that differ between blocks."""
    outcomes = collections.defaultdict(set)
    for record in records:
        for row in record["steps"]:
            for entry in row.get("probes", []) + row.get("observers", []):
                outcomes[(record["history"], row["id"], record["mode"], entry["id"])].add(entry.get("verdict", entry["status"]))
    return [{"history": h, "step": s, "mode": m, "id": i, "outcomes": sorted(v)}
            for (h, s, m, i), v in sorted(outcomes.items()) if len(v) > 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "build/virgil-lsp")
    parser.add_argument("--output", default="-")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build/bench-results")
    parser.add_argument("--cpu", default="auto")
    parser.add_argument("--smoke", action="store_true",
                        help="the first two seed histories, one block; no build manifest required")
    parser.add_argument("--write-inventory", action="store_true", help="regenerate the frozen history inventory")
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.write_inventory:
        with tempfile.TemporaryDirectory() as temp:
            inventory = build(states.Corpus(None, Path(temp)))
        INVENTORY.write_text(pretty(json.loads(json.dumps(inventory))) + "\n")
        return
    args.exe = args.exe.resolve()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    affinity = bench.pin_cpu(args.cpu)
    blocks = 1 if args.smoke else BLOCKS
    result = {"schema": 1, "suite": "cursor-tolerance",
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "command": [sys.executable, *sys.argv], "capture": {"smoke": args.smoke, "blocks": blocks},
              "server_revision": bench.corpus.git_revision(ROOT, "rev-parse", "HEAD"),
              "git_status": bench.corpus.git_revision(ROOT, "status", "--short"), "host": bench.host_info(affinity),
              "inventory_sha256": sha256(INVENTORY.read_bytes()),
              "driver_sha256": {p.name: sha256(p.read_bytes()) for p in (
                  Path(__file__), Path(__file__).with_name("bench-tolerance.py"),
                  Path(__file__).with_name("tolerance-corpus.py"))},
              "builds": [bench.binary_info(args.exe, args.smoke)], "oracles": [], "records": []}
    transcripts = open(args.output + ".transcripts.jsonl", "w") if args.output != "-" else None
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="cursor-", dir=args.work_dir.resolve()) as temp:
            corpus = states.Corpus(args.exe, Path(temp))
            inventory = frozen(build(corpus))
            result["virgil_revision"] = inventory["virgil_revision"]
            check(inventory["virgil_revision"] == bench.corpus.git_revision(VIRGIL, "rev-parse", "HEAD"), "compiler pin changed")
            aeneas = aeneas_sources(corpus)
            histories = [h for h in inventory["histories"] if not args.smoke or h["id"] in SMOKE]
            check(len(histories) == (2 if args.smoke else 17), "history selection changed")
            work = Path(temp)
            prepared = {}
            for history in histories + ([] if args.smoke else [inventory["stale_control"]]):
                texts = texts_for(history, inventory, aeneas)
                check_parses(corpus, history, texts)
                prepared[history["id"]] = (project_for(history, inventory, work, aeneas), texts)
            for history in histories:
                project, texts = prepared[history["id"]]
                oracle = replay(args.exe, project, history, "oracle", texts, transcripts)
                check_oracle(history, oracle)
                result["oracles"].append(oracle)
            if not args.smoke:
                stale = inventory["stale_control"]
                project, texts = prepared[stale["id"]]
                record = replay(args.exe, project, stale, "oracle", texts, transcripts)
                third = next(h for h in histories if h["id"] == "third-damaged-body")
                result["stale_control"] = {"record": record, "rejected": stale_control(third, record)}
            for block in range(blocks):
                # Alternate the order of modes between blocks.
                order = MODES if block % 2 == 0 else tuple(reversed(MODES))
                for history in histories:
                    project, texts = prepared[history["id"]]
                    for mode in order:
                        result["records"].append(replay(args.exe, project, history, mode, texts, transcripts, block))
                print(f"cursor tolerance: block {block + 1} of {blocks}", file=sys.stderr)
            check_records(inventory, result["records"], blocks, [h["id"] for h in histories])
            corpus.verify_inputs()
            result["input_sha256"] = corpus.inputs
        result["wall_seconds"] = time.monotonic() - start
        result["summary"] = summarize(result["records"])
        result["summary"]["inconsistent_between_blocks"] = consistency(result["records"])
        counts = collections.Counter()
        for row in result["summary"]["probes"]:
            counts.update({k: v for k, v in row.items() if k in VERDICTS})
        print(f"cursor tolerance: {len(result['records'])} history records, "
              + ", ".join(f"{counts[v]} {v}" for v in VERDICTS) + f" probe answers, {result['wall_seconds']:.2f}s",
              file=sys.stderr)
    except BaseException as error:
        result["error"] = repr(error)
        result["failed_record"] = getattr(error, "tolerance_record", None)
        if args.output != "-":
            bench.write_json(args.output + ".failure.json", result)
        raise
    finally:
        if transcripts is not None:
            transcripts.close()
    bench.write_json(args.output, result)


if __name__ == "__main__":
    main()
