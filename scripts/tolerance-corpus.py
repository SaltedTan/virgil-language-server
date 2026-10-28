# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The reviewed 45 repair states, 140 byte replay states, small controls, and
94 held-out states.

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


# Held-out states for the brace rules (#317, ADR-0010 rules 2 and 3), which
# they weren't built from: a "{" removed from the end of a control or method
# header or a "}" from a line of its own, chosen with a fixed seed in Aeneas
# files that no other state, including the held-out states above, edits or
# queries. The upstream files are indented with tabs. "spaces" re-indents a
# file with four spaces for each leading tab, "mixed" replaces the leading
# tabs of about half the lines with eight spaces each, which keeps every
# line's width, and "mixed-width" with four, which doesn't. Rule 3 is left for
# #320, so the states that lost a "}" are refused.
BRACE_SEED = 317
BRACE_KINDS = ("if-opener", "loop-opener", "match-opener", "method-opener", "block-closer", "method-closer")
BRACE_PER_STYLE = {"tabs": 2, "spaces": 2, "mixed": 2, "mixed-width": 1}
BRACE_OPENERS = {"if-opener": re.compile(r"\t\t+if \(.*\) \{$"),
                 "loop-opener": re.compile(r"\t\t+(while|for) \(.*\) \{$"),
                 "match-opener": re.compile(r"\t\t+match \(.*\) \{$"),
                 "block-closer": re.compile(r"\t\t+\}$")}


# Held-out states for a lost "}" (#320, ADR-0010 rule 3), which the rule
# wasn't built from: the "}" of a line of its own in a method, an inner
# block's or the method's own, removed with the seed 320 in Aeneas files that
# no other state, including the held-out states above, edits or queries, in
# the four indentation styles above.
CLOSER_SEED = 320
CLOSER_PER_KIND = {"block-closer": 5, "method-closer": 2}
CLOSER_STYLES = ("tabs", "spaces", "mixed", "mixed-width")


def brace_candidates(kind, lines, bodies):
    """The indexes of the lines of {kind} in the methods {bodies}: their
    headers, their closing lines, or the lines inside them that match."""
    if kind == "method-opener":
        return [b for b, _ in bodies]
    if kind == "method-closer":
        return [e for _, e in bodies]
    return [n for b, e in bodies for n in range(b + 1, e) if BRACE_OPENERS[kind].match(lines[n])]


def reindent(rng, lines, style):
    """{lines} in the indentation {style}, choosing mixed lines by {rng}."""
    if style == "tabs":
        return list(lines)
    result = []
    for line in lines:
        tabs = len(line) - len(line.lstrip("\t"))
        if tabs and (style == "spaces" or rng.random() < 0.5):
            line = " " * (tabs * (8 if style == "mixed" else 4)) + line[tabs:]
        result.append(line)
    return result


def diagnostic_paths(state):
    """The files whose diagnostics a state's answers include: its queries'
    files and Observer.v3, C.v3 for the fresh type errors, and the newly
    discovered Added.v3 for the third project change."""
    paths = {q["path"] for q in state["queries"]} | {"Observer.v3"}
    if state["id"].startswith("fresh-type-error"):
        paths.add("C.v3")
    if state.get("project_change") and not state["id"].endswith(("0", "1")):
        paths.add("Added.v3")
    return paths


def oracle_key(state):
    """Identify the intended-text oracle that a state measures.

    States with the same project, overlays, queries, and diagnostic paths read
    the same intended text, so `bench-tolerance.py` captures their oracle once;
    replay states whose last parseable version is the same share one this way.
    """
    paths = diagnostic_paths(state)
    overlays = {**state["predecessor"], **state.get("oracle", {})}
    return (str(state["project"]["base"]), json.dumps(overlays, sort_keys=True),
            json.dumps(state["queries"], sort_keys=True), tuple(sorted(paths)))


