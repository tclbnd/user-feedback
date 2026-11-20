# aggfn

Aggregate functions of the dashboard engine. Some dashboards query
PostgreSQL directly and others go through this engine; the tiles are
compared value by value, so every aggregate must return what PostgreSQL
returns for the same column type and rows: NULL inputs, empty groups,
result types and scale, array and JSON output text, ordered-set
aggregates and error cases included.

## Layout

- `aggfn/aggregates.py` – the aggregates
- `aggcall.py` – evaluates a list of calls

## Running

    python3 aggcall.py examples/dashboard.json out.json
    python3 -m unittest discover -s tests
