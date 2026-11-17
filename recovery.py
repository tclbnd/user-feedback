"""Crash recovery: ARIES-style analysis, redo and undo.

Analysis  - start from the last complete checkpoint, rebuild the transaction
            table (ATT) and dirty page table (DPT).
Redo      - repeat history from the oldest recLSN; torn pages are rebuilt from
            the full-page images (FPI) logged before their first update.
Undo      - roll back every transaction that never committed, in reverse LSN
            order, writing CLR records so that a crash inside undo can be
            resumed by simply running recovery again.
"""
import os

import pager
from wal import Log


class CrashPoint(Exception):
    """Raised to simulate a power loss during the undo pass."""


# Log record types that carry a transaction id and belong to its prev chain.
_TX_TYPES = ("BEGIN", "UPDATE", "COMMIT", "ABORT", "CLR")


def recover(directory, crash_after_undo=None):
    pages = pager.PageFile(os.path.join(directory, "pages.db"))
    log = Log(os.path.join(directory, "wal.log"))
    records = log.records()
    by_lsn = {r["lsn"]: r for r in records}

    # ------------------------------------------------------------------ #
    # Analysis: locate the last complete checkpoint and rebuild ATT / DPT.
    # ------------------------------------------------------------------ #
    checkpoint_lsn = 0
    ckpt_end = None
    for r in records:  # forward scan, keep the last well-formed checkpoint
        if r["type"] == "CKPT_BEGIN":
            begin_lsn = r["lsn"]
            end = next((e for e in records
                        if e["type"] == "CKPT_END" and e.get("begin") == begin_lsn),
                       None)
            if end is not None:
                checkpoint_lsn, ckpt_end = begin_lsn, end

    if ckpt_end is not None:
        att = {int(k): v for k, v in ckpt_end.get("att", {}).items()}
        dpt = {int(k): v for k, v in ckpt_end.get("dpt", {}).items()}
        scan_from = ckpt_end["lsn"]
    else:
        att, dpt, scan_from = {}, {}, 0

    committed = set()
    for r in records:
        if r["lsn"] <= scan_from:
            continue
        t = r.get("tx")
        if r["type"] == "FPI":
            continue
        if r["type"] in ("UPDATE", "CLR"):
            dpt.setdefault(r["page"], r["lsn"])
        if t is None:
            continue
        if r["type"] == "END":
            att.pop(t, None)
            continue
        att[t] = r["lsn"]
        if r["type"] == "COMMIT":
            committed.add(t)

    # -------------------------------------------------------------- #
    # Inspect the page file: find torn pages through their CRC and the
    # FPIs available to repair them.
    # -------------------------------------------------------------- #
    first_fpi = {}   # page -> LSN of its earliest FPI in the whole log
    for r in records:
        if r["type"] == "FPI":
            first_fpi.setdefault(r["page"], r["lsn"])

    torn = set()
    for pid in range(pages.count()):
        if pager.verify(pages.read_raw(pid)) == "torn":
            torn.add(pid)
            # A torn page must be rebuilt, so it is dirty at least as far back
            # as its first logged image.
            dpt[pid] = min(dpt.get(pid, 1 << 62), first_fpi.get(pid, 1))

    repaired_pages = sorted(torn)

    # Redo starts at the oldest recLSN in the DPT, but must also reach every
    # FPI that can repair a torn page (an FPI may predate the checkpoint).
    redo_start = min(dpt.values()) if dpt else checkpoint_lsn
    scan_start = redo_start
    for pid in torn:
        if pid in first_fpi:
            scan_start = min(scan_start, first_fpi[pid])

    buffer = {}

    def load(pid):
        if pid not in buffer:
            raw = pages.read_raw(pid)
            if pager.verify(raw) == "torn":
                # Do not trust a torn page; start from an empty page and let
                # the redo pass rebuild it from its FPI + log records.
                buffer[pid] = pager.empty(pid)
            else:
                buffer[pid] = pager.decode(raw, pid)
        return buffer[pid]

    # ------------------------------------------------------------------ #
    # Redo: repeat history, applying every update whose effects are missing.
    # ------------------------------------------------------------------ #
    if dpt:
        for r in records:
            if r["lsn"] < scan_start:
                continue
            kind = r["type"]
            if kind == "FPI":
                pid = r["page"]
                p = load(pid)
                image = bytes.fromhex(r["image"])
                if pager.verify(image) != "ok" or p["lsn"] < r["lsn"]:
                    buffer[pid] = pager.decode(image, pid)
            elif kind in ("UPDATE", "CLR"):
                pid = r["page"]
                if r["lsn"] < dpt.get(pid, 1 << 62):
                    continue  # page already contained this change at crash
                p = load(pid)
                if p["lsn"] < r["lsn"]:
                    p["slots"][r["slot"]] = r["after"]
                    p["lsn"] = r["lsn"]

        # Persist the repeated history before starting undo; WAL-first makes
        # this write order crash safe.
        for pid in sorted(buffer):
            pages.write_raw(pid, pager.encode(buffer[pid]))

    # ------------------------------------------------------------------ #
    # Undo: roll back every transaction without a COMMIT, newest LSN first.
    # ------------------------------------------------------------------ #
    losers = sorted((tx for tx in att if tx not in committed),
                    key=lambda tx: -att[tx])
    undo_next = {tx: att[tx] for tx in losers}
    clr_count = 0

    while undo_next:
        tx = max(undo_next, key=undo_next.get)  # highest LSN across all losers
        lsn = undo_next.pop(tx)
        r = by_lsn.get(lsn)
        if r is None:
            # Record missing from a truncated log: nothing more to undo.
            continue

        if r["type"] == "UPDATE":
            p = load(r["page"])
            clr = log.append({
                "type": "CLR", "tx": tx, "prev": lsn, "page": r["page"],
                "slot": r["slot"], "after": r["before"],
                "undo_next": r["prev"],
            })
            p["slots"][r["slot"]] = r["before"]
            p["lsn"] = clr["lsn"]
            clr_count += 1
            next_lsn = r["prev"]
            if next_lsn:
                undo_next[tx] = next_lsn
            else:
                log.append({"type": "END", "tx": tx, "prev": clr["lsn"]})
            # Simulated power loss: the CLR is already fsynced, but none of
            # the undo page writes have happened yet. A second recovery run
            # redoes the CLR and resumes the rollback.
            if crash_after_undo is not None and clr_count >= crash_after_undo:
                raise CrashPoint()
        elif r["type"] == "CLR":
            # Already undone on a previous recovery attempt: follow its chain.
            nxt = r.get("undo_next", 0)
            if nxt:
                undo_next[tx] = nxt
            else:
                log.append({"type": "END", "tx": tx, "prev": lsn})
        elif r["type"] in ("BEGIN", "ABORT"):
            log.append({"type": "END", "tx": tx, "prev": lsn})
        elif r["type"] in _TX_TYPES:  # COMMIT without END, etc.
            log.append({"type": "END", "tx": tx, "prev": lsn})

    # A committed transaction whose END was lost with the log tail is finished
    # off here so the next checkpoint starts from a clean ATT.
    for tx, lsn in list(att.items()):
        last = by_lsn.get(lsn, {})
        if last.get("type") == "COMMIT" and tx not in losers:
            log.append({"type": "END", "tx": tx, "prev": lsn})

    # Final write-back of all buffered (possibly undone) pages.
    for pid in sorted(buffer):
        pages.write_raw(pid, pager.encode(buffer[pid]))

    return {
        "checkpoint": checkpoint_lsn,
        "redo_start": redo_start,
        "repaired_pages": repaired_pages,
        "undone": sorted(losers),
        "clrs": clr_count,
    }
