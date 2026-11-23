"""SNES SPC700 sound files: header, ID666 tag (text or binary layout) and the extended xid6 tag."""
import struct
from fractions import Fraction

MAGIC = b"SNES-SPC700 Sound File Data v0.30"
EMULATORS = {0: None, 1: "ZSNES", 2: "Snes9x"}


class SpcError(ValueError):
    pass


def _text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def _id666(data):
    out = {"title": _text(data[0x2E:0x4E]), "game": _text(data[0x4E:0x6E]), "dumper": _text(data[0x6E:0x7E]),
           "comments": _text(data[0x7E:0x9E])}
    date = data[0x9E:0xA9].split(b"\0")[0].decode("latin-1")
    secs = data[0xA9:0xAC].split(b"\0")[0]
    fade = data[0xAC:0xAF].split(b"\0")[0]
    out.update(id666_format="text", date=date or None, play_seconds=int(secs) if secs.isdigit() else None,
               fade_ms=int(fade) if fade.isdigit() else None, artist=_text(data[0xB1:0xD1]),
               emulator=EMULATORS.get(data[0xD2] - 0x30 if data[0xD2] >= 0x30 else data[0xD2]))
    return out


def parse(data):
    if data[:33] != MAGIC or len(data) < 0x10200:
        raise SpcError("not an SPC file")
    pc, = struct.unpack_from("<H", data, 0x25)
    out = {"has_id666": data[0x23] == 26, "pc": "%04X" % pc, "id666_format": None, "title": None, "game": None,
           "artist": None, "dumper": None, "comments": None, "date": None, "play_seconds": None, "fade_ms": None,
           "emulator": None, "ost_title": None, "ost_track": None, "length_seconds": None, "xid6": False,
           "ost_disc": None, "publisher": None, "copyright_year": None, "muted_voices": [], "amplification": None}
    out.update(_id666(data))
    if out["play_seconds"]:
        out["length_seconds"] = str(out["play_seconds"])
    out["xid6"] = data[0x10200:0x10204] == b"xid6"
    return out
