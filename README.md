# bmscan

Bitmap index scans of the executor: each indexed predicate produces a TID
bitmap, AND / OR combine bitmaps, and the bitmap heap scan visits the
pages and returns the rows.

## Layout

- `bitmap/heap.py` – heap pages; deleted rows stay as dead tuples until vacuum, updates leave a version chain
- `bitmap/vacuum.py` – dead tuple and update chain statistics for the maintenance report
- `bitmap/btree.py` – single-column indexes (NULLs are indexed)
- `bitmap/tidbitmap.py` – TID bitmaps with lossy pages under `work_mem`
- `bitmap/executor.py` – bitmap index/heap scan and the sequential scan used as reference
- `bitmap/report.py` – the sales report conditions
- `scan.py` – runs one condition both ways

## Running

    python3 scan.py examples/sales.json examples/q_or.json out.json
    python3 -m unittest discover -s tests

`out.json` contains the ids returned by the bitmap scan (`ids`), by the
sequential scan (`seq_ids`), and how many pages were visited, how many
were lossy and how many tuples were rechecked, plus the vacuum statistics.
The table file may also list `updates` (see `scan.py`).
