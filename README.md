# numfn

Numeric functions of the finance query engine: rounding and truncation,
integer division and modulo, logarithms and powers, `width_bucket`,
`gcd`/`lcm`. Arguments carry their SQL type (`numeric`, `float8`, `int`)
because PostgreSQL's results depend on it. Statements also run on
PostgreSQL, so values, the text psql shows and error cases must match.

## Layout

- `numfn/funcs.py` – the functions
- `numcall.py` – evaluates a list of calls and renders the results

## Running

    python3 numcall.py examples/finance.json out.json
    python3 -m unittest discover -s tests
