"""PSF family files (PSF, PSF2, SSF, DSF, USF, GSF, SNSF, QSF): header, program, CRC, [TAG] section."""
import re
import struct
import zlib
from fractions import Fraction

# Version byte to platform name, as defined by the PSF format family.
SYSTEMS = {
    0x01: "PlayStation",
    0x02: "PlayStation 2",
    0x11: "Saturn",
    0x12: "Dreamcast",
    0x13: "Mega Drive",
    0x21: "Nintendo 64",
    0x22: "Game Boy Advance",
    0x23: "Super NES",
    0x24: "Capcom QSound",
}
MAIN = ("title", "artist", "game", "year")

# Bytes treated as padding around tag names and values (ASCII controls, spaces and DEL).
_PADDING = bytes(range(0x21)) + b"\x7f"

_TIME_PART = re.compile(r"[0-9]+")
_TIME_LAST = re.compile(r"[0-9]+(?:[.,][0-9]+)?")


class PsfError(ValueError):
    pass


def seconds(text):
    """Convert a ``[[h:]m:]s[.d]`` tag value to a fraction-of-a-second string."""
    if text is None:
        return None
    parts = [part.strip() for part in text.strip().split(":")]
    if not 1 <= len(parts) <= 3 or not all(parts):
        return None
    if not all(_TIME_PART.fullmatch(part) for part in parts[:-1]):
        return None
    if not _TIME_LAST.fullmatch(parts[-1]):
        return None
    try:
        values = [Fraction(int(part)) for part in parts[:-1]]
        values.append(Fraction(parts[-1].replace(",", ".")))
    except (ValueError, ZeroDivisionError):
        return None
    total = Fraction(0)
    for value in values:
        total = total * 60 + value
    return str(total)


def _strip(raw):
    return raw.strip(_PADDING)


def _tags(raw):
    """Parse a ``[TAG]`` body, returning lower-case tags and the UTF-8 flag."""
    lines = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    utf8 = False
    for line in lines:
        if b"=" not in line:
            continue
        if _strip(line.split(b"=", 1)[0]).lower() == b"utf8":
            utf8 = True
            break

    encoding = "utf-8" if utf8 else "cp1252"
    tags, order = {}, []
    for line in lines:
        if b"=" not in line:
            continue
        raw_name, raw_value = line.split(b"=", 1)
        raw_name = _strip(raw_name)
        if not raw_name:
            continue
        name = raw_name.decode(encoding, "replace").lower()
        if name == "utf8":
            continue
        value = _strip(raw_value).decode(encoding, "replace")
        if name not in tags:
            order.append(name)
            tags[name] = []
        tags[name].append(value)
    return {name: "\n".join(tags[name]) for name in order}, utf8


def parse(data):
    if data[:3] != b"PSF" or len(data) < 4:
        raise PsfError("not a PSF file")
    version = data[3]
    if version == 0x02:
        # PSF2: 0x14-byte header holding the compressed program size (0x04),
        # the decompressed program size (0x08), the CRC-32 (0x0C) and the
        # reserved area size (0x10).  As in the PSF1 family, the reserved area
        # follows the header and the compressed program follows the reserved
        # area.
        if len(data) < 0x14:
            raise PsfError("truncated PSF2 header")
        csize, _dsize, crc, reserved = struct.unpack_from("<IIII", data, 4)
        start = 0x14 + reserved
    else:
        # PSF1 family: 16-byte header (reserved size, compressed size, CRC),
        # then the reserved area, then the program.
        if len(data) < 0x10:
            raise PsfError("not a PSF file")
        reserved, csize, crc = struct.unpack_from("<III", data, 4)
        start = 0x10 + reserved
    end = start + csize
    if end > len(data):
        raise PsfError("program runs past the end of the file")

    program = data[start:end]
    if csize:
        program_bytes = len(zlib.decompress(program))
        crc_ok = (zlib.crc32(program) & 0xFFFFFFFF) == crc
    else:
        program_bytes = 0
        crc_ok = None

    out = {"system": SYSTEMS.get(version, "0x%02X" % version), "reserved_bytes": reserved, "compressed_bytes": csize,
           "crc_ok": crc_ok, "program_bytes": program_bytes}

    tags, utf8 = {}, False
    if data[end:end + 5] == b"[TAG]":
        tags, utf8 = _tags(data[end + 5:])

    for key in MAIN:
        out[key] = tags.pop(key, None)
    out["length"] = seconds(tags.pop("length", None))
    out["fade"] = seconds(tags.pop("fade", None))

    libs = []
    if "_lib" in tags:
        libs.append(tags.pop("_lib"))
        number = 2
        while "_lib%d" % number in tags:
            libs.append(tags.pop("_lib%d" % number))
            number += 1
    out["libs"] = libs

    out["utf8"] = utf8
    out["tags"] = tags
    return out
