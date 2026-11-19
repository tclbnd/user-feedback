"""Merge join over two inputs sorted on the join key (NULLs last).

Supported join kinds: inner, left, right, full, semi, anti.  Output rows are
(left id, right id) with None for the side that has no match.  ``filters``
are the extra join conditions (see keys.py).
"""
from .keys import key_of, passes, sort_key


def merge_join(left, right, kind="inner", filters=None):
    lk, rk = left.key, right.key
    out = []
    l, r = left.next(), right.next()
    right_matched = False
    while l is not None and r is not None:
        a, b = sort_key(key_of(l, lk)), sort_key(key_of(r, rk))
        if a < b:
            if kind in ("left", "full", "anti"):
                out.append((l["id"], None))
            l = left.next()
        elif a > b:
            if kind in ("right", "full") and not right_matched:
                out.append((None, r["id"]))
            r, right_matched = right.next(), False
        else:
            ok = passes(filters, l, r)
            if kind == "anti":
                pass
            elif kind == "semi":
                if ok:
                    out.append((l["id"], None))
            elif ok:
                out.append((l["id"], r["id"]))
            right_matched = True
            l = left.next()
            if l is None or sort_key(key_of(l, lk)) != b:
                r, right_matched = right.next(), False
    while l is not None:
        if kind in ("left", "full", "anti"):
            out.append((l["id"], None))
        l = left.next()
    while r is not None:
        if kind in ("right", "full") and not right_matched:
            out.append((None, r["id"]))
        r, right_matched = right.next(), False
    return out
