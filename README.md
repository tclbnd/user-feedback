# pidx

Partial index support in the planner. A partial index only contains the
rows matching its WHERE clause, which keeps it small; the planner may scan
it instead of the table when the query is guaranteed to need only rows the
index contains.

## Layout

- `pidx/predicates.py` – predicate trees, three-valued evaluation, canonical form
- `pidx/implication.py` – decides whether a query's WHERE implies an index predicate
- `pidx/planner.py` – partial index objects, plan choice and execution
- `pidx/prepared.py` – prepared statements (parameters, plan reuse)
- `pidx/sizing.py` – index size estimates for the capacity report
- `plan.py` – plans and runs a batch of queries

## Running

    python3 plan.py examples/orders.json out.json
    python3 -m unittest discover -s tests

Predicates: `{"cmp": [col, op, const]}`, `{"in": [col, [values]]}`,
`{"is_null": col}`, `{"not_null": col}`, `{"and": [...]}`, `{"or": [...]}`.
A query returns the rows for which its WHERE is TRUE.

The order API runs its queries as prepared statements: a constant can be
`{"param": name}`, and the job file's `prepared` list gives a statement and
the parameter values of each execution. A statement is planned once and the
plan is reused (see `pidx/prepared.py`); every execution must still return
exactly the rows a full scan returns.
