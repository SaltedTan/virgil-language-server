#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
#
# Validate the editor tests' result streams, one run in each, against the
# coverage manifest and the result schema, by the rules in test/e2e/RESULTS.md.
# The script interprets results-v1.schema.json itself, with draft 2020-12
# semantics for the keywords in SCHEMA_KEYWORDS, and refuses a schema with any
# other keyword, so that the schema and this script can't drift apart. Then it
# checks what one event's schema can't: the stream's encoding, the order of its
# events, the run-start's plan and provenance against the manifest, each check
# against the plan and the manifest's expected-failure rules, and the run-end
# against the checks. It prints one line for each stream: valid, with whether
# its run passed and the number of observations in each status, or invalid,
# with the first error it finds and its line. It exits with 0 if every stream
# is valid and its run passed, 1 if every stream is valid but a run didn't
# pass, and 2 if a stream is invalid or the manifest or the schema can't be
# used. Other tools can load this file as a module, with importlib, since its
# name has a hyphen. See test/e2e/RESULTS.md#validating-a-stream.
# Usage: editor-results.py validate [--manifest PATH] [--schema PATH] STREAM...
# The manifest and the schema default to coverage.json and
# results-v1.schema.json in the test/e2e directory beside this script's.
import argparse
import datetime
import hashlib
import json
import math
import re
import sys
import traceback
from pathlib import Path
from urllib.parse import unquote

E2E = Path(__file__).resolve().parent.parent / "test" / "e2e"
MANIFEST = E2E / "coverage.json"
SCHEMA = E2E / "results-v1.schema.json"
STATUSES = ("pass", "fail", "xfail", "xpass", "skip")
OUTCOMES = {"pass": "passed", "fail": "failed", "aborted": "aborted", "unavailable": "unavailable"}
DRAFT = "https://json-schema.org/draft/2020-12/schema"
# The keywords that the result schema uses, and the only ones that Schema
# interprets.
SCHEMA_KEYWORDS = frozenset([
    "$schema", "$id", "$defs", "$ref", "title", "description", "type", "properties", "required",
    "additionalProperties", "propertyNames", "items", "minItems", "maxItems", "minLength",
    "maxLength", "minimum", "pattern", "enum", "const", "oneOf", "allOf", "if", "then", "else",
])
# A JSON number with a fraction or an exponent, such as 1.0, is never an
# integer here, and never equals one: Python's json module keeps the two
# apart as int and float, and RESULTS.md's integers are written without either.
TYPES = {
    "null": lambda value: value is None,
    "boolean": lambda value: type(value) is bool,
    "object": lambda value: type(value) is dict,
    "array": lambda value: type(value) is list,
    "string": lambda value: type(value) is str,
    "integer": lambda value: type(value) is int,
    "number": lambda value: type(value) in (int, float),
}
TYPE_NAMES = {"null": "null", "boolean": "a boolean", "object": "an object", "array": "an array",
              "string": "a string", "integer": "an integer", "number": "a number"}
# The definition that a provenance value matches when a failed prerequisite
# prevented obtaining it. A run with one can't pass (RESULTS.md, "Result events").
UNAVAILABLE = "/$defs/unavailableProvenance"
# The definition of a UTC time, whose date must also exist: its pattern
# accepts February 30.
TIME = "/$defs/time"
# The conditions that a field of the run-start's provenance decides: each
# holds exactly when the field has the value.
DECIDED_BY_PROVENANCE = {"vsix": ("clientMode", "vsix")}
# A manual record may observe any setup; every other suite's runs observe
# the setups of their own suite.
MANUAL = "manual"
# The launcher's profile, outside the editor, which has no editor
# configuration to digest (RESULTS.md, "The manifest").
WRAPPER = "wrapper"
# The members of the manifest's entries that validation reads.
MANIFEST_FIELDS = {
    "checks": ("id", "suites", "profiles", "level", "when"),
    "suites": ("id", "profiles"),
    "conditions": ("id", "alternative"),
    "setups": ("id", "suite", "client", "host"),
    "xfails": ("id", "check", "profile", "plugin", "commit", "failureCode", "requires"),
}
PROFILE_FIELDS = ("id", "workaround", "repeated")
SURROGATE = re.compile("[\ud800-\udfff]")


class Invalid(Exception):
    """A stream breaks a rule, on a line counted from 1."""

    def __init__(self, line, message):
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


class Unusable(Exception):
    """The manifest or the schema can't be used to validate streams."""


def quote(value):
    return json.dumps(value)


def show(value):
    """value in a message: JSON for a scalar, cut to 60 characters, or its kind."""
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, list):
        return "an array"
    text = quote(value)
    return text if len(text) <= 60 else text[:57] + "..."


def plural(number, noun):
    return f"{number} {noun}" + ("" if number == 1 else "s")


def location(path):
    """A path into an event, such as provenance.server.sha256 or expected[3]."""
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        elif re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$-]*", part):
            text += f".{part}" if text else part
        else:
            text += f"[{quote(part)}]"
    return text


def observation(key):
    check, profile, iteration = key
    return f"{check} in profile {profile}, iteration {iteration}"


