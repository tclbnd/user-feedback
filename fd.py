"""Functional dependency algorithms: closure, minimal cover, candidate keys."""
from itertools import combinations


def closure(attrs, fds):
    result = set(attrs)
    for lhs, rhs in fds:
        if set(lhs) <= result:
            result |= set(rhs)
    return result


def minimal_cover(fds, order):
    rank = {a: i for i, a in enumerate(order)}
    cover = []
    for lhs, rhs in fds:
        for a in rhs:
            fd = (tuple(sorted(set(lhs), key=rank.get)), (a,))
            if a not in lhs and fd not in cover:
                cover.append(fd)
    for fd in list(cover):
        others = [f for f in cover if f != fd]
        if set(fd[1]) <= closure(fd[0], others):
            cover = others
    return cover


def merged(cover):
    out = {}
    for lhs, rhs in cover:
        out.setdefault(lhs, []).extend(rhs)
    return list(out.items())


def candidate_keys(attrs, fds):
    rhs_attrs = {a for _, rhs in fds for a in rhs}
    core = [a for a in attrs if a not in rhs_attrs]
    others = [a for a in attrs if a in rhs_attrs]
    keys = []
    for size in range(len(others) + 1):
        for extra in combinations(others, size):
            cand = set(core) | set(extra)
            if any(set(k) <= cand for k in keys):
                continue
            if closure(cand, fds) >= set(attrs):
                keys.append([a for a in attrs if a in cand])
        if keys:
            break
    return keys
