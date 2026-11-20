"""SBC frame header."""
from math import ceil

SYNC = 0x9C
RATES = (16000, 32000, 44100, 48000)
MODES = ("mono", "dual_channel", "stereo", "joint_stereo")


class FrameError(ValueError):
    pass


def parse(data, pos):
    """Decode the SBC frame header at ``pos``."""
    if pos + 4 > len(data) or data[pos] != SYNC:
        raise FrameError("no SBC sync word at %d" % pos)
    b = data[pos + 1]
    rate = RATES[b >> 6]
    blocks = 4 * (((b >> 4) & 3) + 1)
    mode = MODES[(b >> 2) & 3]
    allocation = "snr" if b & 2 else "loudness"
    subbands = 8 if b & 1 else 4
    bitpool = data[pos + 2]
    channels = 1 if mode == "mono" else 2
    length = 4 + (4 * subbands * channels) // 8 + ceil(blocks * channels * bitpool / 8)
    bitrate = 8 * length * rate // (subbands * blocks * channels)
    return {"sample_rate": rate, "blocks": blocks, "channel_mode": mode, "channels": channels,
            "allocation": allocation, "subbands": subbands, "bitpool": bitpool,
            "frame_length": length, "bitrate": bitrate, "samples": blocks * subbands}
