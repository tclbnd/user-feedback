"""Sony Wave64 (.w64) container.

A Wave64 file starts with the RIFF GUID and a 64-bit file size, followed by
the WAVE GUID and a sequence of chunks.  Every chunk is a 16-byte GUID, an
8-byte little-endian size (header included) and the chunk body; chunks whose
size is not a multiple of 8 are followed by zero padding to the next 8-byte
boundary.
"""
import struct
from fractions import Fraction

# Sony Wave64 GUIDs, stored as the 16 bytes found in the file (mixed-endian
# encoding as produced by Microsoft's GUID serialization).
RIFF_GUID = bytes.fromhex("726966662E91CF11A5D628DB04C10000")
WAVE_GUID = bytes.fromhex("77617665F3ACD3118CD100C04F8EDB8A")
FMT_GUID = bytes.fromhex("666D7420F3ACD3118CD100C04F8EDB8A")
DATA_GUID = bytes.fromhex("64617461F3ACD3118CD100C04F8EDB8A")
LIST_GUID = bytes.fromhex("6C6973742F91CF11A5D628DB04C10000")
FACT_GUID = bytes.fromhex("66616374F3ACD3118CD100C04F8EDB8A")
LEVL_GUID = bytes.fromhex("6C65766CF3ACD3118CD100C04F8EDB8A")
JUNK_GUID = bytes.fromhex("6A756E6BF3ACD3118CD100C04F8EDB8A")

# WAVEFORMATEXTENSIBLE sub-format GUIDs (the data1 word carries the legacy
# WAVEFORMATEX format tag).
SUB_PCM = bytes.fromhex("0100000000001000800000AA00389B71")
SUB_FLOAT = bytes.fromhex("0300000000001000800000AA00389B71")

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

GUID_NAMES = {
    FMT_GUID: "fmt",
    DATA_GUID: "data",
    FACT_GUID: "fact",
    LIST_GUID: "list",
    LEVL_GUID: "levl",
    JUNK_GUID: "junk",
}

HEADER_SIZE = 24  # 16-byte GUID + 8-byte size


class W64Error(ValueError):
    pass


def chunks(data):
    """Return ``(name, body, start)`` tuples for every chunk in file order."""
    if len(data) < HEADER_SIZE + 16 or data[:16] != RIFF_GUID:
        raise W64Error("not a Wave64 file")
    riff_size = struct.unpack_from("<Q", data, 16)[0]
    if riff_size > len(data):
        raise W64Error("truncated Wave64 file")
    if data[24:40] != WAVE_GUID:
        raise W64Error("not a Wave64 file")
    pos, out = 40, []
    while pos + HEADER_SIZE <= len(data):
        guid = data[pos:pos + 16]
        if not guid.strip(b"\x00"):
            # Zero padding after the final chunk, not another chunk header.
            break
        size = struct.unpack_from("<Q", data, pos + 16)[0]
        if size < HEADER_SIZE or pos + size > len(data):
            raise W64Error("invalid chunk at offset %d" % pos)
        name = GUID_NAMES.get(guid, "unknown")
        body = data[pos + HEADER_SIZE:pos + size]
        out.append((name, body, pos))
        # The size covers header plus body; chunks are 8-byte aligned.
        pos += size + ((-size) % 8)
    return out


