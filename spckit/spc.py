"""SNES SPC700 sound files: header, ID666 tag (text or binary layout) and the extended xid6 tag."""
import re
import struct
from fractions import Fraction

MAGIC = b"SNES-SPC700 Sound File Data v0.30"
EMULATORS = {0: None, 1: "ZSNES", 2: "Snes9x"}
# Extended ID666 length fields (intro/loop/end) count samples at the SPC700's 32 kHz rate.
SPC_RATE = 32000


class SpcError(ValueError):
    pass


def _text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def _is_ascii_number(raw):
    """A NUL-padded field holding an ASCII number, as written by the text ID666 layout."""
    field = raw.rstrip(b"\0")
    return bool(field) and all(0x30 <= b <= 0x39 for b in field)


def _text_date(raw):
    """Parse ID666 text dates (MM/DD/YYYY or YYYY-MM-DD) into YYYY-MM-DD."""
    s = raw.split(b"\0")[0].decode("latin-1").strip()
    m = re.fullmatch(r"(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})", s)
    if not m:
        return None
    a, b, c = (int(g) for g in m.groups())
    if a > 31:  # YYYY-MM-DD
        year, month, day = a, b, c
    else:       # MM/DD/YYYY (the format the ID666 spec gives as example)
        month, day, year = a, b, c
    if year < 100:
        year += 2000 if year < 80 else 1900
    if not (1 <= month <= 12 and 1 <= day <= 31 and year > 0):
        return None
    return "%04d-%02d-%02d" % (year, month, day)


def _binary_date(data):
    day, month = data[0x9E], data[0x9F]
    year, = struct.unpack_from("<H", data, 0xA0)
    if not (1 <= month <= 12 and 1 <= day <= 31 and year > 0):
        return None
    return "%04d-%02d-%02d" % (year, month, day)


def _is_text_layout(data):
    """Tell the two ID666 layouts apart.

    The spec marks the text layout by its ASCII emulator byte at 0xD2 ('0'..'2');
    binary dumps keep a raw 0..2 there. Numeric time fields are ASCII in the text
    layout and little-endian integers in the binary one, which settles dumps whose
    emulator byte was left blank.
    """
    if data[0xD2] in (0x30, 0x31, 0x32):
        return True
    if _is_ascii_number(data[0xA9:0xAC]) or _is_ascii_number(data[0xAC:0xB1]):
        return True
    if data[0xD1] in (1, 2):  # raw emulator id, binary layout
        return False
    return True


def _id666(data):
    out = {"title": _text(data[0x2E:0x4E]), "game": _text(data[0x4E:0x6E]),
           "dumper": _text(data[0x6E:0x7E]), "comments": _text(data[0x7E:0x9E])}
    if _is_text_layout(data):
        secs = data[0xA9:0xAC].rstrip(b"\0")
        fade = data[0xAC:0xB1].rstrip(b"\0")
        out.update(id666_format="text", date=_text_date(data[0x9E:0xA9]),
                   play_seconds=int(secs) if secs.isdigit() else None,
                   fade_ms=int(fade) if fade.isdigit() else None,
                   artist=_text(data[0xB1:0xD1]),
                   emulator=EMULATORS.get(data[0xD2] - 0x30))
    else:
        secs = int.from_bytes(data[0xA9:0xAC], "little")
        fade, = struct.unpack_from("<I", data, 0xAC)
        out.update(id666_format="binary", date=_binary_date(data),
                   play_seconds=secs or None, fade_ms=fade or None,
                   artist=_text(data[0xB0:0xD0]), emulator=EMULATORS.get(data[0xD1]))
    return out


# Extended ID666 (xid6) sub-chunk ids.
XID6_SONG = 0x01
XID6_GAME = 0x02
XID6_ARTIST = 0x03
XID6_DUMPER = 0x04
XID6_DATE = 0x06
XID6_EMULATOR = 0x07
XID6_OST_TITLE = 0x10
XID6_OST_TRACK = 0x11
XID6_COMMENTS = 0x05
XID6_INTRO = 0x30
XID6_LOOP = 0x31
XID6_END = 0x32
XID6_FADE = 0x33
XID6_LOOP_COUNT = 0x35


def _xid6(data):
    """Parse the extended ID666 tag trailing the 64 KiB SPC image."""
    out = {}
    if data[0x10200:0x10204] != b"xid6":
        return out, False
    size, = struct.unpack_from("<I", data, 0x10204)
    end = min(0x10208 + size, len(data))
    off = 0x10208
    while off + 4 <= end:
        cid, ctype = data[off], data[off + 1]
        csize, = struct.unpack_from("<H", data, off + 2)
        if ctype == 0:  # value stored in the size field itself
            value = csize
            off += 4
        else:
            if off + 4 + csize > end:
                break
            payload = data[off + 4:off + 4 + csize]
            off += 4 + csize
            if ctype in (1, 2):  # NUL-terminated / fixed-length string
                value = payload.split(b"\0")[0].decode("latin-1").strip() or None
            elif ctype == 3:    # uint16 little endian
                value, = struct.unpack("<H", payload[:2])
            elif ctype == 4:    # uint32 little endian
                value, = struct.unpack("<I", payload[:4])
            else:               # unknown chunk type: skip its payload
                value = None
        if value is None:
            continue
        if cid == XID6_SONG:
            out["title"] = value
        elif cid == XID6_GAME:
            out["game"] = value
        elif cid == XID6_ARTIST:
            out["artist"] = value
        elif cid == XID6_DUMPER:
            out["dumper"] = value
        elif cid == XID6_COMMENTS:
            out["comments"] = value
        elif cid == XID6_OST_TITLE:
            out["ost_title"] = value
        elif cid == XID6_OST_TRACK:
            out["ost_track"] = str(value)
        elif cid == XID6_EMULATOR:
            out["emulator"] = EMULATORS.get(value)
        elif cid == XID6_DATE:
            s = str(value)
            if len(s) == 8:
                out["date"] = "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])
        elif cid == XID6_FADE:
            out["fade_ms"] = value
        elif cid in (XID6_INTRO, XID6_LOOP, XID6_END, XID6_LOOP_COUNT):
            out[cid] = value
    return out, True


def parse(data):
    if data[:33] != MAGIC or len(data) < 0x10200:
        raise SpcError("not an SPC file")
    pc, = struct.unpack_from("<H", data, 0x25)
    out = {"has_id666": data[0x23] == 26, "pc": "%04X" % pc, "id666_format": None, "title": None, "game": None,
           "artist": None, "dumper": None, "comments": None, "date": None, "play_seconds": None, "fade_ms": None,
           "emulator": None, "ost_title": None, "ost_track": None, "length_seconds": None, "xid6": False}
    if out["has_id666"]:
        out.update(_id666(data))
        if out["play_seconds"]:
            out["length_seconds"] = str(out["play_seconds"])
    xid6, present = _xid6(data)
    out["xid6"] = present
    for key, value in xid6.items():
        if key not in (XID6_INTRO, XID6_LOOP, XID6_END, XID6_LOOP_COUNT):
            out[key] = value  # extended tag values override ID666 fields
    if present and any(k in xid6 for k in (XID6_INTRO, XID6_LOOP, XID6_END)):
        samples = (xid6.get(XID6_INTRO, 0)
                   + xid6.get(XID6_LOOP, 0) * xid6.get(XID6_LOOP_COUNT, 1)
                   + xid6.get(XID6_END, 0))
        out["length_seconds"] = str(Fraction(samples, SPC_RATE))
    return out
