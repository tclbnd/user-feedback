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

## Running

    python3 runseq.py examples/orders.json out.json
    python3 -m unittest discover -s tests
