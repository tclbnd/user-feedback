"""CRI HCA audio headers: chunk list, frame layout, loop points, cipher, header checksum."""
import struct
from fractions import Fraction

SAMPLES_PER_FRAME = 1024
# cipher type 0 is plain, type 1 uses the built-in (keyless) cipher; every
# other type is a keyed cipher (e.g. 0x38 for a 56-bit user key).
CIPHERS = {0: "none", 1: "keyless"}
FIXED = {"fmt": 12, "comp": 12, "dec": 8, "vbr": 4, "ath": 2, "loop": 12, "ciph": 2, "rva": 4}
HCA_MAGIC = b"HCA\0"


class HcaError(ValueError):
    pass


def _crc16(data):
    """CRC-16/ARC style checksum CRI stores in the last two header bytes."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x8005) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _unmask(raw):
    """Reverse CRI's chunk-name obfuscation: every non-zero byte XOR 0x80."""
    return bytes(b ^ 0x80 if b else b for b in raw)


def _name(raw, masked):
    if masked:
        raw = _unmask(raw)
    return raw.rstrip(b"\0").decode("latin-1")


def parse(data):
    if len(data) < 8:
        raise HcaError("not an HCA file")
    masked = data[:4] != HCA_MAGIC
    if data[:4] != HCA_MAGIC and _unmask(data[:4]) != HCA_MAGIC:
        raise HcaError("not an HCA file")
    version, hsize = struct.unpack_from(">HH", data, 4)
    if hsize > len(data) or hsize < 10:
        raise HcaError("header runs past the end of the file")
    # The checksum is calculated over the header bytes as stored, so the
    # obfuscation is still in place for masked files.
    stored_crc = struct.unpack_from(">H", data, hsize - 2)[0]
    header_crc_ok = _crc16(data[:hsize - 2]) == stored_crc
    out = {"version": "%d.%d" % (version >> 8, version & 0xFF), "masked": masked,
           "header_crc_ok": header_crc_ok, "comment": None, "volume": 1.0, "cipher": "none",
           "loop": None, "vbr": False}
    pos, chunks = 8, {}
    while pos + 4 <= hsize - 2:
        name = _name(data[pos:pos + 4], masked)
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
        chunks[name] = body
        pos = body + FIXED[name]
    if "fmt" not in chunks:
        raise HcaError("no fmt chunk")
    f = chunks["fmt"]
    channels = data[f]
    rate = int.from_bytes(data[f + 1:f + 4], "big")
    blocks, delay, padding = struct.unpack_from(">IHH", data, f + 4)
    if "comp" not in chunks and "dec" not in chunks:
        raise HcaError("no comp chunk")
    c = chunks["comp"] if "comp" in chunks else chunks["dec"]
    bsize, = struct.unpack_from(">H", data, c)
    if bsize == 0 or "vbr" in chunks:
        out["vbr"] = True
    ath = struct.unpack_from(">H", data, chunks["ath"])[0] if "ath" in chunks else 0
    if "loop" in chunks:
        loop_block, end_block, loop_offset, end_offset = struct.unpack_from(">IIHH", data, chunks["loop"])
        # Loop points are stored as (frame index, sample inside frame) on the
        # full decoded timeline, which still includes the encoder delay.
        start = loop_block * SAMPLES_PER_FRAME + loop_offset - delay
        end = end_block * SAMPLES_PER_FRAME + end_offset - delay
        out["loop"] = {"start": start, "end": end}
    if "ciph" in chunks:
        cipher_type, = struct.unpack_from(">H", data, chunks["ciph"])
        out["cipher"] = CIPHERS.get(cipher_type, "keyed")
    if "rva" in chunks:
        out["volume"] = struct.unpack_from(">f", data, chunks["rva"])[0]
    # Frames cover the whole decoded timeline; the playable portion drops the
    # encoder delay at the front and the padding at the end.
    total_samples = blocks * SAMPLES_PER_FRAME
    samples = total_samples - delay - padding
    frame_bytes = blocks * bsize
    if out["vbr"]:
        truncated = None
        block_size = None
    else:
        truncated = frame_bytes > len(data) - hsize
        block_size = bsize
    out.update(channels=channels, sample_rate=rate, blocks=blocks, block_size=block_size,
               encoder_delay=delay, padding=padding, samples=samples,
               duration=str(Fraction(samples, rate)) if rate else None, ath_type=ath,
               truncated=truncated)
    return out
