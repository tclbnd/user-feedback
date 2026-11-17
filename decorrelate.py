"""Decorrelated subqueries: the inner side is evaluated once, grouped by the
correlation operands and aggregated per group.

A correlated scalar or EXISTS subquery becomes a join on the correlation
operands followed by a grouped aggregate.  Equality correlation operands form
the grouping key (a dictionary lookup per outer row); inequality operands are
checked against the pre-aggregated groups of the matching equality bucket.  The
inner table and every nested subquery are therefore evaluated once instead of
once per outer row.
"""
import bisect

from engine import OPS, MultipleRows, and3

COMPARISONS = frozenset(OPS)
FLIP = {"=": "=", "<>": "<>", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
_UNSET = object()


def refs(node):
    """Aliases an operand or predicate reads."""
    if node.get("col"):
        return {node["col"].split(".", 1)[0]}
    if "scalar" in node:
        return refs({"args": node["scalar"].get("where", [])}) - {node["scalar"]["as"]}
    out = set()
    for key in ("left", "right", "arg"):
        if key in node:
            out |= refs(node[key])
    for p in node.get("args", []):
        out |= refs(p)
    if "query" in node:
        out |= refs({"args": node["query"].get("where", [])}) - {node["query"]["as"]}
    return out


def classify(sub, outer):
    """Split WHERE into inner-only predicates and correlation keys.

    Returns ``(local, equalities, ranges)`` or ``None`` when the subquery cannot
    be safely rewritten.  ``equalities`` holds ``(inner, outer)`` pairs and
    ``ranges`` holds ``(inner, outer, op)`` triples, both meaning
    ``inner <op> outer``.
    """
    local, equalities, ranges = [], [], []
    for p in sub.get("where", []):
        if not refs(p) & outer:
            local.append(p)
            continue
        op = p.get("op")
        if op not in COMPARISONS or "left" not in p or "right" not in p:
            # A correlated OR / IS NULL / EXISTS cannot become a join key.
            return None
        left, right = p["left"], p["right"]
        left_outer, right_outer = bool(refs(left) & outer), bool(refs(right) & outer)
        if left_outer and not right_outer and refs(left) <= outer:
            # outer op inner  ==  inner FLIP[op] outer
            inner, outer_operand, inner_op = right, left, FLIP[op]
        elif right_outer and not left_outer and refs(right) <= outer:
            inner, outer_operand, inner_op = left, right, op
        else:
            # Both sides correlated, or an enclosing alias mixed into the inner
            # side: the group key cannot be formed from the outer row alone.
            return None
        if not refs(inner) <= {sub["as"]}:
            # The inner side is not computable from the inner row alone.
            return None
        if inner_op == "=":
            equalities.append((inner, outer_operand))
        else:
            ranges.append((inner, outer_operand, inner_op))
    return local, equalities, ranges


# An aggregate is a tuple (rows, seen, total, present, low, high):
#   rows    rows in the group (COUNT(*)),
#   seen    non-NULL values (COUNT(column)),
#   total   SUM of the non-NULL values,
#   present 1 when the group has at least one non-NULL value,
#   low/high MIN/MAX of the non-NULL values.
EMPTY = (0, 0, 0, 0, None, None)


def _merge(acc, other):
    if acc == EMPTY:
        return other
    if other == EMPTY:
        return acc
    low = acc[4] if other[4] is None else (other[4] if acc[4] is None else min(acc[4], other[4]))
    high = acc[5] if other[5] is None else (other[5] if acc[5] is None else max(acc[5], other[5]))
    return (acc[0] + other[0], acc[1] + other[1], acc[2] + other[2],
            acc[3] + other[3], low, high)


class _Group:
    """Rows sharing one correlation key and one distinct range value.

    The aggregate is folded on first use, never while the bucket is built, so a
    group no outer row joins contributes exactly what it would row by row --
    including raising the same error, or none.
    """

    __slots__ = ("rows", "seen", "first", "values")

    def __init__(self):
        self.rows = 0
        self.seen = 0
        self.first = _UNSET
        self.values = []

    def aggregate(self, kind):
        if kind == "count":
            return (self.rows, self.seen, 0, 0, None, None)
        if kind == "sum":
            return (self.rows, self.seen, sum(self.values), 1 if self.values else 0, None, None)
        if kind == "min":
            return (self.rows, self.seen, 0, 1 if self.values else 0,
                    min(self.values) if self.values else None, None)
        return (self.rows, self.seen, 0, 1 if self.values else 0, None,
                max(self.values) if self.values else None)


class _Bucket:
    """Groups of one equality key, indexed by the range operands.

    ``prefix[i]`` merges the first ``i`` groups sorted by the range operand and
    ``suffix[i]`` merges the groups from ``i`` onward, so a single inequality
    correlation resolves with two binary searches and one merge.
    """

    __slots__ = ("groups", "column", "order", "prefix", "suffix", "kind", "op", "folded")

    def __init__(self, kind):
        self.groups = {}
        self.column = self.order = self.prefix = self.suffix = None
        self.kind = kind
        self.op = "="
        self.folded = None

    def add(self, group, bounds):
        self.groups[bounds] = group

    def finish(self):
        """Order the range values for lookups; aggregates stay deferred."""
        self.order = None
        try:
            self.order = sorted(self.groups, key=lambda bounds: bounds[0])
        except TypeError:
            # Incomparable range values (mixed types): keep a plain scan, which
            # reproduces any row-by-row comparison error where it occurs.
            return
        self.column = [bounds[0] for bounds in self.order]

    def _folds(self):
        """Whether the prefix/suffix folds are usable (built once)."""
        if self.folded is None:
            self.folded = False
            if self.order is None:
                return False
            try:
                folded = [self.groups[bounds].aggregate(self.kind) for bounds in self.order]
                prefix = [EMPTY]
                for agg in folded:
                    prefix.append(_merge(prefix[-1], agg))
                suffix = [EMPTY]
                for agg in reversed(folded):
                    suffix.append(_merge(suffix[-1], agg))
                suffix.reverse()
            except TypeError:
                # Some matching group cannot be aggregated: scan so the error
                # surfaces for exactly the groups row-by-row would fold.
                return False
            self.prefix, self.suffix = prefix, suffix
            self.folded = True
        return self.folded

    def intervals(self, outer):
        """Index ranges of groups whose range value matches ``outer``."""
        if self.order is None:
            return None
        op, value, column = self.op, outer[0], self.column
        try:
            if op == "<":
                return ((0, bisect.bisect_left(column, value)),)
            if op == "<=":
                return ((0, bisect.bisect_right(column, value)),)
            if op == ">":
                return ((bisect.bisect_right(column, value), len(column)),)
            if op == ">=":
                return ((bisect.bisect_left(column, value), len(column)),)
            lo, hi = bisect.bisect_left(column, value), bisect.bisect_right(column, value)
        except TypeError:
            return None
        return ((0, lo), (hi, len(column)))


class Decorrelated:
    def __init__(self, sub, ctx, equalities, ranges, local, exists=False):
        self.sub, self.ctx = sub, ctx
        self.equalities, self.ranges, self.local = equalities, ranges, local
        self.exists = exists
        self.buckets = None
        self.kind = "count"

    @classmethod
    def build(cls, sub, ctx, outer, exists=False):
        classified = classify(sub, outer)
        if classified is None:
            return None
        local, equalities, ranges = classified
        return cls(sub, ctx, equalities, ranges, local, exists)

    def _add(self, group, env):
        sub, alias = self.sub, self.sub["as"]
        agg, col = sub.get("agg"), sub.get("col")
        group.rows += 1
        value = env[alias].get(col.split(".", 1)[1]) if col else None
        if agg is None:
            if group.first is _UNSET:
                group.first = value
        elif agg == "count":
            if col is None or value is not None:
                group.seen += 1
        elif value is not None:
            group.seen += 1
            group.values.append(value)

    def prepare(self):
        """Scan the inner table once; deferred until a value is actually needed."""
        if self.buckets is not None:
            return
        sub, ctx, alias = self.sub, self.ctx, self.sub["as"]
        agg = self.sub.get("agg")
        self.kind = "count" if agg in (None, "count") else agg
        buckets = {}
        for row in ctx.db[sub["from"]]["rows"]:
            env = {alias: row}
            if and3(ctx.truth(p, env) for p in self.local) is not True:
                continue
            key = tuple(ctx.operand(inner, env) for inner, _ in self.equalities)
            bounds = tuple(ctx.operand(inner, env) for inner, _, _ in self.ranges)
            # A NULL operand makes every comparison UNKNOWN for every outer row,
            # so this inner row can never join.
            if any(v is None for v in key) or any(v is None for v in bounds):
                continue
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = _Bucket(self.kind)
            group = bucket.groups.get(bounds)
            if group is None:
                group = _Group()
                bucket.add(group, bounds)
            self._add(group, env)
        if self.ranges:
            for bucket in buckets.values():
                bucket.op = self.ranges[0][2]
                bucket.finish()
        self.buckets = buckets

    def _scan(self, bucket, outer):
        """Aggregate the groups of ``bucket`` that match ``outer``."""
        acc = EMPTY
        for bounds, group in bucket.groups.items():
            if all(OPS[op](bound, value) for bound, (_, _, op), value
                   in zip(bounds, self.ranges, outer)):
                acc = _merge(acc, group.aggregate(self.kind))
        return acc

    def _reduce(self, bucket, outer):
        """Aggregate every group in ``bucket`` that matches ``outer``."""
        if not self.ranges:
            # One group per equality key: nothing to filter.
            return next(iter(bucket.groups.values())).aggregate(self.kind)
        if len(self.ranges) == 1 and bucket._folds():
            intervals = bucket.intervals(outer)
            if intervals is not None:
                acc = EMPTY
                for lo, hi in intervals:
                    if lo < hi:
                        acc = _merge(acc, bucket.prefix[hi] if lo == 0 else bucket.suffix[lo])
                return acc
        return self._scan(bucket, outer)

    def _found(self, env):
        """One aggregate per contributing bucket, or ``None`` when no join."""
        self.prepare()
        key = tuple(self.ctx.operand(outer, env) for _, outer in self.equalities)
        outer_bounds = tuple(self.ctx.operand(outer, env) for _, outer, _ in self.ranges)
        if any(v is None for v in key) or any(v is None for v in outer_bounds):
            return None
        if self.equalities:
            bucket = self.buckets.get(key)
            return None if bucket is None else [self._reduce(bucket, outer_bounds)]
        return [self._reduce(bucket, outer_bounds) for bucket in self.buckets.values()]

    def value(self, env):
        if self.exists:
            found = self._found(env)
            return bool(found) and any(part[0] for part in found)
        if self.sub.get("agg") is None:
            return self._single(env)
        total = EMPTY
        for part in self._found(env) or ():
            total = _merge(total, part)
        if self.sub.get("agg") == "count":
            return total[0] if self.sub.get("col") is None else total[1]
        if not total[3]:
            return None
        return {"sum": total[2], "min": total[4], "max": total[5]}[self.sub["agg"]]

    def _matching_groups(self, env):
        self.prepare()
        key = tuple(self.ctx.operand(outer, env) for _, outer in self.equalities)
        outer_bounds = tuple(self.ctx.operand(outer, env) for _, outer, _ in self.ranges)
        if any(v is None for v in key) or any(v is None for v in outer_bounds):
            return []
        if self.equalities:
            candidates = [self.buckets[key]] if key in self.buckets else []
        else:
            candidates = list(self.buckets.values())
        found = []
        for bucket in candidates:
            for bounds, group in bucket.groups.items():
                if all(OPS[op](bound, value) for bound, (_, _, op), value
                       in zip(bounds, self.ranges, outer_bounds)):
                    found.append(group)
        return found

    def _single(self, env):
        """A plain-column scalar subquery: one row, NULL, or the runtime error."""
        found = self._matching_groups(env)
        rows = sum(group.rows for group in found)
        if rows > 1:
            raise MultipleRows("more than one row returned by a subquery used as an expression")
        return found[0].first if found else None





