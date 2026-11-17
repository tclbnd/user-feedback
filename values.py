"""NULL-aware grouping keys and aggregate state."""
import json
import zlib


class Null:
    """Sorts before every value."""

    def __lt__(self, other):
        return not isinstance(other, Null)

    def __gt__(self, other):
        return False

    def __repr__(self):
        return "NULL"


def key_of(row, cols):
    return tuple(Null() if row.get(c) is None else row.get(c) for c in cols)


def plain(value):
    return None if isinstance(value, Null) else value


def partition(key, parts):
    return zlib.crc32(json.dumps(key, default=repr).encode()) % parts


def sort_key(value):
    return (value is not None, value if value is not None else 0)


class Agg:
    def __init__(self, spec):
        self.fn, self.arg, self.distinct = spec["fn"], spec["arg"], spec.get("distinct", False)
        self.count, self.values, self.seen = 0, [], set()

    def add(self, row):
        if self.arg == "*":
            self.count += 1
            return
        v = row.get(self.arg)
        if self.distinct:
            if v in self.seen:
                return
            self.seen.add(v)
        elif v is None:
            return
        self.count += 1
        if v is not None:
            self.values.append(v)

    def result(self):
        if self.fn == "count":
            return self.count
        if not self.values:
            return None
        if self.fn == "sum":
            return sum(self.values)
        if self.fn == "avg":
            return sum(self.values) / len(self.values)
        return min(self.values) if self.fn == "min" else max(self.values)
