# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
"""Editing histories for the opt-in tolerance session suite.

Actions retain both the editor text and its intended, syntax-correct text.
The driver replays the same protocol against each, in separate processes.
"""

from pathlib import Path


def action(op, path, text=None, intended=None):
    value = {"op": op, "path": path}
    if text is not None:
        value.update(text=text, intended=text if intended is None else intended)
    return value


def step(name, *actions, **checks):
    return {"id": name, "actions": list(actions), **checks}


def fixtures(corpus, states):
    a = ('component A {\n def edit() { var x = 0; }\n def other() { var y = 0; }\n'
         ' def target() -> int { return 1; }\n}\n')
    b = a.replace('component A', 'component B')
    consumer = ('component Consumer { def use() -> int { return A.target() + B.target(); } }\n')
    broken_a = a.replace('var x = 0;', 'var x = ;')
    broken_b = b.replace('var x = 0;', 'var x = ;')

    def case(name, description, steps, sources=None, **extra):
        project = corpus.project('sessions/' + name, sources or {'A.v3': a, 'B.v3': b, 'Consumer.v3': consumer})
        queries = project['queries'] + [states.query('Consumer.v3', project['sources']['Consumer.v3'],
                                                   owner + '.target()', 'target') for owner in ('A', 'B')]
        return {'id': name, 'description': description, 'project': project,
                'queries': queries, 'initial': {'A.v3': project['sources']['A.v3'], 'B.v3': b},
                'steps': steps, **extra}

    yield case('in-turn', 'Break A, break B, fix A, then fix B.', [
        step('a-broken', action('change', 'A.v3', broken_a, a)),
        step('b-broken', action('change', 'B.v3', broken_b, b)),
        step('a-fixed', action('change', 'A.v3', a)),
        step('b-fixed', action('change', 'B.v3', b))], smoke=True)
    yield case('overlapping', 'Break A and B, then fix B before A.', [
        step('a-broken', action('change', 'A.v3', broken_a, a)),
        step('b-broken', action('change', 'B.v3', broken_b, b)),
        step('b-fixed', action('change', 'B.v3', b)),
        step('a-fixed', action('change', 'A.v3', a))])
    yield case('save-close-reopen', 'Save broken A, close it on disk, reopen, then fix it.', [
        step('broken', action('change', 'A.v3', broken_a, a)),
        step('saved', action('save', 'A.v3', broken_a, a)),
        step('closed', action('close', 'A.v3'), absent_substitutions=['A.v3']),
        step('reopened', action('open', 'A.v3', broken_a, a)),
        step('fixed', action('change', 'A.v3', a))])
    added = ('component Added {\n def edit() { var n = 0; }\n def target() -> int { return 3; }\n'
             ' def check() -> int { return target(); }\n}\n')
    new = case('new-file', 'Create and open a broken new project member, then fix it without restarting.', [
        step('a-broken', action('change', 'A.v3', broken_a, a)),
        step('created', action('write', 'Added.v3', added.replace('n = 0', 'n = '), added),
             action('open', 'Added.v3', added.replace('n = 0', 'n = '), added), member='Added.v3'),
        step('added-fixed', action('change', 'Added.v3', added), member='Added.v3'),
        step('all-fixed', action('change', 'A.v3', a), member='Added.v3')])
    new['project']['config']['projects'][0]['sources'] = ['*.v3']
    for st in new['steps'][1:]:
        st['queries'] = new['queries'] + [states.query('Added.v3', added, 'return target()', 'target')]
        if st['id'] != 'created':
            st['full_semantics'] = True
    new['steps'][-1]['no_substitutions'] = True
    yield new
    newest = a.replace('def target() -> int { return 1; }', 'def target() -> bool { return true; }')
    yield case('newest-parseable', 'Fix A with a new target signature, then break its other body.', [
        step('old-body-broken', action('change', 'A.v3', broken_a, a)),
        step('new-signature', action('change', 'A.v3', newest)),
        step('other-body-broken', action('change', 'A.v3', newest.replace('var y = 0;', 'var y = ;'), newest),
             stale_control=True, substitution={'A.v3': 'fallback'}, full_semantics=True),
        step('fixed', action('change', 'A.v3', newest))])
    yield case('watched-disk', 'Close B, write a broken disk body with a file event, then restore it.', [
        step('a-broken', action('change', 'A.v3', broken_a, a)),
        step('b-closed', action('close', 'B.v3')),
        step('disk-broken', action('write', 'B.v3', broken_b, b), absent_substitutions=['B.v3']),
        step('disk-fixed', action('write', 'B.v3', b)),
        step('all-fixed', action('change', 'A.v3', a))])

    # Errors in one compound let verification reach navigation before its
    # between-compound cutoff. Observer is first and supplies one more error.
    errors = ''.join('var e%d: int = true; ' % i for i in range(14))
    limit_a = a.replace('var y = 0;', errors)
    for name, repair, hidden in [('limit-fallback-visible', False, False),
                                 ('limit-fallback-excluded', False, True),
                                 ('limit-repair', True, False)]:
        original = limit_a.replace('var x = 0;', errors) if hidden else limit_a
        # Repair is forced by also changing a declaration outside the broken body.
        intended = original.replace(errors, 'var x = 0;', 1) if hidden else original
        if repair:
            intended = intended.replace('target() -> int { return 1;', 'target() -> bool { return true;')
        broken = intended.replace('var x = 0;', 'var x = ;', 1)
        # Fallback of the error-filled body excludes its errors, which
        # suppresses the notice. Repair blanks edit(), which returns nothing,
        # so it excludes no error, and the notice stays.
        yield case(name, 'Reach the semantic error limit with ' + ('repair.' if repair else 'fallback.'), [
            step('limit-broken', action('change', 'A.v3', broken, intended),
                 limit_notice={"warm": not hidden, "cold": True}, limit_reached=True,
                 substitution={'A.v3': 'repair' if repair else {'warm': 'fallback', 'cold': 'repair'}}),
            step('fixed', action('change', 'A.v3', intended), limit_notice=True, limit_reached=True)],
            sources={'A.v3': original, 'B.v3': b, 'Consumer.v3': consumer})


