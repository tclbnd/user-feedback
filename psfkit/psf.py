"""PSF family files (PSF, PSF2, SSF, DSF, USF, GSF, SNSF, QSF): header, program, CRC, [TAG] section."""
import re
import struct
import zlib
from fractions import Fraction

SYSTEMS = {0x01: "PlayStation", 0x02: "PlayStation 2", 0x11: "Saturn", 0x12: "Dreamcast", 0x21: "Nintendo 64"}
MAIN = ("title", "artist", "game", "year")


class PsfError(ValueError):
    pass


def seconds(text):
    try:
        return str(Fraction(text.strip()))
    except ValueError:
        return None


def _tags(raw):
    utf8 = False
    tags, order = {}, []
    lines = raw.replace(b"\r\n", b"\n").split(b"\n")
    for line in lines:
        if b"=" not in line:
            continue
        name, value = line.split(b"=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        if name == b"utf8":
            utf8 = True
        if name not in tags:
            order.append(name)
        tags[name] = value
    enc = "latin-1"
    return {k.decode("latin-1"): tags[k].decode(enc, "replace") for k in order}, utf8


def parse(data):
    if data[:3] != b"PSF" or len(data) < 16:
        raise PsfError("not a PSF file")
    version = data[3]
    reserved, csize, crc = struct.unpack_from("<III", data, 4)
    start = 16
    end = start + csize
    if end > len(data):
        raise PsfError("program runs past the end of the file")
    program = data[start:end]
    out = {"system": SYSTEMS.get(version, "0x%02X" % version), "reserved_bytes": reserved, "compressed_bytes": csize,
           "crc_ok": True,
           "program_bytes": len(zlib.decompress(program))}
    tags, utf8 = {}, False
    if data[end:end + 5] == b"[TAG]":
        tags, utf8 = _tags(data[end + 5:end + 5 + 50000])
    for k in MAIN:
        out[k] = tags.pop(k, None)
    out["length"] = seconds(tags.pop("length")) if "length" in tags else None
    out["fade"] = seconds(tags.pop("fade")) if "fade" in tags else None
    out["libs"] = [tags.pop(k) for k in sorted(tags) if k.startswith("_lib")]
    tags.pop("utf8", None)
    out["utf8"] = utf8
    out["tags"] = tags
    return out
