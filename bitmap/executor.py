"""Bitmap scans and the reference sequential scan.

Conditions: {"and": [...]}, {"or": [...]}, leaf {"col": c, "op": op, "value": v}
or {"col": c, "op": "is_null"}.  WHERE keeps a row only when the condition
is TRUE (SQL three-valued logic).
"""
from .btree import OPS
from .tidbitmap import LOSSY, TidBitmap


def truth(cond, row):
    if "and" in cond:
        vals = [truth(c, row) for c in cond["and"]]
        return False if False in vals else (None if None in vals else True)
    if "or" in cond:
        vals = [truth(c, row) for c in cond["or"]]
        return True if True in vals else (None if None in vals else False)
    v = row.get(cond["col"])
    if cond["op"] == "is_null":
        return v is None
    if v is None or cond["value"] is None:
        return None
    return OPS[cond["op"]](v, cond["value"])


def seq_scan(heap, cond):
    out = []
    for tid in heap.all_tids():
        item = heap.fetch(tid)
        if not item["dead"] and truth(cond, item["row"]) is True:
            out.append(item["row"]["id"])
    return sorted(out)


def build_bitmap(indexes, cond, work_mem):
    if "and" in cond:
        parts = [build_bitmap(indexes, c, work_mem) for c in cond["and"]]
        bm = parts[0]
        for p in parts[1:]:
            bm = bm.intersect(p)
        return bm
    if "or" in cond:
        parts = [build_bitmap(indexes, c, work_mem) for c in cond["or"]]
        bm = parts[0]
        for p in parts[1:]:
            bm = bm.union(p)
        return bm
    index = indexes[cond["col"]]
    return TidBitmap.from_tids(index.scan(cond["op"], cond.get("value")), work_mem)


def bitmap_scan(heap, indexes, cond, work_mem=64):
    bm = build_bitmap(indexes, cond, work_mem)
    out, rechecked = [], 0
    for page in sorted(bm.pages):
        slots = bm.pages[page]
        if slots == LOSSY:
            for slot, item in enumerate(heap.page(page)):
                rechecked += 1
                if not item["dead"] and truth(cond, item["row"]) is True:
                    out.append(item["row"]["id"])
            continue
        for slot in sorted(slots):
            item = heap.fetch((page, slot))
            if page in bm.recheck:
                rechecked += 1
                if truth(cond, item["row"]) is not True:
                    continue
            out.append(item["row"]["id"])
    return {"ids": sorted(out), "pages": len(bm.pages), "lossy": len(bm.lossy_pages()), "rechecked": rechecked}
