"""ORDER BY items and sorting with NULL placement.

An ORDER BY item is {"col": name, "dir": "asc"|"desc", "nulls": "first"|"last"|null}.
When ``nulls`` is not given it takes the SQL default for the direction.
"""
import functools


def normalize(item):
    d = item.get("dir", "asc")
    nulls = item.get("nulls") or "last"
    return {"col": item["col"], "dir": d, "nulls": nulls}


def compare_rows(a, b, items):
    for it in map(normalize, items):
        x, y = a.get(it["col"]), b.get(it["col"])
        if x is None and y is None:
            continue
        if x is None or y is None:
            first = x is None
            return -1 if (first == (it["nulls"] == "first")) else 1
        if x != y:
            c = -1 if x < y else 1
            return c if it["dir"] == "asc" else -c
    return 0


def sort_rows(rows, items):
    return sorted(rows, key=functools.cmp_to_key(lambda a, b: compare_rows(a, b, items)))
