"""ORDER BY parsing and row sort keys."""


class OrderError(ValueError):
    pass


def parse(text):
    keys = []
    for part in text.split(","):
        words = part.split()
        if not words:
            raise OrderError(f"empty sort key in {text!r}")
        key = {"col": words[0], "desc": False, "nulls_first": None, "collate": "binary"}
        rest = [w.lower() for w in words[1:]]
        while rest:
            word = rest.pop(0)
            if word in ("asc", "desc"):
                key["desc"] = word == "desc"
            elif word == "collate":
                if not rest:
                    raise OrderError(f"COLLATE needs a name in {part.strip()!r}")
                name = rest.pop(0)
                if name not in ("binary", "nocase"):
                    raise OrderError(
                        f"unknown collation {name!r} in {part.strip()!r}"
                    )
                key["collate"] = name
            elif word == "nulls":
                if not rest:
                    raise OrderError(f"NULLS needs FIRST or LAST in {part.strip()!r}")
                where = rest.pop(0)
                if where not in ("first", "last"):
                    raise OrderError(
                        f"NULLS must be FIRST or LAST, not {where!r} in {part.strip()!r}"
                    )
                key["nulls_first"] = where == "first"
            else:
                raise OrderError(f"unexpected {word!r} in sort key {part.strip()!r}")
        keys.append(key)
    return keys


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _payload(value, collate):
    """Comparable payload for a non-NULL value.

    The first element orders the JSON type: numbers before strings, as
    documented.  Values of the same type use their natural order.  Under
    ``nocase`` strings compare by ``casefold()`` and strings that fold to the
    same text are equal sort keys (no raw-string tie-breaker, so the sort
    stays stable).
    """
    if _is_num(value):
        if value != value:  # NaN is not equal to itself
            # PostgreSQL orders NaN after every other number; do the same so
            # that a column containing NaN still has a total order.
            return (0, 1, 0)
        return (0, 0, value)
    if isinstance(value, str):
        return (1, value.casefold() if collate == "nocase" else value)
    if isinstance(value, bool):
        return (2, 1 if value else 0)
    # JSON objects/arrays are not orderable in SQL either; give them a total
    # order so that a heterogeneous column cannot crash the operator.
    return (3, type(value).__name__, repr(value))


class _Value:
    """A payload together with its key's direction.

    ``<`` flips for DESC while ``==`` stays value equality, so one ascending
    sort (or heap merge) handles mixed ASC/DESC key lists, and rows with equal
    keys are left in input order by Python's stable sort.
    """

    __slots__ = ("payload", "desc")

    def __init__(self, payload, desc):
        self.payload = payload
        self.desc = desc

    def __eq__(self, other):
        return isinstance(other, _Value) and self.payload == other.payload

    def __lt__(self, other):
        if self.desc:
            return other.payload < self.payload
        return self.payload < other.payload

    def __hash__(self):
        return hash(self.payload)

    def __repr__(self):  # pragma: no cover - debugging aid
        return "_Value(%r, desc=%r)" % (self.payload, self.desc)


class _Reverse:
    """Invert the order of a key tuple; used for the bounded top-N max-heap."""

    __slots__ = ("inner",)

    def __init__(self, inner):
        self.inner = inner

    def __eq__(self, other):
        return isinstance(other, _Reverse) and self.inner == other.inner

    def __lt__(self, other):
        return other.inner < self.inner

    def __hash__(self):
        return hash(self.inner)


def row_key(keys):
    """Build the ascending key tuple for a parsed ORDER BY clause.

    NULL placement is resolved before direction (as in PostgreSQL), so DESC
    does not flip NULLS FIRST/LAST.
    """
    specs = []
    for key in keys:
        nulls_first = key.get("nulls_first")
        if nulls_first is None:
            # PostgreSQL defaults: ASC -> NULLS LAST, DESC -> NULLS FIRST.
            nulls_first = bool(key["desc"])
        specs.append(
            (key["col"], bool(key["desc"]), bool(nulls_first), key.get("collate", "binary"))
        )

    def key(row):
        parts = []
        for col, desc, nulls_first, collate in specs:
            value = row.get(col)
            if value is None:
                # Rank 0 sorts before, rank 2 after every non-NULL value.
                parts.append((0 if nulls_first else 2, None))
            else:
                parts.append((1, _Value(_payload(value, collate), desc)))
        return tuple(parts)

    return key
