"""OpusHead, OpusTags and packet TOC parsing (RFC 7845).

Opus always decodes to 48 kHz internally; the granule position of an Ogg
Opus stream counts samples on that 48 kHz clock regardless of the
``input sample rate`` recorded in OpusHead.  A packet's duration comes
from the TOC byte, where code 3 carries an explicit frame count.
"""
from collections import namedtuple
from fractions import Fraction

#: The Opus decoding clock.  Granule positions are always in these units.
RATE = 48000

#: Maximum packet duration allowed by RFC 6716 / RFC 7845 section 3.1.
MAX_PACKET_MS = 120

Head = namedtuple(
    "Head",
    "channels pre_skip input_rate gain_db mapping version stream_count "
    "coupled_count channel_map",
)

Tags = namedtuple("Tags", "vendor comments")

# Samples per frame at 48 kHz, indexed by TOC configuration (RFC 7845 table 2).
_FRAME_SAMPLES = (
    [480, 960, 1920, 2880] * 3      # 0-11: SILK NB / MB / WB, 10/20/40/60 ms
    + [480, 960] * 2                # 12-15: Hybrid SWB / FB, 10/20 ms
    + [120, 240, 480, 960] * 4      # 16-31: CELT NB / WB / SWB / FB
)

#: Maximum frame count that code 3 may encode.
MAX_FRAMES = 48


class OpusError(ValueError):
    pass


def is_head(packet):
    return packet[:8] == b"OpusHead"


def is_tags(packet):
    return packet[:8] == b"OpusTags"


def head(packet):
    """Parse and validate an OpusHead identification packet."""
    if not is_head(packet):
        raise OpusError("not an OpusHead packet")
    if len(packet) < 19:
        raise OpusError("truncated OpusHead packet")
    version = packet[8]
    if version >> 4:
        raise OpusError(f"unsupported OpusHead version {version}")
    channels = packet[9]
    pre_skip = int.from_bytes(packet[10:12], "little")
    rate = int.from_bytes(packet[12:16], "little")
    gain = int.from_bytes(packet[16:18], "little", signed=True) / 256
    family = packet[18]
    stream_count = 1
    coupled_count = 0
    channel_map = ()

    if channels == 0:
        raise OpusError("OpusHead declares zero channels")
    if family == 0:
        if channels > 2:
            raise OpusError("mapping family 0 allows one or two channels")
    else:
        if len(packet) < 21 + channels:
            raise OpusError("truncated OpusHead channel mapping table")
        stream_count = packet[19]
        coupled_count = packet[20]
        channel_map = tuple(packet[21:21 + channels])
        if stream_count == 0 or coupled_count > stream_count:
            raise OpusError("invalid OpusHead stream counts")
        coded = stream_count + coupled_count
        for value in channel_map:
            if value != 255 and value >= coded:
                raise OpusError("OpusHead channel map out of range")

    return Head(channels, pre_skip, rate, gain, family, version,
                stream_count, coupled_count, channel_map)


def tags(packet):
    """Parse an OpusTags comment packet (best effort)."""
    if not is_tags(packet):
        raise OpusError("not an OpusTags packet")
    at = 8
    if len(packet) < at + 4:
        raise OpusError("truncated OpusTags packet")

    def read_len():
        nonlocal at
        if at + 4 > len(packet):
            raise OpusError("truncated OpusTags packet")
        value = int.from_bytes(packet[at:at + 4], "little")
        at += 4
        return value

    vendor_len = read_len()
    if at + vendor_len > len(packet):
        raise OpusError("truncated OpusTags vendor string")
    vendor = packet[at:at + vendor_len].decode("utf-8", "replace")
    at += vendor_len
    count = read_len()
    comments = []
    for _ in range(count):
        length = read_len()
        if at + length > len(packet):
            raise OpusError("truncated OpusTags comment")
        comments.append(packet[at:at + length].decode("utf-8", "replace"))
        at += length
    return Tags(vendor, tuple(comments))


def frame_samples(packet):
    """Samples per frame (at 48 kHz) declared by a packet's TOC."""
    if not packet:
        raise OpusError("empty Opus packet")
    return _FRAME_SAMPLES[packet[0] >> 3]


def frame_count(packet):
    """Number of frames in one Opus packet (RFC 6716 section 3.2)."""
    if not packet:
        raise OpusError("empty Opus packet")
    code = packet[0] & 3
    if code == 0:
        return 1
    if code in (1, 2):
        return 2
    # Code 3: a second byte carries the frame count in its low six bits.
    if len(packet) < 2:
        raise OpusError("code 3 packet is missing its frame count byte")
    count = packet[1] & 0x3F
    if count < 1 or count > MAX_FRAMES:
        raise OpusError(f"invalid code 3 frame count {count}")
    return count


def packet_samples(packet):
    """Total samples (at 48 kHz) carried by one Opus packet."""
    count = frame_count(packet)
    per_frame = frame_samples(packet)
    total = per_frame * count
    if total > RATE * MAX_PACKET_MS // 1000:
        raise OpusError("Opus packet exceeds the 120 ms limit")
    return total


def samples(packet, rate=RATE):
    """Samples at *rate* carried by one Opus packet.

    The returned value is an ``int`` when the duration is a whole number of
    samples at *rate* (always the case at 48 kHz) and an exact
    :class:`~fractions.Fraction` otherwise (2.5 ms frames at 44.1 kHz).
    """
    total = Fraction(packet_samples(packet) * rate, RATE)
    return int(total) if total.denominator == 1 else total

