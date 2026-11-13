"""Temporary byte diagnostic helper."""
import sys

data = open(sys.argv[1], "rb").read()


def rid(d, o):
    first = d[o]
    n = 0
    for i in range(1, 9):
        if first & (1 << (8 - i)):
            n = i
            break
    return n, int.from_bytes(d[o:o + n], "big")


def rsize(d, o):
    first = d[o]
    n = 0
    for i in range(1, 9):
        if first & (1 << (8 - i)):
            n = i
            break
    raw = int.from_bytes(d[o:o + n], "big")
    val = raw & ((1 << (7 * n)) - 1)
    unk = val == (1 << (7 * n)) - 1
    return n, val, unk


def uint(raw):
    return int.from_bytes(raw, "big") if raw else 0


def walk(d, start, end, depth, show_all=True):
    o = start
    while o < end:
        n, eid = rid(d, o)
        n2, sz, unk = rsize(d, o + n)
        p = o + n + n2
        body = d[p:p + sz]
        print("%s%6d id=%#x n=%d size=%d val=%s" %
              ("  " * depth, o, eid, n, sz,
               body[:40].hex() + ("..." if sz > 40 else "") if show_all else ""))
        if eid in (0x114D9B74, 0x1C53BB6B, 0x1549A966, 0x1654AE6B):
            walk(d, p, p + sz, depth + 1)
        if eid == 0xAE:
            walk(d, p, p + sz, depth + 1)
        if eid in (0x4DBB, 0xBB):
            walk(d, p, p + sz, depth + 1)
        o = p + sz


walk(data, 52, 545, 0)
print("CUES")
walk(data, 50532, 50651, 0)

