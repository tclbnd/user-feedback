"""Pack header, PES header and timestamp fields.

Program stream syntax of ISO/IEC 11172-1 (MPEG-1) and ISO/IEC 13818-1
(MPEG-2), plus the DVD-Video sub-streams carried in private_stream_1.
"""


class PsError(ValueError):
    pass


def timestamp(b, p):
    """33-bit field in 90 kHz units from the 5-byte marker layout.

    Used for PTS, DTS and the MPEG-1 system clock reference.  All 33
    bits are significant: programs longer than 2**30/90000 s (~3.3 h)
    set the top bits.
    """
    return (((b[p] >> 1) & 7) << 30) | (b[p + 1] << 22) | ((b[p + 2] >> 1) << 15) | \
           (b[p + 3] << 7) | (b[p + 4] >> 1)


def pack(b, p):
    """Parse the pack header at offset p.

    Return (version, scr, mux_rate, header_length): version is 1 or 2,
    scr is in 27 MHz units, mux_rate in bytes per second (the field is
    coded in units of 50 bytes/s in both standards).
    """
    if p + 12 > len(b):
        raise PsError("truncated pack header")
    x = b[p + 4]
    if x >> 6 == 1:
        # MPEG-2: '01' SCR_base(33) with markers, SCR_extension(9),
        # mux_rate(22), then 0..7 stuffing bytes.
        base = (((x >> 3) & 7) << 30) | ((x & 3) << 28) | (b[p + 5] << 20) | \
               ((b[p + 6] >> 3) << 15) | ((b[p + 6] & 3) << 13) | \
               (b[p + 7] << 5) | (b[p + 8] >> 3)
        ext = ((b[p + 8] & 3) << 7) | (b[p + 9] >> 1)
        mux = (b[p + 10] << 14) | (b[p + 11] << 6) | (b[p + 12] >> 2)
        stuffing = b[p + 13] & 7
        return 2, base * 300 + ext, mux * 50, 14 + stuffing
    if x >> 4 == 2:
        # MPEG-1: '0010' SCR(33) with markers, mux_rate(22); fixed
        # 12-byte header, no stuffing.
        scr = timestamp(b, p + 4)
        mux = ((b[p + 9] & 0x7F) << 15) | (b[p + 10] << 7) | (b[p + 11] >> 1)
        return 1, scr * 300, mux * 50, 12
    raise PsError("bad pack header")


# stream_ids whose packets carry no optional PES header
NO_HEADER = frozenset((0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xFF, 0xF2, 0xF8))


def pes(b, p, end, version):
    """Parse the PES header of the packet body b[p:end].

    b[p - 3] must be the stream_id.  Return (pts, dts, payload_start)
    with pts/dts as 33-bit 90 kHz values or None.
    """
    sid = b[p - 3]
    if sid in NO_HEADER:
        return None, None, p
    if version == 1:
        return _pes1(b, p, end)
    return _pes2(b, p, end)


def _pes1(b, p, end):
    """ISO/IEC 11172-1 packet header: stuffing, STD buffer, PTS/DTS."""
    i = p
    stuffing = 0
    while i < end and b[i] == 0xFF and stuffing < 16:
        i += 1
        stuffing += 1
    if i < end and b[i] >> 6 == 1:
        # '01' STD_buffer_scale(1) STD_buffer_size(13)
        i += 2
    if i >= end:
        return None, None, end
    x = b[i]
    if x >> 4 == 2:                      # '0010' PTS
        return timestamp(b, i), None, i + 5
    if x >> 4 == 3:                      # '0011' PTS, '0001' DTS
        return timestamp(b, i), timestamp(b, i + 5), i + 10
    if x == 0x0F:                        # no timestamps present
        return None, None, i + 1
    raise PsError("bad MPEG-1 PES header")


def _pes2(b, p, end):
    """ISO/IEC 13818-1 PES header: flags and header_data_length."""
    if p + 3 > end or b[p] >> 6 != 2:
        raise PsError("bad MPEG-2 PES header")
    flags = b[p + 1]
    payload = p + 3 + b[p + 2]
    if payload > end:
        raise PsError("bad MPEG-2 PES header")
    pts = dts = None
    if flags & 0x80:                     # PTS_DTS_flags '1x'
        pts = timestamp(b, p + 3)
        if flags & 0x40:
            dts = timestamp(b, p + 8)
    return pts, dts, payload


def dvd_substream(b, p, end):
    """Decode the DVD sub-stream header of private_stream_1 payload at p.

    Return (sub_stream_id, kind, header_length, lpcm); lpcm is a dict
    with bits/sample_rate/channels for LPCM and None otherwise.  The
    header_length covers the sub_stream_id byte and the sub-stream
    specific fields, none of which are elementary stream data.
    """
    sub = b[p]
    have = end - p
    if 0x20 <= sub <= 0x3F:
        return sub, "subpicture", 1, None
    if 0x80 <= sub <= 0x87:
        # sub_id, number_of_frame_headers, first_access_unit_pointer(2)
        return sub, "ac3", min(4, have), None
    if 0x88 <= sub <= 0x8F:
        return sub, "dts", min(4, have), None
    if 0xA0 <= sub <= 0xA7 and have >= 7:
        # sub_id, frame count, pointer(2), audio_frame_number, then one
        # byte of quantization/sampling/channel flags and dynamic range.
        w = b[p + 5]
        lpcm = {
            "bits": (16, 20, 24, None)[w >> 6],
            "sample_rate": (48000, 96000, None, None)[(w >> 4) & 3],
            "channels": (w & 7) + 1,
        }
        return sub, "lpcm", 7, lpcm
    return sub, "private1", 1, None
