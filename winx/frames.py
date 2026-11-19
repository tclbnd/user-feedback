"""Window frames.

A window: {"partition_by": [cols], "order_by": [{"col", "dir"}],
           "frame": {"mode": "rows"|"range"|"groups",
                     "start": {"type": "unbounded_preceding"|"preceding"|"current_row"|"following"|"unbounded_following", "offset": n},
                     "end": {...}, "exclude": "no_others"|"current_row"|"group"|"ties"} or null}
With no frame clause the SQL default applies.
"""


def default_frame(window):
    return {"mode": "rows", "start": {"type": "unbounded_preceding"},
            "end": {"type": "current_row" if window.get("order_by") else "unbounded_following"},
            "exclude": "no_others"}


def _bound(b, i, n, keys, mode):
    t = b["type"]
    if t == "unbounded_preceding":
        return 0
    if t == "unbounded_following":
        return n - 1
    if t == "current_row":
        return i
    off = b.get("offset", 0)
    return i - off if t == "preceding" else i + off


def frame_rows(window, part, i, keys):
    """Indexes (into the ordered partition) of the rows in row i's frame."""
    f = window.get("frame") or default_frame(window)
    n = len(part)
    lo = max(0, _bound(f["start"], i, n, keys, f["mode"]))
    hi = min(n - 1, _bound(f["end"], i, n, keys, f["mode"]))
    rows = list(range(lo, hi + 1))
    if f.get("exclude", "no_others") == "current_row":
        rows = [j for j in rows if j != i]
    return rows
