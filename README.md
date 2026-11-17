# subqplan

A tiny query engine for single-table queries whose WHERE clause may hold
subquery predicates (`IN`, `NOT IN`, `EXISTS`, `NOT EXISTS`). The planner
rewrites each subquery predicate into a join; `--no-rewrite` evaluates them
row by row instead, straight from the SQL definition.

    python3 query.py <db.json> <query.json> [--no-rewrite]
    python3 -m unittest discover -s tests

Output is one JSON object: `{"plan": [...], "rows": [[...], ...]}`. `rows`
holds the selected values of every qualifying outer row, in table order.
`plan` has one node per WHERE conjunct, in order: `filter` for a plain
predicate, and for a subquery predicate `semi_join`, `anti_join`,
`null_aware_anti_join`, or `subplan` (row-by-row, only with `--no-rewrite`).

## Database file

    {"tables": {"orders": {"not_null": ["id"], "rows": [{"id": 1, "cust": 7}, ...]}}}

A missing column or JSON `null` is NULL. Columns in `not_null` never hold NULL.

## Query file

    {"select": ["o.id"], "from": "orders", "as": "o", "where": [<predicate>, ...]}

`where` is a list of conjuncts (AND). Column references are always qualified
with the alias: `{"col": "o.cust"}`; literals are `{"value": 3}`. Predicates:

| predicate | meaning |
|-----------|---------|
| `{"op": "=", "left": X, "right": Y}` | also `<>`, `<`, `<=`, `>`, `>=` |
| `{"op": "is_null", "arg": X}` / `{"op": "is_not_null", "arg": X}` | |
| `{"op": "in", "args": [X, ...], "query": S}` / `"not_in"` | `(X, ...) [NOT] IN (S)` |
| `{"op": "exists", "query": S}` / `"not_exists"` | |

A subquery `S` has the same shape as a query (`select` is only used by `in`
and `not_in` and has as many columns as `args`); its `where` holds plain
predicates only and may reference the outer alias (a correlated subquery).

## Semantics

SQL three-valued logic. A comparison with a NULL operand is UNKNOWN; a row
qualifies only when every conjunct is TRUE. For a row-value `(x1..xn)` and a
subquery row `(y1..yn)` the comparison is the AND of `xi = yi`. `IN` is TRUE
if some subquery row compares TRUE, otherwise UNKNOWN if some compares
UNKNOWN, otherwise FALSE (so FALSE when the subquery is empty). `NOT IN` is
the negation of `IN`. `EXISTS` is TRUE when the subquery returns a row; NULLs
never matter to `EXISTS` / `NOT EXISTS`.

## Rewrite rules

- `IN` and `EXISTS` become `semi_join`; `NOT EXISTS` becomes `anti_join`.
- `NOT IN` becomes `anti_join` only when every outer argument and every
  subquery column is provably non-null; otherwise it becomes
  `null_aware_anti_join`, which must keep the `NOT IN` result exact.
- A column is provably non-null when it is in its table's `not_null` list or
  its own query level has a top-level `is_not_null` conjunct on it (the outer
  WHERE for outer columns, the subquery WHERE for subquery columns). A
  non-NULL literal is non-null.

Rewritten plans must return exactly the rows of the row-by-row definition.
