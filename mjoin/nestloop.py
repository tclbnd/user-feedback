"""Nested loop join, used when inputs are small or unsorted."""
from .keys import has_null, key_of, passes


def nested_loop(left, right, lkey, rkey, kind="inner", filters=None):
    out = []
    matched_right = set()
    for l in left:
        hit = False
        a = key_of(l, lkey)
        for j, r in enumerate(right):
            b = key_of(r, rkey)
            if not has_null(a) and not has_null(b) and a == b and passes(filters, l, r):
                hit = True
                if kind in ("inner", "left", "right", "full"):
                    out.append((l["id"], r["id"]))
                    matched_right.add(j)
                elif kind == "semi":
                    out.append((l["id"], None))
                    break
        if not hit and kind in ("left", "full", "anti"):
            out.append((l["id"], None))
    if kind in ("right", "full"):
        out += [(None, r["id"]) for j, r in enumerate(right) if j not in matched_right]
    return out
