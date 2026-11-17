# colstats

Column statistics and selectivity estimates for the query planner of a small
row store. `ANALYZE` builds a most-common-values list and an equal-height
histogram per column; the planner turns predicates into selectivities and
picks a sequential scan or an index scan.

    python3 statsdb.py run <workload.json>      # print {"results": [...]}, one per event
    python3 statsdb.py report <workload.json>   # readable lines, with the rows that really match
    python3 -m unittest discover -s tests

## Workload

    {"table": {"columns": {"k": [7, 7, null, 120], "u": [1, 2, 3, 4]}, "indexes": ["k"]},
     "events": [{"op": "analyze"},
                {"op": "estimate", "where": {"col": "k", "op": "=", "value": 7}},
                {"op": "insert", "columns": {"k": [9, 9], "u": [5, 6]}},
                {"op": "delete", "where": {"col": "u", "op": "<", "value": 3}}]}

Columns are equal-length lists; `null` is SQL NULL. Predicates:
`{"col", "op", "value"}` with `=`, `!=`, `<`, `<=`, `>`, `>=`;
`{"col", "op": "in", "values": [...]}`; `{"col", "op": "is_null"}`,
`{"col", "op": "not_null"}`; `{"and": [...]}`, `{"or": [...]}`. A comparison
with NULL is false. Results: `analyze` -> `{"stats": {"rows": n, "columns":
{col: {"null_frac", "ndv", "mcv": [[value, freq], ...], "histogram": [...]}}}}`;
`estimate` -> `{"selectivity", "rows", "path", "analyzed"}`; `insert` ->
`{"inserted": n}`; `delete` -> `{"deleted": n}`.

## ANALYZE

- Sample: every row when the table has at most 3000 rows, otherwise every
  `ceil(rows / 3000)`-th row starting with the first. `n` is the sample size.
- `null_frac` = NULLs / n; `ndv` = distinct non-NULL values in the sample.
- MCV list: values ranked by count (ties by value), at most 10, each seen at
  least twice and, unless the column has at most 10 distinct values, with a
  count above 1.25 times the average count of a non-NULL value. Frequencies
  are count / n.
- Histogram: the non-NULL values not in the MCV list, sorted; with m >= 2 such
  values the 11 bounds are the values at positions `round(i * (m - 1) / 10)`,
  i = 0..10 (10 equal-height buckets). With one value it is that value; with
  none it is empty.

## Selectivity

`rest = 1 - null_frac - sum(MCV freqs)` is the share of non-NULL rows outside
the MCV list, spread over `ndv - len(mcv)` values.

- `= v`: the MCV frequency of v, otherwise `rest / (ndv - len(mcv))` (also for
  values outside the histogram range); 0 when no values are left.
- `!= v`: `1 - null_frac - sel(= v)`.
- Ranges: MCVs that satisfy the condition plus `rest` times the histogram
  fraction, found by linear interpolation inside the bucket that holds the
  constant (0 below the first bound, 1 above the last).
- `in`: the sum of `=` over the distinct values, at most `1 - null_frac`.
- `is_null`: `null_frac`; `not_null`: `1 - null_frac`.
- `and`: product; `or`: `s1 + s2 - s1 * s2`, folded left to right.

Estimates are expected to stay within `0.02 + 20%` of the true fraction of
matching rows on the analyzed data.

## Access path and staleness

`rows = selectivity * analyzed row count`. When every column in the predicate
has an index, an index scan costs `2 + 4 * rows` and a sequential scan costs
`1 * analyzed row count`; the cheaper one is chosen (`"index"` or `"seq"`).

Inserted and deleted rows both count as modifications. Before an estimate the
table is re-analyzed when it has never been analyzed or when the
modifications since the last analyze exceed `50 + 0.1 * analyzed row count`;
`analyzed` reports whether that happened.
