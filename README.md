# casts

Type input and casts of the import/ETL layer. Partner files are loaded
into typed staging columns, and the same conversions also happen in
PostgreSQL in other pipelines; a value must convert to the same result —
or fail with an error — in both, and print the same way psql prints it.

## Layout

- `casts/convert.py` – input from text and casts between types, psql text output
- `castcall.py` – evaluates a list of casts

## Running

    python3 castcall.py examples/import.json out.json
    python3 -m unittest discover -s tests
