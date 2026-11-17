# idxq

Access-path selection over composite indexes for a small in-memory table.

    python3 idxq.py <db.json> <query.json>      # prints {"plan": {...}, "rows": [...], "scanned": N, "fetches": N}
    python3 -m unittest discover -s tests

`db.json`: `{"rows": [{"id": 1, "a": ..., ...}], "indexes": [{"name": "ix", "columns": ["a", "b"]}]}`.
Every row has a unique integer `id`; values are integers or strings, never null.
An index holds one entry per row, sorted ascending by its columns and then
`id`; each entry carries the index columns and `id`.

`query.json`: `{"where": [[col, op, value], ...], "select": [cols], "order_by": [[col, "asc"|"desc"], ...]}`.
`where` is a conjunction; `op` is one of `=`, `<`, `<=`, `>`, `>=`.

## Plan

For an index, the usable prefix is built column by column: a column with an
`=` condition extends the prefix; the first column without one ends it, and if
that column has range conditions it becomes the `range` column (its `lo`/`hi`
bounds). Remaining conditions on index columns are `index_filter` (checked on
the entry, before fetching the row); conditions on other columns are
`table_filter`. The index with the most `=` columns wins, then one with a
range column, then a covering one, then the first in `indexes`. An index with
an empty prefix and no range is only used when it delivers the requested
order; otherwise the plan is a full table scan (`index: null`).

`order_from_index` is true when the `order_by` columns, ignoring those bound
by `=`, follow the index columns that come after the `=` prefix in order and
all have the same direction; `direction` is then `desc` if they are all `desc`
(the index is scanned backwards). Otherwise `sort` is true and rows are sorted
after the scan. `covering` is true when every selected, filtered and ordered
column is an index column or `id`; a covering plan never fetches a row.

Plan fields: `index`, `eq` (columns), `range` (column or null), `lo`, `hi`
(`[value, inclusive]` or null), `index_filter`, `table_filter`, `covering`,
`order_from_index`, `direction`, `sort`.

## Counters and row order

`scanned` counts index entries read between the start and end keys of the
scan (every row for a full scan). `fetches` counts table rows fetched: entries
that passed `index_filter`, or 0 for a covering plan. Rows come out in
`order_by` order; rows with equal `order_by` values may come in any order.
