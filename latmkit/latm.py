"""LOAS / LATM (ISO/IEC 14496-3 subpart 1) with AAC payloads."""
from fractions import Fraction

from .bits import Reader

RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)
CHANNELS = (0, 1, 2, 3, 4, 5, 6, 7)


class LatmError(ValueError):
    pass


def _rate(r):
    idx = r.u(4)
    if idx >= len(RATES):
        raise LatmError("reserved sampling frequency index")
    return RATES[idx]


def _aot(r):
    a = r.u(5)
    return 32 + r.u(6) if a == 31 else a


def audio_specific_config(r):
    aot = _aot(r)
    rate = _rate(r)
    chconf = r.u(4)
    ext_rate = None
    if aot not in (1, 2, 3, 4, 5, 6, 7, 17, 19, 20, 21, 22, 23, 29):
        raise LatmError("unsupported audio object type %d" % aot)
    frame_len_flag = r.u(1)
    if r.u(1):
        r.u(14)
    ext_flag = r.u(1)
    if chconf == 0:
        raise LatmError("program_config_element not supported")
    if aot in (6, 20):
        r.u(3)
    if ext_flag:
        if aot == 22:
            r.u(5)
            r.u(11)
        if aot in (17, 19, 20, 23):
            r.u(3)
        r.u(1)
    return {"object_type": aot, "sample_rate": rate, "ext_sample_rate": ext_rate,
            "channels": CHANNELS[chconf] if chconf < 8 else None, "frame_length": 1024}


def latm_value(r):
    n = r.u(2)
    v = 0
    for _ in range(max(n, 1)):
        v = (v << 8) | r.u(8)
    return v


def stream_mux_config(r):
    version = r.u(1)
    version_a = r.u(1) if version == 1 else 0
    if version_a:
        raise LatmError("audioMuxVersionA 1 not supported")
    same_framing = r.u(1)
    subframes = r.u(6) + 1
    programs = r.u(4) + 1
    layers = r.u(3) + 1
    if programs != 1 or layers != 1:
        raise LatmError("multiple programs or layers not supported")
    if version == 0:
        asc = audio_specific_config(r)
    else:
        latm_value(r)
        asc = audio_specific_config(r)
    frame_type = r.u(3)
    if frame_type != 0:
        raise LatmError("frameLengthType %d not supported" % frame_type)
    r.u(8)
    if r.u(1):
        if version == 1:
            latm_value(r)
        else:
            r.u(8)
    crc_present = r.u(1)
    return {"version": version, "crc": crc_present, "same_framing": same_framing, "subframes": subframes, **asc}


def scan(data):
    pos, frames, aus, payload, configs = 0, 0, 0, 0, 0
    cfg = None
    while pos + 3 <= len(data):
        head = (data[pos] << 16) | (data[pos + 1] << 8) | data[pos + 2]
        if head >> 13 != 0x2B7:
            break
        length = head & 0x1FFF
        end = pos + 3 + length
        if end > len(data):
            break
        r = Reader(data[pos + 3:end])
        if not r.u(1):
            cfg = stream_mux_config(r)
            configs += 1
        if cfg is None:
            pos = end
            continue
        for _ in range(1):
            n = r.u(8)
            r.pos += 8 * n
            payload += n
            aus += 1
        frames += 1
        pos = end
    if cfg is None:
        raise LatmError("no StreamMuxConfig found")
    return {
        "mux_version": cfg["version"], "object_type": cfg["object_type"], "sample_rate": cfg["sample_rate"],
        "ext_sample_rate": cfg["ext_sample_rate"], "ps": False, "channels": cfg["channels"], "frame_length": cfg["frame_length"],
        "loas_frames": frames, "access_units": aus, "configs": configs, "payload_bytes": payload,
        "duration": str(Fraction(aus * cfg["frame_length"], cfg["sample_rate"])),
    }
