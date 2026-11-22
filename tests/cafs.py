"""Builders for small synthetic CAF files used by the tests."""
import struct


def chunk(ctype, body, size=None):
    return ctype + struct.pack(">q", len(body) if size is None else size) + body


def desc(rate=44100.0, fmt=b"lpcm", flags=0, bpp=4, fpp=1, ch=2, bits=16):
    return chunk(b"desc", struct.pack(">d4sIIIII", rate, fmt, flags, bpp, fpp, ch, bits))


def varint(v):
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.insert(0, 0x80 | (v & 0x7F))
        v >>= 7
    return bytes(out)


def pakt(sizes=(), frames=(), valid=0, priming=0, remainder=0):
    n = len(sizes)
    body = struct.pack(">qqii", n, valid, priming, remainder)
    for i in range(n):
        body += varint(sizes[i])
        if frames:
            body += varint(frames[i])
    return chunk(b"pakt", body)


def data(audio, edit=0):
    return chunk(b"data", struct.pack(">I", edit) + audio)


def data_with_size(audio, size, edit=0):
    """data chunk with an explicit (possibly wrong, e.g. -1) size field."""
    return b"data" + struct.pack(">q", size) + struct.pack(">I", edit) + audio


def info(pairs):
    body = struct.pack(">I", len(pairs)) + b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in pairs)
    return chunk(b"info", body)


def chan(tag=(101 << 16) | 2, bitmap=0):
    return chunk(b"chan", struct.pack(">III", tag, bitmap, 0))


def kuki(asc):
    """AAC magic cookie: ES_Descriptor (0x03) > DecoderConfigDescriptor (0x04)
    > DecoderSpecificInfo (0x05) containing the AudioSpecificConfig."""
    dsi = b"\x05" + bytes([len(asc)]) + asc
    dcd = b"\x04" + bytes([13 + len(dsi)]) + b"\x00" * 13 + dsi
    esd = b"\x03" + bytes([3 + len(dcd)]) + b"\x00" * 3 + dcd
    return chunk(b"kuki", esd)


class _BitWriter:
    def __init__(self):
        self.bits = []

    def put(self, value, width):
        for i in range(width - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def bytes(self):
        out = bytearray()
        for i in range(0, len(self.bits), 8):
            group = self.bits[i:i + 8]
            byte = 0
            for j, bit in enumerate(group):  # left-aligned, zero padded
                byte |= bit << (7 - j)
            out.append(byte)
        return bytes(out)


def asc(aot=2, rate_index=4, channel=2, he=None):
    """Build an AudioSpecificConfig bitstream.

    he=None gives a plain LC config; he=1/2 produces an explicit HE-AAC
    v1/v2 config with a 24000 Hz core upsampled to 48000 Hz.
    """
    if he is None:
        bw = _BitWriter()
        bw.put(aot, 5)
        bw.put(rate_index, 4)
        bw.put(channel, 4)
        return bw.bytes()
    bw = _BitWriter()
    bw.put(5 if he == 1 else 29, 5)   # extension AOT: SBR / PS
    bw.put(aot, 5)                    # core AOT: AAC LC
    bw.put(3, 4)                      # extension sampling 48000 Hz
    bw.put(6, 4)                      # core sampling 24000 Hz
    bw.put(channel, 4)
    return bw.bytes()


def audio(n, seed=0):
    return bytes(((i * 13 + seed) & 0xFF) for i in range(n))


def caf(*chunks_):
    return b"caff" + struct.pack(">HH", 1, 0) + b"".join(chunks_)
