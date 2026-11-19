# csvcopy

The nightly partner import loads CSV files with the server-side equivalent
of `COPY table FROM file WITH (FORMAT csv, ...)`. The parsing rules, the
options (`DELIMITER`, `QUOTE`, `ESCAPE`, `NULL`, `HEADER`, `FORCE_NULL`,
`FORCE_NOT_NULL`) and the NULL / empty-string handling must be exactly
PostgreSQL's, so that a file gives the same table here and in PostgreSQL.

## Layout

- `csvcopy/options.py` – COPY options and defaults
- `csvcopy/parser.py` – records and fields of the CSV input
- `csvcopy/loader.py` – NULL handling, type input, rows
- `copyin.py` – loads one file

## Running

    python3 copyin.py examples/partners.json examples/partners.csv out.json
    python3 -m unittest discover -s tests
