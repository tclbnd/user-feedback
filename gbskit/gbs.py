"""Game Boy Sound System rips (.gbs): header, addresses, timer or vertical-blank playback rate, ROM banks."""
import struct
from fractions import Fraction

CPU = 4194304                                             # Game Boy CPU clock in Hz
FRAME_CLOCKS = 70224                                      # one frame is 70224 CPU clocks
TIMER_CLOCKS = (4096, 16384, 65536, 262144)
TIMER_ENABLED = 0x04                                      # TAC bit 2: timer interrupt enabled
DOUBLE_SPEED = 0x80                                       # TAC bit 7: CGB double-speed mode


class GbsError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def parse(data):
    if len(data) < 0x70 or data[:3] != b"GBS":
        raise GbsError("not a GBS file")
    version, songs, first = data[3], data[4], data[5]
    # Addresses in the GBS header are stored little-endian.
    load, init, play, stack = struct.unpack_from("<HHHH", data, 6)
    tma, tac = data[0x0E], data[0x0F]
    code = len(data) - 0x70
    double_speed = bool(tac & DOUBLE_SPEED)
    if tac & TIMER_ENABLED:
        # In CGB double-speed mode every timer tick takes half as long.
        clock = TIMER_CLOCKS[tac & 3] * (2 if double_speed else 1)
        timing, rate = "timer", Fraction(clock, 256 - tma)
    else:
        timing, rate = "vblank", Fraction(CPU, FRAME_CLOCKS)
    end = load + code - 1
    return {"version": version, "songs": songs, "first_song": first, "title": text(data[0x10:0x30]),
            "author": text(data[0x30:0x50]), "copyright": text(data[0x50:0x70]),
            "load_address": "$%04X" % load, "init_address": "$%04X" % init, "play_address": "$%04X" % play,
            "stack_pointer": "$%04X" % stack, "data_bytes": code, "end_address": "$%04X" % end if code else None,
            "timing": timing, "play_rate": str(rate), "double_speed": double_speed if timing == "timer" else False,
            "rom_banks": -(-(load + code) // 0x4000) if code else 0, "load_ok": 0x400 <= load < 0x8000}
