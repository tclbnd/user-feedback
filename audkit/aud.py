"""Westwood Studios audio (.aud): header, chunks and IMA ADPCM decoding.

Header (all little-endian)::

    uint16 sample rate
    uint32 size           compressed bytes that follow the header
    uint32 output size    bytes once decompressed
    uint8  flags          bit 0: stereo, bit 1: 16 bit samples
    uint8  codec          1: Westwood ADPCM, 99: IMA ADPCM

Each chunk header is ``uint16 compressed size``, ``uint16 output size`` and the
``uint32`` chunk id ``0x0000DEAF``, all little-endian.  Only the bytes the
header's compressed size covers belong to the audio; a file (or the compressed
region) that stops inside a chunk is truncated.
"""
import struct
from fractions import Fraction

HEADER_SIZE = 12
CHUNK_ID = 0x0000DEAF
CODECS = {1: "westwood_adpcm", 99: "ima_adpcm"}

IMA_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)

IMA_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41,
    45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190,
    209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724,
    796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272,
    2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132,
    7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350,
    22385, 24623, 27086, 29794, 32767,
)

# Magnitude change for every step size and the three low nibble bits; the IMA
# nibble value is step/8 + (step, step/2, step/4 for bits 4, 2, 1).
IMA_DELTA_TABLE = tuple(
    tuple(
        (step >> 3)
        + (step if nibble & 4 else 0)
        + ((step >> 1) if nibble & 2 else 0)
        + ((step >> 2) if nibble & 1 else 0)
        for nibble in range(8)
    )
    for step in IMA_STEP_TABLE
)


class AudError(ValueError):
    pass


class ImaAdpcmDecoder:
    """IMA ADPCM decoder whose predictor and step index survive between calls.

    Westwood packs two samples per byte, low nibble first, and runs the whole
    chunk list through one decoder state.
    """

    def __init__(self):
        self.predictor = 0
        self.index = 0

    def decode(self, data):
        """Decode compressed bytes, continuing from the previous call."""
        predictor = self.predictor
        index = self.index
        deltas = IMA_DELTA_TABLE
        index_steps = IMA_INDEX_TABLE
        samples = []
        for byte in data:
            for nibble in (byte & 0x0F, byte >> 4):
                delta = deltas[index][nibble & 7]
                if nibble & 8:
                    predictor -= delta
                    if predictor < -32768:
                        predictor = -32768
                else:
                    predictor += delta
                    if predictor > 32767:
                        predictor = 32767
                index += index_steps[nibble & 7]
                if index < 0:
                    index = 0
                elif index > 88:
                    index = 88
                samples.append(predictor)
        self.predictor = predictor
        self.index = index
        return samples


def _read_chunks(data, size):
    """Walk the chunks inside the bytes the header's compressed size covers.

    Returns the chunk payloads (the last one may be cut short), the chunk
    counts and byte totals, and whether the audio stops inside a chunk.
    """
    end = min(len(data), HEADER_SIZE + size)
    truncated = len(data) < HEADER_SIZE + size
    payloads = []
    chunks = bad = in_bytes = out_bytes = 0
    p = HEADER_SIZE
    while p < end:
        if p + 8 > end:
            truncated = True                # file ends inside a chunk header
            break
        csize, cout, cid = struct.unpack_from("<HHI", data, p)
        chunks += 1
        if cid != CHUNK_ID:
            bad += 1
        in_bytes += csize
        out_bytes += cout
        start = p + 8
        stop = start + csize
        if stop > end:
            truncated = True                # the last chunk is cut short
            stop = end
        payloads.append(data[start:stop])
        p = start + csize
    return payloads, chunks, bad, in_bytes, out_bytes, truncated


def parse(data):
    if len(data) < HEADER_SIZE:
        raise AudError("not a Westwood AUD file")
    rate, size, out_size, flags, codec = struct.unpack_from("<HIIBB", data)
    if rate == 0:
        raise AudError("not a Westwood AUD file")
    channels, bits = (2 if flags & 0x01 else 1), (16 if flags & 0x02 else 8)
    payloads, chunks, bad, in_bytes, out_bytes, truncated = _read_chunks(data, size)
    frames = out_size // (channels * (bits // 8))
    peak = last_sample = None
    if CODECS.get(codec) == "ima_adpcm":
        decoder = ImaAdpcmDecoder()
        largest = 0
        for payload in payloads:
            samples = decoder.decode(payload)
            if samples:
                last_sample = samples[-1]
                largest = max(largest, max(abs(sample) for sample in samples))
        if last_sample is not None:
            peak = "%.6f" % (largest / 32768.0)
    return {"sample_rate": rate, "channels": channels, "bits": bits, "codec": CODECS.get(codec, "type_%d" % codec),
            "chunks": chunks, "bad_chunk_ids": bad, "data_bytes": in_bytes, "header_size_ok": in_bytes + 8 * chunks == size,
            "output_bytes": out_size, "chunk_output_bytes": out_bytes, "frames": frames,
            "duration": str(Fraction(frames, rate)), "truncated": truncated, "peak": peak, "last_sample": last_sample}
