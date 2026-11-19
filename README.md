# strfn

String functions of the query engine. Reports and exports are written as
SQL against PostgreSQL in some deployments and against this engine in
others, so every function here must behave exactly like the PostgreSQL
function of the same name — NULL handling, edge values (zero, negative,
out of range, empty strings), multi-character arguments and error cases
included.

## Layout

- `strfn/basic.py` – concat, ||, length, padding, trimming, substr/left/right, split_part, replace, translate, overlay, chr, ascii, to_hex, …
- `strfn/quoting.py` – quote_ident, quote_literal, quote_nullable, format()
- `strfn/regex.py` – regexp_replace
- `strfn/registry.py` – function names used by the SQL layer
- `sqlcall.py` – evaluates a list of calls

## Running

    python3 sqlcall.py examples/report.json out.json
    python3 -m unittest discover -s tests
