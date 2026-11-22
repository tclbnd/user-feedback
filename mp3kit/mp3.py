"""MPEG audio frames and the VBR info frames written by encoders.

Frame headers for MPEG-1, MPEG-2 and MPEG-2.5 (all layers) are decoded here,
along with the ``Xing``/``Info``/``VBRI`` headers encoders store in the first
frame.  Tag bytes in front of the audio (ID3v2) and at the end of the file
(ID3v1, APE) are never counted as audio.
"""
from fractions import Fraction

# Version bits -> version label; bit value 1 is reserved.
VERSIONS = {3: "1", 2: "2", 0: "2.5"}
# Layer bits -> layer number; bit value 0 is reserved.
LAYERS = {3: 1, 2: 2, 1: 3}
RATES = {
    "1": (44100, 48000, 32000),
    "2": (22050, 24000, 16000),
    "2.5": (11025, 12000, 8000),
}
BITRATES = {
    ("1", 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    ("1", 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    ("1", 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    ("2", 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    ("2", 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
# MPEG-2.5 uses the MPEG-2 bitrate tables.
BITRATES[("2.5", 1)] = BITRATES[("2", 1)]
BITRATES[("2.5", 2)] = BITRATES[("2", 2)]
BITRATES[("2.5", 3)] = BITRATES[("2", 3)] = BITRATES[("2", 2)]
MODES = ("stereo", "joint_stereo", "dual_channel", "mono")
# Side information bytes, used to locate a Xing/Info header.
SIDE_INFO = {("1", False): 32, ("1", True): 17, ("2", False): 17,
             ("2", True): 9, ("2.5", False): 17, ("2.5", True): 9}


class Mp3Error(ValueError):
    pass


def samples_per_frame(version, layer):
    """Samples produced by one frame (Layer III shrinks in MPEG-2/2.5)."""
    if layer == 1:
        return 384
    if layer == 2:
        return 1152
    return 1152 if version == "1" else 576


def frame_length(version, layer, kbps, rate, padding):
    """Bytes in one frame, including its padding slot when present."""
    if layer == 1:
        return (12 * kbps * 1000 // rate + padding) * 4
    if layer == 3 and version != "1":
        return 72 * kbps * 1000 // rate + padding
    return 144 * kbps * 1000 // rate + padding


def header(data, pos):
    """Decode a frame header at pos, or return None when it cannot start a frame."""
    if pos + 4 > len(data) or data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
        return None
    b1, b2, b3 = data[pos + 1], data[pos + 2], data[pos + 3]
    vbits, lbits = (b1 >> 3) & 3, (b1 >> 1) & 3
    if vbits not in VERSIONS or lbits == 0:
        return None
    version, layer = VERSIONS[vbits], LAYERS[lbits]
    bindex, rindex = b2 >> 4, (b2 >> 2) & 3
    if bindex in (0, 15) or rindex == 3:
        return None
    kbps = BITRATES[(version, layer)][bindex]
    rate = RATES[version][rindex]
    padding = (b2 >> 1) & 1
    return {"version": version, "layer": layer, "rate": rate, "kbps": kbps,
            "length": frame_length(version, layer, kbps, rate, padding),
            "spf": samples_per_frame(version, layer), "mode": MODES[b3 >> 6],
            "padding": padding, "crc": not (b1 & 1)}


def _syncsafe(raw):
    return ((raw[0] & 0x7F) << 21) | ((raw[1] & 0x7F) << 14) | \
           ((raw[2] & 0x7F) << 7) | (raw[3] & 0x7F)


def id3v2_bytes(data):
    """Total length of the ID3v2 tag(s) in front of the audio."""
    pos = 0
    while pos + 10 <= len(data) and data[pos:pos + 3] == b"ID3":
        size = 10 + _syncsafe(data[pos + 6:pos + 10])
        # An ID3v2.4 footer, when flagged, sits after the body.
        if data[pos + 3] == 4 and data[pos + 5] & 0x10:
            size += 10
        if size < 10 or pos + size > len(data):
            break
        pos += size
    return pos


def _lame_gapless(data, start):
    """Encoder delay and padding from a LAME-style tag at start."""
    if start + 24 > len(data) or data[start:start + 3] not in (b"LAM", b"Lav"):
        return None, None
    delay = (data[start + 21] << 4) | (data[start + 22] >> 4)
    padding = ((data[start + 22] & 0x0F) << 8) | data[start + 23]
    return delay, padding


def _xing_info(data, pos, h):
    """Xing/Info header in the first frame, or None."""
    side = SIDE_INFO[(h["version"], h["mode"] == "mono")]
    x = pos + 4 + (2 if h["crc"] else 0) + side
    if data[x:x + 4] not in (b"Xing", b"Info"):
        return None
    kind = data[x:x + 4].decode("ascii")
    flags = int.from_bytes(data[x + 4:x + 8], "big")
    field = x + 8
    frames = None
    if flags & 0x1:
        if field + 4 > len(data):
            return None
        frames = int.from_bytes(data[field:field + 4], "big")
        field += 4
    if flags & 0x2:
        field += 4  # stream bytes
    if flags & 0x4:
        field += 100  # seek table
    if flags & 0x8:
        field += 4  # quality indicator
    delay, padding = _lame_gapless(data, field)
    if delay is None:
        # Some writers always reserve the full Xing layout.
        delay, padding = _lame_gapless(data, x + 120)
    return {"kind": kind, "frames": frames, "delay": delay, "padding": padding}


def _vbri_info(data, pos, h):
    """Fraunhofer VBRI header in the first frame, or None."""
    x = pos + 36  # VBRI always follows the 4-byte header plus 32 bytes
    if data[x:x + 4] != b"VBRI" or x + 18 > len(data):
        return None
    delay = int.from_bytes(data[x + 6:x + 8], "big")
    frames = int.from_bytes(data[x + 14:x + 18], "big")
    return {"kind": "VBRI", "frames": frames, "delay": delay, "padding": None}


def vbr_info(data, pos, h):
    """Info frame (Xing, Info or VBRI) inside the first frame, or None."""
    return _xing_info(data, pos, h) or _vbri_info(data, pos, h)


def scan(data):
    tag_bytes = id3v2_bytes(data)
    pos, frames, first, info, audio = tag_bytes, 0, None, None, 0
    while pos + 4 <= len(data):
        h = header(data, pos)
        if h is None or pos + h["length"] > len(data):
            break
        if first is None:
            first = h
            info = vbr_info(data, pos, h)
            if info is not None:
                pos += h["length"]
                continue
        frames += 1
        audio += h["length"]
        pos += h["length"]
    if first is None:
        raise Mp3Error("no MPEG audio frames")
    samples = frames * first["spf"]
    delay = info["delay"] if info else None
    padding = info["padding"] if info else None
    return {
        "id3v2_bytes": tag_bytes,
        "version": first["version"], "layer": first["layer"], "sample_rate": first["rate"],
        "channel_mode": first["mode"], "crc": first["crc"],
        "vbr_header": info["kind"] if info else None,
        "bitrate_mode": "VBR" if info and info["kind"] != "Info" else "CBR",
        "header_frames": info["frames"] if info else None,
        "frames": frames, "audio_bytes": audio,
        "duration": str(Fraction(samples, first["rate"])),
        "encoder_delay": delay, "encoder_padding": padding,
        "gapless_samples": samples - delay - padding if delay is not None and padding is not None else None,
    }
