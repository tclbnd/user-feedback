"""Merge join over two inputs sorted on the join key (NULLs last).

Supported join kinds: inner, left, right, full, semi, anti.  Output rows are
(left id, right id) with None for the side that has no match.  ``filters``
are the extra join conditions (see keys.py).

Both inputs are read exactly once, front to back (linear scan).  A run of
rows sharing one key is buffered in memory while it is consumed, so the
matching run on the other side is never re-read: no rewind/restore of the
right input happens anywhere in this module.
"""
from .keys import has_null, key_of, passes, sort_key

_LEFT_UNMATCHED_KINDS = ("left", "full", "anti")
_RIGHT_UNMATCHED_KINDS = ("right", "full")
_PAIR_KINDS = ("inner", "left", "right", "full")


def merge_join(left, right, kind="inner", filters=None):
    lk, rk = left.key, right.key
    out = []
    l, r = left.next(), right.next()

    while l is not None and r is not None:
        a, b = key_of(l, lk), key_of(r, rk)
        sa, sb = sort_key(a), sort_key(b)

        # SQL semantics: a NULL key equals nothing, not even another NULL.
        # NULLs sort last, so once one side reaches its NULL run every
        # remaining row on both sides is unmatched.
        if has_null(a) or has_null(b):
            if has_null(a):
                if kind in _LEFT_UNMATCHED_KINDS:
                    out.append((l["id"], None))
                l = left.next()
            if l is not None and has_null(b):
                if kind in _RIGHT_UNMATCHED_KINDS:
                    out.append((None, r["id"]))
                r = right.next()
            continue

        if sa < sb:
            if kind in _LEFT_UNMATCHED_KINDS:
                out.append((l["id"], None))
            l = left.next()
            continue
        if sa > sb:
            if kind in _RIGHT_UNMATCHED_KINDS:
                out.append((None, r["id"]))
            r = right.next()
            continue

        # Equal, non-NULL keys: drain both runs of that key.  The right run
        # is read once into a buffer, then reused from memory for every left
        # row, which gives the m*n cross product without re-reading right.
        left_run = [l]
        l = left.next()
        while l is not None and sort_key(key_of(l, lk)) == sa:
            left_run.append(l)
            l = left.next()

        right_run = [r]
        r = right.next()
        while r is not None and sort_key(key_of(r, rk)) == sb:
            right_run.append(r)
            r = right.next()

        for lrow in left_run:
            matched = False
            for rrow in right_run:
                if not passes(filters, lrow, rrow):
                    continue
                matched = True
                if kind in _PAIR_KINDS:
                    out.append((lrow["id"], rrow["id"]))
                elif kind == "semi":
                    # One output row per left row, like the nested loop's break.
                    out.append((lrow["id"], None))
                    break
            if not matched and kind in _LEFT_UNMATCHED_KINDS:
                # Key matched but every extra join condition failed (or the
                # whole right run failed the filters): outer/anti keep it.
                out.append((lrow["id"], None))

        if kind in _RIGHT_UNMATCHED_KINDS:
            for rrow in right_run:
                if not any(
                    not has_null(key_of(lrow, lk))
                    and passes(filters, lrow, rrow)
                    for lrow in left_run
                ):
                    out.append((None, rrow["id"]))

    # Rows left on either side (including NULL-key rows) cannot match anymore.
    while l is not None:
        if kind in _LEFT_UNMATCHED_KINDS:
            out.append((l["id"], None))
        l = left.next()
    while r is not None:
        if kind in _RIGHT_UNMATCHED_KINDS:
            out.append((None, r["id"]))
        r = right.next()
    return out
