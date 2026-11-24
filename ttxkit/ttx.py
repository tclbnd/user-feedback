"""Teletext (ETSI EN 300 706) captured as T42 packets: addresses, page headers, control bits, parity."""

PACKET = 42


class TtxError(ValueError):
    pass


def ham(b, stats):
    """Hamming 8/4 byte to its 4 data bits."""
    return (b & 1) | (b >> 2 & 1) << 1 | (b >> 4 & 1) << 2 | (b >> 6 & 1) << 3


def text(raw, stats):
    out = []
    for b in raw:
        out.append(chr(b) if b >= 0x20 else " ")
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
        a, b = ham(p[0], stats), ham(p[1], stats)
        mag = a & 7
        row = (a >> 3) << 4 | b
        mags.add(mag)
        if row == 0:
            n = [ham(x, stats) for x in p[2:10]]
            units, tens, s1, s2, s3, s4, c7_10, c11_14 = n
            page = "%d%X%X" % (mag, tens, units)
            out["pages"].append({
                "page": page, "subcode": "%X%X%X%X" % (s4 & 3, s3, s2 & 7, s1),
                "erase": bool(s2 & 8), "newsflash": bool(s4 & 8), "subtitle": bool(s4 & 4),
                "suppress_header": bool(c7_10 & 1), "magazine_serial": bool(c11_14 & 1)})
            if out["header_text"] is None:
                out["header_text"] = text(p[10:42], stats).rstrip()
        elif row <= 24:
            out["display_rows"] += 1
        else:
            out["extension_packets"] += 1
    out.update(stats)
    out["magazines"] = sorted(mags)
    return out
