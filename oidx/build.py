"""Index builds.

``build_offline`` holds an exclusive table lock for the whole build.
``build_online`` lets writers continue; it is a generator that yields after
each batch so the server can interleave other work.
"""
from .index import Index

BATCH = 20


def build_offline(heap, server):
    index = Index(heap)
    server.indexes.append(index)
    heap.locked_by = "create index"
    for tid, t in sorted(heap.live().items()):
        index.add(t["key"], tid)
        if tid % BATCH == 0:
            yield index
    index.state = "valid"
    heap.locked_by = None
    yield index


def build_online(heap, server):
    index = Index(heap)
    server.indexes.append(index)
    snapshot = sorted((tid, t["key"]) for tid, t in heap.live().items())
    for i, (tid, key) in enumerate(snapshot, 1):
        index.add(key, tid)
        if i % BATCH == 0:
            yield index
    index.state = "valid"
    yield index
