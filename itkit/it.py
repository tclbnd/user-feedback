"""Impulse Tracker modules (.it): header, orders, channels, samples, patterns, song message."""
import struct


class ItError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("cp437").rstrip() or None


def version(v):
    """Format an IT tracker version, stored as 0xMMmm in BCD (0x0214 -> 2.14)."""
    return "%d.%02d" % (v >> 8, ((v >> 4) & 0x0F) * 10 + (v & 0x0F))


def loop(flags, bit_on, bit_pp):
    if not flags & bit_on:
        return None
    return "pingpong" if flags & bit_pp else "forward"


def parse(data):
    if len(data) < 192 or data[:4] != b"IMPM":
        raise ItError("not an Impulse Tracker module")
    ordnum, insnum, smpnum, patnum, cwt, cmwt, flags, special = struct.unpack_from("<8H", data, 32)
    gv, mv, speed, tempo, sep = data[48:53]
    msglen, msgoff = struct.unpack_from("<HI", data, 54)
    pans = data[64:128]
    p = 192
    orders = list(data[p:p + ordnum])
    p += ordnum
    ins_off = struct.unpack_from("<%dI" % insnum, data, p); p += 4 * insnum
    smp_off = struct.unpack_from("<%dI" % smpnum, data, p); p += 4 * smpnum
    pat_off = struct.unpack_from("<%dI" % patnum, data, p)
    played = []
    for n in orders:
        if n == 255:            # end of song
            break
        if n == 254:            # separator marker
            continue
        played.append(n)
    samples = []
    for off in smp_off:
        if data[off:off + 4] != b"IMPS":
            raise ItError("bad sample header at %d" % off)
        flg, vol = data[off + 18], data[off + 19]
        length, lb, le, c5 = struct.unpack_from("<IIII", data, off + 48)
        width = 2 if flg & 0x02 else 1
        chans = 2 if flg & 0x04 else 1
        compressed = bool(flg & 0x08)
        # Bit 0 marks a header that carries sample data; length can still be 0.
        data_bytes = None
        if not compressed and flg & 0x01 and length:
            data_bytes = length * width * chans
        samples.append({"name": text(data[off + 20:off + 46]), "length": length, "bits": 8 * width,
                        "stereo": bool(flg & 0x04), "c5_speed": c5, "loop": loop(flg, 0x10, 0x40),
                        "sustain_loop": loop(flg, 0x20, 0x80), "compressed": compressed,
                        "data_bytes": data_bytes})
    patterns = []
    for off in pat_off:
        if off == 0:
            patterns.append(64)     # pattern not stored: empty, 64 rows
        else:
            rows = struct.unpack_from("<H", data, off + 2)[0]
            patterns.append(rows or 64)     # a stored row count of 0 means 64
    message = None
    if special & 1 and msglen:
        message = (data[msgoff:msgoff + msglen].split(b"\0")[0].decode("cp437")
                   .replace("\r\n", "\n").replace("\r", "\n") or None)
    enabled = [v for v in pans if v < 128]
    return {"title": text(data[4:30]), "tracker": version(cwt), "compatible": version(cmwt),
            "stereo": bool(flags & 1), "instrument_mode": bool(flags & 4), "linear_slides": bool(flags & 8),
            "global_volume": gv, "mix_volume": mv, "speed": speed, "tempo": tempo,
            "orders": played, "channels": len(enabled),
            "surround_channels": sum(1 for v in enabled if v == 100), "instruments": insnum,
            "samples": samples, "pattern_rows": patterns, "message": message}
