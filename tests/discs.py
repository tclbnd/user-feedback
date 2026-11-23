"""Small IFO files for the tests."""
import struct

SECTOR = 2048
CODES = {"AC-3": 0, "MPEG-1": 2, "MPEG-2": 3, "LPCM": 4, "DTS": 6}


def bcd(v):
    return ((v // 10) << 4) | (v % 10)


def ptime(seconds_frames, fps=25):
    """(hh, mm, ss, ff) -> 4 BCD bytes with the frame-rate flag in the top bits of ff."""
    hh, mm, ss, ff = seconds_frames
    flag = 0x40 if fps == 25 else 0xC0
    return bytes([bcd(hh), bcd(mm), bcd(ss), flag | bcd(ff)])


def cell(t, fps=25):
    return bytes(4) + ptime(t, fps) + bytes(16)


def pgc(total, cells, entries, fps=25):
    """cells: list of time tuples; entries: first cell number of each program."""
    head = bytearray(0xEC)
    head[2], head[3] = len(entries), len(cells)
    head[4:8] = ptime(total, fps)
    pmap = len(head)
    body = bytes(entries) + bytes((-len(entries)) % 2)
    cpb = pmap + len(body)
    struct.pack_into(">HHH", head, 0xE6, pmap, cpb, 0)
    return bytes(head) + body + b"".join(cell(t, fps) for t in cells)


def ifo(pgcs, titles, audios=(), subs=(), pal=True, wide=False):
    """titles: list of chapter lists [(pgcn, pgn), ...]."""
    head = bytearray(SECTOR)
    head[0:12] = b"DVDVIDEO-VTS"
    struct.pack_into(">II", head, 0xC8, 1, 2)
    struct.pack_into(">H", head, 0x200, ((1 if pal else 0) << 12) | ((3 if wide else 0) << 10))
    struct.pack_into(">H", head, 0x202, len(audios))
    for i, a in enumerate(audios):
        head[0x204 + 8 * i:0x20C + 8 * i] = a
    struct.pack_into(">H", head, 0x254, len(subs))
    for i, s in enumerate(subs):
        head[0x256 + 6 * i:0x25C + 6 * i] = s
    ptt = bytearray(8 + 4 * len(titles))
    for i, t in enumerate(titles):
        struct.pack_into(">I", ptt, 8 + 4 * i, len(ptt))
        ptt += b"".join(struct.pack(">HH", a, b) for a, b in t)
    struct.pack_into(">HHI", ptt, 0, len(titles), 0, len(ptt) - 1)
    ptt = bytes(ptt).ljust(SECTOR, b"\0")
    pgci = bytearray(8 + 8 * len(pgcs))
    for i, p in enumerate(pgcs):
        struct.pack_into(">II", pgci, 8 + 8 * i, 0x81000000 + i, len(pgci))
        pgci += p
    struct.pack_into(">HHI", pgci, 0, len(pgcs), 0, len(pgci) - 1)
    return bytes(head) + ptt + bytes(pgci).ljust(SECTOR * ((len(pgci) + SECTOR - 1) // SECTOR), b"\0")
