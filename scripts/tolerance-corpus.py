# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""The reviewed 45 repair states, 140 byte replay states, and small controls.

Only disposable copies are written. Parser invocations select/validate history;
all measured answers and oracles come from the stdio protocol.
"""
import importlib.util
import json
from pathlib import Path
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

    def large(self):
        sources = {str(Path(p).relative_to(ROOT / "vendor/virgil")): self.remember(p)
                   for p in repair.aeneas_files()}
        self.remember(ROOT / "test/fixtures/repair/aeneas-sites.txt")
        project = self.project("aeneas", sources)
        anchors = [("aeneas/src/util/Paths.v3", "StringBuilder.new", "StringBuilder"),
                   ("aeneas/src/util/Char.v3", "properties[ch]", "properties"),
                   ("lib/util/StringBuilder.v3", "Arrays.", "Arrays")]
        # Fixed bound sites, checked by the protocol oracle before measurement.
        for path, anchor, token in anchors:
            text = sources[path]
            at = text.index(anchor)
            project["queries"].append({"path": path, "position": corpus.position(text, at), "token": token})
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
