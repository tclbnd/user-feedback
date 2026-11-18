"""Write path of the server.

A write statement first plans (looks up the target row and decides which
indexes it must maintain) and applies its changes ``latency`` ticks later,
which is when the transaction commits.
"""


class Server:
    def __init__(self, heap):
        self.heap = heap
        self.indexes = []
        self.inflight = []
        self.blocked_ticks = 0

    def submit(self, op, tick):
        if self.heap.locked_by:
            return False
        targets = [ix for ix in self.indexes if ix.maintained()]
        # ``targets`` is fixed at plan time: a statement only ever maintains
        # the indexes that were already ``ready`` when it was planned.
        self.inflight.append({"op": op, "apply_at": tick + op.get("latency", 0), "indexes": targets})
        return True

    def inflight_without(self, index):
        """True while a statement that does not maintain ``index`` has not
        committed.  Such a statement can still change rows without leaving a
        trace in the index, so a build must wait for them."""
        return any(index not in w["indexes"] for w in self.inflight)

    def apply_due(self, tick):
        due = [w for w in self.inflight if w["apply_at"] <= tick]
        self.inflight = [w for w in self.inflight if w["apply_at"] > tick]
        for w in due:
            self._apply(w["op"], w["indexes"])

    def _apply(self, op, indexes):
        heap = self.heap
        if op["op"] == "insert":
            tid = heap.insert(op["key"], op["val"])
        else:
            old = heap.find(op["key"])
            if old is None:
                return
            old_key = heap.tuples[old]["key"]
            if op["op"] == "delete":
                heap.delete(old)
                for ix in indexes:
                    ix.remove(old_key, old)
                return
            tid = heap.update(old, op.get("new_key", op["key"]), op["val"])
            for ix in indexes:
                ix.remove(old_key, old)
        for ix in indexes:
            ix.add(heap.tuples[tid]["key"], tid)

    def query(self, key):
        for ix in self.indexes:
            if ix.usable():
                return ix.lookup(key)
        return self.heap.seq_scan(lambda t: t["key"] == key)
