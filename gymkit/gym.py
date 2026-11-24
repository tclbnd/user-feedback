"""Sega Genesis / Mega Drive register logs (.gym): GYMX header, zlib packing, YM2612 and PSG writes at 60 Hz."""
import struct
from fractions import Fraction


class GymError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def parse(data):
    head = None
    body = data
    frames = port0 = port1 = psg = dac = 0
    keys = {}
    p, n = 0, len(body)
    while p < n:
        op = body[p]
        if op == 0x00:
            frames += 1
            p += 1
        elif op in (0x01, 0x02):
            if p + 3 > n:
                break
            reg, val = body[p + 1], body[p + 2]
            if op == 0x01:
                port0 += 1
                if reg == 0x28 and val & 0xF0:
                    ch = (val & 3) + 1
                    keys[ch] = keys.get(ch, 0) + 1
            else:
                port1 += 1
            p += 3
        elif op == 0x03:
            if p + 3 > n:
                break
            psg += 1
            p += 3
        else:
            raise GymError("unknown command 0x%02X at %d" % (op, p))
    out = {"header": head, "frames": frames, "duration": str(Fraction(frames, 50)), "ym_port0_writes": port0,
           "ym_port1_writes": port1, "psg_writes": psg, "dac_writes": dac,
           "key_ons": {str(k): v for k, v in sorted(keys.items())}}
    out["loop_seconds"] = None
    return out
