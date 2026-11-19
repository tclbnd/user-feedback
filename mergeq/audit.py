"""Load audit: one summary line per sync run, checked by the DWH monitoring.

The monitoring rejects a run whose counts do not add up to the number of
extract rows, so every source row has to end up in exactly one bucket.
"""


def summarize(extract, outcome):
    counts = outcome.get("result") or {}
    total = sum(counts.get(k, 0) for k in ("inserted", "updated", "deleted", "skipped"))
    return {
        "rows": len(extract),
        "ok": outcome["ok"],
        "counts": counts,
        "balanced": (not outcome["ok"]) or total == len(extract),
        "error": outcome.get("error"),
    }
