"""DMX MUS music lumps (Doom, Heretic, Hexen): header, instruments, event stream, timing at 140 Hz."""
import struct
from fractions import Fraction

TICKS = 140                                                  # MUS clocks played back per second
PERCUSSION_CHANNEL = 15                                      # id Tech 1 reserves channel 15 for percussion

# DMX controller numbers -> names (controller event, type 3, first data byte)
CONTROLLER_NAMES = {
    0: "program",
    1: "bank",
    2: "modulation",
    3: "volume",
    4: "pan",
    5: "expression",
    6: "reverb",
    7: "chorus",
    8: "sustain",
    9: "soft",
}

# DMX system event numbers -> names (system event, type 4, sole data byte)
SYSTEM_NAMES = {
    10: "sounds_off",
    11: "notes_off",
    12: "mono",
    13: "poly",
    14: "reset",
}


class MusError(ValueError):
    pass


def _read_delay(data, p, end):
    """MIDI-style variable-length quantity: 7 data bits per byte, bit 7 = another byte follows."""
    delay = 0
    while True:
        if p >= end:
            raise MusError("truncated delay at %d" % p)
        byte = data[p]
        p += 1
        delay = (delay << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return delay, p


def parse(data):
    if data[:4] != b"MUS\x1a":
        raise MusError("not a MUS lump")
    if len(data) < 16:
        raise MusError("truncated header")
    length, start, primary, secondary, count, pad = struct.unpack_from("<HHHHHH", data, 4)
    if len(data) < 16 + 2 * count:
        raise MusError("truncated instrument list")
    instruments = list(struct.unpack_from("<%dH" % count, data, 16))
    if start < 16 + 2 * count:
        raise MusError("score starts inside the instrument list")
    end = start + length                                     # only the score bytes the header gives belong to the music
    if end > len(data):
        raise MusError("score extends past end of lump")

    ticks = 0
    notes = 0
    channels = set()
    controllers = {}
    systems = {}
    pitch_bends = 0
    measures = 0
    finished = False
    p = start
    while p < end:
        desc = data[p]
        p += 1
        kind, ch = (desc >> 4) & 7, desc & 15
        if kind == 0:                                         # release note
            if p >= end:
                raise MusError("truncated release-note event at %d" % (p - 1))
            p += 1
        elif kind == 1:                                       # play note
            if p >= end:
                raise MusError("truncated play-note event at %d" % (p - 1))
            note = data[p]
            p += 1
            if note & 0x80:                                   # bit 7 set: a volume byte follows
                if p >= end:
                    raise MusError("truncated note volume at %d" % p)
                p += 1
            notes += 1
            channels.add(ch)
        elif kind == 2:                                       # pitch wheel bend, one signed byte
            if p >= end:
                raise MusError("truncated pitch-bend event at %d" % (p - 1))
            p += 1
            pitch_bends += 1
        elif kind == 3:                                       # change controller
            if p + 2 > end:
                raise MusError("truncated controller event at %d" % (p - 1))
            number, value = data[p], data[p + 1]
            p += 2
            name = CONTROLLER_NAMES.get(number, "controller_%d" % number)
            controllers[name] = controllers.get(name, 0) + 1
        elif kind == 4:                                       # system event
            if p >= end:
                raise MusError("truncated system event at %d" % (p - 1))
            number = data[p]
            p += 1
            name = SYSTEM_NAMES.get(number, "system_%d" % number)
            systems[name] = systems.get(name, 0) + 1
        elif kind == 5:                                       # measure marker
            measures += 1
        elif kind == 6:                                       # score end
            finished = True
            break
        else:
            raise MusError("unknown event type %d at %d" % (kind, p - 1))
        if desc & 0x80:                                       # last event of this time: a delay follows
            delay, p = _read_delay(data, p, end)
            ticks += delay

    return {"primary_channels": primary, "secondary_channels": secondary, "instruments": instruments,
            "ticks": ticks, "duration": str(Fraction(ticks, TICKS)), "notes": notes,
            "channels": sorted(channels), "percussion": PERCUSSION_CHANNEL in channels,
            "controllers": dict(sorted(controllers.items())), "system_events": dict(sorted(systems.items())),
            "pitch_bends": pitch_bends, "measures": measures, "score_end": finished}
