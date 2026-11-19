# cancel

Session, transaction and lock handling of the SQL front end, with a
tick-based simulator for reproducing concurrency issues.

## Layout

- `cancel/locks.py` – table and row lock manager (IS/IX/S/X, FIFO wait queues)
- `cancel/session.py` – sessions, implicit and explicit transactions, statement execution, cancel and timeout handling
- `cancel/scheduler.py` – drives sessions one step per tick, applies `statement_timeout` and operator cancel requests
- `cancel/monitor.py` – lock view for the ops console
- `cancel/batch.py` – record-by-record batch jobs (one savepoint per record)
- `simulate.py` – runs a scenario file

## Running

    python3 simulate.py examples/cancel_update.json out.json
    python3 -m unittest discover -s tests

Statement results are `ok`, `canceled`, `timeout`, `rolled back` (COMMIT of
a failed transaction) or an `error: ...`. The output also shows the data
after all sessions finish, each session's transaction state, locks still
held, sessions still waiting (`blocked`) and sessions that never finished
their script (`stuck`).

Cancel and timeout follow the usual server behaviour: the interrupted
statement fails; in an explicit transaction the transaction becomes failed
and only ROLLBACK (or COMMIT, which then rolls back) ends it; outside an
explicit transaction the implicit transaction is rolled back.

Savepoints follow PostgreSQL: ROLLBACK TO SAVEPOINT undoes what was done
after the savepoint and ends the failed state, so a batch can skip one bad
record and go on in the same transaction.
