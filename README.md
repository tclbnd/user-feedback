# arr

Array functions of the query engine (tags, product attributes, role
lists). Queries run unchanged against PostgreSQL and against this engine,
so every function and operator must return what PostgreSQL returns: NULL
arrays and NULL elements, empty arrays, out-of-range subscripts and slices,
text conversion in both directions and the text form of arrays.

## Layout

- `arr/core.py` – length/bounds, subscripts and slices, append/cat/remove/replace, position, @> and &&, trim_array
- `arr/text.py` – array_to_string, string_to_array, array text output
- `arrcall.py` – evaluates a list of calls

## Running

    python3 arrcall.py examples/tags.json out.json
    python3 -m unittest discover -s tests
