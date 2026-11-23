"""IEC 61937 data bursts carried in 16-bit stereo PCM (S/PDIF captures saved as WAV).

A burst begins with the preamble words Pa (0xF872) and Pb (0x4E1F), followed by
Pc, Pd and the payload.  In Pc, bits 0-4 hold the data type, bit 7 the error
flag and bits 8-12 the bit stream number; for AC-3 and E-AC-3 bits 8-10 carry
bsmod instead.  Pd is the payload length in bits, except for E-AC-3, where it
counts payload bytes.
"""
import struct
from fractions import Fraction

PA, PB = 0xF872, 0x4E1F
TYPE_MASK = 0x1F
ERROR_FLAG = 0x0080
NULL, PAUSE = 0, 3
TYPES = {1: "AC-3", 3: "pause", 4: "MPEG-1 L1", 5: "MPEG-1 L2/L3", 6: "MPEG-2 ext", 7: "AAC", 8: "MPEG-2 L1 LSF",
         9: "MPEG-2 L2/L3 LSF", 11: "DTS-I", 12: "DTS-II", 13: "DTS-III", 21: "E-AC-3", 22: "TrueHD"}
# Repetition period of each data type, in sample frames (IEC 61937-2).
PERIODS = {1: 1536, 4: 384, 5: 1152, 6: 1152, 7: 1024, 8: 384, 9: 576,
           11: 512, 12: 1024, 13: 2048, 21: 1536, 22: 40}
# Types whose Pd counts payload bytes instead of bits.
BYTE_LENGTH_TYPES = (21,)
# Types whose Pc bits 8-10 hold bsmod instead of the bit stream number.
BSMOD_TYPES = (1, 21)
# Cap on the payload skipped after a preamble, so a corrupt Pd cannot swallow
# the rest of the capture (the largest real bursts are far shorter).
MAX_PAYLOAD = 1 << 16


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
            # Capture tools sometimes leave the size unset or stream it as
            # 0xFFFFFFFF; take the rest of the file in that case.
            end = p + 8 + size
            body = data[p + 8:] if size in (0, 0xFFFFFFFF) else data[p + 8:min(end, len(data))]
        p += 8 + size + (size & 1)
    if fmt is None or body is None:
        raise BurstError("WAV file without fmt or data chunk")
    if fmt[1] != 2 or fmt[3] != 16:
        raise BurstError("IEC 61937 needs 16-bit stereo PCM")
    return fmt[2], body


def preambles(body, swapped):
    """Payload word offset of every Pa/Pb pair, read in the given word order."""
    sync = struct.pack(">HH" if swapped else "<HH", PA, PB)
    hits, pos = [], body.find(sync)
    while pos >= 0:
        if not pos % 2:  # a whole number of 16-bit words from the data chunk
            hits.append(pos // 2)
        pos = body.find(sync, pos + 2)
    return hits


def payload_bytes(kind, pd):
    """Payload length in bytes; Pd counts bits, except for E-AC-3 (bytes)."""
    return pd if kind in BYTE_LENGTH_TYPES else (pd + 7) // 8


def stream_number(pc):
    """Bit stream number, Pc bits 8-12.

    For AC-3 and E-AC-3 these bits hold bsmod instead; see BSMOD_TYPES.
    """
    return (pc >> 8) & 0x1F


def parse(data):
    rate, body = wav(data)
    normal, swapped_hits = preambles(body, False), preambles(body, True)
    # Some capture tools store the two halves of every 16-bit sample
    # byte-swapped; the reading that finds the preambles is the right one.
    swapped = len(swapped_hits) > len(normal)
    hits = swapped_hits if swapped else normal
    order = ">" if swapped else "<"
    words = len(body) // 2

    streams, irregular, bsmode, period, previous = {}, {}, {}, {}, {}
    pauses = pause_frames = 0
    after_payload = 0  # first word following the previous burst's payload
    for i in hits:
        if i < after_payload:  # a pattern inside a payload, not a preamble
            continue
        if i + 4 > words:  # truncated preamble
            break
        pc, pd = struct.unpack_from(order + "HH", body, 2 * i + 4)
        kind = pc & TYPE_MASK
        size = payload_bytes(kind, pd)
        if size <= MAX_PAYLOAD:
            after_payload = i + 4 + size // 2
        if kind == NULL:
            continue
        if kind == PAUSE:
            pauses += 1
            if pd and i + 5 <= words:  # first payload word: gap announced, in frames
                pause_frames += struct.unpack_from(order + "H", body, 2 * (i + 4))[0]
            continue
        key = "%d/%s" % (stream_number(pc), TYPES.get(kind, "type_%d" % kind))
        s = streams.get(key)
        if s is None:
            s = streams[key] = {"bursts": 0, "payload_bytes": 0, "errors": 0, "seconds": "0"}
            period[key] = PERIODS.get(kind)
            bsmode[key] = (pc >> 8) & 0x07 if kind in BSMOD_TYPES else None
            irregular[key] = 0
        elif period[key] is not None and i // 2 - previous[key] != period[key]:
            irregular[key] += 1
        previous[key] = i // 2
        s["bursts"] += 1
        s["payload_bytes"] += size
        if pc & ERROR_FLAG:
            s["errors"] += 1
    for key, s in streams.items():
        s["seconds"] = str(Fraction(s["bursts"] * (period[key] or 0), rate))
    return {"sample_rate": rate, "byte_order": "big" if swapped else "little", "frames": len(body) // 4,
            "streams": streams, "pauses": pauses, "irregular": irregular, "bsmod": bsmode,
            "pause_frames": pause_frames}
