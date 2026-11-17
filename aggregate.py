"""Hash aggregation with grouping sets, HAVING and partitioned spilling."""
import operator

from values import Agg, key_of, partition, plain, sort_key

OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le,
       ">": operator.gt, ">=": operator.ge}


def grouping_sets(q):
    if "rollup" in q:
        cols = q["rollup"]
        return [cols[:i] for i in range(len(cols), -1, -1)]
    return q.get("grouping_sets") or [q.get("group_by", [])]


def agg_name(spec):
    """Canonical name of an aggregate expression, including GROUPING(...)."""
    if spec["fn"] == "grouping":
        return f"grouping({spec['arg']})"
    d = "distinct " if spec.get("distinct") else ""
    return f"{spec['fn']}({d}{spec['arg']})"


def plan(q):
    """Collect every aggregate expression (selected or only in HAVING).

    Returns specs keyed by canonical name and an alias -> name map.
    Equivalent specs share one running state.
    """
    specs, aliases = {}, {}
    for a in q["aggregates"]:
        name = agg_name(a)
        specs.setdefault(name, a)
        aliases[a["as"]] = name
    for left, _, _ in q.get("having", []):
        if isinstance(left, dict):
            specs.setdefault(agg_name(left), left)
    return specs, aliases


def _new_states(specs):
    return {name: Agg(s) for name, s in specs.items() if s["fn"] != "grouping"}


def _scan(rows, gset, specs):
    """One hash-aggregation pass: all aggregates of a group in one scan."""
    groups = {}
    for row in rows:
        key = key_of(row, gset)
        states = groups.get(key)
        if states is None:
            states = groups[key] = _new_states(specs)
        for state in states.values():
            state.add(row)
    return groups


def aggregate_gset(rows, gset, specs, limit):
    """Aggregate rows for one grouping set, spilling to hash partitions.

    Rows are assigned to partitions by *grouping key* hash; identical keys
    always land in the same partition, so per-partition results combine by
    simple union. The partition count doubles until every partition holds
    at most ``limit`` distinct groups.
    """
    parts = 1
    keys = {key_of(r, gset) for r in rows}
    while parts * limit < len(keys):
        parts *= 2
    if parts > 1:
        buckets = [[] for _ in range(parts)]
        for row in rows:
            buckets[partition(key_of(row, gset), parts)].append(row)
        spilled = parts
    else:
        buckets, spilled = [rows], 0

    groups = {}
    for bucket in buckets:
        groups.update(_scan(bucket, gset, specs))
    if not gset and not groups:  # empty grouping set: one row even on empty input
        groups[()] = _new_states(specs)
    return groups, spilled


def _finalize(groups, gset, specs):
    """Turn running states into values and fill GROUPING() expressions."""
    out = {}
    for key, states in groups.items():
        vals = {name: state.result() for name, state in states.items()}
        for name, s in specs.items():
            if s["fn"] == "grouping":
                vals[name] = 1 if s["arg"] not in gset else 0
        out[key] = vals
    return out


def _having_passes(left, op, val, scope, vals, aliases):
    if isinstance(left, dict):
        got = vals[agg_name(left)]
    elif left in aliases:
        got = vals[aliases[left]]
    else:
        got = scope.get(left)
    # A comparison with NULL on either side is not true (SQL three-valued logic).
    if got is None or val is None:
        return False
    return OPS[op](got, val)


def run(q):
    rows = q.get("rows", [])
    sets = grouping_sets(q)
    specs, aliases = plan(q)
    columns = []
    for gset in sets:
        columns += [c for c in gset if c not in columns]
    limit = q.get("memory_groups", 1000)

    spilled, out = 0, []
    for gset in sets:
        groups, nspill = aggregate_gset(rows, gset, specs, limit)
        spilled += nspill
        results = _finalize(groups, gset, specs)
        mask = sum(1 << i for i, c in enumerate(columns) if c not in gset)
        for key, vals in results.items():
            scope = dict(zip(gset, map(plain, key)))
            if not all(_having_passes(left, op, val, scope, vals, aliases)
                       for left, op, val in q.get("having", [])):
                continue
            row = {c: None for c in columns}
            row.update(zip(gset, map(plain, key)))
            row.update({a["as"]: vals[aliases[a["as"]]] for a in q["aggregates"]})
            row["grouping"] = mask
            out.append(row)

    out.sort(key=lambda r: (r["grouping"], [sort_key(r[c]) for c in columns]))
    return {"rows": out, "spilled_partitions": spilled}
