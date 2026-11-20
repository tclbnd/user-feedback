"""ADTS frame headers."""
from collections import namedtuple

Frame = namedtuple("Frame", "offset length header_size mpeg_version object_type freq_index sample_rate "
                            "channel_config channels raw_blocks crc")

RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000]


class AdtsError(ValueError):
    pass


def parse(b, offset=0):
    if len(b) < 7 or b[0] != 0xFF or (b[1] & 0xF0) != 0xF0:
        raise AdtsError("no ADTS sync")
    mpeg_version = 4
    crc = not (b[1] & 1)
    profile = b[2] >> 6
    freq_index = (b[2] >> 2) & 0xF
    if freq_index >= len(RATES):
        raise AdtsError(f"bad sampling frequency index {freq_index}")
    channel_config = ((b[2] & 1) << 2) | (b[3] >> 6)
    length = ((b[3] & 3) << 11) | (b[4] << 3) | (b[5] >> 5)
    raw_blocks = (b[6] & 3) + 1
    if length < 7:
        raise AdtsError("frame length smaller than header")
    return Frame(offset, length, 7, mpeg_version, profile, freq_index, RATES[freq_index],
                 channel_config, channel_config, raw_blocks, crc)


def frames(data):
    pos = 0
    while pos + 7 <= len(data):
        f = parse(data[pos:pos + 7], pos)
        yield f
        pos += f.length
