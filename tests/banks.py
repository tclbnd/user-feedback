"""Minimal DLS banks used by the tests."""
import struct


def ck(cid, body):
    # RIFF pads an odd-sized chunk body with one byte.
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) & 1 else b"")


def wsmp(unity=60, gain=0, loops=0, loop_type=0, start=0, length=0):
    head = struct.pack("<IHhiII", 20 + 16 * loops, unity, 0, gain, 0, loops)
    if loops:
        head += struct.pack("<IIII", 16, loop_type, start, length)
    return ck(b"wsmp", head)


def wave(frames=100, name=None, with_wsmp=None):
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
    body = ck(b"fmt ", fmt)
    if with_wsmp is not None:
        body += wsmp(**with_wsmp)
    body += ck(b"data", bytes(frames * 2))
    if name is not None:
        body += ck(b"LIST", b"INFO" + ck(b"INAM", name.encode("latin-1")))
    return ck(b"LIST", b"wave" + body)


def region(lo=0, hi=127, link=0, rgn2=False, with_wsmp=None, name=None):
    kind = b"rgn2" if rgn2 else b"rgn "
    body = ck(b"rgnh", struct.pack("<6H", lo, hi, 0, 127, 0, 0))
    if with_wsmp is not None:
        body += wsmp(**with_wsmp)
    body += ck(b"wlnk", struct.pack("<HHII", 0, 0, 1, link))
    if name is not None:
        body += ck(b"LIST", b"INFO" + ck(b"INAM", name.encode("latin-1")))
    return ck(b"LIST", kind + body)


def instrument(bank=0, program=5, regions=(), name=None):
    body = ck(b"insh", struct.pack("<III", len(regions), bank, program))
    body += ck(b"LIST", b"lrgn" + b"".join(regions))
    if name is not None:
        body += ck(b"LIST", b"INFO" + ck(b"INAM", name.encode("latin-1")))
    return ck(b"LIST", b"ins " + body)


def build(instruments, waves, bank_name=None):
    """Assemble a bank; the pool table maps region link i to wave i in order."""
    cues, pool, running = [], b"", 0
    for w in waves:
        cues.append(running)
        # Cues are measured from the pool's wave data (8 bytes into its
        # body); each subsequent wave follows the previous one directly.
        running += len(w)
        pool += w
    n = len(waves)
    ptbl = ck(b"ptbl", struct.pack("<II", 8 + 4 * n, n) + b"".join(struct.pack("<I", c) for c in cues))
    body = b"DLS " + ck(b"colh", struct.pack("<I", len(instruments)))
    body += ck(b"LIST", b"lins" + b"".join(instruments))
    body += ptbl + ck(b"LIST", b"wvpl" + pool)
    if bank_name is not None:
        body += ck(b"LIST", b"INFO" + ck(b"INAM", bank_name.encode("latin-1")))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def bank(frames=100):
    rgn = region(link=0)
    ins = instrument(program=5, regions=[rgn])
    return build([ins], [wave(frames)])
