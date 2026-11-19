"""Run DML statements against one table.

    python3 runsql.py job.json result.json

job.json: {"table": {...definition...},
           "statements": [{"insert": [{...}, {...}]},
                          {"update": {"keys": [1, 2], "set": {"qty": {"add": [{"col": "qty"}, 1]}}}},
                          {"upsert": {"rows": [{...}], "set": {"unit_price": {"excluded": "unit_price"}}}}]}
"""
import json
import sys

from rowexec.dml import DmlError, Session
from rowexec.schema import Table


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    table = Table(job["table"])
    session = Session(table)
    results = []
    for stmt in job["statements"]:
        try:
            if "insert" in stmt:
                results.append({"ok": True, "rows": session.insert(stmt["insert"])})
            elif "upsert" in stmt:
                u = stmt["upsert"]
                results.append({"ok": True, "rows": session.upsert(u["rows"], u["set"])})
            else:
                u = stmt["update"]
                results.append({"ok": True, "rows": session.update(u["keys"], u["set"])})
        except DmlError as exc:
            results.append({"ok": False, "error": str(exc)})
    out = {"results": results, "table": [table.rows[k] for k in sorted(table.rows, key=str)], "audit": session.audit}
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
