"""Teletext (ETSI EN 300 706) captured as T42 packets: addresses, page headers, control bits, parity."""

PACKET = 42

# Hamming 8/4 code words for data nibbles 0..15.  Data bits are transmitted
# in bit positions 1, 3, 5 and 7 (zero-based); the other bits are check bits.
HAMMING_8_4 = (
    0x15, 0x02, 0x49, 0x5E, 0x64, 0x73, 0x38, 0x2F,
    0xD0, 0xC7, 0x8C, 0x9B, 0xA1, 0xB6, 0xFD, 0xEA,
)

# Values received one bit away from a valid code word can be corrected.
_HAMMING_CORRECTION = {}
for _value, _code in enumerate(HAMMING_8_4):
    for _bit in range(8):
        _HAMMING_CORRECTION[_code ^ (1 << _bit)] = _value


class TtxError(ValueError):
    pass


def _bump(stats, key):
    stats[key] = stats.get(key, 0) + 1


def ham(b, stats):
    """Decode a Hamming 8/4 byte, correcting and counting one-bit errors.

    Return the four data bits, or ``None`` when the byte is not decodable.
    """
    b &= 0xFF
    for value, code in enumerate(HAMMING_8_4):
        if b == code:
            return value

    if b in _HAMMING_CORRECTION:
        _bump(stats, "corrected")
        return _HAMMING_CORRECTION[b]

    _bump(stats, "hamming_errors")
    return None


def text(raw, stats):
    """Decode odd-parity teletext characters.

    A parity failure is counted and rendered as a space.  The parity bit is
    removed before converting the remaining seven-bit character value.
    """
    out = []
    for b in raw:
        if bin(b).count("1") % 2 == 0:
            _bump(stats, "parity_errors")
            out.append(" ")
            continue

        c = b & 0x7F
        out.append(chr(c) if c >= 0x20 else " ")
    return "".join(out)


def parse(data):
    if len(data) % PACKET:
        raise TtxError("file size is not a whole number of 42-byte packets")
    stats = {"corrected": 0, "hamming_errors": 0, "parity_errors": 0}
    out = {"packets": len(data) // PACKET, "pages": [], "display_rows": 0, "extension_packets": 0,
           "header_text": None, "magazines": []}
    mags = set()
    for k in range(len(data) // PACKET):
        p = data[k * PACKET:(k + 1) * PACKET]

        a = ham(p[0], stats)
        b = ham(p[1], stats)
        if a is None or b is None:
            # The address is not decodable, so the rest of this packet cannot
            # be assigned to a magazine/row or counted as a page/row packet.
            continue

        # Address data bits: magazine is A1..A3 (0 means magazine 8), A4 is
        # the least-significant row bit, and the second nibble holds row bits
        # 2..5.
        mag = (a & 0x07) or 8
        row = (b << 1) | (a >> 3)
        mags.add(mag)

        if row == 0:
            n = [ham(x, stats) for x in p[2:10]]
            if any(x is None for x in n):
                continue

            units, tens, s1, s2, s3, s4, c7_10, c11_14 = n
            page = "%X%X%X" % (mag, tens, units)
            out["pages"].append({
                "page": page, "subcode": "%X%X%X%X" % (s4 & 3, s3, s2 & 7, s1),
                "erase": bool(s2 & 8), "newsflash": bool(s4 & 4), "subtitle": bool(s4 & 8),
                "suppress_header": bool(c7_10 & 1), "magazine_serial": bool(c11_14 & 1)})

            header_text = text(p[10:42], stats).rstrip()
            is_time_filler = tens == 0xF and units == 0xF
            if not is_time_filler and out["header_text"] is None:
                out["header_text"] = header_text
        elif row <= 24:
            # Rows 1..24 are ordinary character rows.  Check their parity
            # even though the rows themselves are not retained in the summary.
            text(p[2:42], stats)
            out["display_rows"] += 1
        else:
            # Rows 25..31 are extension packets with packet-dependent coding.
            out["extension_packets"] += 1
    out.update(stats)
    out["magazines"] = sorted(mags)
    return out
