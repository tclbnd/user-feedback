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

## Frame semantics

Frames are resolved exactly as PostgreSQL resolves them:

- `ROWS` offsets count physical rows, `RANGE` offsets count value distance
  (and so need exactly one `ORDER BY` column), `GROUPS` offsets count peer
  groups.
- `CURRENT ROW` means the current row in `ROWS` mode but the edge of the
  current peer group in `RANGE` and `GROUPS` mode.  Peers are rows equal on
  the whole `ORDER BY` list, so tied dates all see the same running total.
- With no frame clause the default is `RANGE UNBOUNDED PRECEDING AND CURRENT
  ROW`; without `ORDER BY` every row is a peer and the frame is the whole
  partition (`last_value` is the partition's last row, not the current row).
- `EXCLUDE CURRENT ROW / GROUP / TIES` remove rows after the bounds are
  computed.
- NULL ordering values sort last for ascending and first for descending
  windows, and are peers of each other.
- Definitions PostgreSQL rejects (start `UNBOUNDED FOLLOWING`, end
  `UNBOUNDED PRECEDING`, a `FOLLOWING` start ending at `CURRENT ROW`,
  `RANGE` offsets with several ordering columns, `GROUPS` without `ORDER BY`)
  raise `ValueError`.

## Running

    python3 runwin.py examples/sales.json examples/calls.json out.json
    python3 -m unittest discover -s tests
