"""GROUP BY clauses and their expansion into grouping sets.

A GROUP BY is a list of elements, each one of
  "col"                               a plain column
  {"rollup": [e, ...]}                ROLLUP(...), e is a column or a list of columns
  {"cube": [e, ...]}                  CUBE(...)
  {"sets": [[cols], [cols], ...]}     GROUPING SETS ((...), (...))
and ``distinct`` (GROUP BY DISTINCT) removes duplicate grouping sets.
The elements of a GROUP BY are combined by cross product.
"""
from itertools import combinations


def _cols(e):
    return list(e) if isinstance(e, list) else [e]


def element_sets(el):
    if isinstance(el, str):
        return [[el]]
    if "rollup" in el:
        items = [_cols(e) for e in el["rollup"]]
        return [sum(items[:k], []) for k in range(len(items), -1, -1)]
    if "cube" in el:
        items = [_cols(e) for e in el["cube"]]
        out = []
        for k in range(len(items), -1, -1):
            for combo in combinations(items, k):
                out.append(sum(combo, []))
        return out
    if "sets" in el:
        return [list(s) for s in el["sets"]]
    raise ValueError(el)


def expand(group_by, distinct=False):
    sets = [[]]
    for el in group_by:
        sets = [a + b for a in sets for b in element_sets(el)]
    out = []
    for s in sets:
        key = sorted(set(s))
        if key not in out:
            out.append(key)
    return out
