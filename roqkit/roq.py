"""id Software RoQ movies: chunks, codebooks, video frames, DPCM audio."""
import struct
from fractions import Fraction

HEADER, INFO, CODEBOOK, VQ, JPEG, MONO, STEREO, PACKET = 0x1084, 0x1001, 0x1002, 0x1011, 0x1012, 0x1020, 0x1021, 0x1030
AUDIO_RATE = 22050

# The movie header and every chunk preamble are 8 bytes wide: a 16-bit id, a
# 32-bit little-endian field and a 16-bit argument.  In a chunk the 32-bit
# field is the size of the payload that follows the preamble, so the preamble
# is not part of the size: the next chunk starts size + 8 bytes on.  The
# argument carries chunk specific data: the codebook cell counts, or the
# initial DPCM predictors of an audio chunk.
FILE_HEADER = struct.Struct("<HIH")
CHUNK_HEADER = struct.Struct("<HIH")


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def s8(v):
    return v - 0x100 if v & 0x80 else v


class RoqError(ValueError):
    pass


def parse(data):
    if len(data) < FILE_HEADER.size:
        raise RoqError("not a RoQ movie")
    cid, size, fps = FILE_HEADER.unpack_from(data, 0)
    if cid != HEADER or size != 0xFFFFFFFF:
        raise RoqError("not a RoQ movie")
    out = {"fps": fps, "width": None, "height": None, "frames": 0, "jpeg_frames": 0, "codebooks": 0,
           "cells_2x2": 0, "cells_4x4": 0, "audio_channels": None, "audio_samples": 0,
           "video_duration": None, "audio_duration": "0", "audio_predictors": None, "packets": 0,
           "unknown_chunks": 0, "truncated": False}
    p = FILE_HEADER.size
    while p + CHUNK_HEADER.size <= len(data):
        cid, size, arg = CHUNK_HEADER.unpack_from(data, p)
        body = p + CHUNK_HEADER.size
        if body + size > len(data):
            out["truncated"] = True
            break
        if cid == INFO:
            if size >= 4:
                out["width"], out["height"] = struct.unpack_from("<HH", data, body)
        elif cid == CODEBOOK:
            # The argument holds the 2x2 cell count in its high byte and the 4x4
            # cell count in its low byte.  A zero byte means a full codebook of
            # 256 cells: the counts are kept in a byte, so 256 wraps to zero.
            out["codebooks"] += 1
            out["cells_2x2"] += ((arg >> 8) & 0xFF) or 256
            out["cells_4x4"] += (arg & 0xFF) or 256
        elif cid in (VQ, JPEG):
            out["frames"] += 1
            if cid == JPEG:
                out["jpeg_frames"] += 1
        elif cid in (MONO, STEREO):
            channels = 1 if cid == MONO else 2
            if out["audio_channels"] is None:
                out["audio_channels"] = channels
                # Mono chunks carry one 16-bit predictor; stereo chunks pack
                # two 8-bit predictors, left in the high byte.
                out["audio_predictors"] = ([s16(arg)] if channels == 1 else
                                           [s8(arg >> 8), s8(arg & 0xFF)])
            # DPCM data is one byte per sample per channel, without a header.
            out["audio_samples"] += size // channels
        elif cid == PACKET:
            out["packets"] += 1
        else:
            out["unknown_chunks"] += 1
        p = body + size
    if out["fps"]:
        out["video_duration"] = str(Fraction(out["frames"], out["fps"]))
    out["audio_duration"] = str(Fraction(out["audio_samples"], AUDIO_RATE))
    return out
