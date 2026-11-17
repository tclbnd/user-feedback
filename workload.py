"""Slow query log parsing, fingerprints and predicate extraction."""
import re

LITERAL = r"(?:'[^']*'|-?\d+(?:\.\d+)?)"
COLUMN = r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)"


def fingerprint(sql):
    sql = re.sub(r"'[^']*'", "?", sql.strip().rstrip(";"))
    sql = re.sub(r"-?\d+(?:\.\d+)?", "?", sql)
    return re.sub(r"\s+", " ", sql)


def read_log(path):
    """Statements of a slow query log grouped by fingerprint, in order of first appearance."""
    groups, text = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            text.append(line.strip())
            if line.rstrip().endswith(";"):
                for sql in split_statements(" ".join(text)):
                    fp = fingerprint(sql)
                    groups.setdefault(fp, {"fingerprint": fp, "sql": sql, "count": 0})["count"] += 1
                text = []
    return list(groups.values())


def split_statements(text):
    return [s.strip() for s in text.split(";") if s.strip()]


def parse(sql):
    """{"kind", "table", "preds": [(table, col, "eq"|"range")], "order": [cols], "join", "set"}."""
    s = sql.strip().rstrip(";")
    kind = s.split()[0].lower()
    table = re.search(r"(?:FROM|INTO|UPDATE)\s+(\w+)", s, re.I).group(1)
    q = {"kind": kind, "table": table, "preds": [], "order": [], "join": None, "set": []}
    join = re.search(r"JOIN\s+(\w+)\s+ON\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)", s, re.I)
    if join:
        inner = join.group(1)
        a, b = (join.group(2), join.group(3)), (join.group(4), join.group(5))
        q["join"] = {"table": inner, "column": (a if a[0] == inner else b)[1]}
    where = re.search(r"WHERE\s+(.*?)(?:\s+ORDER\s+BY|\s+LIMIT|$)", s, re.I)
    for cond in re.split(r"\s+AND\s+", where.group(1), flags=re.I) if where else []:
        m = re.match(COLUMN + r"\s*(=|<=|>=|<|>|BETWEEN)", cond.strip(), re.I)
        if m:
            t, _, c = m.group(1).rpartition(".")
            q["preds"].append((t or table, c, "eq" if m.group(2) == "=" else "range"))
    order = re.search(r"ORDER\s+BY\s+(.*?)(?:\s+LIMIT|$)", s, re.I)
    if order:
        q["order"] = [part.split()[0].split(".")[-1] for part in order.group(1).split(",")]
    if kind == "update":
        q["set"] = re.findall(r"(\w+)\s*=", re.search(r"SET\s+(.*?)\s+WHERE", s, re.I).group(1))
    return q
