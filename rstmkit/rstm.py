"""Nintendo BRSTM streams: file header, HEAD (stream, track and channel info), ADPC, DATA."""
import struct
from fractions import Fraction

CODECS = {0: "pcm8", 1: "pcm16", 2: "dsp-adpcm"}

# AudioStreamInfo offsets, relative to the stream-info reference target.
STREAM_CODEC = 0
STREAM_LOOP = 1
STREAM_CHANNELS = 2
STREAM_RATE = 4
STREAM_LOOP_START = 8
STREAM_TOTAL = 12
STREAM_DATA_OFF = 16
STREAM_BLOCKS = 20
STREAM_BLOCK_SIZE = 24
STREAM_SAMPLES_PER_BLOCK = 28
STREAM_LAST_SIZE = 32
STREAM_LAST_SAMPLES = 36
STREAM_LAST_PADDED = 40

# Per-channel ADPCM info, relative to the channel's inner reference target.
ADPCM_INITIAL_PS = 0x22
ADPCM_LOOP_PS = 0x28


class RstmError(ValueError):
    pass


class Reader:
    def __init__(self, data, endian):
        self.d, self.e = data, endian

    def u(self, fmt, pos):
        try:
            return struct.unpack_from(self.e + fmt, self.d, pos)
        except struct.error:
            raise RstmError("header runs past the end of the file at %d" % pos)


def _target(r, head, pos):
    """Follow a BRSTM reference (type/pad/pad/offset) and return its target."""
    try:
        _, _, _, off = r.u("BBHI", pos)
    except RstmError:
        return None
    return head + off


def _loop(r, chunks, info, loop_flag, start, rate, bsamples, blocks, channels, codec):
    if not loop_flag:
        return None
    block = start // bsamples if bsamples else None
    seconds = str(Fraction(start, rate)) if rate else None
    out = {"start": start, "start_seconds": seconds, "block": block}
    if codec == 2:
        out["predictor_scale"] = ["0x%02x" % p if p is not None else None
                                  for p in info["loop_ps"]]
        if b"ADPC" in chunks and block is not None and 0 <= block < blocks:
            base = chunks[b"ADPC"][0] + 8
            history = []
            for c in range(channels):
                # ADPC history is block-major: block * channels + channel.
                try:
                    history.append(list(r.u("hh", base + (block * channels + c) * 4)))
                except RstmError:
                    history.append(None)
            out["history"] = history
        else:
            out["history"] = None
    return out


def _tracks(r, head, tracks_at, channels):
    if tracks_at is None:
        return []
    try:
        ntracks, track_type = r.u("BB", tracks_at)
    except RstmError:
        return []
    tracks = []
    for i in range(ntracks):
        entry = _target(r, head, tracks_at + 4 + i * 8)
        if entry is None:
            continue
        if track_type == 1:
            # volume, pan, six pad bytes, then channel count and channel list
            try:
                volume, pan = r.u("BB", entry)
            except RstmError:
                volume = pan = None
            count_pos = entry + 8
        else:
            volume = pan = None
            count_pos = entry
        try:
            (n,) = r.u("B", count_pos)
            chans = list(r.u("%dB" % n, count_pos + 1)) if n else []
        except RstmError:
            chans = []
        chans = [c for c in chans if c < channels]
        tracks.append({"channels": chans, "volume": volume, "pan": pan})
    return tracks


def _channel_scales(r, head, chans_at, channels):
    """Read each channel's initial and loop predictor/scale values."""
    initial, loop = [], []
    for c in range(channels):
        record = _target(r, head, chans_at + 4 + c * 8) if chans_at is not None else None
        if record is None:
            initial.append(None)
            loop.append(None)
            continue
        # A channel record starts with a reference to its ADPCM info, which
        # holds the coefficients and both predictor/scale values.
        info = _target(r, head, record)
        if info is None:
            info = record + 8
        try:
            initial.append(r.u("H", info + ADPCM_INITIAL_PS)[0])
            loop.append(r.u("H", info + ADPCM_LOOP_PS)[0])
        except RstmError:
            initial.append(None)
            loop.append(None)
    return initial, loop


def parse(data):
    if len(data) < 16 or data[:4] != b"RSTM":
        raise RstmError("not a BRSTM file")
    endian = "<" if data[4:6] == b"\xff\xfe" else ">"
    r = Reader(data, endian)
    version, size, hsize, nchunks = r.u("HIHH", 6)
    chunks = {}
    for i in range(nchunks):
        try:
            off, length = r.u("II", 0x10 + i * 8)
        except RstmError:
            break
        if off + 8 > len(data):
            continue
        chunks[bytes(data[off:off + 4])] = (off, length)
    if b"HEAD" not in chunks:
        raise RstmError("no HEAD chunk")
    head = chunks[b"HEAD"][0] + 8

    stream = _target(r, head, head)
    tracks_at = _target(r, head, head + 8)
    chans_at = _target(r, head, head + 16)
    if stream is None:
        raise RstmError("no stream info")

    codec, loop_flag, channels = r.u("BBB", stream + STREAM_CODEC)
    (rate,) = r.u("H", stream + STREAM_RATE)
    (loop_start, total, data_off, blocks, bsize, bsamples,
     last_size, last_samples, last_padded) = r.u("9I", stream + STREAM_LOOP_START)

    initial_ps, loop_ps = _channel_scales(r, head, chans_at, channels)
    info = {"initial_ps": initial_ps, "loop_ps": loop_ps}

    # Audio bytes the interleave layout needs: whole blocks plus the last block,
    # which is padded to 32 bytes per channel.
    if last_size:
        last_bytes = -(-last_size // 32) * 32
    else:
        last_bytes = last_padded
    data_bytes_expected = (((blocks - 1) * bsize if blocks else 0) + last_bytes) * channels

    available = 0
    if b"DATA" in chunks:
        data_at, data_len = chunks[b"DATA"]
        # Audio starts after the 0x20-byte DATA chunk header, unless data_off
        # points elsewhere inside the chunk.
        start = data_off if data_at + 0x20 <= data_off <= data_at + data_len else data_at + 0x20
        end = min(data_at + data_len, len(data))
        available = max(end - start, 0)

    return {
        "version": "%d.%d" % (version >> 8, version & 0xFF),
        "codec": CODECS.get(codec, str(codec)),
        "channels": channels,
        "sample_rate": rate,
        "total_samples": total,
        "duration": str(Fraction(total, rate)) if rate else None,
        "loop": _loop(r, chunks, info, loop_flag, loop_start, rate, bsamples, blocks, channels, codec),
        "blocks": blocks,
        "block_size": bsize,
        "samples_per_block": bsamples,
        "last_block_samples": last_samples,
        "tracks": _tracks(r, head, tracks_at, channels),
        "initial_predictor_scale": (["0x%02x" % p if p is not None else None for p in initial_ps]
                                    if codec == 2 else None),
        "has_adpc": b"ADPC" in chunks,
        "data_bytes_expected": data_bytes_expected,
        "truncated": available < data_bytes_expected,
    }
