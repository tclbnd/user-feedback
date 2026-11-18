# seqdb

Storage layer behind the order service. It keeps the `orders` and
`order_lines` tables, hands out order numbers from the `orders_id` sequence,
and ships its write-ahead log to a hot standby that can be promoted when the
primary goes down.

Order numbers only need to be unique. Gaps are fine (a declined payment or a
crash may skip a few numbers), reusing a number that was already handed out
is not.

## Layout

- `seqdb/disk.py` – simulated local disk with a page cache; unsynced writes are lost on crash
- `seqdb/wal.py` – write-ahead log, synced at every commit
- `seqdb/sequence.py` – cached sequences (`cache` numbers per block)
- `seqdb/engine.py` – tables, transactions, crash recovery
- `seqdb/replication.py` – log shipping and standby promotion
- `seqdb/orders.py` – order placement and the nightly bulk import
- `seqdb/report.py` – daily summary for the finance dashboard
- `sim.py` – replays an operations scenario against a primary/standby pair

## Running

    python3 sim.py examples/failover.json out.json
    python3 -m unittest discover -s tests

A scenario is a list of steps:

    {"cache": 20, "steps": [{"op": "order", "n": 5}, {"op": "ship"}, {"op": "failover"}]}

Steps: `order` (n orders, one transaction each), `failed_order` (takes a
number, then the payment check fails and the transaction rolls back), `bulk`
(n orders in one transaction), `ship` (send the primary's log to the
standby), `crash` (the primary loses everything not yet synced and restarts),
`restart` (clean shutdown and start), `failover` (promote the standby; a new
empty standby is attached to it).

`out.json` contains `issued` (every order number handed to a caller, in
order), `errors`, `syncs` (disk syncs on the current primary's disk) and the
daily `summary`.
