"""Builders for small synthetic SPC files used by the tests."""
import struct

MAGIC = b"SNES-SPC700 Sound File Data v0.30"


def pad(s, n):
    b = s.encode("latin-1")
    return b[:n] + bytes(max(0, n - len(b)))


def spc(title="Theme", game="Game", dumper="dumper", comments="", artist="Composer", date=(2001, 6, 15), secs=180,
        fade=10000, emulator=2, pc=0x0430):
    layout, tag, xid6 = "text", True, b""
    d = bytearray(0x10200)
    d[0:33] = MAGIC
    d[33:35] = b"\x1a\x1a"
    d[0x23] = 26 if tag else 27
    d[0x24] = 30
    struct.pack_into("<H", d, 0x25, pc)
    if tag:
        d[0x2E:0x4E] = pad(title, 32)
        d[0x4E:0x6E] = pad(game, 32)
        d[0x6E:0x7E] = pad(dumper, 16)
        d[0x7E:0x9E] = pad(comments, 32)
        if layout == "text":
            y, m, dd = date
            d[0x9E:0xA9] = pad("%02d/%02d/%04d" % (m, dd, y) if y else "", 11)
            d[0xA9:0xAC] = pad(str(secs) if secs else "", 3)
            d[0xAC:0xB1] = pad(str(fade) if fade else "", 5)
            d[0xB1:0xD1] = pad(artist, 32)
            d[0xD2] = 0x30 + emulator
        else:
            y, m, dd = date
            d[0x9E], d[0x9F] = dd, m
            struct.pack_into("<H", d, 0xA0, y)
            d[0xA9:0xAC] = secs.to_bytes(3, "little")
            struct.pack_into("<I", d, 0xAC, fade)
            d[0xB0:0xD0] = pad(artist, 32)
            d[0xD1] = emulator
    else:
        d[0x2E:0x4E] = pad("leftover RAM bytes", 32)
    if xid6:
        d += b"xid6" + struct.pack("<I", len(xid6)) + xid6
    return bytes(d)