class Corpus:
    def __init__(self, exe, work, parse=None):
        # {parse} replaces the compiler's parse oracle with recorded verdicts so
        # that the inventory check needs no built server: it maps a text to the
        # line of its first syntax error, or to None when the text parses.
        self.exe, self.work, self.parse = exe, work, parse
        self.parsed = {}
        self.inputs = {"test/benchmarks/tolerance-seeds.json": corpus.sha256(SEEDS.read_bytes())}

    def error_line(self, text):
        """The line of the first syntax error that the CLI parser reports in
        {text}, counting from 1, or None if it parses."""
        digest = corpus.sha256(text.encode())
        if digest not in self.parsed:
            self.parsed[digest] = self.parse(text) if self.parse else self.compiler_error_line(text)
        return self.parsed[digest]

    def compiler_error_line(self, text):
        path = self.work / "parse-input.v3"
        path.write_bytes(text.encode())
        result = subprocess.run([str(self.exe), "parse", str(path)], capture_output=True, timeout=30)
        assert result.returncode in (0, 1), (result.returncode, result.stderr.decode())
        if result.returncode == 0:
            return None
        return int(re.search(r":(\d+):\d+: ", result.stdout.decode()).group(1))

    def parseable(self, text):
        return self.error_line(text) is None

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
        chosen = set()
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
                    chosen.add(path)
                    yield self.state(project, kind + "/" + path.removeprefix("aeneas/src/"), "heldout", "body",
                                     {path: original}, {path: "\n".join(current)}, "original upstream file",
                                     heldout_kind=kind, edited_lines=[n + 1 for n, _ in edits])
                    break
        assert all(n == HELDOUT_PER_KIND for n in counts.values()), ("held-out inventory changed", counts)
        brace = list(self.brace_heldout(project, sources, used | chosen))
        yield from brace
        yield from self.closer_heldout(project, sources, used | chosen | {p for s in brace for p in s["changed"]})

    def brace_heldout(self, project, sources, used):
        """BRACE_PER_STYLE[style] states of each kind in BRACE_KINDS for each
        indentation style, one file each, none of them in {used}: one "{" or
        "}" removed from the end of a line of a method, chosen by the seed,
        whose removal alone is a syntax error. The intended program is the
        upstream file in that style."""
        files = sorted(p for p in sources if p.startswith("aeneas/src/") and p not in used)
        rng = random.Random(BRACE_SEED)
        rng.shuffle(files)
        wanted = [(style, kind) for style in BRACE_PER_STYLE for kind in BRACE_KINDS]
        counts = dict.fromkeys(wanted, 0)
        for path in files:
            pending = sorted((w for w in wanted if counts[w] < BRACE_PER_STYLE[w[0]]), key=lambda w: counts[w])
            if not pending:
                break
            lines = sources[path].split("\n")
            bodies = method_bodies(lines)
            for style, kind in pending:
                candidates = brace_candidates(kind, lines, bodies)
                if not candidates:
                    continue
                n = rng.choice(candidates)
                intended = reindent(rng, lines, style)
                current = intended[:n] + [intended[n][:-1]] + intended[n + 1:]
                if self.error_line("\n".join(current)) is None:
                    continue
                counts[(style, kind)] += 1
                yield self.state(project, "brace-" + style + "/" + kind + "/" + path.removeprefix("aeneas/src/"),
                                 "heldout", "header" if kind.endswith("opener") else "body",
                                 {path: "\n".join(intended)}, {path: "\n".join(current)},
                                 "original upstream file" if style == "tabs" else "upstream file, re-indented",
                                 heldout_kind=kind, indentation=style, edited_lines=[n + 1])
                break
        assert all(counts[w] == BRACE_PER_STYLE[w[0]] for w in wanted), ("brace held-out inventory changed", counts)

    def closer_heldout(self, project, sources, used):
        """CLOSER_PER_KIND[kind] states of each kind for each style in
        CLOSER_STYLES, one file each, none of them in {used}: the "}" of a
        line of its own in a method removed, chosen by the seed, whose removal
        alone is a syntax error. The intended program is the upstream file in
        that style."""
        files = sorted(p for p in sources if p.startswith("aeneas/src/") and p not in used)
        rng = random.Random(CLOSER_SEED)
        rng.shuffle(files)
        wanted = [(style, kind) for style in CLOSER_STYLES for kind in CLOSER_PER_KIND]
        counts = dict.fromkeys(wanted, 0)
        for path in files:
            pending = sorted((w for w in wanted if counts[w] < CLOSER_PER_KIND[w[1]]), key=lambda w: counts[w])
            if not pending:
                break
            lines = sources[path].split("\n")
            bodies = method_bodies(lines)
            for style, kind in pending:
                candidates = brace_candidates(kind, lines, bodies)
                if not candidates:
                    continue
                n = rng.choice(candidates)
                intended = reindent(rng, lines, style)
                current = intended[:n] + [intended[n][:-1]] + intended[n + 1:]
                if self.error_line("\n".join(current)) is None:
                    continue
                counts[(style, kind)] += 1
                yield self.state(project, "closer-" + style + "/" + kind + "/" + path.removeprefix("aeneas/src/"),
                                 "heldout", "body", {path: "\n".join(intended)}, {path: "\n".join(current)},
                                 "original upstream file" if style == "tabs" else "upstream file, re-indented",
                                 heldout_kind=kind, indentation=style, edited_lines=[n + 1])
                break
        assert all(counts[w] == CLOSER_PER_KIND[w[1]] for w in wanted), ("closer held-out inventory changed", counts)

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
        yield from self.brace_controls(caller)
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

    def brace_controls(self, caller):
        """Controls for the brace rules (#317, ADR-0010 rules 2 and 3). Rule 2
        reads a file's layout when its braces are one opener short. Rule 3
        (#320) empties a body when the braces are one closer short only where
        every place for the lost "}" that parses leaves it in that body, before
        the body's own "}"; the rule-3 controls from #317 are refused. Each is
        a small tab-indented program; as for the other repair controls, each
        oracle changes target's signature, so the predecessor's answers are
        wrong."""
        def text(*rows):
            return "\n".join(rows) + "\n"
        new = "\tdef target() -> bool { return true; }"
        helpers = ("\tdef a() -> bool { return true; }", "\tdef g() { }")
        errors = "\t\t" + "".join("var x%d: int = true; " % n for n in range(13))
        if_block = ("\t\tif (a()) {", "\t\t\tg();", "\t\t}")

        def holder(name, header):
            # Review round 3's program: h lost its "{" right before a ";", so
            # it parses without a body, and its local function, as deep as h,
            # reads as a member whose if lost its "{". A comment or a line
            # break in h's header must not hide it. target's local() would
            # bind to the local function instead of the file's.
            rows = ("    def local() {", "            if (true)", "                g();", "            }",
                    "        var count = 7;", "    }", "    def target() -> bool { return local() == 11; }",
                    "    def g() { }", "}")
            lost = header[:-1] + (header[-1].replace("{;", ";"),)
            return (name, "header", text("def local() -> int { return 11; }", "component A {", *header, *rows),
                    text("def local() -> int { return 11; }", "component A {", *lost, *rows), None)
        controls = (
            # An if loses its "{" in a method that is followed by a field and
            # methods and has no "}" of its own at its indentation, as in
            # ADR-0010's swallow-field: a "{" after the if parses, with the
            # field and methods inside the method. The text can't say which
            # brace was lost, so this must be refused.
            ("brace-swallow-field", "header",
             text("component A {", "\tdef f() {", *if_block, "\t}", "\tvar x = 1;", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tif (a())", "\t\t\tg();", "\t\t}", "\tvar x = 1;", new,
                  *helpers, "}", "}"), None),
            # The if's "}" lost its indentation with its "{": two closers at
            # one indentation.
            ("brace-two-closers", "header",
             text("component A {", "\tdef f() {", *if_block, "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tif (a())", "\t\t\tg();", "\t}", "\t}", new, *helpers, "}"), None),
            # The method's header is split over two lines.
            ("brace-split-header", "header",
             text("component A {", "\tdef f(p: int,", "\t\tq: int) {", "\t\tif (p > q) {", "\t\t\tg();", "\t\t}", "\t}",
                  new, *helpers, "}"),
             text("component A {", "\tdef f(p: int,", "\t\tq: int) {", "\t\tif (p > q)", "\t\t\tg();", "\t\t}", "\t}",
                  new, *helpers, "}"), None),
            # A line of the damaged body is no deeper than its header.
            ("brace-indentation-disagrees", "header",
             text("component A {", "\tdef f() {", "\t\twhile (a()) {", "\t\t\tg();", "\t\t}", "\t\tg();", "\t}",
                  new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\twhile (a())", "\t\t\tg();", "\t\t}", "\tg();", "\t}",
                  new, *helpers, "}"), None),
            # A method header loses its "{" below a method whose header is
            # indented with four spaces and its "}" with a tab. Blanking from
            # that header to the four-space "}" would parse, and would drop
            # other, which B calls.
            ("brace-mixed-width", "header",
             text("component A {", "    def first() {", "\t\tg();", "\t}", "\tdef other() {", "\t\tg();", "    }",
                  new, *helpers, "}"),
             text("component A {", "    def first() {", "\t\tg();", "\t}", "\tdef other() ", "\t\tg();", "    }",
                  new, *helpers, "}"),
             "component B { def use() -> int { A.other(); return A.target(); } }\n"),
            # A string left open in the damaged body.
            ("brace-unterminated-string", "header",
             text("component A {", "\tdef f() {", *if_block, '\t\tvar s = "open";', "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tif (a())", "\t\t\tg();", "\t\t}", '\t\tvar s = "open;', "\t}",
                  new, *helpers, "}"), None),
            # A method loses its "}", and its last statement, indented like the
            # others, is a field of the component in the intended program: no
            # layout says where the method ends, so this must be refused.
            ("brace-closer-deeper-field", "body",
             text("component A {", "\tdef f() {", "\t\tg();", "\t}", "\t\tvar count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tg();", "\t", "\t\tvar count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             "component B { def use() -> int { if (A.count > 0) return A.target(); return 0; } }\n"),
            # A legal if without braces, then a while that lost its "{".
            ("brace-braceless-if-then-while", "header",
             text("component A {", "\tdef f() {", "\t\tif (a())", "\t\t\tg();", "\t\twhile (a()) {", "\t\t\tg();",
                  "\t\t}", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tif (a())", "\t\t\tg();", "\t\twhile (a())", "\t\t\tg();",
                  "\t\t}", "\t}", new, *helpers, "}"), None),
            # An inner block loses its "}".
            ("brace-inner-closer", "body",
             text("component A {", "\tdef f() {", *if_block, "\t\tg();", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tif (a()) {", "\t\t\tg();", "\t\t", "\t\tg();", "\t}",
                  new, *helpers, "}"), None),
            # A method loses its own "}".
            ("brace-method-closer", "body",
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t", new, *helpers, "}"), None),
            # Header and body lines indented with tabs and spaces of equal width.
            ("brace-tab-stops", "header",
             text("component A {", "\tdef f() {", "\t        if (a()) {", "\t\t\tg();", "                }",
                  "        }", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t        if (a())", "\t\t\tg();", "                }",
                  "        }", new, *helpers, "}"), None),
            # A brace rule's body and an error in another body, before it.
            ("brace-and-blank-two-bodies", "header",
             text("component A {", "\tdef other() {", "\t\tvar y = 1;", "\t}", "\tdef f() {", *if_block, "\t}",
                  new, *helpers, "}"),
             text("component A {", "\tdef other() {", "\t\tvar y = ;", "\t}", "\tdef f() {", "\t\tif (a())",
                  "\t\t\tg();", "\t\t}", "\t}", new, *helpers, "}"), None),
            # A method loses its "}", and a later body has an error.
            ("brace-closer-then-blank", "body",
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t}", "\tdef other() {",
                  "\t\tvar y = 1;", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t", "\tdef other() {",
                  "\t\tvar y = ;", "\t}", new, *helpers, "}"), None),
            # A body that rule 2 empties has a missing return that is the
            # fifteenth error, after the Observer's and 13 in A, as in
            # repair-missing-return-limit. The same method that lost its "}"
            # instead is refused, so it has no fifteenth error.
            ("brace-opener-missing-return-limit", "header",
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tif (a()) {",
                  "\t\t\treturn 1;", "\t\t}", "\t\treturn 0;", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tif (a())",
                  "\t\t\treturn 1;", "\t\t}", "\t\treturn 0;", "\t}", new, *helpers, "}"), None),
            ("brace-closer-missing-return-limit", "body",
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tg();",
                  "\t\treturn 1;", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tg();",
                  "\t\treturn 1;", "\t", new, *helpers, "}"), None),
            # A method loses its "}" before a member that is indented deeper
            # than its statements, or that follows its last statement on one
            # line, in a program whose brace pairs all agree with the layout or
            # whose method ends on a statement's line. Each member is in the
            # method by layout, so these must be refused. First the review's
            # case: a field whose first line is deeper and whose continuation
            # is as deep as the statements, beside a file-level count that
            # target's count would otherwise bind to.
            ("brace-closer-deeper-continuation", "body",
             text("def count = 11;", "component A {", "\tdef f() {", "\t\tg();", "\t}", "\t\t\tvar count =", "\t\t1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             text("def count = 11;", "component A {", "\tdef f() {", "\t\tg();", "\t", "\t\t\tvar count =", "\t\t1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             "component B { def use() -> int { if (A.count > 0) return A.target(); return 0; } }\n"),
            # A deeper value, method, constructor, and variant case.
            ("brace-closer-deeper-def", "body",
             text("component A {", "\tdef f() {", "\t\tg();", "\t}", "\t\t\tdef count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tg();", "\t", "\t\t\tdef count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             "component B { def use() -> int { if (A.count > 0) return A.target(); return 0; } }\n"),
            ("brace-closer-deeper-method", "body",
             text("component A {", "\tdef f() {", "\t\tg();", "\t}", "\t\t\tdef inner() -> int { return 1; }", new,
                  *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tg();", "\t", "\t\t\tdef inner() -> int { return 1; }", new,
                  *helpers, "}"),
             "component B { def use() -> int { if (A.inner() > 0) return A.target(); return 0; } }\n"),
            ("brace-closer-deeper-constructor", "body",
             text("class Box {", "\tdef f() {", "\t\tg();", "\t}", "\t\t\tnew(x: int) { }", "\tdef g() { }", "}",
                  "component A {", new, helpers[0], "}"),
             text("class Box {", "\tdef f() {", "\t\tg();", "\t", "\t\t\tnew(x: int) { }", "\tdef g() { }", "}",
                  "component A {", new, helpers[0], "}"),
             "component B { def use() -> int { var box = Box.new(1); return A.target(); } }\n"),
            ("brace-closer-deeper-case", "body",
             text("type V {", "\tcase X;", "\tdef f() {", "\t\tg();", "\t}", "\t\t\tcase Y;", "\tdef g() { }", "}",
                  "component A {", new, helpers[0], "}"),
             text("type V {", "\tcase X;", "\tdef f() {", "\t\tg();", "\t", "\t\t\tcase Y;", "\tdef g() { }", "}",
                  "component A {", new, helpers[0], "}"),
             "component B { def use() -> int { var v = V.Y; return A.target(); } }\n"),
            # A field after the method's "}" on its last statement's line.
            ("brace-closer-same-line-field", "body",
             text("component A {", "\tdef f() {", "\t\tg(); } var count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tg(); var count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             "component B { def use() -> int { if (A.count > 0) return A.target(); return 0; } }\n"),
            # A field whose "def" is alone on a deeper line, so that its "var"
            # line, as deep as the statements, continues it.
            ("brace-closer-continued-member", "body",
             text("component A {", "\tdef f() {", "\t\tg();", "\t}", "\t\t\tdef", "\t\tvar count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tg();", "\t", "\t\t\tdef", "\t\tvar count = 1;",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             "component B { def use() -> int { if (A.count > 0) return A.target(); return 0; } }\n"),
            # Rule 2: the method's "}" is deeper than its header, after a legal
            # if without braces, and the method after it, as deep as that "}",
            # lost its "{". Blanking from f's header to inner's "}" would parse
            # and drop inner, so this must be refused.
            ("brace-opener-deeper-method", "header",
             text("component A {", "\tdef f() {", "\t\tif (a())", "\t\t\tg();", "\t\t}", "\t\tdef inner() -> int {",
                  "\t\t\treturn 1;", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tif (a())", "\t\t\tg();", "\t\t}", "\t\tdef inner() -> int",
                  "\t\t\treturn 1;", "\t}", new, *helpers, "}"),
             "component B { def use() -> int { if (A.inner() > 0) return A.target(); return 0; } }\n"),
            # A method that lost its "}" can't end before a header that a
            # local function's could be, as the review's case shows: the local
            # function and the local after it, both as deep as the method's
            # header, would become members, and target's count would bind to
            # the local instead of the file's count. These are refused, with a
            # second query on that count. First the review's program, then a
            # local function that returns a value, one that spans lines, and
            # a local value whose function is no header.
            ("brace-closer-local-function", "body",
             text("def count = 11;", "component A {", "    def f() {", "        g();", "    def local() { }",
                  "        var count = 7;", "    }", "    def target() -> bool { return count == 1; }", "    def g() { }",
                  "}"),
             text("def count = 11;", "component A {", "    def f() {", "        g();", "    def local() { }",
                  "        var count = 7;", "    ", "    def target() -> bool { return count == 1; }", "    def g() { }",
                  "}"), None),
            ("brace-closer-local-function-returns", "body",
             text("def count = 11;", "component A {", "    def f() {", "        g();",
                  "    def local() -> int { return 1; }", "        var count = 7;", "    }",
                  "    def target() -> bool { return count == 1; }", "    def g() { }", "}"),
             text("def count = 11;", "component A {", "    def f() {", "        g();",
                  "    def local() -> int { return 1; }", "        var count = 7;", "    ",
                  "    def target() -> bool { return count == 1; }", "    def g() { }", "}"), None),
            ("brace-closer-local-function-lines", "body",
             text("def count = 11;", "component A {", "    def f() {", "        g();", "    def local() {", "        g();",
                  "    }", "        var count = 7;", "    }", "    def target() -> bool { return count == 1; }",
                  "    def g() { }", "}"),
             text("def count = 11;", "component A {", "    def f() {", "        g();", "    def local() {", "        g();",
                  "    }", "        var count = 7;", "    ", "    def target() -> bool { return count == 1; }",
                  "    def g() { }", "}"), None),
            ("brace-closer-local-value", "body",
             text("def count = 11;", "component A {", "    def f() {", "        g();", "    def local = fun () { };",
                  "        var count = 7;", "    }", "    def target() -> bool { return count == 1; }", "    def g() { }",
                  "}"),
             text("def count = 11;", "component A {", "    def f() {", "        g();", "    def local = fun () { };",
                  "        var count = 7;", "    ", "    def target() -> bool { return count == 1; }", "    def g() { }",
                  "}"), None),
            # An if's "}" is as deep as the method's header, and a local
            # function and a local follow before the method's "}", which is
            # lost. Had that "}" ended the method, they would become members.
            ("brace-inner-closer-local-function", "body",
             text("def count = 11;", "component A {", "    def f() {", "        if (true) {", "            g();", "    }",
                  "    def local() { }", "        var count = 7;", "    }",
                  "    def target() -> bool { return count == 1; }", "    def g() { }", "}"),
             text("def count = 11;", "component A {", "    def f() {", "        if (true) {", "            g();", "    }",
                  "    def local() { }", "        var count = 7;", "    ",
                  "    def target() -> bool { return count == 1; }", "    def g() { }", "}"), None),
            # Rule 2: h lost its "{" right before a ";", so its header still
            # parses, without a body, and count, its local function as deep
            # as h, reads as a member whose if lost its "{". target's count
            # would bind to it instead of the file's count.
            ("brace-opener-bodiless-holder", "header",
             text("def count = 11;", "component A {", "\tdef h() -> int {;", "\tdef count() {", "\t\t\tif (a())",
                  "\t\t\t\tg();", "\t\t\t}", "\t\tvar value = 7;", "\t\treturn value;", "\t}",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"),
             text("def count = 11;", "component A {", "\tdef h() -> int ;", "\tdef count() {", "\t\t\tif (a())",
                  "\t\t\t\tg();", "\t\t\t}", "\t\tvar value = 7;", "\t\treturn value;", "\t}",
                  "\tdef target() -> bool { return count == 1; }", *helpers, "}"), None),
            *(holder(name, header) for name, header in (
                ("brace-opener-holder-comment-name", ("    def h /* comment */ () {;",)),
                ("brace-opener-holder-comment-def", ("    def /* comment */ h() {;",)),
                ("brace-opener-holder-split-def", ("    def", "    h() {;")),
                ("brace-opener-holder-split-name", ("    def h", "    () {;")),
                ("brace-opener-holder-private-comment", ("    private def h /* comment */ () {;",)))),
            # The method's own "}", an inner block's, and the two-body and
            # error-limit cases before a private method, which no statement
            # can begin. Rule 3 put a "}" back in these before it was left out.
            ("brace-method-closer-private", "body",
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t}", "\tprivate def p() { }",
                  new, *helpers, "}"),
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t", "\tprivate def p() { }",
                  new, *helpers, "}"), None),
            ("brace-inner-closer-private", "body",
             text("component A {", "\tdef f() {", *if_block, "\t\tg();", "\t}", "\tprivate def p() { }", new, *helpers,
                  "}"),
             text("component A {", "\tdef f() {", "\t\tif (a()) {", "\t\t\tg();", "\t\t", "\t\tg();", "\t}",
                  "\tprivate def p() { }", new, *helpers, "}"), None),
            ("brace-closer-then-blank-private", "body",
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t}", "\tprivate def other() {",
                  "\t\tvar y = 1;", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() -> int {", "\t\tg();", "\t\treturn 1;", "\t", "\tprivate def other() {",
                  "\t\tvar y = ;", "\t}", new, *helpers, "}"), None),
            ("brace-closer-missing-return-limit-private", "body",
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tg();",
                  "\t\treturn 1;", "\t}", "\tprivate def p() { }", new, *helpers, "}"),
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tg();",
                  "\t\treturn 1;", "\t", "\tprivate def p() { }", new, *helpers, "}"), None),
            # A file one "}" short in which every place for the lost "}" that
            # parses ends the method at the same "}" (#320): a match case's
            # block, before the next case, which no statement can begin; an
            # if's block in a lambda, which needs its ";" after its own "}";
            # and the match case again, where the emptied body's missing
            # return is the fifteenth error, and with an error in a later body.
            ("brace-closer-match-case", "body",
             text("component A {", "\tdef f(x: int) {", "\t\tmatch (x) {", "\t\t\t0 => {", "\t\t\t\tg();", "\t\t\t}",
                  "\t\t\t_ => g();", "\t\t}", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f(x: int) {", "\t\tmatch (x) {", "\t\t\t0 => {", "\t\t\t\tg();", "\t\t\t",
                  "\t\t\t_ => g();", "\t\t}", "\t}", new, *helpers, "}"), None),
            ("brace-closer-lambda-if", "body",
             text("component A {", "\tdef f() {", "\t\tvar fn = fun () -> int {", "\t\t\tif (a()) {", "\t\t\t\tg();",
                  "\t\t\t}", "\t\t\treturn 1;", "\t\t};", "\t\tg();", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f() {", "\t\tvar fn = fun () -> int {", "\t\t\tif (a()) {", "\t\t\t\tg();",
                  "\t\t\t", "\t\t\treturn 1;", "\t\t};", "\t\tg();", "\t}", new, *helpers, "}"), None),
            ("brace-closer-match-case-missing-return-limit", "body",
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tmatch (a()) {",
                  "\t\t\ttrue => {", "\t\t\t\treturn 1;", "\t\t\t}", "\t\t\tfalse => ;", "\t\t}", "\t\treturn 0;", "\t}",
                  new, *helpers, "}"),
             text("component A {", "\tdef errors() {", errors, "\t}", "\tdef broken() -> int {", "\t\tmatch (a()) {",
                  "\t\t\ttrue => {", "\t\t\t\treturn 1;", "\t\t\t", "\t\t\tfalse => ;", "\t\t}", "\t\treturn 0;", "\t}",
                  new, *helpers, "}"), None),
            ("brace-closer-match-case-then-blank", "body",
             text("component A {", "\tdef f(x: int) {", "\t\tmatch (x) {", "\t\t\t0 => {", "\t\t\t\tg();", "\t\t\t}",
                  "\t\t\t_ => g();", "\t\t}", "\t}", "\tdef other() {", "\t\tvar y = 1;", "\t}", new, *helpers, "}"),
             text("component A {", "\tdef f(x: int) {", "\t\tmatch (x) {", "\t\t\t0 => {", "\t\t\t\tg();", "\t\t\t",
                  "\t\t\t_ => g();", "\t\t}", "\t}", "\tdef other() {", "\t\tvar y = ;", "\t}", new, *helpers, "}"),
             None),
            # Review round 3 of #317: an if's "}" is as deep as the method's
            # header, and the method's own "}", after it, is lost before a
            # private method. Whether the lost "}" was the if's or the
            # method's, the declarations agree, but the method's extent, which
            # call hierarchy reports, doesn't, so this must be refused.
            ("brace-closer-equivalent", "body",
             text("def count = 11;", "component A {", "\tdef f() {", "\t\tif (true) {", "\t\t\tg();", "\t}", "\t}",
                  "\tprivate def local() { }", "\tdef target() -> bool { return count == 1; }", "\tdef g() { }", "}"),
             text("def count = 11;", "component A {", "\tdef f() {", "\t\tif (true) {", "\t\t\tg();", "\t}", "\t",
                  "\tprivate def local() { }", "\tdef target() -> bool { return count == 1; }", "\tdef g() { }", "}"),
             None),
        )
        # Every other control is refused: no body, and so no fifteenth error.
        expected = {
            "brace-braceless-if-then-while": (1, False),
            "brace-tab-stops": (1, False),
            "brace-and-blank-two-bodies": (2, False),
            "brace-opener-missing-return-limit": (1, True),
            "brace-closer-match-case": (1, False),
            "brace-closer-lambda-if": (1, False),
            "brace-closer-match-case-missing-return-limit": (1, True),
        }
        # Controls whose wrong reading would bind target's count, or its
        # local(), elsewhere get a second query, on that name.
        count = ("return count == 1", "return count;", "count")
        local = ("return local() == 11", "return local();", "local")
        bindings = {"brace-closer-local-function": count, "brace-closer-local-function-returns": count,
                    "brace-closer-local-function-lines": count, "brace-closer-local-value": count,
                    "brace-inner-closer-local-function": count, "brace-opener-bodiless-holder": count,
                    "brace-closer-equivalent": count,
                    "brace-opener-holder-comment-name": local, "brace-opener-holder-comment-def": local,
                    "brace-opener-holder-split-def": local, "brace-opener-holder-split-name": local,
                    "brace-opener-holder-private-comment": local}
        for name, kind, fixed, broken, other in controls:
            calls = other or caller
            previous = fixed.replace(new, "\tdef target() -> int { return 1; }").replace(
                "def target() -> bool { return count == 1; }", "def target() -> int { return count; }").replace(
                "def target() -> bool { return local() == 11; }", "def target() -> int { return local(); }")
            assert previous != fixed, name
            bodies, reached = expected.get(name, (0, False))
            project = self.project("negative/" + name, {"A.v3": previous, "B.v3": calls})
            state = self.state(project, name, "negative", kind, {"A.v3": previous}, {"A.v3": broken},
                               "original small program", oracle={"A.v3": fixed}, allow_absent=True,
                               expected_bodies={"A.v3": bodies}, expected_limit=reached)
            state["queries"] = state["stale_queries"] = [query("B.v3", calls, "A.target()", "target")]
            if name in bindings:
                # The edit leaves target's line where it was.
                anchor, stale, token = bindings[name]
                use = query("A.v3", broken, anchor, token)
                assert use == query("A.v3", fixed, anchor, token), name
                state["queries"] = state["queries"] + [use]
                state["stale_queries"] = state["stale_queries"] + [query("A.v3", previous, stale, token)]
            yield state

    def verify_inputs(self):
        for path, digest in self.inputs.items():
            assert corpus.sha256((ROOT / path).read_bytes()) == digest, (path, "benchmark mutated input")
