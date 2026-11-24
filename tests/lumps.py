"""A minimal MUS lump: one instrument, release events with short delays, score end."""
import struct


def lump():
    body = bytes([0x80, 60, 10, 0x80, 61, 10, 0x60])        # release ch0 note 60 +10 ticks, release note 61 +10, end
    return b"MUS\x1a" + struct.pack("<HHHHHH", len(body), 18, 1, 0, 1, 0) + struct.pack("<H", 30) + body


def vlq(value):
    """Encode a delay the DMX way: MIDI-style variable-length quantity."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def wrap(body, score_length=None):
    """Pack an event stream into a MUS lump; bytes past score_length are padding, not music."""
    if score_length is None:
        score_length = len(body)
    header = b"MUS\x1a" + struct.pack("<HHHHHH", score_length, 18, 1, 0, 1, 0) + struct.pack("<H", 30)
    return header + body


def full_lump():
    """Every event type: controllers (named and unknown), pitch bend, measure, system events,
    notes with and without a volume byte on the percussion channel, multi-byte delays, score end."""
    body = b"".join([
        b"\x30\x00\x00",                              # change controller: program (0), ch0
        b"\xB3\x03\x64" + vlq(128),                   # volume (3), ch3, then a 128-tick multi-byte delay
        b"\x31\x14\x05",                              # unknown controller 20, ch1
        b"\x22\x40",                                  # pitch wheel bend, ch2
        b"\x50",                                      # measure marker
        b"\x40\x0A",                                  # system event: sounds off (10)
        b"\x40\x0F",                                  # unknown system event 15
        b"\x9F\xC0\x64" + vlq(272),                   # note on ch15 (percussion), note 64 with volume 100, +272 ticks
        b"\x90\x3C" + vlq(5),                         # note on ch0, note 60, no volume byte, +5 ticks
        b"\x80\x3C" + vlq(7),                         # release ch0 note 60, +7 ticks
        b"\x60",                                      # score end
    ])
    return wrap(body)
