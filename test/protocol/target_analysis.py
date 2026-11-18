#!/usr/bin/env python3
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""A version 2 project analyzed for x86-64-linux over stdio (ADR-0018).

test/fixtures/projects/native-target is copied to a temporary directory, with
vendor/virgil as the Virgil root. Its "native" project names the target and
"portable" doesn't; both select shared/Words.v3, and portable/Main.v3 calls
the synthetic System itself, so a target leaking into "portable" shows there.
The clean target files get no diagnostics, and neither do the clean pinned
runtime's files. Hover, definition into
rt/native and rt/x86-64-linux, and references answer in the target project,
and the shared file answers from its active project, "native". An unsaved
type error is published and cleared. Rename refuses a runtime declaration and
renames one of the project's own. Signature help and completion in text that
doesn't parse, which run query analyses, see the target's types, and so does an analysis that substitutes a body
that doesn't parse. The development request for a document analyzes for the
target, and explicit URIs don't. Setting the target to null in an unsaved
project file brings back UnresolvedType, and restoring it clears it. Without
a Virgil root, a target project with virgilDependencies gets MissingVirgilRoot
at its target value.
"""
import json
from pathlib import Path
import shutil
import sys
import tempfile

from worker import Session

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / 'test/fixtures/projects/native-target'
VIRGIL = REPO / 'vendor/virgil'


def position(text, needle, skip=0, start=0):
    offset = text.index(needle, start) + skip
    prefix = text[:offset]
    return {'line': prefix.count('\n'),
            'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def location(path, text, name, start=0):
    begin = position(text, name, start=start)
    return {'uri': path.as_uri(), 'range': {'start': begin, 'end': {
        'line': begin['line'], 'character': begin['character'] + len(name)}}}


class Client:
    def __init__(self, s):
        self.s = s
        self.versions = {}

    def open(self, path, text, language='virgil'):
        self.versions[path] = 1
        self.s.send('textDocument/didOpen', {'textDocument': {
            'uri': path.as_uri(), 'languageId': language, 'version': 1, 'text': text}}, request=False)

    def change(self, path, text):
        self.versions[path] += 1
        self.s.send('textDocument/didChange', {'textDocument': {'uri': path.as_uri(), 'version': self.versions[path]},
                                              'contentChanges': [{'text': text}]}, request=False)

    def at(self, method, path, text, needle, skip=0, start=0, **extra):
        params = {'textDocument': {'uri': path.as_uri()}, 'position': position(text, needle, skip, start)}
        params.update(extra)
        return self.s.reply(self.s.send(method, params))

    def result(self, method, path, text, needle, skip=0, start=0, **extra):
        reply = self.at(method, path, text, needle, skip, start, **extra)
        assert 'result' in reply, reply
        return reply['result']

    def published(self):
        """The last list published for each URI so far."""
        lists = {}
        for direction, m in self.s.transcript:
            if direction == 'recv' and m.get('method') == 'textDocument/publishDiagnostics':
                lists[m['params']['uri']] = m['params']['diagnostics']
        return lists


def codes(diagnostics):
    return sorted({d['code'] for d in diagnostics})


def run(exe, root):
    native, words, portable = root / 'native/Native.v3', root / 'shared/Words.v3', root / 'portable/Main.v3'
    config = root / '.virgil-lsp.json'
    native_text, words_text, config_text = native.read_text(), words.read_text(), config.read_text()
    ri_runtime = VIRGIL / 'rt/native/RiRuntime.v3'
    linux_const = VIRGIL / 'rt/x86-64-linux/LinuxConst.v3'
    system = VIRGIL / 'rt/x86-64-linux/System.v3'
    s = Session(exe)
    c = Client(s)
    try:
        s.result(s.send('initialize', {
            'rootUri': root.as_uri(), 'initializationOptions': {'virgilRoot': str(VIRGIL)},
            'capabilities': {'workspace': {'workspaceEdit': {'documentChanges': True}}}}))
        s.send('initialized', {}, request=False)
        c.open(native, native_text)
        c.open(words, words_text)
        # Hover waits for the first analysis. nextFrame's types come from
        # the target's Pointer.
        hover = c.result('textDocument/hover', native, native_text, 'nextFrame')
        assert hover['contents']['value'] == \
            '```virgil\ndef nextFrame(ip: Pointer, sp: Pointer) -> (Pointer, Pointer)\n```', hover
        lists = c.published()
        assert lists.get(native.as_uri(), []) == [], lists
        assert lists.get(words.as_uri(), []) == [], lists
        # The pinned runtime is clean, so nothing is published for its files.
        # A runtime file's own errors would be published by its owner, the
        # active project that contains it, as for any dependency.
        rt = [uri for uri in lists if uri.startswith(VIRGIL.as_uri() + '/')]
        assert rt == [], rt
        # Definition goes into the runtime sources.
        ri_text, const_text = ri_runtime.read_text(), linux_const.read_text()
        assert c.result('textDocument/definition', native, native_text, 'extends RiUserCode', 8) == \
            location(ri_runtime, ri_text, 'RiUserCode', ri_text.index('class RiUserCode')), 'RiUserCode'
        assert c.result('textDocument/definition', native, native_text, 'SYS_write') == \
            location(linux_const, const_text, 'SYS_write', const_text.index('def SYS_write')), 'SYS_write'
        # References of RiUserCode include the runtime's own uses.
        refs = c.result('textDocument/references', native, native_text, 'extends RiUserCode', 8,
                        context={'includeDeclaration': True})
        files = {r['uri'] for r in refs}
        assert {native.as_uri(), ri_runtime.as_uri()} <= files, files
        assert all(uri == native.as_uri() or uri.startswith((VIRGIL / 'rt').as_uri() + '/') for uri in files), files
        assert location(native, native_text, 'RiUserCode', native_text.index('extends')) in refs, refs
        # The shared file answers from its active project, the target one:
        # its System is the runtime's source component.
        system_text = system.read_text()
        assert c.result('textDocument/definition', words, words_text, 'System.puts') == \
            location(system, system_text, 'System', system_text.index('component System')), 'System'
        refs = c.result('textDocument/references', words, words_text, 'greeting',
                        context={'includeDeclaration': True})
        assert {r['uri'] for r in refs} == {words.as_uri(), native.as_uri()}, refs
        # Rename refuses a runtime declaration, as for any dependency...
        reply = c.at('textDocument/rename', native, native_text, 'extends RiUserCode', 8, newName='Code')
        assert reply.get('error', {}).get('code') == -32803, reply
        assert 'declared in a dependency' in reply['error']['message'], reply
        # ...and renames the project's own.
        edit = c.result('textDocument/rename', native, native_text, 'StubCode', newName='Stub')
        changes = edit['documentChanges']
        assert [d['textDocument']['uri'] for d in changes] == [native.as_uri()], changes
        assert len(changes[0]['edits']) == 2 and {e['newText'] for e in changes[0]['edits']} == {'Stub'}, changes
        # Signature help and completion in text that doesn't parse run their
        # own query analyses, for the target too: without it, Pointer would
        # be '?'.
        call = 'var code = StubCode.new(CiRuntime.CODE_START, '
        partial = native_text.replace('var code = StubCode.new(CiRuntime.CODE_START, CiRuntime.CODE_END);', call)
        assert partial != native_text
        c.change(native, partial)
        help = c.result('textDocument/signatureHelp', native, partial, call, len(call))
        assert [g['label'] for g in help['signatures']] == ['new(start: Pointer, end: Pointer)'], help
        assert help['activeParameter'] == 1, help
        member = 'Words.say(if(code.'
        partial = native_text.replace('Words.say(if(code.start', member)
        c.change(native, partial)
        items = c.result('textDocument/completion', native, partial, member, len(member))
        details = {i['label']: i.get('detail') for i in items['items']}
        assert details.get('nextFrame') == 'def nextFrame(ip: Pointer, sp: Pointer) -> (Pointer, Pointer)', details
        assert details.get('start') == 'var start: Pointer', details
        # A body that doesn't parse is analyzed through method-body fallback
        # or current-text repair, still for the target: only the syntax
        # error is published, and nextFrame keeps its Pointer types.
        partial = native_text.replace('var p = ', 'var bad = ;\n\t\tvar p = ', 1)
        assert partial != native_text
        c.change(native, partial)
        hover = c.result('textDocument/hover', native, partial, 'nextFrame')
        assert 'Pointer, sp: Pointer' in hover['contents']['value'], hover
        assert codes(c.published()[native.as_uri()]) == ['ParseError'], c.published()[native.as_uri()]
        c.change(native, native_text)
        # An unsaved type error is published, and fixing it clears it.
        broken = native_text.replace('return if(r.0 >= 0, 0, 1);', 'return Pointer.SIZE + "";')
        assert broken != native_text
        c.change(native, broken)
        c.result('textDocument/hover', native, broken, 'nextFrame')
        assert codes(c.published()[native.as_uri()]) == ['TypeError'], c.published()[native.as_uri()]
        c.change(native, native_text)
        c.result('textDocument/hover', native, native_text, 'nextFrame')
        assert c.published()[native.as_uri()] == [], c.published()
        # The development request for a document analyzes its project for
        # the target; the same files as explicit URIs form no project and
        # have no target, so the runtime's System redefines the synthetic one.
        result = s.result(s.send('virgil-lsp/analyze', {'uri': native.as_uri()}))
        assert result['diagnostics'] == [] and len(result['documentVersions']) == 14, result
        uris = [v['uri'] for v in result['documentVersions']]
        result = s.result(s.analyze(uris))
        kinds = {d['kind'] for d in result['diagnostics']}
        assert 'TypeRedefined' in kinds, result['diagnostics']
        # Without the target, RiUserCode and Pointer can't be found.
        untargeted = config_text.replace('"target": "x86-64-linux"', '"target": null')
        assert untargeted != config_text
        c.open(config, untargeted, 'json')
        hover = c.result('textDocument/hover', native, native_text, 'nextFrame')
        assert hover['contents']['value'] == '```virgil\ndef nextFrame(ip: ?, sp: ?) -> (?, ?)\n```', hover
        assert 'UnresolvedType' in codes(c.published()[native.as_uri()]), c.published()[native.as_uri()]
        assert c.published().get(config.as_uri(), []) == [], c.published()
        # The runtime files are no longer in the project.
        assert c.result('textDocument/definition', native, native_text, 'extends RiUserCode', 8) is None
        c.change(config, config_text)
        hover = c.result('textDocument/hover', native, native_text, 'nextFrame')
        assert 'Pointer, sp: Pointer' in hover['contents']['value'], hover
        assert c.published()[native.as_uri()] == [], c.published()
        # The portable program still verifies, with the synthetic System.
        portable_text = portable.read_text()
        c.open(portable, portable_text)
        assert c.result('textDocument/hover', portable, portable_text, 'say')['contents']['value'] == \
            '```virgil\ndef say(s: Array<byte>)\n```'
        assert c.published().get(portable.as_uri(), []) == [], c.published()
        rt = [uri for uri, d in c.published().items() if uri.startswith(VIRGIL.as_uri() + '/') and d]
        assert rt == [], rt
        s.shutdown()
    except BaseException:
        print(json.dumps(s.transcript, indent=2)[-20000:], file=sys.stderr)
        print(s.logs()[-5000:], file=sys.stderr)
        raise
    finally:
        s.close()


class WarnedSession(Session):
    # An invalid project file also gets a warning.
    NOTIFICATIONS = Session.NOTIFICATIONS + ('window/showMessage',)


def without_root(exe, root):
    """Without a Virgil root, a target project that also lists
    virgilDependencies gets one MissingVirgilRoot, at the target value."""
    config = root / '.virgil-lsp.json'
    text = json.dumps({'version': 2, 'projects': [{
        'name': 'native', 'sources': ['native/*.v3'], 'virgilDependencies': ['lib/util/*.v3'],
        'target': 'x86-64-linux'}]})
    s = WarnedSession(exe)
    c = Client(s)
    try:
        # A development build has no bundle, so no root is selected.
        s.result(s.send('initialize', {'rootUri': root.as_uri()}))
        c.open(config, text, 'json')
        s.ping()
        diagnostics = c.published()[config.as_uri()]
        assert [d['code'] for d in diagnostics] == ['MissingVirgilRoot'], diagnostics
        assert diagnostics[0]['range'] == location(config, text, '"x86-64-linux"')['range'], diagnostics
        s.shutdown()
    except BaseException:
        print(json.dumps(s.transcript, indent=2)[-20000:], file=sys.stderr)
        print(s.logs()[-5000:], file=sys.stderr)
        raise
    finally:
        s.close()


def main():
    exe = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='target-analysis-', dir=exe.parent) as work:
        root = Path(work).resolve() / 'native-target'
        shutil.copytree(FIXTURE, root)
        run(exe, root)
        without_root(exe, root)
    print('protocol target analysis: diagnostics, hover, definition, references, rename, shared file, '
          'target changes, and a missing root passed')


if __name__ == '__main__':
    main()