def parse_json(text):
    """Parses JSON text, and raises ValueError for what Python's json module
    accepts but RESULTS.md doesn't: a repeated key in an object, NaN, Infinity,
    and a number too large to be finite."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"the key {quote(key)} is repeated")
            result[key] = value
        return result

    def constant(name):
        raise ValueError(f"{name} is not JSON")

    def number(literal):
        value = float(literal)
        if math.isinf(value):
            raise ValueError(f"the number {literal} is too large")
        return value

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def strings(value):
    """Yields every string in a JSON value, member names included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)


def same(a, b):
    """Whether two JSON values are equal, as JSON Schema's const and enum
    compare them, except that an integer never equals another number."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[key], b[key]) for key in a)
    return a == b


# ECMA-262 regular expressions, as JSON Schema's pattern has them: Unicode
# patterns (the u flag), which search the string rather than match it whole.
# translate() rewrites the constructs below in Python's syntax, with ECMA's
# meaning, and refuses the rest. Python's "$" also matches before a final LF,
# its "." matches CR, and its \d and \w match non-ASCII digits and letters.
ECMA_SYNTAX = "^$\\.*+?()[]{}|/"
ECMA_CONTROLS = {"t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r"}
ECMA_CLASSES = {"d": "0-9", "w": "A-Za-z0-9_"}
ECMA_DOT = "[^\\n\\r\\u2028\\u2029]"
QUANTIFIER = re.compile(r"\{([0-9]+)(,([0-9]*))?\}")


def literal(char):
    """A Python pattern that matches the character, in or out of a class."""
    if char.isascii() and (char.isalnum() or char == "_"):
        return char
    code = ord(char)
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


def translate(source):
    """Translates an ECMA-262 pattern to a Python one with the same matches:
    literals, ".", "^" and "$", alternatives, groups and (?:...), the greedy
    and lazy quantifiers, classes with ranges, escapes of syntax characters,
    \\d, \\D, \\w, \\W, \\t, \\n, \\v, \\f, \\r, and \\uXXXX. Raises ValueError for
    anything else, such as \\s, \\b, a lookaround, a backreference, or a named
    group."""
    out = []
    position = 0
    depth = 0
    quantifiable = False

    def fail(what):
        raise ValueError(f"{what} (at offset {position})")

    def escape(in_class):
        """Reads the escape after a backslash: its translation, and the
        character it stands for, or None for a class escape such as \\d."""
        nonlocal position
        if position >= len(source):
            fail("a \\ at the end")
        char = source[position]
        position += 1
        if char in ECMA_SYNTAX or (in_class and char == "-"):
            return literal(char), char
        if char in ECMA_CONTROLS:
            return literal(ECMA_CONTROLS[char]), ECMA_CONTROLS[char]
        if char == "u" and re.fullmatch(r"[0-9A-Fa-f]{4}", source[position:position + 4]):
            code = int(source[position:position + 4], 16)
            if 0xD800 <= code <= 0xDFFF:
                fail("a surrogate escape")
            position += 4
            return literal(chr(code)), chr(code)
        if char.lower() in ECMA_CLASSES and not (in_class and char.isupper()):
            members = ECMA_CLASSES[char.lower()]
            if in_class:
                return members, None
            return ("[^" if char.isupper() else "[") + members + "]", None
        fail(f"the escape \\{char}, which this doesn't translate")

    while position < len(source):
        char = source[position]
        position += 1
        if char == "\\":
            out.append(escape(False)[0])
            quantifiable = True
        elif char == "[":
            negated = source.startswith("^", position)
            if negated:
                position += 1
            members = []
            while True:
                if position >= len(source):
                    fail("an unclosed [")
                char = source[position]
                position += 1
                if char == "]":
                    break
                low, low_char = escape(True) if char == "\\" else (literal(char), char)
                if source.startswith("-", position) and source[position + 1:position + 2] not in ("", "]"):
                    position += 1
                    char = source[position]
                    position += 1
                    high, high_char = escape(True) if char == "\\" else (literal(char), char)
                    if low_char is None or high_char is None:
                        fail("a range with a class escape")
                    if low_char > high_char:
                        fail("a range out of order")
                    members.append(f"{low}-{high}")
                else:
                    members.append(low)
            if not members:
                fail("an empty class")
            out.append("[" + "^" * negated + "".join(members) + "]")
            quantifiable = True
        elif char == "(":
            if source.startswith("?:", position):
                position += 2
                out.append("(?:")
            elif source.startswith("?", position):
                fail("a group other than (...) and (?:...)")
            else:
                out.append("(")
            depth += 1
            quantifiable = False
        elif char == ")":
            if not depth:
                fail("an unmatched )")
            depth -= 1
            out.append(")")
            quantifiable = True
        elif char in "*+?{":
            text = char
            if char == "{":
                match = QUANTIFIER.match(source, position - 1)
                if not match:
                    fail("a { that doesn't start a quantifier")
                if match.group(3) and int(match.group(3)) < int(match.group(1)):
                    fail("a quantifier's bounds out of order")
                text = match.group(0)
                position = match.end()
            if not quantifiable:
                fail("nothing to repeat")
            if source.startswith("?", position):
                position += 1
                text += "?"
            out.append(text)
            quantifiable = False
        elif char in "]}":
            fail(f"a lone {char}")
        elif char == "|":
            out.append("|")
            quantifiable = False
        elif char in "^$":
            out.append("\\A" if char == "^" else "\\Z")
            quantifiable = False
        else:
            out.append(ECMA_DOT if char == "." else literal(char))
            quantifiable = True
    if depth:
        fail("an unclosed (")
    return "".join(out)


def condition(schema):
    """Describes an if schema such as {"properties": {"status": {"const": "pass"}}}."""
    if isinstance(schema, dict) and list(schema) == ["properties"] and len(schema["properties"]) == 1:
        (name, test), = schema["properties"].items()
        if isinstance(test, dict) and list(test) == ["const"]:
            return f"{name} is {show(test['const'])}"
        if isinstance(test, dict) and list(test) == ["enum"]:
            return f"{name} is " + " or ".join(show(option) for option in test["enum"])
    return "its if schema holds"


class Schema:
    """The result schema, which errors() interprets with draft 2020-12
    semantics for SCHEMA_KEYWORDS, apart from integers (TYPES)."""

    def __init__(self, document):
        if not isinstance(document, dict):
            raise Unusable("the schema must be a JSON object")
        if document.get("$schema") != DRAFT:
            raise Unusable(f"the schema's $schema must be {DRAFT}")
        self.document = document
        self.schemas = {}     # JSON pointer -> each schema in the document
        self.references = {}  # $ref -> the JSON pointer it names
        self.patterns = {}    # pattern -> its compiled translation
        self._walk(document, "")
        for reference, pointer in self.references.items():
            if pointer not in self.schemas:
                raise Unusable(f"the schema's $ref {reference} doesn't name a schema in it")
        for pointer, what in ((UNAVAILABLE, "marks unavailable provenance"), (TIME, "describes a time")):
            if pointer not in self.schemas:
                raise Unusable(f"the schema has no {pointer}, which {what}")

    @classmethod
    def load(cls, path=SCHEMA):
        try:
            return cls(parse_json(Path(path).read_bytes().decode("utf-8")))
        except OSError as error:
            raise Unusable(f"can't read the schema {path}: {error.strerror}") from error
        except (ValueError, RecursionError) as error:
            raise Unusable(f"the schema {path} is not JSON: {error}") from error

    def _walk(self, schema, pointer):
        """Records the schema at the JSON pointer and each one in it, and
        refuses a keyword outside SCHEMA_KEYWORDS or a value it can't use."""
        self.schemas[pointer] = schema
        if isinstance(schema, bool):
            return
        where = f"the schema's {pointer or 'root'}"
        if not isinstance(schema, dict):
            raise Unusable(f"{where} must be an object or a boolean")
        unsupported = sorted(set(schema) - SCHEMA_KEYWORDS)
        if unsupported:
            raise Unusable(f"{where} has the keyword {unsupported[0]}, which this script doesn't interpret")
        if pointer and ("$schema" in schema or "$id" in schema):
            raise Unusable(f"{where} has $schema or $id, which this script interprets only at the root")
        for keyword, value in schema.items():
            at = f"{pointer}/{keyword}"
            if keyword in ("$defs", "properties"):
                if not isinstance(value, dict):
                    raise Unusable(f"the schema's {at} must be an object")
                for name, item in value.items():
                    self._walk(item, f"{at}/" + name.replace("~", "~0").replace("/", "~1"))
            elif keyword in ("additionalProperties", "propertyNames", "items", "if", "then", "else"):
                self._walk(value, at)
            elif keyword in ("oneOf", "allOf"):
                if not isinstance(value, list) or not value:
                    raise Unusable(f"the schema's {at} must be a nonempty array")
                for index, item in enumerate(value):
                    self._walk(item, f"{at}/{index}")
            elif keyword == "required":
                if not isinstance(value, list) or not all(type(name) is str for name in value) \
                        or len(set(value)) != len(value):
                    raise Unusable(f"the schema's {at} must be an array of distinct strings")
            elif keyword == "type":
                names = value if isinstance(value, list) else [value]
                if not names or not all(name in TYPES for name in names) or len(set(names)) != len(names):
                    raise Unusable(f"the schema's {at} must name JSON types")
            elif keyword == "enum":
                if not isinstance(value, list):
                    raise Unusable(f"the schema's {at} must be an array")
            elif keyword in ("minItems", "maxItems", "minLength", "maxLength"):
                if type(value) is not int or value < 0:
                    raise Unusable(f"the schema's {at} must be a nonnegative integer")
            elif keyword == "minimum":
                if type(value) not in (int, float):
                    raise Unusable(f"the schema's {at} must be a number")
            elif keyword == "pattern":
                try:
                    self.patterns[value] = re.compile(translate(value))
                except (TypeError, ValueError, re.error) as error:
                    raise Unusable(f"the schema's {at} {show(value)} can't be translated: {error}") from error
            elif keyword == "$ref":
                if type(value) is not str or not re.fullmatch(r"#(/.*)?", value):
                    raise Unusable(f"the schema's {at} must be a JSON pointer in the schema, such as #/$defs/name")
                self.references[value] = unquote(value[1:])
            elif keyword != "const" and type(value) is not str:
                raise Unusable(f"the schema's {at} must be a string")

    def errors(self, instance, refs=None):
        """Returns the instance's errors, each a message that starts with its
        location, in the order the schema finds them. If there are none, refs,
        if given, gets a (pointer, path) pair for each $ref that described a
        value: the JSON pointer of the schema it named, and the value's path."""
        found = []
        errors = self._evaluate(self.document, instance, (), found)
        if refs is not None and not errors:
            refs += found
        return [f"{location(path)}: {message}" if path else message for path, message in errors]

    def _typed(self, schema, value):
        """Whether the schema, and each schema that it refers to, allows the
        value's type."""
        seen = set()
        while isinstance(schema, dict) and id(schema) not in seen:
            seen.add(id(schema))
            names = schema.get("type", [])
            names = [names] if isinstance(names, str) else names
            if names and not any(TYPES[name](value) for name in names):
                return False
            if "$ref" not in schema:
                break
            schema = self.schemas[self.references[schema["$ref"]]]
        return schema is not False

    def _evaluate(self, schema, value, path, refs):
        if schema is True:
            return []
        if schema is False:
            return [(path, "is not allowed")]
        errors = []
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(TYPES[name](value) for name in names):
                return [(path, f"{show(value)} is not " + " or ".join(TYPE_NAMES[name] for name in names))]
        if "const" in schema and not same(value, schema["const"]):
            errors.append((path, f"{show(value)} is not {show(schema['const'])}"))
        if "enum" in schema and not any(same(value, option) for option in schema["enum"]):
            errors.append((path, f"{show(value)} is not one of " + ", ".join(map(show, schema["enum"]))))
        if "$ref" in schema:
            pointer = self.references[schema["$ref"]]
            found = []
            failed = self._evaluate(self.schemas[pointer], value, path, found)
            errors += failed
            if not failed:
                refs.append((pointer, path))
                refs += found
        if TYPES["string"](value):
            if len(value) < schema.get("minLength", 0):
                errors.append((path, "is empty" if not value else
                               f"{show(value)} is shorter than {plural(schema['minLength'], 'character')}"))
            if len(value) > schema.get("maxLength", math.inf):
                errors.append((path, f"{show(value)} is longer than {plural(schema['maxLength'], 'character')}"))
            if "pattern" in schema and not self.patterns[schema["pattern"]].search(value):
                errors.append((path, f"{show(value)} doesn't match {schema['pattern']}"))
        if TYPES["number"](value) and value < schema.get("minimum", -math.inf):
            errors.append((path, f"{show(value)} is less than {schema['minimum']}"))
        if TYPES["array"](value):
            if len(value) < schema.get("minItems", 0):
                errors.append((path, f"has {plural(len(value), 'item')}, fewer than {schema['minItems']}"))
            if len(value) > schema.get("maxItems", math.inf):
                errors.append((path, f"has {plural(len(value), 'item')}, more than {schema['maxItems']}"))
            if "items" in schema:
                for index, item in enumerate(value):
                    errors += self._evaluate(schema["items"], item, path + (index,), refs)
        if TYPES["object"](value):
            errors += [(path, f"lacks the member {quote(name)}")
                       for name in schema.get("required", ()) if name not in value]
            properties = schema.get("properties", {})
            for name, item in value.items():
                if name in properties:
                    errors += self._evaluate(properties[name], item, path + (name,), refs)
                elif schema.get("additionalProperties") is False:
                    errors.append((path, f"has the member {quote(name)}, which the schema doesn't allow"))
                elif "additionalProperties" in schema:
                    errors += self._evaluate(schema["additionalProperties"], item, path + (name,), refs)
            if "propertyNames" in schema:
                for name in value:
                    errors += [(path, f"the member name {message}")
                               for _, message in self._evaluate(schema["propertyNames"], name, path, [])]
        for item in schema.get("allOf", ()):
            errors += self._evaluate(item, value, path, refs)
        if "oneOf" in schema:
            outcomes = []
            for item in schema["oneOf"]:
                found = []
                outcomes.append((self._evaluate(item, value, path, found), found))
            matched = [found for failed, found in outcomes if not failed]
            if len(matched) == 1:
                refs += matched[0]
            elif matched:
                errors.append((path, f"matches {len(matched)} of the oneOf schemas, not exactly one"))
            else:
                # The errors of the schema that it comes closest to matching:
                # one that allows its type, with the fewest errors.
                ranks = [(not self._typed(item, value), len(failed))
                         for item, (failed, _) in zip(schema["oneOf"], outcomes)]
                errors += outcomes[ranks.index(min(ranks))][0]
        if "if" in schema:
            held = not self._evaluate(schema["if"], value, path, [])
            branch = schema.get("then" if held else "else")
            if branch is not None:
                why = ("when " if held else "unless ") + condition(schema["if"])
                errors += [(where, f"{message}, as the schema requires {why}")
                           for where, message in self._evaluate(branch, value, path, refs)]
        return errors


