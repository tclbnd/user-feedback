"""Builders for small synthetic ADPCM WAVE files used by the tests."""
import struct


def wav(tag, channels, rate, align, data, extra=b"", bits=4, fact=None):
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * align // 256 or 1, align, bits) + extra
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + (b"\x00" if len(fmt) & 1 else b"")
    if fact is not None:
        body += b"fact" + struct.pack("<II", 4, fact)
    body += b"data" + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def ima_extra(spb):
    return struct.pack("<HH", 2, spb)


def ms_extra(spb, coefs=((256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232))):
    return struct.pack("<HHH", 4 + 4 * len(coefs), spb, len(coefs)) + b"".join(struct.pack("<hh", *c) for c in coefs)


def ima_block(headers, payload):
    """headers: [(predictor, index), ...] per channel."""
    return b"".join(struct.pack("<hBB", p, i, 0) for p, i in headers) + payload


def ms_block(preds, deltas, s1, s2, payload):
    n = len(preds)
    return bytes(preds) + struct.pack("<" + "h" * n, *deltas) + struct.pack("<" + "h" * n, *s1) + struct.pack("<" + "h" * n, *s2) + payload


def pattern(n, seed=7):
    out, x = bytearray(), seed
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((x >> 16) & 0xFF)
    return bytes(out)


def gentle(n, seed=7, ms=False):
    """Bytes whose nibbles keep the decoded signal well inside 16 bits."""
    pool = (0, 1, 2, 3, 4, 15, 14, 13, 12) if ms else (0, 1, 2, 3, 4, 8, 9, 10, 11, 12)
    out, x = bytearray(), seed
    for _ in range(n):
        nib = []
        for _ in range(2):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF
            nib.append(pool[(x >> 16) % len(pool)])
        out.append((nib[0] << 4) | nib[1])
    return bytes(out)
