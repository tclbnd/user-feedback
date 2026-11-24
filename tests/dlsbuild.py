"""Synthetic DLS Level 1 / Level 2 bank builders for the tests.

Chunks are RIFF-padded to even byte lengths, exactly like a DLS tool writes
them on disk.
"""
import struct


def ck(cid, body):
    pad = b"\0" if len(body) & 1 else b""
    return cid + struct.pack("<I", len(body)) + body + pad


def inam(text):
    return ck(b"LIST", b"INFO" + ck(b"INAM", text.encode("latin-1") + b"\0"))


def fmt(channels=1, align=2):
    return ck(b"fmt ", struct.pack("<HHIIHH", 1, channels, 22050, 44100, align, 16))


def wsmp(unity=60, gain=0, loops=0, loop_type=0, start=0, length=0):
    # the cbSize field covers the fixed 20-byte header only; WLOOP records
    # follow it and are announced by cLoopCount
    body = struct.pack("<IHhiII", 20, unity, 0, gain, 0, loops)
    if loops:
        body += struct.pack("<IIII", 16, loop_type, start, length)
    return ck(b"wsmp", body)


def wave(frames, name=None, with_wsmp=False, odd=False):
    # 'odd' asks for an odd byte count so the data chunk carries a pad byte
    nbytes = frames * 2 + (1 if odd else 0)
    body = fmt() + ck(b"data", bytes(nbytes))
    if with_wsmp:
        body += wsmp(unity=72, loops=1, start=10, length=20)
    if name is not None:
        body += inam(name)
    return ck(b"LIST", b"wave" + body)


def region(lo, hi, table_index, level2=False, own_wsmp=None, name=None):
    kind = b"rgn2" if level2 else b"rgn "
    body = ck(b"rgnh", struct.pack("<6H", lo, hi, 0, 127, 0, 0))
    if own_wsmp is not None:
        body += own_wsmp
    body += ck(b"wlnk", struct.pack("<HHII", 0, 0, 1, table_index))
    if name is not None:
        body += inam(name)
    return ck(b"LIST", kind + body)


def instrument(bank, program, regions, name=None, drums=False):
    if drums:
        bank |= 0x80000000
    body = ck(b"insh", struct.pack("<III", len(regions), bank, program))
    body += ck(b"LIST", b"lrgn" + b"".join(regions))
    if name is not None:
        body += inam(name)
    return ck(b"LIST", b"ins " + body)


def build(insts, waves, cue_order=None, version=None):
    """cue_order[i] says which pool wave entry i of the pool table points at."""
    if cue_order is None:
        cue_order = list(range(len(waves)))
    offsets, running = [], 0
    for w in waves:
        offsets.append(running)          # LIST header offset inside pool content
        running += len(w)
    cues = [offsets[i] for i in cue_order]
    ptbl = struct.pack("<II", 8 + 4 * len(cues), len(cues))
    ptbl += struct.pack("<%dI" % len(cues), *cues)
    body = b"DLS " + ck(b"colh", struct.pack("<I", len(insts)))
    if version is not None:
        body += ck(b"vers", struct.pack(
            "<II", version[0] << 16 | version[1], version[2] << 16 | version[3]))
    body += ck(b"LIST", b"lins" + b"".join(insts))
    body += ck(b"ptbl", ptbl)
    body += ck(b"LIST", b"wvpl" + b"".join(waves))
    return b"RIFF" + struct.pack("<I", len(body)) + body
