"""ADTS frame headers.

The fixed ADTS header is defined in ISO/IEC 13818-7 (MPEG-2) and
ISO/IEC 14496-3 (MPEG-4); both standards use the same layout::

    byte 0  syncword high              0xFF
    byte 1  syncword low (4) | ID (1) | layer (2, always 0) | protection_absent (1)
    byte 2  profile (2) | sampling_frequency_index (4) | private (1) | channel_config high (1)
    byte 3  channel_config low (2) | original/copy (1) | home (1) | copyright id bit (1)
            | copyright id start (1) | frame_length high (2)
    byte 4  frame_length middle (8)
    byte 5  frame_length low (5) | buffer fullness high (3)
    byte 6  buffer fullness low (5) | number_of_raw_data_blocks_in_frame (2)

A zero protection_absent bit adds a 16 bit CRC right after the header.
"""
from collections import namedtuple

Frame = namedtuple(
    "Frame",
    "offset length header_size mpeg_version profile object_type freq_index "
    "sample_rate channel_config channels raw_blocks crc",
)

# ISO/IEC 14496-3 Table 1.17 "samplingFrequencyIndex". Index 15 means the
# explicit frequency is carried in the AudioSpecificConfig rather than here.
RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
         16000, 12000, 11025, 8000, 7350, 0, 0, 0]
EXPLICIT_RATE_INDEX = 0xF

# ISO/IEC 14496-3 Table 1.19 "channelConfiguration". Config 7 is the
# 7.1 layout (front left/right/center, LFE, two surround pairs) = 8 channels.
CHANNEL_COUNTS = (0, 1, 2, 3, 4, 5, 6, 8)

# Samples per raw_data_block for the AAC profiles carried in ADTS
# (Main, LC, SSR, LTP, ER_LC, ER_LTP). Each frame holds one or more blocks.
SAMPLES_PER_RAW_BLOCK = 1024

SYNC_HIGH = 0xFF
SYNC_LOW_MASK = 0xF0
SYNC_LOW = 0xF0
HEADER_SIZE = 7
CRC_SIZE = 2


class AdtsError(ValueError):
    pass


def parse(b, offset=0):
    """Parse one fixed ADTS header (7 bytes).

    ``b`` must contain at least the fixed header; the variable CRC, if
    present, is reported through ``header_size``/``crc`` without being read.
    """
    if len(b) < HEADER_SIZE or b[0] != SYNC_HIGH or (b[1] & SYNC_LOW_MASK) != SYNC_LOW:
        raise AdtsError("no ADTS sync")
    # ID: 1 = MPEG-2 (13818-7), 0 = MPEG-4 (14496-3); layer is always 0.
    mpeg_version = 2 if (b[1] >> 3) & 1 else 4
    crc = not (b[1] & 1)
    header_size = HEADER_SIZE + (CRC_SIZE if crc else 0)

    # ADTS profile is the 2 bit field; the MPEG-4 audioObjectType used in the
    # AudioSpecificConfig is one higher (ADTS profile 1 == AAC LC == object 2).
    profile = (b[2] >> 6) & 3
    object_type = profile + 1
    freq_index = (b[2] >> 2) & 0xF
    if freq_index in (13, 14):
        raise AdtsError(f"reserved sampling frequency index {freq_index}")
    sample_rate = None if freq_index == EXPLICIT_RATE_INDEX else RATES[freq_index]

    channel_config = ((b[2] & 1) << 2) | (b[3] >> 6)
    channels = CHANNEL_COUNTS[channel_config]

    length = ((b[3] & 3) << 11) | (b[4] << 3) | (b[5] >> 5)
    raw_blocks = (b[6] & 3) + 1
    if length < header_size:
        raise AdtsError("frame length smaller than header")
    return Frame(offset, length, header_size, mpeg_version, profile, object_type,
                 freq_index, sample_rate, channel_config, channels, raw_blocks, crc)


def skip_id3v2(data, pos=0):
    """Return the position just past a leading ID3v2 tag, else ``pos``.

    The tag size is a 28 bit syncsafe integer (ID3v2.3/2.4); the footer flag
    adds another 10 bytes that the syncsafe size does not include.
    """
    if data[pos:pos + 3] != b"ID3" or pos + 10 > len(data):
        return pos
    flags = data[pos + 5]
    syncsafe = ((data[pos + 6] & 0x7F) << 21 | (data[pos + 7] & 0x7F) << 14
                | (data[pos + 8] & 0x7F) << 7 | (data[pos + 9] & 0x7F))
    tag_size = 10 + syncsafe + (10 if flags & 0x10 else 0)
    return pos + tag_size


def frames(data):
    """Yield every complete ADTS frame in ``data``.

    An ID3v2 tag at the start is skipped. Stray bytes between frames (or a
    truncated trailing frame) do not stop iteration: the scanner resyncs on
    the next valid sync word and simply skips what cannot be decoded.
    """
    n = len(data)
    pos = skip_id3v2(data)
    while pos + HEADER_SIZE <= n:
        if data[pos] != SYNC_HIGH or (data[pos + 1] & SYNC_LOW_MASK) != SYNC_LOW:
            pos += 1
            continue
        try:
            frame = parse(data[pos:pos + HEADER_SIZE], pos)
        except AdtsError:
            pos += 1
            continue
        end = pos + frame.length
        if end > n:
            # Incomplete final frame: nothing complete remains to read.
            break
        yield frame
        pos = end
