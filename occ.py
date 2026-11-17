"""Optimistic transactions with backward validation."""
from kv import Store


class Txn:
    def __init__(self, engine):
        self.snap = engine.store.snapshot()
        self.start = engine.commit_no
        self.reads, self.ranges, self.writes = set(), [], {}

    def view(self):
        merged = dict(self.snap)
        merged.update(self.writes)
        return merged

    def get(self, key):
        self.reads.add(key)
        return self.view().get(key)

    def scan(self, lo, hi):
        return Store.scan(self.view(), lo, hi)

    def put(self, key, value):
        self.writes[key] = value

    def delete(self, key):
        self.writes[key] = None


class Engine:
    def __init__(self):
        self.store, self.commit_no, self.log = Store(), 0, []

    def begin(self):
        return Txn(self)

    def conflicts(self, txn, written):
        return any(k in txn.reads or any(lo <= k < hi for lo, hi in txn.ranges) for k in written)

    def commit(self, txn):
        for no, written in self.log:
            if no > txn.start and self.conflicts(txn, written):
                return "aborted"
        self.store.install(txn.writes)
        self.commit_no += 1
        self.log.append((self.commit_no, set(txn.writes)))
        return "committed"
