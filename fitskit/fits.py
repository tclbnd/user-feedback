"""FITS files (.fits): header cards in 2880-byte blocks, primary and extension HDUs, image statistics."""
import math
import struct

BLOCK = 2880
TYPES = {8: ">B", 16: ">h", 32: ">i", 64: ">q", -32: ">f", -64: ">d"}


class FitsError(ValueError):
    pass


def value(text):
    """Value field of a card (columns 11-80): quoted string, logical, integer or float; comment after '/'."""
    s = text.strip()
    if s.startswith("'"):
        # Inside a quoted string a literal quote is written as two adjacent
        # quotes; the string ends at a quote that is not doubled.
        out, i = [], 1
        while i < len(s):
            if s[i] == "'":
                if s[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(s[i])
            i += 1
        return "".join(out).rstrip()
    s = s.split("/", 1)[0].strip()
    if s in ("T", "F"):
        return s == "T"
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return float(s.replace("D", "E"))


def header(data, p):
    cards = {}
    while True:
        if p + BLOCK > len(data):
            raise FitsError("header runs past the end of the file")
        block = data[p:p + BLOCK].decode("ascii")
        p += BLOCK
        for i in range(36):
            card = block[80 * i:80 * i + 80]
            key = card[:8].strip()
            if key == "END":
                return cards, p
            if card[8:10] == "= ":
                cards[key] = value(card[10:])


def integer(card_value, default=0):
    """An integer keyword value, or `default` when absent or of the wrong type."""
    return card_value if isinstance(card_value, int) and not isinstance(card_value, bool) else default


def number(card_value, default):
    """A numeric keyword value, or `default` when absent or of the wrong type."""
    return card_value if isinstance(card_value, (int, float)) and not isinstance(card_value, bool) else default


def padded(n):
    """A data unit occupies whole 2880-byte blocks."""
    return n + (-n % BLOCK)


def parse(data):
    if not data.startswith(b"SIMPLE  ="):
        raise FitsError("not a FITS file")
    p, hdus = 0, []
    while p + BLOCK <= len(data):
        # A block of zero fill at the end of the file is the deprecated
        # end-of-file marker, not another HDU.
        if not data[p:p + BLOCK].strip(b"\x00"):
            break
        start = p
        cards, p = header(data, p)
        bitpix = cards.get("BITPIX")
        if bitpix not in TYPES:
            raise FitsError("bad BITPIX %r" % (bitpix,))
        naxis = integer(cards.get("NAXIS"))
        shape = [max(integer(cards.get("NAXIS%d" % (i + 1))), 0) for i in range(naxis)]
        count = math.prod(shape) if naxis else 0
        # Data unit: the NAXIS array plus PCOUNT bytes of special data, repeated
        # GCOUNT times, at abs(BITPIX)/8 bytes per element.
        nbytes = ((count + max(integer(cards.get("PCOUNT")), 0)) * (abs(bitpix) // 8)
                  * max(integer(cards.get("GCOUNT"), 1), 1))
        xtension = cards.get("XTENSION")
        kind = "primary" if not hdus else (xtension.strip() if isinstance(xtension, str) else "")
        hdu = {"type": kind, "bitpix": bitpix, "shape": shape, "data_bytes": nbytes, "header_offset": start,
               "object": cards.get("OBJECT"), "complete": p + nbytes <= len(data), "min": None, "max": None, "blank": None}
        if kind in ("primary", "IMAGE") and count and hdu["complete"]:
            bzero = number(cards.get("BZERO"), 0)
            bscale = number(cards.get("BSCALE"), 1)
            # BLANK applies to integer data only; floating point marks undefined pixels with NaN.
            blank_value = integer(cards.get("BLANK"), None) if bitpix > 0 else None
            raw = struct.unpack_from(">%d%s" % (count, TYPES[bitpix][1]), data, p)
            vals, nb = [], 0
            for v in raw:
                if isinstance(v, float) and math.isnan(v):
                    nb += 1
                    continue
                if blank_value is not None and v == blank_value:
                    nb += 1
                    continue
                vals.append(bzero + bscale * v)
            hdu.update(blank=nb, min="%.6g" % min(vals) if vals else None, max="%.6g" % max(vals) if vals else None)
        hdus.append(hdu)
        p += padded(nbytes)
    return {"hdus": len(hdus), "extensions": [h["type"] for h in hdus[1:]], "hdu": hdus}
