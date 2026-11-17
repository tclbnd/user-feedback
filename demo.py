"""End-to-end demonstration: LRU-K trace stats, crash sweep and recovery."""
import json
import os
import subprocess
import sys
import tempfile

import storage

ROOT = os.path.dirname(os.path.abspath(__file__))
BP = os.path.join(ROOT, "bp.py")

WRITE_HEAVY = [
    {"op": "write", "page": p, "slot": 0, "value": "w%d" % p} for p in range(6)
] + [
    {"op": "flush", "max": 2},
    {"op": "write", "page": 1, "slot": 1, "value": "second"},
    {"op": "write", "page": 4, "slot": 2, "value": "third"},
    {"op": "checkpoint"},
    {"op": "write", "page": 0, "slot": 3, "value": "fourth"},
    {"op": "commit"},
    {"op": "flush", "max": 3},
    {"op": "write", "page": 7, "slot": 0, "value": "late"},
    {"op": "write", "page": 8, "slot": 0, "value": "later"},
    {"op": "flush", "max": 10},
]


def run(directory, trace, crash_at_write=None):
    if isinstance(trace, str):  # path to an existing trace file
        path = trace
    else:
        path = os.path.join(directory, "trace.json")
        with open(path, "w") as fh:
            json.dump(trace, fh)
    cmd = [sys.executable, BP, "run", directory, path]
    if crash_at_write is not None:
        cmd += ["--crash-at-write", str(crash_at_write)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


def wal_invariant(directory):
    durable = storage.Wal(os.path.join(directory, "wal.log")).durable()
    by_lsn = {r["lsn"]: r for r in durable if "page" in r}
    disk = storage.Disk(os.path.join(directory, "data.db"))
    for pid in range(disk.count()):
        lsn, _ = disk.read(pid)
        if lsn and (lsn not in by_lsn or by_lsn[lsn]["page"] != pid):
            return False
    return True


def oracle(directory):
    durable = [r for r in
               storage.Wal(os.path.join(directory, "wal.log")).durable()
               if "page" in r]
    out = {}
    for r in sorted(durable, key=lambda r: r["lsn"]):
        out.setdefault(r["page"], ["", "", "", ""])[r["slot"]] = r["value"]
    return out


def main():
    print("== mixed hot access + full table scan ==")
    with tempfile.TemporaryDirectory() as d:
        code, out = run(d, os.path.join(ROOT, "examples", "hot_and_scan.json"))
        print("exit", code, json.dumps(out))
        hit_rate = out["hits"] / (out["hits"] + out["misses"])
        print("hit rate: %.1f%%; hot pages 0..2 never evicted: %s"
              % (100 * hit_rate,
                 all(p not in out["evictions"] for p in (0, 1, 2))))

    print("\n== all frames pinned ==")
    with tempfile.TemporaryDirectory() as d:
        code, out = run(d, os.path.join(ROOT, "examples", "all_pinned.json"))
        print("exit", code, json.dumps(out), "(no hang, trace continued)")

    print("\n== crash sweep over every data page write ==")
    # derive total write-backs from an in-process run
    from bufpool import BufferPool
    with tempfile.TemporaryDirectory() as d:
        disk = storage.Disk(os.path.join(d, "data.db"))
        wal = storage.Wal(os.path.join(d, "wal.log"))
        pool = BufferPool(disk, wal, 4, 2)
        for op in WRITE_HEAVY:
            if op["op"] == "write":
                pool.modify(op["page"], op["slot"], op["value"])
            elif op["op"] == "commit":
                wal.flush()
            elif op["op"] == "flush":
                pool.flush_some(op["max"])
            elif op["op"] == "checkpoint":
                pool.checkpoint()
        total = pool.writes
    print("total data page writes:", total)
    for n in range(1, total + 2):
        with tempfile.TemporaryDirectory() as d:
            code, out = run(d, WRITE_HEAVY, crash_at_write=n)
            assert wal_invariant(d), "WAL invariant broken at write %d" % n
            p = subprocess.run([sys.executable, BP, "recover", d],
                               capture_output=True, text=True, check=True)
            rec = json.loads(p.stdout)
            disk = storage.Disk(os.path.join(d, "data.db"))
            for pid, slots in oracle(d).items():
                assert disk.read(pid)[1] == slots, pid
            status = "crash@%d" % n if code == 75 else "finished"
            print("write %2d (%s): WAL ok, redone=%d, recovery complete"
                  % (n, status, rec["redone"]))
    print("\nall crash points satisfy WAL-first and recover to full state")


if __name__ == "__main__":
    main()
