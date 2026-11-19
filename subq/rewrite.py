"""Subquery flattening.

Uncorrelated IN / NOT IN and correlated EXISTS / NOT EXISTS conjuncts at
the top of the WHERE clause are turned into hash semi-joins and
anti-joins: the subquery runs once into a hash table instead of once per
outer row.  The remaining conjuncts are evaluated row by row.  A rewritten
plan must return exactly what row-by-row evaluation returns.

The flattening is NULL-aware, because SQL's ``=`` is three-valued:

* ``x IN (subquery)`` is ``x = ANY(subquery)``.  An outer NULL, or a NULL
  in a compared subquery column that is not outweighed by an equal row,
  makes the predicate *unknown* (rejected by WHERE).
* ``x NOT IN (subquery)`` is the negation of that.  In particular, if the
  subquery contains any NULL that no outer value equals, no row survives --
  the plan must mirror this instead of treating NULL keys as ordinary
  values (the classic "NOT IN with NULL returns no rows" behaviour).
* ``EXISTS`` correlation uses ``inner = outer`` equality, so an inner row
  with a NULL correlation column can never match, and an outer row with a
  NULL correlation value can never match either.

A top-level ``NOT`` wrapped around one of these predicates is folded into
the join's polarity, since ``NOT (x IN s)`` is exactly ``x NOT IN s``.
"""
from .expr import evaluate, subquery_rows, terms
from .logic import row_compare

_SIMPLE_KINDS = (
    ("in", "in"),
    ("not_in", "in"),
    ("exists", "exists"),
    ("not_exists", "exists"),
)


def _conjuncts(where):
    if where is None:
        return []
    return list(where["and"]) if "and" in where else [where]


def _refs_outer(node):
    """True if ``node`` contains a correlated reference {"outer": ...}."""
    if isinstance(node, dict):
        if "outer" in node:
            return True
        return any(_refs_outer(v) for v in node.values())
    if isinstance(node, list):
        return any(_refs_outer(x) for x in node)
    return False


def _predicate(p):
    """Peel any top-level NOTs.

    Returns ``(kind, spec, positive)`` for a simple IN / EXISTS predicate,
    where ``positive`` says whether the original predicate is the un-negated
    form, and ``None`` for anything else.
    """
    positive = True
    while isinstance(p, dict) and len(p) == 1 and "not" in p:
        positive = not positive
        p = p["not"]
    if not isinstance(p, dict) or len(p) != 1:
        return None
    for key, kind in _SIMPLE_KINDS:
        if key in p:
            spec = p[key]
            if key == "not_in" or key == "not_exists":
                positive = not positive
            return kind, spec, positive
    return None


def _flattenable(kind, spec):
    sub = spec["sub"]
    if kind == "in":
        # Correlated IN subqueries cannot be hashed once; leave them to the
        # row-by-row path.
        return not sub.get("corr") and not _refs_outer(sub)
    # EXISTS: the correlation lives in "corr"; the WHERE itself must not
    # reference the outer row or it cannot be hashed.
    return not _refs_outer(sub.get("where"))


class _InIndex:
    """Hash index for a set of compared subquery rows.

    ``rows`` holds every value tuple (including NULLs) so unknown results can
    be detected exactly; ``nonnull`` answers the common true/false case in
    O(1).
    """

    def __init__(self, rows):
        self.rows = [tuple(v) for v in rows]
        self.nonnull = {t for t in self.rows if all(x is not None for x in t)}
        self.has_null = any(x is None for t in self.rows for x in t)
        self.empty = not self.rows

    def tri(self, probe):
        probe = tuple(probe)
        if all(x is not None for x in probe) and not self.has_null:
            return probe in self.nonnull
        if self.empty:
            return False
        if all(x is not None for x in probe) and probe in self.nonnull:
            return True
        unknown = False
        for h in self.rows:
            value = row_compare(list(probe), "=", list(h))
            if value is True:
                return True
            if value is None:
                unknown = True
        return None if unknown else False


class _ExistsIndex:
    """Set of non-null correlation keys for an EXISTS subquery."""

    def __init__(self, keys):
        # NULL correlation values can never satisfy ``inner = outer``.
        self.keys = {tuple(k) for k in keys if all(x is not None for x in k)}

    def tri(self, probe):
        probe = tuple(probe)
        if any(x is None for x in probe):
            return False
        return probe in self.keys


def _hash_side(kind, spec, db):
    sub = spec["sub"]
    if kind == "in":
        return _InIndex(subquery_rows(db, sub, {}))
    corr = sub.get("corr") or []
    keys = []
    for r in db[sub["from"]]:
        if sub.get("where") is None or evaluate(db, sub["where"], r, {}) is True:
            keys.append([r.get(inner) for _, inner in corr])
    return _ExistsIndex(keys)


def plan(query):
    """Split conjuncts into flattened joins and row-by-row leftovers.

    Each join is ``(kind, spec, positive, index)``.
    """
    joins, rest = [], []
    for c in _conjuncts(query.get("where")):
        simple = _predicate(c)
        if simple is not None:
            kind, spec, positive = simple
            if _flattenable(kind, spec):
                joins.append((kind, spec, positive))
                continue
        rest.append(c)
    return joins, rest


def _probe(kind, spec, index, row):
    if kind == "in":
        return index.tri(terms(spec["left"], row, {}))
    corr = spec["sub"].get("corr") or []
    return index.tri([row.get(outer) for outer, _ in corr])


def run(db, query):
    joins, rest = plan(query)
    tables = []
    for kind, spec, positive in joins:
        tables.append((kind, spec, positive, _hash_side(kind, spec, db)))
    out = []
    for row in db[query["from"]]:
        keep = True
        for kind, spec, positive, index in tables:
            result = _probe(kind, spec, index, row)
            # A row survives a semi-join iff the predicate is TRUE; an
            # anti-join iff it is FALSE (UNKNOWN never survives).
            if positive:
                if result is not True:
                    keep = False
                    break
            elif result is not False:
                keep = False
                break
        if not keep:
            continue
        if all(evaluate(db, c, row) is True for c in rest):
            out.append(row)
    return out, {"semi_anti_joins": len(joins), "row_by_row": len(rest)}

