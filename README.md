# numfmt

`to_char(number, text)` for the report engine. Statements are rendered by
PostgreSQL for some customers and by this engine for others, and the
exported text files are compared byte for byte, so the output must match
PostgreSQL's numeric template patterns exactly: digit positions, the sign
position, fill mode, padding, rounding and overflow included.

## Layout

- `numfmt/to_char.py` – template parsing and formatting
- `fmtcall.py` – evaluates a list of calls

## Running

    python3 fmtcall.py examples/statement.json out.json
    python3 -m unittest discover -s tests
