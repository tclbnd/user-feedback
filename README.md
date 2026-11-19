# winx

Window functions of the reporting engine: running totals, moving averages,
rankings and first/last values per partition. Frames follow PostgreSQL
(`ROWS`, `RANGE`, `GROUPS`, offsets, `EXCLUDE ...`, and the default frame
when none is written); results must be what PostgreSQL returns for the
same window definition.

## Layout

- `winx/frames.py` – frame definitions and which rows are in a row's frame
- `winx/functions.py` – aggregates, value functions and ranking functions
- `winx/engine.py` – partitioning, ordering and evaluation of a call
- `runwin.py` – evaluates calls over a table

## Running

    python3 runwin.py examples/sales.json examples/calls.json out.json
    python3 -m unittest discover -s tests
