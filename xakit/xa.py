"""CD-ROM XA (Mode 2) sectors in RIFF CDXA files, as copied from Video CDs (.dat).

Sector layout (2352 bytes, "raw" 2448-byte sectors without the 96 subchannel
bytes):

    Mode 1:  sync(12) header(4) user(2048) EDC(4) reserved(8) ECC(276)
    Mode 2:  sync(12) header(4) subheader(8) user ... EDC(4)
        Form 1: user 2048 bytes, EDC at offset 2072, then ECC(276)
        Form 2: user 2324 bytes, EDC at offset 2348 (may be 0 = not computed)

The header address is BCD-encoded MSF (minute/second/frame).  MSF 00:02:00
is logical block address 0, so LBA = MSF frame count - 150.
"""
import struct
from fractions import Fraction

SECTOR = 2352
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"

# Submode byte bits (first subheader, offset 18).
SUB_EOR = 0x01       # end of record
SUB_VIDEO = 0x02
SUB_AUDIO = 0x04
SUB_DATA = 0x08
SUB_FORM2 = 0x20     # 0 = Form 1, 1 = Form 2
SUB_EOF = 0x80       # end of file

# XA ADPCM coding byte bits (first subheader, offset 19).
COD_MONO = 0x01      # 0 = stereo (2 ch), 1 = mono (1 ch)
COD_RATE_18900 = 0x04  # 0 = 37.8 kHz, 1 = 18.9 kHz
COD_8BIT = 0x10      # 0 = 4 bit, 1 = 8 bit
COD_EMPHASIS = 0x40

# XA attributes word in the RIFF "fmt " chunk, bit 13 = interleaved file.
ATTR_INTERLEAVED = 0x2000

# EDC placement and user-data sizes per sector kind.
MODE1_USER = 2048
FORM1_USER = 2048
FORM2_USER = 2324
MODE1_EDC_OFF = 2064
FORM1_EDC_OFF = 2072
FORM2_EDC_OFF = 2348


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
    """Decode a binary-coded decimal byte (two nibbles, each 0-9)."""
    return (b >> 4) * 10 + (b & 0x0F)


def _riff(data):
    if data[:4] != b"RIFF" or data[8:12] != b"CDXA":
        raise XaError("not a RIFF CDXA file")
    pos, fmt, body, have_data = 12, None, b"", False
    while pos <= len(data) - 8:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        start = pos + 8
        end = start + size
        if end > len(data):  # truncated chunk
            break
        if cid == b"fmt ":
            fmt = data[start:end]
        elif cid == b"data":
            body, have_data = data[start:end], True
        # RIFF chunks are word-aligned: odd sizes carry one padding byte.
        pos = end + (size & 1)
    if fmt is None or not have_data:
        raise XaError("CDXA file without fmt or data")
    return fmt, body


def _empty_report():
    return {
        "xa_file_number": None, "interleaved": False, "sectors": 0,
        "start_lba": None, "end_lba": None, "duration": None, "modes": {},
        "form1": 0, "form2": 0, "video": 0, "audio": 0, "data": 0, "empty": 0,
        "user_bytes": 0, "edc_errors": 0, "bad_sync": 0, "subheader_errors": 0,
        "discontinuities": 0, "audio_format": None, "trailing_bytes": 0,
        "channels": {}, "records": 0, "eof_lba": None,
    }


def parse(data):
    fmt, body = _riff(data)
    # The XA attributes word is stored big-endian in the "fmt " chunk.
    attrs = struct.unpack_from(">H", fmt, 4)[0] if len(fmt) >= 6 else 0
    out = _empty_report()
    out["xa_file_number"] = fmt[8] if len(fmt) > 8 else None
    out["interleaved"] = bool(attrs & ATTR_INTERLEAVED)
    out["trailing_bytes"] = len(body) % SECTOR

    prev_lba = None
    whole = len(body) // SECTOR
    for i in range(whole):
        s = body[i * SECTOR:(i + 1) * SECTOR]
        out["sectors"] += 1

        # A sector without the sync pattern is unreadable: it counts only in
        # bad_sync and sectors, never in any other statistic.
        if s[0:12] != SYNC:
            out["bad_sync"] += 1
            continue

        mm, ss, ff, mode = _bcd(s[12]), _bcd(s[13]), _bcd(s[14]), s[15]
        # MSF 00:02:00 is LBA 0.
        lba = (mm * 60 + ss) * 75 + ff - 150

        if out["start_lba"] is None:
            out["start_lba"] = lba
        elif lba != prev_lba + 1:
            out["discontinuities"] += 1
        prev_lba = lba
        out["end_lba"] = lba
        out["modes"][str(mode)] = out["modes"].get(str(mode), 0) + 1

        if mode == 0:
            # Empty sector: no user data and no EDC.
            continue
        if mode == 1:
            out["user_bytes"] += MODE1_USER
            if struct.unpack_from("<I", s, MODE1_EDC_OFF)[0] != edc(s[0:2064]):
                out["edc_errors"] += 1
            continue

        # Mode 2 CD-ROM XA sector.
        sub, sub2 = s[16:20], s[20:24]
        file_no, channel, submode, coding = sub
        if sub != sub2:
            out["subheader_errors"] += 1

        key = "%d:%d" % (file_no, channel)
        out["channels"][key] = out["channels"].get(key, 0) + 1

        if submode & (SUB_EOR | SUB_EOF):
            out["records"] += 1
        if submode & SUB_EOF and out["eof_lba"] is None:
            out["eof_lba"] = lba

        if submode & SUB_VIDEO:
            out["video"] += 1
        elif submode & SUB_AUDIO:
            out["audio"] += 1
            if out["audio_format"] is None:
                out["audio_format"] = {
                    "channels": 1 if coding & COD_MONO else 2,
                    "sample_rate": 18900 if coding & COD_RATE_18900 else 37800,
                    "bits": 8 if coding & COD_8BIT else 4,
                    "emphasis": bool(coding & COD_EMPHASIS),
                }
        elif submode & SUB_DATA:
            out["data"] += 1
        else:
            out["empty"] += 1

        if submode & SUB_FORM2:
            out["form2"] += 1
            out["user_bytes"] += FORM2_USER
            stored = struct.unpack_from("<I", s, FORM2_EDC_OFF)[0]
            # A zero Form 2 EDC means it was never computed, not an error.
            if stored != 0 and stored != edc(s[16:FORM2_EDC_OFF]):
                out["edc_errors"] += 1
        else:
            out["form1"] += 1
            out["user_bytes"] += FORM1_USER
            if struct.unpack_from("<I", s, FORM1_EDC_OFF)[0] != edc(s[16:FORM1_EDC_OFF]):
                out["edc_errors"] += 1

    if out["start_lba"] is not None:
        out["duration"] = str(Fraction(out["end_lba"] - out["start_lba"] + 1, 75))
    return out

