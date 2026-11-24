"""Westwood Studios audio (.aud): header, chunks and IMA ADPCM decoding."""
import struct
from fractions import Fraction

# Westwood codec ids used in the AUD header.
CODECS = {1: "westwood_adpcm", 99: "ima_adpcm"}

# Every chunk starts with this id (little-endian on disk).
CHUNK_ID = 0x0000DEAF
CHUNK_HEADER = 8
FILE_HEADER = 12

# Standard IMA ADPCM step table (89 entries) and nibble index table.
IMA_STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 875, 962, 1058, 1164, 1280, 1408, 1548, 1703, 1873, 2060,
    2266, 2493, 2742, 3016, 3317, 3649, 4014, 4415, 4857, 5342, 5876, 6463,
    7109, 7820, 8602, 9462, 10408, 11449, 12594, 13853, 15238, 16762, 18438, 20282,
    22310, 24541, 26995, 29695, 32664, 35930, 39524, 43476, 47824, 52606, 57866, 63653,
    70018, 77020, 84722, 93194, 102514,
]
IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


class AudError(ValueError):
    pass


def decode_ima(data):
    """Decode IMA ADPCM nibbles (low nibble first) as one continuous stream."""
    predictor = 0
    step_index = 0
    samples = []
    for byte in data:
        for nibble in (byte & 0x0F, byte >> 4):
            step = IMA_STEP[step_index]
            diff = step >> 3
            if nibble & 4:
                diff += step
            if nibble & 2:
                diff += step >> 1
            if nibble & 1:
                diff += step >> 2
            if nibble & 8:
                predictor -= diff
            else:
                predictor += diff
            if predictor > 32767:
                predictor = 32767
            elif predictor < -32768:
                predictor = -32768
            step_index += IMA_INDEX[nibble & 7]
            if step_index < 0:
                step_index = 0
            elif step_index > 88:
                step_index = 88
            samples.append(predictor)
    return samples


def parse(data):
    if len(data) < FILE_HEADER:
        raise AudError("not a Westwood AUD file")
    rate, out_size, size, flags, codec = struct.unpack_from("<HIIBB", data)
    if rate == 0:
        raise AudError("not a Westwood AUD file")
    # Flag bit 0 marks stereo, bit 1 marks 16-bit samples.
    channels = 2 if flags & 1 else 1
    bits = 16 if flags & 2 else 8
    frame_size = channels * (bits // 8)

    # Only the bytes the header's compressed size covers belong to the audio.
    # The declared end may reach past the actual file when the file is cut short.
    declared_end = FILE_HEADER + size
    audio_end = min(declared_end, len(data))
    p = FILE_HEADER
    chunks = bad = in_bytes = out_bytes = 0
    truncated = len(data) < declared_end
    ima_stream = bytearray()

    while p < audio_end:
        if p + CHUNK_HEADER > len(data):
            # A chunk header itself is cut off by the end of the file.
            truncated = True
            break
        csize, cout, cid = struct.unpack_from("<HHI", data, p)
        chunks += 1
        if cid != CHUNK_ID:
            bad += 1
        payload_start = p + CHUNK_HEADER
        avail = min(csize, len(data) - payload_start)
        if avail < 0:
            avail = 0
        if avail < csize:
            truncated = True
        in_bytes += csize
        out_bytes += cout
        if avail:
            ima_stream += data[payload_start:payload_start + avail]
        p += CHUNK_HEADER + csize

    if p > audio_end:
        # The announced chunks do not fit inside the header's compressed size.
        truncated = True

    frames = out_size // frame_size if frame_size else 0
    result = {
        "sample_rate": rate,
        "channels": channels,
        "bits": bits,
        "codec": CODECS.get(codec, "type_%d" % codec),
        "chunks": chunks,
        "bad_chunk_ids": bad,
        "data_bytes": in_bytes,
        "header_size_ok": (not truncated) and in_bytes + CHUNK_HEADER * chunks == size,
        "output_bytes": out_size,
        "chunk_output_bytes": out_bytes,
        "frames": frames,
        "duration": str(Fraction(frames, rate)),
        "truncated": truncated,
        "peak": None,
        "last_sample": None,
    }

    if codec == 99:
        samples = decode_ima(ima_stream)
        if samples:
            peak = max(abs(sample) for sample in samples) / 32768
            result["peak"] = round(peak, 6)
            result["last_sample"] = samples[-1]

    return result
