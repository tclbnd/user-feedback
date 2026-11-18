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
