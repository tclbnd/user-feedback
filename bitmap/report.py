"""Sales report queries that end up as bitmap scans."""

REPORTS = {
    "open_eu": {"and": [{"col": "region", "op": "=", "value": "eu"}, {"col": "status", "op": "=", "value": "open"}]},
    "eu_or_big": {"or": [{"col": "region", "op": "=", "value": "eu"}, {"col": "amount", "op": ">", "value": 900}]},
    "unpriced": {"col": "amount", "op": "is_null"},
    "not_refund": {"col": "status", "op": "!=", "value": "refund"},
}


def describe(name):
    cond = REPORTS[name]

    def show(c):
        if "and" in c:
            return "(" + " AND ".join(show(x) for x in c["and"]) + ")"
        if "or" in c:
            return "(" + " OR ".join(show(x) for x in c["or"]) + ")"
        if c["op"] == "is_null":
            return f"{c['col']} IS NULL"
        return f"{c['col']} {c['op']} {c['value']!r}"
    return show(cond)
