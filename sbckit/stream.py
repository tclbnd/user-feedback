"""Walk SBC streams and A2DP payload dumps."""
from fractions import Fraction

from .header import SYNC, FrameError, parse


def frames_in(data):
    """Frames of a raw SBC stream; bytes between frames are skipped."""
    out, pos = [], 0
    while pos < len(data):
        if data[pos] != SYNC:
            pos += 1
            continue
        f = parse(data, pos)
        if pos + f["frame_length"] > len(data):
            break
        out.append(f)
        pos += f["frame_length"]
    return out


def a2dp_payloads(data):
    """Split a dump of A2DP media payloads (2-byte big-endian length prefix each)."""
    pos, out = 0, []
    while pos + 2 <= len(data):
        n = int.from_bytes(data[pos:pos + 2], "big")
        out.append(data[pos + 2:pos + 2 + n])
        pos += 2 + n
    return out


def a2dp_frames(data):
    out = []
    for payload in a2dp_payloads(data):
        pos = 0
        while pos < len(payload):
            f = parse(payload, pos)
            out.append(f)
            pos += f["frame_length"]
    return out


def summary(data, a2dp=False):
    frames = a2dp_frames(data) if a2dp else frames_in(data)
    if not frames:
        raise FrameError("no SBC frames")
    first = frames[0]
    samples = sum(f["samples"] for f in frames)
    return {
        "codec": "sbc",
        "sample_rate": first["sample_rate"],
        "channel_mode": first["channel_mode"],
        "blocks": first["blocks"],
        "subbands": first["subbands"],
        "allocation": first["allocation"],
        "bitpool": first["bitpool"],
        "frame_length": first["frame_length"],
        "bitrate": first["bitrate"],
        "frames": len(frames),
        "duration": str(Fraction(samples, first["sample_rate"])),
        "invalid_frames": 0,
        "lost_frames": 0,
    }
