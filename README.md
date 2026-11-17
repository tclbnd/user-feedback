# lockd

The lock manager of a row store. Transactions take intention locks on
tables and shared or exclusive locks on rows; blocked requests wait in FIFO
queues, and deadlocks are found on the wait-for graph.

    python3 locksim.py run <script.json>      # print the JSON outcome
    python3 locksim.py report <script.json>   # one line per event
    python3 -m unittest discover -s tests

## Script

    {"ops": [{"tx": 1, "op": "begin"},
             {"tx": 1, "op": "lock", "res": "orders", "mode": "IX"},
             {"tx": 1, "op": "write", "res": "orders/7"},
             {"tx": 1, "op": "lock", "res": "items/3", "mode": "S"},
             {"tx": 1, "op": "commit"}]}

Operations are fed in order. Each transaction runs its own operations in
order: while it waits for a lock, its later operations queue up behind the
blocked one and run once the lock is granted. `write` takes an `X` lock on the
row and, once granted, counts one written row. `commit` and `abort` release
every lock of the transaction. Operations of a transaction that has been
aborted are ignored.

## Output

    {"events": [...], "committed": [1, 3], "aborted": [2], "waiting": []}

`committed` is in completion order; `waiting` lists transactions still blocked
at the end. Events, in the order they happen:

    {"event": "granted", "tx": 1, "res": "orders/7", "mode": "X"}
    {"event": "waiting", "tx": 2, "res": "orders/7", "mode": "S"}
    {"event": "deadlock", "cycle": [1, 2], "victim": 2}   # cycle members sorted
    {"event": "abort", "tx": 2}
    {"event": "commit", "tx": 1}

## Modes

| held \ requested | IS | IX | S | SIX | X |
|------------------|----|----|---|-----|---|
| IS               | y  | y  | y | y   | n |
| IX               | y  | y  | n | n   | n |
| S                | y  | n  | y | n   | n |
| SIX              | y  | n  | n | n   | n |
| X                | n  | n  | n | n   | n |

A request by a transaction that already holds the resource is a conversion
to the join of both modes (`S` + `IX` = `SIX`; otherwise the stronger mode, `X`
if neither covers the other). A conversion already covered by the held mode is
granted at once.

## Queues

A new request is granted only when it is compatible with every granted lock
and nobody is queued on the resource; otherwise it joins the tail of the
queue. A conversion only has to be compatible with the locks of the other
holders; if it is not, it waits at the head of the queue, behind earlier
conversions. When locks are released the queue is granted from the head, in
order, until the first request that is still incompatible, so a waiting `X`
request is never overtaken by later shared requests. Transactions woken by one
release resume in the order they were granted.

## Deadlocks

The wait-for graph has an edge `W -> H` when `W` waits on a resource where
`H` holds an incompatible lock, or where `H` is queued ahead of `W` with an
incompatible mode (conversions only wait for holders). The graph is kept up
to date as requests block and locks are released. Each time a request blocks,
the engine looks for a cycle through the requester (depth first, smaller
transaction ids first). The victim is the member of the cycle that has written
the fewest rows, and among those the one that began last; it is aborted and
its locks are released. This repeats while the requester is still blocked and
on a cycle. There are no timeouts: a transaction can wait as long as it takes.

## Acceptance scripts

The `examples/` directory contains scripted workloads for every required
case; `tests/test_deadlock.py` asserts the detected cycles, victims and
completion orders for all of them:

| script                  | scenario                                              |
|-------------------------|-------------------------------------------------------|
| `cycle2.json`           | two-party cycle, later beginner is victim             |
| `cycle3.json`           | three-party cycle, survivors finish in wake order     |
| `cycle_upgrade.json`    | deadlock from S -> X conversion (lock upgrade)        |
| `cycle_intention.json`  | three-party cycle built from IS/IX/S/SIX conflicts    |
| `victim_writes.json`    | fewest-written-rows rule overrides begin time         |
| `acyclic.json`          | long FIFO wait with no cycle: zero false deadlocks    |
| `intention_compat.json` | intention compatibility and SIX upgrade, no deadlock  |
| `upgrade_queue.json`    | conversions at queue head; waiting X is not starved   |

Run everything with `python3 -m unittest discover -s tests`.
