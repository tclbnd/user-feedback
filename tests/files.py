"""Builders for small synthetic HCA files used by the tests."""
import struct


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def sig(name, mask=False):
    raw = name.encode().ljust(4, b"\0")
    return bytes(b | 0x80 if mask and b else b for b in raw)


def hca(channels=2, rate=48000, blocks=500, delay=128, padding=300, bsize=0x200, loop=None, cipher=None, comment=None):
    version, ath, volume, mask, dec, vbr, cut, bad_crc, extra_pad, data = 0x0200, None, None, False, False, None, 0, False, 8, True
    body = sig("fmt", mask) + bytes([channels]) + rate.to_bytes(3, "big") + struct.pack(">IHH", blocks, delay, padding)
    if dec:
        body += sig("dec", mask) + struct.pack(">HBBBBBB", bsize, 1, 15, 0x1F, 0x1F, 0x20, 1)
    else:
        body += sig("comp", mask) + struct.pack(">HBBBBBBBBH", bsize, 1, 15, 1, 0, 0x80, 0x80, 0, 0, 0)
    if vbr:
        body += sig("vbr", mask) + struct.pack(">HH", vbr, 0)
    if ath is not None:
        body += sig("ath", mask) + struct.pack(">H", ath)
    if loop:
        body += sig("loop", mask) + struct.pack(">IIHH", *loop)
    if cipher is not None:
        body += sig("ciph", mask) + struct.pack(">H", cipher)
    if volume is not None:
        body += sig("rva", mask) + struct.pack(">f", volume)
    if comment is not None:
        c = comment.encode() + b"\0"
        body += sig("comm", mask) + bytes([len(c)]) + c
    if extra_pad:
        body += sig("pad", mask) + bytes(extra_pad)
    hsize = 8 + len(body) + 2
    head = sig("HCA", mask) + struct.pack(">HH", version, hsize) + body
    crc = crc16(head) ^ (1 if bad_crc else 0)
    head += struct.pack(">H", crc)
    out = head + (bytes(blocks * bsize) if data and bsize else bytes(1000))
    return out[:len(out) - cut] if cut else out
