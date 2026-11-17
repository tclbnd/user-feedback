"""3NF synthesis, BCNF analysis, DDL and data migration SQL."""
from itertools import combinations

from fd import candidate_keys, closure, merged


def ordered(attrs, order):
    keep = set(attrs)
    return [a for a in order if a in keep]


def synthesize(order, cover, keys):
    """Dependency-preserving 3NF synthesis.

    One relation per left side of the minimal cover; a relation whose
    attributes are contained in another is dropped; if no remaining relation
    contains a candidate key, one built from the first candidate key is added.
    """
    rels = [(ordered(set(lhs) | set(rhs), order), list(lhs))
            for lhs, rhs in merged(cover)]

    # drop relations strictly contained in another; on ties keep the first
    kept = []
    for i, (attrs, key) in enumerate(rels):
        if any((set(attrs) < set(o[0]))
               or (set(attrs) == set(o[0]) and j < i)
               for j, (o, _) in enumerate(rels)):
            continue
        kept.append((attrs, key))
    rels = kept

    key_sets = [set(k) for k in keys]
    if not any(set(k) <= set(attrs) for attrs, _ in rels for k in key_sets):
        rels.append((ordered(keys[0], order), list(keys[0])))
    return rels


def violation(attrs, fds):
    """First BCNF violator X inside ``attrs``: smallest X, then schema order.

    Returns (X, X+ intersect attrs) where X determines other attributes of
    the relation without determining all of them; None if the relation is in
    BCNF. Dependencies are projected from the full closure, so any subset X
    (not only declared left sides) is examined.
    """
    attr_set = set(attrs)
    for size in range(1, len(attrs)):
        for x in combinations(attrs, size):
            plus = closure(x, fds) & attr_set
            if plus > set(x) and plus != attr_set:
                return list(x), ordered(plus, attrs)
    return None


def bcnf(rels, fds, order):
    """Recursively split every non-BCNF 3NF relation on its first violator."""
    out = []

    def split(attrs):
        v = violation(attrs, fds)
        if v is None:
            out.append(attrs)
            return
        x, plus = v
        r1 = ordered(plus, order)                        # X+ intersect R
        r2 = ordered(set(x) | (set(attrs) - set(plus)), order)
        split(r1)
        split(r2)

    for attrs, _ in rels:
        split(attrs)
    return out


def preserved(fd, rels, fds):
    """Standard dependency-preservation test.

    An FD X -> A is preserved by the decomposition iff A belongs to the
    closure of X computed using only dependencies whose two sides lie inside
    a single decomposed relation (iterated over all relations).
    """
    lhs, rhs = fd
    result = set(lhs)
    projected = [set(r) for r in rels]
    changed = True
    while changed:
        changed = False
        for r in projected:
            part = closure(result & r, fds) & r
            grown = result | part
            if grown != result:
                result, changed = grown, True
    return set(rhs) <= result


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
    """Check every declared FD with all of its right-hand attributes.

    Two rows agreeing on the left side must agree on every right-side value.
    One entry per violated dependency, first offending group (first row that
    breaks a previously seen group).
    """
    found = []
    for fd in spec["fds"]:
        seen = {}
        for row in rows:
            left = tuple(row[a] for a in fd["lhs"])
            right = tuple(row[a] for a in fd["rhs"])
            if left not in seen:
                seen[left] = right
            elif seen[left] != right:
                found.append({"lhs": list(fd["lhs"]),
                              "rhs": list(fd["rhs"]),
                              "values": list(left)})
                break
    return found


def sql(spec, report):
    types = spec.get("types", {})
    ddl, dml = [], []
    for rel in report["relations"]:
        cols = ", ".join(f"{a} {types.get(a, 'TEXT')}" for a in rel["attributes"])
        ddl.append(
            f"CREATE TABLE {rel['name']} ({cols}, "
            f"PRIMARY KEY ({', '.join(rel['key'])}));")
        names = ", ".join(rel["attributes"])
        dml.append(
            f"INSERT INTO {rel['name']} ({names}) "
            f"SELECT DISTINCT {names} FROM {spec['table']};")
    return "\n".join(ddl) + "\n", "\n".join(dml) + "\n"
