"""Microsoft ADPCM (format tag 2) WAV files.

Implements the RIFF WAVE container with the Microsoft ADPCM waveform-audio
type as described in the Microsoft documentation (RIFFCHUNK,
MSADPCMWAVEFORMAT and the block decoding pseudocode).
"""
import struct
import zlib

# Adaptation table indexed by the 4-bit error nibble.
ADAPT = [230, 230, 230, 230, 307, 409, 512, 614,
         768, 614, 512, 409, 307, 230, 230, 230]

# Coefficients used when a fmt chunk carries no coefficient table.
DEFAULT_COEF = [(256, 0), (512, -256), (0, 0), (192, 64),
                (240, 0), (460, -208), (392, -232)]


class AdpcmError(ValueError):
    pass


def _chunks(data):
    """Yield (chunk_id, payload) pairs, honouring the RIFF pad byte."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AdpcmError("not a RIFF WAVE file")
    pos = 12
    while pos + 8 <= len(data):
        size = struct.unpack_from("<I", data, pos + 4)[0]
        start = pos + 8
        end = start + size
        if end > len(data):
            # Truncated final chunk: take whatever payload is present.
            end = len(data)
        yield data[pos:pos + 4], data[start:end]
        # Chunks are word-aligned; an odd payload has one pad byte.
        pos = start + size + (size & 1)


def _clamp16(v):
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return v


def _parse_fmt(fmt):
    if len(fmt) < 22:
        raise AdpcmError("no fmt chunk")
    (tag, channels, rate, _bytes_per_sec, block_align, _bits,
     cb_size, samples_per_block, n_coef) = struct.unpack_from(
        "<HHIIHHHHH", fmt)
    if tag != 2:
        raise AdpcmError("not Microsoft ADPCM")
    if channels < 1 or block_align < 7 * channels:
        raise AdpcmError("bad block alignment")
    table = 22
    if len(fmt) < table + 4 * n_coef:
        raise AdpcmError("truncated coefficient table")
    if n_coef:
        coefs = [struct.unpack_from("<hh", fmt, table + 4 * i)
                 for i in range(n_coef)]
    else:
        coefs = list(DEFAULT_COEF)
    return channels, rate, block_align, samples_per_block, coefs


def _decode_block(block, channels, coefs, samples_per_block):
    """Decode one MS ADPCM block, returning one sample list per channel."""
    if len(block) < 7 * channels:
        return [[] for _ in range(channels)]

    # Header: one predictor byte per channel, then per-channel initial
    # delta, first sample and second sample (fields are interleaved).
    predictor = list(block[:channels])
    delta = [0] * channels
    samp1 = [0] * channels
    samp2 = [0] * channels
    off = channels
    for c in range(channels):
        delta[c] = struct.unpack_from("<H", block, off)[0]
        off += 2
    for c in range(channels):
        samp1[c] = struct.unpack_from("<h", block, off)[0]
        off += 2
    for c in range(channels):
        samp2[c] = struct.unpack_from("<h", block, off)[0]
        off += 2

    for c in range(channels):
        if predictor[c] >= len(coefs):
            raise AdpcmError("bad predictor index %d" % predictor[c])

    # samp1 is the most recent sample and samp2 the one before it, so the
    # playback order of the two header samples is samp2 then samp1.
    out = [[samp2[c], samp1[c]] for c in range(channels)]

    # Nibbles are packed high nibble first and distributed round-robin
    # across channels.
    nibbles = []
    for byte in block[off:]:
        nibbles.append(byte >> 4)
        nibbles.append(byte & 0x0F)

    wanted = (samples_per_block - 2) * channels
    if len(nibbles) > wanted:
        nibbles = nibbles[:wanted]
    # A short last block may leave a dangling nibble without a frame.
    nibbles = nibbles[:len(nibbles) - (len(nibbles) % channels)]

    for i, nibble in enumerate(nibbles):
        c = i % channels
        coef1, coef2 = coefs[predictor[c]]
        # Arithmetic shift rounds towards minus infinity, as specified.
        pred = (samp1[c] * coef1 + samp2[c] * coef2) >> 8
        signed = nibble - 16 if nibble & 8 else nibble
        sample = _clamp16(pred + signed * delta[c])
        out[c].append(sample)
        delta[c] = max(16, (ADAPT[nibble] * delta[c]) >> 8)
        samp2[c] = samp1[c]
        samp1[c] = sample
    return out


def parse(data):
    chunks = dict(_chunks(data))
    fmt = chunks.get(b"fmt ")
    if fmt is None:
        raise AdpcmError("no fmt chunk")
    channels, rate, block_align, samples_per_block, coefs = _parse_fmt(fmt)

    body = chunks.get(b"data", b"")
    chans = [[] for _ in range(channels)]
    blocks = 0
    for off in range(0, len(body), block_align):
        block = body[off:off + block_align]
        if not block:
            continue
        decoded = _decode_block(block, channels, coefs, samples_per_block)
        if not decoded[0]:
            # Fragment too short to hold a header: no samples at all.
            continue
        blocks += 1
        for c in range(channels):
            chans[c].extend(decoded[c])

    frames = len(chans[0])
    pcm = bytearray()
    pack = struct.Struct("<%dh" % channels).pack
    for i in range(frames):
        pcm += pack(*(chans[c][i] for c in range(channels)))
    return {
        "channels": channels,
        "sample_rate": rate,
        "block_align": block_align,
        "samples_per_block": samples_per_block,
        "blocks": blocks,
        "frames": frames,
        "duration": round(frames / rate, 4) if rate else None,
        "peak": max((abs(v) for ch in chans for v in ch), default=0),
        "head": [ch[:6] for ch in chans],
        "last": [ch[-1] if ch else None for ch in chans],
        "crc32": "%08x" % (zlib.crc32(bytes(pcm)) & 0xFFFFFFFF),
    }
