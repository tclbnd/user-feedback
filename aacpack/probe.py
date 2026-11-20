"""Stream summary and the decoder config the MP4 muxer needs."""
from fractions import Fraction

from . import adts


def audio_specific_config(f):
    bits = (f.object_type << 11) | (f.freq_index << 7) | (f.channel_config << 3)
    return bits.to_bytes(2, "big").hex()


def probe(data):
    fs = list(adts.frames(data))
    if not fs:
        raise adts.AdtsError("no ADTS frames")
    first = fs[0]
    samples = len(fs) * 1024
    payload = sum(f.length - f.header_size for f in fs)
    return {
        "mpeg_version": first.mpeg_version,
        "object_type": first.object_type,
        "sample_rate": first.sample_rate,
        "channels": first.channels,
        "crc": first.crc,
        "frames": len(fs),
        "raw_blocks": len(fs),
        "samples": samples,
        "duration": str(Fraction(samples, first.sample_rate)),
        "payload_bytes": payload,
        "audio_specific_config": audio_specific_config(first),
    }
