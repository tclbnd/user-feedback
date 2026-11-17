"""Optimistic transactions with backward validation."""
import threading

from kv import Store


class Txn:
    def __init__(self, engine):
        self.snap = engine.store.snapshot()
        self.start = engine.commit_no
        self.reads, self.ranges, self.writes = set(), [], {}

    def view(self):
        merged = dict(self.snap)
        merged.update(self.writes)
        # Writes with value None are deletes; they must not be visible to
        # this transaction's own gets and scans either.
        return {k: v for k, v in merged.items() if v is not None}

    def get(self, key):
        self.reads.add(key)
        return self.view().get(key)

    def scan(self, lo, hi):
        items = Store.scan(self.view(), lo, hi)
        # A scan observes the rows it returns *and* the absence of every
        # other key in the range.  Record both the rows (so a concurrent
        # delete is detected) and the predicate (so a concurrent insert or
        # delete of an unseen row is detected) in the read set.
        self.reads.update(key for key, _ in items)
        self.ranges.append((lo, hi))
        return items

    def put(self, key, value):
        self.writes[key] = value

    def delete(self, key):
        self.writes[key] = None


class Engine:
    def __init__(self):
        self.store, self.commit_no, self.log = Store(), 0, []
        # Validation and installing a transaction's writes must be one
        # atomic step, otherwise a transaction could validate against a log
        # that changes before its own writes land.
        self._lock = threading.Lock()

    def begin(self):
        # Take the snapshot and the commit number together, so `start`
        # always describes exactly the state in `snap`.
        with self._lock:
            return Txn(self)

    def conflicts(self, txn, written):
        """True if `written` intersects the rows or ranges `txn` read."""
        for key in written:
            if key in txn.reads:
                return True
            if any(lo <= key < hi for lo, hi in txn.ranges):
                return True
        return False

    def commit(self, txn):
        # Read-only fast path: nothing to validate, nothing to install.
        # Such a transaction is serialized at its begin.
        if not txn.writes:
            return "committed"
        with self._lock:
            for no, written in self.log:
                if no > txn.start and self.conflicts(txn, written):
                    return "aborted"
            self.store.install(txn.writes)
            self.commit_no += 1
            self.log.append((self.commit_no, set(txn.writes)))
            return "committed"
