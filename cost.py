"""Query model, equivalence classes and the cardinality / cost formulas."""
from math import prod

RANGE_SEL = 1 / 3


def table_of(col):
    return col.split(".", 1)[0]


class Query:
    def __init__(self, q):
        self.stats = q["tables"]
        self.items = q["from"]
        self.order = [it["table"] for it in self.items]
        self.shape = q.get("shape", "bushy")
        self.nullable = {it["table"]: [tuple(p) for p in it["on"]] for it in self.items if it.get("join") == "left"}
        pairs = [tuple(p) for it in self.items if it.get("join", "inner") == "inner" for p in it.get("on", [])]
        pairs += [tuple(w["eq"]) for w in q.get("where", []) if "eq" in w]
        self.filters = [w for w in q.get("where", []) if "op" in w]
        self.classes = self._classes(pairs)

    @staticmethod
    def _classes(pairs):
        """Merge equality pairs into transitive equivalence classes."""
        classes = []
        for a, b in pairs:
            merged = {a, b}
            rest = []
            for cls in classes:
                if cls & merged:
                    merged |= cls
                else:
                    rest.append(cls)
            rest.append(merged)
            classes = rest
        return classes

    def ndv(self, col):
        t, c = col.split(".", 1)
        return self.stats[t]["ndv"][c]

    def filter_sel(self, f):
        if f["op"] == "=":
            return 1 / self.ndv(f["col"])
        if f["op"] == "!=":
            return 1 - 1 / self.ndv(f["col"])
        return RANGE_SEL

    def scan_rows(self, table, filters):
        return self.stats[table]["rows"] * prod(self.filter_sel(f) for f in filters)

    def spanning(self, left, right):
        """Equivalence classes with columns on both sides."""
        out = []
        for cls in self.classes:
            lc = [c for c in cls if table_of(c) in left]
            rc = [c for c in cls if table_of(c) in right]
            if lc and rc:
                out.append((sorted(lc), sorted(rc)))
        return out

    def inner_rows(self, lrows, rrows, left, right):
        sel = prod(1 / max(min(self.ndv(c) for c in lc), min(self.ndv(c) for c in rc))
                   for lc, rc in self.spanning(left, right))
        return lrows * rrows * sel

    def left_rows(self, lrows, rrows, on, filters):
        inner = lrows * rrows * prod(1 / max(self.ndv(a), self.ndv(b)) for a, b in on)
        return max(lrows, inner) * prod(self.filter_sel(f) for f in filters)
