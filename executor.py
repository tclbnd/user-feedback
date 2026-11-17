"""Run a plan over the table and its indexes."""
import functools
import operator

OPS = {"=": operator.eq, "<": operator.lt, "<=": operator.le,
       ">": operator.gt, ">=": operator.ge}


class _Inf:
    """Sentinel that sorts below/above every real value (str or int)."""

    def __init__(self, name, below):
        self.name = name
        self._below = below

    def __lt__(self, other):
        if isinstance(other, _Inf):
            return self._below and not other._below
        return self._below

    def __gt__(self, other):
        if isinstance(other, _Inf):
            return other._below and not self._below
        return not self._below

    def __eq__(self, other):
        return isinstance(other, _Inf) and self._below == other._below

    def __hash__(self):
        return hash(self._below)

    def __repr__(self):
        return self.name


NEG_INF = _Inf("-inf", True)
POS_INF = _Inf("+inf", False)


def matches(row, conds):
    return all(OPS[op](row[col], val) for col, op, val in conds)


def index_entries(db, index):
    """One entry per row, sorted ascending by index columns then id."""
    cols = index["columns"]
    return sorted(({c: r[c] for c in cols + ["id"]} for r in db["rows"]),
                  key=lambda e: [e[c] for c in cols] + [e["id"]])


def _backwards_cmp(cols, reverse_len, e1, e2):
    """Ordering for a backwards scan.

    The first ``reverse_len`` index columns (the equality prefix plus the
    effective ORDER BY columns) are compared descending; every remaining
    index column and the trailing id are compared ascending, exactly as
    they are stored within each reversed prefix group.
    """
    for i, col in enumerate(cols):
        v1, v2 = e1[col], e2[col]
        if v1 == v2:
            continue
        if i < reverse_len:
            # Descending on the ordering prefix: larger value first.
            return 1 if v1 < v2 else -1
        # Ascending on every later index column.
        return -1 if v1 < v2 else 1
    # Equal index key: id always ascending.
    return (e1["id"] > e2["id"]) - (e1["id"] < e2["id"])


def seek_keys(db, query, plan):
    """Return the (start_key, end_key) the index scan is positioned on.

    Each key is a list of ``[column, value, inclusive]`` triples; an open
    side carries +/- infinity with ``inclusive=False``. Keys cover the
    equality prefix and the range column when one exists.
    """
    where = query.get("where", [])
    eqv = [next(v for c, op, v in where if c == col and op == "=")
           for col in plan["eq"]]

    def side(bound, open_value):
        if bound is None:
            value, inclusive = open_value, False
        else:
            value, inclusive = bound
        parts = [[c, v, True] for c, v in zip(plan["eq"], eqv)]
        if plan["range"]:
            parts.append([plan["range"], value, inclusive])
        return parts

    return side(plan["lo"], NEG_INF), side(plan["hi"], POS_INF)


def _lower_bound(keys, seek, n):
    """First index whose first n key components are >= seek."""
    lo, hi = 0, len(keys)
    while lo < hi:
        mid = (lo + hi) // 2
        if keys[mid][:n] < seek:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _upper_bound(keys, seek, n):
    """First index whose first n key components are > seek."""
    lo, hi = 0, len(keys)
    while lo < hi:
        mid = (lo + hi) // 2
        if keys[mid][:n] <= seek:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _scan_slice(entries, cols, plan, eqv):
    """Position the scan on its start/end keys and return the entry slice.

    Boundary keys are compared only against the matching prefix of each
    index key, so an exclusive bound behaves like a SQL key condition and
    entries sharing the boundary value stay on the correct side.
    """
    def full_key(entry):
        return tuple(entry[c] for c in cols) + (entry["id"],)

    keys = [full_key(e) for e in entries]
    prefix = tuple(eqv)
    # An open side still ends at the equality prefix (or the whole index
    # when there is no equality prefix).
    open_end = (_upper_bound(keys, prefix, len(prefix)) if prefix
                else len(keys))
    open_start = (_lower_bound(keys, prefix, len(prefix)) if prefix else 0)
    if plan["range"] is not None:
        n = len(prefix) + 1
        if plan["lo"] is None:
            start = open_start
        else:
            seek = prefix + (plan["lo"][0],)
            start = (_lower_bound if plan["lo"][1]
                     else _upper_bound)(keys, seek, n)
        if plan["hi"] is None:
            end = open_end
        else:
            seek = prefix + (plan["hi"][0],)
            end = (_upper_bound if plan["hi"][1]
                   else _lower_bound)(keys, seek, n)
    else:
        # Equality-only prefix: bracket every entry beginning with it.
        start = open_start
        end = open_end
    return entries[start:end]


def execute(db, query, plan):
    rows = {r["id"]: r for r in db["rows"]}
    out, scanned, fetches = [], 0, 0
    if plan["index"] is None:
        for r in sorted(rows.values(), key=lambda r: r["id"]):
            scanned += 1
            if matches(r, plan["table_filter"]):
                fetches += 1
                out.append(r)
    else:
        index = next(i for i in db["indexes"] if i["name"] == plan["index"])
        cols = index["columns"]
        where = query.get("where", [])
        eqv = [next(v for c, op, v in where if c == col and op == "=")
               for col in plan["eq"]]
        window = _scan_slice(index_entries(db, index), cols, plan, eqv)
        scanned = len(window)
        if plan["direction"] == "desc":
            # Backwards scan: the effective ordering prefix is reversed,
            # while later index columns and the id tiebreaker stay
            # ascending, so entries sharing an ordered key keep id order.
            window.sort(key=functools.cmp_to_key(
                lambda e1, e2: _backwards_cmp(
                    cols, plan["order_prefix_len"], e1, e2)))
        if plan["covering"]:
            # Every needed column rides in the index entry: no row fetches.
            for e in window:
                if matches(e, plan["index_filter"]):
                    out.append(e)
        else:
            for e in window:
                if not matches(e, plan["index_filter"]):
                    continue
                fetches += 1
                r = rows[e["id"]]
                if matches(r, plan["table_filter"]):
                    out.append(r)
    if plan["sort"]:
        for col, d in reversed(query.get("order_by", [])):
            out.sort(key=lambda r: r[col], reverse=d == "desc")
    sel = query.get("select") or (sorted(out[0]) if out else [])
    return [{c: r[c] for c in sel} for r in out], scanned, fetches
