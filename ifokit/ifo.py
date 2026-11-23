"""DVD-Video title set information (VTS_xx_0.IFO): attributes, program chains, cells, chapters."""
import struct
from fractions import Fraction

SECTOR = 2048
CODECS = {0: "AC-3", 2: "MPEG-1", 3: "MPEG-2", 4: "LPCM"}


class IfoError(ValueError):
    pass


def bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def ptime(raw):
    """DVD playback time: BCD hh mm ss ff; the top two bits of ff give the frame rate (01 = 25, 11 = 30)."""
    hh, mm, ss, ff = raw
    return Fraction(bcd(hh) * 3600 + mm * 60 + ss) + Fraction(ff & 0x3F, 30)


def lang(b):
    code = b.decode("latin-1")
    return code if code.isalpha() else None


def pgc(data, at):
    programs, cells = data[at + 2], data[at + 3]
    total = ptime(data[at + 4:at + 8])
    pmap, cpb = struct.unpack_from(">HH", data, at + 0xE6)
    entries = [data[at + pmap + i] for i in range(programs)] if programs else []
    cell_times = []
    for i in range(cells):
        c = at + cpb + 24 * i
        mode, kind = data[c] >> 6, (data[c] >> 4) & 3
        t = ptime(data[c + 4:c + 8])
        cell_times.append(t)
    starts = [sum(cell_times[:p], Fraction(0)) for p in range(len(entries))]
    return {"programs": programs, "cells": cells, "duration": str(total),
            "cell_total": str(sum(cell_times, Fraction(0))), "program_starts": [str(s) for s in starts]}


def parse(data):
    if data[:12] != b"DVDVIDEO-VTS":
        raise IfoError("not a DVD video title set IFO")
    ptt_sector, pgci_sector = struct.unpack_from(">II", data, 0xC8)
    video = struct.unpack_from(">H", data, 0x200)[0]
    out = {"standard": "PAL" if (video >> 12) & 3 == 1 else "NTSC",
           "aspect": "16:9" if (video >> 10) & 3 == 2 else "4:3", "audio": [], "subpictures": []}
    for i in range(min(struct.unpack_from(">H", data, 0x202)[0], 8)):
        a = 0x204 + 8 * i
        b0, b1 = data[a], data[a + 1]
        out["audio"].append({"codec": CODECS.get(b0 >> 5, "type_%d" % (b0 >> 5)),
                             "channels": b1 & 7, "sample_rate": 48000,
                             "language": lang(data[a + 2:a + 4])})
    for i in range(min(struct.unpack_from(">H", data, 0x254)[0], 32)):
        s = 0x256 + 6 * i
        out["subpictures"].append(lang(data[s + 2:s + 4]))
    base = pgci_sector * SECTOR
    if base + 8 > len(data):
        raise IfoError("program chain table outside the file")
    count = struct.unpack_from(">H", data, base)[0]
    out["pgcs"] = []
    for i in range(count):
        off = struct.unpack_from(">I", data, base + 8 + 8 * i + 4)[0]
        out["pgcs"].append(pgc(data, base + off))
    tbase = ptt_sector * SECTOR
    titles = []
    ntitles, _, end = struct.unpack_from(">HHI", data, tbase)
    offs = [struct.unpack_from(">I", data, tbase + 8 + 4 * i)[0] for i in range(ntitles)] + [end + 1]
    for i in range(ntitles):
        chapters = []
        for p in range(tbase + offs[i], tbase + offs[i + 1], 4):
            pgcn, pgn = struct.unpack_from(">HH", data, p)
            chapters.append(out["pgcs"][pgcn - 1]["program_starts"][pgn - 1])
        titles.append({"chapters": len(chapters), "chapter_starts": chapters})
    out["titles"] = titles
    return out
