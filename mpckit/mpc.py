"""Musepack SV8 streams: packet keys and sizes, stream header, replay gain, encoder info.

Reference: Musepack SV8 bitstream specification. Every packet is a two-byte key
(uppercase ASCII) followed by the total packet size as a big-endian 7-bit
variable-length integer and then the payload. The stream starts with the
"MPCK" signature, optionally preceded by an ID3v2 tag, and ends at the "SE"
packet; anything after "SE" is trailing tag data, not a packet.
"""
import struct
import zlib
from fractions import Fraction

RATES = (44100, 48000, 37800, 32000)
FRAME = 1152


class MpcError(ValueError):
    pass


def varint(data, pos):
    """Read an SV8 big-endian 7-bit varint; return (value, new position)."""
    value = 0
    n = 0
    while pos < len(data):
        byte = data[pos]
        value = (value << 7) | (byte & 0x7F)
        pos += 1
        n += 1
        if not byte & 0x80:
            return value, pos
        if n >= 8:
            raise MpcError("variable-length integer is too long")
    raise MpcError("variable-length integer runs past the end of the file")


def id3v2_size(data):
    """Return the size in bytes of an ID3v2 tag prefix, or 0 if there is none."""
    if data[:3] != b"ID3" or len(data) < 10:
        return 0
    # 2.3/2.4 header: "ID3", version(2), flags(1), synchsafe size (4x7 bits).
    sync_size = ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14
                 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
    return 10 + sync_size


def packets(data, pos):
    out = []
    while pos + 3 <= len(data):
        key = data[pos:pos + 2]
        if not (65 <= key[0] <= 90 and 65 <= key[1] <= 90):
            break
        size, after = varint(data, pos + 2)
        if size < after - pos or pos + size > len(data):
            raise MpcError("packet %s runs past the end of the file" % key.decode())
        out.append((key.decode(), data[after:pos + size]))
        pos += size
        if key == b"SE":
            break
    return out


def parse(data):
    start = id3v2_size(data)
    if data[start:start + 4] != b"MPCK":
        raise MpcError("not a Musepack SV8 stream")
    pk = packets(data, start + 4)
    out = {"id3v2_bytes": start, "version": None, "sample_rate": None, "channels": None, "max_band": None,
           "mid_side": None, "frames_per_packet": None, "samples": None, "beginning_silence": None,
           "duration": None, "header_crc_ok": None, "audio_packets": 0, "frames": None,
           "replay_gain": None, "encoder": None, "chapters": 0, "stream_end": False,
           "packets": [k for k, _ in pk]}
    for key, p in pk:
        if key == "SH":
            crc, = struct.unpack_from(">I", p, 0)
            out["header_crc_ok"] = zlib.crc32(p[4:]) & 0xFFFFFFFF == crc
            out["version"] = p[4]
            q = 5
            samples, q = varint(p, q)
            silence, q = varint(p, q)
            b1, b2 = p[q], p[q + 1]
            rate_index = b1 >> 5
            out["sample_rate"] = RATES[rate_index] if rate_index < len(RATES) else None
            out["max_band"] = (b1 & 0x1F) + 1
            out["channels"] = (b2 >> 4) + 1
            out["mid_side"] = bool(b2 & 8)
            # Low three bits are a power of two: frames per audio packet = 2 ** value.
            out["frames_per_packet"] = 1 << (b2 & 7)
            out["samples"], out["beginning_silence"] = samples, silence
        elif key == "RG":
            # Gains are signed 16-bit fixed-point values, 1/256 dB per LSB.
            ver, tg, tp, ag, ap = struct.unpack_from(">BhHhH", p, 0)
            out["replay_gain"] = {"title_gain": str(Fraction(tg, 256)), "title_peak": tp,
                                  "album_gain": str(Fraction(ag, 256)), "album_peak": ap}
        elif key == "EI":
            out["encoder"] = {"profile": str(Fraction(p[0] >> 1, 8)), "pns": bool(p[0] & 1),
                              "version": "%d.%d.%d" % (p[1], p[2], p[3])}
        elif key == "AP":
            out["audio_packets"] += 1
        elif key == "CT":
            out["chapters"] += 1
        elif key == "SE":
            out["stream_end"] = True
    if out["samples"] is not None and out["sample_rate"]:
        playable = out["samples"] - out["beginning_silence"]
        out["duration"] = str(Fraction(playable, out["sample_rate"]))
        out["frames"] = -(-out["samples"] // FRAME)
    return out
