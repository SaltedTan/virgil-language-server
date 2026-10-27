# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The reviewed 45 repair states, 140 byte replay states, small controls, and
24 held-out states.

Only disposable copies are written. Parser invocations select/validate history;
all measured answers and oracles come from the stdio protocol.
"""
import importlib.util
import json
from pathlib import Path
import random
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "test/benchmarks/tolerance-seeds.json"
OBSERVER = """component ToleranceObserver {
 def stable(x: int) -> int { return x; }
 def check() -> int { return stable(1); }
 def bad() -> int { return "tolerance-error"; }
}
"""

# Each fixture's syntax error: a control or declaration header is `header`
# even inside a routine body; any other expression or statement is `body`.
REPAIR_EDIT_KINDS = {
    "call-after-string-and-comment": "body",
    "call-empty-arguments": "body",
    "call-generic": "body",
    "call-nested-missing-paren": "body",
    "call-open-arguments": "body",
    "call-trailing-comma": "body",
    "else-branch": "body",
    "if-condition-open": "header",
    "index-open": "body",
    "local-type": "header",
    "member-end-of-statement": "body",
    "member-in-component-field": "body",
    "member-in-condition": "header",
    "member-in-field-initializer": "body",
    "member-in-lambda": "body",
    "member-in-match-arm": "body",
    "member-mid-expression": "body",
    "member-of-call-result": "body",
    "member-of-this": "body",
    "member-of-type": "body",
    "member-with-prefix": "body",
    "missing-brace-end-of-method": "body",
    "missing-semicolon": "body",
    "return-expression": "body",
    "statement-keyword-prefix": "body",
    "syntax-error-after-cursor": "body",
    "syntax-error-elsewhere-in-file": "body",
    "syntax-error-in-other-file": "body",
    "type-error-elsewhere-in-file": "body",
    "type-error-in-other-file": "body",
    "var-initializer": "body",
}


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


repair = load("repair-corpus")
corpus = load("bench-corpus")


def query(path, text, anchor, token):
    assert text.count(anchor) == 1, (path, anchor)
    at = text.index(anchor) + anchor.index(token)
    return {"path": path, "position": corpus.position(text, at), "token": token}


# Held-out states for body blanking (#312, ADR-0010 rule 1), which the rule
# wasn't built from: the edit kinds it covers, chosen with a fixed seed in
# Aeneas files that no other state edits or queries.
HELDOUT_SEED = 312
HELDOUT_KINDS = ("next-line", "body-end", "two-bodies")
HELDOUT_PER_KIND = 8
MEMBER_ACCESS = re.compile(r"\.[A-Za-z_]")


def method_bodies(lines):
    """Each method or constructor body of a tab-indented Aeneas file, as the
    index of its header line, which ends in "{", and of its closing "}" line,
    both at one tab."""
    result, start = [], None
    for i, line in enumerate(lines):
        if re.match(r"\t(private )?(def|new)\b.*\{$", line):
            start = i
        elif line == "\t}" and start is not None:
            result.append((start, i))
            start = None
    return result


def statement(line):
    """A statement line of a body with a member access to cut, and no
    literal or comment whose text could hold one."""
    return (re.match(r"\t\t+[A-Za-z_]", line) is not None and line.endswith(";")
            and not any(c in line for c in "\"'/") and MEMBER_ACCESS.search(line) is not None)


def unfinished(rng, line):
    """{line} cut just after the "." of one of its member accesses, chosen by
    {rng}, so that the expression is unfinished at the line's end."""
    return line[:rng.choice([m.start() for m in MEMBER_ACCESS.finditer(line)]) + 1]


