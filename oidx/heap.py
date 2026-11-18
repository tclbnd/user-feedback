"""Heap table.  Every insert or update writes a new tuple with a new TID;
updates and deletes mark the old tuple dead."""


class Heap:
    def __init__(self):
        self.tuples = {}      # tid -> {"key": ..., "val": ..., "live": bool}
        self.next_tid = 1
        self.locked_by = None  # set while an exclusive table lock is held

    def insert(self, key, val):
        tid = self.next_tid
        self.next_tid += 1
        self.tuples[tid] = {"key": key, "val": val, "live": True}
        return tid

    def find(self, rowid_key):
        for tid, t in self.tuples.items():
            if t["live"] and t["key"] == rowid_key:
                return tid
        return None

    def update(self, tid, key, val):
        self.tuples[tid]["live"] = False
        return self.insert(key, val)

    def delete(self, tid):
        self.tuples[tid]["live"] = False

    def live(self):
        return {tid: t for tid, t in self.tuples.items() if t["live"]}

    def seq_scan(self, pred):
        return sorted(t["val"] for t in self.tuples.values() if t["live"] and pred(t))
