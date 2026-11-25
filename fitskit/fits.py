"""FITS files (.fits): header cards in 2880-byte blocks, primary and extension HDUs, image statistics."""
import math
import struct

BLOCK = 2880
TYPES = {8: ">B", 16: ">h", 32: ">i", 64: ">q", -32: ">i", -64: ">d"}


class FitsError(ValueError):
    pass


def value(text):
    """Value field of a card (columns 11-80): quoted string, logical, integer or float; comment after '/'."""
    s = text.strip()
    if s.startswith("'"):
        return s[1:].split("'")[0].rstrip()
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


def parse(data):
    if not data.startswith(b"SIMPLE  ="):
        raise FitsError("not a FITS file")
    p, hdus = 0, []
    while p + BLOCK <= len(data):
        start = p
        cards, p = header(data, p)
        bitpix, naxis = cards.get("BITPIX"), cards.get("NAXIS", 0)
        if bitpix not in TYPES:
            raise FitsError("bad BITPIX %r" % bitpix)
        shape = [cards.get("NAXIS%d" % (i + 1), 0) for i in range(naxis)]
        count = math.prod(shape) if naxis else 0
        nbytes = abs(bitpix) // 8 * count
        kind = "primary" if not hdus else (cards.get("XTENSION") or "").strip()
        hdu = {"type": kind, "bitpix": bitpix, "shape": shape, "data_bytes": nbytes, "header_offset": start,
               "object": cards.get("OBJECT"), "complete": p + nbytes <= len(data), "min": None, "max": None, "blank": None}
        if kind in ("primary", "IMAGE") and count and hdu["complete"]:
            raw = struct.unpack_from(">%d%s" % (count, TYPES[bitpix][1]), data, p)
            vals, nb = [], 0
            for v in raw:
                if isinstance(v, float) and math.isnan(v):
                    nb += 1
                else:
                    vals.append(v)
            hdu.update(blank=nb, min="%.6g" % min(vals) if vals else None, max="%.6g" % max(vals) if vals else None)
        hdus.append(hdu)
        p += nbytes
    return {"hdus": len(hdus), "extensions": [h["type"] for h in hdus[1:]], "hdu": hdus}
