"""Creative Voice File (.voc) blocks."""
import struct
from fractions import Fraction

MAGIC = b"Creative Voice File\x1a"
CODECS = {0: "pcm_u8", 1: "adpcm4", 2: "adpcm26", 3: "adpcm2",
          4: "pcm_s16", 6: "alaw", 7: "ulaw"}

# Samples produced per byte of compressed Creative ADPCM data, per channel.
ADPCM_SPB = {"adpcm4": 2, "adpcm26": 3, "adpcm2": 4}
PCM_CODECS = ("pcm_u8", "pcm_s16")
HEADER_SIZE = 26


class VocError(ValueError):
    pass


def blocks(data):
    if data[:20] != MAGIC:
        raise VocError("not a Creative Voice File")
    header_size, version = struct.unpack_from("<HH", data, 20)
    if header_size < HEADER_SIZE:
        raise VocError("invalid header size")
    pos, out = header_size, []
    while pos < len(data):
        kind = data[pos]
        if kind == 0:
            break
        length = int.from_bytes(data[pos + 1:pos + 4], "little")
        out.append((kind, data[pos + 4:pos + 4 + length]))
        pos += 4 + length
    return version, out


def _rate(tc):
    return Fraction(1000000, 256 - tc)


def _rate_ext(tc):
    return Fraction(256000000, 65536 - tc)


def _spb(codec, width):
    """Samples per byte (per channel) for a sound codec."""
    if codec in ADPCM_SPB:
        return ADPCM_SPB[codec]
    if width == 16:
        return Fraction(1, 2)
    return Fraction(1)


def _frames(byte_count, codec, width, channels):
    return byte_count * _spb(codec, width) / Fraction(channels)


def _peak(chunk, codec, width):
    if codec == "pcm_u8":
        return max((abs(b - 128) for b in chunk), default=0)
    return max((abs(v) for v, in struct.iter_unpack("<h", chunk)), default=0)


def scan(data):
    version, found = blocks(data)
    total = Fraction(0)
    counts = {}
    markers = []
    texts = []
    first = None            # (rate, channels, codec, width)
    current = None          # format of the last sound block, reused by type 2
    pending_ext = None      # format from a type 8 block for the next type 1 block
    peak = 0
    have_peak = False

    def add_sound(chunk, fmt):
        nonlocal total, peak, have_peak, first
        rate, channels, codec, width = fmt
        if first is None:
            first = fmt
        total += _frames(len(chunk), codec, width, channels) / rate
        if codec in PCM_CODECS and fmt == first:
            peak = max(peak, _peak(chunk, codec, width))
            have_peak = True

    for kind, body in found:
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 1:
            if len(body) < 2:
                raise VocError("truncated sound block")
            if pending_ext is not None:
                # A type 8 block overrides the tc/pack bytes in this block.
                current = pending_ext
                pending_ext = None
            else:
                tc, pack = body[0], body[1]
                codec = CODECS.get(pack, "unknown")
                current = (_rate(tc), 1, codec, 16 if codec == "pcm_s16" else 8)
            add_sound(body[2:], current)
        elif kind == 2:
            if current is None:
                raise VocError("continuation block without a sound block")
            add_sound(body, current)
        elif kind == 3:
            if len(body) < 3:
                raise VocError("truncated silence block")
            n, tc = struct.unpack_from("<HB", body)
            # The stored count is one less than the number of silent samples.
            total += Fraction(n + 1) / _rate(tc)
        elif kind == 4:
            if len(body) < 2:
                raise VocError("truncated marker block")
            markers.append(struct.unpack_from("<H", body)[0])
        elif kind == 5:
            texts.append(body.rstrip(b"\x00").decode("latin-1"))
        elif kind in (6, 7):
            pass  # repeat / end-repeat: the repeated block is not expanded
        elif kind == 8:
            if len(body) < 4:
                raise VocError("truncated extended block")
            tc, pack, stereo = struct.unpack_from("<HBB", body)
            codec = CODECS.get(pack, "unknown")
            current = (_rate_ext(tc), 2 if stereo else 1, codec,
                       16 if codec == "pcm_s16" else 8)
            # Must be followed by a type 1 block, whose own header is ignored.
            pending_ext = current
        elif kind == 9:
            if len(body) < 12:
                raise VocError("truncated new-format block")
            rate_num, bits, channels, pack = struct.unpack_from("<IBBH", body)
            codec = CODECS.get(pack, "unknown")
            current = (Fraction(rate_num, 1), channels, codec,
                       16 if codec == "pcm_s16" else 8)
            pending_ext = None
            add_sound(body[12:], current)
        else:
            raise VocError("unsupported block type %d" % kind)

    return {
        "version": "%d.%02d" % (version >> 8, version & 0xFF),
        "blocks": {str(k): v for k, v in sorted(counts.items())},
        "sample_rate": None if first is None else float(round(first[0], 3)),
        "channels": None if first is None else first[1],
        "codec": None if first is None else first[2],
        "duration": str(total),
        "peak": peak if have_peak else None,
        "markers": markers,
        "texts": texts,
    }
