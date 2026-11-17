"""Crash recovery: analysis, redo, undo."""
import os

import pager
from wal import Log


class CrashPoint(Exception):
    pass


def recover(directory, crash_after_undo=None):
    pages = pager.PageFile(os.path.join(directory, "pages.db"))
    log = Log(os.path.join(directory, "wal.log"))
    records = log.records()
    buffer = {}

    def page(pid):
        if pid not in buffer:
            buffer[pid] = pages.read(pid)
        return buffer[pid]

    start = max([r["lsn"] for r in records if r["type"] == "CKPT_BEGIN"] + [0])
    att, committed = {}, set()
    for r in records:
        if r["lsn"] < start:
            continue
        if r["type"] == "CKPT_END" and r["begin"] == start:
            att.update({int(k): v for k, v in r["att"].items()})
        tx = r.get("tx")
        if tx is None:
            continue
        if r["type"] == "END":
            att.pop(tx, None)
        else:
            att[tx] = r["lsn"]
            if r["type"] == "COMMIT":
                committed.add(tx)

    for r in records:
        if r["lsn"] >= start and r["type"] in ("UPDATE", "CLR"):
            p = page(r["page"])
            if p["lsn"] < r["lsn"]:
                p["slots"][r["slot"]], p["lsn"] = r["after"], r["lsn"]

    losers = sorted(tx for tx in att if tx not in committed)
    undone = 0
    for r in records:
        if r["type"] == "UPDATE" and r["tx"] in losers:
            page(r["page"])["slots"][r["slot"]] = r["before"]
            undone += 1
            if crash_after_undo is not None and undone >= crash_after_undo:
                raise CrashPoint()

    for p in buffer.values():
        pages.write_raw(p["id"], pager.encode(p))
    return {"checkpoint": start, "redo_start": max(start, 1), "repaired_pages": [],
            "undone": losers, "clrs": 0}
