"""id Software RoQ movies: chunks, codebooks, video frames, DPCM audio."""
import struct
from fractions import Fraction

HEADER, INFO, CODEBOOK, VQ, JPEG, MONO, STEREO, PACKET = 0x1084, 0x1001, 0x1002, 0x1011, 0x1012, 0x1020, 0x1021, 0x1030
AUDIO_RATE = 22050


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def s8(v):
    return v - 0x100 if v & 0x80 else v


def codebook_units(v):
    # A count byte of 0 means the full 256-entry codebook.
    return v if v else 256


class RoqError(ValueError):
    pass


def parse(data):
    if len(data) < 8:
        raise RoqError("not a RoQ movie")
    cid, size, fps = struct.unpack_from("<HIH", data, 0)
    if cid != HEADER or size != 0xFFFFFFFF:
        raise RoqError("not a RoQ movie")
    out = {"fps": fps, "width": None, "height": None, "frames": 0, "jpeg_frames": 0, "codebooks": 0,
           "cells_2x2": 0, "cells_4x4": 0, "audio_channels": None, "audio_samples": 0,
           "video_duration": None, "audio_duration": "0", "audio_predictors": None, "packets": 0,
           "unknown_chunks": 0, "truncated": False}
    p = 8
    # Chunks share the file header's layout: 16-bit id, 32-bit size, 16-bit argument.
    while p + 8 <= len(data):
        cid, size, arg = struct.unpack_from("<HIH", data, p)
        body = p + 8
        if body + size > len(data):
            out["truncated"] = True
            break
        if cid == INFO:
            out["width"], out["height"] = struct.unpack_from("<HH", data, body)
        elif cid == CODEBOOK:
            n2 = codebook_units((arg >> 8) & 0xFF)
            n4 = codebook_units(arg & 0xFF)
            out["codebooks"] += 1
            out["cells_2x2"] += n2
            out["cells_4x4"] += n4
        elif cid in (VQ, JPEG):
            # Both VQ and JPEG chunks are video frames; JPEG appears from Quake III on.
            out["frames"] += 1
            if cid == JPEG:
                out["jpeg_frames"] += 1
        elif cid in (MONO, STEREO):
            ch = 1 if cid == MONO else 2
            out["audio_channels"] = out["audio_channels"] or ch
            if out["audio_predictors"] is None:
                if ch == 1:
                    out["audio_predictors"] = [s16(arg)]
                else:
                    out["audio_predictors"] = [s8((arg >> 8) & 0xFF), s8(arg & 0xFF)]
            # The size counts bytes: one per mono sample frame, two per stereo sample frame.
            out["audio_samples"] += size if ch == 1 else size // 2
        elif cid == PACKET:
            out["packets"] += 1
        else:
            out["unknown_chunks"] += 1
        p = body + size
    if out["fps"]:
        out["video_duration"] = str(Fraction(out["frames"], out["fps"]))
    out["audio_duration"] = str(Fraction(out["audio_samples"], AUDIO_RATE))
    return out
