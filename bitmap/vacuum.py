"""Vacuum statistics for the nightly maintenance report.

``dead_ratio`` is what the autovacuum threshold looks at; ``chains``
counts update chains that readers have to follow because some index still
points at an old version.
"""


def stats(heap):
    total = dead = chains = 0
    for tid in heap.all_tids():
        item = heap.fetch(tid)
        total += 1
        if item["dead"]:
            dead += 1
            if item["next"] is not None:
                chains += 1
    return {"tuples": total, "dead": dead, "chains": chains,
            "dead_ratio": round(dead / total, 3) if total else 0.0}
