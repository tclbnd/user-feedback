"""Deterministic synthetic dataset for the dynamic acceptance test."""

DDL = [
    "CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, "
    "region TEXT, segment TEXT, created_at TEXT)",
    "CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, "
    "status TEXT, amount INTEGER, ordered_at TEXT)",
    "CREATE TABLE order_items(order_id INTEGER, product_id INTEGER, "
    "qty INTEGER, unit_price INTEGER, discount INTEGER)",
    "CREATE TABLE products(id INTEGER PRIMARY KEY, sku TEXT, name TEXT, "
    "category TEXT, price INTEGER, active INTEGER)",
    "CREATE TABLE shipments(id INTEGER PRIMARY KEY, order_id INTEGER, "
    "shipped_at TEXT, carrier TEXT, status TEXT)",
    "CREATE TABLE events(id INTEGER PRIMARY KEY, customer_id INTEGER, "
    "event_type TEXT, payload TEXT, ts TEXT)",
]

TEXT_COLS = dict(
    customers=["name", "region", "segment", "created_at"],
    orders=["status", "ordered_at"],
    order_items=[],
    products=["sku", "name", "category"],
    shipments=["shipped_at", "carrier", "status"],
    events=["event_type", "payload", "ts"],
)

INT_COLS = dict(
    customers=["id"],
    orders=["id", "customer_id", "amount"],
    order_items=["order_id", "product_id", "qty", "unit_price", "discount"],
    products=["id", "price", "active"],
    shipments=["id", "order_id"],
    events=["id", "customer_id"],
)


def build(conn):
    for ddl in DDL:
        conn.execute(ddl)
    regions = ["EU", "US", "APAC"]
    segments = ["retail", "enterprise"]
    for i in range(1, 13):
        conn.execute("INSERT INTO customers VALUES (?,?,?,?,?)",
                     (i, "Cust%02d" % i, regions[i % 3],
                      segments[i % 2], "2024-01-%02d" % i))
    statuses = ["paid", "shipped", "cancelled", "pending"]
    for i in range(1, 31):
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?)",
                     (i, (i - 1) % 12 + 1, statuses[i % 4],
                      (i * 37) % 900 + 20, "2024-02-%02d" % (i % 28 + 1)))
    for i in range(1, 41):
        conn.execute("INSERT INTO order_items VALUES (?,?,?,?,?)",
                     ((i - 1) % 30 + 1, (i - 1) % 10 + 1, i % 10 + 1,
                      (i * 13) % 50 + 5, (i % 3) * 5))
    cats = ["books", "retail", "electronics", "grocery"]
    for i in range(1, 11):
        conn.execute("INSERT INTO products VALUES (?,?,?,?,?,?)",
                     (i, "SKU%02d" % i, "Prod%02d" % i, cats[i % 4],
                      (i * 29) % 200 + 10, i % 2))
    carriers = ["DHL", "UPS", "FedEx"]
    for i in range(1, 16):
        conn.execute("INSERT INTO shipments VALUES (?,?,?,?,?)",
                     (i, (i * 2 - 1) % 30 + 1, "2024-03-%02d" % i,
                      carriers[i % 3], ["shipped", "delivered"][i % 2]))
    for i in range(1, 6):
        conn.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                     (i, i, "click", "payload%d" % i, "2024-01-0%d" % i))
    conn.commit()


def column_mutation_sql(table, col):
    if col in TEXT_COLS[table]:
        return "UPDATE %s SET %s = %s || '_M' WHERE rowid %% 2 = 0" % (
            table, col, col)
    if col == "id":
        return "UPDATE %s SET %s = %s + 1000 WHERE rowid %% 2 = 0" % (
            table, col, col)
    return "UPDATE %s SET %s = %s + 1 WHERE rowid %% 2 = 0" % (
        table, col, col)


ROW_MUTATIONS = [
    ("orders", "INSERT INTO orders VALUES (9000, 3, 'paid', 750,"
               " '2024-02-15')"),
    ("customers", "INSERT INTO customers VALUES (9000, 'New Cust', 'EU',"
                  " 'retail', '2024-01-15')"),
    ("products", "DELETE FROM products WHERE id IN (3, 4)"),
    ("shipments", "DELETE FROM shipments WHERE id IN (1, 2)"),
    ("events", "INSERT INTO events VALUES (9000, 1, 'click', 'x',"
               " '2024-01-01')"),
]

# pre-designed irrelevant mutations: no query may be affected by these
IRRELEVANT_COLS = [
    ("events", "id"), ("events", "customer_id"), ("events", "event_type"),
    ("events", "payload"), ("events", "ts"), ("shipments", "shipped_at"),
]
IRRELEVANT_ROWS = ["events"]
