# hafence

A tick-by-tick simulator of a primary/replica database cluster with a high
availability controller. It is used to check that failover stays safe under
network partitions, crashes and clock drift.

    python3 ha.py run <scenario.json> <out>    # writes out/trace.jsonl, state.json, conflicts/
    python3 ha.py show <out>
    python3 ha.py verify <scenario.json>       # audit invariants, exit 1 on violation
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
   newer the node steps down instead; a step-down always clears the lease;
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
  someone else, so at most one node accepts writes in any tick. The boundary
  is strict: a lease expiring exactly at `t` already counts as expired, since
  the controller may promote a replacement at that same instant.
* An accepted write is appended to the primary's log as
  `{"term", "key", "value"}` and pushed to every node it reaches. The storage
  layer itself also refuses an append whose term is older than the node's
  current term (`store_write`), so a request that was in flight during a
  fencing cannot slip into the log.
* Fencing: a node rejects a push from a primary whose term is older than its
  own; the pushing primary then steps down and adopts the newer term (a write
  whose push was rejected is reported `fenced`). Term numbers are strictly
  increasing -- every election raises the controller's term above every
  candidate's -- so an old primary can never regain authority by rebooting.
  The controller likewise refuses to renew a node whose term is older than its
  own, so a stale primary cannot re-acquire a lease just by reaching `ctl`.
* A node accepting a push finds the fork point: the longest prefix whose
  entries carry the same terms as the primary's. Its entries after the fork are
  rolled back, appended to `conflicts/<node>.jsonl` as
  `{"index", "term", "key", "value"}` (index from 1), and the primary's entries
  are copied. A rejected push changes nothing. The rollback is also emitted as
  a `rollback` trace event carrying `fork` and the rolled-back entries.
* A restarted node keeps its log and term, forgets any lease and comes back as
  a replica, so it catches up (rolling back any fork) before it can serve.
  A crash forgets the lease; a restart of an already-running node is a no-op.

## Scenario validation

`run` and `verify` refuse scenarios the safety model cannot cover, with exit
status 2: unknown or duplicate node names, `lease <= 0`, `drift` outside
`[0, 1)`, a `clock_rate` outside `[1 - drift, 1 + drift]` (the lease margin is
derived from that bound), events or writes outside `1..ticks`, writes without
`key`/`value`, and partition groups naming unknown members.

## Auditing

`verify` simulates the scenario and reports:

* `single_writer`: no tick has more than one node that is *writable* (alive,
  primary, valid lease on its own clock) -- checked on every tick, not only on
  ticks with client writes;
* `consistent`: exactly one primary and every alive node's log equals it;
* `rollbacks`: every `rollback` event with its fork index and entries;
* `violations`: dropped-but-unrecorded entries (no silent loss), a conflict
  file that does not match its rollback events, and any index that was never
  written;
* `writes`: per node counts of `ok` / `rejected` / `fenced`.

Exit status 0 means safe and converged, 1 means a violation.

## Examples

* `examples/slow_clock.json` -- `n1`'s clock runs 20% slow and is partitioned
  from the controller; the old primary must stop accepting before `n2` is
  promoted, so no write is accepted on two sides at once;
* `examples/delayed_rejoin.json` -- `n1` writes while isolated, `n2` takes
  over, and `n1` rejoins late; its divergent writes are rolled back into
  `conflicts/n1.jsonl` and it catches up;
* `examples/double_failure.json` -- `n2` fails mid-partition and `n3` is
  promoted, then `n1` (long stale) rejoins; two failovers and a three-entry
  rollback, ending with all logs identical.

`python3 ha.py verify examples/<file>.json` audits each of them.

## Output

`trace.jsonl`: one line per write
`{"t", "node", "key", "value", "result": "ok" | "rejected" | "fenced"}`, per
event `{"t", "event": "promote" | "step_down", "node", "term"}`, and per
reconciliation `{"t", "event": "rollback", "node", "leader", "term", "fork",
"rolled_back"}`.
`state.json`: per node `role`, `term`, `alive`, `log` (`[term, key, value]`)
and `kv` (the log applied in order). `conflicts/<node>.jsonl` exists for every
node, empty when nothing was rolled back. `run` prints the events and the
writes that were not accepted.
