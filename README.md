# jsonbx

The product catalogue keeps each product as a jsonb document. The API's
filters become jsonb operators — `@>` (containment), `?`, `?|`, `?&` (key
existence) — and are served by GIN indexes on the column, with either the
`jsonb_ops` or the `jsonb_path_ops` operator class. The operators follow
PostgreSQL's jsonb semantics; the indexes must never change a query's
result, only how fast it is found.

## Layout

- `jsonbx/value.py` – parsing and the stored jsonb form
- `jsonbx/ops.py` – the operators, evaluated row by row
- `jsonbx/gin.py` – GIN index operator classes `jsonb_ops` and `jsonb_path_ops`
- `jsonbx/table.py` – table, index maintenance and plan choice (index + recheck, or scan)
- `jsonbx/stats.py` – index size report
- `query.py` – runs queries through each index and as a full scan

## Running

    python3 query.py examples/products.json examples/queries.json out.json
    python3 -m unittest discover -s tests
