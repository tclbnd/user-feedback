"""Westwood Studios audio (.aud): header, chunks and IMA ADPCM decoding.

Westwood AUD layout (all multi-byte integers are little-endian):

File header (12 bytes)
    u16 sample_rate
    u32 compressed_size   bytes of audio that follow the file header
    u32 uncompressed_size decoded PCM bytes the whole stream should yield
    u8  flags             bit0: 16-bit samples, bit1: stereo, bit2: signed
    u8  codec             1 = Westwood SND1 ADPCM, 99 = IMA ADPCM

Chunk header (8 bytes)
    u16 compressed_size   compressed payload bytes in this chunk
    u16 uncompressed_size decoded bytes this chunk yields
    u16 id                0xDEAF on every Westwood chunk
    u16 number            chunk number (little-endian)

Only the compressed_size bytes named by the file header belong to the audio.
"""
import struct
from fractions import Fraction

CODECS = {1: "westwood_adpcm", 99: "ima_adpcm"}

CHUNK_ID = 0xDEAF
HEADER_SIZE = 12
CHUNK_HEADER_SIZE = 8

# Standard IMA ADPCM step table (89 entries) and index adjustment table.
IMA_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37,
    41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173,
    190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
    724, 796, 875, 962, 1058, 1164, 1280, 1408, 1548, 1703, 1873, 2060,
    2266, 2492, 2741, 3015, 3316, 3648, 4012, 4414, 4855, 5340, 5874,
    6461, 7107, 7818, 8600, 9460, 10406, 11446, 12591, 13850, 15235,
    16758, 18433, 20277, 22304, 24534, 26986, 29684, 32652,
)
IMA_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)


class AudError(ValueError):
    pass


class _ImaState:
    """One IMA ADPCM channel: predictor and step index, kept across chunks."""

    def __init__(self):
        self.predictor = 0
        self.index = 0

    def decode_nibble(self, nibble):
        step = IMA_STEP_TABLE[self.index]
        # Rebuild the difference from the 4-bit nibble, as Westwood's
        # decoder (FFmpeg ADPCM_IMA_WS) does.
        diff = step >> 3
        if nibble & 4:
            diff += step
        if nibble & 2:
            diff += step >> 1
        if nibble & 1:
            diff += step >> 2
        if nibble & 8:
            self.predictor -= diff
        else:
            self.predictor += diff
        if self.predictor > 32767:
            self.predictor = 32767
        elif self.predictor < -32768:
            self.predictor = -32768
        self.index += IMA_INDEX_TABLE[nibble]
        if self.index < 0:
            self.index = 0
        elif self.index > 88:
            self.index = 88
        return self.predictor


def parse(data):
    if len(data) < HEADER_SIZE:
        raise AudError("not a Westwood AUD file")
    rate, compressed_size, uncompressed_size, flags, codec = struct.unpack_from(
        "<HIIBB", data
    )
    if rate == 0:
        raise AudError("not a Westwood AUD file")
    channels = 2 if flags & 2 else 1
    bits = 16 if flags & 1 else 8

    # Only the bytes the header's compressed size covers belong to the audio.
    audio_end = HEADER_SIZE + compressed_size
    if audio_end > len(data):
        audio_end = len(data)
        truncated = True
    else:
        truncated = False

    chunks = bad = in_bytes = out_bytes = 0
    ima_states = [_ImaState() for _ in range(channels)]
    peak_mag = 0
    last_sample = None
    channel_cursor = 0

    p = HEADER_SIZE
    while p < audio_end:
        if p + CHUNK_HEADER_SIZE > audio_end:
            # A chunk header itself is cut short.
            truncated = True
            break
        csize, cout, cid, cnumber = struct.unpack_from("<HHHH", data, p)
        if cid != CHUNK_ID:
            bad += 1
        chunks += 1
        out_bytes += cout
        payload_start = p + CHUNK_HEADER_SIZE
        available = audio_end - payload_start
        if csize > available:
            # The last chunk is incomplete: use only what the file covers.
            payload = data[payload_start:payload_start + available]
            in_bytes += available
            truncated = True
            p = audio_end
        else:
            payload = data[payload_start:payload_start + csize]
            in_bytes += csize
            p = payload_start + csize

        if codec == 99:
            # Nibbles are stored low-then-high; stereo alternates channels,
            # and the predictor/step state continues across chunk boundaries.
            for byte in payload:
                for nibble in (byte & 0x0F, byte >> 4):
                    sample = ima_states[channel_cursor].decode_nibble(nibble)
                    channel_cursor = (channel_cursor + 1) % channels
                    magnitude = sample if sample >= 0 else -sample
                    if magnitude > peak_mag:
                        peak_mag = magnitude
                    last_sample = sample

    frames = uncompressed_size // (channels * bits // 8)
    if codec == 99 and last_sample is not None:
        peak = float("%.6f" % (peak_mag / 32768.0))
    else:
        peak = None
    return {
        "sample_rate": rate,
        "channels": channels,
        "bits": bits,
        "codec": CODECS.get(codec, "type_%d" % codec),
        "chunks": chunks,
        "bad_chunk_ids": bad,
        "data_bytes": in_bytes,
        "header_size_ok": in_bytes + CHUNK_HEADER_SIZE * chunks == compressed_size,
        "output_bytes": uncompressed_size,
        "chunk_output_bytes": out_bytes,
        "frames": frames,
        "duration": str(Fraction(frames, rate)),
        "truncated": truncated,
        "peak": peak,
        "last_sample": last_sample,
    }
