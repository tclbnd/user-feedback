"""MPEG audio frames and the VBR info frames written by encoders."""
from fractions import Fraction

VERSIONS = {0: "2.5", 2: "2", 3: "1"}
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
    ("2", 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
# Low sampling-rate streams (MPEG-2.5) reuse the MPEG-2 bitrate tables.
BITRATES[("2.5", 1)] = BITRATES[("2", 1)]
BITRATES[("2.5", 2)] = BITRATES[("2", 2)]
BITRATES[("2.5", 3)] = BITRATES[("2", 3)]
MODES = ("stereo", "joint_stereo", "dual_channel", "mono")
# Layer III side information size in bytes as (stereo, mono), by version.
SIDE_INFO = {"1": (32, 17), "2": (17, 9), "2.5": (17, 9)}


class Mp3Error(ValueError):
    pass


def samples_per_frame(version, layer):
    """Samples produced by one frame: Layer III halves at MPEG-2/2.5."""
    if layer == 1:
        return 384
    if layer == 2:
        return 1152
    return 1152 if version == "1" else 576


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
    pad = (b2 >> 1) & 1
    if layer == 1:
        # Layer I slots are 4 bytes and every frame carries a 32-bit header.
        length = (12 * kbps * 1000 // rate + pad) * 4
    elif layer == 3 and version != "1":
        # MPEG-2/2.5 Layer III halves the samples per frame.
        length = 72 * kbps * 1000 // rate + pad
    else:
        length = 144 * kbps * 1000 // rate + pad
    return {"version": version, "layer": layer, "rate": rate, "kbps": kbps, "length": length,
            "spf": samples_per_frame(version, layer), "mode": MODES[b3 >> 6],
            "crc": not (b1 & 1)}


def _encoder_tag(data, start, frame_end):
    """Encoder delay and padding from the LAME-style tag after a Xing header.

    The 36-byte tag starts with a 9-byte encoder name and version (LAME3.100,
    Lavc57.64, ...) and stores the delay and padding as two 12-bit values in
    bytes 21..23.
    """
    end = min(frame_end, len(data))
    if start + 24 > end:
        return None, None
    name = data[start:start + 9]
    if not (0x41 <= name[0] <= 0x5A or 0x61 <= name[0] <= 0x7A):
        return None, None  # no encoder name, so no delay/padding recorded
    if any(not 0x20 <= byte < 0x7F for byte in name) or not any(
            0x30 <= byte <= 0x39 for byte in name):
        return None, None
    value = int.from_bytes(data[start + 21:start + 24], "big")
    return value >> 12, value & 0xFFF


def _xing_info(data, x, frame_end):
    """Xing/Info header at x (followed by an optional LAME tag)."""
    end = min(frame_end, len(data))
    if x + 8 > end:
        return None
    kind = data[x:x + 4].decode("ascii")
    flags = int.from_bytes(data[x + 4:x + 8], "big")
    cursor = x + 8
    frames = None
    if flags & 1:
        if cursor + 4 > end:
            return None
        frames = int.from_bytes(data[cursor:cursor + 4], "big")
        cursor += 4
    if flags & 2:
        cursor += 4
    if flags & 4:
        cursor += 100
    if flags & 8:
        cursor += 4
    delay, padding = _encoder_tag(data, cursor, end)
    return {"kind": kind, "frames": frames, "delay": delay, "padding": padding}


def _vbri_info(data, x, frame_end):
    """Fraunhofer VBRI header at x: delay at +6, frame count at +14."""
    end = min(frame_end, len(data))
    if x + 18 > end:
        return None
    delay = int.from_bytes(data[x + 6:x + 8], "big")
    frames = int.from_bytes(data[x + 14:x + 18], "big")
    # VBRI records a delay but no padding, so gapless stays unknown.
    return {"kind": "VBRI", "frames": frames, "delay": delay, "padding": None}


def vbr_info(data, pos, h):
    """Info frame (Xing/Info/VBRI) at the start of a frame, or None."""
    if h["layer"] != 3:  # only Layer III frames carry an encoder info frame
        return None
    side = SIDE_INFO[h["version"]][0 if h["mode"] != "mono" else 1]
    frame_end = pos + h["length"]
    # Encoders put the Xing/Info tag after the side information; be lenient
    # about a CRC word so both protected and unprotected frames are found.
    candidates = [pos + 4 + side, pos + 4 + side + 2]
    # Fraunhofer VBRI headers sit 32 bytes after the 4-byte frame header.
    candidates.append(pos + 36)
    for x in dict.fromkeys(candidates):
        tag = data[x:x + 4]
        if tag in (b"Xing", b"Info"):
            info = _xing_info(data, x, frame_end)
        elif tag == b"VBRI":
            info = _vbri_info(data, x, frame_end)
        else:
            continue
        if info is not None:
            return info
    return None


def _syncsafe(value):
    return (value[0] << 21) | (value[1] << 14) | (value[2] << 7) | value[3]


def id3v2_bytes(data):
    """Length of the ID3v2 tag(s) at the start of the file."""
    pos = total = 0
    while pos + 10 <= len(data) and data[pos:pos + 3] == b"ID3":
        major, flags = data[pos + 3], data[pos + 5]
        if major not in (2, 3, 4) or data[pos + 4] == 0xFF:
            break
        length = 10 + _syncsafe(data[pos + 6:pos + 10])
        if major == 4 and flags & 0x10:  # ID3v2.4 footer follows the tag body
            length += 10
        total += length
        pos += length
    return total


def scan(data):
    start = id3v2_bytes(data)
    pos, frames, first, info, audio = start, 0, None, None, 0
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
        "id3v2_bytes": start,
        "version": first["version"], "layer": first["layer"], "sample_rate": first["rate"],
        "channel_mode": first["mode"], "crc": first["crc"],
        "vbr_header": info["kind"] if info else None,
        "bitrate_mode": "VBR" if info and info["kind"] in ("Xing", "VBRI") else "CBR",
        "header_frames": info["frames"] if info else None,
        "frames": frames, "audio_bytes": audio,
        "duration": str(Fraction(samples, first["rate"])),
        "encoder_delay": delay, "encoder_padding": padding,
        "gapless_samples": samples - delay - padding if delay is not None and padding is not None else None,
    }
