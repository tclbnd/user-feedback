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

## Running

    python3 runq.py examples/db.json examples/queries.json out.json
    python3 -m unittest discover -s tests
