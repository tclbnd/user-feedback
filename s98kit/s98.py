"""S98 sound logs (PC-98 / FM chips): header v1-v3, device list, sync commands, loop point, [S98] tags."""
import struct
from fractions import Fraction

DEVICES = {0: "none", 1: "ym2149", 2: "ym2203", 3: "ym2612", 4: "ym2608", 5: "ym2151", 6: "ym2413", 7: "ym3526",
           8: "ym3812", 9: "ymf262", 15: "ay-3-8910", 16: "sn76489"}


class S98Error(ValueError):
    pass


def parse(data):
    if data[:3] != b"S98" or len(data) < 0x20:
        raise S98Error("not an S98 log")
    version = chr(data[3])
    num, den, _, tag_off, dump_off, loop_off = struct.unpack_from("<6I", data, 4)
    num, den = num or 10, den or 1000                     # defaults: 10 / 1000 seconds per sync
    tick = Fraction(den, num)
    devices = []
    if version == "3":
        count, = struct.unpack_from("<I", data, 0x1C)
        for i in range(count):
            t, clock, pan, _ = struct.unpack_from("<4I", data, 0x20 + 16 * i)
            devices.append({"type": DEVICES.get(t, "type_%d" % t), "clock": clock, "pan": pan})
    writes = [0] * len(devices)
    p, n = dump_off, len(data)
    syncs = 0
    loop_syncs = None
    ended = False
    while p < n:
        c = data[p]
        if c == 0xFF:
            syncs += 1
            p += 1
        elif c == 0xFE:                                   # n syncs: 7-bit groups, least significant first, plus 2
            p += 1
            v = 0
            while True:
                b = data[p]
                p += 1
                v = v << 7 | (b & 0x7F)
                if not b & 0x80:
                    break
            syncs += v
        elif c == 0xFD:
            ended = True
            break
        elif c < 0x80:
            dev = c & 1
            if dev >= len(devices):
                raise S98Error("write to device %d at %d" % (dev, p))
            writes[dev] += 1
            p += 3
        else:
            raise S98Error("unknown command 0x%02X at %d" % (c, p))
    tags = {}
    return {"version": version, "tick": str(tick), "devices": devices, "writes": writes, "syncs": syncs,
            "duration": str(syncs * tick), "loop_seconds": str(loop_syncs * tick) if loop_syncs is not None else None,
            "end_marker": ended, "tags": tags}
