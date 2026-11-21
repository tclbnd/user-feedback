"""FastTracker II extended modules (.xm): header, patterns, instruments, samples, play time."""
import struct
from fractions import Fraction

MAGIC = b"Extended Module: "
HEADER_END = 336            # 60 bytes of fixed fields + 276-byte song header
INSTRUMENT_HEADER = 263
SAMPLE_HEADER = 40


class XmError(ValueError):
    pass


def _pattern(data, pos):
    hlen, packing, rows, size = struct.unpack_from("<IBHH", data, pos)
    body = pos + hlen
    end = body + size
    if end > len(data):
        raise XmError("pattern data runs past the end of the file")
    cells = [tuple(data[p:p + 5]) for p in range(body, end, 5)]
    return {"rows": rows, "cells": cells, "end": end}


def _instrument(data, pos):
    name = data[pos + 4:pos + 26].split(b"\0")[0].decode("latin-1").rstrip()
    nsamp, = struct.unpack_from("<H", data, pos + 27)
    p = pos + INSTRUMENT_HEADER
    heads = []
    for i in range(nsamp):
        h = p + i * SAMPLE_HEADER
        length, lstart, llen = struct.unpack_from("<III", data, h)
        vol, fine, typ, pan, rel = struct.unpack_from("<BBBBB", data, h + 12)
        sname = data[h + 18:h + 40].split(b"\0")[0].decode("latin-1").rstrip()
        heads.append((sname, length, lstart, llen, vol, fine, typ, rel))
    p += nsamp * SAMPLE_HEADER
    samples = []
    for sname, length, lstart, llen, vol, fine, typ, rel in heads:
        loop = "forward" if llen else "none"
        raw = data[p:p + length]
        samples.append({"name": sname, "frames": length, "bits": 16 if typ & 0x10 else 8, "loop": loop,
                        "peak": max((abs(b - 256 if b > 127 else b) for b in raw), default=0),
                        "loop_start": lstart if llen else 0, "loop_length": llen,
                        "volume": vol, "finetune": fine, "relative_note": rel})
        p += length
    return {"name": name, "samples": samples}, p


def _duration(orders, pats, channels, speed, bpm):
    total = Fraction(0)
    for pi in orders:
        cells = pats[pi]["cells"] if pi < len(pats) else []
        for row in range(64):
            for note, ins, vol, eff, par in cells[row * channels:(row + 1) * channels]:
                if eff == 0xF and par:
                    if par < 32:
                        speed = par
                    else:
                        bpm = par
            total += Fraction(speed * 5, bpm * 2)
    return total


def parse(data):
    if not data.startswith(MAGIC) or len(data) < 80:
        raise XmError("not an XM module")
    name = data[17:37].split(b"\0")[0].decode("latin-1").rstrip()
    tracker = data[38:58].decode("latin-1").rstrip("\0 ")
    version, hsize = struct.unpack_from("<HI", data, 58)
    length, restart, channels, npat, ninst, flags, tempo, bpm = struct.unpack_from("<8H", data, 64)
    orders = list(data[80:80 + 256])[:length]
    pos = HEADER_END
    pats = []
    for _ in range(npat):
        pat = _pattern(data, pos)
        pats.append(pat)
        pos = pat["end"]
    insts = []
    for _ in range(ninst):
        inst, pos = _instrument(data, pos)
        insts.append(inst)
    notes = sum(1 for p in pats for c in p["cells"] if 1 <= c[0] <= 96)
    return {
        "name": name, "tracker": tracker, "version": "%d.%02d" % (version >> 8, version & 0xFF),
        "channels": channels, "song_length": length, "restart": restart,
        "frequency_table": "linear" if flags & 1 else "amiga",
        "patterns": [p["rows"] for p in pats], "notes": notes,
        "instruments": insts,
        "duration": str(_duration(orders, pats, channels, tempo, bpm)),
    }
