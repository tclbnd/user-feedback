"""MPEG audio frames and the VBR info frames written by encoders."""
from fractions import Fraction

VERSIONS = {3: "1", 2: "2"}
LAYERS = {3: 1, 2: 2, 1: 3}
RATES = {"1": (44100, 48000, 32000), "2": (22050, 24000, 16000)}
BITRATES = {
    ("1", 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    ("1", 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    ("1", 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    ("2", 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    ("2", 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
BITRATES[("2", 3)] = BITRATES[("2", 2)]
MODES = ("stereo", "joint_stereo", "dual_channel", "mono")
SAMPLES_PER_FRAME = {1: 384, 2: 1152, 3: 1152}


class Mp3Error(ValueError):
    pass


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
    length = 144 * kbps * 1000 // rate
    return {"version": version, "layer": layer, "rate": rate, "kbps": kbps, "length": length,
            "spf": SAMPLES_PER_FRAME[layer], "mode": MODES[b3 >> 6], "crc": not (b1 & 1)}


def vbr_info(data, pos, h):
    """Xing header inside the first frame, or None."""
    x = pos + 36
    if data[x:x + 4] == b"Xing":
        flags = int.from_bytes(data[x + 4:x + 8], "big")
        frames = int.from_bytes(data[x + 8:x + 12], "big") if flags & 1 else None
        return {"kind": "Xing", "frames": frames, "delay": None, "padding": None}
    return None


def scan(data):
    pos, frames, first, info, audio = 0, 0, None, None, 0
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
        "id3v2_bytes": 0,
        "version": first["version"], "layer": first["layer"], "sample_rate": first["rate"],
        "channel_mode": first["mode"], "crc": first["crc"],
        "vbr_header": info["kind"] if info else None,
        "bitrate_mode": "VBR" if info else "CBR",
        "header_frames": info["frames"] if info else None,
        "frames": frames, "audio_bytes": audio,
        "duration": str(Fraction(samples, first["rate"])),
        "encoder_delay": delay, "encoder_padding": padding,
        "gapless_samples": samples - delay - padding if delay is not None and padding is not None else None,
    }