class Corpus:
    def __init__(self, exe, work):
        self.exe, self.work = exe, work
        self.parsed = {}
        self.inputs = {"test/benchmarks/tolerance-seeds.json": corpus.sha256(SEEDS.read_bytes())}

    def parseable(self, text):
        digest = corpus.sha256(text.encode())
        if digest not in self.parsed:
            path = self.work / "parse-input.v3"
            path.write_bytes(text.encode())
            result = subprocess.run([str(self.exe), "parse", str(path)], capture_output=True, timeout=30)
            assert result.returncode in (0, 1), (result.returncode, result.stderr.decode())
            self.parsed[digest] = result.returncode == 0
        return self.parsed[digest]

    def remember(self, path):
        path = Path(path)
        data = path.read_bytes()
        self.inputs[str(path.relative_to(ROOT))] = corpus.sha256(data)
        return data.decode()

    def project(self, name, sources):
        base = self.work / name
        sources = {"Observer.v3": OBSERVER, **sources}
        for rel, text in sources.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode())
        config = {"version": 1, "projects": [{"name": "tolerance", "sources": list(sources)}]}
        (base / ".virgil-lsp.json").write_text(json.dumps(config))
        queries = [query("Observer.v3", OBSERVER, "stable(1)", "stable")]
        return {"name": name, "base": base, "sources": sources, "config": config, "queries": queries}

    def state(self, project, name, suite, kind, predecessor, current, policy, **extra):
        assert all(self.parseable(text) for text in predecessor.values()), (name, "invalid predecessor")
        # The edited file remains excluded even when reinserting the last byte
        # restores the original seed exactly. It is still this sequence's site.
        changed = list(current)
        # Queries always select intact files, except the deliberately changed
        # lexical scope in the shadowing safety control.
        queries = [q for q in project["queries"] if q["path"] not in changed]
        return {"id": name, "suite": suite, "edit_kind": kind, "project": project,
                "predecessor": predecessor, "current": current, "changed": changed,
                "seed_policy": policy, "queries": queries,
                "parseable_current": {p: self.parseable(t) for p, t in current.items()}, **extra}

    def fixtures(self):
        seeds = json.loads(SEEDS.read_text())
        cases = repair.fixture_cases()
        assert set(seeds) == {name for name, _ in cases}, "fixture seed inventory changed"
        assert set(REPAIR_EDIT_KINDS) == set(seeds), "fixture edit-kind inventory changed"
        for name, files in cases:
            seed = seeds[name]
            assert set(seed["files"]) == {str(Path(p).relative_to(ROOT)) for p in files}, name
            current, previous = {}, {}
            for filename in files:
                path = Path(filename)
                raw = self.remember(path)
                assert self.inputs[str(path.relative_to(ROOT))] == seed["files"][str(path.relative_to(ROOT))]
                current[path.name] = raw.replace("<|>", "", 1)
                previous[path.name] = raw.replace("<|>", seed["insertion"], 1).replace("var x = ;", "var x = 0;")
            project = self.project("fixtures/" + name, previous)
            yield self.state(project, name, "repair", REPAIR_EDIT_KINDS[name], previous, current, seed["policy"])

    def aeneas(self, name):
        """The Aeneas program, with fixed bound query sites in three of its files."""
        sources = {str(Path(p).relative_to(ROOT / "vendor/virgil")): self.remember(p)
                   for p in repair.aeneas_files()}
        project = self.project(name, sources)
        anchors = [("aeneas/src/util/Paths.v3", "StringBuilder.new", "StringBuilder"),
                   ("aeneas/src/util/Char.v3", "properties[ch]", "properties"),
                   ("lib/util/StringBuilder.v3", "Arrays.", "Arrays")]
        # Fixed bound sites, checked by the protocol oracle before measurement.
        for path, anchor, token in anchors:
            text = sources[path]
            at = text.index(anchor)
            project["queries"].append({"path": path, "position": corpus.position(text, at), "token": token})
        return project, sources

    def large(self):
        self.remember(ROOT / "test/fixtures/repair/aeneas-sites.txt")
        project, sources = self.aeneas("aeneas")
        sites = []
        replay = {"aeneas-member-of-component", "aeneas-call-nested", "aeneas-condition", "aeneas-for-header"}
        for name, rel, cut, needle in repair.aeneas_cases():
            path = "aeneas/src/" + rel
            original = sources[path]
            at = original.index(needle.replace("|", "", 1)) + len(needle.split("|")[0])
            end = original.index("\n", at) if cut == "line" else len(original)
            # aeneas-member-chain cuts a member access inside an if condition.
            kind = "whole_file" if cut == "file" else "header" if name in (
                "aeneas-condition", "aeneas-for-header", "aeneas-member-chain") else "body"
            yield self.state(project, name, "repair", kind, {path: original},
                             {path: original[:at] + original[end:]}, "original upstream file")
            if name in replay:
                sites.append((name, path, at, end, kind))
        for filename in ("StringBuilder.v3", "Vector.v3"):
            path = "lib/util/" + filename
            at = sources[path].index("Arrays.") + len("Arrays.")
            sites.append(("util-" + filename, path, at, sources[path].index("\n", at), "body"))
        count = 0
        for name, path, at, end, kind in sites:
            original = sources[path]
            previous = original
            # These pinned suffixes are ASCII, so character insertion is byte insertion.
            assert original[at:end].isascii()
            for n in range(end - at + 1):
                text = original[:at + n] + original[end:]
                yield self.state(project, name + "/" + str(n), "replay", kind,
                                 {path: previous}, {path: text}, "most recent parseable replay version",
                                 prefix_bytes=n, suffix_bytes=end - at)
                if self.parseable(text):
                    previous = text
                count += 1
        assert count == 140, ("replay inventory changed", count)

    def error_line(self, text):
        """The line of the first syntax error that the CLI parser reports in
        {text}, counting from 1, or None if it parses."""
        path = self.work / "parse-input.v3"
        path.write_bytes(text.encode())
        result = subprocess.run([str(self.exe), "parse", str(path)], capture_output=True, timeout=30)
        assert result.returncode in (0, 1), (result.returncode, result.stderr.decode())
        if result.returncode == 0:
            return None
        return int(re.search(r":(\d+):\d+: ", result.stdout.decode()).group(1))

    def heldout(self):
        """HELDOUT_PER_KIND states of each kind in HELDOUT_KINDS, one file each:
        a member access unfinished at a line's end that the next statement
        line continues, so that the parser's first error is on a later line;
        one unfinished on a body's last line, before its closing brace; and
        one in each of two bodies. Each cut alone is a syntax error in its own
        body. The intended program is the upstream file."""
        project, sources = self.aeneas("heldout")
        used = {"aeneas/src/" + rel for _, rel, _, _ in repair.aeneas_cases()}
        used |= {q["path"] for q in project["queries"]} | {"aeneas/src/arm64/Arm64AddrPatcher.v3"}
        files = sorted(p for p in sources if p.startswith("aeneas/src/") and p not in used)
        rng = random.Random(HELDOUT_SEED)
        rng.shuffle(files)
        counts = dict.fromkeys(HELDOUT_KINDS, 0)
        for path in files:
            pending = sorted((k for k in HELDOUT_KINDS if counts[k] < HELDOUT_PER_KIND), key=lambda k: counts[k])
            if not pending:
                break
            original = sources[path]
            lines = original.split("\n")
            bodies = method_bodies(lines)
            for kind in pending:
                edits = self.heldout_edit(rng, kind, lines, bodies)
                if edits is None:
                    continue
                current = list(lines)
                for n, (b, e) in edits:
                    current[n] = unfinished(rng, lines[n])
                    # Lines count from 1 here, from 0 in {lines}.
                    alone = self.error_line("\n".join(lines[:n] + [current[n]] + lines[n + 1:]))
                    if alone is None or not b + 1 < alone <= e + 1 or (kind == "next-line" and alone <= n + 1):
                        break
                else:
                    counts[kind] += 1
                    yield self.state(project, kind + "/" + path.removeprefix("aeneas/src/"), "heldout", "body",
                                     {path: original}, {path: "\n".join(current)}, "original upstream file",
                                     heldout_kind=kind, edited_lines=[n + 1 for n, _ in edits])
                    break
        assert all(n == HELDOUT_PER_KIND for n in counts.values()), ("held-out inventory changed", counts)

    def heldout_edit(self, rng, kind, lines, bodies):
        """The lines to cut for {kind}, each by index with its body, or None if
        the file has none."""
        if kind == "next-line":
            candidates = [(n, (b, e)) for b, e in bodies for n in range(b + 1, e - 1)
                          if statement(lines[n]) and re.match(r"\t\t+[A-Za-z_]", lines[n + 1])]
            return [rng.choice(candidates)] if candidates else None
        if kind == "body-end":
            candidates = [(e - 1, (b, e)) for b, e in bodies if e - 1 > b and statement(lines[e - 1])]
            return [rng.choice(candidates)] if candidates else None
        per_body = [[(n, (b, e)) for n in range(b + 1, e) if statement(lines[n])] for b, e in bodies]
        per_body = [c for c in per_body if c]
        if len(per_body) < 2:
            return None
        first, second = sorted(rng.sample(range(len(per_body)), 2))
        return [rng.choice(per_body[first]), rng.choice(per_body[second])]

    def controls(self):
        a = "component A { def f(x: int) -> int { return x; } }\n"
        b = "component B { def f() -> int { return A.f(1); } }\n"
        c = "component C { def f() -> int { return 1; } }\n"
        source = {"A.v3": a, "B.v3": b, "C.v3": c}
        for name, fixed in (
                ("deleted-declaration", {"A.v3": "component A { }\n"}),
                ("changed-signature", {"A.v3": a.replace("x: int", "x: bool").replace("return x;", "return 1;")}),
                ("shadowing-local", {"B.v3": b.replace("return A.f", "var A = 3; return A.f")})):
            project = self.project("negative/" + name, source)
            path = next(iter(fixed))
            broken = fixed[path].rstrip()[:-1] + " def broken( { }\n" if path == "A.v3" else fixed[path].replace("; } }", "; var bad = ; } }")
            q = query("B.v3", fixed.get("B.v3", b), "A.f(1)", "f")
            state = self.state(project, name, "negative", "header" if path == "A.v3" else "body",
                               {path: source[path]}, {path: broken}, "original small program",
                               oracle=fixed, allow_absent=True)
            state["queries"] = [q]
            state["stale_queries"] = [query("B.v3", b, "A.f(1)", "f")]
            yield state
        # An insertion inside the old body can move unchanged later members
        # to another component. Brace containment must hold in both texts.
        original = ("component A {\n    def broken() { var x = 0; }\n"
                    "    def target() -> int { return 1; }\n}\n")
        caller = "component B { def use() -> int { return A.target(); } }\n"
        fixed = original.replace("{ var x = 0;", "{ } } component C { def broken() { var x = 0;")
        broken = fixed.replace("var x = 0;", "var malformed = ; var x = 0;")
        project = self.project("negative/body-boundary-escape", {"A.v3": original, "B.v3": caller})
        state = self.state(project, "body-boundary-escape", "negative", "body",
                           {"A.v3": original}, {"A.v3": broken}, "original small program",
                           oracle={"A.v3": fixed}, allow_absent=True)
        state["queries"] = state["stale_queries"] = [query("B.v3", caller, "A.target()", "target")]
        yield state
        # Removed body errors must not leave a foreign ErrorLimit notice at
        # the last visible error in an intact file. Navigation stays bound.
        # The benchmark's Observer supplies the fourteenth error before B.
        errors = ''.join('var x%d: int = true; ' % n for n in range(13))
        original = ('component A { def target() -> int { return 1; } '
                    'def broken() { ' + errors + '} }\n')
        caller = ('component B { def use() -> int { return A.target(); } '
                  'def wrong() -> int { return "bad"; } }\n')
        project = self.project("negative/excluded-error-limit", {"A.v3": original, "B.v3": caller})
        state = self.state(project, "excluded-error-limit", "negative", "body",
                           {"A.v3": original}, {"A.v3": original.replace(errors, 'var syntax = ; ')},
                           "original small program with body type errors",
                           oracle={"A.v3": original.replace(errors, 'var syntax = 1; ')}, allow_absent=True)
        state["queries"] = state["stale_queries"] = [query("B.v3", caller, "A.target()", "target")]
        yield state
        # Current-text repair (#293) must refuse a site that needs a brace,
        # lies in a declaration header or a field initializer, or is one of
        # sites in two bodies, and must keep a repaired call's arity and type
        # inside its excluded body. Each oracle also changes target's
        # signature, so the predecessor's answers are wrong.
        original = ("component A {\n    def broken() { var x = 0; }\n    def other() { var y = 0; }\n"
                    "    def target() -> int { return 1; }\n}\n")
        caller = "component B { def use() -> int { return A.target(); } }\n"
        changed = original.replace("def target() -> int { return 1; }", "def target() -> bool { return true; }")
        repairs = (
            # Only a "}" completes the body. One there would end it early.
            ("repair-needs-brace", "body", changed.replace("{ var x = 0; }", "{ if (true) { var x = ;\n    }"),
             changed.replace("{ var x = 0; }", "{ if (true) { var x = 1;\n    } }")),
            ("repair-in-header", "header", original.replace("def target()", "def target(limit: )"),
             original.replace("def target()", "def target(limit: int)")),
            ("repair-in-field-initializer", "body", changed.replace("    def target", "    def pair = (1, true);\n    def limit = pair.;\n    def target"),
             changed.replace("    def target", "    def pair = (1, true);\n    def limit = pair.1;\n    def target")),
            ("repair-in-two-bodies", "body", changed.replace("var x = 0;", "var x = ;").replace("var y = 0;", "var y = ;"),
             changed.replace("var x = 0;", "var x = 1;").replace("var y = 0;", "var y = 1;")),
            # The repaired call target(1,<sentinel>) has two arguments; the
            # intended call, and the caller's, have other arities and types.
            ("repair-call-arity", "body",
             original.replace("def target() -> int { return 1; }", "def target(a: int, b: bool) -> int { return a; }")
                     .replace("var x = 0;", "var x = target(1, ;"),
             original.replace("def target() -> int { return 1; }", "def target(a: int, b: bool) -> int { return a; }")
                     .replace("var x = 0;", "var x = target(1, true, 3);")),
        )
        # Body blanking (#312) excludes both bodies of the two-body control
        # and the arity control's one body, and refuses the other three.
        bodies = {"repair-in-two-bodies": 2, "repair-call-arity": 1}
        for name, kind, broken, fixed in repairs:
            # The arity control's predecessor already calls target in the
            # body, so its reference set differs only by the binding.
            previous = original.replace("var x = 0;", "var x = target();") if name == "repair-call-arity" else original
            project = self.project("negative/" + name, {"A.v3": previous, "B.v3": caller})
            state = self.state(project, name, "negative", kind, {"A.v3": previous}, {"A.v3": broken},
                               "original small program", oracle={"A.v3": fixed}, allow_absent=True,
                               expected_bodies={"A.v3": bodies.get(name, 0)})
            state["queries"] = state["stale_queries"] = [query("B.v3", caller, "A.target()", "target")]
            yield state
        # Body blanking (#312) must refuse a body with an unterminated string
        # and a method whose header line was deleted, which leaves an extra
        # "}", and an emptied body's missing return must count toward the
        # error limit: here it is the fifteenth error, after the Observer's
        # and 13 in A, so verification never reaches target or B, whose
        # answers are missing, and the excluded error suppresses the limit
        # notice. Each oracle changes target's signature, so the
        # predecessor's answers are wrong and fallback can't substitute.
        original = ("component A {\n    def broken() {\n        var x = 0;\n    }\n"
                    "    def other() {\n        var y = 0;\n    }\n    def target() -> int { return 1; }\n}\n")
        changed = original.replace("def target() -> int { return 1; }", "def target() -> bool { return true; }")
        errors = "".join("var x%d: int = true; " % n for n in range(13))
        limit = ("component A { def errors() { " + errors + "} def broken() -> int { return 1; } "
                 "def target() -> int { return 1; } }\n")
        limit_changed = limit.replace("def target() -> int { return 1; }", "def target() -> bool { return true; }")
        blanking = (
            ("repair-unterminated-string", original,
             changed.replace("var x = 0;", 'var s = "open;\n        var x = 0;'),
             changed.replace("var x = 0;", 'var s = "open";\n        var x = 0;'), 0, False),
            ("repair-deleted-header", original, changed.replace("    def other() {\n", ""),
             changed.replace("    def other() {\n        var y = 0;\n    }\n", ""), 0, False),
            ("repair-missing-return-limit", limit,
             limit_changed.replace("{ return 1; } def target", "{ var syntax = ; return 1; } def target"),
             limit_changed.replace("{ return 1; } def target", "{ var syntax = 1; return 1; } def target"), 1, True),
        )
        for name, previous, broken, fixed, count, reached in blanking:
            project = self.project("negative/" + name, {"A.v3": previous, "B.v3": caller})
            state = self.state(project, name, "negative", "body", {"A.v3": previous}, {"A.v3": broken},
                               "original small program", oracle={"A.v3": fixed}, allow_absent=True,
                               expected_bodies={"A.v3": count}, expected_limit=reached)
            state["queries"] = state["stale_queries"] = [query("B.v3", caller, "A.target()", "target")]
            yield state
        # Three variants per boundary, including distinct fresh diagnostic kinds.
        for variant in (0, 1, 2):
            broken = a.replace("return x;", "return x.;")
            fresh = (c.replace("return 1;", 'return "bad";'),
                     c.replace("return 1;", "return false;"), c.replace("-> int", "-> bool"))[variant]
            scenarios = [
                ("two-broken-files", {"A.v3": broken, "C.v3": c.replace("return 1;", "return ;.")}, {}),
                ("fresh-type-error", {"A.v3": broken, "C.v3": fresh}, {"oracle": {"C.v3": fresh}}),
                ("crlf" if variant == 1 else "utf16", {"A.v3": broken}, {}),
                ("closed-file-on-disk", {"A.v3": broken}, {"disk": True}),
                ("project-file-change", {"A.v3": broken}, {"project_change": True}),
            ]
            for category, current, options in scenarios:
                name = category + "/" + str(variant)
                texts = dict(source)
                if category == "utf16":
                    prefix = "/* é😀 */" if variant == 0 else "/* 𐐀β */"
                    texts["B.v3"] = b.replace("return A.f", prefix + " return A.f")
                if category == "crlf":
                    # CRLF is itself a syntax error in the pinned compiler.
                    current["B.v3"] = b.replace("\n", "\r\n")
                if variant == 1 and category in ("two-broken-files", "closed-file-on-disk"):
                    current["A.v3"] = "component A { def f( {\n"
                if variant == 2 and category in ("two-broken-files", "closed-file-on-disk"):
                    current["A.v3"] = a.replace("x: int", "x: ")
                project = self.project("extra/" + name, texts)
                project["queries"].append(query("B.v3", texts["B.v3"], "A.f(1)", "f"))
                previous = {p: texts[p] for p in current}
                kind = "body"
                if variant == 1 and category == "closed-file-on-disk":
                    kind = "whole_file"
                elif variant == 2 and category in ("two-broken-files", "closed-file-on-disk"):
                    kind = "header"
                yield self.state(project, name, "extra", kind,
                                 previous, current, "original small program", **options)

    def verify_inputs(self):
        for path, digest in self.inputs.items():
            assert corpus.sha256((ROOT / path).read_bytes()) == digest, (path, "benchmark mutated input")
