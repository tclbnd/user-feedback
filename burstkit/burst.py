"""IEC 61937 data bursts carried in 16-bit stereo PCM (S/PDIF captures saved as WAV)."""
import struct
from fractions import Fraction

PA, PB = 0xF872, 0x4E1F
TYPES = {1: "AC-3", 3: "pause", 4: "MPEG-1 L1", 5: "MPEG-1 L2/L3", 6: "MPEG-2 ext", 7: "AAC", 8: "MPEG-2 L1 LSF",
         9: "MPEG-2 L2/L3 LSF", 11: "DTS-I", 12: "DTS-II", 13: "DTS-III", 21: "E-AC-3", 22: "TrueHD"}
FRAME = 1536


class BurstError(ValueError):
    pass


def wav(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise BurstError("not a WAV file")
    p, fmt, body = 12, None, None
    while p + 8 <= len(data):
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        if cid == b"fmt ":
            tag, ch, rate = struct.unpack_from("<HHI", data, p + 8)
            bits = struct.unpack_from("<H", data, p + 22)[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            body = data[p + 8:p + 8 + size]
        p += 8 + size
    if fmt is None or body is None:
        raise BurstError("WAV file without fmt or data chunk")
    if fmt[1] != 2 or fmt[3] != 16:
        raise BurstError("IEC 61937 needs 16-bit stereo PCM")
    return fmt[2], body


def parse(data):
    rate, body = wav(data)
    n = len(body) // 2
    words = struct.unpack("<%dH" % n, body[:2 * n])
    streams, pauses = {}, 0
    for frame in range(0, n // 2, FRAME):
        i = frame * 2
        if i + 3 >= n or words[i] != PA or words[i + 1] != PB:
            continue
        pc, pd = words[i + 2], words[i + 3]
        kind = pc & 0xFF
        if kind == 3:
            pauses += 1
            continue
        name = TYPES.get(kind, "type_%d" % kind)
        s = streams.setdefault("0/%s" % name, {"bursts": 0, "payload_bytes": 0, "errors": 0, "seconds": "0"})
        s["bursts"] += 1
        s["payload_bytes"] += (pd + 7) // 8
        s["seconds"] = str(Fraction(s["bursts"] * FRAME, rate))
    return {"sample_rate": rate, "byte_order": "little", "frames": len(body) // 4, "streams": streams, "pauses": pauses}
