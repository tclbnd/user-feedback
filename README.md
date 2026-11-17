# dialectshift

Tooling for moving reporting queries from the legacy database to the target
database: a rewriter from the legacy SQL dialect to the target dialect, mock
executors for both dialects, and a validator that runs the original and the
rewritten query side by side and classifies the differences.

    python3 migrate.py rewrite <dataset.json> "<legacy SQL>"     # prints target SQL
    python3 migrate.py run <dataset.json> "<SQL>" --dialect legacy|target   # one JSON row per line
    python3 migrate.py check <dataset.json> <queries.json>       # one JSON report line per query
    python3 -m unittest discover -s tests

## SQL subset (both dialects)

    SELECT expr [AS alias], ... FROM table
      [WHERE expr = expr [AND ...]]
      [ORDER BY expr [ASC|DESC] [NULLS FIRST|LAST], ...]
      [LIMIT ...]

An expression is a column, a string `'...'` or integer literal, or a function
call. A selected column keeps its name; give every function an alias.

| | legacy | target |
|---|---|---|
| paging | `LIMIT offset, count`, `LIMIT count`, `LIMIT count OFFSET offset` | `LIMIT count OFFSET offset` only |
| NULL order | NULL is the lowest value: first in ASC, last in DESC; no `NULLS` clause | NULL is the highest value: last in ASC, first in DESC; `NULLS FIRST/LAST` supported |
| text compare | case-insensitive collation, in `=` and in `ORDER BY` | binary, case-sensitive; `LOWER(x)` available |
| timestamps | naive `YYYY-MM-DD HH:MM:SS` in the session zone (`legacy_zone`) | absolute, stored as UTC `YYYY-MM-DDTHH:MM:SSZ` |
| date functions | `DATE(ts)` → `YYYY-MM-DD`; `CONVERT_TZ(ts, from, to)` → shifted naive timestamp | `DATE_TRUNC('day', ts[, zone])` → `YYYY-MM-DD` of the local day in `zone` (default `+00:00`); `TIMEZONE(zone, ts)` → naive local `YYYY-MM-DD HH:MM:SS` |

Rows with equal sort keys come back in no particular order. The legacy
executor happens to keep storage order; the target executor, like its top-N
sort, returns ties in an order that changes with `LIMIT`/`OFFSET`, so paging
over a non-unique sort key repeats and skips rows.

## Rewrite rules

* `LIMIT o, n` becomes `LIMIT n OFFSET o`; `LIMIT n` becomes `LIMIT n OFFSET 0`.
* Every `ORDER BY` key gets an explicit `NULLS FIRST` (ASC) or `NULLS LAST`
  (DESC) so NULLs land where the legacy database put them.
* Text columns (`text_columns`) used in `WHERE` equality or as sort keys are
  wrapped in `LOWER()`, and string literals compared with them are lowercased.
* `DATE(ts)` becomes `DATE_TRUNC('day', ts, '<legacy_zone>')`;
  `CONVERT_TZ(ts, '<legacy_zone>', z)` becomes `TIMEZONE(z, ts)` (another
  source zone is rejected). A timestamp column selected as is becomes
  `TIMEZONE('<legacy_zone>', ts)`, so both sides print the same text.
* If the primary key is not already a sort key it is appended as the last
  key, ascending, which makes the order total and paging stable.

## Dataset

```json
{"legacy_zone": "+08:00",
 "primary_key": {"orders": "id"},
 "text_columns": {"orders": ["customer", "city"]},
 "timestamp_columns": {"orders": ["created_at"]},
 "legacy": {"orders": [{"id": 1, "customer": "Ann", "city": null, "created_at": "2026-03-01 23:30:00"}]},
 "target": {"orders": [{"id": 1, "customer": "Ann", "city": null, "created_at": "2026-03-01T15:30:00Z"}]}}
```

`legacy` and `target` hold the same table as stored in each database (the
target copy was migrated separately and may differ).

## Validation (`check`)

`queries.json` is a list of `{"name", "source"}` with an optional hand-written
`"target"` SQL that is used instead of the rewriter's output. For each query,
the source runs on the legacy executor over `legacy` data (with the primary
key appended as a final sort key, so both sides are totally ordered) and the
target SQL runs on the target executor over `target` data. Rows are compared
in order. On a mismatch the target SQL is run again over a mirror of the
legacy data (legacy timestamps converted to UTC): if that reproduces the
legacy result, the rewrite is right and the difference is in the data
(`"cause": "data"`), otherwise `"cause": "rewrite"`.

Report line: `{"name", "target_sql", "status": "match"|"mismatch", "cause":
null|"data"|"rewrite", "legacy_rows", "target_rows"}` plus `first_diff`, the
first differing row index, on a mismatch.