def by_id(items):
    return {item["id"]: item for item in items}


class Manifest:
    """The coverage manifest: its checks, suites with their profiles,
    conditions, setups, and expected-failure rules, and its SHA-256."""

    def __init__(self, data):
        self.sha256 = hashlib.sha256(data).hexdigest()
        try:
            document = parse_json(data.decode("utf-8"))
            for section, fields in MANIFEST_FIELDS.items():
                for item in document[section]:
                    missing = [field for field in fields if field not in item]
                    if missing:
                        raise ValueError(f"an entry of {section} lacks {missing[0]}")
            for suite in document["suites"]:
                for profile in suite["profiles"]:
                    missing = [field for field in PROFILE_FIELDS if field not in profile]
                    if missing:
                        raise ValueError(f"a profile of suite {suite['id']} lacks {missing[0]}")
            self.checks = by_id(document["checks"])
            self.retired = set(document["retired"])
            self.suites = by_id(document["suites"])
            self.profiles = {suite["id"]: by_id(suite["profiles"]) for suite in document["suites"]}
            self.conditions = by_id(document["conditions"])
            self.setups = by_id(document["setups"])
            self.xfails = by_id(document["xfails"])
        except (ValueError, RecursionError, KeyError, TypeError) as error:
            raise Unusable(f"the manifest can't be used: {error}") from error

    @classmethod
    def load(cls, path=MANIFEST):
        try:
            return cls(Path(path).read_bytes())
        except OSError as error:
            raise Unusable(f"can't read the manifest {path}: {error.strerror}") from error


