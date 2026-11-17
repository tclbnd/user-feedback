"""Run a plan over the table and its indexes."""
import operator

OPS = {"=": operator.eq, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def matches(row, conds):
    return all(OPS[op](row[col], val) for col, op, val in conds)


def in_range(entry, plan):
    if any(entry[c] != v for c, v in zip(plan["eq"], plan["_eqv"])):
        return False
    if plan["range"]:
        v = entry[plan["range"]]
        if plan["lo"] and not (v > plan["lo"][0] or (plan["lo"][1] and v == plan["lo"][0])):
            return False
        if plan["hi"] and not (v < plan["hi"][0] or (plan["hi"][1] and v == plan["hi"][0])):
            return False
    return True


def execute(db, query, plan):
    rows = {r["id"]: r for r in db["rows"]}
    out, scanned, fetches = [], 0, 0
    if plan["index"] is None:
        for r in sorted(rows.values(), key=lambda r: r["id"]):
            scanned += 1
            if matches(r, plan["table_filter"]):
                out.append(r)
    else:
        index = next(i for i in db["indexes"] if i["name"] == plan["index"])
        cols = index["columns"]
        where = query.get("where", [])
        plan["_eqv"] = [next(v for c, op, v in where if c == col and op == "=") for col in plan["eq"]]
        entries = sorted(({c: r[c] for c in cols + ["id"]} for r in rows.values()),
                         key=lambda e: [e[c] for c in cols] + [e["id"]],
                         reverse=plan["direction"] == "desc")
        inside = [e for e in entries if in_range(e, plan)]
        scanned = len(inside)
        for e in inside:
            if not matches(e, plan["index_filter"]):
                continue
            fetches += 1
            r = rows[e["id"]]
            if matches(r, plan["table_filter"]):
                out.append(r)
        del plan["_eqv"]
    if plan["sort"]:
        for col, d in reversed(query.get("order_by", [])):
            out.sort(key=lambda r: r[col], reverse=d == "desc")
    sel = query.get("select") or sorted(out[0]) if out else []
    return [{c: r[c] for c in sel} for r in out], scanned, fetches
