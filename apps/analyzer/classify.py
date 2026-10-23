"""Requirement of a write template: none, order, or atomic visibility (Secs. 4-5 of the paper).

A template writes rows of several tables; each write is an insertion, a deletion, or a
modification of named attributes.  Constraints between two written tables decide the requirement:
  reference R.P -> S.L   order, if a row of R or S is inserted or deleted or a pointer of R changes
                         (Prop. 5.4: insertions parent first, deletions child first, moves between)
  copy R[P D] <= S[L C]  atomic visibility if a source C_i and its copy D_i both change
                         (Thm. 4.8: two mutable copies of one value); order if the source changes
                         while the pointer moves, or if rows are inserted or deleted
  derived S.A from R     atomic visibility if S.A changes together with rows of R (strict reading:
                         the value equals its definition); order under the one-way reading, in
                         which readers accept a value that lags behind its rows
A template whose constraints link no two written tables is unconstrained.
A cascade that sets a copy to null exempts its row from the IND, as a deletion does, so it needs
order only.  A copy that the application itself breaks, by changing the source without the copy or
the copy without the source or pointer, is no constraint (maintained()).
"""

RANK = {'none': 0, 'order': 1, 'atomic': 2}


def summarize(writes):
    """Operations and modified attributes per table.  A write marked sync = (table, column,
    pointer) re-copies a column of the referenced row and counts only if that column or the
    pointer changes in the template.  A write marked rsync = (table R, column d, pointer of R)
    copies R.d into the row of S that R references (a reverse copy) and counts only if the template
    changes d (or attributes it leaves open), moves the pointer, or inserts or deletes rows of R."""
    ops, mod = {}, {}
    nulled, set_ = {}, {}
    waiting = []
    for w in writes:
        if w.get('sync') or w.get('guard') or w.get('rsync'):
            waiting.append(w)
            continue
        ops.setdefault(w['table'], set()).add(w['op'])
        if w['op'] == 'modify':
            mod.setdefault(w['table'], set()).update(w['attrs'])
            (nulled if w.get('via') == 'dependent: nullify' else set_).setdefault(w['table'], set()).update(w['attrs'])

    def ready(w):
        if w.get('sync'):
            t, col, fk = w['sync']
            if not (col in mod.get(t, set()) or fk in mod.get(w['table'], set())):
                return False
        if w.get('rsync'):
            t, col, fk = w['rsync']
            m = mod.get(t, set())
            if not (col in m or {'?', '*'} & m or set(fk) & m or ops.get(t, set()) & {'insert', 'delete'}):
                return False
        if w.get('guard'):
            t, attrs = w['guard']
            m = mod.get(t, set())
            if not (set(attrs) & m or '?' in m or '*' in m or 'insert' in ops.get(t, set())):
                return False
        return True

    changed = True
    while changed:
        changed = False
        for w in list(waiting):
            if ready(w):
                ops.setdefault(w['table'], set()).add(w['op'])
                if w['op'] == 'modify':
                    mod.setdefault(w['table'], set()).update(w['attrs'])
                    set_.setdefault(w['table'], set()).update(w['attrs'])
                waiting.remove(w)
                changed = True
    summarize.excluded = {id(w) for w in waiting}
    # attributes changed only by nullify cascades
    summarize.nulled = {t: v - set_.get(t, set()) for t, v in nulled.items()}
    return ops, mod


def maintained(templates, copies):
    """Copies that no template breaks, and the broken ones with a template that breaks them.  A
    template breaks R[P D] <= S[L C] if it changes C but not D, or D but neither C nor P (null
    cascades aside).  The write that keeps a reverse copy (S.c := R.d, marked rsync) changes C on
    behalf of D and breaks nothing, as a sync write keeps a forward copy."""
    keep, broken = [], []
    summaries = []
    for tp in templates:
        ops, mod = summarize(tp['writes'])
        # (table, attr) changed by writes other than reverse-copy writes, and the rsync of those
        other, rsync = set(), {}
        for w in tp['writes']:
            if w['op'] != 'modify' or id(w) in summarize.excluded:
                continue
            for a in w['attrs']:
                if w.get('rsync'):
                    rsync.setdefault((w['table'], a), set()).add(w['rsync'])
                else:
                    other.add((w['table'], a))
        summaries.append((tp, {t: v - summarize.nulled.get(t, set()) for t, v in mod.items()}, other, rsync))
    for c in copies:
        R, S = c['table'], c['ref']
        own = (R, c['d'], tuple(c['fk'])) if c.get('flow') == 'reverse' else None
        why = None
        for tp, mod, other, rsync in summaries:
            mS, mR = mod.get(S, set()), mod.get(R, set())
            if R == S:
                break
            cS = c['c'] in mS
            if own and cS and (S, c['c']) not in other and rsync.get((S, c['c']), set()) <= {own}:
                cS = False      # only the copy's own write changes C
            if cS and c['d'] not in mR and not {'*', '?'} & mR:
                why = ('source', tp['origin'])
            elif c['d'] in mR and c['c'] not in mS and not {'*', '?'} & mS and not set(c['fk']) & mR:
                why = ('copy', tp['origin'])
            if why:
                break
        if why:
            broken.append(dict(c, broken=why))
        else:
            keep.append(c)
    return keep, broken


