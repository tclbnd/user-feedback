# rcte

The recursive-query part of the reporting engine. Org charts and bills of
materials are stored as edge tables and queried with `WITH RECURSIVE`; this
package evaluates those queries.

## Layout

- `rcte/recursive.py` – evaluation of the recursive CTE (anchor, iteration, UNION / UNION ALL, CYCLE clause)
- `rcte/stream.py` – depth-first streaming evaluation of the same queries, for the big exports
- `rcte/graph.py` – node and edge tables
- `rcte/bom.py` – BOM explosion, MRP gross requirements and level listing
- `rcte/export.py` – reporting-line export and access-review list for the HR jobs
- `rcte/metrics.py` – per-query statistics and the slow-query log
- `rquery.py` – runs one query from JSON files
- `hrjob.py`, `mrp.py` – the nightly HR jobs and the MRP requirement run

## Running

    python3 rquery.py examples/org.json examples/org_query.json out.json
    python3 hrjob.py export examples/org_matrix.json lines.csv
    python3 mrp.py examples/bike_qty.json bike 10 req.json
    python3 -m unittest discover -s tests

A query selects `columns` out of `id`, `depth` (0 for anchor rows, +1 per
recursion step) and `path` (list of ids from the anchor to the row).
`union` is `all` or `distinct`. `cycle: ["id"]` corresponds to
`CYCLE id SET is_cycle USING path` and adds the `is_cycle` and `path`
columns to the output. `edge_filter` restricts the edges followed.
