"""Window functions over one sorted partition."""
from frames import Frames

RUNNING = ("sum", "count", "avg")


def _direct(fn, values):
    present = [v for v in values if v is not None]
    if fn == "count":
        return len(present)
    if fn == "first_value":
        return values[0] if values else None
    if not present:
        return None
    return {"sum": sum, "min": min, "max": max, "avg": lambda v: sum(v) / len(v)}[fn](present)


def evaluate(rows, order_by, func, stats, recompute=False):
    """Values of one window function for every row of a sorted partition."""
    fn, arg, n = func["fn"], func.get("arg"), len(rows)
    frames = Frames(rows, order_by)
    if fn in ("row_number", "rank", "dense_rank"):
        out, rank, dense = [], 0, 0
        key = order_by[0]["col"] if order_by else None
        for i in range(n):
            if i == 0 or (key and rows[i].get(key) != rows[i - 1].get(key)):
                rank, dense = i + 1, dense + 1
            out.append(i + 1 if fn == "row_number" else rank if fn == "rank" else dense)
        return out
    if fn in ("lag", "lead"):
        step = func.get("offset", 1) * (-1 if fn == "lag" else 1)
        return [rows[i + step].get(arg) if 0 <= i + step < n else func.get("default") for i in range(n)]
    default = {"mode": "range", "start": ["unbounded_preceding"], "end": ["current"]} if order_by else \
        {"mode": "rows", "start": ["unbounded_preceding"], "end": ["unbounded_following"]}
    frame = func.get("frame", default)
    values = [r.get(arg) if arg else 1 for r in rows]
    out = []
    for i in range(n):
        start, end = frames.bounds(i, frame)
        window = values[max(start, 0):end + 1] if start <= end else []
        if fn in RUNNING:
            stats["agg_updates"] += len(window)
            if fn == "count" and not arg:
                window = [1] * len(window)
        out.append(_direct(fn, window))
    return out
