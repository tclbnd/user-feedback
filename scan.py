"""Run a condition as a bitmap scan and as a sequential scan.

    python3 scan.py table.json query.json result.json

table.json: {"rows": [{"id": 1, "region": "eu", ...}, ...],
             "updates": [{"id": 3, "set": {"status": "closed"}}, ...],
             "deleted": [ids...]}

Updates are applied in order, then deletes (a deleted id removes the
current version of that row).
query.json: {"cond": {...}, "work_mem": 64}   or   {"report": "open_eu"}
"""
import json
import sys

from bitmap.btree import Index
from bitmap.executor import bitmap_scan, seq_scan
from bitmap.heap import Heap
from bitmap.report import REPORTS
from bitmap.vacuum import stats


def load(spec):
    heap = Heap()
    tids = {}
    for row in spec["rows"]:
        tids[row["id"]] = heap.insert(row)
    cols = sorted({c for r in spec["rows"] for c in r} - {"id"})
    indexes = {c: Index(heap, c) for c in cols}
    for upd in spec.get("updates", []):
        new_tid, changed = heap.update(tids[upd["id"]], upd["set"])
        for col in changed:
            indexes[col].add(upd["set"][col], new_tid)
        tids[upd["id"]] = new_tid
    for rid in spec.get("deleted", []):
        heap.delete(tids[rid])
    return heap, indexes


def main():
    heap, indexes = load(json.load(open(sys.argv[1], encoding="utf-8")))
    q = json.load(open(sys.argv[2], encoding="utf-8"))
    cond = REPORTS[q["report"]] if "report" in q else q["cond"]
    result = bitmap_scan(heap, indexes, cond, q.get("work_mem", 64))
    result["seq_ids"] = seq_scan(heap, cond)
    result["vacuum"] = stats(heap)
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
