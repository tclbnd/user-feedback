# seqdb

Sequence support of the database layer: `CREATE SEQUENCE` options and the
functions `nextval`, `currval`, `lastval` and `setval`, used for order
numbers and invoice ids. Several application servers (sessions) share a
sequence. Behaviour is meant to be exactly PostgreSQL's.

## Layout

- `seqdb/sequence.py` – sequence objects and their stored state
- `seqdb/session.py` – per-session functions and transactions
- `seqdb/catalog.py` – the sequences of the database, order number helper
- `runseq.py` – replays operations from several sessions

## Semantics

State is PostgreSQL's `(last_value, is_called)` pair: with `is_called` false
the next value is `last_value` itself, otherwise `last_value + increment`.

- `nextval` never returns a value outside `[MINVALUE, MAXVALUE]`. At the
  bound it wraps to the opposite bound when `CYCLE` is set and otherwise
  raises an error, leaving the sequence untouched.
- Options: `INCREMENT` (may be negative), `MINVALUE`, `MAXVALUE`, `START`,
  `CYCLE`, `CACHE`. Defaults: ascending `1 .. 2^63-1` starting at `1`,
  descending `-(2^63-1) .. -1` starting at `-1`.
- `currval` and `lastval` are per session: they report what *this* session
  obtained, so another session advancing the sequence does not change them.
- `setval(seq, n, false)` sets the state to `(n, false)`, so the next
  `nextval` returns exactly `n`; `setval` returns `n`.
- `CACHE` `k` reserves a block of `k` values at once; concurrent sessions
  reserve following blocks, values of a session interleave with the others
  and reserved-but-unused values are skipped. A block is truncated at the
  bounds, so no value outside them is ever handed out.
- Sequence changes are non-transactional, as in PostgreSQL: `COMMIT` and
  `ROLLBACK` never undo a `nextval` or a `setval`. A rollback also keeps the
  block already cached by the session.

## Running

    python3 runseq.py examples/orders.json out.json
    python3 runseq.py examples/options.json out.json
    python3 -m unittest discover -s tests
