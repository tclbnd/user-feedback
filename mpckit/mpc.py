"""Musepack SV8 streams: packet keys and sizes, stream header, replay gain, encoder info."""
import struct
import zlib
from fractions import Fraction

RATES = (44100, 48000, 37800, 32000)
FRAME = 1152


class MpcError(ValueError):
    pass


def varint(data, pos):
    return data[pos] & 0x7F, 1


def packets(data, pos):
    out = []
    while pos + 3 <= len(data):
        key = data[pos:pos + 2]
        if not (65 <= key[0] <= 90 and 65 <= key[1] <= 90):
            break
        size, n = varint(data, pos + 2)
        if size < 2 + n or pos + size > len(data):
            raise MpcError("packet %s runs past the end of the file" % key.decode())
        out.append((key.decode(), data[pos + 2 + n:pos + size]))
        pos += size
        if key == b"SE":
            break
    return out


def parse(data):
    start = 0
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
            samples, n = varint(p, q); q += n
            silence, n = varint(p, q); q += n
            b1, b2 = p[q], p[q + 1]
            out["sample_rate"] = RATES[b1 >> 5] if b1 >> 5 < 4 else None
            out["max_band"] = (b1 & 0x1F) + 1
            out["channels"] = (b2 >> 4) + 1
            out["mid_side"] = bool(b2 & 8)
            out["frames_per_packet"] = b2 & 7
            out["samples"], out["beginning_silence"] = samples, silence
        elif key == "RG":
            ver, tg, tp, ag, ap = struct.unpack_from(">BHHHH", p, 0)
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
        out["duration"] = str(Fraction(out["samples"], out["sample_rate"]))
        out["frames"] = -(-out["samples"] // FRAME)
    return out
