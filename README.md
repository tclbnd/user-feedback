# numfn

Numeric functions and operators of the query engine. Settlement jobs run
against PostgreSQL for some tenants and against this engine for others,
so every function and operator here must give exactly what PostgreSQL
gives for the same literals: integer versus numeric arithmetic, result
scale, negative and zero arguments, negative rounding positions, overflow
and division by zero included.

## Layout

- `numfn/core.py` – operators, rounding, div/mod, roots and logarithms, gcd/lcm, factorial, width_bucket, scale functions
- `numfn/registry.py` – names used by the SQL layer
- `sqlcall.py` – evaluates a list of calls

## Running

    python3 sqlcall.py examples/settlement.json out.json
    python3 -m unittest discover -s tests
