"""Builders for small synthetic VGM logs used by the tests."""
import struct

OFF = {"SN76489": 0x0C, "YM2413": 0x10, "YM2612": 0x2C, "YM2151": 0x30, "SegaPCM": 0x38, "YM2203": 0x44,
       "YM2608": 0x48, "YM2610": 0x4C, "YM3812": 0x50, "AY8910": 0x74, "GameBoy": 0x80, "NES": 0x84}


def gd3(title="", game="", system="", author="", date="", ripper="", notes="", jp=("", "", "", "")):
    vals = [title, jp[0], game, jp[1], system, jp[2], author, jp[3], date, ripper, notes]
    body = "".join(v + "\0" for v in vals).encode("utf-16-le")
    return b"Gd3 " + struct.pack("<II", 0x100, len(body)) + body


def commands(frames=60, wait="62", writes=2):
    extra = b""
    out = b""
    for i in range(frames):
        for w in range(writes):
            out += bytes([0x52, 0x28 + w, i & 0xFF])
        out += {"62": b"\x62", "63": b"\x63", "61": b"\x61" + struct.pack("<H", 700)}[wait]
    return out + extra


def vgm(version=0x171, chips=None, body=None, total=None, tag=None):
    loop_cmd, header_size, loop_samples, data_offset = None, 0x100, 0, True
    chips = chips if chips is not None else {"SN76489": 3579545, "YM2612": 7670453}
    body = commands() if body is None else body
    head = bytearray(header_size)
    head[0:4] = b"Vgm "
    struct.pack_into("<I", head, 8, version)
    for name, clock in chips.items():
        struct.pack_into("<I", head, OFF[name], clock)
    if version >= 0x150 and data_offset:
        struct.pack_into("<I", head, 0x34, header_size - 0x34)
    if total is None:
        total = sum_waits(body)
    struct.pack_into("<I", head, 0x18, total)
    data = bytes(head) + body + b"\x66"
    if loop_cmd is not None:
        struct.pack_into("<II", head, 0x1C, header_size + loop_cmd - 0x1C, loop_samples)
        data = bytes(head) + body + b"\x66"
    if tag is not None:
        struct.pack_into("<I", head, 0x14, len(data) - 0x14)
        data = bytes(head) + body + b"\x66" + tag
    out = bytearray(data)
    struct.pack_into("<I", out, 4, len(out) - 4)
    return bytes(out)


def sum_waits(body):
    import sys
    sys.path.insert(0, ".")
    total, p = 0, 0
    lens = {0x52: 2, 0x61: 2, 0x62: 0, 0x63: 0, 0x67: None, 0x50: 1, 0x4F: 1, 0x93: 10, 0x90: 4, 0x91: 4,
            0x92: 5, 0x94: 1, 0x95: 4, 0xE0: 4, 0x30: 1, 0xA0: 2, 0xB4: 2, 0xC0: 3, 0xD2: 3, 0xE1: 4}
    while p < len(body):
        op = body[p]
        if op == 0x61:
            total += struct.unpack_from("<H", body, p + 1)[0]
        elif op == 0x62:
            total += 735
        elif op == 0x63:
            total += 882
        elif 0x70 <= op <= 0x7F:
            total += (op & 15) + 1
        elif 0x80 <= op <= 0x8F:
            total += op & 15
        if op == 0x67:
            p += 7 + struct.unpack_from("<I", body, p + 3)[0]
            continue
        n = lens.get(op, 0 if 0x70 <= op <= 0x8F else 2)
        p += 1 + n
    return total


