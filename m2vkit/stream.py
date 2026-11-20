"""Walk start codes of a video elementary stream and summarise it."""
from fractions import Fraction

from . import headers


class StreamError(ValueError):
    pass


def units(data):
    """Yield ``(start_code_value, payload)`` for every start code."""
    pos = data.find(b"\x00\x00\x01")
    while 0 <= pos < len(data) - 3:
        nxt = data.find(b"\x00\x00\x01", pos + 3)
        end = len(data) if nxt < 0 else nxt
        yield data[pos + 3], data[pos + 4:end]
        pos = nxt


def summary(data):
    seq = None
    ext = {}
    pictures = {"I": 0, "P": 0, "B": 0, "D": 0}
    gops = 0
    for code, payload in units(data):
        if code == 0xB3:
            if seq is None:
                seq = headers.sequence_header(payload)
        elif code == 0xB5 and payload and payload[0] >> 4 == 1:
            ext = headers.sequence_extension(payload)
        elif code == 0xB8:
            gops += 1
        elif code == 0x00:
            kind = headers.picture_header(payload)["type"]
            pictures[kind] = pictures.get(kind, 0) + 1
    if seq is None:
        raise StreamError("no sequence header")
    rate = headers.FRAME_RATES[seq["frame_rate_code"]]
    if rate is None:
        raise StreamError("reserved frame rate code")
    frames = sum(pictures.values())
    aspect = headers.ASPECT_RATIOS.get(seq["aspect_code"])
    return {
        "standard": "MPEG-2",
        "width": seq["width"],
        "height": seq["height"],
        "display_width": seq["width"],
        "display_height": seq["height"],
        "dar": aspect,
        "sar": aspect,
        "frame_rate": f"{rate}/1",
        "bit_rate": seq["bit_rate_value"] * 400,
        "vbv_buffer_size": seq["vbv_value"] * 16 * 1024,
        "profile": ext.get("profile"),
        "level": ext.get("level"),
        "chroma_format": "4:2:0",
        "progressive_sequence": ext.get("progressive_sequence", True),
        "pictures": pictures,
        "frames": frames,
        "gops": gops,
        "duration": str(Fraction(frames, rate)),
    }
