# decorr

A small report engine for single-table queries with correlated scalar and
`EXISTS` subqueries. The planner decorrelates subqueries into a join plus a
grouped aggregate so that they are not re-executed for every outer row.

    python3 query.py <db.json> <query.json> [--no-rewrite]
    python3 -m unittest discover -s tests

Output: `{"plan": [...], "rows": [[...], ...], "subplan_runs": N}`. `rows` are
the select-list values of the qualifying outer rows, in table order. `plan`
lists every subquery once, in pre-order (select list first, then WHERE; a
nested subquery right after its parent): `"SubPlan <alias>"` when it is
executed again for each row of its enclosing query, `"Decorrelated <alias>"`
when it was rewritten. `subplan_runs` counts SubPlan executions. With
`--no-rewrite` every subquery stays a SubPlan.

Exit status 4 with the message `more than one row returned by a subquery used
as an expression` when a scalar subquery without an aggregate yields more
than one row for some outer row; this error must be kept after rewriting.

## Files

`db.json`: `{"tables": {"orders": {"rows": [{"id": 1, "cust": 7, ...}, ...]}}}`;
a missing column or JSON `null` is NULL.

`query.json`: `{"from": "customers", "as": "c", "select": [<operand>, ...], "where": [<predicate>, ...]}`

Operands: `{"col": "c.id"}` (always alias-qualified), `{"value": 3}`, or a
scalar subquery `{"scalar": S}`. Predicates: `{"op": "=", "left": A, "right": B}`
(also `<>`, `<`, `<=`, `>`, `>=`), `{"op": "is_null", "arg": A}`,
`{"op": "or", "args": [<predicate>, ...]}`, `{"op": "exists", "query": S}` and
`{"op": "not_exists", "query": S}`. `where` lists are ANDed.

A subquery `S` is `{"from": T, "as": a, "agg": F, "col": "a.x", "where": [...]}`
with `F` one of `count`, `sum`, `min`, `max`, or `null` for a plain column
(`"col": null` with `count` is `COUNT(*)`). Its predicates may reference any
enclosing alias and may themselves contain scalar subqueries.

## Semantics

SQL three-valued logic; a row qualifies when every conjunct is TRUE.
`COUNT` counts non-NULL values (all rows for `COUNT(*)`) and is 0 when no row
matches. `SUM`, `MIN`, `MAX` ignore NULLs and are NULL when nothing is left.
A plain-column scalar subquery is NULL with no row, its value with one row,
and an error with more.

## Rewrite rules

A subquery is decorrelated when every conjunct of its WHERE either references
no enclosing alias, or is a comparison whose one side references only
enclosing aliases and whose other side references none of them. Comparisons
may be equalities or inequalities. Anything else (an enclosing alias inside
`or`, `is_null`, or mixed into the inner side) keeps the subquery as a
SubPlan. Nested subqueries are planned the same way, each against all of its
enclosing aliases. A rewritten plan must return exactly what the row-by-row
plan returns.