def classify(writes, refs, copies, derived, implicit_derived=True, mutable=None):
    """Returns dict(strict, oneway, reasons) or None for single-table templates."""
    ops, mod = summarize(writes)
    nulled = summarize.nulled
    tables = set(ops)
    if len(tables) < 2:
        return None
    reasons = []

    def touched(t):
        return bool(ops.get(t, set()) & {'insert', 'delete'})

    linked = set()
    for r in refs:
        R, S = r['table'], r['ref']
        if R not in tables or S not in tables or R == S:
            continue
        linked.add((R, S))
        if touched(R) or touched(S) or set(r['cols']) & mod.get(R, set()) or '*' in mod.get(R, set()):
            reasons.append(('order', 'reference', R, S, '/'.join(r['cols'])))
    for c in copies:
        R, S = c['table'], c['ref']
        if R not in tables or S not in tables or R == S:
            continue
        linked.add((R, S))
        mS, mR = mod.get(S, set()), mod.get(R, set())
        # '?': the update of a row whose changed attributes the event leaves open, which may change
        # the source of the copy if some write of the application changes it; the source is S.c, or
        # R.d for a reverse copy (R writes its own d into S)
        rev = c.get('flow') == 'reverse'
        cS = c['c'] in mS or (not rev and '?' in mS and (mutable is None or c['c'] in mutable.get(S, ())))
        dR = c['d'] in mR or (rev and '?' in mR and (mutable is None or c['d'] in mutable.get(R, ())))
        if c['c'] in mS and c['d'] in mR and c['d'] in nulled.get(R, set()):
            reasons.append(('order', 'copy-null', R, S, '%s=%s' % (c['d'], c['c'])))
        elif cS and dR:
            reasons.append(('atomic', 'reverse-copy' if rev else 'copy', R, S, '%s=%s' % (c['d'], c['c'])))
        elif c['c'] in mS and (set(c['fk']) & mR or touched(R)):
            reasons.append(('order', 'copy-move', R, S, '%s=%s' % (c['d'], c['c'])))
        elif touched(R) or touched(S):
            reasons.append(('order', 'copy-insdel', R, S, '%s=%s' % (c['d'], c['c'])))
    for d in derived:
        S, R = d['table'], d['src']
        if R not in tables or S not in tables or R == S:
            continue
        linked.add((R, S))
        if set(d['cols']) & mod.get(S, set()) and (touched(R) or set(d['fk']) & mod.get(R, set())):
            reasons.append(('derived', d['kind'], R, S, '/'.join(d['cols'])))
    if implicit_derived:
        # a callback of R, run when a row of R is inserted or deleted, changes non-pointer attributes
        # of the row of S that it references: S keeps an aggregate of its R rows (last activity,
        # counters kept by hand)
        pointers = {}
        for r in refs:
            pointers.setdefault(r['table'], set()).update(r['cols'])
        for w in writes:
            if id(w) in summarize.excluded:
                continue
            trig = w.get('trigger')
            if w['op'] != 'modify' or not trig or trig[1] not in ('create', 'destroy'):
                continue
            R, S = trig[0], w['table']
            if R == S or not any(r['table'] == R and r['ref'] == S for r in refs):
                continue
            extra = set(w['attrs']) - {'*', '?'} - pointers.get(S, set())
            if extra and not any(x[0] == 'derived' and x[2] == R and x[3] == S for x in reasons):
                reasons.append(('derived', 'maintained', R, S, '/'.join(sorted(extra))))
        # a callback of S, run when a row of S is updated and guarded by a change of S, writes
        # non-pointer attributes of a row of R that references it: R keeps a copy of (a function
        # of) attributes of S (routes.path of a namespace), changed here together with them
        for w in writes:
            if id(w) in summarize.excluded:
                continue
            trig, g = w.get('trigger'), w.get('guard')
            if w['op'] != 'modify' or not trig or trig[1] != 'update' or not g or g[0] != trig[0]:
                continue
            S, R = trig[0], w['table']
            if R == S or not any(r['table'] == R and r['ref'] == S for r in refs):
                continue
            extra = set(w['attrs']) - {'*', '?'} - pointers.get(R, set())
            if extra and set(g[1]) & mod.get(S, set()):
                reasons.append(('atomic', 'pushed', R, S, '/'.join(sorted(extra))))
    strict = 'none'
    oneway = 'none'
    for kind, *_ in reasons:
        s = 'atomic' if kind in ('atomic', 'derived') else kind
        o = 'order' if kind == 'derived' else kind
        if RANK[s] > RANK[strict]:
            strict = s
        if RANK[o] > RANK[oneway]:
            oneway = o
    return dict(strict=strict, oneway=oneway, reasons=reasons, tables=sorted(tables),
                linked=sorted(linked))
