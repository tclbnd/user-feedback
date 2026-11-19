# plancache

Prepared statements and the plan cache of the query layer, with the
catalog and the cost-based planner they depend on.

## Layout

- `plancache/catalog.py` – tables, indexes and planner statistics (`ANALYZE` refreshes them)
- `plancache/planner.py` – picks nested loop / hash join and index use from the statistics
- `plancache/executor.py` – runs a plan against the current data and reports its cost in row visits
- `plancache/prepared.py` – `Server`, `PreparedStatement` and the cached plan
- `plancache/slowlog.py` – slow query log for the on-call dashboard
- `plancache/stats.py` – per-statement execution and planning counters for the dashboard
- `plancache/reload.py` – nightly swap reload of the dimension tables
- `workload.py` – replays DDL, data growth and executions

## Running

    python3 workload.py examples/growth.json out.json
    python3 -m unittest discover -s tests

Workload steps: `execute` (name, optional `repeat`), `insert` (table, rows),
`analyze` (table), `create_index` (index, table, column), `drop_index`
(index), `reload` (table, rows), `drop_table` (table), `create_table`
(table, rows). The output lists every execution with the plan used and its cost,
the number of times the planner ran (`plannings`), the slowest entries and
the per-statement counters (`statements`).
Planning costs roughly as much as a few hundred cheap executions, which is
why plans are cached.
