"""CRI ADX audio: header, versions 3 / 4 / 5 loop information, encryption, frames."""
import math
import struct
from fractions import Fraction

ENCODINGS = {2: "adx_preset", 3: "adx_standard", 4: "adx_exponential", 0x10: "ahx", 0x11: "ahx"}
CRI_SIGNATURE = b"(c)CRI"
END_OF_STREAM = 0x8001


class AdxError(ValueError):
    pass


class CoefficientPair(list):
    """JSON coefficient pair which also compares equal to a tuple pair."""

    def __eq__(self, other):
        if isinstance(other, tuple):
            return list.__eq__(self, list(other))
        return list.__eq__(self, other)


def _round_half_away(value):
    """Round to the nearest integer, with exact halves moved away from zero."""
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def prediction_coefficients(highpass, sample_rate):
    """Coefficients used by the ADX ADPCM predictor, in 12.4 fixed point."""
    if not highpass or not sample_rate:
        return None

    # Derivation used by CRI's ADX decoder.  The parameter c is the pole
    # placement obtained from the high-pass cutoff and the sample rate.
    a = math.sqrt(2.0) - math.cos(2.0 * math.pi * highpass / sample_rate)
    b = math.sqrt(2.0) - 1.0
    c = (1.0 - math.sqrt(a / b)) / (1.0 + math.sqrt(a / b))
    scale = 1 << 12

    coefficients = CoefficientPair([
        _round_half_away((1.0 - c) * (1.0 - c) * scale),
        _round_half_away((c * c - 1.0) * scale),
    ])
    if coefficients[0] == 0 or coefficients[1] == 0:
        return None
    return coefficients


def _unpack_loop(data, base, limit):
    """Unpack the loop record layout used by the supplied header offset."""
    if base < 0 or base + 20 > limit:
        return None
    enabled, start, start_byte, end, end_byte = struct.unpack_from(">IIIII", data, base)
    if not enabled:
        return None
    return {
        "loop": True,
        "loop_start": start,
        "loop_end": end,
        "loop_start_byte": start_byte,
        "loop_end_byte": end_byte,
    }


def _read_loop(data, data_offset):
    # ADX headers have grown additional fixed fields in newer encoder
    # revisions.  The loop words are still big-endian five uint32 values in
    # enabled/start/start-byte/end/end-byte order, but their absolute offset
    # is not the same for every header version.
    limit = min(data_offset, len(data))
    if limit < 20:
        return None

    # Check the conventional/versioned locations first.  This also accepts
    # records whose enable word is merely truthy, as older parsers did.
    preferred = (0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38)
    for base in preferred:
        loop = _unpack_loop(data, base, limit)
        if loop is not None:
            return loop

    # Fall back to a bounded search so headers from all CRI encoder versions
    # remain readable.  An enable word is a boolean one in valid files.
    for base in range(0x14, limit - 19):
        if base in preferred:
            continue
        if struct.unpack_from(">I", data, base)[0] != 1:
            continue
        loop = _unpack_loop(data, base, limit)
        if loop is not None:
            return loop
    return None


def parse(data):
    if len(data) < 0x14 or data[:2] != b"\x80\x00":
        raise AdxError("not an ADX file")

    copy_off, enc, block, bits, channels, rate, total, highpass, version, flags = struct.unpack_from(
        ">HBBBBIIHBB", data, 2
    )

    # The uint16 at offset 2 points two bytes into the copyright string and
    # four bytes before the first audio frame.  The six-byte "(c)CRI"
    # signature therefore ends at the data offset.
    data_offset = copy_off + 4
    copyright_ok = (
        6 <= data_offset <= len(data)
        and data[data_offset - 6:data_offset] == CRI_SIGNATURE
    )

    key_type = flags if flags in (8, 9) else None
    out = {
        "encoding": ENCODINGS.get(enc, "type_%d" % enc),
        "block_size": block,
        "bit_depth": bits,
        "channels": channels,
        "sample_rate": rate,
        "total_samples": total,
        "duration": str(Fraction(total, rate)) if rate else None,
        "highpass": highpass,
        "version": version,
        "encrypted": key_type is not None,
        "copyright_ok": copyright_ok,
        "data_offset": data_offset,
        "loop": False,
        "loop_start": None,
        "loop_end": None,
        "loop_start_byte": None,
        "loop_end_byte": None,
        "samples_per_block": None,
        "data_frames": 0,
        "data_samples": 0,
        "key_type": key_type,
        "coefficients": prediction_coefficients(highpass, rate) if enc in (3, 4) else None,
        "max_scale": None,
        "silent_blocks": 0,
    }

    loop = _read_loop(data, data_offset)
    if loop is not None:
        out.update(loop)

    if block >= 2 and bits > 0 and channels > 0:
        # A block opens with a 16-bit scale word.  The rest is packed sample
        # data, so the count depends on the actual block and bit depths.
        samples_per_block = ((block - 2) * 8) // bits
        out["samples_per_block"] = samples_per_block

        frame_bytes = block * channels
        frames = 0
        silent_blocks = 0
        max_scale = None
        p = data_offset

        while p + frame_bytes <= len(data):
            if struct.unpack_from(">H", data, p)[0] == END_OF_STREAM:
                break

            for channel in range(channels):
                scale_offset = p + channel * block
                scale = struct.unpack_from(">H", data, scale_offset)[0]
                if max_scale is None or scale > max_scale:
                    max_scale = scale
                if scale == 0:
                    silent_blocks += 1

            frames += 1
            p += frame_bytes

        out["data_frames"] = frames
        out["data_samples"] = frames * samples_per_block
        out["max_scale"] = max_scale
        out["silent_blocks"] = silent_blocks

    return out
