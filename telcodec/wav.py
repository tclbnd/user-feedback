"""WAV files holding G.711 or 16-bit PCM call audio."""
import struct

from . import g711

TAGS = {1: "pcm16", 6: "alaw", 7: "ulaw"}

TAG_EXTENSIBLE = 0xFFFE
# Remaining 14 bytes of the subformat GUID (KSDATAFORMAT_SUBTYPE_*):
# {xxxxxxxx-0000-0010-8000-00aa00389b71}
GUID_TAIL = bytes.fromhex("00000000" "0000" "1000" "8000" "00aa00389b71")[2:]


class WavError(ValueError):
    pass


def chunks(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a WAVE file")
    pos, out = 12, {}
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        out.setdefault(cid, (pos + 8, size))
        pos += 8 + size + (size & 1)  # chunks are word-aligned with a pad byte
    return out


def read(data):
    ch = chunks(data)
    if b"fmt " not in ch or b"data" not in ch:
        raise WavError("missing fmt or data chunk")
    fo, fsize = ch[b"fmt "]
    if fsize < 16:
        raise WavError("fmt chunk too small")
    tag, channels, rate = struct.unpack("<HHI", data[fo:fo + 8])
    if tag == TAG_EXTENSIBLE:
        if fsize < 40:
            raise WavError("extensible fmt chunk too small")
        sub = data[fo + 24:fo + 40]
        if sub[2:] != GUID_TAIL:
            raise WavError("unsupported extensible subformat GUID")
        tag = int.from_bytes(sub[:2], "little")
    if tag not in TAGS:
        raise WavError(f"unsupported format tag {tag:#x}")
    if not channels or not rate:
        raise WavError("bad channel count or sample rate")
    do, dsize = ch[b"data"]
    return {"format": TAGS[tag], "channels": channels, "sample_rate": rate}, data[do:do + dsize]


def info(data):
    meta, payload = read(data)
    width = 2 if meta["format"] == "pcm16" else 1
    frames = len(payload) // (width * meta["channels"])
    return {**meta, "frames": frames, "duration": round(frames / meta["sample_rate"], 3)}


def write(fmt, channels, rate, payload):
    tag = {v: k for k, v in TAGS.items()}[fmt]
    width = 2 if fmt == "pcm16" else 1
    block = channels * width
    fmt_chunk = struct.pack("<HHIIHH", tag, channels, rate, rate * block, block, width * 8)
    body = b"WAVE"
    if fmt == "pcm16":
        body += b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
    else:
        # Non-PCM formats need a cbSize field and a fact chunk (sample count).
        fmt_chunk += struct.pack("<H", 0)
        body += b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
        body += b"fact" + struct.pack("<II", 4, len(payload) // block)
    body += b"data" + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        body += b"\0"
    return b"RIFF" + struct.pack("<I", len(body)) + body


def decode(data):
    meta, payload = read(data)
    if meta["format"] == "pcm16":
        return data
    fn = g711.alaw_decode if meta["format"] == "alaw" else g711.ulaw_decode
    pcm = b"".join(struct.pack("<h", fn(b)) for b in payload)
    return write("pcm16", meta["channels"], meta["sample_rate"], pcm)


def encode(data, law):
    meta, payload = read(data)
    if meta["format"] != "pcm16":
        raise WavError("input must be 16-bit PCM")
    fn = g711.alaw_encode if law == "alaw" else g711.ulaw_encode
    codes = bytes(fn(s) for (s,) in struct.iter_unpack("<h", payload[:len(payload) // 2 * 2]))
    return write(law, meta["channels"], meta["sample_rate"], codes)
