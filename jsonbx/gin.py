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

from .value import Num, kind


def _text(value):
    if isinstance(value, Num):
        return "n:" + value.text
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
        sets = [self.entries.get(i, set()) for i in items]
        return set.intersection(*sets) if sets else set()

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
                for i, x in enumerate(v):
                    walk(x, path + [str(i)])
            else:
                items.add(self._hash(path, v))
        walk(doc, [])
        return items

    def search(self, op, arg):
        if op != "@>":
            raise ValueError(f"{self.name} does not support {op}")
        return self._all(self.extract_value(arg))


OPCLASSES = {"jsonb_ops": JsonbOps, "jsonb_path_ops": JsonbPathOps}
