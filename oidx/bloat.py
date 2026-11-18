"""Index bloat estimate shown in the maintenance dashboard."""


def bloat(index):
    total = sum(len(v) for v in index.entries.values())
    live = 0
    for key, tids in index.entries.items():
        for tid in tids:
            t = index.heap.tuples.get(tid)
            if t and t["live"] and t["key"] == key:
                live += 1
    return {"entries": total, "live": live, "dead_ratio": 0 if total == 0 else round(1 - live / total, 3)}
