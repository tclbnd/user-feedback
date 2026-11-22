"""CD-ROM XA (Mode 2) sectors in RIFF CDXA files, as copied from Video CDs (.dat).

Sector layout (2352 bytes, raw/2352 rips):

    sync            0..12     00 FF*10 00
    header          12..16    absolute MSF address (BCD) + mode byte
    Mode 2 subheader 16..24   file/channel, submode, coding, twice
    user data       ...       2048 bytes (Form 1) or 2324 bytes (Form 2)
    EDC                       4 bytes, little endian

MSF addresses are relative to the start of the disc, so LBA 0 is MSF
00:02:00: subtract the 150-sector lead-in.
"""
import struct
from fractions import Fraction

SECTOR = 2352
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
MSF_LBA_OFFSET = 150  # MSF 00:02:00 is LBA 0

# Mode 2 subheader / submode bits
SUBMODE_VIDEO = 0x02
SUBMODE_AUDIO = 0x04
SUBMODE_DATA = 0x08
SUBMODE_FORM2 = 0x20

# XA ADPCM coding byte bits (as used by the RIFF CDXA fmt / sector subheader)
CODING_STEREO = 0x01
CODING_RATE_18900 = 0x04
CODING_8BIT = 0x10
CODING_EMPHASIS = 0x40


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
    """Decode a binary-coded decimal byte (the MSF fields are BCD)."""
    return (b >> 4) * 10 + (b & 0x0F)


def _msf_to_lba(mm, ss, ff):
    """Absolute MSF (BCD bytes) to logical block address; LBA 0 is 00:02:00."""
    return (_bcd(mm) * 60 + _bcd(ss)) * 75 + _bcd(ff) - MSF_LBA_OFFSET


def _riff(data):
    if data[:4] != b"RIFF" or data[8:12] != b"CDXA":
        raise XaError("not a RIFF CDXA file")
    pos, fmt, body = 12, None, None
    # Walk the chunks defensively: rippers add padding on odd-sized chunks and
    # occasionally extra chunks between fmt and data.
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        start = pos + 8
        if start + size > len(data):
            raise XaError("truncated RIFF chunk")
        if cid == b"fmt ":
            fmt = data[start:start + size]
        elif cid == b"data":
            body = data[start:start + size]
        pos = start + size + (size & 1)  # RIFF pads odd chunks with one byte
    if fmt is None or body is None:
        raise XaError("CDXA file without fmt or data")
    return fmt, body


def parse(data):
    fmt, body = _riff(data)
    # The fmt chunk is big-endian ("XACPEXT" style): wFormatTag(2) dwSize(2),
    # wXAAttributes(2, big endian), "XA", bFileNumber, ...
    attrs = struct.unpack_from(">H", fmt, 4)[0] if len(fmt) >= 6 else 0
    out = {"xa_file_number": fmt[8] if len(fmt) > 8 else None,
           "interleaved": bool(attrs & 0x2000),
           "sectors": 0, "start_lba": None, "end_lba": None, "duration": None,
           "modes": {}, "form1": 0, "form2": 0,
           "video": 0, "audio": 0, "data": 0, "empty": 0,
           "user_bytes": 0, "edc_errors": 0, "bad_sync": 0,
           "subheader_errors": 0, "discontinuities": 0,
           "audio_format": None, "trailing_bytes": len(body) % SECTOR}
    prev_lba = None
    nsectors = len(body) // SECTOR
    for i in range(nsectors):
        s = body[i * SECTOR:(i + 1) * SECTOR]
        # A broken sync means the sector could not be read at all: count it
        # only in bad_sync, nowhere else.
        if s[:12] != SYNC:
            out["bad_sync"] += 1
            continue  # unreadable: excluded from every other statistic
        out["sectors"] += 1
        mm, ss, ff, mode = s[12], s[13], s[14], s[15]
        lba = _msf_to_lba(mm, ss, ff)
        if out["start_lba"] is None:
            out["start_lba"] = lba
        elif lba != prev_lba + 1:
            out["discontinuities"] += 1
        prev_lba = lba
        out["end_lba"] = lba
        out["modes"][str(mode)] = out["modes"].get(str(mode), 0) + 1

        if mode == 1:
            # Mode 1: 2048 user bytes at 16..2064, EDC at 2064 over 0..2064.
            out["user_bytes"] += 2048
            if struct.unpack_from("<I", s, 2064)[0] != edc(s[:2064]):
                out["edc_errors"] += 1
            continue

        if mode != 2:
            # Unknown/other mode: no XA interpretation available.
            continue

        # Mode 2: the subheader appears twice and must match.
        if s[16:20] != s[20:24]:
            out["subheader_errors"] += 1
        submode, coding = s[18], s[19]
        form2 = bool(submode & SUBMODE_FORM2)
        if form2:
            out["form2"] += 1
            # Form 2: 2324 user bytes at 24..2348, EDC at 2348 over 16..2348.
            out["user_bytes"] += 2324
            stored = struct.unpack_from("<I", s, 2348)[0]
            # A zero EDC means the drive/ripper did not compute one.
            if stored != 0 and stored != edc(s[16:2348]):
                out["edc_errors"] += 1
        else:
            out["form1"] += 1
            # Form 1: 2048 user bytes at 24..2072, EDC at 2072 over 16..2072.
            out["user_bytes"] += 2048
            if struct.unpack_from("<I", s, 2072)[0] != edc(s[16:2072]):
                out["edc_errors"] += 1

        if submode & SUBMODE_VIDEO:
            out["video"] += 1
        elif submode & SUBMODE_AUDIO:
            out["audio"] += 1
            if out["audio_format"] is None:
                out["audio_format"] = {
                    "channels": 2 if coding & CODING_STEREO else 1,
                    "sample_rate": 18900 if coding & CODING_RATE_18900 else 37800,
                    "bits": 8 if coding & CODING_8BIT else 4,
                    "emphasis": bool(coding & CODING_EMPHASIS)}
        elif submode & SUBMODE_DATA:
            out["data"] += 1
        else:
            out["empty"] += 1
    if out["start_lba"] is not None:
        out["duration"] = str(Fraction(out["end_lba"] - out["start_lba"] + 1, 75))
    return out
