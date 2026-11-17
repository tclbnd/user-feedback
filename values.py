"""NULL-aware grouping keys, hash partitioning and aggregate state."""
import zlib


class Null:
    """The SQL NULL value.

    All instances compare equal and hash alike so that every NULL in a
    grouping column lands in the same group. Sorts before every value.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __eq__(self, other):
        return isinstance(other, Null)

    def __ne__(self, other):
        return not isinstance(other, Null)

    def __hash__(self):
        return 0

    def __lt__(self, other):
        return not isinstance(other, Null)

    def __le__(self, other):
        return isinstance(other, Null)

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return isinstance(other, Null)

    def __bool__(self):
        return False

    def __repr__(self):
        return "NULL"


NULL = Null()


def key_of(row, cols):
    """Grouping key for a row; a missing/NULL column becomes NULL."""
    return tuple(NULL if row.get(c) is None else row.get(c) for c in cols)


def plain(value):
    """Turn the internal NULL sentinel back into JSON ``None``."""
    return None if isinstance(value, Null) else value


def _encode(value):
    """Deterministic, collision-resistant encoding for JSON-ish values.

    Strings and containers are length-prefixed so the encoding of two
    different value trees can never be identical.
    """
    if isinstance(value, Null) or value is None:
        return "N"
    if isinstance(value, bool):
        return "b" + str(int(value))
    if isinstance(value, (int, float)):
        return "i" + repr(value)
    if isinstance(value, str):
        return "s" + str(len(value)) + ":" + value
    if isinstance(value, (list, tuple)):
        return "l" + str(len(value)) + ";" + "".join(_encode(v) for v in value)
    if isinstance(value, dict):
        keys = sorted(value, key=repr)
        return ("d" + str(len(keys)) + ";"
                + "".join(_encode(k) + _encode(value[k]) for k in keys))
    raise TypeError(f"unencodable value: {value!r}")


def partition(key, parts):
    """Hash a grouping key (a tuple possibly containing NULL) to a bucket."""
    return zlib.crc32(_encode(key).encode("utf-8")) % parts


def sort_key(value):
    """Sort key with NULL first; mixed-type columns never raise."""
    if value is None or isinstance(value, Null):
        return (0, 0, 0)
    if isinstance(value, (int, float)):  # bool is an int; numeric order works
        return (1, 0, value)
    if isinstance(value, str):
        return (1, 1, value)
    return (1, 2, repr(value))


class Agg:
    """Running state for one aggregate over one group.

    ``count(*)`` counts every row. Every other aggregate ignores NULL
    inputs; with ``distinct`` only the first occurrence of each non-NULL
    value counts. All aggregates of a group are updated in the same scan.
    """

    def __init__(self, spec):
        self.fn = spec["fn"]
        self.arg = spec["arg"]
        self.distinct = bool(spec.get("distinct", False))
        self.count = 0
        self.total = 0
        self.min = None
        self.max = None
        self.seen = set()

    def add(self, row):
        if self.arg == "*":
            self.count += 1
            return
        v = row.get(self.arg)
        if v is None:
            return  # aggregates ignore NULL (count(*) is the exception)
        if self.distinct:
            if v in self.seen:
                return
            self.seen.add(v)
        self.count += 1
        if self.fn in ("sum", "avg"):
            self.total += v
        if self.fn in ("min", "max"):
            if self.min is None or v < self.min:
                self.min = v
            if self.max is None or v > self.max:
                self.max = v

    def result(self):
        if self.fn == "count":
            return self.count
        if self.count == 0:  # sum/min/max/avg with no non-NULL input -> NULL
            return None
        if self.fn == "sum":
            return self.total
        if self.fn == "avg":
            return self.total / self.count
        if self.fn == "min":
            return self.min
        return self.max
