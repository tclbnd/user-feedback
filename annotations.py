"""Hand-annotated expected lineage for the 32 test queries.

Convention (must match analyzer semantics):
  value: base-table columns determining the output column's VALUES
  row:   base-table columns determining WHICH ROWS appear. This covers
         JOIN ON/USING, WHERE, GROUP BY, HAVING, DISTINCT, LIMIT+ORDER BY,
         row dependencies lifted from CTE / derived-table sources, and a
         rows-only "t.*" entry for every base table scanned (transitively)
         by the query: inserting/deleting rows of t may change output row
         membership even when no column of t is referenced.
  "t.*": dependency on every column of t. The rows-only form (table scan,
         COUNT(*)) matches row insert/delete only; the conservative form
         (unknown schema, unsupported syntax) matches any change on t.
"""


def O(v, r):
    return dict(value=sorted(v), row=sorted(r))


ANN = {}

ANN["q01"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"], ["customers.*"]),
    customer_name=O(["customers.name"], ["customers.*"]),
    region=O(["customers.region"], ["customers.*"]),
))

ANN["q02"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"], ["customers.*", "customers.region"]),
    name=O(["customers.name"], ["customers.*", "customers.region"]),
    segment=O(["customers.segment"], ["customers.*", "customers.region"]),
))

ANN["q03"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"],
         ["customers.*", "customers.id", "orders.*", "orders.customer_id"]),
    name=O(["customers.name"],
           ["customers.*", "customers.id", "orders.*", "orders.customer_id"]),
    amount=O(["orders.amount"],
             ["customers.*", "customers.id", "orders.*",
              "orders.customer_id"]),
))

ANN["q04"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"],
         ["customers.*", "customers.id", "customers.region", "orders.*",
          "orders.customer_id", "orders.status"]),
    amt=O(["orders.amount"],
          ["customers.*", "customers.id", "customers.region", "orders.*",
           "orders.customer_id", "orders.status"]),
))

ANN["q05"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["products.id"], ["products.*", "products.active"]),
    sku=O(["products.sku"], ["products.*", "products.active"]),
    name=O(["products.name"], ["products.*", "products.active"]),
    category=O(["products.category"], ["products.*", "products.active"]),
    price=O(["products.price"], ["products.*", "products.active"]),
    active=O(["products.active"], ["products.*", "products.active"]),
))

ANN["q06"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"],
         ["customers.*", "customers.id", "orders.*", "orders.customer_id"]),
    name=O(["customers.name"],
           ["customers.*", "customers.id", "orders.*", "orders.customer_id"]),
    region=O(["customers.region"],
             ["customers.*", "customers.id", "orders.*",
              "orders.customer_id"]),
    segment=O(["customers.segment"],
              ["customers.*", "customers.id", "orders.*",
               "orders.customer_id"]),
    created_at=O(["customers.created_at"],
                 ["customers.*", "customers.id", "orders.*",
                  "orders.customer_id"]),
    amt=O(["orders.amount"],
          ["customers.*", "customers.id", "orders.*", "orders.customer_id"]),
))

ANN["q07"] = dict(executable=True, reports=[], outputs=dict(
    customer_id=O(["orders.customer_id"], ["orders.*", "orders.customer_id"]),
    cnt=O(["orders.*"], ["orders.*", "orders.customer_id"]),
    total=O(["orders.amount"], ["orders.*", "orders.customer_id"]),
))

ANN["q08"] = dict(executable=True, reports=[], outputs=dict(
    n=O(["orders.*"], ["orders.*", "orders.status"]),
    avg_amt=O(["orders.amount"], ["orders.*", "orders.status"]),
))

ANN["q09"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"], ["customers.*", "customers.region"]),
    name=O(["customers.name"], ["customers.*", "customers.region"]),
))

# NOTE: SQLite rejects q10 (treats the CTE name as a self-reference); the
# analyzer follows standard SQL scoping (a non-recursive CTE body sees the
# base table), so q10 is verified statically only.
ANN["q10"] = dict(executable=False, reports=[], outputs=dict(
    id=O(["customers.id"],
         ["customers.*", "customers.region", "customers.segment"]),
    region=O(["customers.region"],
             ["customers.*", "customers.region", "customers.segment"]),
))

ANN["q11"] = dict(executable=True, reports=[], outputs=dict(
    x=O(["orders.customer_id"],
        ["orders.*", "orders.amount", "orders.customer_id"]),
    total=O(["orders.amount"],
            ["orders.*", "orders.amount", "orders.customer_id"]),
))

ANN["q12"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"], ["orders.*"]),
    status=O(["orders.status"], ["orders.*"]),
))

ANN["q13"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"], ["customers.*"]),
    total=O(["customers.id", "orders.*", "orders.amount",
             "orders.customer_id"],
            ["customers.*"]),
))

ANN["q14"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"],
         ["customers.*", "customers.id", "orders.*", "orders.customer_id",
          "orders.status"]),
    name=O(["customers.name"],
           ["customers.*", "customers.id", "orders.*", "orders.customer_id",
            "orders.status"]),
))

ANN["q15"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"],
         ["customers.*", "customers.id", "customers.region", "orders.*",
          "orders.customer_id"]),
    amount=O(["orders.amount"],
             ["customers.*", "customers.id", "customers.region", "orders.*",
              "orders.customer_id"]),
))

ANN["q16"] = dict(executable=True, reports=[], outputs=dict(
    cid=O(["orders.customer_id"],
          ["orders.*", "orders.amount", "orders.customer_id"]),
    total=O(["orders.amount"],
            ["orders.*", "orders.amount", "orders.customer_id"]),
))

