"""NES Sound Format: NSF (incl. NSF2) headers and NSFe chunk files."""
import struct
from fractions import Fraction

# Offset 0x7B of an NSF header / the chip byte of an NSFe INFO chunk, bit 0
# first.
CHIPS = ("VRC6", "VRC7", "FDS", "MMC5", "Namco163", "Sunsoft5B", "VT02")

# Chunks defined by the NSFe specification.  Anything else is an extension: a
# name that begins with a lower-case letter is private and may be ignored, one
# that begins with an upper-case letter is an error.
NSFE_CHUNKS = frozenset(("INFO", "DATA", "NEND", "BANK",
                         "auth", "tlbl", "time", "plst"))

NSF_HEADER_SIZE = 128
DEFAULT_NTSC_SPEED = 16639  # microseconds per play call
DEFAULT_PAL_SPEED = 19997


class NsfError(ValueError):
    pass


def _text(raw):
    """Decode an NSF header text field, which ends at the first NUL byte.

    Rippers sometimes leave junk behind after the NUL; fields are also padded
    with spaces by some tools, so trailing blanks are dropped as well.
    """
    text = raw.split(b"\0", 1)[0].decode("latin-1").rstrip()
    return text or None


def _utf8(raw):
    """Decode one NUL-free NSFe UTF-8 string, mapping an empty one to None."""
    text = raw.decode("utf-8", "replace")
    return text or None


def _chips(bits):
    return [name for i, name in enumerate(CHIPS) if bits & (1 << i)]


def _region(bits, dual_ok=True):
    # Bit 0 selects PAL; bit 1 marks a file that also runs on NTSC.  The
    # second bit only has a meaning from NSF2 / NSFe onwards.
    if dual_ok and bits & (1 << 1):
        return "dual"
    return "PAL" if bits & (1 << 0) else "NTSC"


def _nsf_rate(region, ntsc_speed, pal_speed):
    # The speed fields hold the playback period in microseconds for the region
    # the file is meant for; dual files follow the NTSC timing.
    if region == "PAL":
        speed = pal_speed or DEFAULT_PAL_SPEED
    else:
        speed = ntsc_speed or DEFAULT_NTSC_SPEED
    return str(Fraction(1000000, speed))


def _nsfe_rate(region):
    # NSFe has no speed fields: the play routine is called 60 times a second
    # on NTSC (and dual) files and 50 times a second on PAL files.
    return "50" if region == "PAL" else "60"


def _nsf(data):
    if len(data) < NSF_HEADER_SIZE:
        raise NsfError("NSF header is shorter than 128 bytes")
    version, songs, start, load, init, play = struct.unpack_from("<BBBHHH", data, 5)
    ntsc_speed, = struct.unpack_from("<H", data, 0x6E)
    banks = data[0x70:0x78]
    pal_speed, = struct.unpack_from("<H", data, 0x78)
    region = _region(data[0x7A], dual_ok=version >= 2)
    out = {"format": "NSF", "version": version, "songs": songs,
           # Song numbers are 1-based; rippers that write 0 mean the first song.
           "start_song": start or 1,
           "title": _text(data[0x0E:0x2E]), "artist": _text(data[0x2E:0x4E]),
           "copyright": _text(data[0x4E:0x6E]),
           "region": region, "play_rate_hz": _nsf_rate(region, ntsc_speed, pal_speed),
           "chips": _chips(data[0x7B]), "bankswitched": any(banks),
           "load": "%04X" % load, "init": "%04X" % init, "play": "%04X" % play,
           "track_labels": None, "track_times_ms": None, "playlist": None}
    size = len(data) - NSF_HEADER_SIZE
    if version >= 2:
        # NSF2 adds a 24-bit program length at 0x7D so that data appended
        # after the program is not counted; zero means "to end of file".
        program = data[0x7D] | data[0x7E] << 8 | data[0x7F] << 16
        if program:
            if program > size:
                raise NsfError("NSF2 program data runs past the end of the file")
            size = program
    out["data_bytes"] = size
    return out


def _nsfe(data):
    out = {"format": "NSFe", "version": None, "songs": 1, "start_song": 1,
           "title": None, "artist": None, "copyright": None, "region": "NTSC",
           "play_rate_hz": "60", "chips": [], "bankswitched": False,
           "load": None, "init": None, "play": None, "track_labels": None,
           "track_times_ms": None, "playlist": None, "data_bytes": 0}
    pos, seen = 4, set()
    while pos + 8 <= len(data):
        size, = struct.unpack_from("<I", data, pos)
        cid = data[pos + 4:pos + 8].decode("latin-1")
        if pos + 8 + size > len(data):
            raise NsfError("chunk %s runs past the end of the file" % cid)
        body = data[pos + 8:pos + 8 + size]
        pos += 8 + size
        if cid not in NSFE_CHUNKS:
            # A private extension starting with a lower-case letter can be
            # ignored; anything else is an unknown chunk and an error.
            if cid[:1].islower():
                continue
            raise NsfError("unknown NSFe chunk %s" % cid)
        if cid == "NEND":
            break
        seen.add(cid)
        if cid == "INFO":
            if len(body) < 8:
                raise NsfError("INFO chunk is shorter than 8 bytes")
            load, init, play, region_bits, chips = struct.unpack_from("<HHHBB", body)
            # The song count and starting song are optional.
            songs = body[8] if len(body) > 8 else 1
            start = body[9] if len(body) > 9 else 0
            region = _region(region_bits)
            out.update(load="%04X" % load, init="%04X" % init, play="%04X" % play,
                       region=region, chips=_chips(chips), songs=songs,
                       # NSFe numbers the first song 0.
                       start_song=start + 1, play_rate_hz=_nsfe_rate(region))
        elif cid == "DATA":
            out["data_bytes"] = size
        elif cid == "BANK":
            # The chunk is only written by files that use bank switching.
            out["bankswitched"] = True
        elif cid == "auth":
            parts = body.split(b"\0")
            for key, raw in zip(("title", "artist", "copyright"), parts):
                out[key] = _utf8(raw)
        elif cid == "tlbl":
            labels = body.split(b"\0")
            # Labels are NUL-terminated, so the empty piece after the last
            # terminator is not a label of its own.
            if labels and not labels[-1]:
                labels.pop()
            out["track_labels"] = [label.decode("utf-8", "replace") for label in labels]
        elif cid == "time":
            vals = struct.unpack_from("<%di" % (size // 4), body)
            # -1 (0xFFFFFFFF) marks a track of unknown length.
            out["track_times_ms"] = [v if v >= 0 else None for v in vals]
        elif cid == "plst":
            # Playlist entries are 0-based song numbers; report them from 1.
            out["playlist"] = [song + 1 for song in body]
    if "INFO" not in seen or "DATA" not in seen:
        raise NsfError("NSFe file without INFO or DATA")
    return out


def parse(data):
    if data[:5] == b"NESM\x1a":
        return _nsf(data)
    if data[:4] == b"NSFE":
        return _nsfe(data)
    raise NsfError("not an NSF or NSFe file")
