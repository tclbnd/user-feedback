"""VGM (Video Game Music) logs: header by version, chip clocks, command stream, loop point, GD3 tag."""
import struct
from fractions import Fraction

# (name, header offset, first version with the field)
CHIPS = [("SN76489", 0x0C, 0x100), ("YM2413", 0x10, 0x100), ("YM2612", 0x2C, 0x110), ("YM2151", 0x30, 0x110),
         ("SegaPCM", 0x38, 0x151), ("YM2203", 0x44, 0x151), ("YM2608", 0x48, 0x151), ("YM2610", 0x4C, 0x151),
         ("YM3812", 0x50, 0x151), ("AY8910", 0x74, 0x151), ("GameBoy", 0x80, 0x161), ("NES", 0x84, 0x161)]
DUAL_FLAG = 0x40000000
YM2610B_FLAG = 0x80000000
CLOCK_MASK = 0x3FFFFFFF
# GD3 strings: (output key, index of the English string in the tag)
GD3_EN = (("title", 0), ("game", 2), ("system", 4), ("author", 6), ("date", 8), ("ripper", 9), ("notes", 10))
FIXED = {0x4F: 1, 0x50: 1, 0x61: 2, 0x62: 0, 0x63: 0, 0x66: 0, 0x68: 11,
         0x90: 4, 0x91: 4, 0x92: 5, 0x93: 10, 0x94: 1, 0x95: 4, 0xE0: 4}


class VgmError(ValueError):
    pass


def _cmd_len(op):
    if op in FIXED:
        return FIXED[op]
    if 0x30 <= op <= 0x3F:
        return 1
    if 0x40 <= op <= 0x4E or 0x51 <= op <= 0x5F or 0x96 <= op <= 0xBF:
        return 2
    if 0x70 <= op <= 0x8F:
        return 0
    if 0xC0 <= op <= 0xDF:
        return 3
    if 0xE1 <= op <= 0xFF:
        return 4
    raise VgmError("unknown command 0x%02X" % op)


def parse(data):
    if data[:4] != b"Vgm " or len(data) < 0x40:
        raise VgmError("not a VGM file")
    eof = 4 + struct.unpack_from("<I", data, 4)[0]
    version, = struct.unpack_from("<I", data, 8)
    data_rel, = struct.unpack_from("<I", data, 0x34)
    start = 0x34 + data_rel if version >= 0x150 and data_rel else 0x40
    end = min(eof, len(data))

    def field(off, since, width=4):
        """Header fields exist only from their version on and only before the command data starts."""
        if version < since or off + width > start:
            return 0
        return int.from_bytes(data[off:off + width], "little")
    chips = {}
    for name, off, since in CHIPS:
        clock = field(off, since)
        if clock:
            chip = {"clock": clock & CLOCK_MASK, "dual": bool(clock & DUAL_FLAG)}
            if name == "YM2610":
                chip["variant"] = "YM2610B" if clock & YM2610B_FLAG else "YM2610"
            chips[name] = chip
    if version < 0x110 and "YM2413" in chips:
        # Before 1.10 the YM2413 clock field also stands for the YM2612 and YM2151.
        for name in ("YM2612", "YM2151"):
            chips[name] = dict(chips["YM2413"])
    total = field(0x18, 0x100)
    loop_rel = field(0x1C, 0x100)
    loop_samples = field(0x20, 0x100)
    loop_at = 0x1C + loop_rel if loop_rel else None
    gd3_rel = field(0x14, 0x100)
    pos, waited, commands, blocks, block_bytes = start, 0, 0, 0, 0
    loop_start = None
    while pos < end:
        if loop_at is not None and pos == loop_at and loop_start is None:
            loop_start = waited
        op = data[pos]
        commands += 1
        if op == 0x66:
            break
        if op == 0x67:  # data block: 0x67 0x66 type size(4) payload(size)
            if pos + 7 > end or data[pos + 1] != 0x66:
                raise VgmError("bad data block at 0x%X" % pos)
            size, = struct.unpack_from("<I", data, pos + 3)
            blocks += 1
            block_bytes += size
            pos += 7 + size
            continue
        n = _cmd_len(op)
        if op == 0x61:
            waited += struct.unpack_from("<H", data, pos + 1)[0]
        elif op == 0x62:
            waited += 735
        elif op == 0x63:
            waited += 882
        elif 0x70 <= op <= 0x7F:
            waited += (op & 0x0F) + 1
        elif 0x80 <= op <= 0x8F:
            waited += op & 0x0F
        pos += 1 + n
    gd3 = None
    if gd3_rel:
        g = 0x14 + gd3_rel
        if g + 12 <= len(data) and data[g:g + 4] == b"Gd3 ":
            length, = struct.unpack_from("<I", data, g + 8)
            strings = data[g + 12:g + 12 + length].decode("utf-16-le").split("\0")
            gd3 = {key: strings[i] for key, i in GD3_EN if i < len(strings)}
    return {"version": "%x.%02x" % (version >> 8, version & 0xFF), "chips": chips, "total_samples": total,
            "duration": str(Fraction(total, 44100)), "samples_counted": waited, "commands": commands,
            "data_blocks": blocks, "data_block_bytes": block_bytes,
            "loop": {"start_sample": loop_start, "samples": loop_samples,
                     "duration": str(Fraction(loop_samples, 44100))} if loop_at is not None else None,
            "gd3": gd3}
