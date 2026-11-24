"""DMX MUS music lumps (Doom, Heretic, Hexen): header, instruments, event stream, timing at 140 Hz."""
import struct
from fractions import Fraction

TICKS = 140                                               # MUS time unit is 1/140 second
PERCUSSION = 15                                           # MUS channel 15 plays percussion
CONTROLLERS = {0: "program", 1: "bank", 2: "modulation", 3: "volume", 4: "pan", 5: "expression", 6: "reverb",
               7: "chorus", 8: "sustain", 9: "soft"}
SYSTEM = {10: "sounds_off", 11: "notes_off", 12: "mono", 13: "poly", 14: "reset"}


class MusError(ValueError):
    pass


def parse(data):
    if data[:4] != b"MUS\x1a":
        raise MusError("not a MUS lump")
    if len(data) < 16:
        raise MusError("truncated MUS header")
    length, start, primary, secondary, count = struct.unpack_from("<HHHHH", data, 4)
    if 16 + 2 * count > len(data):
        raise MusError("truncated instrument list")
    instruments = list(struct.unpack_from("<%dH" % count, data, 16))
    p, end = start, min(start + length, len(data))
    ticks = 0
    notes = 0
    channels = set()
    controllers = {}
    systems = {}
    pitch_bends = 0
    finished = False

    def take(n):
        """Consume n score bytes, refusing to read past the score's end."""
        nonlocal p
        if p + n > end:
            raise MusError("truncated event at %d" % p)
        chunk = data[p:p + n]
        p += n
        return chunk

    while p < end:
        desc = take(1)[0]
        kind, ch = (desc >> 4) & 7, desc & 15
        if kind == 0:                                         # release note: one note byte
            take(1)
        elif kind == 1:                                       # play note, bit 7 of note byte: a volume follows
            note = take(1)[0]
            if note & 0x80:
                take(1)
            notes += 1
            channels.add(ch)
        elif kind == 2:                                       # pitch wheel: one data byte
            take(1)
            pitch_bends += 1
        elif kind == 3:                                       # system event: one data byte
            code = take(1)[0]
            name = SYSTEM.get(code, "system_%d" % code)
            systems[name] = systems.get(name, 0) + 1
        elif kind == 4:                                       # controller change: number and value bytes
            code = take(2)[0]
            name = CONTROLLERS.get(code, "controller_%d" % code)
            controllers[name] = controllers.get(name, 0) + 1
        elif kind == 5:                                       # end of measure
            pass
        elif kind == 6:                                       # score end
            finished = True
            break
        else:
            raise MusError("unknown event type 7 at %d" % (p - 1))
        if desc & 0x80:                                       # last event of this time: a variable-length delay follows
            delay = 0
            while True:
                byte = take(1)[0]
                delay = (delay << 7) | (byte & 0x7F)
                if not byte & 0x80:
                    break
            ticks += delay
    return {"primary_channels": primary, "secondary_channels": secondary, "instruments": instruments,
            "ticks": ticks, "duration": str(Fraction(ticks, TICKS)), "notes": notes,
            "channels": sorted(channels), "percussion": PERCUSSION in channels,
            "controllers": dict(sorted(controllers.items())), "system_events": dict(sorted(systems.items())),
            "pitch_bends": pitch_bends, "score_end": finished}
