"""Tiny JPEG builders for the tests."""
import struct


def minimal(w=64, h=48):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + sof + b"\xff\xd9"


def segment(m, payload=b""):
    return bytes([0xFF, m]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg(*parts):
    return b"\xff\xd8" + b"".join(parts) + b"\xff\xd9"


def sof(m=0xC0, width=64, height=48, precision=8, comps=((1, 0x11, 0),)):
    payload = struct.pack(">BHHB", precision, height, width, len(comps))
    payload += b"".join(bytes([cid, hv, tq]) for cid, hv, tq in comps)
    return segment(m, payload)


def dqt(*tables):
    """tables of (id, precision, 64 values); precision 0 is 8-bit, 1 is 16-bit."""
    payload = b""
    for tid, precision, values in tables:
        payload += bytes([(precision << 4) | tid])
        payload += struct.pack(">%dH" % len(values), *values) if precision else bytes(values)
    return segment(0xDB, payload)


def dht(*tables):
    """tables of (class, id, 16 code counts, symbols)."""
    payload = b""
    for cls, tid, counts, symbols in tables:
        payload += bytes([(cls << 4) | tid]) + bytes(counts) + bytes(symbols)
    return segment(0xC4, payload)


def sos(comp_ids=(1,), ss=0, se=63, ah=0, al=0):
    payload = bytes([len(comp_ids)]) + b"".join(bytes([cid, 0]) for cid in comp_ids)
    payload += bytes([ss, se, (ah << 4) | al])
    return segment(0xDA, payload)


def dri(interval):
    return segment(0xDD, struct.pack(">H", interval))


def jfif(xd=72, yd=72, units=1):
    return segment(0xE0, b"JFIF\0" + bytes([1, 2, units]) + struct.pack(">HH", xd, yd) + b"\0\0")


def adobe(transform):
    return segment(0xEE, b"Adobe" + struct.pack(">HHHB", 100, 0, 0, transform))


def _tiff(byte_order, ifd0, exif_ifd):
    e = "<" if byte_order == "little" else ">"
    head = (b"II" if byte_order == "little" else b"MM") + struct.pack(e + "HI", 42, 8)

    def ifd_size(entries):
        return 2 + 12 * len(entries) + 4

    entries = list(ifd0)
    exif_offset = None
    if exif_ifd is not None:
        entries.append(None)  # placeholder so the offset accounts for the pointer entry
        exif_offset = 8 + ifd_size(entries)
        entries[-1] = (0x8769, 4, 1, struct.pack(e + "I", exif_offset))
    data_offset = 8 + ifd_size(entries) + (ifd_size(exif_ifd) if exif_ifd is not None else 0)
    blob = bytearray()

    def build(these):
        body = struct.pack(e + "H", len(these))
        for tag, typ, count, raw in these:
            if len(raw) <= 4:
                field = raw.ljust(4, b"\x00")
            else:
                field = struct.pack(e + "I", data_offset + len(blob))
                blob.extend(raw + (b"\x00" if len(raw) % 2 else b""))
            body += struct.pack(e + "HHI", tag, typ, count) + field
        return body + struct.pack(e + "I", 0)

    out = head + build(entries)
    if exif_ifd is not None:
        out += build(exif_ifd)
    return bytes(out + blob)


def text(tag, value):
    """An ASCII TIFF entry."""
    return (tag, 2, len(value) + 1, value.encode("utf-8") + b"\0")


def short(tag, value, byte_order="little"):
    """A SHORT TIFF entry."""
    return (tag, 3, 1, struct.pack(("<" if byte_order == "little" else ">") + "H", value))


def exif(byte_order="little", ifd0=(), exif_ifd=None):
    return segment(0xE1, b"Exif\0\0" + _tiff(byte_order, ifd0, exif_ifd))
