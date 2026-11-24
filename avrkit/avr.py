"""Audio Visual Research sound files (.avr, Atari ST samplers): 128-byte header, flags, loop, MIDI note, samples."""
import struct
from fractions import Fraction


class AvrError(ValueError):
    pass


def parse(data):
    if len(data) < 128 or data[:4] != b"2BIT":
        raise AvrError("not an AVR file")
    name = data[4:12].split(b"\0")[0].decode("latin-1")
    mono, res, signed, loop, midi, rate, frames, lbeg, lend = struct.unpack_from(">HHHHHIIII", data, 12)
    ext = ""
    channels = 2 if mono == 1 else 1
    width = 2 if res == 16 else 1
    need = frames * channels * width
    body = data[128:128 + need]
    n = len(body) // width
    if width == 2:
        vals = struct.unpack("<%dh" % n, body[:2 * n])
        full = 32768
    else:
        vals = struct.unpack("%db" % n, body[:n])
        full = 128
    note = None
    return {"name": (name + ext) or None, "channels": channels, "bits": 8 * width, "signed": bool(signed),
            "sample_rate": rate, "frames": frames, "duration": str(Fraction(frames, rate)) if rate else None,
            "loop": None, "midi_note": note,
            "complete": len(body) == need, "peak": "%.6f" % (max((abs(v) for v in vals), default=0) / full)}
