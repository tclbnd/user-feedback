"""Deluxe Paint Animation (.anm) frame records: run, skip and copy operations onto one 8-bit picture."""
import zlib


class AnmError(ValueError):
    pass


class Exhausted(Exception):
    pass


def decode(pic, rec):
    """Apply one frame record; raises AnmError for records the player rejects."""
    if len(rec) < 7:
        raise AnmError("record is too short")
    p = 4
    d = 0
    end = len(pic)

    def byte():
        nonlocal p
        v = rec[p] if p < len(rec) else 0
        p += 1
        return v

    def op(copy, pixel, count):
        nonlocal d, p
        while count > 0:
            strip = count
            if copy:
                part = rec[p:p + strip]
                pic[d:d + len(part)] = part
                p += strip
            elif pixel >= 0:
                pic[d:d + strip] = bytes([pixel]) * strip
            d += strip
            count -= strip
            if d >= end:
                raise Exhausted

    try:
        while True:
            t = byte()
            count = t & 0x7F
            high = t >> 7
            if count:
                op(bool(high), -1, count)
            elif not high:
                pixel = byte()
                count = byte()
                op(False, pixel, count)
            else:
                word = byte() | byte() << 8
                count = word & 0x3FFF
                kind = word >> 14
                if not count:
                    break
                pixel = byte() if kind == 3 else -1
                op(kind == 2, pixel, count)
            if p >= len(rec):
                break
    except Exhausted:
        pass


def parse(data):
    if len(data) < 16 + 1024 or data[:8] != b"DPANMSEQ":
        raise AnmError("not a Deluxe Paint animation pack")
    w = int.from_bytes(data[8:10], "little")
    h = int.from_bytes(data[10:12], "little")
    count = int.from_bytes(data[12:14], "little")
    if not w or not h:
        raise AnmError("empty picture")
    pal = [(data[16 + 4 * k], data[16 + 4 * k + 1], data[16 + 4 * k + 2]) for k in range(256)]
    pos = 16 + 1024
    pic = bytearray(w * h)
    frames = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise AnmError("frame table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        rec = data[pos + 4:pos + 4 + n]
        if len(rec) < n:
            raise AnmError("frame data is truncated")
        pos += 4 + n
        try:
            decode(pic, rec)
            ok = True
        except AnmError:
            ok = False
        rgb = b"".join(bytes(pal[v]) for v in pic)
        frames.append({"ok": ok, "crc32": "%08x" % zlib.crc32(rgb)})
    return {"width": w, "height": h, "frames": frames}
