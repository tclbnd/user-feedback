"""IFF 8SVX instruments (EA IFF 85 / 8SVX).

An IFF file is a tree of chunks.  Every chunk is a 4 byte identifier, a 4
byte big-endian size and that many payload bytes; both chunks and the
enclosing FORM are padded with a single zero byte when the payload size is
odd so that everything stays word aligned.  8SVX FORM chunks contain a
VHDR, optional NAME/AUTH/ANNO/CHAN chunks and the BODY with the samples.
"""
import struct
from fractions import Fraction

# Chunk identifiers used inside an 8SVX FORM.
VHDR = b"VHDR"
BODY = b"BODY"
NAME = b"NAME"
AUTH = b"AUTH"
ANNO = b"ANNO"
CHAN = b"CHAN"

# 8SVX compression attributes (VHDR compression field).
COMP_NONE = 0
COMP_FIB_DELTA = 1
COMPRESSION_NAMES = {COMP_NONE: "none", COMP_FIB_DELTA: "fibonacci"}

# CHAN channel-assignment flags (bits 30..0 of the CHAN chunk).
CHAN_LEFT = 1 << 5      # bit 5
CHAN_RIGHT = 1 << 4     # bit 4
CHAN_STEREO = CHAN_LEFT | CHAN_RIGHT

# Deltas selected by the 4 bit Fibonacci-delta codes, code 0..15.
FIB_DELTAS = (-34, -21, -13, -8, -5, -3, -2, -1,
              0, 1, 2, 3, 5, 8, 13, 21)


class SvxError(ValueError):
    pass


def _u32(buf, pos):
    return int.from_bytes(buf[pos:pos + 4], "big")


def chunks(data):
    """Yield (chunk_id, payload) pairs for the chunks of an 8SVX FORM.

    Honours the IFF word-alignment rule: a chunk whose payload has an odd
    length is followed by one padding byte that is not part of the payload.
    """
    if data[:4] != b"FORM":
        raise SvxError("not an IFF file (missing FORM)")
    if data[8:12] != b"8SVX":
        raise SvxError("not an IFF 8SVX file")
    form_end = 8 + _u32(data, 4)
    if form_end > len(data):
        raise SvxError("truncated FORM")
    pos = 12
    out = []
    while pos < form_end:
        if pos + 8 > form_end:
            raise SvxError("truncated chunk header")
        cid = data[pos:pos + 4]
        size = _u32(data, pos + 4)
        start = pos + 8
        end = start + size
        if end > form_end:
            raise SvxError("truncated chunk payload")
        out.append((cid, data[start:end]))
        # Odd payloads are padded with one byte to restore word alignment.
        pos = end + (size & 1)
    return out


def _decode_pcm(body):
    """Decode an uncompressed BODY: unsigned bytes holding signed samples."""
    return [b - 256 if b > 127 else b for b in body]


def _decode_fibonacci(body, sample_count):
    """Decode a Fibonacci-delta compressed BODY.

    The first byte is a compression padding zero and the second byte is
    the initial signed sample.  Following bytes hold two 4 bit delta
    codes each, high nibble first; a code indexes FIB_DELTAS and the delta
    is added to the running sample.  The BODY is word aligned, so an odd
    number of delta codes is rounded up with a pad nibble (code 8, a zero
    delta); the stream is trimmed to the VHDR declared sample count, which
    drops the padding sample(s).
    """
    if not body:
        return []
    initial = body[1] if len(body) > 1 else 0
    sample = initial - 256 if initial > 127 else initial
    samples = [sample]
    for byte in body[2:]:
        for nibble in (byte >> 4, byte & 0x0F):
            sample += FIB_DELTAS[nibble]
            if sample > 127:
                sample = 127
            elif sample < -128:
                sample = -128
            samples.append(sample)
    return samples[:sample_count]


def _decode_body(body, compression, sample_count):
    if compression == COMP_NONE:
        return _decode_pcm(body)
    if compression == COMP_FIB_DELTA:
        return _decode_fibonacci(body, sample_count)
    raise SvxError("unsupported compression %d" % compression)


def _split_channels(samples, channels):
    """Split a planar BODY into per-channel sample lists.

    Stereo 8SVX BODYs store the whole left-channel block followed by the
    whole right-channel block rather than interleaving the samples.
    """
    if channels == 1:
        return [samples]
    per_channel = len(samples) // channels
    return [samples[i * per_channel:(i + 1) * per_channel]
            for i in range(channels)]


def _channel_info(flags):
    if flags == 0:
        return 1, "mono"
    if flags == CHAN_LEFT:
        return 1, "left"
    if flags == CHAN_RIGHT:
        return 1, "right"
    if flags == CHAN_STEREO:
        return 2, "stereo"
    raise SvxError("unsupported CHAN flags 0x%08x" % flags)


def _text(payload):
    # Text chunks hold a NUL terminated Latin-1 string.
    return payload.split(b"\0", 1)[0].decode("latin-1")


def summary(data):
    vhdr = body = None
    name = author = None
    annos = []
    chan_flags = 0
    for cid, payload in chunks(data):
        if cid == VHDR:
            vhdr = payload
        elif cid == BODY:
            body = payload
        elif cid == NAME:
            name = _text(payload)
        elif cid == AUTH:
            author = _text(payload)
        elif cid == ANNO:
            # There may be several ANNO chunks; keep every one in order.
            annos.append(_text(payload))
        elif cid == CHAN:
            if len(payload) < 4:
                raise SvxError("truncated CHAN chunk")
            chan_flags = _u32(payload, 0)
    if vhdr is None:
        raise SvxError("missing VHDR")
    if body is None:
        raise SvxError("missing BODY")
    if len(vhdr) < 20:
        raise SvxError("truncated VHDR")

    # Voice Property Chunk: one-shot, repeat, hi-cycle, rate, octaves,
    # compression and a 16.16 fixed point volume.
    one_shot, repeat, _hi_cycle, rate, octaves, compression, volume = \
        struct.unpack(">IIIHBBI", vhdr[:20])
    channels, channel = _channel_info(chan_flags)

    # Octaves are stored highest first; every lower octave holds half as
    # many samples as the one above it.  Their sum is the per-channel
    # sample count declared for the instrument.
    highest = one_shot + repeat
    samples_per_octave = [highest >> i for i in range(max(1, octaves))]
    sample_count = sum(samples_per_octave)

    decoded = _decode_body(body, compression, sample_count)
    per_channel = _split_channels(decoded, channels)

    if rate:
        duration = str(Fraction(highest, rate))
    else:
        duration = None

    return {
        "name": name,
        "author": author,
        "annotations": annos,
        "sample_rate": rate,
        "octaves": octaves,
        "one_shot": one_shot,
        "repeat": repeat,
        "samples_per_octave": samples_per_octave,
        "compression": COMPRESSION_NAMES.get(compression, "unknown"),
        "volume": volume / 0x10000,
        "channels": channels,
        "channel": channel,
        "frames": [len(ch) for ch in per_channel],
        "duration": duration,
        "peak": [max((abs(s) for s in ch), default=0) for ch in per_channel],
        "head": [ch[:4] for ch in per_channel],
    }
