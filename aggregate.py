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


def specs(q):
    out = {a["as"]: a for a in q["aggregates"]}
    for left, _, _ in q.get("having", []):
        if isinstance(left, dict):
            out[_name(left)] = left
    return out


def _name(spec):
    return f"{spec['fn']}({'distinct ' if spec.get('distinct') else ''}{spec['arg']})"


def aggregate_part(rows, gset, aggs):
    groups = {}
    for row in rows:
        key = key_of(row, gset)
        if key not in groups:
            groups[key] = {n: Agg(s) for n, s in aggs.items()}
        for a in groups[key].values():
            a.add(row)
    if not gset and not groups:
        groups[()] = {n: Agg(s) for n, s in aggs.items()}
    return {k: {n: a.result() for n, a in v.items()} for k, v in groups.items()}


def run(q):
    rows, sets, aggs = q.get("rows", []), grouping_sets(q), specs(q)
    columns = []
    for s in sets:
        columns += [c for c in s if c not in columns]
    limit, spilled, out = q.get("memory_groups", 1000), 0, []
    for gset in sets:
        parts = 1
        keys = {key_of(r, gset) for r in rows}
        while parts * limit < len(keys):
            parts *= 2
        if parts > 1:
            spilled += parts
        result = {}
        for p in range(parts):
            result.update(aggregate_part([r for r in rows if partition(sorted(r.items()), parts) == p], gset, aggs))
        mask = sum(1 << i for i, c in enumerate(columns) if c not in gset)
        for key, vals in result.items():
            scope = dict(zip(gset, key))
            ok = all((scope.get(left) if isinstance(left, str) else vals[_name(left)]) is not None
                     and OPS[op](scope.get(left) if isinstance(left, str) else vals[_name(left)], val)
                     for left, op, val in q.get("having", []))
            if ok:
                row = {c: None for c in columns}
                row.update(zip(gset, map(plain, key)))
                row.update({a["as"]: vals[a["as"]] for a in q["aggregates"]})
                row["grouping"] = mask
                out.append(row)
    out.sort(key=lambda r: (r["grouping"], [sort_key(r[c]) for c in columns]))
    return {"rows": out, "spilled_partitions": spilled}