ANN["q17"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id", "products.id"], ["customers.*", "products.*"]),
    name=O(["customers.name", "products.sku"],
           ["customers.*", "products.*"]),
))

ANN["q18"] = dict(executable=True, reports=[], outputs=dict(
    status=O(["orders.status", "shipments.status"],
             ["orders.*", "orders.status", "shipments.*",
              "shipments.status"]),
))

ANN["q19"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"], ["orders.*"]),
    size=O(["orders.amount"], ["orders.*"]),
))

ANN["q20"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"], ["orders.*", "orders.customer_id", "orders.id"]),
    other_id=O(["orders.id"],
               ["orders.*", "orders.customer_id", "orders.id"]),
))

ANN["q21"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"], ["orders.*"]),
    amount=O(["orders.amount"], ["orders.*"]),
    cust_total=O(["orders.amount", "orders.customer_id"], ["orders.*"]),
    rn=O(["orders.ordered_at"], ["orders.*"]),
))

ANN["q22"] = dict(executable=True, reports=[], outputs=dict(
    region=O(["customers.region"],
             ["customers.*", "customers.region", "customers.segment"]),
    segment=O(["customers.segment"],
              ["customers.*", "customers.region", "customers.segment"]),
))

ANN["q23"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"], ["orders.*", "orders.amount"]),
    amount=O(["orders.amount"], ["orders.*", "orders.amount"]),
))

ANN["q24"] = dict(executable=True, reports=[], outputs=dict(
    category=O(["products.category"],
               ["order_items.*", "order_items.order_id",
                "order_items.product_id", "orders.*", "orders.id",
                "orders.status", "products.*", "products.category",
                "products.id"]),
    revenue=O(["order_items.qty", "order_items.unit_price"],
              ["order_items.*", "order_items.order_id",
               "order_items.product_id", "orders.*", "orders.id",
               "orders.status", "products.*", "products.category",
               "products.id"]),
))

ANN["q25"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["customers.id"],
         ["customers.*", "customers.id", "orders.*", "orders.customer_id",
          "orders.id"]),
    name=O(["customers.name"],
           ["customers.*", "customers.id", "orders.*", "orders.customer_id",
            "orders.id"]),
))

ANN["q26"] = dict(executable=True, reports=[], outputs=dict(
    order_id=O(["orders.id"], ["orders.*", "orders.id", "shipments.*",
                               "shipments.order_id"]),
    carrier=O(["shipments.carrier"],
              ["orders.*", "orders.id", "shipments.*", "shipments.order_id"]),
))

ANN["q27"] = dict(executable=True, reports=[], outputs=dict(
    id=O(["orders.id"],
         ["customers.*", "customers.id", "orders.*", "orders.amount",
          "orders.customer_id"]),
    customer_id=O(["orders.customer_id"],
                  ["customers.*", "customers.id", "orders.*",
                   "orders.amount", "orders.customer_id"]),
    status=O(["orders.status"],
             ["customers.*", "customers.id", "orders.*", "orders.amount",
              "orders.customer_id"]),
    amount=O(["orders.amount"],
             ["customers.*", "customers.id", "orders.*", "orders.amount",
              "orders.customer_id"]),
    ordered_at=O(["orders.ordered_at"],
                 ["customers.*", "customers.id", "orders.*", "orders.amount",
                  "orders.customer_id"]),
    name=O(["customers.name"],
           ["customers.*", "customers.id", "orders.*", "orders.amount",
            "orders.customer_id"]),
))

ANN["q28"] = dict(executable=True, reports=[], outputs=dict(
    status=O(["orders.status"],
             ["order_items.*", "order_items.order_id", "orders.*",
              "orders.id", "orders.status"]),
    discounted_qty=O(["order_items.discount", "order_items.qty"],
                     ["order_items.*", "order_items.order_id", "orders.*",
                      "orders.id", "orders.status"]),
))

ANN["q29"] = dict(
    executable=False,
    reports=[dict(kind="ambiguous", line=1, col=14)],
    outputs=dict(
        id=O(["customers.id"],
             ["customers.*", "customers.segment", "products.*",
              "products.category"]),
        name=O(["customers.name", "products.name"],
               ["customers.*", "customers.segment", "products.*",
                "products.category"]),
    ))

ANN["q30"] = dict(
    executable=False,
    reports=[dict(kind="missing_table", line=2, col=6)],
    outputs=dict(
        id=O(["metrics.id"], ["customers.*", "customers.id", "metrics.*",
                              "metrics.id"]),
        val=O(["metrics.val"], ["customers.*", "customers.id", "metrics.*",
                                "metrics.id"]),
        name=O(["customers.name"], ["customers.*", "customers.id",
                                    "metrics.*", "metrics.id"]),
    ))

_ORDERS_ALL = ["orders.amount", "orders.customer_id", "orders.id",
               "orders.ordered_at", "orders.status"]
ANN["q31"] = dict(
    executable=True,
    reports=[dict(kind="unsupported", line=1, col=16)],
    outputs=dict(
        id=O(_ORDERS_ALL, _ORDERS_ALL),
        amount=O(_ORDERS_ALL, _ORDERS_ALL),
    ))

_Q32_ROW = ["customers.*", "customers.id", "order_items.*",
            "order_items.order_id", "order_items.qty", "orders.*",
            "orders.amount", "orders.customer_id", "orders.id",
            "shipments.*", "shipments.order_id"]
ANN["q32"] = dict(executable=True, reports=[], outputs=dict(
    name=O(["customers.name"], _Q32_ROW),
    amount=O(["orders.amount"], _Q32_ROW),
    carrier=O(["shipments.carrier"], _Q32_ROW),
))
