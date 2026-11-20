"""Stream summary and the decoder config the MP4 muxer needs."""
from fractions import Fraction

from . import adts


class _BitWriter:
    """Minimal MSB-first bit buffer for AudioSpecificConfig."""

    def __init__(self):
        self._bits = []

    def put(self, value, width):
        for shift in range(width - 1, -1, -1):
            self._bits.append((value >> shift) & 1)

    def bytes(self):
        out = bytearray()
        for i in range(0, len(self._bits), 8):
            chunk = self._bits[i:i + 8]
            byte = 0
            for bit in chunk:
                byte = (byte << 1) | bit
            byte <<= 8 - len(chunk)
            out.append(byte)
        return bytes(out)


def audio_specific_config(f):
    """Build the AudioSpecificConfig (ISO/IEC 14496-3 1.6.2.1).

    The ADTS profile field is one less than the MPEG-4 audioObjectType, so
    the object type parsed from the header is emitted directly.
    """
    w = _BitWriter()
    if f.object_type >= 31:
        w.put(31, 5)
        w.put(f.object_type - 32, 6)
    else:
        w.put(f.object_type, 5)
    if f.freq_index == adts.EXPLICIT_RATE_INDEX:
        if f.sample_rate is None:
            raise adts.AdtsError(
                "explicit sampling frequency index but no sample rate available")
        w.put(adts.EXPLICIT_RATE_INDEX, 4)
        w.put(f.sample_rate, 24)
    else:
        w.put(f.freq_index, 4)
    w.put(f.channel_config, 4)
    # AAC LC/Main/SSR config ends here; sbr/ps signalling would follow.
    return w.bytes().hex()


def probe(data):
    fs = list(adts.frames(data))
    if not fs:
        raise adts.AdtsError("no ADTS frames")
    first = fs[0]
    # Every raw_data_block carries 1024 PCM samples; a frame may hold several.
    raw_blocks = sum(f.raw_blocks for f in fs)
    samples = raw_blocks * adts.SAMPLES_PER_RAW_BLOCK
    # Payload handed to the muxer excludes the header and the 16 bit CRC.
    payload = sum(f.length - f.header_size for f in fs)
    result = {
        "mpeg_version": first.mpeg_version,
        "object_type": first.object_type,
        "sample_rate": first.sample_rate,
        "channels": first.channels,
        "channel_config": first.channel_config,
        "crc": first.crc,
        "frames": len(fs),
        "raw_blocks": raw_blocks,
        "samples": samples,
        "payload_bytes": payload,
        "audio_specific_config": audio_specific_config(first),
    }
    if first.sample_rate is not None:
        result["duration"] = str(Fraction(samples, first.sample_rate))
    else:
        result["duration"] = None
    return result