class Plan:
    """A run's planned observations, as (id, profile, iteration) keys, the
    number of iterations of each profile, and the conditions that hold."""

    def __init__(self, keys, iterations, holds):
        self.keys = keys
        self.iterations = iterations
        self.holds = holds


def plan_of(start, manifest):
    """Checks that the run-start's expected is the manifest's plan, as
    RESULTS.md defines it, and returns the Plan. Raises Invalid on line 1."""
    suite = start["suite"]
    profiles = manifest.profiles[suite]
    keys = [(key["id"], key["profile"], key["iteration"]) for key in start["expected"]]
    for index, (check_id, profile, iteration) in enumerate(keys):
        where = f"expected[{index}]"
        check = manifest.checks.get(check_id)
        if check is None:
            raise Invalid(1, f"{where}: {check_id} is " + (
                "a retired check" if check_id in manifest.retired else "not a check in the manifest"))
        if suite not in check["suites"]:
            raise Invalid(1, f"{where}: {check_id} is not a check of suite {suite}")
        if profile not in check["profiles"] or profile not in profiles:
            raise Invalid(1, f"{where}: {check_id} doesn't run in profile {profile}")
        if iteration != 1 and not profiles[profile]["repeated"]:
            raise Invalid(1, f"{where}: profile {profile} isn't repeated, so its only iteration is 1, not {iteration}")
    for index in range(1, len(keys)):
        if keys[index] == keys[index - 1]:
            raise Invalid(1, f"expected[{index}] repeats expected[{index - 1}]")
        if keys[index] < keys[index - 1]:
            raise Invalid(1, f"expected[{index}] sorts before expected[{index - 1}]: "
                             "expected is sorted by id, profile, then iteration")

    found = {}
    for _, profile, iteration in keys:
        found.setdefault(profile, set()).add(iteration)
    iterations = {}
    for profile in sorted(found):
        numbers = sorted(found[profile])
        if numbers != list(range(1, len(numbers) + 1)):
            gap = min(set(range(1, numbers[-1] + 1)) - found[profile])
            raise Invalid(1, f"expected has profile {profile} in iterations "
                             f"{', '.join(map(str, numbers))}, without iteration {gap}")
        iterations[profile] = len(numbers)
    repeated = [profile for profile in iterations if profiles[profile]["repeated"]]
    for profile in repeated[1:]:
        if iterations[profile] != iterations[repeated[0]]:
            raise Invalid(1, f"expected has {plural(iterations[repeated[0]], 'iteration')} of profile "
                             f"{repeated[0]}, but {iterations[profile]} of profile {profile}: a repeated "
                             "profile has one iteration for each of the run's repetitions")

    checks = [check for check in manifest.checks.values() if suite in check["suites"]]

    def planned(conditions):
        return sorted((check["id"], profile, iteration) for check in checks
                      if check["when"] is None or check["when"] in conditions
                      for profile in check["profiles"] if profile in iterations
                      for iteration in range(1, iterations[profile] + 1))

    unconditional = set(planned(()))

    def conditional(condition_id):
        """The keys of the condition's checks in the run's profiles and iterations."""
        return set(planned({condition_id})) - unconditional

    holds = {manifest.checks[check_id]["when"] for check_id, _, _ in keys} - {None}
    for condition_id, condition in manifest.conditions.items():
        other = condition["alternative"]
        if other is None:
            continue
        if condition_id in holds and other in holds:
            raise Invalid(1, f"expected has checks of both {condition_id} and its alternative {other}: "
                             "a run takes one of them")
        if condition_id not in holds and other not in holds and conditional(condition_id) and conditional(other):
            raise Invalid(1, f"expected has checks of neither {condition_id} nor its alternative {other}: "
                             "a run takes one of them")
    # Every key is in the plan already, by the checks above.
    missing = sorted(set(planned(holds)) - set(keys))
    if missing:
        check_id, profile, iteration = missing[0]
        when = manifest.checks[check_id]["when"]
        raise Invalid(1, f"expected lacks {observation(missing[0])}, " + (
            f"a check of suite {suite} in that profile" if when is None else
            f"which has the condition {when}: a run plans all of a condition's checks or none"))

    provenance = start["provenance"]
    for condition_id, (field, value) in DECIDED_BY_PROVENANCE.items():
        if condition_id in manifest.conditions and conditional(condition_id):
            held = condition_id in holds
            if held != (provenance[field] == value):
                raise Invalid(1, f"expected {'has' if held else 'lacks'} the {condition_id} checks, but "
                                 f"provenance.{field} is {show(provenance[field])}")
    return Plan(set(keys), iterations, holds)


