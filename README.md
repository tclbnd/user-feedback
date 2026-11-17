# ivm

Incremental maintenance of one materialized view:

    SELECT c.region, COUNT(*) AS n, SUM(o.amount) AS total, AVG(o.amount) AS avg,
           MIN(o.amount) AS lo, MAX(o.amount) AS hi
    FROM orders o JOIN customers c ON o.cust = c.id
    WHERE o.status <> 'void'
    GROUP BY c.region

    python3 ivm.py <script.json>            # prints one JSON result per output step
    python3 -m unittest discover -s tests

Tables: `orders {id, cust, amount, status}` and `customers {id, region}`;
`id` is the primary key, `amount` an integer. `orders` is indexed by `cust` and
`customers` by `region`, so a single group can be scanned without touching the
rest of the base tables. Script steps:

    {"op": "insert", "table": "orders", "row": {...}}
    {"op": "delete", "table": "orders", "id": 7}
    {"op": "update", "table": "customers", "row": {...}}     # replaces the row with the same id
    {"op": "txn", "steps": [ ...insert/delete/update... ]}   # applied together
    {"op": "view"}      -> {"op", "rows": [{"region", "n", "total", "avg", "lo", "hi"}]}  (by region)
    {"op": "stats"}     -> {"op", "rechecked": [regions]}   (since the previous stats, sorted)

## Maintenance rules

The view is never recomputed as a whole. Each base change produces deltas of
joined rows: an insert adds the joined rows it creates, a delete removes them,
and an update is handled as a delete of the old row followed by an insert of
the new one. A group keeps `n` and `total` and adjusts them by the delta;
`avg` is `total / n`. Adding a row lowers `lo` / raises `hi` directly. Removing
a row whose amount equals the group's current `lo` or `hi` rechecks that group
alone from the base tables (and records the region for `stats`); other groups
are not touched, and removing an interior value needs no recheck. A group whose
count reaches 0 disappears. All steps of a `txn` are applied before the next
output step.

A customer whose region changes is handled as a delete of the old customer row
followed by an insert of the new one, so all of that customer's orders move
between groups. Because such a change touches many rows of the same group, the
rechecks it schedules are batched: each affected region is rechecked at most
once, after the base tables are consistent, never on a half-updated state.
