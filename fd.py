"""Functional dependency algorithms: closure, minimal cover, candidate keys."""
from itertools import combinations


def closure(attrs, fds):
    """Closure of an attribute set under ``fds``, iterated to a fixpoint."""
    result = set(attrs)
    changed = True
    while changed:
        changed = False
        for lhs, rhs in fds:
            if set(lhs) <= result:
                grown = result | set(rhs)
                if grown != result:
                    result, changed = grown, True
    return result


def minimal_cover(fds, order):
    """Equivalent minimal (canonical) cover.

    1. split right sides into singletons, dropping trivial and duplicate FDs
       (input order);
    2. for each FD in turn, drop extraneous left attributes (schema order);
    3. drop dependencies implied by the remaining ones, in order.
    """
    rank = {a: i for i, a in enumerate(order)}

    # 1. singleton right sides, no trivial / duplicate dependencies
    cover, seen = [], set()
    for lhs, rhs in fds:
        lhs_t = tuple(sorted(set(lhs), key=rank.get))
        for a in rhs:
            if a in lhs_t:  # trivial X...,a -> a
                continue
            fd = (lhs_t, (a,))
            if fd not in seen:
                seen.add(fd)
                cover.append(fd)

    # 2. remove extraneous left-hand attributes, one FD at a time
    live = list(cover)
    slim = []
    for lhs, (a,) in live:
        cols = list(lhs)
        i = 0
        while i < len(cols):
            trial = tuple(cols[:i] + cols[i + 1:])
            if a in closure(trial, live):  # implied without this attribute
                cols = list(trial)
            else:
                i += 1
        slim.append((tuple(cols), (a,)))

    # left reduction may have collapsed two FDs onto the same one
    cover, seen = [], set()
    for fd in slim:
        if fd not in seen:
            seen.add(fd)
            cover.append(fd)

    # 3. remove redundant dependencies in order
    i = 0
    while i < len(cover):
        lhs, (a,) = cover[i]
        others = cover[:i] + cover[i + 1:]
        if a in closure(lhs, others):
            cover.pop(i)
        else:
            i += 1
    return cover


def merged(cover):
    """Group the singleton-RHS cover by left side, preserving cover order."""
    out = {}
    for lhs, rhs in cover:
        out.setdefault(lhs, []).extend(rhs)
    return list(out.items())


def _key_rank(key, pos):
    return (len(key), tuple(sorted(pos[a] for a in key)))


def candidate_keys(attrs, fds):
    """Every minimal attribute set whose closure is the whole relation."""
    attrs = list(attrs)
    pos = {a: i for i, a in enumerate(attrs)}
    whole = set(attrs)
    rhs_attrs = {a for _, rhs in fds for a in rhs}
    lhs_attrs = {a for lhs, _ in fds for a in lhs}

    # never on a right side: forced into every key.
    must = [a for a in attrs if a not in rhs_attrs]
    # right-side-only attributes can never help a closure: never in a key.
    optional = [a for a in attrs if a in rhs_attrs and a in lhs_attrs]

    if closure(must, fds) >= whole:
        return [list(must)]

    keys = []  # frozensets, all sizes must be explored (keys may differ in size)
    for size in range(1, len(optional) + 1):
        for extra in combinations(optional, size):
            cand = set(must) | set(extra)
            if any(set(k) <= cand for k in keys):  # contains a smaller key
                continue
            if closure(cand, fds) >= whole:
                keys.append(frozenset(cand))

    return [[a for a in attrs if a in k]
            for k in sorted(keys, key=lambda k: _key_rank(k, pos))]
