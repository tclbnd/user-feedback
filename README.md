# patx

Pattern matching of the search and reporting engine: `LIKE` / `ILIKE`
(with `ESCAPE`), `SIMILAR TO` and `substring(... SIMILAR ... ESCAPE ...)`,
and the POSIX regular expression operators and functions `~`, `~*`, `!~`,
`regexp_match`, `regexp_split_to_array`. The same expressions also run on
PostgreSQL, so every match result, extracted value and error must be the
one PostgreSQL gives (PostgreSQL's regular expressions are its own ARE
flavour, not Python's).

## Layout

- `patx/like.py` – LIKE / ILIKE / SIMILAR TO, translated to Python regexes
- `patx/posix.py` – POSIX regex operators and functions
- `patcall.py` – evaluates a list of expressions

## Running

    python3 patcall.py examples/search.json out.json
    python3 -m unittest discover -s tests
