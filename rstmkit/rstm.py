"""Nintendo BRSTM streams: file header, HEAD (stream, track and channel info), ADPC, DATA."""
import struct
from fractions import Fraction

# Stream codec ids.  BRSTM numbers PCM8 first, unlike the later BCSTM family.
CODECS = {0: "pcm8", 1: "pcm16", 2: "dsp-adpcm"}
DSP_ADPCM = 2

# A section reference is a u8 type, three padding bytes, then a u32 offset.
REF_SIZE = 8
# A DSP-ADPCM channel's info: 16 coefficients, gain, then initial predictor/scale.
ADPCM_INFO_SIZE = 0x30
ADPCM_INITIAL_PS = 0x22
# Audio begins this far into the DATA chunk (its 0x20-byte header).
DATA_HEADER_SIZE = 0x20

MAGIC = b"RSTM"


class RstmError(ValueError):
    pass


class Reader:
    def __init__(self, data, endian=">"):
        self.d, self.e = data, endian

    def u(self, fmt, pos):
        try:
            return struct.unpack_from(self.e + fmt, self.d, pos)
        except struct.error:
            raise RstmError("header runs past the end of the file at %d" % pos)


def _pad32(size):
    return (size + 31) // 32 * 32


def _adpcm_bytes(samples):
    """Bytes one channel needs to hold `samples` DSP-ADPCM samples."""
    full, rest = divmod(samples, 14)
    return full * 8 + ((rest + 1) // 2 + 1 if rest else 0)


def _codec_bytes(codec, samples):
    if codec == DSP_ADPCM:
        return _adpcm_bytes(samples)
    return samples * (2 if codec == 1 else 1)


def _track_channels(r, pos, header, channels, limit):
    """Channel list stored at `pos + header`, or None if it does not fit."""
    # A track always names at least one channel, and no track can name more
    # channels than the stream has.  Rejecting impossible counts keeps a wrong
    # header offset from turning the following header bytes into a long list of
    # random channel numbers.
    count_at = pos + header
    if count_at + 1 > limit:
        return None
    count = r.d[count_at]
    if not 0 < count <= channels or count_at + 1 + count > limit:
        return None
    chans = list(r.d[count_at + 1:count_at + 1 + count])
    return chans if all(c < channels for c in chans) else None


def _track_entry(r, pos, track_type, channels, limit):
    """Read one track entry: optional volume/pan, then the channel list.

    A type 1 entry starts with a 1-byte volume, a 1-byte pan and six padding
    bytes.  A type 0 entry has no volume/pan and starts with the channel count.
    """
    if track_type != 1:
        return {"channels": _track_channels(r, pos, 0, channels, limit) or [],
                "volume": None, "pan": None}
    if pos + 2 > limit:
        return {"channels": [], "volume": None, "pan": None}
    volume, pan = r.u("BB", pos)
    # Real files pad the pair to eight bytes; a four-byte header is accepted for
    # exporters that leave the padding out.
    for header in (8, 4):
        chans = _track_channels(r, pos, header, channels, limit)
        if chans is not None:
            return {"channels": chans, "volume": volume, "pan": pan}
    return {"channels": [], "volume": volume, "pan": pan}


def _initial_predictor_scale(r, head, entry, head_end):
    """Initial predictor/scale for one channel entry.

    The entry references the channel's ADPCM info; exporters that leave that
    reference blank put the info directly after the entry instead.
    """
    offset, = r.u("I", entry + 4)
    info = head + offset
    if not (offset and entry + REF_SIZE <= info and info + ADPCM_INFO_SIZE <= head_end):
        info = entry + REF_SIZE
    return r.u("H", info + ADPCM_INITIAL_PS)[0]


def _last_block_bytes(codec, block_size, last_used, last_padded, last_samples):
    """Bytes the interleave layout keeps for one channel of the last block."""
    for size in (last_used, last_padded):
        if 0 < size <= block_size:
            return _pad32(size)
    # Some exporters leave the byte counts blank; the sample count still
    # describes the block, so derive the size from it.
    size = _codec_bytes(codec, last_samples) if 0 < last_samples else 0
    if 0 < size <= block_size:
        return _pad32(size)
    return block_size


def parse(data):
    if len(data) < 16:
        raise RstmError("not a BRSTM file")
    # A little-endian file stores the magic reversed (and/or writes a 0xFF 0xFE
    # byte-order mark behind the big-endian one).
    if data[:4] == MAGIC:
        endian = "<" if data[4:6] == b"\xff\xfe" else ">"
    elif data[:4] == MAGIC[::-1]:
        endian = "<"
    else:
        raise RstmError("not a BRSTM file")
    r = Reader(data, endian)
    version, _size, _hsize, nchunks = r.u("HIHH", 6)

    # Chunk table entries only carry offset/size, so read the fourcc at the offset
    # each entry points at.  Files without an ADPC chunk have two entries only,
    # and the chunks are not guaranteed to be HEAD, ADPC, DATA in that order.
    chunks = {}
    for i in range(nchunks):
        off, size = r.u("II", 16 + i * 8)
        if off + 4 <= len(data):
            chunks.setdefault(data[off:off + 4], (off, size))
    if b"HEAD" not in chunks:
        raise RstmError("no HEAD chunk")

    head_off, head_size = chunks[b"HEAD"]
    head = head_off + 8
    head_end = len(data) if head_size <= 0 else min(len(data), head_off + head_size)
    head_end = max(head_end, head)

    def ref(pos):
        # Reference offsets are relative to the start of the HEAD body.
        off, = r.u("I", pos + 4)
        return head + off

    stream, tracks_at, chans_at = ref(head), ref(head + 8), ref(head + 16)

    # sample rate is a u16 followed by a padding u16, not part of a u32.
    codec, loop_flag, channels, _pad, rate, _pad2 = r.u("BBBBHH", stream)
    (loop_start, total, data_off, blocks, bsize, bsamples,
     last_used, last_samples, last_padded) = r.u("9I", stream + 8)

    ntracks, ttype = r.u("BB", tracks_at)
    tracks = [_track_entry(r, ref(tracks_at + 4 + i * 8), ttype, channels, head_end)
              for i in range(ntracks)]

    initial_ps = None
    if codec == DSP_ADPCM:
        nch, = r.u("B", chans_at)
        initial_ps = []
        for i in range(nch):
            entry = ref(chans_at + 4 + i * 8)
            initial_ps.append(_initial_predictor_scale(r, head, entry, head_end))

    # Interleave layout: every block but the last is a full block_size chunk per
    # channel; the last block keeps only the bytes its samples need, rounded up
    # to 32 bytes per channel.  Exporters that leave `blocks` or the last-block
    # sample count at zero are still handled from the total sample count.
    block_count = blocks if blocks > 0 else (-(-total // bsamples) if bsamples > 0 else 0)
    if block_count > 0:
        block_bytes = bsize if bsize > 0 else _pad32(_codec_bytes(codec, bsamples))
        last = last_samples if 0 < last_samples <= bsamples else total - (block_count - 1) * bsamples
        need = ((block_count - 1) * block_bytes
                + _last_block_bytes(codec, block_bytes, last_used, last_padded, last)) * channels
    else:
        need = 0

    have = 0
    if b"DATA" in chunks:
        chunk_off, chunk_size = chunks[b"DATA"]
        chunk_end = len(data) if chunk_size <= 0 else min(len(data), chunk_off + chunk_size)
        # `data offset` is normally an absolute file offset, but a few exporters
        # write it relative to the DATA chunk.  Audio always follows the 0x20-byte
        # DATA header, which is the fallback when the field is missing entirely.
        if chunk_off + DATA_HEADER_SIZE <= data_off <= chunk_end:
            start = data_off
        elif 0 < data_off <= chunk_end - chunk_off:
            start = chunk_off + data_off
        else:
            start = chunk_off + DATA_HEADER_SIZE
        have = max(0, chunk_end - start)

    return {"version": "%d.%d" % (version >> 8, version & 0xFF), "codec": CODECS.get(codec, str(codec)),
            "channels": channels, "sample_rate": rate, "total_samples": total,
            "duration": str(Fraction(total, rate)) if rate else None,
            "loop": {"start": loop_start,
                     "start_seconds": str(Fraction(loop_start, rate)) if rate else None}
                    if loop_flag else None,
            "blocks": blocks, "block_size": bsize, "samples_per_block": bsamples,
            "last_block_samples": last_samples, "tracks": tracks,
            "initial_predictor_scale": ["0x%02x" % p for p in initial_ps]
                                      if initial_ps is not None else None,
            "has_adpc": b"ADPC" in chunks, "data_bytes_expected": need,
            "truncated": have < need}
