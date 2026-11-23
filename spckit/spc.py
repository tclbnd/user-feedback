"""SNES SPC700 sound files: header, ID666 tag (text or binary layout) and the extended xid6 tag."""
import struct
from datetime import date as _date
from fractions import Fraction

MAGIC = b"SNES-SPC700 Sound File Data v0.30"
EMULATORS = {0: None, 1: "ZSNES", 2: "Snes9x"}

# ID666 tag offsets (the tag starts at 0x2E in the file).
ID666_TITLE, ID666_GAME, ID666_DUMPER, ID666_COMMENTS = (0x2E, 32), (0x4E, 32), (0x6E, 16), (0x7E, 32)
ID666_DATE, ID666_SECONDS, ID666_FADE = 0x9E, 0xA9, 0xAC
# text layout: artist 0xB1..0xD0, emulator byte at 0xD2
# binary layout: artist 0xB0..0xCF, emulator byte at 0xD1
TEXT_ARTIST, BINARY_ARTIST, TEXT_EMU, BINARY_EMU = (0xB1, 32), (0xB0, 32), 0xD2, 0xD1

# Extended ID666 (xid6) sub-ids, per the extended ID666 tag specification.
XID6_TEXT = {
    0x01: "title", 0x02: "game", 0x03: "artist", 0x04: "dumper", 0x05: "date",
    0x06: "comments", 0x07: "ost_title", 0x08: "ost_disc", 0x09: "ost_track",
    0x0A: "publisher",
}
XID6_INT = {
    0x0B: "copyright_year", 0x0D: "fade_ms", 0x0E: "muted_voices",
    0x12: "emulator",
    0x30: "intro_ms", 0x31: "loop_ms", 0x32: "end_ms", 0x33: "amplification",
    0x35: "loop_count",
}
# xid6 sub-ids that only feed the length/gain computation, never the output.
XID6_INTERNAL = {"intro_ms", "loop_ms", "end_ms", "loop_count"}


class SpcError(ValueError):
    pass


def _text(raw):
    """Null-terminated ID666/xid6 text; None when the field is empty."""
    value = raw.split(b"\0")[0].decode("latin-1", "replace").strip()
    return value or None


def _uint(raw):
    """Little-endian unsigned integer, None when the field is blank."""
    raw = raw.rstrip(b"\0")
    if not raw:
        return None
    return int.from_bytes(raw, "little")


def _date_from_text(text):
    """Normalise an MM/DD/YYYY (or YYYY/MM/DD, MM-DD-YY, YYYYMMDD) date to YYYY-MM-DD."""
    text = text.strip()
    if not text:
        return None
    digits = text.replace("-", "/").replace(".", "/").split("/")
    if len(digits) == 1 and len(text) == 8 and text.isdigit():
        digits = [text[:4], text[4:6], text[6:8]]  # YYYYMMDD
    if len(digits) != 3 or not all(p.strip().isdigit() for p in digits):
        return None
    first, second, third = (int(p) for p in digits)
    if len(digits[0].strip()) == 4:  # YYYY/MM/DD
        year, month, day = first, second, third
    elif len(digits[2].strip()) == 4:  # MM/DD/YYYY
        month, day, year = first, second, third
    else:  # MM/DD/YY
        month, day, year = first, second, 2000 + third
    return _date(year, month, day).isoformat()


def _parse_date(raw, binary):
    """Normalise an ID666 date to YYYY-MM-DD; None when unset or unparseable."""
    try:
        if binary:
            day, month = raw[0], raw[1]
            year = int.from_bytes(raw[2:4], "little")
            if not (year or month or day):
                return None
            return _date(year, month, day).isoformat()
        return _date_from_text(raw.split(b"\0")[0].decode("latin-1"))
    except (ValueError, IndexError):
        return None


def _empty_result():
    return {"has_id666": False, "pc": None, "id666_format": None, "title": None, "game": None,
            "artist": None, "dumper": None, "comments": None, "date": None, "play_seconds": None,
            "fade_ms": None, "emulator": None, "ost_title": None, "ost_track": None,
            "length_seconds": None, "xid6": False, "ost_disc": None, "publisher": None,
            "copyright_year": None, "muted_voices": [], "amplification": None}


