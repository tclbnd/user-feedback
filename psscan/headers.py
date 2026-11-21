"""Pack header, PES header and timestamp fields."""


class PsError(ValueError):
    pass


def timestamp(b, p):
    """PTS/DTS/SCR value from the 5-byte marker layout."""
    return (b[p + 1] << 22) | ((b[p + 2] >> 1) << 15) | (b[p + 3] << 7) | (b[p + 4] >> 1)


def pack(b, p):
    """Return (version, scr_27mhz, mux_rate, header_length) for the pack at p."""
    x = b[p + 4]
    base = (((x >> 3) & 7) << 30) | ((x & 3) << 28) | (b[p + 5] << 20) | ((b[p + 6] >> 3) << 15) | \
           ((b[p + 6] & 3) << 13) | (b[p + 7] << 5) | (b[p + 8] >> 3)
    mux = (b[p + 10] << 14) | (b[p + 11] << 6) | (b[p + 12] >> 2)
    return 2, base * 300, mux, 14


NO_HEADER = {0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xFF, 0xF2, 0xF8}


def pes(b, p, end, version):
    """Return (pts, dts, payload_start) for a PES packet body starting at p."""
    sid = b[p - 3]
    if sid in NO_HEADER or b[p] >> 6 != 2:
        return None, None, p
    flags = b[p + 1] >> 6
    hlen = b[p + 2]
    pts = timestamp(b, p + 3) if flags & 2 else None
    dts = timestamp(b, p + 8) if flags == 3 else None
    return pts, dts, p + 3 + hlen