class Run:
    """A valid stream's run: its run-start, Plan, check events, and run-end,
    and the locations of its unavailable provenance."""

    def __init__(self, start, plan, checks, end, unavailable):
        self.start = start
        self.plan = plan
        self.checks = checks
        self.end = end
        self.unavailable = unavailable

    @property
    def passed(self):
        return self.end["status"] == "pass"

    def summary(self):
        end = self.end
        counts = ", ".join(f"{end['counts'][status]} {status}" for status in STATUSES)
        return (f"valid, run {OUTCOMES[end['status']]} ({end['checkCount']} of {end['expectedCount']} "
                f"observations): {counts}")


class _Reader:
    """Checks a stream's events, in order, by RESULTS.md's rules."""

    def __init__(self, manifest, schema):
        self.manifest = manifest
        self.schema = schema
        self.start = None
        self.plan = None
        self.unavailable = []
        self.checks = []
        self.lines = {}     # each observed key -> the line of its check
        # (line, check, rule) of each xfail, and of each fail that the rule
        # would excuse if its prerequisites passed, which the run-end checks.
        self.excepted = []
        self.failed = False
        self.end = None
        self.end_line = None

    def add(self, number, event):
        refs = []
        errors = self.schema.errors(event, refs)
        if errors:
            raise Invalid(number, errors[0])
        for pointer, path in refs:
            if pointer == TIME:
                time = event
                for part in path:
                    time = time[part]
                try:
                    datetime.date(int(time[:4]), int(time[5:7]), int(time[8:10]))
                except ValueError as error:
                    raise Invalid(number, f"{location(path)}: {show(time)} is not a date: {error}") from error
        kind = event["event"]
        if self.end is not None:
            raise Invalid(number, f"the {kind} comes after the run-end on line {self.end_line}")
        if self.start is None and kind != "run-start":
            raise Invalid(number, f"the first event is a {kind}, not a run-start")
        if self.start is not None:
            if kind == "run-start":
                raise Invalid(number, "a second run-start; the run's is on line 1")
            if event["runId"] != self.start["runId"]:
                raise Invalid(number, f"runId is {show(event['runId'])}, but the run-start's is "
                                      f"{show(self.start['runId'])}")
        if event["seq"] != number - 1:
            raise Invalid(number, f"seq is {event['seq']}, not {number - 1}: seq counts the events from 0 "
                                  "without gaps")
        if kind == "run-start":
            self._start(event, refs)
        elif kind == "check":
            self._check(number, event)
        else:
            self._end(number, event)

    def finish(self, lines):
        if self.end is None:
            raise Invalid(lines + 1, "incomplete: no run-end")
        return Run(self.start, self.plan, self.checks, self.end, self.unavailable)

    def _start(self, start, refs):
        manifest = self.manifest
        if start["coverageSha256"] != manifest.sha256:
            raise Invalid(1, f"coverageSha256 is {start['coverageSha256']}, but the manifest's is "
                             f"{manifest.sha256}: the run was planned with another coverage.json")
        suite = start["suite"]
        if suite not in manifest.suites:
            raise Invalid(1, f"suite {suite} is not in the manifest")
        setup = manifest.setups.get(start["setup"])
        if setup is None:
            raise Invalid(1, f"setup {start['setup']} is not in the manifest")
        if suite != MANUAL and setup["suite"] != suite:
            raise Invalid(1, f"setup {setup['id']} is a setup of suite {setup['suite']}, not {suite}")
        self.plan = plan_of(start, manifest)
        provenance = start["provenance"]
        for side in ("client", "host"):
            if provenance[side]["os"] != setup[side]:
                raise Invalid(1, f"provenance.{side}.os is {provenance[side]['os']}, but setup {setup['id']} "
                                 f"has a {setup[side]} {side}")
        names = [pin["name"] for pin in provenance["pins"]]
        for index, name in enumerate(names):
            if name in names[:index]:
                raise Invalid(1, f"provenance.pins[{index}] repeats the name {show(name)}")
        digests = provenance["configurationSha256"]
        configured = sorted(set(self.plan.iterations) - {WRAPPER})
        for profile in digests:
            if profile not in configured:
                raise Invalid(1, f"provenance.configurationSha256 has profile {profile}, " + (
                    "which runs outside the editor" if profile == WRAPPER else "which the plan doesn't have"))
        for profile in configured:
            if profile not in digests:
                raise Invalid(1, f"provenance.configurationSha256 lacks profile {profile}, which the plan has")
        profiles = manifest.profiles[suite]
        for profile, digest in digests.items():
            for other, other_digest in digests.items():
                if profiles[profile]["workaround"] and not profiles[other]["workaround"] and other_digest == digest:
                    raise Invalid(1, f"provenance.configurationSha256 gives the workaround profile {profile} the "
                                     f"digest of profile {other}: a workaround has a configuration of its own")
        self.unavailable = [location(path) for pointer, path in refs if pointer == UNAVAILABLE]
        self.start = start

    def _pinned(self, rule):
        """The run's pin of the rule's plugin, as a phrase, and whether it has the rule's commit."""
        for pin in self.start["provenance"]["pins"]:
            if pin["name"] == rule["plugin"]:
                commit = pin["commit"]
                return f"{pin['name']} at {commit or 'no commit'}", commit == rule["commit"]
        return f"no pin of {rule['plugin']}", False

    def _check(self, number, check):
        key = (check["id"], check["profile"], check["iteration"])
        if key not in self.plan.keys:
            raise Invalid(number, f"{observation(key)} is not in the plan")
        if key in self.lines:
            raise Invalid(number, f"{observation(key)} is observed again, after line {self.lines[key]}")
        level = self.manifest.checks[check["id"]]["level"]
        if check["level"] != level:
            raise Invalid(number, f"level is {check['level']}, but the manifest gives {check['id']} the level {level}")
        status = check["status"]
        if status == "skip" and not self.failed:
            raise Invalid(number, f"{observation(key)} is skipped, but no check before it failed")
        rules = self.manifest.xfails
        if status in ("pass", "fail"):
            for rule in rules.values():
                pin, pinned = self._pinned(rule)
                if rule["check"] != check["id"] or rule["profile"] != check["profile"] or not pinned:
                    continue
                # Where the rule applies, observing what the check asserts is
                # an xpass, and not observing it is an xfail if the
                # prerequisites passed, which the run-end checks.
                if status == "pass":
                    raise Invalid(number, f"{observation(key)} passed with {pin}, where rule {rule['id']} "
                                          "makes a pass an xpass")
                if check["failureCode"] == rule["failureCode"]:
                    self.excepted.append((number, check, rule))
        elif status in ("xfail", "xpass"):
            rule = rules.get(check["expectation"])
            if rule is None:
                raise Invalid(number, f"expectation {check['expectation']} is not an expected-failure rule "
                                      "of the manifest")
            if rule["check"] != check["id"]:
                raise Invalid(number, f"rule {rule['id']} is for {rule['check']}, not {check['id']}")
            if rule["profile"] != check["profile"]:
                raise Invalid(number, f"rule {rule['id']} applies only in profile {rule['profile']}, "
                                      f"not {check['profile']}")
            pin, pinned = self._pinned(rule)
            if not pinned:
                raise Invalid(number, f"rule {rule['id']} applies only with {rule['plugin']} at {rule['commit']}, "
                                      f"but the run's pins have {pin}")
            if status == "xfail":
                if check["failureCode"] != rule["failureCode"]:
                    raise Invalid(number, f"rule {rule['id']} excuses only the failure code {rule['failureCode']}, "
                                          f"not {check['failureCode']}")
                self.excepted.append((number, check, rule))
        self.failed = self.failed or status == "fail"
        self.lines[key] = number
        self.checks.append(check)

    def _end(self, number, end):
        # The prerequisites of an xfail, or of a fail that the rule may
        # excuse, may come after it.
        statuses = {(check["id"], check["profile"], check["iteration"]): check["status"] for check in self.checks}
        for line, check, rule in self.excepted:
            keys = [(required, check["profile"], check["iteration"]) for required in rule["requires"]]
            unmet = [key for key in keys if statuses.get(key) != "pass"]
            if check["status"] == "fail" and not unmet:
                raise Invalid(line, f"{check['id']} failed with {check['failureCode']}, where rule {rule['id']} "
                                    "makes it an xfail: its prerequisites passed in the same profile and iteration")
            if check["status"] == "xfail" and unmet:
                key = unmet[0]
                found = (f"it is a {statuses[key]}, on line {self.lines[key]}" if key in statuses
                         else "it has no check")
                raise Invalid(line, f"rule {rule['id']} excuses {check['id']} only if {observation(key)} "
                                    f"passed, but {found}")
        counts = dict.fromkeys(STATUSES, 0)
        for check in self.checks:
            counts[check["status"]] += 1
        if end["expectedCount"] != len(self.plan.keys):
            raise Invalid(number, f"expectedCount is {end['expectedCount']}, but the run-start plans "
                                  f"{plural(len(self.plan.keys), 'observation')}")
        if end["checkCount"] != len(self.checks):
            raise Invalid(number, f"checkCount is {end['checkCount']}, but the stream has "
                                  f"{plural(len(self.checks), 'check')}")
        for status in STATUSES:
            if end["counts"][status] != counts[status]:
                raise Invalid(number, f"counts.{status} is {end['counts'][status]}, but the stream has "
                                      f"{plural(counts[status], status + ' check')}")
        missing = sorted(self.plan.keys - set(self.lines))
        if end["complete"] and missing:
            more = f", nor do {len(missing) - 1} more" if len(missing) > 1 else ""
            raise Invalid(number, f"complete is true, but {observation(missing[0])} has no check{more}")
        if not end["complete"] and not missing:
            raise Invalid(number, "complete is false, but every planned observation has its check")
        # Why the run can't pass, in RESULTS.md's order.
        reasons = [f"{observation(key)} has no check" for key in missing[:1]]
        reasons += [f"{plural(counts[status], 'check')} {verb}"
                    for status, verb in (("fail", "failed"), ("skip", "skipped"), ("xpass", "passed unexpectedly"))
                    if counts[status]]
        if end["exitCode"] != 0:
            reasons.append(f"exitCode is {show(end['exitCode'])}")
        if end["signal"] is not None:
            reasons.append(f"signal is {end['signal']}")
        if end["cleanup"] != "verified":
            reasons.append(f"cleanup is {end['cleanup']}")
        if end["issues"]:
            reasons.append(f"it lists {plural(len(end['issues']), 'issue')}")
        reasons += [f"the run-start's {path} is unavailable" for path in self.unavailable[:1]]
        if end["status"] == "pass" and reasons:
            raise Invalid(number, f"status is pass, but {reasons[0]}")
        if end["status"] != "pass" and not reasons:
            raise Invalid(number, f"status is {end['status']}, but the run meets every condition of a pass")
        self.end = end
        self.end_line = number


