"""Index builds.

``build_offline`` holds an exclusive table lock for the whole build.

``build_online`` lets writes continue and still ends with an index that
matches the table exactly.  It runs in three phases, none of which takes the
table lock:

1. scan the heap while the catalog entry is still ``building``: queries do
   not use it and the write path does not maintain it yet;
2. flip the entry to ``ready`` so writes start maintaining it, then wait for
   statements planned before the flip (their target-index list was computed
   without this index, so their changes would otherwise be invisible here);
3. reconcile with the heap.  Rows inserted or updated during the scan are
   missing from the index, and rows updated or deleted during the scan left
   entries behind that now point at dead tuples.  Only then does the index
   become ``valid``.

Statements planned from phase 2 onwards are maintained by the write path;
inside phase 3 only statements planned before the barrier can still commit.
"""
from .index import Index

BATCH = 20


def _scan(index, rows, server):
    """Index ``rows``, yielding between batches so other work can run."""
    for n, (tid, t) in enumerate(rows, 1):
        index.add(t["key"], tid)
        if n % BATCH == 0:
            yield index


def build_offline(heap, server):
    index = Index(heap)
    server.indexes.append(index)
    heap.locked_by = "create index"
    # Statements planned before the lock was taken may still commit while we
    # build; let them finish so the scan sees their final rows.  No new
    # statement can be planned while the lock is held.
    while server.inflight_without(index):
        yield index
    yield from _scan(index, sorted(heap.live().items()), server)
    index.state = "valid"
    heap.locked_by = None
    yield index


def build_online(heap, server):
    index = Index(heap)
    server.indexes.append(index)

    # Phase 1: first copy from a heap scan.  Writers keep running; because
    # the entry is still ``building`` they do not maintain it.
    yield from _scan(index, sorted(heap.live().items()), server)

    # Phase 2: start capturing writes.  Statements planned from now on keep
    # the index up to date themselves; the ones already in flight were planned
    # without it, so let them commit before phase 3 reconciles.
    index.state = "ready"
    while server.inflight_without(index):
        yield index

    # Phase 3: catch up on everything that changed since the scan started.
    # Statements that commit from here on maintain the index themselves, so
    # each candidate is checked against the *current* heap state: a row that
    # was already deleted or updated again must not be re-added.
    for n, tid in enumerate(sorted(heap.tuples), 1):
        t = heap.tuples.get(tid)
        if t and t["live"] and not index.contains(t["key"], tid):
            index.add(t["key"], tid)
        if n % BATCH == 0:
            yield index
    index.compact()

    index.state = "valid"
    yield index
