# binfn

bytea and bit string support for the attachment service's blob engine.
Attachments live in PostgreSQL bytea columns; some export and repair jobs
run the same expressions in this engine instead, and the outputs are
compared, so every function and operator here must behave exactly like
PostgreSQL's: input and output formats, encode/decode, byte and bit
addressing, bit string lengths, and the cases PostgreSQL rejects.

## Layout

- `binfn/values.py` – literals and output
- `binfn/functions.py` – functions and operators
- `bincall.py` – evaluates a list of calls

## Running

    python3 bincall.py examples/export.json out.json
    python3 -m unittest discover -s tests
