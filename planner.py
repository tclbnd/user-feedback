"""Rewrite subquery predicates into joins.

SQL three-valued logic drives every choice here:

* ``IN`` / ``EXISTS`` always become semi joins. NULLs cannot make an ``IN``
  predicate wrongly TRUE (a NULL comparison is UNKNOWN, never TRUE), so the
  join only has to evaluate the match under 3VL.
* ``NOT EXISTS`` counts rows only; NULL values never matter, so it is always
  a plain anti join. It must NOT be turned into ``NOT IN``: the two differ
  whenever the compared columns can be NULL.
* ``NOT IN`` becomes a plain anti join only when *both* sides are provably
  non-null -- every outer argument and every subquery select column. A single
  NULL on either side makes the predicate UNKNOWN instead of TRUE/FALSE, so
  otherwise a null-aware anti join that reproduces the 3VL result is needed.
"""
from expr import SUBQUERY_OPS


def _known_non_null_columns(table, alias, where):
    """Columns provably non-null at this query level.

    A column is provably non-null when its table declares it NOT NULL or when
    this level's WHERE has a top-level ``is_not_null`` conjunct on it.
    """
    cols = {f"{alias}.{c}" for c in table.get("not_null", [])}
    for pred in where:
        if pred.get("op") == "is_not_null" and "col" in pred["arg"]:
            cols.add(pred["arg"]["col"])
    return cols


def _provably_non_null(node, known_cols):
    """Whether an outer argument (column ref or literal) cannot be NULL."""
    if "value" in node:
        return node["value"] is not None
    if "col" in node:
        return node["col"] in known_cols
    return False


def plan(query, db, rewrite=True):
    nodes = []
    outer_known = _known_non_null_columns(
        db[query["from"]], query["as"], query.get("where", []))
    for pred in query.get("where", []):
        op = pred["op"]
        if op not in SUBQUERY_OPS:
            nodes.append(("filter", pred))
        elif not rewrite:
            nodes.append(("subplan", pred))
        elif op in ("in", "exists"):
            nodes.append(("semi_join", pred))
        elif op == "not_exists":
            # EXISTS / NOT EXISTS only test whether rows exist, so inner NULLs
            # are irrelevant: a plain anti join is exact for every NOT EXISTS.
            nodes.append(("anti_join", pred))
        else:  # not_in -- needs the nullability proof on BOTH sides
            sub = pred["query"]
            inner_known = _known_non_null_columns(
                db[sub["from"]], sub["as"], sub.get("where", []))
            outer_safe = all(_provably_non_null(a, outer_known) for a in pred["args"])
            inner_safe = all(c in inner_known for c in sub["select"])
            nodes.append(("anti_join" if outer_safe and inner_safe
                          else "null_aware_anti_join", pred))
    return nodes
