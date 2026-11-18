# oidx

Index builds for the storage engine: the classic build under an exclusive
table lock, and the online build that lets writes continue while the index
is being created.

## Layout

- `oidx/heap.py` – heap table; updates and deletes leave dead tuples, every new version gets a new TID
- `oidx/index.py` – secondary index and its catalog state (`building`, `ready`, `valid`)
- `oidx/build.py` – `build_offline` and `build_online`
- `oidx/server.py` – write path (plan, then apply after the statement's latency) and query routing
- `oidx/bloat.py` – bloat estimate for the maintenance dashboard
- `runbuild.py` – runs a build next to a write workload and checks the index against full scans

## How the online build stays consistent

Writers never take the table lock during an online build. Instead the build
goes through the catalog states:

1. `building` – the heap is scanned into the index; queries ignore it and the
   write path does not maintain it, so heavy writes are unaffected.
2. `ready` – the write path starts maintaining the index. The build waits for
   statements that were planned before this switch (their target-index list
   did not include the new index).
3. reconcile – rows written during the scan are added, entries left behind by
   updates and deletes are dropped, then the index becomes `valid`.

The write path also maintains the index after the build, removing the old
entry on update/delete, so the index does not accumulate dead tuples.

## Running

    python3 runbuild.py examples/busy.json out.json
    python3 -m unittest discover -s tests

`out.json` reports the final index state, the tick the build finished,
how many ticks writes spent waiting for the table lock (`blocked_ticks`),
and every key for which the index lookup and the full scan disagree.
