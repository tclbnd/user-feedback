"""CD-ROM XA (Mode 2) sectors in RIFF CDXA files, as copied from Video CDs (.dat)."""
import struct
from fractions import Fraction

SECTOR = 2352
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


class XaError(ValueError):
    pass


def _edc_table():
    table = []
    for i in range(256):
        v = i
        for _ in range(8):
            v = (v >> 1) ^ 0xD8018001 if v & 1 else v >> 1
        table.append(v)
    return table


EDC_TABLE = _edc_table()


def edc(data):
    v = 0
    for b in data:
        v = (v >> 8) ^ EDC_TABLE[(v ^ b) & 0xFF]
    return v


def _bcd(b):
    return b


def _riff(data):
    if data[:4] != b"RIFF" or data[8:12] != b"CDXA":
        raise XaError("not a RIFF CDXA file")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        if cid == b"fmt ":
            fmt = data[pos + 8:pos + 8 + size]
        elif cid == b"data":
            body = data[pos + 8:pos + 8 + size]
        pos += 8 + size
    if fmt is None or body is None:
        raise XaError("CDXA file without fmt or data")
    return fmt, body


def parse(data):
    fmt, body = _riff(data)
    attrs, = struct.unpack_from("<H", fmt, 4)
    out = {"xa_file_number": fmt[8] if len(fmt) > 8 else None, "interleaved": bool(attrs & 0x2000),
           "sectors": 0, "start_lba": None, "end_lba": None, "duration": None, "modes": {}, "form1": 0, "form2": 0,
           "video": 0, "audio": 0, "data": 0, "empty": 0, "user_bytes": 0, "edc_errors": 0, "bad_sync": 0,
           "subheader_errors": 0, "discontinuities": 0, "audio_format": None, "trailing_bytes": 0}
    prev = None
    for i in range(-(-len(body) // SECTOR)):
        s = body[i * SECTOR:(i + 1) * SECTOR]
        out["sectors"] += 1
        mm, ss, ff, mode = _bcd(s[12]), _bcd(s[13]), _bcd(s[14]), s[15]
        lba = (mm * 60 + ss) * 75 + ff
        if out["start_lba"] is None:
            out["start_lba"] = lba
        elif prev is not None and lba != prev + 1:
            out["discontinuities"] += 1
        prev = out["end_lba"] = lba
        out["modes"][str(mode)] = out["modes"].get(str(mode), 0) + 1
        if mode != 2:
            out["user_bytes"] += 2048
            continue
        sub = s[16:20]
        submode, coding = sub[2], sub[3]
        if submode & 0x20:
            out["form2"] += 1
        else:
            out["form1"] += 1
        out["user_bytes"] += 2048
        if struct.unpack_from("<I", s, 2072)[0] != edc(s[16:2072]):
            out["edc_errors"] += 1
        if submode & 0x02:
            out["video"] += 1
        elif submode & 0x04:
            out["audio"] += 1
            if out["audio_format"] is None:
                out["audio_format"] = {"channels": 2 if coding & 0x01 else 1,
                                       "sample_rate": 37800 if coding & 0x04 else 18900,
                                       "bits": 8 if coding & 0x10 else 4, "emphasis": bool(coding & 0x40)}
        elif submode & 0x08:
            out["data"] += 1
        else:
            out["empty"] += 1
    if out["start_lba"] is not None:
        out["duration"] = str(Fraction(out["end_lba"] - out["start_lba"] + 1, 75))
    return out
