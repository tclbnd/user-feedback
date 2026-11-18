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
        self.inflight.append({"op": op, "apply_at": tick + op.get("latency", 0), "indexes": targets})
        return True

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
            if op["op"] == "delete":
                heap.delete(old)
                return
            tid = heap.update(old, op.get("new_key", op["key"]), op["val"])
        for ix in indexes:
            ix.add(heap.tuples[tid]["key"], tid)

    def query(self, key):
        for ix in self.indexes:
            if ix.usable():
                return ix.lookup(key)
        return self.heap.seq_scan(lambda t: t["key"] == key)
