# jmod

jsonb modification for the customer-profile service: `||`, `-`, `#-`,
`jsonb_set`, `jsonb_insert`, `jsonb_strip_nulls`, `jsonb_array_length`,
`jsonb_typeof`, and jsonb's text output. The service runs the same
statements against PostgreSQL in the main region and against this module
at the edge; documents written by either side are compared byte for byte,
so results — values, error cases and the printed text — must be
PostgreSQL's.

## Layout

- `jmod/value.py` – jsonb input, Decimal numbers, jsonb text output
- `jmod/ops.py` – the operators and functions
- `jcall.py` – evaluates a list of calls

## Running

    python3 jcall.py examples/profile.json out.json
    python3 -m unittest discover -s tests
