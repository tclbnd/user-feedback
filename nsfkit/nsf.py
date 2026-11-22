"""NES Sound Format: NSF (incl. NSF2) headers and NSFe chunk files."""
import struct
from fractions import Fraction

# Sound chip bits in the order defined by the NSF/NSF2 header and the NSFe
# INFO chunk: bit 0 is VRC6, bit 1 is VRC7, ...
CHIPS = ("VRC6", "VRC7", "FDS", "MMC5", "Namco163", "Sunsoft5B", "VT02")
REGIONS = {0: "NTSC", 1: "PAL"}
# Default play routine periods (CPU cycles per call) from the NSF spec, used
# by NSFe files, which carry no speed fields of their own.
DEFAULT_NTSC_SPEED = 16639
DEFAULT_PAL_SPEED = 19997


class NsfError(ValueError):
    pass


def _text(raw):
    """Decode a fixed-length NUL-terminated field; None when it is empty."""
    text = raw.split(b"\0", 1)[0].decode("latin-1")
    return text or None


def _chips(bits):
    return [name for i, name in enumerate(CHIPS) if bits & (1 << i)]


def _region(bits):
    # Bit 0 set: PAL supported; bit 1 set: NTSC supported. Both -> dual.
    if (bits & 1) and (bits & 2):
        return "dual"
    return REGIONS[bits & 1]


def _rate(region, ntsc_speed, pal_speed):
    if region == "PAL":
        # A zero PAL speed means "fall back to the NTSC speed" per spec.
        speed = pal_speed or ntsc_speed
    else:
        # NTSC-only and dual files are reported at the NTSC rate.
        speed = ntsc_speed
    return str(Fraction(1000000, speed)) if speed else None


def _strings(body, count=None):
    """Split an NSFe chunk of NUL-separated UTF-8 strings."""
    parts = body.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if count is not None:
        parts = parts[:count]
    return [s.decode("utf-8", "replace") for s in parts]


def _nsf(data):
    if len(data) < 128:
        raise NsfError("NSF header is shorter than 128 bytes")
    version, songs, start, load, init, play = struct.unpack_from("<BBBHHH", data, 5)
    ntsc_speed, = struct.unpack_from("<H", data, 0x6E)
    banks = data[0x70:0x78]
    pal_speed, = struct.unpack_from("<H", data, 0x78)
    region = _region(data[0x7A])
    out = {"format": "NSF", "version": version, "songs": songs, "start_song": start,
           "title": _text(data[0x0E:0x2E]), "artist": _text(data[0x2E:0x4E]), "copyright": _text(data[0x4E:0x6E]),
           "region": region, "play_rate_hz": _rate(region, ntsc_speed, pal_speed), "chips": _chips(data[0x7B]),
           "bankswitched": any(banks), "load": "%04X" % load, "init": "%04X" % init, "play": "%04X" % play,
           "track_labels": None, "track_times_ms": None, "playlist": None}
    out["data_bytes"] = len(data) - 128
    return out


def _nsfe(data):
    out = {"format": "NSFe", "version": None, "songs": 1, "start_song": 1, "title": None, "artist": None,
           "copyright": None, "region": "NTSC",
           "play_rate_hz": str(Fraction(1000000, DEFAULT_NTSC_SPEED)), "chips": [], "bankswitched": False,
           "load": None, "init": None, "play": None, "track_labels": None, "track_times_ms": None,
           "playlist": None, "data_bytes": 0}
    pos, seen = 4, set()
    while pos + 8 <= len(data):
        size, = struct.unpack_from("<I", data, pos)
        cid = data[pos + 4:pos + 8].decode("latin-1")
        body = data[pos + 8:pos + 8 + size]
        if pos + 8 + size > len(data):
            raise NsfError("chunk %s runs past the end of the file" % cid)
        pos += 8 + size
        if cid == "NEND":
            break
        if cid == "INFO":
            if size < 8:
                raise NsfError("INFO chunk is too short")
            load, init, play, region, chips = struct.unpack_from("<HHHBB", body, 0)
            songs = body[8] if size >= 9 else 1
            # The starting song is 0-based in the file, 1-based in the output.
            start = body[9] + 1 if size >= 10 else 1
            out.update(load="%04X" % load, init="%04X" % init, play="%04X" % play, region=_region(region),
                       chips=_chips(chips), songs=songs, start_song=start)
            out["play_rate_hz"] = _rate(out["region"], DEFAULT_NTSC_SPEED, DEFAULT_PAL_SPEED)
        elif cid == "DATA":
            out["data_bytes"] = size
        elif cid == "BANK":
            out["bankswitched"] = any(body[:8])
        elif cid == "auth":
            parts = body.split(b"\0")
            if parts and parts[-1] == b"":
                parts.pop()
            for key, raw in zip(("title", "artist", "copyright"), parts):
                out[key] = raw.decode("utf-8", "replace") or None
        elif cid == "tlbl":
            out["track_labels"] = _strings(body, out["songs"])
        elif cid == "time":
            vals = struct.unpack_from("<%di" % (size // 4), body)
            # Negative durations (the spec uses -1) mean the length is unknown.
            out["track_times_ms"] = [None if v < 0 else v for v in vals]
        elif cid == "plst":
            # Playlist entries are 0-based song numbers in the file.
            out["playlist"] = [song + 1 for song in body]
        elif cid[0].isupper():
            raise NsfError("unknown NSFe chunk %s" % cid)
        # Unknown chunks whose name starts with a lower-case letter are skipped.
        seen.add(cid)
    if "INFO" not in seen or "DATA" not in seen:
        raise NsfError("NSFe file without INFO or DATA")
    return out


def parse(data):
    if data[:5] == b"NESM\x1a":
        return _nsf(data)
    if data[:4] == b"NSFE":
        return _nsfe(data)
    raise NsfError("not an NSF or NSFe file")
