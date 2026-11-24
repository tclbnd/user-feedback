"""id Software RoQ movies: chunks, codebooks, video frames, DPCM audio."""
import struct
from fractions import Fraction

HEADER, INFO, CODEBOOK, VQ, JPEG, MONO, STEREO, PACKET = 0x1084, 0x1001, 0x1002, 0x1011, 0x1012, 0x1020, 0x1021, 0x1030
AUDIO_RATE = 22050


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


class RoqError(ValueError):
    pass


def parse(data):
    if len(data) < 8:
        raise RoqError("not a RoQ movie")
    cid, size, fps = struct.unpack_from("<HIH", data, 0)
    if cid != HEADER or size != 0xFFFFFFFF:
        raise RoqError("not a RoQ movie")
    fps = 30
    out = {"fps": fps, "width": None, "height": None, "frames": 0, "jpeg_frames": 0, "codebooks": 0,
           "cells_2x2": 0, "cells_4x4": 0, "audio_channels": None, "audio_samples": 0,
           "video_duration": None, "audio_duration": "0", "audio_predictors": None, "packets": 0,
           "unknown_chunks": 0, "truncated": False}
    p = 8
    while p + 8 <= len(data):
        cid, size, arg = struct.unpack_from("<HHH", data, p)
        body = p + 6
        if body + size > len(data):
            out["truncated"] = True
            break
        if cid == INFO:
            out["width"], out["height"] = struct.unpack_from("<HH", data, body)
        elif cid == CODEBOOK:
            n2, n4 = (arg >> 8) & 0xFF, arg & 0xFF
            out["codebooks"] += 1
            out["cells_2x2"] += n2
            out["cells_4x4"] += n4
        elif cid == VQ:
            out["frames"] += 1
        elif cid in (MONO, STEREO):
            ch = 1 if cid == MONO else 2
            out["audio_channels"] = out["audio_channels"] or ch
            if out["audio_predictors"] is None:
                out["audio_predictors"] = [s16(arg)] if ch == 1 else [arg >> 8, arg & 0xFF]
            out["audio_samples"] += size
        elif cid == PACKET:
            out["packets"] += 1
        else:
            out["unknown_chunks"] += 1
        p = body + size
    if out["fps"]:
        out["video_duration"] = str(Fraction(out["frames"], out["fps"]))
    out["audio_duration"] = str(Fraction(out["audio_samples"], AUDIO_RATE))
    return out
