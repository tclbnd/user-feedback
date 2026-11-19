"""GROUP BY clauses and their expansion into grouping sets.

A GROUP BY is a list of elements, each one of
  "col"                               a plain column
  {"rollup": [e, ...]}                ROLLUP(...), e is a column or a list of columns
  {"cube": [e, ...]}                  CUBE(...)
  {"sets": [[cols], [cols], ...]}     GROUPING SETS ((...), (...))
and ``distinct`` (GROUP BY DISTINCT) removes duplicate grouping sets.
The elements of a GROUP BY are combined by cross product.

Column order inside a grouping set is the order in which the columns
appear in the GROUP BY clause, as in PostgreSQL.  Duplicate columns
inside one set are collapsed (a grouping set is a set of columns), but
duplicate grouping sets are only collapsed under GROUP BY DISTINCT:
with the default GROUP BY ALL every expanded set survives, and each one
produces its own (possibly identical) result rows.
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


def _dedup_cols(cols):
    """Drop a column repeated inside one grouping set, keep first order."""
    seen = set()
    out = []
    for c in cols:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def expand(group_by, distinct=False):
    sets = [[]]
    for el in group_by:
        sets = [a + b for a in sets for b in element_sets(el)]
    sets = [_dedup_cols(s) for s in sets]
    if not distinct:
        # GROUP BY ALL: identical grouping sets survive and each emits rows.
        return sets
    # GROUP BY DISTINCT: collapse equal grouping sets, keep first occurrence.
    out = []
    seen = set()
    for s in sets:
        key = tuple(sorted(set(s)))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
