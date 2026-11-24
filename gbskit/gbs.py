"""Game Boy Sound System rips (.gbs): header, addresses, timer or vertical-blank playback rate, ROM banks."""
import struct
from fractions import Fraction

CPU = 4194304                                             # Game Boy CPU clock in Hz
TIMER_CLOCKS = (4096, 16384, 65536, 262144)


class GbsError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def parse(data):
    if len(data) < 0x70 or data[:3] != b"GBS":
        raise GbsError("not a GBS file")
    version, songs, first = data[3], data[4], data[5]
    load, init, play, stack = struct.unpack_from(">HHHH", data, 6)
    tma, tac = data[0x0E], data[0x0F]
    code = len(data) - 0x70
    if tac:
        clock = TIMER_CLOCKS[tac & 3]
        timing, rate = "timer", Fraction(clock, 256 - tma)
    else:
        timing, rate = "vblank", Fraction(CPU, 70224)     # one frame is 70224 clocks
    end = load + code - 1
    return {"version": version, "songs": songs, "first_song": first + 1, "title": text(data[0x10:0x30]),
            "author": text(data[0x30:0x50]), "copyright": text(data[0x50:0x70]),
            "load_address": "$%04X" % load, "init_address": "$%04X" % init, "play_address": "$%04X" % play,
            "stack_pointer": "$%04X" % stack, "data_bytes": code, "end_address": "$%04X" % end if code else None,
            "timing": timing, "play_rate": str(rate), "double_speed": bool(tac & 0x80) if timing == "timer" else False,
            "rom_banks": -(-(load + code) // 0x4000) if code else 0, "load_ok": 0x400 <= load < 0x8000}
