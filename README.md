# dumper

The nightly logical backup tool. It exports tables in chunks while the
order service keeps writing, so a dump can run without a maintenance
window.

## Layout

- `dumper/mvcc.py` – the multi-version store the tool reads from, with autovacuum
- `dumper/dump.py` – the chunked dump
- `dumper/workload.py` – the order service's write pattern (orders and lines always change together)
- `dumper/restore.py` – checks run by the recovery drill
- `rehearse.py` – runs a dump next to the workload

## Running

    python3 rehearse.py examples/nightly.json out.json
    python3 -m unittest discover -s tests

A dump is usable for recovery only if it equals the database as it was at
one single point in time. The dump gets that by pinning one MVCC snapshot
before it reads anything and reading every chunk at that timestamp, so all
tables come from the same state and writes committed during the dump are
simply not part of it. The pinned snapshot also keeps autovacuum from
removing versions the dump still has to read, so no table lock is taken
and writers never wait. `lock_tables: true` remains available as a
fallback, but it blocks writes for the whole dump, which the business does
not accept.
