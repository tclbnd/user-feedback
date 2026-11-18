# xshard

Cross-shard transactions for the ledger service. Each account lives on one
shard; a transfer between accounts on different shards runs as a two-phase
commit driven by the coordinator.

## Layout

- `xshard/participant.py` – a shard: row locks, prepare/commit/abort, local writes, restart from its log
- `xshard/coordinator.py` – the coordinator and its decision log
- `xshard/log.py`, `xshard/disk.py` – per-node log on a simulated disk (unsynced writes are lost on crash)
- `xshard/transfers.py` – routing of accounts to shards and the balance report
- `cluster.py` – runs a scenario against a small cluster and injects crashes

## Running

    python3 cluster.py examples/coordinator_crash.json out.json
    python3 cluster.py examples/split_outcome.json out.json
    python3 cluster.py examples/double_crash.json out.json
    python3 -m unittest discover -s tests

Scenario steps: `transfer` (tx, from, to, amount, optional `crash`),
`restart_coordinator`, `crash_shard` / `restart_shard` (shard), `recover`
(the coordinator's recovery pass followed by every live shard asking about
its in-doubt transactions), `write` (shard, key, delta: a local single-shard
update, refused while the row is locked).

Crash points for `transfer`: `after_prepare` (coordinator dies once all
shards have voted), `after_first_decision` (coordinator dies right after
telling the first shard the outcome), `participant_after_prepare:<shard>`
(that shard dies right after voting yes).

`out.json` has the per-transaction outcome on every shard (`committed`,
`aborted`, `prepared`, `unknown`), the locks each shard still holds, and the
balance report. Transfers must never create or destroy money.

## Crash recovery guarantees

The protocol is two-phase commit with write-ahead logging, so a crash at any
step – on the coordinator or on any shard, any number of times – converges
to one outcome and releases the row locks:

1. The coordinator syncs a `begin` record *before* asking shards to prepare,
   so after a restart it still knows about every transaction that may have
   shards waiting in `prepared`.
2. The `decision` record is synced *before* any shard is told the outcome.
   A shard can therefore only observe a decision the coordinator will
   remember, which makes a one-shard-commit / one-shard-rollback split
   impossible.
3. On recovery, every durable `begin` without a durable `decision` gets a
   durable abort (presumed abort: no shard could have heard a decision that
   was never written), and the outcome is re-sent to every shard still in
   doubt. All re-sends and shard commit/abort calls are idempotent, so
   repeated crashes and repeated recovery passes reach the same state.
4. A shard that crashes after voting yes replays its log on restart,
   re-acquires the locks of every still-prepared transaction, and either
   applies the coordinator's decision on the next `recover` or keeps
   refusing local writes until one arrives.

`examples/double_crash.json` exercises the coordinator crashing twice
around an unresolved transaction while a shard is also down; both shards
end at the same abort and every lock is released.