def _parse_format(body):
    """Return the effective format description from a fmt chunk body."""
    if len(body) < 16:
        raise W64Error("truncated fmt chunk")
    tag, channels, rate, _avg_bytes, align, container_bits = struct.unpack_from(
        "<HHIIHH", body
    )
    cb_size = struct.unpack_from("<H", body, 16)[0] if len(body) >= 18 else 0
    bits = container_bits
    if tag == WAVE_FORMAT_EXTENSIBLE:
        if len(body) < 40 or cb_size < 22:
            raise W64Error("truncated WAVEFORMATEXTENSIBLE fmt chunk")
        bits, _channel_mask = struct.unpack_from("<HI", body, 18)
        sub_format = body[24:40]
        if sub_format == SUB_PCM:
            kind, tag = "pcm", WAVE_FORMAT_PCM
        elif sub_format == SUB_FLOAT:
            kind, tag = "float", WAVE_FORMAT_IEEE_FLOAT
        else:
            raise W64Error("unsupported extensible sub-format")
        if bits == 0:
            bits = container_bits
    elif tag == WAVE_FORMAT_PCM:
        kind = "pcm"
    elif tag == WAVE_FORMAT_IEEE_FLOAT:
        kind = "float"
    else:
        raise W64Error("unsupported format tag %#x" % tag)
    if channels == 0 or container_bits == 0 or align == 0:
        raise W64Error("invalid fmt chunk")
    return {
        "format": kind,
        "channels": channels,
        "sample_rate": rate,
        "bits": bits,
        "container_bits": container_bits,
        "block_align": align,
    }


def _integer_peaks(audio, channels, align, width, bits, unsigned):
    """Peak relative to full scale for packed integer PCM samples."""
    frames = len(audio) // align
    pad_bits = width * 8 - bits
    full_scale = 2 ** (bits - 1)
    peaks = [0.0] * channels
    int_code = {1: "b", 2: "h", 4: "i", 8: "q"}.get(width)
    for frame in range(frames):
        base = frame * align
        for channel in range(channels):
            start = base + channel * width
            if unsigned:
                # 8-bit PCM is unsigned, centred on 128.
                value = audio[start] - 128
            elif int_code is not None:
                value = struct.unpack_from("<%c" % int_code, audio, start)[0]
            else:
                value = int.from_bytes(
                    audio[start:start + width], "little", signed=True
                )
            # Samples wider than their valid width are stored left-aligned
            # (e.g. 24-bit audio in a 32-bit container); shift the padding
            # off the low end before comparing against valid full scale.
            if pad_bits:
                value >>= pad_bits
            level = abs(value) / full_scale
            if level > peaks[channel]:
                peaks[channel] = level
    return peaks, frames


def _float_peaks(audio, channels, align, width):
    """Peak relative to 1.0 for IEEE floating-point samples."""
    code = {4: "f", 8: "d"}.get(width)
    if code is None:
        raise W64Error("unsupported float width %d" % width)
    frames = len(audio) // align
    peaks = [0.0] * channels
    for frame in range(frames):
        base = frame * align
        for channel in range(channels):
            value = struct.unpack_from(
                "<%c" % code, audio, base + channel * width
            )[0]
            level = abs(value)
            if level > peaks[channel]:
                peaks[channel] = level
    return peaks, frames


def summary(data):
    found = chunks(data)
    fmt_body = next((b for n, b, _ in found if n == "fmt"), None)
    audio = next((b for n, b, _ in found if n == "data"), None)
    if fmt_body is None or audio is None:
        raise W64Error("missing fmt or data chunk")
    info = _parse_format(fmt_body)
    channels = info["channels"]
    align = info["block_align"]
    width = info["container_bits"] // 8
    if info["format"] == "float":
        peaks, frames = _float_peaks(audio, channels, align, width)
    elif info["container_bits"] == 8:
        peaks, frames = _integer_peaks(
            audio, channels, align, width, info["bits"], unsigned=True
        )
    else:
        peaks, frames = _integer_peaks(
            audio, channels, align, width, info["bits"], unsigned=False
        )
    return {
        "format": info["format"],
        "channels": channels,
        "sample_rate": info["sample_rate"],
        "bits": info["bits"],
        "container_bits": info["container_bits"],
        "block_align": align,
        "frames": frames,
        "duration": str(Fraction(frames, info["sample_rate"])),
        "peak": [round(p, 6) for p in peaks],
        "chunks": [n for n, _, _ in found],
    }
