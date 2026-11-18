# rwsplit

A read/write splitting proxy on top of a simulated primary/replica cluster.
Writes go to the primary; reads are routed to replicas while keeping session
consistency: a session reads its own writes and never sees data older than
what it has already read.

    python3 simulate.py <scenario.json> <out.json>
    python3 -m unittest discover -s tests

## Scenario

    {"replicas": [{"name": "r1", "lag": 30}, {"name": "r2", "lag": 80}],
     "policy": {"on_stale": "wait", "max_wait": 50, "probe_interval": 100},
     "events": [{"t": 0, "op": "write", "session": "s1", "key": "a", "value": 1},
                {"t": 5, "op": "read", "session": "s1", "key": "a"}]}

Times are integers on a simulated clock; events are processed in order and
their `t` never decreases. Event ops: `write` (session, key, value), `read`
(session, key), `begin` / `commit` (session), `set_lag` (replica, lag),
`down` / `up` (replica).

## Cluster model (`cluster.py`, given)

A write outside a transaction commits at once on the primary with the next
commit LSN (1, 2, ...). Writes inside `begin` ... `commit` are buffered and
commit together under one LSN at the `commit` event. Replica `R` replays
commits in LSN order: commit `i` made at time `c_i` becomes visible on `R` at
`a_i = max(a_(i-1), c_i + lag)`, with `R`'s lag at time `c_i`. The applied LSN
of `R` at time `τ` is the largest `i` with `a_i ≤ τ` (0 if none); the
primary's is its last commit LSN. A read returns the key's latest version
with LSN ≤ the node's applied LSN: its value and that LSN as `version`
(`null` and 0 if there is none). A `down` replica rejects reads until `up`.

## Routing rules

Each session remembers `last_write` (commit LSN of its latest write) and
`last_read` (the applied LSN of the node that served its latest read). A
replica is eligible for a read when its applied LSN is at least
`max(last_write, last_read)`.

1. Inside a transaction every read and write goes to the primary, at once.
   A read of a key the transaction has written returns the buffered value
   with `version` null.
2. Otherwise replicas are tried in round-robin order: a cursor over the
   configured list starts at the first replica; the read goes to the first
   eligible replica at or after the cursor (wrapping around), and the cursor
   moves to the replica after it.
3. With no eligible replica, `on_stale: "primary"` sends the read to the
   primary at once. `on_stale: "wait"` waits until the earliest time, at most
   `max_wait` after the read arrived, at which some replica becomes eligible
   and serves it then (rule 2 among those eligible at that time); if none
   becomes eligible in time it goes to the primary at arrival time plus
   `max_wait`.
4. A replica that rejects a read is removed from routing at that time and the
   read is routed again at the same time (with the same deadline). A removed
   replica is considered again for reads served at or after
   `removed_at + probe_interval`; if it is still down it is removed again.

## Output

`{"reads": [{"i": 3, "session": "s1", "target": "r1", "served_at": 35, "value": 1, "version": 1, "failed": []}, ...]}`:
one entry per read event (`i` is the event index), `target` is a replica name
or `primary`, `failed` lists the replicas that rejected this read, in order.
