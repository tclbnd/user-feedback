"""VGM (Video Game Music) logs: header by version, chip clocks, command stream, loop point, GD3 tag."""
import struct
from fractions import Fraction

# (name, header offset, first version with the field)
CHIPS = [("SN76489", 0x0C, 0x101), ("YM2413", 0x10, 0x101), ("YM2612", 0x2C, 0x110),
         ("YM2151", 0x30, 0x110),
         ("SegaPCM", 0x38, 0x151), ("YM2203", 0x44, 0x151), ("YM2608", 0x48, 0x151), ("YM2610", 0x4C, 0x151),
         ("YM3812", 0x50, 0x151), ("AY8910", 0x74, 0x151), ("GameBoy", 0x80, 0x161), ("NES", 0x84, 0x161)]
# English GD3 fields plus the language-neutral trailing fields.
GD3_OUT = (("title", 0), ("game", 2), ("system", 4), ("author", 6), ("date", 8), ("ripper", 9), ("notes", 10))
# Extra data bytes for commands whose length does not follow a range rule.
FIXED = {0x30: 1, 0x4F: 1, 0x50: 1, 0x61: 2, 0x62: 0, 0x63: 0, 0xE0: 4}
DUAL_BIT = 0x40000000
CLOCK_MASK = 0x3FFFFFFF
VARIANT_BIT = 0x80000000

WRITES = {0x30: "SN76489", 0x50: "SN76489", 0x51: "YM2413", 0xA1: "YM2413",
          0x52: "YM2612", 0x53: "YM2612", 0xA2: "YM2612", 0xA3: "YM2612",
          0x54: "YM2151", 0xA4: "YM2151", 0x55: "YM2203", 0xA5: "YM2203",
          0x56: "YM2608", 0x57: "YM2608", 0xA6: "YM2608", 0xA7: "YM2608",
          0x58: "YM2610", 0x59: "YM2610", 0xA8: "YM2610", 0xA9: "YM2610",
          0x5A: "YM3812", 0xAA: "YM3812", 0xA0: "AY8910", 0xB3: "GameBoy",
          0xB4: "NES", 0xC0: "SegaPCM"}


class VgmError(ValueError):
    pass


def field_word(data, off):
    """Read a little-endian 32-bit header word without raising on truncated files."""
    if off + 4 > len(data):
        return 0
    return int.from_bytes(data[off:off + 4], "little")


def _cmd_len(op):
    if op in FIXED:
        return FIXED[op]
    if 0x31 <= op <= 0x4E or 0x51 <= op <= 0x5F or 0x80 <= op <= 0x8F or 0x96 <= op <= 0xBF:
        return 2
    if 0x90 <= op <= 0x91 or op == 0x95 or 0xC0 <= op <= 0xDF:
        return 4 if (0x90 <= op <= 0x91 or op == 0x95) else 3
    if op == 0x92:
        return 5
    if op == 0x93:
        return 10
    if op == 0x94:
        return 1
    if 0x70 <= op <= 0x7F:
        return 0
    if 0xE1 <= op <= 0xFF:
        return 4
    raise VgmError("unknown command 0x%02X" % op)


def parse(data):
    if data[:4] != b"Vgm " or len(data) < 0x40:
        raise VgmError("not a VGM file")
    eof = 4 + struct.unpack_from("<I", data, 4)[0]
    version, = struct.unpack_from("<I", data, 8)
    data_rel = field_word(data, 0x34) if version >= 0x150 else 0
    start = 0x34 + data_rel if data_rel else 0x40
    end = min(eof, len(data))

    def field(off, since, width=4):
        """Header fields exist only from their version on and only before the command data starts."""
        if version < since or off + width > start:
            return 0
        return int.from_bytes(data[off:off + width], "little")
    chips = {}
    for name, off, since in CHIPS:
        raw = field(off, since)
        if not raw:
            continue
        clock = raw & CLOCK_MASK
        chip = {"clock": clock, "dual": bool(raw & DUAL_BIT)}
        if name == "YM2610":
            chip["variant"] = "YM2610B" if raw & VARIANT_BIT else "YM2610"
        chips[name] = chip
    # Before version 1.10 the YM2413 clock field also stands for the YM2612 and YM2151.
    if version < 0x110 and "YM2413" in chips:
        for name in ("YM2612", "YM2151"):
            chips[name] = dict(chips["YM2413"])
    total = field(0x18, 0x101)
    loop_rel = field(0x1C, 0x101)
    loop_samples = field(0x20, 0x101)
    loop_at = (0x1C + loop_rel) if loop_rel else None
    gd3_rel = field(0x14, 0x150)
    pos, waited, commands, blocks, block_bytes = start, 0, 0, 0, 0
    writes = {}
    loop_start = None
    while pos < end:
        if loop_at is not None and pos == loop_at:
            loop_start = waited
        op = data[pos]
        if op == 0x66:
            commands += 1
            break
        if op == 0x67:
            # Data block: 67 66 [type] [32-bit size] [payload]
            size = struct.unpack_from("<I", data, pos + 3)[0]
            blocks += 1
            block_bytes += size
            commands += 1
            pos += 7 + size
            continue
        if op == 0x68:
            # PCM RAM write: 68 [type] [32-bit size] [32-bit offset] [data]
            size = struct.unpack_from("<I", data, pos + 2)[0]
            commands += 1
            pos += 10 + size
            continue
        n = _cmd_len(op)
        chip = WRITES.get(op)
        if chip:
            writes[chip] = writes.get(chip, 0) + 1
        if op == 0x61:
            waited += struct.unpack_from("<H", data, pos + 1)[0]
        elif op == 0x62:
            waited += 735
        elif op == 0x63:
            waited += 882
        elif 0x70 <= op <= 0x7F:
            waited += (op & 0x0F) + 1
        elif 0x80 <= op <= 0x8F:
            # YM2612 port 0 write-and-wait: wait n samples
            waited += op & 0x0F
            writes["YM2612"] = writes.get("YM2612", 0) + 1
        commands += 1
        pos += 1 + n
    modifier = field(0x7F, 0x151, 1) or 0x10
    base = int.from_bytes(data[0x7E:0x7F], "little", signed=True) if field(0x7E, 0x151, 1) else 0
    play_loops = Fraction(2 * modifier, 0x10) - base
    gd3 = None
    if gd3_rel:
        g = 0x14 + gd3_rel
        if g + 12 <= len(data) and data[g:g + 4] == b"Gd3 ":
            length, = struct.unpack_from("<I", data, g + 8)
            strings = data[g + 12:g + 12 + length].decode("utf-16-le").split("\0")
            gd3 = {key: strings[idx] for key, idx in GD3_OUT if idx < len(strings)}
    return {"version": "%d.%02d" % (version >> 8, ((version >> 4) & 0x0F) * 10 + (version & 0x0F)),
            "chips": chips, "total_samples": total,
            "duration": str(Fraction(total, 44100)), "samples_counted": waited, "commands": commands,
            "data_blocks": blocks, "data_block_bytes": block_bytes,
            "loop": {"start_sample": loop_start, "samples": loop_samples,
                     "duration": str(Fraction(loop_samples, 44100))} if loop_at is not None else None,
            "gd3": gd3, "chip_writes": dict(sorted(writes.items())),
            "play_loops": str(play_loops) if loop_at is not None else None}
