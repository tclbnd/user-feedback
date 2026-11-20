"""Video headers carried after start codes."""
from .bits import BitReader

FRAME_RATES = (None, 24, 24, 25, 30, 30, 50, 60, 60)
ASPECT_RATIOS = {1: "1:1", 2: "4:3", 3: "16:9", 4: "221:100"}
PICTURE_TYPES = {1: "I", 2: "P", 3: "B", 4: "D"}
PROFILES = {1: "High", 2: "Spatial", 3: "SNR", 4: "Main", 5: "Simple"}
LEVELS = {4: "High", 6: "High 1440", 8: "Main", 10: "Low"}


def sequence_header(payload):
    r = BitReader(payload)
    out = {
        "width": r.read(12),
        "height": r.read(12),
        "aspect_code": r.read(4),
        "frame_rate_code": r.read(4),
        "bit_rate_value": r.read(18),
    }
    r.skip(1)
    out["vbv_value"] = r.read(10)
    return out


def sequence_extension(payload):
    r = BitReader(payload)
    r.skip(4)
    pl = r.read(8)
    return {
        "profile": PROFILES.get((pl >> 4) & 7),
        "level": LEVELS.get(pl & 15),
        "progressive_sequence": bool(r.read(1)),
    }


def gop_header(payload):
    r = BitReader(payload)
    r.skip(25)
    return {"closed": bool(r.read(1))}


def picture_header(payload):
    r = BitReader(payload)
    r.skip(10)
    return {"type": PICTURE_TYPES.get(r.read(3), "?")}
