"""Game Boy Sound System rips (.gbs): header, addresses, timer or vertical-blank playback rate, ROM banks."""
import struct
from fractions import Fraction

CPU = 4194304                                             # Game Boy CPU clock in Hz
FRAME_CLOCKS = 70224                                      # clocks in one vertical-blank frame
# TIMA input clock selected by TAC bits 0-1 (CPU/1024, CPU/256, CPU/64, CPU/16)
TIMER_CLOCKS = (CPU // 1024, CPU // 256, CPU // 64, CPU // 16)
TAC_TIMER_ENABLE = 0x04                                   # TAC bit 2: timer interrupt enabled
TAC_DOUBLE_SPEED = 0x80                                   # GBS TAC bit 7: CGB double-speed mode


class GbsError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def parse(data):
    if len(data) < 0x70 or data[:3] != b"GBS":
        raise GbsError("not a GBS file")
    version, songs, first = data[3], data[4], data[5]
    load, init, play, stack = struct.unpack_from("<HHHH", data, 6)
    tma, tac = data[0x0E], data[0x0F]
    code = len(data) - 0x70
    if tac & TAC_TIMER_ENABLE:
        clock = TIMER_CLOCKS[tac & 3]
        if tac & TAC_DOUBLE_SPEED:
            clock *= 2                                    # timer clocks double in CGB double-speed mode
        timing, rate = "timer", Fraction(clock, 256 - tma)
    else:
        timing, rate = "vblank", Fraction(CPU, FRAME_CLOCKS)     # one frame is 70224 clocks
    end = load + code - 1
    return {"version": version, "songs": songs, "first_song": first, "title": text(data[0x10:0x30]),
            "author": text(data[0x30:0x50]), "copyright": text(data[0x50:0x70]),
            "load_address": "$%04X" % load, "init_address": "$%04X" % init, "play_address": "$%04X" % play,
            "stack_pointer": "$%04X" % stack, "data_bytes": code, "end_address": "$%04X" % end if code else None,
            "timing": timing, "play_rate": str(rate), "double_speed": bool(tac & TAC_DOUBLE_SPEED) if timing == "timer" else False,
            "rom_banks": -(-(load + code) // 0x4000) if code else 0, "load_ok": 0x400 <= load < 0x8000}
