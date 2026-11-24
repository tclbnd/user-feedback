"""Impulse Tracker modules (.it): header, orders, channels, samples, patterns, song message."""
import struct


class ItError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("cp437").rstrip() or None


def version(v):
    return str(v)


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
    played = orders
    samples = []
    for off in smp_off:
        if data[off:off + 4] != b"IMPS":
            raise ItError("bad sample header at %d" % off)
        length, lb, le, c5 = struct.unpack_from("<IIII", data, off + 48)
        samples.append({"name": text(data[off + 20:off + 46]), "length": length, "bits": 8,
                        "stereo": False, "c5_speed": 8363, "loop": None,
                        "sustain_loop": None, "compressed": False,
                        "data_bytes": length})
    patterns = []
    for off in pat_off:
        if off == 0:
            patterns.append(0)
        else:
            patterns.append(struct.unpack_from("<H", data, off + 2)[0])
    message = None
    return {"title": text(data[4:30]), "tracker": version(cwt), "compatible": version(cmwt),
            "stereo": bool(flags & 1), "instrument_mode": bool(flags & 4), "linear_slides": bool(flags & 8),
            "global_volume": gv, "mix_volume": mv, "speed": speed, "tempo": tempo,
            "orders": played, "channels": sum(1 for v in pans if v != 0),
            "surround_channels": 0, "instruments": insnum,
            "samples": samples, "pattern_rows": patterns, "message": message}
