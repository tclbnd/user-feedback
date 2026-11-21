"""SBC / mSBC frame headers.

Frame layout and length formulae follow the SBC codec definition referenced
by A2DP (section 4.3); the mSBC parameters follow HFP 1.6 Appendix A.
"""
from math import ceil

SYNC = 0x9C          # A2DP SBC sync word
MSBC_SYNC = 0xAD     # HFP 1.6 Appendix A mSBC sync word
RATES = (16000, 32000, 44100, 48000)
MODES = ("mono", "dual_channel", "stereo", "joint_stereo")

# HFP 1.6 Appendix A: mSBC uses one fixed parameter set, a 57-byte frame
# (24-bit header, SBC audio data, 16-bit CRC) carrying 120 samples at 16 kHz.
MSBC_RATE = 16000
MSBC_BLOCKS = 15
MSBC_SUBBANDS = 8
MSBC_BITPOOL = 26
MSBC_FRAME_LENGTH = 57


class FrameError(ValueError):
    pass


def frame_length(mode, subbands, blocks, bitpool):
    """SBC frame length in bytes, per the A2DP SBC frame header definition."""
    channels = 1 if mode == "mono" else 2
    if mode in ("mono", "dual_channel"):
        audio = ceil(blocks * channels * bitpool / 8)
    elif mode == "stereo":
        # One bitpool for the whole frame, not one per channel.
        audio = ceil(blocks * bitpool / 8)
    else:  # joint_stereo adds one join bit per subband
        audio = ceil((subbands + blocks * bitpool) / 8)
    return 4 + (4 * subbands * channels) // 8 + audio


def parse(data, pos):
    """Decode the SBC or mSBC frame header at ``pos``."""
    if pos + 4 > len(data):
        raise FrameError("truncated frame header at %d" % pos)
    if data[pos] == MSBC_SYNC:
        # mSBC header bytes 1..3 are reserved and set to zero; the coding
        # parameters are implicit (HFP 1.6 Appendix A).
        length = MSBC_FRAME_LENGTH
        rate, blocks, subbands, bitpool = MSBC_RATE, MSBC_BLOCKS, MSBC_SUBBANDS, MSBC_BITPOOL
        mode, allocation, channels = "mono", "loudness", 1
        codec = "msbc"
    else:
        if data[pos] != SYNC:
            raise FrameError("no SBC sync word at %d" % pos)
        b = data[pos + 1]
        rate = RATES[b >> 6]
        blocks = 4 * (((b >> 4) & 3) + 1)
        mode = MODES[(b >> 2) & 3]
        allocation = "snr" if b & 2 else "loudness"
        subbands = 8 if b & 1 else 4
        bitpool = data[pos + 2]
        if not 2 <= bitpool <= 250:
            raise FrameError("bitpool %d out of range at %d" % (bitpool, pos))
        channels = 1 if mode == "mono" else 2
        length = frame_length(mode, subbands, blocks, bitpool)
        codec = "sbc"
    samples = blocks * subbands
    # Frame duration is samples / rate regardless of the channel count.
    bitrate = 8 * length * rate // samples
    return {"codec": codec, "sample_rate": rate, "blocks": blocks,
            "channel_mode": mode, "channels": channels,
            "allocation": allocation, "subbands": subbands, "bitpool": bitpool,
            "frame_length": length, "bitrate": bitrate, "samples": samples}
