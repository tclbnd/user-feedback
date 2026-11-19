# jsonidx

JSON document column with a path index. Product records are stored as JSON
documents; the API filters them with JSON path predicates, and the path
index turns common predicates into index probes.

## Layout

- `jsonidx/path.py` – path syntax, predicate semantics and row-by-row evaluation
- `jsonidx/pathindex.py` – the path-value index
- `jsonidx/table.py` – document table and the choice between index probe and scan
- `jsonidx/catalog_stats.py` – key frequencies for the schema explorer
- `query.py` – runs predicates both through the planner and as a full scan

## Running

    python3 query.py examples/products.json examples/predicates.json out.json
    python3 -m unittest discover -s tests

Predicate semantics are documented at the top of `jsonidx/path.py`. Using
the index must never change a query's result; it only makes it faster.