def pressure(corpus, states):
    """Two complete Aeneas contexts exceed the global cache, but each fits analysis."""
    originals = {str(Path(p).relative_to(states.ROOT / 'vendor/virgil')):
                 corpus.remember(p) for p in states.repair.aeneas_files()}
    early = 'aeneas/src/arm64/Arm64AddrPatcher.v3'
    late = 'lib/util/StringBuilder.v3'
    sources = {f'{prefix}/{path}': text for prefix in ('first', 'second') for path, text in originals.items()}
    observer = states.OBSERVER
    sources.update({f'{prefix}/Observer.v3': observer for prefix in ('first', 'second')})
    project = corpus.project('sessions/cache-pressure', sources)
    del project['sources']['Observer.v3']
    (project['base'] / 'Observer.v3').unlink()
    project['observer'] = 'first/Observer.v3'
    project['config']['projects'] = [{'name': prefix, 'sources': [p for p in sources if p.startswith(prefix + '/')]}
                                      for prefix in ('first', 'second')]
    queries = {prefix: [states.query(prefix + '/Observer.v3', observer, 'stable(1)', 'stable'),
                       states.query(prefix + '/aeneas/src/util/Paths.v3', originals['aeneas/src/util/Paths.v3'],
                                            'StringBuilder.new', 'StringBuilder')] for prefix in ('first', 'second')}
    old = originals[early]
    broken = old.replace('def pos = mw.pos;', 'def pos = ;', 1)
    assert old != broken
    cached = originals[late]
    cached_broken = cached.replace('var offset = 0;', 'var offset = ;', 1)
    assert cached != cached_broken
    charge = {prefix: sum(512 + len((project['base'] / p).as_uri().encode()) + len(t.encode())
                          for p, t in sources.items() if p.startswith(prefix + '/')) for prefix in queries}
    assert max(charge.values()) < 4 * 1024 * 1024 < sum(charge.values())
    return {'id': 'cache-pressure', 'description': 'Analyze two Aeneas contexts, break an evicted file, then a retained file.',
            'project': project, 'initial': {'first/' + early: old}, 'queries': queries['first'],
            'cache_charge': charge,
            'steps': [
                step('first-analyzed', queries=queries['first']),
                step('second-analyzed', action('open', 'second/' + early, old),
                     action('open', 'second/Observer.v3', observer), queries=queries['second']),
                step('evicted-broken', action('change', 'first/' + early, broken, old), queries=queries['first'],
                     substitution={'first/' + early: 'repair'}),
                step('retained-broken', action('open', 'first/' + late, cached_broken, cached), queries=queries['first'],
                     substitution={'first/' + early: 'repair', 'first/' + late: 'fallback'})]}
