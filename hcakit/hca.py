"""CRI HCA audio headers: chunk list, frame layout, loop points, cipher, header checksum."""
import struct
from fractions import Fraction

SAMPLES_PER_FRAME = 1024
# ciph chunk: 0 means the stream is not encrypted, 1 uses the fixed "keyless"
# key; every other type (in practice 56 = 0x38) needs a user supplied key.
CIPHERS = {0: "none", 1: "keyless"}
FIXED = {"fmt": 12, "comp": 12, "dec": 8, "vbr": 4, "ath": 2, "loop": 12, "ciph": 2, "rva": 4}


class HcaError(ValueError):
    pass


def _crc16(data):
    """CRC-16 (polynomial 0x8005, initial value 0) used by the header."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _name(raw):
    """Decode a signature or chunk name, undoing the high-bit obfuscation."""
    masked = any(byte & 0x80 for byte in raw)
    return bytes(byte & 0x7F for byte in raw).rstrip(b"\0").decode("latin-1"), masked


def parse(data):
    if len(data) < 8:
        raise HcaError("not an HCA file")
    name, masked = _name(data[:4])
    if name != "HCA":
        raise HcaError("not an HCA file")
    version, hsize = struct.unpack_from(">HH", data, 4)
    if hsize > len(data) or hsize < 10:
        raise HcaError("header runs past the end of the file")
    stored_crc, = struct.unpack_from(">H", data, hsize - 2)
    out = {"version": "%d.%d" % (version >> 8, version & 0xFF), "masked": masked,
           "header_crc_ok": _crc16(data[:hsize - 2]) == stored_crc, "comment": None,
           "volume": 1.0, "cipher": "none", "loop": None, "vbr": False}
    pos, chunks = 8, {}
    while pos + 4 <= hsize - 2:
        name, m = _name(data[pos:pos + 4])
        out["masked"] = out["masked"] or m
        body = pos + 4
        if name == "pad":
            break
        if name == "comm":
            n = data[body]
            out["comment"] = data[body + 1:body + 1 + n].split(b"\0")[0].decode("utf-8", "replace")
            pos = body + 1 + n
            continue
        if name not in FIXED:
            raise HcaError("unknown chunk %r at %d" % (name, pos))
        if body + FIXED[name] > hsize - 2:
            raise HcaError("%s chunk runs past the end of the header" % name)
        chunks[name] = body
        pos = body + FIXED[name]
    if "fmt" not in chunks:
        raise HcaError("no fmt chunk")
    f = chunks["fmt"]
    channels = data[f]
    # the sample rate is a 24-bit big-endian field right after the channel count
    rate = int.from_bytes(data[f + 1:f + 4], "big")
    blocks, delay, padding = struct.unpack_from(">IHH", data, f + 4)
    if "comp" not in chunks and "dec" not in chunks:
        raise HcaError("no comp chunk")
    # newer encoders emit a "comp" chunk, older ones a "dec" chunk; both start
    # with the frame size.
    bsize, = struct.unpack_from(">H", data, chunks.get("comp", chunks.get("dec")))
    # a variable-rate stream either has no fixed frame size or a "vbr" chunk
    vbr = bsize == 0 or "vbr" in chunks
    out["vbr"] = vbr
    ath = struct.unpack_from(">H", data, chunks["ath"])[0] if "ath" in chunks else 0
    if "loop" in chunks:
        start_frame, end_frame, start_delay, end_padding = struct.unpack_from(">IIHH", data, chunks["loop"])
        # the loop chunk stores frames plus its own start delay / end padding;
        # the encoder delay muted at the start shifts both ends.
        start = start_frame * SAMPLES_PER_FRAME + start_delay - delay
        end = end_frame * SAMPLES_PER_FRAME + end_padding - delay
        out["loop"] = {"start": start, "end": end}
    if "ciph" in chunks:
        c, = struct.unpack_from(">H", data, chunks["ciph"])
        out["cipher"] = CIPHERS.get(c, "keyed")
    if "rva" in chunks:
        out["volume"] = struct.unpack_from(">f", data, chunks["rva"])[0]
    # the delay muted at the start and the padding at the end are not playable
    samples = blocks * SAMPLES_PER_FRAME - delay - padding
    out.update(channels=channels, sample_rate=rate, blocks=blocks,
               block_size=None if vbr else bsize,
               encoder_delay=delay, padding=padding, samples=samples,
               duration=str(Fraction(samples, rate)) if rate else None, ath_type=ath,
               truncated=None if vbr else len(data) - hsize < blocks * bsize)
    return out
