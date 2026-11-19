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

## Semantics

Inputs follow PostgreSQL: a JSON list is an array, `null` inside it is a
NULL element, and a bare `null` argument is a NULL array.  Errors are
reported per call (`{"call": ..., "error": ...}`), never by aborting the
batch.

| Case | Result |
| --- | --- |
| `a[i]` with `i < 1` or `i > length` | NULL (no error) |
| `a[i]` with `a` or `i` NULL | NULL |
| slice with either bound out of range | clamped to the array |
| slice whose lower bound exceeds its upper bound | empty array |
| slice with `a`, `lo` or `hi` NULL | NULL |
| `array_length`/`array_lower`/`array_upper` on `{}` | NULL (an empty array has no dimensions) |
| `cardinality` on `{}` | 0 |
| `array_length` for dimension other than 1 | NULL |
| `array_append`/`array_prepend` with a NULL array | NULL |
| `array_append(a, NULL)` | array with a NULL element |
| `array_cat` with one NULL side | the other array (only NULL || NULL is NULL) |
| `array_to_string` with NULL elements | elements omitted |
| `array_to_string` with a `null_string` | NULL elements replaced by it |
| `string_to_array('', ...)` | empty array |
| `string_to_array(s, ',', '')` | empty fields become NULL |
| `string_to_array(s, NULL)` | one element per character |
| `@>`/`&&` with a NULL array | NULL |
| `@>`/`&&` element matching | NULL matches NULL, but not a value |
| `array_out` of an element needing quotes | quoted and backslash-escaped |
| `trim_array` trimming more than the length | error |
| `array_position` with a negative start | error |
