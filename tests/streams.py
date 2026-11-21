"""Builders for small synthetic program streams used by the tests."""
def _ts(prefix, v):
    """5-byte PTS/DTS field: prefix(4) v[32..30] 1 v[29..15] 1 v[14..0] 1."""
    return bytes([(prefix << 4) | (((v >> 30) & 7) << 1) | 1,
                  (v >> 22) & 0xFF, (((v >> 15) & 0x7F) << 1) | 1,
                  (v >> 7) & 0xFF, ((v & 0x7F) << 1) | 1])


def pack2(scr_base, scr_ext=0, mux=10080, stuffing=0):
    b = [0x44 | (((scr_base >> 30) & 7) << 3) | ((scr_base >> 28) & 3),
         (scr_base >> 20) & 0xFF,
         (((scr_base >> 15) & 0x1F) << 3) | 4 | ((scr_base >> 13) & 3),
         (scr_base >> 5) & 0xFF,
         ((scr_base & 0x1F) << 3) | 4 | ((scr_ext >> 7) & 3),
         ((scr_ext & 0x7F) << 1) | 1,
         (mux >> 14) & 0xFF, (mux >> 6) & 0xFF, ((mux & 0x3F) << 2) | 3,
         0xF8 | stuffing]
    return b"\x00\x00\x01\xba" + bytes(b) + b"\xff" * stuffing


def pack1(scr, mux=3528):
    return b"\x00\x00\x01\xba" + bytes([0x21 | (((scr >> 30) & 7) << 1)]) + _ts(0, scr)[1:] + \
        bytes([0x80 | ((mux >> 15) & 0x7F), (mux >> 7) & 0xFF, ((mux & 0x7F) << 1) | 1])


def system_header():
    body = bytes([0x80, 0x1B, 0x83, 0x04, 0xE1, 0xFF, 0xE0, 0xE0, 0xE8, 0xC0, 0xC0, 0x20])
    return b"\x00\x00\x01\xbb" + len(body).to_bytes(2, "big") + body


def pes2(sid, payload, pts=None, dts=None):
    flags = (2 if pts is not None else 0) | (1 if dts is not None else 0)
    hdr = b""
    if pts is not None:
        hdr += _ts(3 if dts is not None else 2, pts)
    if dts is not None:
        hdr += _ts(1, dts)
    body = bytes([0x81, flags << 6, len(hdr)]) + hdr + payload
    return b"\x00\x00\x01" + bytes([sid]) + len(body).to_bytes(2, "big") + body


def pes1(sid, payload, pts=None, dts=None, stuffing=0, std=False):
    body = b"\xff" * stuffing + (b"\x60\x2e" if std else b"")
    if pts is None:
        body += b"\x0f"
    elif dts is None:
        body += _ts(2, pts)
    else:
        body += _ts(3, pts) + _ts(1, dts)
    body += payload
    return b"\x00\x00\x01" + bytes([sid]) + len(body).to_bytes(2, "big") + body


def raw_pes(sid, body):
    return b"\x00\x00\x01" + bytes([sid]) + len(body).to_bytes(2, "big") + body


def ac3_sub(track, data):
    return bytes([0x80 + track, 1, 0, 1]) + data


def lpcm_sub(track, data, bits=16, rate=48000, channels=2):
    q = {16: 0, 20: 1, 24: 2}[bits]
    f = {48000: 0, 96000: 1}[rate]
    return bytes([0xA0 + track, 1, 0, 1, 0x00, (q << 6) | (f << 4) | (channels - 1), 0x80]) + data


def sub_sub(track, data):
    return bytes([0x20 + track]) + data


END = b"\x00\x00\x01\xb9"
