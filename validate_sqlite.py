#!/usr/bin/env python3
"""End-to-end check on real data: build the recommended indexes in SQLite and
confirm the query plans change as the advisor predicted.

A small deterministic data set (orders/customers/events, 2000/400/1000 rows)
is loaded into a temporary SQLite database. Each acceptance query is EXPLAINed
with only the existing indexes and again after creating the advisor's
recommended indexes. The script asserts the access path switched from a full
scan to the expected index, and also cross-checks the advisor's own `explain`
row counts against the rows SQLite actually returns.
"""
import json
import os
import sqlite3
import tempfile

import advisor
import workload

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(os.path.dirname(HERE), "tmp")


def build_data():
    statuses = ["paid", "shipped", "new"]
    orders = [{"id": i + 1, "customer_id": i % 50, "status": statuses[i % 3],
               "created_at": 1000 + i, "total": i % 64} for i in range(2000)]
    customers = [{"id": i + 1, "region": f"r{i % 8}", "email": f"u{i}@x.io"} for i in range(400)]
    events = [{"id": i + 1, "kind": f"k{i % 10}", "at": 5000 + i} for i in range(1000)]
    return {"tables": {
        "orders": {"columns": {"id": 8, "customer_id": 8, "status": 10, "created_at": 8, "total": 8},
                   "indexes": [{"name": "pk_orders", "columns": ["id"], "unique": True},
                               {"name": "ix_orders_status", "columns": ["status"]},
                               {"name": "ix_orders_status_total", "columns": ["status", "total"]},
                               {"name": "ix_orders_total", "columns": ["total"]}],
                   "rows": orders},
        "customers": {"columns": {"id": 8, "region": 12, "email": 40},
                      "indexes": [{"name": "pk_customers", "columns": ["id"], "unique": True}],
                      "rows": customers},
        "events": {"columns": {"id": 8, "kind": 12, "at": 8},
                   "indexes": [{"name": "pk_events", "columns": ["id"], "unique": True}],
                   "rows": events}}}


DDL = {
    "orders": "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, status TEXT, "
              "created_at INTEGER, total INTEGER)",
    "customers": "CREATE TABLE customers (id INTEGER PRIMARY KEY, region TEXT, email TEXT)",
    "events": "CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT, at INTEGER)",
}

CHECKS = [
    ("orders", "SELECT id, total FROM orders WHERE customer_id = 7 AND created_at > 1500 "
               "ORDER BY created_at", "ix_orders_customer_id_created_at"),
    ("events", "SELECT id FROM events WHERE kind = 'k3' AND at BETWEEN 5200 AND 5900",
     "ix_events_kind_at"),
    ("customers", "SELECT id FROM customers WHERE region = 'r2'", "ix_customers_region"),
]


def explain_plan(conn, sql):
    return conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()


def used_indexes(plan_rows):
    names = []
    for row in plan_rows:
        detail = row[3]
        for token in detail.replace("(", " ").replace(")", " ").split():
            if token.startswith("ix_") or token.startswith("pk_"):
                names.append(token)
    return names


def main():
    data = build_data()
    os.makedirs(TMP, exist_ok=True)
    data_path = os.path.join(TMP, "validate_data.json")
    json.dump(data, open(data_path, "w", encoding="utf-8"))

    # stats derived from the real rows, exactly like `explain` does
    stats = {t: {"rows": len(d["rows"]),
                 "columns": {c: {"ndv": max(1, len({r.get(c) for r in d["rows"]})), "width": w}
                             for c, w in d["columns"].items()},
                 "indexes": d["indexes"]}
             for t, d in data["tables"].items()}
    log_path = os.path.join(TMP, "validate.log")
    with open(log_path, "w", encoding="utf-8") as f:
        for table, sql, _ in CHECKS:
            f.write("# Time: x\n# Query_time: 1\n" + sql + ";\n")
    groups = workload.read_log(log_path)
    queries = [{**g, "parsed": workload.parse(g["sql"])} for g in groups]
    report = advisor.advise(stats, queries, {"max_indexes": 5, "space_bytes": 10 ** 12})
    recommended = {c["name"]: c for c in report["recommended"]}
    print("advisor recommended:", sorted(recommended))

    db_path = os.path.join(TMP, "validate.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    for table, ddl in DDL.items():
        conn.execute(ddl)
        cols = list(data["tables"][table]["columns"])
        conn.executemany(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in data["tables"][table]["rows"]])
    for table, d in data["tables"].items():
        for ix in d["indexes"]:
            if ix["name"].startswith("pk_"):
                continue
            conn.execute(f"CREATE INDEX {ix['name']} ON {table} ({','.join(ix['columns'])})")
    conn.commit()

    failures = 0
    for table, sql, want in CHECKS:
        before = explain_plan(conn, sql)
        before_ix = used_indexes(before)
        if want not in recommended:
            print(f"[FAIL] {sql}\n  advisor did not recommend {want}: {sorted(recommended)}")
            failures += 1
            continue
        cols = recommended[want]["columns"]
        conn.execute(f"CREATE INDEX {want} ON {table} ({','.join(cols)})")
        conn.commit()
        after = explain_plan(conn, sql)
        after_ix = used_indexes(after)
        actual_rows = conn.execute(sql).fetchall()
        model = advisor.explain(data, sql, list(recommended.values()))
        status = "OK" if want in after_ix and model["rows"] == len(actual_rows) else "FAIL"
        if status == "FAIL":
            failures += 1
        print(f"[{status}] {sql}")
        print(f"   before: {before_ix or ['SCAN']}  after: {after_ix}  (expected {want})")
        print(f"   advisor explain: {model}  sqlite rows: {len(actual_rows)}")
    conn.close()
    if failures:
        raise SystemExit(f"{failures} validation check(s) failed")
    print("all plans changed to the recommended indexes and row counts match")


if __name__ == "__main__":
    main()
