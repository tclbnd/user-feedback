# idxadvisor

A workload-driven index advisor. It reads a slow query log and table
statistics, proposes candidate indexes, estimates every query's cost with
hypothetical indexes, charges index maintenance for writes, and picks indexes
greedily within a count and space budget. It also lists redundant and unused
existing indexes, and can explain a query against a small real data set.

    python3 advisor.py advise <stats.json> <slow.log> [--out report.json]   # JSON report
    python3 advisor.py explain <data.json> "<SQL>" [--indexes <report.json | list.json>]
    python3 -m unittest discover -s tests

## Inputs

`slow.log`: lines starting with `#` (`# Time:`, `# Query_time:` …) are headers;
every other line is SQL, a statement ends with `;`. Statements are grouped by
fingerprint: string and number literals become `?`, whitespace collapses.

SQL subset: `SELECT ... FROM t [JOIN u ON t.a = u.b] [WHERE p AND p ...]
[ORDER BY c [ASC|DESC], ...]`, `INSERT INTO t (...) VALUES (...)`,
`UPDATE t SET c = v[, ...] WHERE ...`, `DELETE FROM t WHERE ...`. A predicate
is `col = lit`, `col <|<=|>|>= lit` or `col BETWEEN lit AND lit`; columns of a
join are qualified with full table names (`orders.status`), unqualified
columns belong to the first table. Join queries have no ORDER BY.

`stats.json`:

```json
{"tables": {"orders": {"rows": 200000,
                       "columns": {"status": {"ndv": 5, "width": 10}, "...": {}},
                       "indexes": [{"name": "pk_orders", "columns": ["id"], "unique": true}]}},
 "budget": {"max_indexes": 3, "space_bytes": 20000000}}
```

## Cost model

Per execution, with `N` the table's rows:

* selectivity: `col = lit` is `1/ndv`; a range or BETWEEN predicate is `0.1`.
* sequential scan: `N`.
* index access: the index matches its leading columns that have an equality
  predicate, then at most one more column with a range predicate. With `M` =
  `N` × the selectivities of the matched predicates, cost `3 + 1.5 × M`. An
  index with no matched column is only usable to provide the order (cost
  `3 + 1.5 × N`).
* sort: a SELECT with ORDER BY adds `R` (rows left after all predicates on the
  table) unless the access path provides the order: the index columns right
  after the matched equality prefix start with the ORDER BY columns (either
  direction; a matched range column must itself be the first ORDER BY column).
* join `FROM a JOIN b ON a.x = b.y`: cost of reading `a` plus the cheaper of a
  hash join `N_b + R_a` and, for an index on `b` whose first column is `y`,
  `R_a × (3 + 1.5 × N_b / ndv(b.y))`.
* writes: INSERT costs only maintenance; UPDATE and DELETE pay the access cost
  to locate their rows plus maintenance. Maintenance is `2 × rows written` for
  each index on the table that the statement changes: all of them for INSERT
  (1 row) and DELETE (`R` rows), and for UPDATE (`R` rows) those containing a
  SET column.
* index size in bytes: `N × (sum of column widths + 8)`.

## Candidates and selection

For each query and table: equality columns ordered by descending ndv (ties
by name) form `E`; the first range column `r`; the ORDER BY columns `O`.
Candidates are `E + [r]`, `E + O` and `E` (empty ones dropped), plus `[b.y]`
for the inner table of a join. A candidate identical to an existing index is
dropped. Names are `ix_<table>_<col>_<col>...`.

The workload cost is `sum(count × cost)` over all fingerprints. Selection is
greedy: at each step take the candidate with the largest positive reduction
of the workload cost whose size fits in the space still left (ties by name),
until `max_indexes` are chosen or nothing helps.

## Report

```json
{"recommended": [{"name", "table", "columns", "size"}],
 "queries": [{"fingerprint", "count", "cost_before", "cost_after", "benefit"}],
 "redundant": [names], "unused": [names]}
```

`cost_before` uses the existing indexes, `cost_after` the existing plus the
recommended ones (per execution, rounded to 2 decimals); `benefit` is
`(cost_before - cost_after) × count`. An existing non-unique index is
**redundant** when its columns are a leading prefix of another index's (an
existing or recommended one; for identical columns the one with the larger
name is redundant). It is **unused** when it is non-unique, not redundant and
no query's cheapest plan uses it with the recommended indexes in place.

## Explain

`data.json` holds real rows: `{"tables": {"orders": {"columns": {col: width},
"indexes": [...], "rows": [...]}}}`. Statistics are taken from the rows
(`ndv` = distinct values). `explain` runs one single-table SELECT and prints
`{"access": index name | "seq", "cost": ..., "rows": matching row count}`;
`--indexes` adds hypothetical indexes (the `recommended` list of an `advise`
report, or a JSON list of `{"name", "table", "columns"}`).