def validate_stream(data, manifest, schema):
    """Validates one stream, the bytes of a file. Returns its Run if it is
    valid, and raises Invalid at the first error that it finds otherwise. The
    prerequisites of an xfail are checked at the run-end, since they may come
    after it."""
    lines = data.split(b"\n")
    cut = None
    if lines[-1] == b"":
        lines.pop()
    else:
        cut = len(lines)
    if not lines:
        raise Invalid(1, "the stream is empty")
    reader = _Reader(manifest, schema)
    for number, line in enumerate(lines, 1):
        if number == cut:
            raise Invalid(number, "the last line doesn't end with LF")
        if b"\r" in line:
            raise Invalid(number, "ends with CR LF" if line.endswith(b"\r") else "has a CR")
        if not line.strip(b" \t"):
            raise Invalid(number, "is blank")
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as error:
            raise Invalid(number, f"is not UTF-8: byte {error.start + 1} is 0x{line[error.start]:02x}") from error
        if text.startswith("\ufeff"):
            raise Invalid(number, "starts with a byte order mark")
        try:
            event = parse_json(text)
        except json.JSONDecodeError as error:
            raise Invalid(number, f"is not JSON: {error.msg}, at column {error.colno}") from error
        except ValueError as error:
            raise Invalid(number, str(error)) from error
        except RecursionError as error:
            raise Invalid(number, "is nested too deeply") from error
        if not isinstance(event, dict):
            raise Invalid(number, "is not a JSON object")
        for string in strings(event):
            match = SURROGATE.search(string)
            if match:
                raise Invalid(number, f"a string holds the lone surrogate \\u{ord(match.group()):04x}, "
                                      "which UTF-8 can't encode")
        try:
            reader.add(number, event)
        except RecursionError as error:
            raise Invalid(number, "is nested too deeply") from error
    return reader.finish(len(lines))


