"""CD-Text (Red Book subcode text): 18-byte packs, CRC, text strings."""
import struct

FIELDS = {0x80: "title", 0x81: "performer", 0x82: "songwriter", 0x83: "composer", 0x84: "arranger", 0x85: "message"}


class CdTextError(ValueError):
    pass


def crc16(b):
    c = 0
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = (c << 1) ^ 0x1021 if c & 0x8000 else c << 1
            c &= 0xFFFF
    return c


def parse(data):
    if not data or len(data) % 18:
        raise CdTextError("not a whole number of CD-Text packs")
    packs = [data[i:i + 18] for i in range(0, len(data), 18)]
    bad = sum(1 for p in packs if crc16(p[:16]) != struct.unpack(">H", p[16:18])[0])
    tracks = {}
    for p in packs:
        kind, track, block = p[0], p[1] & 0x7F, (p[3] >> 4) & 7
        if block != 0 or kind not in FIELDS:
            continue
        for raw in p[4:16].split(b"\0"):
            if raw:
                tracks.setdefault(track, {})[FIELDS[kind]] = raw.decode("latin-1")
    return {"packs": len(packs), "crc_errors": bad,
            "blocks": [{"block": 0, "charset": None, "language": None, "double_byte": False, "album": {},
                        "tracks": [dict(number=k, **v) for k, v in sorted(tracks.items())]}]}
