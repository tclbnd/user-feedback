"""jsonb values.

Documents are stored the way PostgreSQL stores jsonb: parsed once, with
object keys de-duplicated and sorted, numbers kept as exact decimals.  The
in-memory form is plain Python: dict, list, str, bool, None and ``Num``.
"""
import json
from decimal import Decimal


class Num:
    """A JSON number kept as written (the text is what the API echoes back)."""

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return self.text

    def __eq__(self, other):
        return isinstance(other, Num) and self.text == other.text

    def __hash__(self):
        return hash(self.text)

    def decimal(self):
        return Decimal(self.text)


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key not in out:
            out[key] = value
    return dict(sorted(out.items()))


def parse(text):
    return json.loads(text, object_pairs_hook=_pairs, parse_float=Num, parse_int=Num,
                      parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))


def dump(value):
    if isinstance(value, Num):
        return value.text
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)}: {dump(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(dump(v) for v in value) + "]"
    return json.dumps(value)


def kind(value):
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Num):
        return "number"
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    return "string"
