"""Westwood Studios audio (.aud): header, chunk headers and IMA ADPCM.

File header (little-endian, 12 bytes):

    uint16 sample_rate
    uint32 compressed_size   bytes of chunk headers + chunk data that follow
    uint32 uncompressed_size decoded PCM bytes
    uint8  flags             bit 0: stereo, bit 1: 16-bit, bit 2: signed
    uint8  codec             1 = Westwood ADPCM, 99 = IMA ADPCM

Each chunk has an 8-byte little-endian header followed by its payload:

    uint16 compressed_size   payload bytes
    uint16 uncompressed_size decoded bytes
    uint32 id                always 0x0000DEAF

Only the bytes the header's compressed size covers belong to the audio.
IMA ADPCM chunks carry no per-chunk state: the whole file is one nibble
stream and the decoder state continues across every chunk boundary.
"""
import struct
from fractions import Fraction

WESTWOOD_ADPCM = 1
IMA_ADPCM = 99
CHUNK_ID = 0x0000DEAF
HEADER_SIZE = 12
CHUNK_HEADER_SIZE = 8

CODECS = {WESTWOOD_ADPCM: "westwood_adpcm", IMA_ADPCM: "ima_adpcm"}

# Canonical IMA ADPCM step table (89 entries); indices 63..88 are clamped.
_STEP_TABLE = [
    89, 97, 106, 116, 127, 139, 152, 167, 183, 201, 221, 243, 267, 293, 322, 354,
    389, 427, 469, 515, 566, 622, 684, 752, 827, 909, 999, 1098, 1207, 1327,
    1459, 1604, 1764, 1940, 2134, 2347, 2581, 2839, 3122, 3434, 3777, 4154,
    4569, 5025, 5527, 6079, 6686, 7354, 8089, 8897, 9786, 10764, 11840, 13024,
    14326, 15758, 17333, 19066, 20972, 23069, 25375, 27912, 30723,
] + [32767] * 26

# Step-index adjustment per nibble; nibble sign bit (0x08) sets the direction.
_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


class AudError(ValueError):
    pass


class ImaAdpcmDecoder:
    """Stateful IMA ADPCM decoder; the low nibble of each byte comes first."""

    def __init__(self):
        self.predictor = 0
        self.step_index = 0
        self.last_sample = None
        self.peak_magnitude = 0
        self.samples = 0

    def feed(self, data):
        step_table = _STEP_TABLE
        index_table = _INDEX_TABLE
        predictor = self.predictor
        step_index = self.step_index
        peak_magnitude = self.peak_magnitude
        samples = self.samples
        for byte in data:
            for nibble in (byte & 0x0F, byte >> 4):
                step = step_table[step_index]
                diff = step >> 3
                if nibble & 0x01:
                    diff += step >> 2
                if nibble & 0x02:
                    diff += step >> 1
                if nibble & 0x04:
                    diff += step
                predictor += -diff if nibble & 0x08 else diff
                if predictor > 32767:
                    predictor = 32767
                elif predictor < -32768:
                    predictor = -32768
                step_index += index_table[nibble]
                if step_index < 0:
                    step_index = 0
                elif step_index > 88:
                    step_index = 88
                magnitude = predictor if predictor >= 0 else -predictor
                if magnitude > peak_magnitude:
                    peak_magnitude = magnitude
                samples += 1
        self.predictor = predictor
        self.step_index = step_index
        self.last_sample = predictor
        self.peak_magnitude = peak_magnitude
        self.samples = samples
        return self


def parse(data):
    if len(data) < HEADER_SIZE:
        raise AudError("not a Westwood AUD file")
    rate, compressed_size, output_size, flags, codec = struct.unpack_from(
        "<HIIBB", data, 0)
    if rate == 0:
        raise AudError("not a Westwood AUD file")

    # bit 0: stereo (else mono); bit 1: 16-bit (else 8-bit); bit 2: signed
    channels = 2 if flags & 0x01 else 1
    bits = 16 if flags & 0x02 else 8

    # Only the bytes the header's compressed size covers belong to the audio.
    audio_end = HEADER_SIZE + compressed_size
    if audio_end > len(data):
        truncated = True
        audio_end = len(data)
    else:
        truncated = False

    pos = HEADER_SIZE
    chunks = bad = in_bytes = out_bytes = 0
    decoder = ImaAdpcmDecoder() if codec == IMA_ADPCM else None
    while pos + CHUNK_HEADER_SIZE <= audio_end:
        csize, cout, cid = struct.unpack_from("<HHI", data, pos)
        payload_start = pos + CHUNK_HEADER_SIZE
        payload_end = payload_start + csize
        if payload_end > audio_end:
            # Final chunk header exists but its payload is missing/partial.
            truncated = True
            break
        if cid != CHUNK_ID:
            bad += 1
        chunks += 1
        in_bytes += csize
        out_bytes += cout
        if decoder is not None:
            decoder.feed(data[payload_start:payload_end])
        pos = payload_end
    else:
        # A trailing partial chunk header at the boundary is a missing chunk.
        if pos != audio_end:
            truncated = True

    if decoder is not None and decoder.samples:
        peak = float("%.6f" % (decoder.peak_magnitude / 32768.0))
        last_sample = decoder.last_sample
    else:
        peak = None
        last_sample = None

    frame_size = channels * bits // 8
    frames = output_size // frame_size
    return {
        "sample_rate": rate,
        "channels": channels,
        "bits": bits,
        "codec": CODECS.get(codec, "type_%d" % codec),
        "chunks": chunks,
        "bad_chunk_ids": bad,
        "data_bytes": in_bytes,
        "header_size_ok": in_bytes + CHUNK_HEADER_SIZE * chunks == compressed_size,
        "output_bytes": output_size,
        "chunk_output_bytes": out_bytes,
        "frames": frames,
        "duration": str(Fraction(frames, rate)),
        "truncated": truncated,
        "peak": peak,
        "last_sample": last_sample,
    }

