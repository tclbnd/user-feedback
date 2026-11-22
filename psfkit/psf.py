"""PSF family files (PSF, PSF2, SSF, DSF, USF, GSF, SNSF, QSF): header, program, CRC, [TAG] section."""
import re
import struct
import zlib
from fractions import Fraction

# Version byte -> console, per the PSF format specification.
SYSTEMS = {
    0x01: "PlayStation",
    0x02: "PlayStation 2",
    0x11: "Saturn",
    0x12: "Dreamcast",
    0x21: "Nintendo 64",
    0x22: "Game Boy Advance",
    0x23: "Super NES",
    0x41: "Capcom QSound",
}
MAIN = ("title", "artist", "game", "year")
_LIB_RE = re.compile(r"^_lib(\d*)$")


class PsfError(ValueError):
    pass


def seconds(text):
    """Parse a [[h:]m:]s[.d] time tag (`,` is also a decimal mark).

    Returns the number of seconds as a fraction string, or None when
    absent/unreadable.
    """
    parts = text.strip().replace(",", ".").split(":")
    if len(parts) > 3 or any(not part.strip() for part in parts):
        return None
    total, scale = Fraction(0), 1
    for index, part in enumerate(reversed(parts)):
        part = part.strip()
        # Only the last component (seconds) may carry a decimal fraction.
        if not re.fullmatch(r"\d+(?:\.\d+)?" if index == 0 else r"\d+", part):
            return None
        try:
            total += Fraction(part) * scale
        except ValueError:
            return None
        scale *= 60
    return str(total)


def _tags(raw):
    utf8 = False
    values, order = {}, []
    lines = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    for line in lines:
        if b"=" not in line:
            continue
        name, value = line.split(b"=", 1)
        name = name.strip().lower()
        value = value.strip()
        if not name:
            continue
        if name == b"utf8":
            utf8 = True
        key = name.decode("latin-1")
        if key not in values:
            order.append(key)
            values[key] = []
        values[key].append(value)
    # The utf8 tag selects the text encoding for every tag in the section.
    enc = "utf-8" if utf8 else "windows-1252"
    tags = {key: "\n".join(v.decode(enc, "replace") for v in values[key]) for key in order}
    return tags, utf8


def parse(data):
    if data[:3] != b"PSF" or len(data) < 16:
        raise PsfError("not a PSF file")
    version = data[3]
    reserved, csize, crc = struct.unpack_from("<III", data, 4)
    # The reserved area sits between the 16-byte header and the program
    # (PSF2 aligns its program this way too).
    start = 16 + reserved
    end = start + csize
    if end > len(data):
        raise PsfError("program runs past the end of the file")
    program = data[start:end]
    # A rip without a program (mini/rip file) has nothing to check or inflate.
    if csize == 0:
        crc_ok, program_bytes = None, 0
    else:
        crc_ok = (zlib.crc32(program) & 0xFFFFFFFF) == crc
        program_bytes = len(zlib.decompress(program))
    out = {"system": SYSTEMS.get(version, "0x%02X" % version), "reserved_bytes": reserved,
           "compressed_bytes": csize, "crc_ok": crc_ok, "program_bytes": program_bytes}
    tags, utf8 = {}, False
    if data[end:end + 5] == b"[TAG]":
        tags, utf8 = _tags(data[end + 5:])
    for key in MAIN:
        out[key] = tags.pop(key, None)
    out["length"] = seconds(tags.pop("length")) if "length" in tags else None
    out["fade"] = seconds(tags.pop("fade")) if "fade" in tags else None
    # _lib, _lib2, _lib3, ... in loading order, stopping at the first gap.
    lib_keys = {}
    for key in tags:
        match = _LIB_RE.match(key)
        if match:
            lib_keys[int(match.group(1)) if match.group(1) else 1] = key
    libs = []
    number = 1
    while number in lib_keys:
        libs.append(tags.pop(lib_keys[number]))
        number += 1
    out["libs"] = libs
    tags.pop("utf8", None)
    out["utf8"] = utf8
    out["tags"] = tags
    return out
