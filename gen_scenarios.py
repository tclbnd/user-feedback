#!/usr/bin/env python3
"""Generate the three acceptance slow-query logs under examples/scenarios/.

read_heavy : many SELECTs, almost no writes -> multiple new indexes recommended.
write_heavy: mass INSERT/UPDATE, a handful of reads -> write amplification
             rejects the read-only candidates; only redundant/unused indexes show.
mixed      : sizable reads and writes -> some indexes win, some lose to writes.
"""
import os

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "scenarios")


def emit(path, statements):
    with open(path, "w", encoding="utf-8") as f:
        for i, sql in enumerate(statements):
            f.write(f"# Time: 2026-11-10T08:{i % 60:02d}:00\n")
            f.write("# Query_time: 1.0  Rows_examined: 1000\n")
            f.write(sql + "\n")


def read_heavy():
    stmts = []
    for i in range(400):  # equality + range + sort on page_views
        stmts.append(
            f"SELECT id FROM page_views WHERE user_id = {i % 50000} AND tenant_id = {i % 20} "
            f"AND viewed_at > 1000 ORDER BY viewed_at;")
    for i in range(300):  # high-ndv equality: (path) index turns a full scan into a pinpoint lookup
        stmts.append(f"SELECT id FROM page_views WHERE path = '/p/{i % 300}';")
    for i in range(200):  # range-only query
        stmts.append(f"SELECT id FROM page_views WHERE viewed_at BETWEEN {i} AND {i + 100};")
    for i in range(150):  # join: inner lookup on sessions.user_id
        stmts.append(
            "SELECT sessions.device, page_views.id FROM page_views "
            "JOIN sessions ON sessions.user_id = page_views.user_id "
            "WHERE page_views.tenant_id = 3;")
    for i in range(5):  # token writes
        stmts.append(f"INSERT INTO page_views (id, user_id, tenant_id, viewed_at, path) "
                     f"VALUES ({i}, {i}, 1, 2000, '/home');")
    emit(os.path.join(HERE, "read_heavy.log"), stmts)


def write_heavy():
    stmts = []
    # one physical line holds many statements to keep the log file small;
    # workload.read_log splits statements on ';' regardless of line layout.
    batch = []
    for i in range(700000):
        batch.append(f"INSERT INTO ledger (id, acct_id, posted_at, amount, a, b, c) "
                     f"VALUES ({i}, {i % 100000}, {i}, {i % 50000}, {i % 1000}, {i % 500}, {i % 50});")
        if len(batch) == 100:
            stmts.append(" ".join(batch))
            batch = []
    for i in range(5000):
        stmts.append(f"UPDATE ledger SET amount = {i} WHERE id = {i};")
    for i in range(2000):  # delete by primary key: no new index can help, still pays maintenance
        stmts.append(f"DELETE FROM ledger WHERE id = {i};")
    for i in range(1):  # a single rare read over a million-row table
        stmts.append("SELECT id FROM ledger WHERE acct_id = 7 AND posted_at BETWEEN 1 AND 100;")
    for i in range(2):  # read on (a, b): the existing (a,b) index already covers it
        stmts.append(f"SELECT id FROM ledger WHERE a = {i} AND b = {i};")
    emit(os.path.join(HERE, "write_heavy.log"), stmts)


def mixed():
    stmts = []
    for i in range(200):  # read: equality + range + sort
        stmts.append(f"SELECT id, total FROM orders WHERE customer_id = {i % 20000} "
                     f"AND created_at > 1000 ORDER BY created_at;")
    for i in range(120):  # read: range-only
        stmts.append(f"SELECT id FROM orders WHERE created_at BETWEEN {i} AND {i + 50};")
    for i in range(150):  # read on events: (kind, at)
        stmts.append(f"SELECT id FROM events WHERE kind = {i % 40} AND at BETWEEN 1 AND 1000;")
    for i in range(3000):  # heavy inserts into events -> write amplification on (kind, at)
        stmts.append(f"INSERT INTO events (id, kind, at) VALUES ({i}, {i % 40}, {i});")
    for i in range(200):  # updates touch status -> status indexes cost maintenance
        stmts.append(f"UPDATE orders SET status = {i % 5} WHERE id = {i};")
    emit(os.path.join(HERE, "mixed.log"), stmts)


if __name__ == "__main__":
    read_heavy()
    write_heavy()
    mixed()
    print("generated:", sorted(os.listdir(HERE)))
