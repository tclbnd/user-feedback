# subq

WHERE-clause evaluation with subqueries for the reporting query engine,
and the planner step that flattens IN / NOT IN / EXISTS / NOT EXISTS
subqueries into hash semi-joins and anti-joins. Results follow SQL
semantics as implemented by PostgreSQL (three-valued logic, NULLs in
subqueries and arrays, row-wise comparisons); flattening must never change
a query's result.

## Layout

- `subq/logic.py` – three-valued logic, scalar and row comparisons, IS DISTINCT FROM
- `subq/expr.py` – row-by-row evaluation of predicates and subqueries
- `subq/rewrite.py` – flattening into semi/anti hash joins and execution
- `runq.py` – runs queries both ways

## NULL semantics

Both paths implement SQL three-valued logic, so they agree with PostgreSQL
under any NULL distribution:

- `IN` is `= ANY(subquery)`: an outer NULL, or a NULL in a compared
  subquery column that no row matches, makes the predicate *unknown*
  (rejected by `WHERE`). An empty subquery is false.
- `NOT IN` is the negation of that, so if the subquery contains a NULL that
  the outer value does not equal, the result is unknown and the row is
  dropped — hence "`NOT IN` with a NULL returns no rows".
- `EXISTS` / `NOT EXISTS` are never unknown; correlation uses three-valued
  `inner = outer`, so a NULL on either side cannot match.
- `ANY` / `ALL` propagate unknown: `x = ALL(...)` is unknown (not false)
  when an element comparison is unknown, and unknown when the left value is
  NULL.
- Row comparisons `(a, b) op (c, d)` are element-wise; `<`, `<=`, `>`, `>=`
  compare lexicographically, so a NULL in a later column is irrelevant once
  an earlier column decides the order.

Flattening folds top-level `NOT` into the join polarity and keeps the hash
side NULL-aware (`IN` tracks NULL keys for unknown detection, `EXISTS` drops
them), so the planned result always equals the row-by-row result.

## Testing

`tests/test_differential.py` checks both paths against SQLite (same SQL
three-valued semantics) over fixed cases, randomized NULL-heavy tables, and
fuzzed `AND` / `OR` / `NOT` combinations.

    python3 runq.py examples/db.json examples/queries.json out.json
    python3 -m unittest discover -s tests
