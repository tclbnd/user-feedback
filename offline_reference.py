"""Independent offline reference implementation.

This module deliberately does NOT import sessions.py.  It re-derives the
expected final materialized rows from the raw source with straightforward
batch processing, so tests can compare two independent implementations.

Rules mirror the README contract:
  * per-partition watermarks never move backwards;
  * idle partitions are excluded from the global minimum; resuming one never
    lowers the global watermark; all-idle / no-report keeps the old value;
  * duplicate event ids are ignored;
  * an event with t + gap + lateness < global watermark is expired (late list);
  * accepted events are grouped per key; adjacent events with distance <= gap
    belong to the same session;
  * only sessions with end + gap <= final global watermark are visible.
"""
import json


def simulate(source):
    gap = source["gap"]
    lateness = source["lateness"]

    watermarks = {}
    idle = set()
    global_watermark = None
    seen = set()
    accepted = []
    late = []

    def recompute():
        nonlocal global_watermark
        values = [
            wm for name, wm in watermarks.items()
            if name not in idle and wm is not None
        ]
        if values:
            candidate = min(values)
            if global_watermark is None or candidate > global_watermark:
                global_watermark = candidate

    for event in source["events"]:
        kind = event["kind"]
        if kind == "watermark":
            name = event["partition"]
            old = watermarks.get(name)
            if old is None or event["t"] > old:
                watermarks[name] = event["t"]
            recompute()
        elif kind == "idle":
            idle.add(event["partition"])
            watermarks.setdefault(event["partition"], None)
            recompute()
        elif kind == "resume":
            idle.discard(event["partition"])
            watermarks.setdefault(event["partition"], None)
            recompute()
        elif kind == "event":
            name = event["partition"]
            watermarks.setdefault(name, None)
            event_id = event["id"]
            if event_id in seen:
                continue
            seen.add(event_id)
            if (
                global_watermark is not None
                and event["t"] + gap + lateness < global_watermark
            ):
                late.append(event_id)
                continue
            accepted.append((event["key"], event["t"], event_id))

    grouped = {}
    for key, t, event_id in accepted:
        grouped.setdefault(key, []).append((t, event_id))

    rows = []
    for key in sorted(grouped):
        entries = sorted(grouped[key], key=lambda item: (item[0], item[1]))
        current_start = None
        current_end = None
        ids = []
        for t, event_id in entries:
            if current_start is None:
                current_start = current_end = t
                ids = [event_id]
            elif t - current_end <= gap:
                current_end = t
                ids.append(event_id)
            else:
                if global_watermark is not None and current_end + gap <= global_watermark:
                    rows.append(_row(key, current_start, current_end, ids))
                current_start = current_end = t
                ids = [event_id]
        if current_start is not None:
            if global_watermark is not None and current_end + gap <= global_watermark:
                rows.append(_row(key, current_start, current_end, ids))

    rows.sort(
        key=lambda r: (r["key"], r["start"], r["end"], tuple(r["event_ids"]))
    )
    return {"watermark": global_watermark, "rows": rows, "late": late}


def _row(key, start, end, ids):
    return {
        "key": key,
        "start": start,
        "end": end,
        "event_ids": sorted(ids),
    }


if __name__ == "__main__":
    import sys

    with open(sys.argv[1]) as handle:
        source = json.load(handle)
    print(json.dumps(simulate(source), sort_keys=True))
