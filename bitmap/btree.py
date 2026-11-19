"""Single-column index.  NULLs are indexed too (stored as None) so that
IS NULL can use the index.  Dead heap tuples keep their entries."""
import operator

OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


class Index:
    def __init__(self, heap, column):
        self.heap = heap
        self.column = column
        self.entries = []
        for tid in heap.all_tids():
            self.entries.append((heap.fetch(tid)["row"].get(column), tid))

    def add(self, value, tid):
        self.entries.append((value, tid))

    def scan(self, op, value):
        """TIDs whose indexed value satisfies ``op value`` (or IS NULL)."""
        if op == "is_null":
            return sorted(tid for v, tid in self.entries if v is None)
        fn = OPS[op]
        out = []
        for v, tid in self.entries:
            try:
                if fn(v, value):
                    out.append(tid)
            except TypeError:
                continue
        return sorted(out)
