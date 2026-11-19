"""Run statements as different sessions against a database with RLS.

    python3 runsec.py db.json statements.json result.json

statements.json: [{"as": {"role": "alice", "user": "alice", "tenant": "acme"},
                   "select": "invoices", "where": expr?}
                  | {"as": ..., "insert": "invoices", "row": {...}}
                  | {"as": ..., "update": "invoices", "where": expr, "set": {...}}
                  | {"as": ..., "delete": "invoices", "where": expr}, ...]
Each statement reports the visible row ids (select), the number of rows
affected, or the error.
"""
import json
import sys

from rlsx.catalog import Catalog
from rlsx.engine import Engine, PolicyViolation


def run(db, statements):
    eng = Engine(Catalog(db))
    out = []
    for st in statements:
        s = st["as"]
        try:
            if "select" in st:
                res = {"ids": sorted(r["id"] for r in eng.select(st["select"], s, st.get("where")))}
            elif "insert" in st:
                res = {"rows": eng.insert(st["insert"], st["row"], s)}
            elif "update" in st:
                res = {"rows": eng.update(st["update"], st.get("where"), st["set"], s)}
            else:
                res = {"rows": eng.delete(st["delete"], st.get("where"), s)}
        except PolicyViolation as exc:
            res = {"error": str(exc)}
        out.append(res)
    return out


def main():
    db = json.load(open(sys.argv[1], encoding="utf-8"))
    st = json.load(open(sys.argv[2], encoding="utf-8"))
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(run(db, st), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