def _parse_id666(data):
    """Parse a text- or binary-layout ID666 tag."""
    # The emulator byte is an ASCII digit (0x30..0x32) in the text layout and a
    # raw 0..2 value in the binary layout, which distinguishes the two layouts.
    emu_byte = data[TEXT_EMU]
    if 0x30 <= emu_byte <= 0x32:
        binary = False
    elif emu_byte in (0, 1, 2):
        binary = True
    else:  # odd dumper output: decide from whether the text fields look valid
        text_secs = data[ID666_SECONDS:ID666_SECONDS + 3].split(b"\0")[0]
        text_date = data[ID666_DATE:ID666_DATE + 11].split(b"\0")[0]
        binary = not (text_secs.isdigit() or b"/" in text_date)

    out = {"id666_format": "binary" if binary else "text"}
    out["title"] = _text(data[ID666_TITLE[0]:ID666_TITLE[0] + ID666_TITLE[1]])
    out["game"] = _text(data[ID666_GAME[0]:ID666_GAME[0] + ID666_GAME[1]])
    out["dumper"] = _text(data[ID666_DUMPER[0]:ID666_DUMPER[0] + ID666_DUMPER[1]])
    out["comments"] = _text(data[ID666_COMMENTS[0]:ID666_COMMENTS[0] + ID666_COMMENTS[1]])
    artist = BINARY_ARTIST if binary else TEXT_ARTIST
    out["artist"] = _text(data[artist[0]:artist[0] + artist[1]])
    out["date"] = _parse_date(data[ID666_DATE:ID666_DATE + 11], binary)

    if binary:
        out["play_seconds"] = _uint(data[ID666_SECONDS:ID666_SECONDS + 3])
        out["fade_ms"] = _uint(data[ID666_FADE:ID666_FADE + 4])
        out["emulator"] = EMULATORS.get(data[BINARY_EMU])
    else:
        secs = data[ID666_SECONDS:ID666_SECONDS + 3].split(b"\0")[0]
        # The text fade field is five digits (0xAC..0xB0).
        fade = data[ID666_FADE:ID666_FADE + 5].split(b"\0")[0]
        out["play_seconds"] = int(secs) if secs.isdigit() else None
        out["fade_ms"] = int(fade) if fade.isdigit() else None
        out["emulator"] = EMULATORS.get(emu_byte - 0x30)
    return out


def _parse_xid6(data):
    """Parse the extended ID666 (xid6) tag following the 64 KiB RAM image.

    Each sub-chunk is (sub_id u8, data_type u8, data_size u16 LE, data).
    Data types: 0 = 16-bit integer, 1 = variable-length string, 4 = 32-bit integer.
    """
    out = {}
    if data[0x10200:0x10204] != b"xid6":
        return out
    size = struct.unpack_from("<I", data, 0x10204)[0]
    end = min(0x10208 + size, len(data))
    pos = 0x10208
    while pos < end:  # a header starting at end-4 is still a complete chunk
        sub_id, data_type, chunk_size = struct.unpack_from("<BBH", data, pos)
        pos += 4
        raw = data[pos:pos + chunk_size]
        pos += chunk_size
        if data_type == 1:  # null-terminated variable-length text
            field = XID6_TEXT.get(sub_id)
            if field:
                value = _text(raw)
                if value is not None:
                    out[field] = value
        elif data_type in (0, 4):  # little-endian integer (2 or 4 bytes)
            field = XID6_INT.get(sub_id)
            width = 2 if data_type == 0 else 4
            if field:
                # Some rippers truncate the final chunk's data; missing low
                # bytes read as zero rather than dropping the field entirely.
                out[field] = int.from_bytes(raw.ljust(width, b"\0")[:width], "little")
        # unknown sub-ids / types are skipped
    return out


def parse(data):
    if data[:33] != MAGIC or len(data) < 0x10200:
        raise SpcError("not an SPC file")
    pc, = struct.unpack_from("<H", data, 0x25)
    out = _empty_result()
    out["pc"] = "%04X" % pc

    if data[0x23] == 26:  # ID666 tag present
        out["has_id666"] = True
        out.update(_parse_id666(data))
        if out["play_seconds"]:
            out["length_seconds"] = str(out["play_seconds"])

    xid6 = _parse_xid6(data)
    out["xid6"] = data[0x10200:0x10204] == b"xid6"

    # Extended-tag values override the matching ID666 fields. Only fields the
    # extended tag actually carries are applied.
    for field, value in xid6.items():
        if field in XID6_INTERNAL:
            continue
        if field == "date":
            try:
                normalized = _date_from_text(value)
            except ValueError:
                normalized = None
            if normalized:
                out["date"] = normalized
        elif field == "emulator":
            if value in EMULATORS:
                out["emulator"] = EMULATORS[value]
        elif field == "muted_voices":
            out["muted_voices"] = [v for v in range(8) if value & (1 << v)]
        elif field == "amplification":
            # Fixed-point mixing level: 0x10000 represents a gain of 1.
            out["amplification"] = str(Fraction(value, 0x10000))
        elif value:
            out[field] = value

    # Full length from the extended timing tags when present:
    # intro + loop * loop count + end (all extended times are in milliseconds).
    if any(field in xid6 for field in ("intro_ms", "loop_ms", "end_ms")):
        intro = Fraction(xid6.get("intro_ms") or 0, 1000)
        loop = Fraction(xid6.get("loop_ms") or 0, 1000)
        end = Fraction(xid6.get("end_ms") or 0, 1000)
        loops = xid6.get("loop_count") or 0
        out["length_seconds"] = str(intro + loop * loops + end)
    return out
