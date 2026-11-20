"""OpusHead and packet TOC."""
from collections import namedtuple

Head = namedtuple("Head", "channels pre_skip input_rate gain_db mapping")

_FRAME_MS = [10, 20, 40, 60] * 3 + [10, 20] * 2 + [2.5, 5, 10, 20] * 4


def head(packet):
    if packet[:8] != b"OpusHead":
        raise ValueError("not an OpusHead packet")
    channels = packet[9]
    pre_skip = int.from_bytes(packet[10:12], "little")
    rate = int.from_bytes(packet[12:16], "little")
    gain = int.from_bytes(packet[16:18], "little") / 256
    return Head(channels, pre_skip, rate, gain, packet[18])


def samples(packet, rate=48000):
    """Samples (at rate) carried by one Opus packet."""
    if not packet:
        return 0
    config = packet[0] >> 3
    code = packet[0] & 3
    frames = 1 if code in (0, 3) else 2
    return int(_FRAME_MS[config] * rate / 1000) * frames
