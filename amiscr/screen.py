"""Amiga OCS screen dumps (.ascr): BPLCON0/BPLCON2, colour registers, bitplanes, display modes."""
import struct


class ScreenError(ValueError):
    pass


# BPLCON0 bits
BPL0_HAM = 1 << 11       # hold-and-modify (HAM6)
BPL0_DPF = 1 << 10       # dual playfield
BPL0_COLOR = 1 << 9      # six-bitplane / extra half-brite enable

# BPLCON2 bits
BPL2_PF2PRI = 1 << 10    # playfield 2 (even planes) shown in front


def _hex(c):
    return "#%02x%02x%02x" % ((c >> 8 & 15) * 17, (c >> 4 & 15) * 17, (c & 15) * 17)


def _half(c):
    """EHB: each 4-bit component loses its least significant bit before widening."""
    return "#%02x%02x%02x" % (
        ((c >> 8 & 15) >> 1) * 17,
        ((c >> 4 & 15) >> 1) * 17,
        ((c & 15) >> 1) * 17,
    )


def _runs(line):
    out = []
    for c in line:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def parse(data):
    if len(data) < 14 + 64 or data[:6] != b"AMISCR":
        raise ScreenError("not an Amiga screen dump")
    c0, c2, w, h = struct.unpack_from(">HHHH", data, 6)
    regs = list(struct.unpack_from(">32H", data, 14))
    planes = c0 >> 12 & 7
    if planes > 6:
        raise ScreenError("bad plane count")

    # HAM and dual playfield are mutually exclusive display options; HAM wins
    # if both bits are set. EHB only exists once six planes are enabled.
    if c0 & BPL0_HAM:
        mode = "ham6"
    elif c0 & BPL0_DPF:
        mode = "dpf"
    elif planes == 6 and c0 & BPL0_COLOR:
        mode = "ehb"
    else:
        mode = "normal"

    bpr = (w + 15) // 16 * 2
    psize = bpr * h
    body = data[78:]
    if len(body) < psize * planes:
        return {"mode": mode, "planes": planes, "width": w, "height": h, "complete": False,
                "colors_used": None, "first_line": None, "last_line": None}

    def bit(p, x, y):
        return body[p * psize + y * bpr + x // 8] >> (7 - x % 8) & 1

    seen, lines = set(), []
    for y in range(h):
        line = []
        # HAM keeps its modified colour for the rest of the scanline; every
        # scanline starts out holding the background register (COLOR00).
        hold = regs[0]
        for x in range(w):
            if mode == "ham6":
                v = 0
                for p in range(planes):
                    v |= bit(p, x, y) << p
                ctrl, d = v >> 4, v & 15
                if ctrl == 0:                      # load one of 16 registers
                    hold = regs[d]
                elif ctrl == 1:                    # modify blue
                    hold = (hold & 0xff0) | d
                elif ctrl == 2:                    # modify red
                    hold = (hold & 0x0ff) | (d << 8)
                else:                              # modify green
                    hold = (hold & 0xf0f) | (d << 4)
                col = _hex(hold)
            elif mode == "dpf":
                # Odd planes build playfield 1 (COLOR00..07), even planes
                # build playfield 2 (COLOR08..15). A zero index is
                # transparent, letting the playfield behind show through;
                # when both are transparent the background colour appears.
                i1 = i2 = 0
                k1 = k2 = 0
                for p in range(planes):
                    if p & 1:
                        i2 |= bit(p, x, y) << k2
                        k2 += 1
                    else:
                        i1 |= bit(p, x, y) << k1
                        k1 += 1
                front1 = not (c2 & BPL2_PF2PRI)
                if front1:
                    col = (_hex(regs[i1]) if i1 else
                           _hex(regs[8 + i2]) if i2 else _hex(regs[0]))
                else:
                    col = (_hex(regs[8 + i2]) if i2 else
                           _hex(regs[i1]) if i1 else _hex(regs[0]))
            else:
                # Plane 1 contributes the least significant index bit, so a
                # pixel's index is the six plane bits read low to high.
                v = 0
                for p in range(planes):
                    v |= bit(p, x, y) << p
                col = _half(regs[v & 31]) if mode == "ehb" and v >= 32 else _hex(regs[v & 31])
            seen.add(col)
            line.append(col)
        lines.append(line)
    return {"mode": mode, "planes": planes, "width": w, "height": h, "complete": True,
            "colors_used": len(seen), "first_line": _runs(lines[0]), "last_line": _runs(lines[-1])}
