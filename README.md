# hafence

A tick-by-tick simulator of a primary/replica database cluster with a high
availability controller. It is used to check that failover stays safe under
network partitions, crashes and clock drift.

    python3 ha.py run <scenario.json> <out>    # writes out/trace.jsonl, state.json, conflicts/
    python3 ha.py show <out>
    python3 -m unittest discover -s tests

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
lies within `1 ± drift`.

At start `n1` is primary with term 1 and a lease granted at `t = 0`; all logs
are empty. Each tick `t = 1 .. ticks` runs, in this order:

1. events of tick `t`; a restarted node keeps its log and term and is a replica;
2. lease renewal: an alive primary that reaches `ctl` renews (the controller
   records `t`, the node records its local time). If the controller's term is
   newer the node steps down instead;
3. failover: once `t - last renewal >= lease`, the controller promotes one of
   the alive nodes it reaches: the one whose log is most up to date, compared
   by (term of last entry, log length), ties to the smaller name. The term goes
   up by one, all reached nodes adopt it, the winner gets a lease. Nobody
   reachable: no failover;
4. client writes of tick `t`, in file order, then
5. every alive primary pushes its log to every node it reaches.

## Safety rules

* A primary accepts a write only while its lease is valid on its own clock:
  local time `< lease start + lease * (1 - drift)`. Since a slow clock may run
  at `1 - drift`, the lease then always ends before the controller can promote
  someone else, so at most one node accepts writes in any tick.
* An accepted write is appended to the primary's log as
  `{"term", "key", "value"}` and pushed to every node it reaches.
* Storage fencing: a node rejects a push from a primary whose term is older
  than its own; the pushing primary then steps down and adopts the newer term
  (a write whose push was rejected is reported `fenced`).
* A node accepting a push finds the fork point: the longest prefix whose
  entries carry the same terms as the primary's. Its entries after the fork are
  rolled back, appended to `conflicts/<node>.jsonl` as
  `{"index", "term", "key", "value"}` (index from 1), and the primary's entries
  are copied.

## Output

`trace.jsonl`: one line per write
`{"t", "node", "key", "value", "result": "ok" | "rejected" | "fenced"}` and per
event `{"t", "event": "promote" | "step_down", "node", "term"}`.
`state.json`: per node `role`, `term`, `alive`, `log` (`[term, key, value]`)
and `kv` (the log applied in order). `conflicts/<node>.jsonl` exists for every
node, empty when nothing was rolled back. `run` prints the events and the
writes that were not accepted.
