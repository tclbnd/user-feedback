"""GIN indexes on a jsonb column.

Two operator classes, as in PostgreSQL:

- ``jsonb_ops`` (the default) indexes every key and every value separately.
  It supports ``@>``, ``?``, ``?|`` and ``?&``.
- ``jsonb_path_ops`` indexes one item per value: a hash of the value
  together with the chain of keys leading to it.  It is smaller and more
  selective for ``@>``, and supports only ``@>``.

An index lookup returns candidate rows; the executor always re-checks the
candidates with the operator itself, so an index may return extra rows but
must never miss one.
"""
import hashlib
from collections import defaultdict
from decimal import Decimal

from .value import Num


def _canonical_number(text):
    """One spelling per numeric value, so 20, 20.0 and 2e1 index identically."""
    d = Decimal(text).normalize()
    if d == 0:
        d = Decimal(0)  # collapse 0, 0.0, -0 into a single form
    return str(d)


def _text(value):
    if isinstance(value, Num):
        return "n:" + _canonical_number(value.text)
    if isinstance(value, bool):
        return "b:" + str(value).lower()
    if value is None:
        return "z:"
    return "s:" + value


class GinIndex:
    def __init__(self):
        self.entries = defaultdict(set)   # item -> row ids
        self.rows = set()

    def insert(self, rid, doc):
        self.rows.add(rid)
        for item in self.extract_value(doc):
            self.entries[item].add(rid)

    def delete(self, rid, doc):
        self.rows.discard(rid)
        for item in self.extract_value(doc):
            self.entries[item].discard(rid)

    def _all(self, items):
        if not items:
            # an empty query (e.g. @> '{}') matches every row for recheck
            return set(self.rows)
        sets = [self.entries.get(i, set()) for i in items]
        return set.intersection(*sets)

    def size(self):
        return sum(len(v) for v in self.entries.values())


class JsonbOps(GinIndex):
    name = "jsonb_ops"
    operators = ("@>", "?", "?|", "?&")

    def extract_value(self, doc):
        items = set()

        def walk(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    items.add("K" + k)
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            else:
                if isinstance(v, str):
                    # string values are indexed like keys, so `?` also
                    # finds documents with the string as an array element
                    items.add("K" + v)
                else:
                    items.add("V" + _text(v))
        walk(doc)
        return items

    def search(self, op, arg):
        if op == "@>":
            return self._all(self.extract_value(arg))
        if op == "?":
            return set(self.entries.get("K" + arg, set()))
        if op == "?|":
            out = set()
            for k in arg:
                out |= self.entries.get("K" + k, set())
            return out
        if op == "?&":
            return self._all(["K" + k for k in arg])
        raise ValueError(op)


class JsonbPathOps(GinIndex):
    name = "jsonb_path_ops"
    operators = ("@>",)

    @staticmethod
    def _hash(path, value):
        return hashlib.sha1(("/".join(path) + "=" + _text(value)).encode()).hexdigest()[:16]

    def extract_value(self, doc):
        items = set()

        def walk(v, path):
            if isinstance(v, dict):
                for k, x in v.items():
                    walk(x, path + [k])
            elif isinstance(v, list):
                # array elements share the path: jsonb_path_ops ignores
                # array structure, so element order and count don't matter
                for x in v:
                    walk(x, path)
            else:
                items.add(self._hash(path, v))
        walk(doc, [])
        return items

    def search(self, op, arg):
        if op != "@>":
            raise ValueError(f"{self.name} does not support {op}")
        return self._all(self.extract_value(arg))


OPCLASSES = {"jsonb_ops": JsonbOps, "jsonb_path_ops": JsonbPathOps}
