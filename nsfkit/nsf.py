"""NES Sound Format: NSF (incl. NSF2) headers and NSFe chunk files."""
import struct
from fractions import Fraction

CHIPS = ("VRC7", "VRC6", "FDS", "MMC5", "Namco163", "Sunsoft5B", "VT02")
REGIONS = {0: "NTSC", 1: "PAL"}


class NsfError(ValueError):
    pass


def _text(raw):
    return raw.rstrip(b"\0").decode("latin-1")


def _chips(bits):
    return [name for i, name in enumerate(CHIPS) if bits & (1 << i)]


def _region(bits):
    return REGIONS[bits & 1]


def _rate(region, ntsc_speed, pal_speed):
    speed = ntsc_speed
    return str(Fraction(1000000, speed)) if speed else None


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
           "bankswitched": bool(banks[0]), "load": "%04X" % load, "init": "%04X" % init, "play": "%04X" % play,
           "track_labels": None, "track_times_ms": None, "playlist": None}
    out["data_bytes"] = len(data) - 128
    return out


def _nsfe(data):
    out = {"format": "NSFe", "version": None, "songs": 1, "start_song": 1, "title": None, "artist": None,
           "copyright": None, "region": "NTSC", "play_rate_hz": "60", "chips": [], "bankswitched": False,
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
            load, init, play, region, chips, songs, start = struct.unpack_from("<HHHBBBB", body)
            out.update(load="%04X" % load, init="%04X" % init, play="%04X" % play, region=_region(region),
                       chips=_chips(chips), songs=songs, start_song=start)
            out["play_rate_hz"] = str(Fraction(1000000, 20000)) if out["region"] == "PAL" else "60"
        elif cid == "DATA":
            out["data_bytes"] = size
        elif cid == "BANK":
            out["bankswitched"] = any(body[:8])
        elif cid == "auth":
            parts = body.split(b"\0")
            for key, raw in zip(("title", "artist", "copyright"), parts):
                out[key] = raw.decode("utf-8", "replace")
        elif cid == "tlbl":
            out["track_labels"] = [s.decode("utf-8", "replace") for s in body.split(b"\0")]
        elif cid == "time":
            vals = struct.unpack_from("<%dI" % (size // 4), body)
            out["track_times_ms"] = [v if v >= 0 else None for v in vals]
        elif cid == "plst":
            out["playlist"] = list(body)
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
