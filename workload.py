"""Replay a workload of DDL, data growth and prepared-statement executions.

    python3 workload.py workload.json result.json

workload.json:

    {"tables": {"orders": 50, "customers": 20},
     "statements": {"q1": {"outer": "orders", "inner": "customers", "join_col": "id", "filter_col": "status"}},
     "steps": [{"op": "execute", "name": "q1", "repeat": 3},
               {"op": "insert", "table": "orders", "rows": 5000},
               {"op": "analyze", "table": "orders"},
               {"op": "create_index", "index": "cust_pk", "table": "customers", "column": "id"},
               {"op": "drop_index", "index": "cust_pk"},
               {"op": "reload", "table": "customers", "rows": 25000}]}

``reload`` is the nightly swap reload of a dimension (see plancache/reload.py);
``drop_table`` and ``create_table`` (table, rows) are also available.
"""
import json
import sys

from plancache.catalog import Catalog, CatalogError
from plancache.prepared import Server
from plancache.reload import indexes_of, swap_reload
from plancache.stats import statement_view
from plancache.slowlog import SlowLog


def run(spec):
    catalog = Catalog()
    for name, rows in spec["tables"].items():
        catalog.create_table(name, rows)
    for name, (table, column) in spec.get("indexes", {}).items():
        catalog.create_index(name, table, column)
    server = Server(catalog)
    for name, query in spec["statements"].items():
        server.prepare(name, query)
    slow, executions = SlowLog(), []
    for i, step in enumerate(spec["steps"]):
        op = step["op"]
        if op == "execute":
            for _ in range(step.get("repeat", 1)):
                try:
                    result = server.execute(step["name"])
                except CatalogError as exc:
                    result = {"plan": None, "cost": None, "error": str(exc)}
                else:
                    slow.record(step["name"], result, i)
                executions.append(dict(result, step=i))
        elif op == "insert":
            catalog.insert_rows(step["table"], step["rows"])
        elif op == "analyze":
            catalog.analyze(step["table"])
        elif op == "create_index":
            catalog.create_index(step["index"], step["table"], step["column"])
        elif op == "drop_index":
            catalog.drop_index(step["index"])
        elif op == "reload":
            swap_reload(catalog, step["table"], step["rows"], indexes_of(catalog, step["table"]))
        elif op == "drop_table":
            catalog.drop_table(step["table"])
        elif op == "create_table":
            catalog.create_table(step["table"], step.get("rows", 0))
        else:
            raise ValueError(f"unknown op {op}")
    return {"executions": executions, "plannings": server.plannings, "slow": slow.worst(),
            "statements": statement_view(server)}


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(spec), f, indent=1)


if __name__ == "__main__":
    main()
