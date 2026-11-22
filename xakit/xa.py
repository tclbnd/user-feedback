"""CD-ROM XA (Mode 2) sectors in RIFF CDXA files, as copied from Video CDs (.dat)."""
import struct
from fractions import Fraction

SECTOR = 2352
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
# MSF addressing: the first track starts at 00:02:00, which is LBA 0.
MSF_LBA_OFFSET = 150


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
    """Decode a binary-coded decimal byte (two nibbles, e.g. 0x59 -> 59)."""
    return (b >> 4) * 10 + (b & 0x0F)


def _riff(data):
    if data[:4] != b"RIFF" or data[8:12] != b"CDXA":
        raise XaError("not a RIFF CDXA file")
    pos, fmt, body = 12, None, []
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        chunk = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = chunk
        elif cid == b"data":
            # Some rippers split the image across several data chunks.
            body.append(chunk)
        # RIFF chunks are word aligned; a pad byte follows odd-sized chunks.
        pos += 8 + size + (size & 1)
    if fmt is None or not body:
        raise XaError("CDXA file without fmt or data")
    return fmt, b"".join(body)


def parse(data):
    fmt, body = _riff(data)
    # The XA attributes are stored big endian (RIFF style); bit 13 marks an
    # interleaved file. Reading them little endian hides that flag.
    attrs, = struct.unpack_from(">H", fmt, 4)
    out = {"xa_file_number": fmt[8] if len(fmt) > 8 else None, "interleaved": bool(attrs & 0x2000),
           "sectors": len(body) // SECTOR, "start_lba": None, "end_lba": None, "duration": None,
           "modes": {}, "form1": 0, "form2": 0,
           "video": 0, "audio": 0, "data": 0, "empty": 0, "user_bytes": 0, "edc_errors": 0, "bad_sync": 0,
           "subheader_errors": 0, "discontinuities": 0, "audio_format": None,
           "trailing_bytes": len(body) % SECTOR, "channels": {}, "records": 0, "eof_lba": None}
    prev = None
    for i in range(out["sectors"]):
        s = body[i * SECTOR:(i + 1) * SECTOR]
        # A sector with a damaged sync pattern cannot be read: count it once
        # here and leave every other statistic untouched.
        if s[0:12] != SYNC:
            out["bad_sync"] += 1
            prev = None
            continue
        mm, ss, ff, mode = _bcd(s[12]), _bcd(s[13]), _bcd(s[14]), s[15]
        # MSF 00:02:00 is LBA 0, so the physical MSF address is 150 ahead.
        lba = (mm * 60 + ss) * 75 + ff - MSF_LBA_OFFSET
        if out["start_lba"] is None:
            out["start_lba"] = lba
        elif prev is not None and lba != prev + 1:
            out["discontinuities"] += 1
        prev = out["end_lba"] = lba
        out["modes"][str(mode)] = out["modes"].get(str(mode), 0) + 1
        if mode == 0:
            # Empty sector: no user data and no EDC.
            continue
        if mode == 1:
            # CD-ROM Mode 1: 2048 user bytes, EDC over bytes 0..2063 at 2064.
            out["user_bytes"] += 2048
            if struct.unpack_from("<I", s, 2064)[0] != edc(s[0:2064]):
                out["edc_errors"] += 1
            continue
        if mode != 2:
            # Unknown mode: still account for the nominal user area if present.
            out["user_bytes"] += 2048
            continue
        sub, sub2 = s[16:20], s[20:24]
        file_no, channel, submode, coding = sub
        if sub != sub2:
            out["subheader_errors"] += 1
        key = "%d:%d" % (file_no, channel)
        out["channels"][key] = out["channels"].get(key, 0) + 1
        # A record ends with an end-of-record sector; an end-of-file sector
        # closes the final record even when EOR is not set.
        if submode & (0x01 | 0x80):
            out["records"] += 1
        if submode & 0x80 and out["eof_lba"] is None:
            out["eof_lba"] = lba
        form2 = bool(submode & 0x20)
        if form2:
            out["form2"] += 1
            # Mode 2 Form 2: 2324 user bytes (subheader included), EDC at 2348
            # over bytes 16..2347. An all-zero EDC means it was not computed.
            out["user_bytes"] += 2324
            stored = struct.unpack_from("<I", s, 2348)[0]
            if stored != 0 and stored != edc(s[16:2348]):
                out["edc_errors"] += 1
        else:
            out["form1"] += 1
            # Mode 2 Form 1: 2048 user bytes, EDC at 2072 over bytes 16..2071.
            out["user_bytes"] += 2048
            if struct.unpack_from("<I", s, 2072)[0] != edc(s[16:2072]):
                out["edc_errors"] += 1
        if submode & 0x02:
            out["video"] += 1
        elif submode & 0x04:
            out["audio"] += 1
            if out["audio_format"] is None:
                # ADPCM coding byte: bit 0 mono/stereo, bits 2-3 sample rate
                # (0 = 37.8 kHz, 1 = 18.9 kHz), bit 4 sample size, bit 6 emphasis.
                rate_bits = (coding >> 2) & 0x03
                out["audio_format"] = {
                    "channels": 2 if coding & 0x01 else 1,
                    "sample_rate": 18900 if rate_bits & 0x01 else 37800,
                    "bits": 8 if coding & 0x10 else 4,
                    "emphasis": bool(coding & 0x40)}
        elif submode & 0x08:
            out["data"] += 1
        else:
            out["empty"] += 1
    if out["start_lba"] is not None:
        out["duration"] = str(Fraction(out["end_lba"] - out["start_lba"] + 1, 75))
    return out
