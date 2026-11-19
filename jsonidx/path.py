"""JSON path expressions and row-by-row evaluation.

Path syntax: ``$`` followed by steps ``.key``, ``[*]`` (every element of an
array; nothing for non-arrays) and ``[n]`` (one element).  A path produces a
list of values; a missing key produces nothing.

Predicates (``{"path": ..., "op": ..., "value": ...}``):

- ``=``: some produced value equals ``value`` (JSON equality: 1 equals 1.0,
  true is not 1, "1" is not 1)
- ``exists``: the path produces at least one value (a null value counts)
- ``is_null``: some produced value is null
- ``is_empty_array``: some produced value is ``[]``
- ``<``, ``<=``, ``>``, ``>=``: some produced value compares that way with
  ``value``.  Only numbers compare with numbers and strings with strings
  (code point order); booleans, null, arrays and objects never satisfy a
  range comparison, and a number never compares with a string.
"""
import operator
import re

STEP = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\*|\d+)\]")


def parse(path):
    if not path.startswith("$"):
        raise ValueError(f"path must start with $: {path}")
    steps, pos = [], 1
    while pos < len(path):
        m = STEP.match(path, pos)
        if not m:
            raise ValueError(f"bad path {path!r} at {pos}")
        steps.append(("key", m.group(1)) if m.group(1) else ("idx", m.group(2)))
        pos = m.end()
    return steps


def produce(doc, steps):
    values = [doc]
    for kind, arg in steps:
        nxt = []
        for v in values:
            if kind == "key":
                if isinstance(v, dict) and arg in v:
                    nxt.append(v[arg])
            elif arg == "*":
                if isinstance(v, list):
                    nxt.extend(v)
            elif isinstance(v, list) and int(arg) < len(v):
                nxt.append(v[int(arg)])
        values = nxt
    return values


def json_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) == type(b) and a == b


RANGE = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def comparable(a, b):
    return (_number(a) and _number(b)) or (isinstance(a, str) and isinstance(b, str))


def matches(doc, pred):
    values = produce(doc, parse(pred["path"]))
    op = pred["op"]
    if op == "exists":
        return bool(values)
    if op == "is_null":
        return any(v is None for v in values)
    if op == "is_empty_array":
        return any(v == [] and isinstance(v, list) for v in values)
    if op == "=":
        return any(json_equal(v, pred["value"]) for v in values)
    if op in RANGE:
        return any(comparable(v, pred["value"]) and RANGE[op](v, pred["value"]) for v in values)
    raise ValueError(f"unknown op {op}")
