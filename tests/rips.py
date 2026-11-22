"""Builders for small synthetic RIFF CDXA files used by the tests."""
import struct

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def _edc_table():
    t = []
    for i in range(256):
        v = i
        for _ in range(8):
            v = (v >> 1) ^ 0xD8018001 if v & 1 else v >> 1
        t.append(v)
    return t


T = _edc_table()


def edc(data):
    v = 0
    for b in data:
        v = (v >> 8) ^ T[(v ^ b) & 0xFF]
    return v


def bcd(v):
    return (v // 10) << 4 | v % 10


def msf(lba):
    a = lba + 150
    return bytes([bcd(a // 4500), bcd(a // 75 % 60), bcd(a % 75)])


def sector(lba, submode=0x62, coding=0, fill=0x11, bad_sync=False, sub_mismatch=False,
           edc_mode="ok", mode=2):
    form2 = bool(submode & 0x20) if mode == 2 else None
    s = bytearray(2352)
    s[0:12] = SYNC if not bad_sync else b"\x00" * 12
    s[12:15] = msf(lba)
    s[15] = mode
    if mode == 1:
        # Mode 1: user data at 16..2064, EDC at 2064 over bytes 0..2064.
        s[16:2064] = bytes([fill]) * 2048
        val = edc(bytes(s[:2064])) ^ (0x10 if edc_mode == "bad" else 0)
        struct.pack_into("<I", s, 2064, val)
        return bytes(s)
    if mode != 2:
        s[16:2064] = bytes([fill]) * 2048
        return bytes(s)
    sub = bytes([1, 1, submode, coding])
    s[16:20] = sub
    s[20:24] = bytes([1, 1, submode ^ 0x01, coding]) if sub_mismatch else sub
    if form2:
        s[24:2348] = bytes([fill]) * 2324
        val = edc(bytes(s[16:2348]))
        if edc_mode == "zero":
            val = 0
        elif edc_mode == "bad":
            val ^= 0x10
        struct.pack_into("<I", s, 2348, val)
    else:
        s[24:2072] = bytes([fill]) * 2048
        val = edc(bytes(s[16:2072])) ^ (0x10 if edc_mode == "bad" else 0)
        struct.pack_into("<I", s, 2072, val)
    return bytes(s)


def cdxa(sectors, file_number=1, attrs=0x0D55, trailing=b"", extra=b""):
    fmt = struct.pack("<HH", 0, 0) + struct.pack(">H", attrs) + b"XA" + bytes([file_number]) + bytes(5)
    data = b"".join(sectors) + trailing
    body = (b"CDXA" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra +
            b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def vcd(start=0, n=20, audio_every=4):
    """MPEG-1 track as on a Video CD: video sectors with an audio sector every few, all form 2."""
    return [sector(start + i, 0x64 if i % audio_every == 0 else 0x62, 0) for i in range(n)]
