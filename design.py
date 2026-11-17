"""3NF synthesis, BCNF analysis, DDL and data migration SQL."""
from itertools import combinations

from fd import candidate_keys, closure, merged


def ordered(attrs, order):
    return [a for a in order if a in set(attrs)]


def synthesize(order, cover, keys):
    rels = [(ordered(set(lhs) | set(rhs), order), list(lhs)) for lhs, rhs in merged(cover)]
    rels = [r for i, r in enumerate(rels)
            if not any(set(r[0]) < set(o[0]) or (set(r[0]) == set(o[0]) and j < i) for j, o in enumerate(rels))]
    return rels


def violation(attrs, fds):
    for size in range(1, len(attrs)):
        for x in combinations(attrs, size):
            plus = closure(x, fds) & set(attrs)
            if plus > set(x) and plus != set(attrs):
                return list(x), plus
    return None


def bcnf(rels, fds, order):
    out, todo = [], [attrs for attrs, _ in rels]
    while todo:
        attrs = todo.pop(0)
        v = violation(attrs, fds)
        if v is None:
            out.append(attrs)
            continue
        x, plus = v
        todo[:0] = [ordered(plus, order), ordered(set(x) | (set(attrs) - plus), order)]
    return out


def preserved(fd, rels, fds):
    lhs, rhs = fd
    return any(set(lhs) | set(rhs) <= set(attrs) for attrs in rels)


def analyze(spec, cover):
    order = spec["attributes"]
    keys = candidate_keys(order, cover)
    rels = synthesize(order, cover, keys)
    split = bcnf(rels, cover, order)
    names = [f"{spec['table']}_{i + 1}" for i in range(len(rels))]
    return {"minimal_cover": [{"lhs": list(l), "rhs": list(r)} for l, r in cover],
            "candidate_keys": keys,
            "relations": [{"name": n, "attributes": a, "key": k} for n, (a, k) in zip(names, rels)],
            "is_bcnf": all(violation(a, cover) is None for a, _ in rels),
            "bcnf_relations": split,
            "lost_dependencies": [{"lhs": list(l), "rhs": list(r)} for l, r in cover if not preserved((l, r), split, cover)]}


def violations(spec, rows):
    found = []
    for fd in spec["fds"]:
        seen = {}
        for row in rows:
            left = tuple(row[a] for a in fd["lhs"])
            right = row[fd["rhs"][0]]
            if seen.setdefault(left, right) != right:
                found.append({"lhs": fd["lhs"], "rhs": fd["rhs"], "values": list(left)})
                break
    return found


def sql(spec, report):
    types = spec.get("types", {})
    ddl, dml = [], []
    for rel in report["relations"]:
        cols = ", ".join(f"{a} {types.get(a, 'TEXT')}" for a in rel["attributes"])
        ddl.append(f"CREATE TABLE {rel['name']} ({cols}, PRIMARY KEY ({', '.join(rel['key'])}));")
        names = ", ".join(rel["attributes"])
        dml.append(f"INSERT INTO {rel['name']} ({names}) SELECT DISTINCT {names} FROM {spec['table']};")
    return "\n".join(ddl) + "\n", "\n".join(dml) + "\n"
