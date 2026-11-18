"""Rehearse a nightly dump next to the live order workload.

    python3 rehearse.py config.json result.json

config.json: {"seed": 1, "initial": 40, "writes_per_chunk": 2, "chunk": 10,
              "lock_tables": false, "vacuum_every": 5, "tables": ["orders", "order_lines"]}

After every dump chunk the workload runs ``writes_per_chunk`` transactions;
while the tables are locked, those writes wait.  The result contains the
dumped data, the restore-drill problems, how many write attempts had to
wait (``blocked``), the commit log of the workload, and how many versions
the store still holds afterwards.  Autovacuum runs every ``vacuum_every``
write transactions.
"""
import json
import sys

from dumper.dump import Dump
from dumper.mvcc import Store
from dumper.restore import check_orders
from dumper.workload import OrderWorkload


def run(cfg):
    store = Store()
    work = OrderWorkload(store, cfg.get("seed", 1), cfg.get("initial", 40))
    dump = Dump(store, cfg.get("tables", ["orders", "order_lines"]), cfg.get("chunk", 10), cfg.get("lock_tables", False))
    blocked, backlog, writes = 0, 0, 0
    every = cfg.get("vacuum_every", 5)

    def write():
        nonlocal writes
        work.step()
        writes += 1
        if every and writes % every == 0:
            store.vacuum()

    for _ in dump.run():
        backlog += cfg.get("writes_per_chunk", 2)
        while backlog:
            if store.locked:
                blocked += backlog
                break
            write()
            backlog -= 1
    while backlog:
        write()
        backlog -= 1
    store.vacuum()
    return {"data": dump.data, "problems": check_orders(dump.data), "blocked": blocked,
            "snapshot": dump.snapshot, "log": store.log, "versions_after": store.version_count(),
            "snapshots_held": sorted(store.active_snapshots)}


def main():
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(cfg), f, indent=1)


if __name__ == "__main__":
    main()
