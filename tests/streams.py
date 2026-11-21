"""FLAC stream builder (headers and checksums only; subframe payload is filler)."""
import struct


def _table(poly, width):
    top, mask, out = 1 << (width - 1), (1 << width) - 1, []
    for i in range(256):
        c = i << (width - 8)
        for _ in range(8):
            c = ((c << 1) ^ poly) if c & top else c << 1
        out.append(c & mask)
    return out


_T8, _T16 = _table(0x07, 8), _table(0x8005, 16)


def crc8(d):
    c = 0
    for b in d:
        c = _T8[c ^ b]
    return c


def crc16(d):
    c = 0
    for b in d:
        c = ((c << 8) & 0xFFFF) ^ _T16[(c >> 8) ^ b]
    return c


def utf8(v):
    if v < 0x80:
        return bytes([v])
    for n in range(2, 8):
        if v < (1 << (5 * n + 1)) or n == 7:
            break
    out = []
    for _ in range(n - 1):
        out.append(0x80 | (v & 0x3F))
        v >>= 6
    lead = (0xFF << (8 - n)) & 0xFF
    out.append(lead | v)
    return bytes(reversed(out))


def streaminfo(rate=44100, channels=2, bits=16, total=0, min_block=4096, max_block=4096, min_frame=0, max_frame=0):
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total
    return struct.pack(">HH", min_block, max_block) + min_frame.to_bytes(3, "big") + max_frame.to_bytes(3, "big") + packed.to_bytes(8, "big") + bytes(16)


def block(kind, body, last=False):
    return bytes([(0x80 if last else 0) | kind]) + len(body).to_bytes(3, "big") + body


def seektable(points, placeholders=0):
    out = b""
    for s, off, n in points:
        out += struct.pack(">QQH", s, off, n)
    out += (b"\xff" * 8 + bytes(10)) * placeholders
    return out


def vorbis(vendor="reference libFLAC 1.4.3", tags=()):
    out = struct.pack("<I", len(vendor)) + vendor.encode() + struct.pack("<I", len(tags))
    for t in tags:
        b = t.encode()
        out += struct.pack("<I", len(b)) + b
    return out


BS = {192: 1, 576: 2, 1152: 3, 2304: 4, 4608: 5, 256: 8, 512: 9, 1024: 10, 2048: 11, 4096: 12, 8192: 13, 16384: 14, 32768: 15}
SR = {88200: 1, 176400: 2, 192000: 3, 8000: 4, 16000: 5, 22050: 6, 24000: 7, 32000: 8, 44100: 9, 48000: 10, 96000: 11}
BPS = {8: 1, 12: 2, 16: 4, 20: 5, 24: 6, 32: 7}


def header(number, blocksize=4096, rate=44100, ch_code=1, bits=16, variable=False, sr_code=None, bs_code=None, bps_code=None):
    tail = b""
    if bs_code is None:
        bs_code = BS.get(blocksize)
        if bs_code is None:
            bs_code = 6 if blocksize <= 256 else 7
    if bs_code == 6:
        tail += bytes([blocksize - 1])
    elif bs_code == 7:
        tail += (blocksize - 1).to_bytes(2, "big")
    if sr_code is None:
        sr_code = SR.get(rate)
        if sr_code is None:
            sr_code = 12 if rate % 1000 == 0 and rate // 1000 < 256 else 14 if rate % 10 == 0 and rate // 10 < 65536 and rate > 65535 else 13
    if sr_code == 12:
        tail2 = bytes([rate // 1000])
    elif sr_code == 13:
        tail2 = rate.to_bytes(2, "big")
    elif sr_code == 14:
        tail2 = (rate // 10).to_bytes(2, "big")
    else:
        tail2 = b""
    if bps_code is None:
        bps_code = BPS[bits]
    h = bytes([0xFF, 0xF8 | (1 if variable else 0), (bs_code << 4) | sr_code, (ch_code << 4) | (bps_code << 1)]) + utf8(number) + tail + tail2
    return h + bytes([crc8(h)])


def frame(number, size=300, seed=0, fake=None, **kw):
    body = bytes(((i * 13 + seed * 7) % 0xF0) for i in range(size))
    if fake is not None:
        body = body[:size // 2] + fake + body[size // 2:]
    f = header(number, **kw) + body
    return f + crc16(f).to_bytes(2, "big")


def flac(frames, info=None, extra_blocks=(), id3=b""):
    info = info or streaminfo()
    blocks = [block(0, info)] + list(extra_blocks)
    meta = b"".join(blocks[:-1]) + bytes([blocks[-1][0] | 0x80]) + blocks[-1][1:]
    return id3 + b"fLaC" + meta + b"".join(frames)


def id3tag(size=200, footer=False):
    ss = bytes((size >> (7 * (3 - i))) & 0x7F for i in range(4))
    return b"ID3\x04\x00" + bytes([0x10 if footer else 0]) + ss + bytes(size) + (b"3DI\x04\x00\x10" + ss if footer else b"")
