# gsets

GROUP BY with GROUPING SETS, ROLLUP and CUBE for the finance cube reports,
including the GROUPING() function and GROUP BY DISTINCT. The expansion of
grouping clauses, the rows produced and the GROUPING() values must be
exactly what PostgreSQL returns for the same query.

## Layout

- `gsets/clauses.py` – GROUP BY elements and their expansion into grouping sets
- `gsets/aggregate.py` – aggregation per grouping set, GROUPING()
- `rungroup.py` – runs one query over a table

## Running

    python3 rungroup.py examples/sales.json examples/rollup.json out.json
    python3 -m unittest discover -s tests
