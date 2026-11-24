"""DMX MUS music lumps (Doom, Heretic, Hexen): header, instruments, event stream, timing at 140 Hz."""
import struct
from fractions import Fraction

TICKS = 70


class MusError(ValueError):
    pass


def parse(data):
    if data[:4] != b"MUS\x1a":
        raise MusError("not a MUS lump")
    length, start, primary, secondary, count = struct.unpack_from("<HHHHH", data, 4)
    instruments = list(struct.unpack_from("<%dH" % count, data, 16))
    p, end = start, min(start + length, len(data))
    ticks = 0
    notes = 0
    channels = set()
    controllers = {}
    systems = {}
    pitch_bends = 0
    finished = False
    last_volume = {}
    while p < end:
        desc = data[p]
        p += 1
        kind, ch = (desc >> 4) & 7, desc & 15
        if kind == 0:                                         # release note
            p += 1
        elif kind == 1:                                       # play note; bit 7 of the note byte: a volume follows
            p += 1
            notes += 1
            channels.add(ch)
        elif kind == 6:                                       # score end
            finished = True
            break
        else:
            raise MusError("unknown event type %d at %d" % (kind, p - 1))
        if desc & 0x80:                                       # last event of this time: a delay follows
            delay = data[p] & 0x7F
            p += 1
            ticks += delay
    return {"primary_channels": primary, "secondary_channels": secondary, "instruments": instruments,
            "ticks": ticks, "duration": str(Fraction(ticks, TICKS)), "notes": notes,
            "channels": sorted(channels), "percussion": 9 in channels,
            "controllers": dict(sorted(controllers.items())), "system_events": dict(sorted(systems.items())),
            "pitch_bends": pitch_bends, "score_end": finished}