def validate(args):
    try:
        manifest = Manifest.load(args.manifest)
        schema = Schema.load(args.schema)
    except Unusable as error:
        print(f"editor-results.py: {error}", file=sys.stderr)
        return 2
    status = 0
    for path in args.streams:
        try:
            run = validate_stream(Path(path).read_bytes(), manifest, schema)
        except OSError as error:
            print(f"{path}: invalid: can't read it: {error.strerror}")
            status = 2
        except Invalid as error:
            print(f"{path}: invalid: {error}")
            status = 2
        else:
            print(f"{path}: {run.summary()}")
            status = max(status, 0 if run.passed else 1)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(prog="editor-results.py", description=(
        "Validate the editor tests' result streams (test/e2e/RESULTS.md)."))
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("validate", help="validate result streams, one run in each", description=(
        "Validate each stream against the manifest and the schema. Exits with 0 if every stream is valid "
        "and its run passed, 1 if every stream is valid but a run didn't pass, and 2 if a stream is invalid."))
    command.add_argument("--manifest", default=str(MANIFEST), metavar="PATH",
                         help="the coverage manifest (default: test/e2e/coverage.json)")
    command.add_argument("--schema", default=str(SCHEMA), metavar="PATH",
                         help="the result schema (default: test/e2e/results-v1.schema.json)")
    command.add_argument("streams", nargs="+", metavar="STREAM", help="a result stream")
    args = parser.parse_args(argv)
    return validate(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # A bug here mustn't exit with 1, which means a run that didn't pass.
        traceback.print_exc()
        sys.exit(2)
