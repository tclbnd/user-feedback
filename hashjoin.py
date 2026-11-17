"""Join keys and the in-memory hash join.

Only build rows whose key is not NULL ever enter the hash table: a NULL key
never equals anything, NULL included, so NULL rows must not be matchable.
They are still tracked so that right outer joins can emit them as unmatched.
"""

JOIN_TYPES = ("inner", "left", "right", "semi", "anti")


class JoinError(ValueError):
    pass


class MemoryExceeded(Exception):
    pass


def key_of(row, column):
    """Return the join key of a row, or None for a NULL key.

    A missing column or an explicit JSON null is NULL. Keys are integers or
    strings and the two families never compare equal. Booleans and other
    types are rejected rather than treated as integers.
    """
    value = row.get(column)
    if isinstance(value, bool) or not (value is None or isinstance(value, (int, str))):
        raise JoinError(
            "join key %r must be an integer or a string, got %r" % (column, value)
        )
    return value


class InMemoryJoin:
    """Grace hash join for a build side that fits inside the memory quota.

    ``work_mem`` bounds the number of build rows held in the hash table,
    i.e. the number of non-NULL-keyed build rows. Exceeding it raises
    MemoryExceeded so the executor is killed instead of silently blowing
    the quota.
    """

    def __init__(self, kind, left_col, right_col, work_mem, emit):
        self.kind = kind
        self.left_col = left_col
        self.right_col = right_col
        self.work_mem = work_mem
        self.emit = emit

    def run(self, build_rows, probe_rows):
        table = {}
        built = []
        held = 0
        for row in build_rows:
            entry = [row, False]
            built.append(entry)
            key = key_of(row, self.right_col)
            if key is None:
                # NULL keys never participate in equality matching.
                continue
            if held >= self.work_mem:
                raise MemoryExceeded(
                    "more than %d build rows in the hash table" % self.work_mem
                )
            table.setdefault(key, []).append(entry)
            held += 1

        for row in probe_rows:
            key = key_of(row, self.left_col)
            matches = table.get(key, ()) if key is not None else ()
            self._probe(row, matches)

        if self.kind == "right":
            for row, hit in built:
                if not hit:
                    self.emit({"l": None, "r": row})

    def _probe(self, row, matches):
        if self.kind == "semi":
            if matches:
                self.emit(row)
        elif self.kind == "anti":
            if not matches:
                self.emit(row)
        else:
            for entry in matches:
                entry[1] = True
                self.emit({"l": row, "r": entry[0]})
            if not matches and self.kind == "left":
                self.emit({"l": row, "r": None})
