# orderidx

ORDER BY ... LIMIT for the order history pages. The planner decides
whether a B-tree index, read forwards or backwards, already returns the
rows in the requested order (so the sort can be skipped), with equality
conditions on leading index columns allowed. Pages use `LIMIT` / `OFFSET`
and, for leaderboards, `FETCH FIRST n ROWS WITH TIES`. Sorting and NULL placement
follow PostgreSQL: the defaults are ASC NULLS LAST and DESC NULLS FIRST,
for ORDER BY as well as for index columns.

## Layout

- `orderidx/sortkeys.py` – ORDER BY items, comparison with NULL placement, sorting
- `orderidx/btree.py` – B-tree indexes with per-column ASC/DESC and NULLS FIRST/LAST
- `orderidx/planner.py` – ordered index paths (forward/backward), execution with LIMIT
- `runorder.py` – runs queries against a table and its indexes

## Running

    python3 runorder.py examples/orders.json examples/queries.json out.json
    python3 -m unittest discover -s tests
