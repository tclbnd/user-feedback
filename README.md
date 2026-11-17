# hafence

A tick-by-tick simulator of a primary/replica database cluster with a high
availability controller. It is used to check that failover stays safe under
network partitions, crashes and clock drift.

    python3 ha.py run <scenario.json> <out>    # writes out/trace.jsonl, state.json, summary.json, conflicts/
    python3 ha.py show <out>
    python3 ha.py check <out>                  # verify the safety invariants of a finished run
    python3 -m unittest discover -s tests

`run` and `check` exit non-zero when an invariant is violated. `examples/`
holds one scenario per acceptance case:

| scenario | covers |
| --- | --- |
| `delayed_rejoin.json` | partition, failover, old primary rejoins late |
| `slow_clock.json` | a primary whose clock runs at `1 - drift` |
| `partition_writes.json` | writes on the minority side during the partition |
| `failover_double_fault.json` | a second failure while the failover is in progress |
| `late_rejoin.json` | the old primary keeps writing, then rejoins after a heal |

## Scenario

    {"nodes": ["n1", "n2", "n3"], "lease": 10, "drift": 0.2,
     "clock_rate": {"n1": 0.8}, "ticks": 30,
     "events": [{"t": 3, "partition": [["n1"], ["n2", "n3", "ctl"]]},
                {"t": 16, "heal": true}, {"t": 20, "crash": "n2"}, {"t": 25, "restart": "n2"}],
     "writes": [{"t": 1, "node": "n1", "key": "a", "value": "1"}]}

`ctl` is the controller. Two members reach each other when some partition
group holds both (no partition: everyone reaches everyone); a crashed node
reaches nobody and does nothing. Node `x` reads its local clock as
`t * clock_rate[x]` (default 1.0); the controller's clock is `t`. Every rate
lies within `1 ± drift` (the simulator refuses a scenario that breaks this).

At start `n1` is primary with term 1 and a lease granted at `t = 0`; all logs
are empty. Each tick `t = 1 .. ticks` runs, in this order:

1. events of tick `t`; a restarted node keeps its log and term and is a replica;
2. lease renewal: an alive primary that reaches `ctl` renews (the controller
   records `t`, the node records its local time). If the controller's term is
   newer the node steps down instead. A primary that cannot reach `ctl` resigns
   as soon as its own lease has run out, so it stops pushing a stale log even
   if no newer term ever reached it;
3. failover: once `t - last renewal >= lease`, the controller promotes one of
   the alive nodes it reaches: the one whose log is most up to date, compared
   by (term of last entry, log length), ties to the smaller name. The term goes
   up by one, all reached nodes adopt it, the winner gets a lease. Nobody
   reachable: no failover;
4. client writes of tick `t`, in file order, then
5. every alive primary pushes its log to every node it reaches.

## Safety rules

* **Lease.** A primary accepts a write only while its lease is valid on its own
  clock: local time `< lease start + lease * (1 - drift)`. Since the local rate
  is at least `1 - drift`, the real-time deadline is at most
  `grant + lease`, i.e. no later than the instant the controller may promote
  somebody else, so at most one node accepts writes in any tick.
* **Monotone terms.** `failover` sets `term = max(controller term, every
  candidate's term) + 1`; a node never adopts a term below the one it already
  has, and a restarted node keeps its term. A primary that has adopted a newer
  term can never win a later election with an old one.
* **Freshest log wins.** The election key is (term of last entry, log length),
  so a candidate whose last entry carries an older term loses to one that has
  seen the newer term even if it has more entries.
* **Storage fencing.** A node refuses a push from a primary whose term is
  older than its own (and from a second primary in the same term). Before
  pushing, a primary checks the reachable nodes: if any has adopted a newer
  term, it steps down without touching a log. So a zombie primary cannot
  overwrite a replica that heard the news. A write whose push is rejected is
  reported `fenced`; it was accepted locally on a valid lease, so it stays in
  the log and is exported as a conflict by the next primary.
* **Reconciliation on rejoin.** A node accepting a push finds the fork point:
  the longest prefix that is identical to the primary's log. Its entries after
  the fork are rolled back, appended to `conflicts/<node>.jsonl` as
  `{"index", "term", "key", "value"}` (index from 1), and the primary's entries
  are copied. The node then follows the primary as a replica.

## Output

`trace.jsonl`: one line per write
`{"t", "node", "key", "value", "result": "ok" | "rejected" | "fenced"}` and per
event `{"t", "event": "promote" | "step_down", "node", "term"}`.
`state.json`: per node `role`, `term`, `alive`, `log` (`[term, key, value]`)
and `kv` (the log applied in order). `conflicts/<node>.jsonl` exists for every
node, empty when nothing was rolled back. `summary.json` records, per tick, the
nodes whose lease was valid (`lease_holders`) and that accepted writes
(`accepted`), plus every reconciliation episode (`reconciliations`: time,
leader, node, fork point, rolled-back entries) and the partition in force at
the end of the run. `run` prints the events, the writes that were not accepted,
and the reconciliation episodes.

## Checks

`check` re-reads a finished run and reports:

1. no tick in which two nodes accepted a write;
2. no tick in which two nodes held a valid lease, and the acceptance record
   agrees with the trace;
3. with no partition at the end, all live nodes share one log and one `kv`;
   with a partition still in force, at least every node its primary reaches
   has caught up;
4. each rollback in `summary.json` appears in the owning node's conflict file,
   forms exactly the suffix after its fork point, and never rewrites a log
   position inside a term;
5. no node's final term is below a term the trace shows it adopted.
